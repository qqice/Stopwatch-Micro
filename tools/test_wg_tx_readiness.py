"""Native production getter vs independently extracted TX admission prefix; no device IO."""
import argparse,os,re,shutil,subprocess,tempfile,unittest
from pathlib import Path
from test_derp_tx_backpressure import BASE,function
WG=BASE/'components/wireguard_lwip/src'
SOURCE=WG/'wireguardif.c'

def native_harness(legacy=False):
 s=SOURCE.read_text(encoding='utf8')
 tx=function(s,'wireguardif_output_to_peer')
 # Preserve the real TX selector, role/age/counter gates. Stop at entry to
 # packet construction/encryption, without substituting the getter's helper.
 prefix=tx[tx.index('struct wireguard_keypair *keypair ='):tx.index('// Calculate the outgoing packet size')]
 tx_probe='static bool tx_admits(struct wireguard_peer *peer) {\n'+prefix+'return true;\n}}return false;\n}\n'
 query=s[s.index('typedef struct { struct netif *netif; u8_t peer_index; ip_addr_t *current_ip;'):s.index('typedef struct { struct netif *netif; u8_t peer_index; err_t result; } wireguardif_remove_peer_owner_ctx;')]
 # Legacy export is an explicit old-getter negative control, not an old
 # whole-source snapshot. The archived pre-fix harness proves the original.
 if legacy:
  query=query.replace('wireguardif_peer_can_tx(peer)','((peer->curr_keypair.valid) || (peer->prev_keypair.valid))')
 helper=function(s,'wireguardif_peer_can_tx') if not legacy and 'static bool wireguardif_peer_can_tx(' in s else ''
 connect=s[s.index('typedef struct { struct netif *netif; u8_t peer_index; err_t result; } wireguardif_connect_derp_owner_ctx;'):s.index('typedef struct { struct netif *netif; uint32_t src_ip;',s.index('wireguardif_connect_derp_owner_ctx;'))]
 constants='\n'.join(l for l in (WG/'wireguard.h').read_text(encoding='utf8').splitlines() if re.match(r'#define (REJECT_AFTER_(TIME|MESSAGES)|REKEY_TIMEOUT)\b',l))
 return r'''
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <string.h>
#include <stdio.h>
#include <stddef.h>
typedef int err_t;typedef uint8_t u8_t;typedef uint16_t u16_t;typedef uint32_t ip_addr_t;
#define ERR_OK 0
#define ERR_CONN -1
#define ERR_IF -2
#define ERR_ARG -3
#define WG_DEBUG(...) ((void)0)
struct wireguard_keypair {bool valid,initiator;uint32_t last_rx,keypair_millis;uint64_t sending_counter;unsigned char secret[32];};
struct handshake_state {bool valid,initiator;uint32_t index;unsigned char secret[32];};
struct wireguard_peer {struct handshake_state handshake;bool active,send_handshake;uint32_t last_initiation_tx;struct wireguard_keypair curr_keypair,prev_keypair,next_keypair;ip_addr_t ip;u16_t port;unsigned char secret[32];};
struct netif {struct wireguard_peer *peer;};
static uint32_t now_ms;static int owner,dispatches,dispatch_error,lookup_error,starts;
static uint32_t wireguard_sys_now(void){return now_ms;}
static bool wireguardif_owner_current(void){return owner;}
static err_t wireguardif_owner_call(void (*cb)(void*),void *arg){++dispatches;if(dispatch_error)return ERR_IF;assert(!owner);owner=1;cb(arg);owner=0;return ERR_OK;}
static err_t wireguardif_lookup_peer(struct netif *n,u8_t i,struct wireguard_peer **p){assert(owner);if(lookup_error||!n||!n->peer||i)return ERR_ARG;*p=n->peer;return ERR_OK;}
static void ip_addr_set_any(bool v6,ip_addr_t *ip){assert(!v6);*ip=0;}
static void wireguard_start_handshake(struct netif *n,struct wireguard_peer *p){assert(owner&&n->peer==p);++starts;++p->handshake.index;p->handshake.valid=true;p->handshake.initiator=true;p->last_initiation_tx=now_ms;}
typedef int wg_tx_syn_t;
static wg_tx_syn_t wireguardif_tx_syn(void *q){(void)q;return 0;}
#define q NULL
''' +constants+'\n'+function((WG/'wireguard.c').read_text(encoding='utf8'),'wireguard_expired')+tx_probe+helper+query+connect+('\n#define LEGACY 1\n' if legacy else '\n#define LEGACY 0\n')+r'''
static int mismatches,cases;
static struct wireguard_keypair good(void){struct wireguard_keypair k;memset(&k,0,sizeof(k));k.valid=true;k.initiator=true;k.keypair_millis=now_ms-1;memset(k.secret,0x5a,sizeof(k.secret));return k;}
static void check(const char *name,struct wireguard_peer *p,bool tx_expected){
 struct wireguard_peer before=*p;struct netif n={p};bool tx=tx_admits(p);assert(tx==tx_expected);assert(!memcmp(&before,p,sizeof(before)));
 for(int direct=0;direct<2;++direct){
  owner=direct;int calls=dispatches;ip_addr_t ip=0;u16_t port=0;
  int r=wireguardif_peer_is_up(&n,0,&ip,&port);assert(ip==p->ip&&port==p->port);
  assert(dispatches==calls+!direct);assert(!memcmp(&before,p,sizeof(before)));
  if((r==ERR_OK)!=tx){++mismatches;printf("mismatch %s getter_up=%d tx_enters_encrypt=%d\n",name,r==ERR_OK,tx);}
  if(!LEGACY)assert((r==ERR_OK)==tx);
 }
 ++cases;
}
static void pending_tests(void){
 for(int direct=0;direct<2;++direct){
  for(int kind=0;kind<4;++kind){
   now_ms=500000;struct wireguard_peer p;memset(&p,0,sizeof(p));p.ip=123;p.port=456;
   if(kind==0){p.curr_keypair=good();p.curr_keypair.keypair_millis=now_ms-REJECT_AFTER_TIME*1000;}
   if(kind==1)p.prev_keypair=good();
   if(kind==2){p.curr_keypair=good();p.curr_keypair.sending_counter=REJECT_AFTER_MESSAGES;}
   if(kind==3){p.curr_keypair=good();p.next_keypair=good();p.curr_keypair.sending_counter=17;p.next_keypair.sending_counter=23;}
   p.handshake.valid=true;p.handshake.initiator=true;p.handshake.index=12345;memset(p.handshake.secret,0xa5,32);p.last_initiation_tx=now_ms-1;
   struct netif n={&p};struct wireguard_peer before=p;owner=direct;int old_starts=starts,calls=dispatches;
   assert(wireguardif_peer_is_up(&n,0,NULL,NULL)==(kind==3?ERR_OK:ERR_CONN));
   assert(wireguardif_connect_derp(&n,0)==ERR_OK);assert(starts==old_starts);assert(dispatches==calls+2*!direct);assert(!p.ip&&!p.port);before.ip=0;before.port=0;assert(!memcmp(&before,&p,sizeof(p)));
  }
  for(int mode=0;mode<3;++mode){
   now_ms=500000;struct wireguard_peer p;memset(&p,0,sizeof(p));p.ip=123;p.port=456;p.curr_keypair=good();p.handshake.index=23456;
   p.handshake.valid=(mode!=1);p.handshake.initiator=(mode!=2);p.last_initiation_tx=mode==0?now_ms-REKEY_TIMEOUT*1000:now_ms-1;
   struct netif n={&p};struct wireguard_peer before=p;owner=direct;int old_starts=starts,calls=dispatches;
   assert(wireguardif_connect_derp(&n,0)==ERR_OK);assert(starts==old_starts+1&&dispatches==calls+!direct);
   assert(p.active&&p.send_handshake&&!p.ip&&!p.port&&p.handshake.index==before.handshake.index+1&&p.last_initiation_tx==now_ms);
   assert(!memcmp(&before.curr_keypair,&p.curr_keypair,sizeof(p.curr_keypair)));assert(!memcmp(&before.prev_keypair,&p.prev_keypair,sizeof(p.prev_keypair)));assert(!memcmp(&before.next_keypair,&p.next_keypair,sizeof(p.next_keypair)));
  }
 }
 puts("pending initiation preservation: PASS endpoint-only-demotion=expired/prev-only/counter-limit/usable-current; allowed=expired/no-pending/responder; direct+owner");
}
int main(void){
 struct wireguard_peer p;memset(&p,0,sizeof(p));p.ip=0x64400002;p.port=1234;now_ms=500000;
 check("no_keys",&p,false);
 p.next_keypair=good();check("next_only_not_tx_ready",&p,false);
 p.curr_keypair=good();check("usable_curr_with_next",&p,true);memset(&p.curr_keypair,0,sizeof(p.curr_keypair));
 p.prev_keypair=good();check("curr_invalid_prev_valid_not_selected",&p,false);
 p.curr_keypair=good();check("usable_curr",&p,true);
 p.curr_keypair.initiator=false;p.curr_keypair.last_rx=0;check("responder_unconfirmed_usable_prev",&p,true);
 p.prev_keypair.keypair_millis=now_ms-REJECT_AFTER_TIME*1000;check("responder_unconfirmed_expired_prev",&p,false);
 p.prev_keypair=good();p.prev_keypair.initiator=false;check("both_responder_unconfirmed",&p,false);
 p.prev_keypair.last_rx=1;check("responder_confirmed_prev",&p,true);
 p.curr_keypair=good();p.curr_keypair.keypair_millis=now_ms-REJECT_AFTER_TIME*1000;check("expired_curr_valid_prev_no_fallback",&p,false);
 p.curr_keypair.keypair_millis++;check("age_one_ms_before_expiry",&p,true);
 p.curr_keypair=good();p.curr_keypair.sending_counter=REJECT_AFTER_MESSAGES;check("counter_limit",&p,false);
 p.curr_keypair.sending_counter--;check("counter_one_before_limit",&p,true);
 now_ms=20;p.curr_keypair=good();p.curr_keypair.keypair_millis=UINT32_MAX-100;check("clock_wrap_fresh",&p,true);
 p.curr_keypair.keypair_millis=now_ms-REJECT_AFTER_TIME*1000;check("clock_wrap_expired",&p,false);
 p.curr_keypair=good();p.curr_keypair.initiator=false;p.curr_keypair.last_rx=1;check("confirmed_responder_curr",&p,true);
 struct wireguard_peer before=p;struct netif n={&p};ip_addr_t ip=77;u16_t port=88;owner=0;
 lookup_error=1;assert(wireguardif_peer_is_up(&n,0,&ip,&port)==ERR_ARG&&ip==77&&port==88);lookup_error=0;
 dispatch_error=1;assert(wireguardif_peer_is_up(&n,0,&ip,&port)==ERR_IF&&ip==77&&port==88);dispatch_error=0;
 assert(wireguardif_peer_is_up(&n,0,NULL,NULL)==ERR_OK);assert(!memcmp(&before,&p,sizeof(p)));
 if(LEGACY)assert(mismatches>=10);else {assert(!mismatches);pending_tests();}
 printf("WG TX readiness %s: PASS cases=%d mismatches=%d owner/endpoint/readonly=PASS\n",LEGACY?"legacy getter negative control":"fixed contract",cases,mismatches);return 0;
}
'''

