"""Bounded source contracts + owner-dispatch model (not a hardware/runtime test)."""
import re
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'components/microlink-source/components/microlink'
SRC = BASE / 'components/wireguard_lwip/src'

class Owner:
    def __init__(self, locking):
        self.locking = locking
        self.owner = False
        self.dispatches = 0
        self.depth = 0
        self.fail = False
    def call(self, fn):
        if self.owner:
            fn()
            return 0
        self.dispatches += 1
        if self.fail:
            return -1
        self.owner = True
        self.depth += 1
        assert self.depth == 1, 'recursive non-recursive core lock'
        try:
            fn()
        finally:
            self.depth -= 1
            self.owner = False
        return 0

class WGOwnerTests(unittest.TestCase):
    def test_nested_owner_inline_both_profiles(self):
        for locking in (False, True):
            o = Owner(locking)
            mutations = []
            self.assertEqual(o.call(lambda: o.call(lambda: mutations.append(1))), 0)
            self.assertEqual(mutations, [1])
            self.assertEqual(o.dispatches, 1)
            self.assertFalse(o.owner)
    def test_failed_dispatch_no_mutation(self):
        o = Owner(False); o.fail = True
        mutations = []
        self.assertNotEqual(o.call(lambda: mutations.append(1)), 0)
        self.assertEqual(mutations, [])
    def test_actual_helper_contract(self):
        h = (SRC / 'wireguardif_owner.h').read_text(encoding='utf-8')
        self.assertIn('sys_thread_tcpip(LWIP_CORE_LOCK_QUERY_HOLDER)', h)
        self.assertRegex(h, r'if \(wireguardif_owner_current\(\)\)\s*\{\s*fn\(ctx\);\s*return ERR_OK;')
        self.assertIn('return tcpip_callback_wait(fn, ctx);', h)
        self.assertNotIn('LOCK_TCPIP_CORE()', h)
    def test_all_shared_entrypoints_dispatch_before_state(self):
        s = (SRC / 'wireguardif.c').read_text(encoding='utf-8')
        names = ['disable_socket_bind','output','network_rx','connect','disconnect','peer_is_up',
                 'remove_peer','shutdown','update_endpoint','add_peer','periodic','init',
                 'set_derp_output','set_udp_output','force_derp_output','connect_derp','inject_packet']
        for suffix in names:
            name = 'wireguardif_' + suffix
            self.assertRegex(s, rf'(?:void|err_t) {name}\([^;]*?\) \{{\s*if \(!wireguardif_owner_current\(\)\)')
            self.assertIn(f'wireguardif_owner_call({name}_owner_cb, &c)', s)
        self.assertIn('if (dispatch != ERR_OK) pbuf_free(p)', s)
    def test_manager_no_raw_unowned_peer_state(self):
        s = (BASE/'src/ml_wg_mgr.c').read_text(encoding='utf-8')
        self.assertEqual(s.count('struct wireguard_device *dev'), 2) # owner init + owner peer callback
        self.assertIn('LWIP_ASSERT_CORE_LOCKED();', s)
        for name in ('wg_init_interface', 'wg_update_vpn_ip'):
            self.assertIn(f'wireguardif_owner_call({name}_owner_cb, &c)', s)
        proc = s[s.index('static void process_wg_packet'):s.index('static void process_wg_packet')+2000]
        self.assertNotIn('sendto(', proc)
    def test_udp_raw_callbacks_and_worker_cmm(self):
        s = (BASE/'src/ml_udp.c').read_text(encoding='utf-8')
        for name in ('udp_create_in_owner','udp_remove_in_owner','udp_send_in_owner'):
            self.assertIn(f'wireguardif_owner_call({name}', s)
        create = s[s.index('static void udp_create_in_owner'):s.index('static void udp_remove_in_owner')]
        self.assertNotIn('ml_wg_mgr_send_cmm', create)
        self.assertIn('udp_remove(sock->pcb);\n        sock->pcb = NULL;', s)
        self.assertIn('retaining live socket', s)
    def test_diag_bounded_nonfatal(self):
        s = (SRC/'wireguardif_diag.h').read_text(encoding='utf-8')
        self.assertIn('if (*budget >= 2) return;', s)
        self.assertIn('heap_caps_check_integrity_addr', s)
        self.assertNotIn('abort(', s)
        self.assertNotIn('heap_caps_check_integrity_all', s)
        lib = (SRC/'wireguardif.c').read_text(encoding='utf-8')
        for stage in ('response_before','response_after','rx_before_free'):
            self.assertIn(f'"{stage}"', lib)
    def test_derp_queue_zero_wait(self):
        s = (BASE/'src/ml_derp.c').read_text(encoding='utf-8')
        body=s[s.index('esp_err_t ml_derp_queue_send'):s.index('/* ============================================================================',s.index('esp_err_t ml_derp_queue_send'))]
        self.assertNotIn('sendto(', body)
        self.assertNotIn('portMAX_DELAY', body)
        for call in re.findall(r'xQueue\w+\([^;]*?\)', body):
            self.assertRegex(call, r', 0\)$')

if __name__ == '__main__':
    unittest.main()
