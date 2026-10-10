import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class Lifecycle(unittest.TestCase):
    def test_observer_commands_allowed_on_uart(self):
        s=(ROOT/'main/debug/serial_debug_transport.h').read_text()
        allowed=s.split('const char* allowed[] = {',1)[1].split('};',1)[0]
        self.assertIn('"rx-observe"',allowed)
        self.assertIn('"derp-tx-budget"',allowed)
    def test_owner_wait_is_ttl_bounded(self):
        s=(ROOT/'main/host/network_quota.cpp').read_text()
        wait=s.split('void NetworkQuota::wait(uint32_t milliseconds)',1)[1].split('void NetworkQuota::setCpu',1)[0]
        self.assertIn('rxObserver.deadlineMs - nowMs',wait)
        self.assertIn('std::min<uint32_t>(remaining, 1000)',wait)
    def test_ota_consumption_disables_observer(self):
        s=(ROOT/'main/host/network_quota.cpp').read_text()
        self.assertIn('if (on && MosaicoOta::busy()) return false;',s)
        body=s.split('if (MosaicoOta::takeRequest()) {',1)[1].split('continue;',1)[0]
        self.assertLess(body.index('mosaico_rx_observer::request(false)'),body.index('updateFirmware()'))
        self.assertLess(body.index('mosaico_rx_observer::service(false)'),body.index('updateFirmware()'))
    def test_forced_stop_rejects_concurrent_enable(self):
        s=(ROOT/'main/debug/mosaico_rx_observer.cpp').read_text()
        self.assertIn('command = pending || !allowEnable; on = requested && allowEnable;',s)
    def test_manifest_and_local_install_stop_observer(self):
        s=(ROOT/'main/host/network_quota.cpp').read_text()
        body=s.split('if (MosaicoOta::takeCheckRequest() || MosaicoOta::automaticCheckDue()) {',1)[1].split('MosaicoOta::finishCheck(downloaded);',1)[0]
        self.assertLess(body.index('mosaico_rx_observer::service(false)'),body.index('requestJson('))
        ota=(ROOT/'main/ota/mosaico_ota.cpp').read_text().split('void processLocalRequests()',1)[1]
        self.assertLess(ota.index('mosaico_rx_observer::service(false)'),ota.index('installVerified()'))
    def test_hook_keeps_forwarding_and_default_hooks(self):
        s=(ROOT/'main/debug/mosaico_rx_observer.cpp').read_text()
        hook=s.split('extern "C" int mosaico_rx_ip4_input',1)[1]
        self.assertNotIn('return 1',hook)
        self.assertNotIn('pbuf_free',hook)
        self.assertNotIn('esp_wifi_sta_twt_config',s)
        cm=(ROOT/'boards/mosaico/CMakeLists.txt').read_text()
        self.assertIn('ESP_IDF_LWIP_HOOK_FILENAME=',cm)
        self.assertNotIn('LWIP_HOOK_FILENAME=',cm.replace('ESP_IDF_LWIP_HOOK_FILENAME=',''))
if __name__=='__main__':unittest.main()
