"""Source regressions for waking the existing OTA owner; no build/device access."""
from pathlib import Path
import unittest

from test_microlink_peer_probe import function

ROOT = Path(__file__).resolve().parents[1]
OTA = (ROOT / 'main/ota/mosaico_ota.cpp').read_text(encoding='utf-8')
NETWORK = (ROOT / 'main/host/network_quota.cpp').read_text(encoding='utf-8')
HEADER = (ROOT / 'main/host/network_quota.h').read_text(encoding='utf-8')
NOTIFY = 'GetNetworkQuota().notifyOtaQueued();'


class OtaQueueWakeTests(unittest.TestCase):
    def test_each_network_queue_publishes_before_notification(self):
        for signature, publication in (
            ('bool requestCheck(', 'checkQueued.store(true)'),
            ('bool approveUpdate(', 'approvedRequest.store(true)'),
            ('bool requestLocked(', 'active.store(true)'),
        ):
            with self.subTest(signature=signature):
                body = function(OTA, signature)
                self.assertEqual(body.count(NOTIFY), 1)
                self.assertLess(body.index(publication), body.index(NOTIFY))
                self.assertLess(body.index('StandbySleep::otaActivity();'), body.index(NOTIFY))
                self.assertEqual(body[body.index(NOTIFY):].strip(), NOTIFY + '\n    return true;\n}')
                # Queue admission and touch/storage exclusions remain in place.
                self.assertLess(body.index('StorageAdmission storageAdmission;'), body.index(publication))
                self.assertLess(body.index('TouchSleep::OtaAdmission touchGuard;'), body.index(publication))
        self.assertEqual(OTA.count(NOTIFY), 3)

    def test_download_bypass_paths_share_notified_admission(self):
        self.assertIn('requestLocked(true)', function(OTA, 'bool request('))
        self.assertIn('requestLocked(false,false,true)', function(OTA, 'bool requestMaintenanceUpdate('))
        self.assertLess(function(OTA, 'bool requestLocked(').index('pending = true;'),
                        function(OTA, 'bool requestLocked(').index(NOTIFY))

    def test_notifier_has_no_lock_radio_or_refresh_side_effects(self):
        body = function(NETWORK, 'void NetworkQuota::notifyOtaQueued(')
        # Ignore comments, then require exactly the existing task notification.
        statements = '\n'.join(line.split('//', 1)[0] for line in body.splitlines()[2:-1]).strip()
        self.assertEqual(statements, 'if (_ota_owner_ready.load(std::memory_order_acquire) && _task_handle) xTaskNotifyGive(_task_handle);')
        self.assertIn('void notifyOtaQueued();', HEADER)
        self.assertIn('ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(milliseconds));',
                      function(NETWORK, 'void NetworkQuota::wait('))

    def test_ready_published_only_after_all_failure_exits(self):
        self.assertIn('#ifdef MOSAICO_BOARD\n    std::atomic<bool> _ota_owner_ready{false};', HEADER)
        publication = '_ota_owner_ready.store(true, std::memory_order_release);'
        self.assertEqual(NETWORK.count('_ota_owner_ready.store('), 1)
        # Raw brace extraction cannot balance mutually exclusive Wi-Fi #if branches.
        run = NETWORK.split('void NetworkQuota::run()', 1)[1].split('bool NetworkQuota::requestJson(', 1)[0]
        before, after = run.split(publication, 1)
        self.assertEqual(before.count('return;'), 4)  # netif/event-loop/STA/Wi-Fi startup failures
        for gate in ('esp_netif_init()', 'esp_event_loop_create_default()',
                     'esp_netif_create_default_wifi_sta()', 'esp_wifi_init(&init)',
                     'esp_netif_sntp_init(&timeConfig)', 'MosaicoDisplay::claimNetworkOwner()'):
            self.assertIn(gate, before)
        self.assertTrue(after.split('while (true)', 1)[0].strip().startswith('// No return/delete'))
        self.assertNotIn('return;', after)
        self.assertNotIn('vTaskDelete(', after)
        self.assertIn('vTaskDelete(nullptr);', function(NETWORK, 'void NetworkQuota::task('))

    def test_existing_busy_and_bounded_owner_gates_remain(self):
        idle = function(HEADER, '    bool idleLocked(')
        self.assertIn('!MosaicoOta::busy() && !MosaicoOta::healthPending()', idle)
        busy = function(OTA, 'bool busy(')
        for queue in ('checkQueued.load()', 'approvedRequest.load()', 'active.load()'):
            self.assertIn(queue, busy)
        self.assertIn('if (MosaicoOta::busy() && MosaicoOta::requestAgeMs() > 120000)\n'
                      '            MosaicoOta::fail("network_ready_timeout");', NETWORK)
        self.assertIn('if(gaugeHold)closeWindow(MosaicoTwt::CycleReason::Cancel);', NETWORK)
        self.assertIn('if (locked && updateWindow && now >= windowDeadline)', NETWORK)
        self.assertIn('if(!radioLease.held||GetHAL().gaugeScaleTrialActive())', NETWORK)


if __name__ == '__main__':
    unittest.main()
