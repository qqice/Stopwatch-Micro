"""Extracted production PONG -> trigger -> direct/DERP owner sequence on RV32.
Mock handshake construction only; no cryptography, scheduler, network or hardware claim.
"""
import hashlib
import re
import unittest
import test_microlink_peer_probe as base

WG = base.ROOT / 'components/microlink-source/components/microlink/components/wireguard_lwip/src'
RUN = base.ROOT / 'artifacts/microlink-trusted-handshake-tests'
ORIGINAL_FIXTURE = base.fixture


def fixture(source, legacy_priority=False, legacy_connect=False):
    wg = (WG / 'wireguardif.c').read_text(encoding='utf8')
    direct = wg[wg.index('typedef struct { struct netif *netif; u8_t peer_index; err_t result; } wireguardif_connect_owner_ctx;'):wg.index('typedef struct { struct netif *netif; u8_t peer_index; err_t result; } wireguardif_disconnect_owner_ctx;')]
    if legacy_connect:
        direct, n = re.subn(r'            /\* Promote the route.*?\n            }\n', '', direct, count=1, flags=re.S)
        assert n == 1
    relay = wg[wg.index('typedef struct { struct netif *netif; u8_t peer_index; err_t result; } wireguardif_connect_derp_owner_ctx;'):wg.index('typedef struct { struct netif *netif; uint32_t src_ip;')]
    trigger = base.function(source, 'esp_err_t ml_wg_mgr_trigger_handshake(')
    if legacy_priority:
        token = '    bool trusted_direct ='
        pos = trigger.index(token)
        trigger = trigger[:pos] + '''    if (ml->derp.connected) {
        wireguardif_connect_derp(netif, (u8_t)p->wg_peer_index);
        return ESP_OK;
    }
''' + trigger[pos:]
    prefix = base.PREFIX.replace('return 100;', 'return now_ms;')
    prefix = re.sub(r'static int wireguardif_connect\(netif\*n,u8_t i\).*?\n', '', prefix)
    prefix = re.sub(r'static int wireguardif_update_endpoint\(netif\*.*?\n', '', prefix)
    prefix += r'''
#define ERR_IF -2
#define ERR_ARG -3
#define WG_DEBUG(...) ((void)0)
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_STATE -2
#define ESP_ERR_NOT_FOUND -3
typedef int esp_err_t;
static int relay_calls, update_error, lookup_error;
static bool wireguardif_owner_current(){return in_owner;}
static bool ip_addr_isany(const ip_addr_t *p){return !p->ip;}
static void ip_addr_set_any(bool,ip_addr_t*p){p->ip=0;}
static int wireguardif_lookup_peer(netif*n,u8_t i,wireguard_peer**p){if(lookup_error||!n||!n->state||i>=2||!((wireguard_device*)n->state)->peers[i].valid)return ERR_ARG;*p=&((wireguard_device*)n->state)->peers[i];return ERR_OK;}
static void wireguard_start_handshake(netif*,wireguard_peer*p){++connects;p->handshake={true,true,p->handshake.local_index+1};p->last_initiation_tx=now_ms;}
static int wireguardif_update_endpoint(netif*n,u8_t i,const ip_addr_t*p,u16_t port){++updates;if(update_error)return ERR_IF;auto &w=((wireguard_device*)n->state)->peers[i];w.connect_ip=*p;w.connect_port=port;return ERR_OK;}
static int find_peer_by_ip(microlink_t*,uint32_t ip){return ip==1?0:-1;}
'''
    # Count relay selection independently of actual init construction.
    relay = relay.replace('err_t wireguardif_connect_derp(struct netif *netif, u8_t peer_index) {', 'err_t wireguardif_connect_derp(struct netif *netif, u8_t peer_index) {\n    if (!in_owner) ++relay_calls;')
    prefix += direct + relay
    old_prefix, old_cases = base.PREFIX, base.CASES
    try:
        base.PREFIX = prefix
        base.CASES = trigger + base.CASES.split('static int run_case(', 1)[0] + CASES
        return ORIGINAL_FIXTURE(source)
    finally:
        base.PREFIX, base.CASES = old_prefix, old_cases