class Tests(unittest.TestCase):
 def test_source_boundaries(self):
  s=SOURCE.read_text(encoding='utf8');h=function(s,'wireguardif_peer_can_tx')
  self.assertIn('const struct wireguard_peer *peer',h)
  for forbidden in ('keypair_destroy','encrypt','send_handshake','memset','crypto_zero'):
   self.assertNotIn(forbidden,h)
  self.assertNotRegex(h,r'->\w+\s*=(?!=)')
  query=s[s.index('err_t wireguardif_peer_is_up(struct netif *netif, u8_t peer_index, ip_addr_t *current_ip, u16_t *current_port) {'):]
  self.assertIn('wireguardif_peer_can_tx(peer)',query.split('wireguardif_remove_peer_owner_ctx')[0])
  connect=s[s.index('err_t wireguardif_connect_derp(struct netif *netif, u8_t peer_index) {'):].split('wireguardif_inject_packet_owner_ctx')[0]
  self.assertEqual(connect.count('ip_addr_set_any(false, &peer->ip);'),1)
  self.assertLess(connect.index('peer->port = 0;'),connect.index('if (peer->handshake.valid'))
  self.assertLess(connect.index('if (peer->handshake.valid'),connect.index('peer->last_initiation_tx = 0;'))
 def test_native(self):
  cc=os.environ.get('VPN_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
  if not cc:self.skipTest('native compiler unavailable: export for approved host')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'wg.c';p.write_text(native_harness());exe=Path(d)/('wg.exe' if os.name=='nt' else 'wg')
   subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-variable',str(p),'-o',str(exe)],check=True);subprocess.run([str(exe)],check=True)

if __name__=='__main__':
 if '--export-native-harness' in __import__('sys').argv:
  p=argparse.ArgumentParser();p.add_argument('--export-native-harness',type=Path,required=True);p.add_argument('--legacy',action='store_true');a=p.parse_args();a.export_native_harness.write_text(native_harness(a.legacy),encoding='utf8')
 else:unittest.main()

