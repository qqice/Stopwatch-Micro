"""RV32 extracted readd, route-owner, PONG and trust-expiry statements.
Mocks WG add/network calls; no device, crypto or scheduler correctness claim.
"""
from pathlib import Path
from unittest.mock import patch
import unittest
import test_microlink_peer_probe as base
ROOT=base.ROOT
SOURCE=base.SOURCE
RUN=ROOT/'artifacts/private/peer-readd-r56/tests'
BEFORE=ROOT/'artifacts/private/peer-readd-r56/ml_wg_mgr.before.c'

EXTRA=r'''
#define ML_MAX_PEERS 2
#define ML_MAX_ENDPOINTS 2
#define UINT64_MAX (~(uint64_t)0)
#define ESP_LOGE(...) ((void)0)
static void ip_addr_set_any(bool,ip_addr_t*p){p->ip=0;}
static char* strncpy(char*d,const char*s,unsigned n){unsigned i=0;for(;i<n&&s[i];++i)d[i]=s[i];for(;i<n;++i)d[i]=0;return d;}
static bool ml_config_peer_is_allowed(int,uint32_t){return true;}
static void microlink_ip_to_str(uint32_t,char*){}
static void wireguardif_remove_peer(netif*,int){}
static int relay_calls, ping_calls, cmm_calls;
static void disco_send_call_me_maybe(microlink_t*,int){++cmm_calls;}
static bool ml_at_socket_is_ready(){return false;}
static int wireguardif_connect_derp(netif*n,u8_t i){++relay_calls;auto&w=((wireguard_device*)n->state)->peers[i];w.ip.ip=w.connect_ip.ip=w.port=w.connect_port=0;return 0;}
static void disco_send_ping_to_peer(microlink_t*,int,bool){++ping_calls;}
'''

CASES=r'''
static int run_sequence(int){return 0;}
static ml_peer_update_t update;
static void ready(){setup();up=true;ml.peers[0].vpn_ip=99;update={};update.vpn_ip=99;update.public_key[0]=11;update.disco_key[0]=1;update.hostname[0]='x';update.derp_region=901;update.endpoint_count=1;update.endpoints[0].ip=456;update.endpoints[0].port=789;relay_calls=ping_calls=cmm_calls=0;}
static bool route_zero(const wireguard_peer&w){return !w.ip.ip&&!w.connect_ip.ip&&!w.port&&!w.connect_port;}
static int run_case(int id){ready();now_ms+=id*20000;auto&m=ml.peers[0];auto&w=device.peers[0];
 if(id==0){
  pong(1);register_probe(&ml,0);auto probes=pending_probes[0];auto before=m;auto wg=w;
  m.last_ping_sent_ms=31;m.last_send_ms=32;m.last_upgrade_ms=33;m.tried_initial_handshake=true;before=m;
  readd(&ml,&update);
  if(!m.has_direct_path||m.best_ip!=before.best_ip||m.best_port!=before.best_port||m.trust_until_ms!=before.trust_until_ms||m.last_pong_recv_ms!=before.last_pong_recv_ms||m.last_ping_sent_ms!=31||m.last_send_ms!=32||m.last_upgrade_ms!=33||!m.tried_initial_handshake||m.wg_peer_index!=0||memcmp(&probes,&pending_probes[0],sizeof probes)||memcmp(&wg,&w,sizeof w))return 1;
  if(m.derp_region!=901||m.endpoint_count!=1||m.endpoints[0].ip!=456||m.hostname[0]!='x')return 2;
  readd(&ml,&update);if(!m.has_direct_path||memcmp(&probes,&pending_probes[0],sizeof probes))return 3;
  now_ms=(uint32_t)m.trust_until_ms+1;expire(&ml,0,now_ms);
  return !m.has_direct_path&&relay_calls==1&&ping_calls>=1&&route_zero(w)?0:4;
 }
 if(id==1||id==2){
  pong(1);register_probe(&ml,0);w.active=true;w.handshake={true,true,77};w.last_initiation_tx=1234;w.curr_keypair.sending_key[3]=88;w.prev_keypair.receiving_key[4]=99;
  auto expected=w;expected.ip.ip=expected.connect_ip.ip=expected.port=expected.connect_port=0;
  if(id==1)update.disco_key[0]=2;else update.vpn_ip=100;
  readd(&ml,&update);
  return !m.has_direct_path&&!m.trust_until_ms&&!m.best_ip&&!m.best_port&&!pending_probes[0].active&&!memcmp(&expected,&w,sizeof w)&&!connects?0:5;
 }
 if(id>=3&&id<=6){
  w.ip.ip=w.connect_ip.ip=42;w.port=w.connect_port=123;auto before=device;
  uint8_t key[32]={11};int index=0;
  if(id==3)index=-1;if(id==4)index=2;if(id==5)key[0]=12;if(id==6)owner_error=1;
  wg_retire_route(&nf,index,key);
  return !memcmp(&before,&device,sizeof device)?0:6;
 }
 if(id==7){m.active=false;ml.peer_count=0;readd(&ml,&update);return m.active&&m.wg_peer_index==0&&!m.has_direct_path&&!m.tried_initial_handshake&&!connects?0:7;}
 if(id==8){w.valid=false;auto before=w;wg_retire_route(&nf,0,update.public_key);return !memcmp(&before,&w,sizeof w)?0:8;}
 if(id==9){pong(1);auto before=w;readd(&ml,&update);
  if(memcmp(&before,&w,sizeof w)||w.ip.ip!=42)return 91;
  return m.has_direct_path?0:100; // negative control: manager cleared, WG UDP retained
 }
 if(id>=10&&id<=15){
  // Identical live map vs NVS, rebuild WG-index loss, zero tracking,
  // changed advertised endpoint and genuinely new peer.
  pong(1);register_probe(&ml,0);m.last_ping_sent_ms=11;
  m.endpoint_count=update.endpoint_count;m.endpoints[0].ip=update.endpoints[0].ip;m.endpoints[0].port=update.endpoints[0].port;
  if(id==11){m.wg_peer_index=-1;m.has_direct_path=false;m.best_ip=0;m.best_port=0;m.trust_until_ms=m.last_ping_sent_ms=m.last_pong_recv_ms=0;}
  if(id==12)m.wg_peer_index=-1;
  if(id==13){m.has_direct_path=false;m.best_ip=0;m.best_port=0;m.trust_until_ms=m.last_ping_sent_ms=m.last_pong_recv_ms=0;m.tried_initial_handshake=false;}
  if(id==14)update.endpoints[0].ip=777;
  if(id==15){m.active=false;ml.peer_count=0;}
  auto probes=pending_probes[0];auto before=m;int prior=ping_calls;
  readd(&ml,&update);
  if(cmm_calls!=(id==10?0:1)||ping_calls-prior!=(id==10?0:1))return 10+id;
  if(id!=15 && memcmp(&probes,&pending_probes[0],sizeof probes))return 30+id;
  if(id==10||id==12||id==14){if(m.best_ip!=before.best_ip||m.trust_until_ms!=before.trust_until_ms||!m.has_direct_path)return 50+id;}
  return 0;
 }
 return 99;
}
extern "C" {volatile int test_results[16]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<16;++i)test_results[i]=run_case(i);test_done();}
'''