CASES = r'''
static void ready(){setup();relay_calls=update_error=lookup_error=0;in_owner=false;ml.derp.connected=true;ml.peers[0].has_direct_path=true;ml.peers[0].trust_until_ms=20000;ml.peers[0].best_ip=42;ml.peers[0].best_port=1234;}
static int trigger(){return ml_wg_mgr_trigger_handshake(&ml,1);}
static void pending(){auto &p=device.peers[0];p.handshake={true,true,7};p.last_initiation_tx=9000;p.active=false;p.send_handshake=false;p.curr_keypair.sending_key[3]=99;p.prev_keypair.receiving_key[5]=77;}
static bool intact(){auto &p=device.peers[0];return p.handshake.valid&&p.handshake.initiator&&p.handshake.local_index==7&&p.last_initiation_tx==9000&&!p.send_handshake&&!connects&&p.curr_keypair.sending_key[3]==99&&p.prev_keypair.receiving_key[5]==77;}
static int run_sequence(int id){ready();auto &p=device.peers[0];auto &m=ml.peers[0];switch(id){
case 0:trigger();return !relay_calls&&connects==1&&p.ip.ip==42?0:1;
case 1:m.has_direct_path=false;trigger();return relay_calls==1&&connects==1&&!updates&&!p.ip.ip?0:2;
case 2:m.trust_until_ms=now_ms;trigger();return relay_calls==1&&!updates?0:3;
case 3:m.trust_until_ms=now_ms-1;trigger();return relay_calls==1&&!updates?0:4;
case 4:up=true;p.curr_keypair.valid=true;{auto before=p;trigger();return !connects&&!updates&&!relay_calls&&!memcmp(&before,&p,sizeof p)?0:5;}
case 5:m.has_direct_path=false;ml.derp.connected=false;trigger();return connects==1&&!relay_calls&&p.ip.ip==42?0:6;
case 6:m.best_ip=0;trigger();return relay_calls==1&&!updates?0:7;
case 7:m.best_port=0;trigger();return relay_calls==1&&!updates?0:8;
case 8:ml.derp.connected=false;m.best_ip=0;trigger();return !connects&&!updates&&!relay_calls?0:9;
case 9:pending();trigger();trigger();return intact()&&p.active&&p.ip.ip==42&&!relay_calls&&updates==2?0:10;
case 10:pending();now_ms=13999;trigger();return intact()&&p.active&&p.ip.ip==42?0:11;
case 11:pending();now_ms=14000;trigger();return connects==1&&p.handshake.local_index==8&&p.last_initiation_tx==14000&&p.ip.ip==42?0:12;
case 12:pending();p.handshake.initiator=false;trigger();return connects==1&&p.handshake.local_index==8?0:13;
case 13:pending();p.handshake.valid=false;trigger();return connects==1&&p.handshake.local_index==8?0:14;
case 14:pending();p.prev_keypair.valid=true;{auto prev=p.prev_keypair;trigger();return intact()&&!memcmp(&prev,&p.prev_keypair,sizeof prev)&&p.ip.ip==42?0:15;}
case 15:pending();now_ms=20;p.last_initiation_tx=0xfffffff0;trigger();return !connects&&p.handshake.local_index==7&&p.last_initiation_tx==0xfffffff0?0:16;
case 16:pending();m.has_direct_path=false;trigger();return intact()&&!p.ip.ip&&relay_calls==1?0:17;
case 17:pending();m.has_direct_path=false;m.best_ip=0;m.best_port=0;m.trust_until_ms=0;
 pong(1);if(p.ip.ip||p.connect_ip.ip||ml_peer_path_diag.pending_deferred!=1||m.best_ip!=42||!m.has_direct_path)return 18;
 trigger();if(!intact()||p.ip.ip!=42||!p.active||relay_calls)return 19;
 now_ms=14000;trigger();return connects==1&&p.handshake.local_index==8&&p.ip.ip==42?0:20;
case 18:update_error=1;p.connect_ip.ip=99;p.connect_port=88;return trigger()==ESP_FAIL&&!connects&&!relay_calls&&!p.ip.ip&&p.connect_ip.ip==99?0:21;
case 19:lookup_error=1;return trigger()==ESP_FAIL&&!connects?0:22;
case 20:owner_error=1;return trigger()==ESP_FAIL&&!connects?0:23;
}return 99;}
static int run_case(int id){return run_sequence(id);}
extern "C" {volatile int test_results[21]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<21;++i)test_results[i]=run_case(i);test_done();}
'''


class TrustedHandshakeTests(unittest.TestCase):
    def execute(self, label, **options):
        old_fixture, old_run = base.fixture, base.RUN
        try:
            base.fixture = lambda s: fixture(s, **options)
            base.RUN = RUN
            results = base.execute(base.SOURCE.read_text(encoding='utf8'), label, 21)
            log = RUN / (label + '-execution.log')
            details = log.read_text(encoding='utf8').split('Source SHA256=', 1)[1]
            wg_hash = hashlib.sha256((WG / 'wireguardif.c').read_bytes()).hexdigest()
            log.write_text('Actual PONG/owner promotion, trigger, connect, DERP and expiration; '
                           'mock peer-up getter, endpoint update, owner dispatch and handshake construction. '
                           'No crypto/network/hardware claim. ml_wg_mgr Source SHA256=' + details +
                           'wireguardif SHA256=' + wg_hash + '\n', encoding='utf8')
            return results
        finally:
            base.fixture, base.RUN = old_fixture, old_run

    def test_production_sequence(self):
        self.assertEqual(self.execute('fixed'), (0,) * 21)

    def test_old_derp_priority_negative_control(self):
        results = self.execute('old-derp-priority', legacy_priority=True)
        self.assertNotEqual(results[0], 0)
        self.assertNotEqual(results[17], 0)

    def test_old_connect_overwrite_negative_control(self):
        results = self.execute('old-connect-overwrite', legacy_connect=True)
        self.assertNotEqual(results[9], 0)
        self.assertNotEqual(results[17], 0)


if __name__ == '__main__':
    unittest.main()
