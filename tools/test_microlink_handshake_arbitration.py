"""Actual owner helper/PONG + protocol expiration RV32 sequences; no hardware or crypto claim."""
from pathlib import Path
import unittest
import test_microlink_peer_probe as base
RUN=base.ROOT/'.artifacts/private/mosaico/peer-handshake-tests-001'
CASES=r'''
static bool requested;
static void prepare_pending(){auto &p=device.peers[0];p.handshake={true,true,7};p.last_initiation_tx=9000;p.active=true;}
static void again(){register_probe(&ml,0);pong(1);}
static bool pending_intact(){auto &p=device.peers[0];return p.handshake.valid&&p.handshake.initiator&&p.handshake.local_index==7&&p.last_initiation_tx==9000&&!connects&&!p.ip.ip&&!p.connect_ip.ip&&!ml.peers[0].tried_initial_handshake;}
static int run_case(int id){setup();auto &p=device.peers[0];switch(id){
case 0:prepare_pending();pong(1);if(!pending_intact()||ml.peers[0].best_ip!=42)return 1;again();if(!pending_intact())return 2;
 // Acceptance model: response receiver index must retain the DERP initiation's 7.
 if(!p.handshake.valid||p.handshake.local_index!=7)return 3;p.curr_keypair.valid=true;return 0;
case 1:prepare_pending();now_ms=14000;pong(1);return connects==1&&p.handshake.local_index==8&&ml.peers[0].tried_initial_handshake?0:4;
case 2:ml.derp.connected=true;pong(1);return connects==1&&p.ip.ip==42?0:5;
case 3:case 4:{up=id==3;p.prev_keypair.valid=id==4;p.curr_keypair.sending_key[3]=91;p.prev_keypair.receiving_key[5]=92;p.curr_keypair.counter=73;p.handshake={true,true,7};p.last_initiation_tx=9000;p.active=true;p.send_handshake=true;auto expected=p;expected.connect_ip.ip=expected.ip.ip=42;expected.connect_port=expected.port=1234;
 // pong supplies up only for current-session case; previous remains independent.
 pong(1);expected.curr_keypair.valid=up;return !connects&&!memcmp(&p,&expected,sizeof p)&&!ml.peers[0].tried_initial_handshake?0:6;}
case 5:p.public_key[0]=99;pong(1);return !connects&&!p.connect_ip.ip&&!ml.peers[0].tried_initial_handshake?0:7;
case 6:p.valid=false;pong(1);return !connects&&!p.connect_ip.ip?0:8;
case 7:ml.peers[0].wg_peer_index=2;pong(1);return !connects&&!p.connect_ip.ip?0:9;
case 8:ml.peers[0].active=false;pong(1);return !connects&&!ml.peers[0].best_ip?0:10;
case 9:p.active=true;pong(1);return connects==1&&p.active?0:11;
case 10:p.active=false;pong(1);return connects==1&&!p.active?0:12;
case 11:prepare_pending();p.last_initiation_tx=0xfffffff0;now_ms=0x20;pong(1);return !connects&&p.handshake.local_index==7?0:13;
case 12:prepare_pending();now_ms=13999;pong(1);return !connects&&p.handshake.local_index==7?0:14;
case 13:owner_error=1;pong(1);return !connects&&!p.connect_ip.ip&&!ml.peers[0].tried_initial_handshake?0:15;
case 14:connect_error=1;pong(1);return connects==1&&!p.active&&!ml.peers[0].tried_initial_handshake?0:16;
case 15:nf.state=0;pong(1);return !connects&&!ml.peers[0].tried_initial_handshake?0:17;
case 16:{wg_direct_path_ctx c={};c.netif=&nf;c.index=-1;c.endpoint.ip=42;c.port=1234;memcpy(c.expected_key,p.public_key,32);wg_direct_path_in_owner(&c);return !connects&&!p.connect_ip.ip&&!c.started?0:18;}
case 17:prepare_pending();p.last_initiation_tx=0xfffffff0;now_ms=uint32_t(0xfffffff0u+5000u);pong(1);return connects==1&&p.handshake.local_index==8?0:19;
}return 99;}
extern "C" {volatile int test_results[18]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<18;++i)test_results[i]=run_case(i);test_done();}
'''

# Branch counters and getter invariants are exercised after the actual sequence.
COUNTER_CHECK=r"""
static int run_case(int id){int result=run_sequence(id);if(result)return result;
 microlink_peer_path_diag_t snap{},snap2{};auto beforeDevice=device;auto beforeMl=ml;
 unsigned oldCalls=critical_calls;microlink_peer_path_get_diagnostics(0);if(critical_calls!=oldCalls)return 90;
 microlink_peer_path_get_diagnostics(&snap);if(critical_calls!=oldCalls+2)return 91;
 microlink_peer_path_get_diagnostics(&snap2);if(memcmp(&snap,&snap2,sizeof snap)||memcmp(&beforeDevice,&device,sizeof device)||memcmp(&beforeMl,&ml,sizeof ml))return 92;
 if(snap.version!=1)return 93;
 if((id==0&&snap.pending_deferred!=2)||(id==5&&snap.key_mismatch!=1)||((id==6||id==7||id==15||id==16)&&snap.invalid_state!=1)||(id==13&&snap.owner_error!=1)||(id==14&&snap.connect_error!=1))return 94;
 if((id==3||id==4)&&snap.endpoint_promoted!=1)return 95;
 if((id==1||id==2||id==9||id==10||id==17)&&snap.oneshot_requested!=1)return 96;
 if((id==11||id==12)&&snap.pending_deferred!=1)return 97;
 if(id==8&&(snap.probe_stale!=1||snap.oneshot_requested))return 98;
 return 0;}
"""
class ArbitrationTests(unittest.TestCase):
 def test_actual_owner_and_pong(self):
  original_cases,original_run=base.CASES,base.RUN
  try:
   base.RUN=RUN
   cases=CASES.replace('static int run_case(', 'static int run_sequence(')
   marker='extern "C" {volatile int test_results'
   cases=cases.replace(marker,COUNTER_CHECK+marker,1)
   base.CASES=base.CASES.split('static int run_case(',1)[0]+cases
   self.assertEqual(base.execute(base.SOURCE.read_text(encoding='utf8'),'current',18),(0,)*18)
  finally:base.CASES,base.RUN=original_cases,original_run
 def test_snapshot_and_owner_scope(self):
  s=base.SOURCE.read_text(encoding='utf8');body=base.function(s,'static void wg_promote_direct_path(')
  self.assertIn('memcpy(c.expected_key, p->public_key, sizeof(c.expected_key));',body)
  self.assertIn('wireguardif_owner_call(wg_direct_path_in_owner, &c)',body)
  pong=base.function(s,'static void process_disco_pong(')
  self.assertNotIn('wireguardif_connect(',pong);self.assertNotIn('ml->derp.connected',pong)
  getter=base.function(s,'void microlink_peer_path_get_diagnostics(')
  self.assertIn('*out = ml_peer_path_diag;',getter)
  for token in ('wireguardif_', 'ml_get_time', 'ESP_LOG', 'printf', 'peer->', 'netif', 'send', 'socket'):
   self.assertNotIn(token,getter)
  serial=(base.ROOT/'main/debug/serial_debug.cpp').read_text(encoding='utf8')
  self.assertEqual(serial.count('DBG PEER_PATH '),1)
  self.assertIn('cached_only=1',serial)

if __name__=='__main__':unittest.main()