def fixture(source):
    current=SOURCE.read_text(encoding='utf8')
    # Existing PONG harness supplies exact production probes, PONG and promotion.
    prefix=base.PREFIX.replace('return 100;', 'return now_ms;').replace('char hostname[32];','char hostname[64];uint32_t vpn_ip;uint16_t derp_region;int endpoint_count;struct {uint32_t ip;uint16_t port;bool is_ipv6;}endpoints[2];uint64_t last_ping_sent_ms,last_send_ms,last_upgrade_ms;')
    prefix=prefix.replace('int peer_count;void*wg_netif;', 'int peer_count;int config_httpd;struct{uint32_t priority_peer_ip;}config;void*wg_netif;')
    # Update uses only public manager metadata, not live tracking.
    prefix+='typedef ml_peer_t ml_peer_update_t;\n'+EXTRA
    owner=current[current.index('/* Identity replacement retires'):current.index('/* Serialize PONG path promotion')]
    add=base.function(source,'static int add_peer(')
    manager=add[add.index('    /* Peer allowlist'):add.index('    char ip_str')]
    # Extract actual post-WGadd success hook. The mock WGadd preserves same-key
    # state, matching the source-bound production reuse branch.
    hook=''
    if 'wg_retire_route(netif, wg_peer_idx' in add:
        hook='if(existing && !same_identity) wg_retire_route((netif*)ml->wg_netif,0,p->public_key);'
    tail=add[add.index('    /* Send CallMeMaybe'):add.index('    /* Notify app')]
    readd='static int readd(microlink_t*ml,const ml_peer_update_t*update){'+manager+'if(ml->wg_netif){p->wg_peer_index=0;'+hook+'}'+tail+'return idx;}\n'
    process=current[current.index('        if (p->has_direct_path && now > p->trust_until_ms) {'):current.index('        if (!peer_allowed) continue;',current.index('        if (p->has_direct_path && now > p->trust_until_ms) {'))]
    expire='static void expire(microlink_t*ml,int i,uint64_t now){ml_peer_t*p=&ml->peers[i];bool peer_allowed=true;'+process+'}\n'
    setup=base.CASES.split('static int run_case(',1)[0]
    with patch.object(base,'PREFIX',prefix),patch.object(base,'CASES',base.function(current,'static int find_peer_by_key(')+owner+readd+expire+setup+CASES):
        return ORIGINAL(current)

ORIGINAL=base.fixture
class ReaddTests(unittest.TestCase):
    def execute(self, source,label):
        with patch.object(base,'RUN',RUN),patch.object(base,'fixture',fixture):
            return base.execute(source,label,count=16)
    def test_current_and_negative_control(self):
        source=SOURCE.read_text(encoding='utf8')
        self.assertEqual(self.execute(source,'current'),(0,)*16)
        old=self.execute(BEFORE.read_text(encoding='utf8'),'before')
        self.assertNotEqual(old[0],0)
        self.assertNotEqual(old[1],0)
        self.assertNotEqual(old[2],0)
        self.assertEqual(old[9],100)
    def test_source_binding(self):
        s=SOURCE.read_text(encoding='utf8');add=base.function(s,'static int add_peer(')
        self.assertLess(add.index('bool same_identity'),add.index('p->vpn_ip ='))
        self.assertIn('if (!same_identity) disco_cancel_peer_probes(idx);',add)
        self.assertIn('if (existing && !same_identity)\n                wg_retire_route(netif, wg_peer_idx, p->public_key);',add)
        self.assertEqual(add.count('if (initial_discovery && !ml_at_socket_is_ready())'),2)
        wg=(ROOT/'components/microlink-source/components/microlink/components/wireguard_lwip/src/wireguardif.c').read_text(encoding='utf8')
        addwg=base.function(wg,'err_t wireguardif_add_peer(struct netif *netif, struct wireguardif_peer *p, u8_t *peer_index) {')
        self.assertLess(addwg.index('if (!peer) {'),addwg.index('peer->connect_ip = p->endpoint_ip;'))
if __name__=='__main__':unittest.main()
