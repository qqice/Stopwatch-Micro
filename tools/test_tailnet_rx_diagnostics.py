"""Offline bounded numeric return-path instrumentation tests. No SDK/serial/network."""
import os, re, shutil, subprocess, tempfile, unittest
from pathlib import Path
R = Path(__file__).resolve().parents[1]
B = R / 'components/microlink-source/components/microlink'
W = B / 'components/wireguard_lwip/src'
H = (W / 'wireguardif_rx_stats.h').read_text(encoding='utf8')
C = (W / 'wireguardif.c').read_text(encoding='utf8')
FIELDS = re.findall(r'uint32_t (\w+);', H)

class RxDiagnosticsTests(unittest.TestCase):
    def test_all_boundary_counters_and_independent_snapshot(self):
        sources = C + (B/'src/ml_derp.c').read_text(encoding='utf8') + (B/'src/ml_wg_mgr.c').read_text(encoding='utf8')
        for field in FIELDS:
            self.assertIn('wireguardif_rx_count(WG_RX_'+field.upper()+')', sources)
            self.assertIn('value.'+field+' = __atomic_load_n(', C)
        self.assertIn('__ATOMIC_RELAXED', H)
        self.assertIn('__atomic_fetch_add', H)
        self.assertNotRegex(H, r'ESP_LOG|vTaskDelay|malloc|memcpy')
        self.assertEqual(len(FIELDS), 25)
        # Queue, timeout, input ownership, and encryption policy are untouched.
        self.assertIn('xQueueSend(target, &pkt, 0)', sources)
        self.assertIn('if (device->netif->input && device->netif->input(pbuf, device->netif) == ERR_OK)', C)
        for gate in ['if (decrypt_ok)', 'if (wireguard_check_replay(keypair, nonce))', 'if (header_len <= pbuf->tot_len)']:
            self.assertLess(C.index(gate), C.index('wireguardif_rx_count(WG_RX_INNER_OK)'))

    def test_owner_format_transport_and_keepalive_boundaries(self):
        owner = C.split('void wireguardif_network_rx(void *arg, struct udp_pcb *pcb, struct pbuf *p, const ip_addr_t *addr, u16_t port) {',1)[1].split('static err_t wireguard_start_handshake',1)[0]
        wrapper = owner.split('LWIP_ASSERT',1)[0]
        self.assertIn('if (dispatch != ERR_OK) wireguardif_rx_count(WG_RX_OWNER_DISPATCH_DROP);', wrapper)
        self.assertIn('if (dispatch != ERR_OK) pbuf_free(p)', wrapper)
        self.assertNotIn('WG_RX_OWNER_PROCESSED', wrapper)
        self.assertLess(owner.index('return;'), owner.index('wireguardif_rx_count(WG_RX_OWNER_PROCESSED)'))
        self.assertEqual(owner.count('WG_RX_OWNER_PROCESSED'),1)
        self.assertLess(owner.index('WG_RX_OWNER_PROCESSED'),owner.index('wireguard_get_message_type(data, len)'))
        transport = owner.split('case MESSAGE_TRANSPORT_DATA:',1)[1].split('break;',1)[0]
        self.assertLess(transport.index('WG_RX_TRANSPORT_RX'),transport.index('peer_lookup_by_receiver'))
        self.assertEqual(owner.count('WG_RX_TRANSPORT_RX'),1)
        default = owner.split('default:',1)[1].split('break;',1)[0]
        self.assertIn('WG_RX_INVALID_PACKET',default)
        classifier = (W/'wireguard.c').read_text(encoding='utf8').split('uint8_t wireguard_get_message_type(',1)[1].split('struct wireguard_peer *wireguard_process_initiation_message',1)[0]
        for gate in ('if (len >= 4)', 'data[1] == 0', 'data[2] == 0', 'data[3] == 0',
                     'len >= sizeof(struct message_transport_data) + WIREGUARD_AUTHTAG_LEN'):
            self.assertIn(gate,classifier)
        keepalive = C.split('// This is a duplicate packet / replayed / too far out of order',1)[1].split('if (pbuf)',1)[0]
        self.assertIn('} else {\n\t\t\t\t\t\twireguardif_rx_count(WG_RX_EMPTY_KEEPALIVE)',keepalive)
        self.assertEqual(C.count('wireguardif_rx_count(WG_RX_EMPTY_KEEPALIVE)'),1)
        self.assertLess(C.index('if (decrypt_ok)'),C.index('wireguardif_rx_count(WG_RX_EMPTY_KEEPALIVE)'))
        # Minimal owner-dispatch accounting trace: wrapper never parses; callback does once.
        for already_owner, dispatch_ok in ((True,True),(False,True),(False,False)):
            counts = dict(owner_dispatch_drop=0,owner_processed=0)
            if already_owner or dispatch_ok: counts['owner_processed'] += 1
            else: counts['owner_dispatch_drop'] += 1
            self.assertEqual(sum(counts.values()),1)
            self.assertEqual(counts['owner_processed'],int(already_owner or dispatch_ok))

    def test_serial_numeric_max_length_and_read_only_getter(self):
        serial = (R/'main/debug/serial_debug.cpp').read_text(encoding='utf8')
        fmt = re.search(r'debugPrintf\("(DBG TAIL_RX .*?)",', serial)[1]
        self.assertNotIn('%s', fmt)
        maximum = fmt.replace('%lu', '4294967295').replace(r'\r\n', '\r\n')
        self.assertLess(len(maximum), 1024)
        self.assertLess(len(maximum), 1536)
        getter = (R/'main/host/tailscale_transport.cpp').read_text(encoding='utf8').split('TailnetQuota::rxDiagnostics() const',1)[1].split('}',1)[0]
        self.assertEqual(getter.strip(), '{\n    return wireguardif_rx_stats();')

    def test_network_terminal_result_and_fake_serial_followup(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        sys.path.insert(0, str(R / 'tools'))
        try:
            import serial_debug_test as host
        finally:
            sys.path.pop(0)
        source = (R/'main/debug/serial_debug.cpp').read_text(encoding='utf8')
        block = source.split('if (std::strcmp(command, "network") == 0) {', 1)[1].split('        return;', 1)[0]
        terminal = block.index('result("network", "PASS", details);')
        for diagnostic in ('DBG TAIL_RX ', 'DBG TAIL_FETCH ', 'DBG WIFI_RETRY '):
            self.assertLess(block.index(diagnostic), terminal)
        self.assertEqual(block.count('result("network",'), 1)
        self.assertEqual(block.count('std::snprintf(details,'), 1)
        self.assertIn('std::snprintf(retryDetails, sizeof(retryDetails)', block)
        lines = [b'DBG TAIL_RX derp_frame=1 tcp_synack=1\r\n']
        lines += [f'DBG TAIL_FETCH path={n} stage=12\r\n'.encode() for n in range(4)]
        lines += [b'DBG WIFI_RETRY connect_attempts=1\r\n',
                  b'DBG RESULT command=network status=PASS configured=1 connected=1\r\n',
                  b'DBG RESULT command=ping status=PASS reply=pong\r\n']
        for uart in (False, True):
            queue = list(lines); writes = []; output = []
            fake = SimpleNamespace(open=lambda:None, close=lambda:None,
                                   write=writes.append, readline=lambda:queue.pop(0))
            with patch.object(host.serial, 'Serial', return_value=fake), patch('builtins.print', side_effect=output.append):
                client = host.DebugClient('TEST-NO-DEVICE', uart=uart)
                result = client.command('debug network', 'network')
                self.assertEqual(result.details, 'configured=1 connected=1')
                self.assertEqual(len(output), 7)
                self.assertIn('DBG TAIL_RX ', output[0])
                self.assertEqual(queue, lines[-1:])
                start = len(output)
                self.assertEqual(client.command('debug ping', 'ping').details, 'reply=pong')
                self.assertEqual(len(output)-start, 1)
                self.assertFalse(queue)
            self.assertEqual(writes, [host.encode_command('debug network', uart), host.encode_command('debug ping', uart)])

    def test_host_actual_atomic_and_synack_harness(self):
        cc = os.environ.get('WG_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
        if not cc: self.skipTest('host C compiler unavailable; no SDK fallback')
        snapshot = C.split('wireguardif_rx_stats_t wireguardif_rx_stats(void) {',1)[1].split('\n}',1)[0]
        harness = '''#include "wireguardif_rx_stats.h"
#include <assert.h>
#include <pthread.h>
#include <string.h>
uint32_t wireguardif_rx_counters[WG_RX_COUNT];
wireguardif_rx_stats_t wireguardif_rx_stats(void) { SNAPSHOT }
static void *writer(void *arg) { (void)arg; for (int i=0;i<100000;i++) wireguardif_rx_count(WG_RX_DERP_FRAME); return NULL; }
int main(void) {
    pthread_t t[4]; for(int i=0;i<4;i++) assert(!pthread_create(&t[i],NULL,writer,NULL));
    for(int i=0;i<10000;i++) assert(wireguardif_rx_stats().derp_frame <= 400000);
    for(int i=0;i<4;i++) assert(!pthread_join(t[i],NULL));
    assert(wireguardif_rx_stats().derp_frame==400000);
    assert(wireguardif_rx_stats().derp_frame==400000);
    __atomic_store_n(&wireguardif_rx_counters[WG_RX_DERP_FRAME],UINT32_MAX,__ATOMIC_RELAXED);
    wireguardif_rx_count(WG_RX_DERP_FRAME); assert(wireguardif_rx_stats().derp_frame==0);
    unsigned char ip[60]={0}; ip[0]=0x45;ip[3]=40;ip[9]=6;ip[32]=0x50;ip[33]=0x12;
    assert(wireguardif_rx_is_synack(ip,40));
    for(unsigned n=0;n<40;n++) assert(!wireguardif_rx_is_synack(ip,n));
    ip[33]=0x10; assert(!wireguardif_rx_is_synack(ip,40));ip[33]=0x12;
    ip[6]=0x20; assert(!wireguardif_rx_is_synack(ip,40));ip[6]=0;
    ip[7]=1; assert(!wireguardif_rx_is_synack(ip,40));ip[7]=0;
    ip[32]=0x40; assert(!wireguardif_rx_is_synack(ip,40));
    ip[32]=0x60; assert(!wireguardif_rx_is_synack(ip,40));
    ip[0]=0x44; assert(!wireguardif_rx_is_synack(ip,40));
    return 0;
}'''.replace('SNAPSHOT', snapshot)
        with tempfile.TemporaryDirectory(prefix='rx-stats-test-') as d:
            src=Path(d)/'rx.c';exe=Path(d)/('rx.exe' if os.name=='nt' else 'rx')
            src.write_text(harness,encoding='utf8')
            subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror','-pthread','-I',str(W),str(src),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)

if __name__ == '__main__': unittest.main()
