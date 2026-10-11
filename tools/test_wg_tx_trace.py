"""Production TX trace helpers/DERP fault shim; no SDK/network/device IO."""
import os, re, shutil, subprocess, tempfile, unittest, sys
from pathlib import Path
from test_derp_tx_backpressure import function, BASE
SRC=BASE/'components/wireguard_lwip/src'
def native_harness(hook=True):
    wg=(SRC/'wireguardif.c').read_text(encoding="utf8")
    derp=(BASE/'src/ml_derp.c').read_text(encoding="utf8")
    header=(SRC/'wireguardif_rx_stats.h').read_text(encoding="utf8")
    # Host ABI only: Darwin's linker requires weak_import for an optional
    # undefined symbol. Keep production helper bodies and ESP/ELF weak ABI intact.
    host_weak = """#if defined(__APPLE__)
#define WG_TX_HOST_WEAK __attribute__((weak_import))
#else
#define WG_TX_HOST_WEAK __attribute__((weak))
#endif
"""
    header = host_weak + header.replace('__attribute__((weak));', 'WG_TX_HOST_WEAK;')
    helpers=wg[wg.index('typedef struct { uint32_t src_port'):wg.index('static err_t wireguardif_peer_output(')]
    functions=function(derp,'derp_send_packet')+function(derp,'ml_derp_queue_send')
    prefix=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
struct pbuf { struct pbuf *next; const void *payload; unsigned len,tot_len; };
static unsigned reads,largest_read;
static unsigned pbuf_copy_partial(const struct pbuf *p,void *dst,unsigned n,unsigned off) {
 ++reads;if(n>largest_read)largest_read=n;
 unsigned done=0;while(p && off>=p->len){off-=p->len;p=p->next;}
 while(p && done<n){unsigned k=p->len-off;if(k>n-done)k=n-done;memcpy((uint8_t*)dst+done,(const uint8_t*)p->payload+off,k);done+=k;off=0;p=p->next;}
 return done;
}
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 10
#define ESP_ERR_NO_MEM 11
#define ESP_ERR_TIMEOUT 12
#define DERP_FRAME_SEND_PACKET 4
#define ESP_LOGW(...) ((void)0)
#define pdTRUE 1
typedef int esp_err_t;
typedef struct { uint8_t *data;size_t len;unsigned frame_type;uint8_t dest_pubkey[32]; } ml_derp_tx_item_t;
typedef struct { void *derp_tx_queue; } microlink_t;
static void derp_trace_failure(microlink_t *ml,int ret) { (void)ml;(void)ret; }
static int alloc_fail,send_result=1,send_calls,recv_result,frame_result,frame_calls;
static ml_derp_tx_item_t queued;
static void *test_malloc(size_t n){return alloc_fail?NULL:malloc(n);}
#define malloc test_malloc
static int xQueueSend(void *q,const void *item,int t){(void)q;(void)t;++send_calls;if(send_result)queued=*(const ml_derp_tx_item_t*)item;return send_result;}
static int xQueueSendToFront(void *q,const void *item,int t){return xQueueSend(q,item,t);}
static int xQueueReceive(void *q,void *item,int t){(void)q;(void)t;if(recv_result){memset(item,0,sizeof(ml_derp_tx_item_t));return 1;}return 0;}
static int derp_write_frame(microlink_t *ml,uint8_t type,const uint8_t *data,uint32_t len){(void)ml;(void)data;assert(type==4 && len>=32);++frame_calls;return frame_result;}
static unsigned counts[8];static uint32_t fields[8];
'''
    hook_def=r'''
void mosaico_wg_tx_trace(uint32_t stage,uint32_t receiver,uint32_t lo,uint32_t hi,uint32_t sp,uint32_t dp,uint32_t seq,int32_t result){
 assert(stage<8);++counts[stage];fields[0]=stage;fields[1]=receiver;fields[2]=lo;fields[3]=hi;fields[4]=sp;fields[5]=dp;fields[6]=seq;fields[7]=(uint32_t)result;errno=EDOM;
}
'''
    suffix=r'''
int main(void){
 uint8_t ip[40]={0x45,0,0,40},pub[16]={4,0,0,0,0x78,0x56,0x34,0x12,0xff,0xff,0xff,0xff,1},key[32]={0};
 ip[9]=6;ip[20]=0x12;ip[21]=0x34;ip[22]=0;ip[23]=80;ip[24]=0x89;ip[25]=0xab;ip[26]=0xcd;ip[27]=0xef;ip[32]=0x50;ip[33]=2;
 struct pbuf tail={NULL,ip+7,33,33},head={&tail,ip,7,40};
 wg_tx_syn_t syn=wireguardif_tx_syn(&head);
 if(HOOK_PRESENT){
  assert(syn.syn && syn.src_port==0x1234 && syn.dst_port==80 && syn.seq==0x89abcdef);
  assert(reads==2 && largest_read==20);errno=EPIPE;wireguardif_tx_syn_note(1,syn,NULL,0);assert(errno==EPIPE);
  wireguardif_tx_syn_note(2,syn,pub,0);assert(fields[1]==0x12345678 && fields[2]==UINT32_MAX && fields[3]==1);
  struct pbuf pt={NULL,pub+9,7,7},ph={&pt,pub,9,16};wireguardif_tx_pbuf_note(3,&ph,1);assert(counts[3]==1 && fields[7]==1);
  ip[33]=0x12;assert(!wireguardif_tx_syn(&head).syn);ip[33]=2;ip[6]=0x20;assert(!wireguardif_tx_syn(&head).syn);ip[6]=0;
  ip[0]=0x44;assert(!wireguardif_tx_syn(&head).syn);ip[0]=0x45;ip[32]=0x40;assert(!wireguardif_tx_syn(&head).syn);ip[32]=0x50;
  head.tot_len=39;assert(!wireguardif_tx_syn(&head).syn);head.tot_len=40;tail.len=10;assert(!wireguardif_tx_syn(&head).syn);tail.len=33;
  uint8_t options[44]={0};memcpy(options,ip,20);memcpy(options+24,ip+20,20);options[0]=0x46;options[3]=44;
  struct pbuf op={NULL,options,44,44};assert(wireguardif_tx_syn(&op).syn);
  unsigned before=counts[3];pub[0]=1;wireguardif_tx_packet_note(3,pub,16,0);pub[0]=4;pub[1]=1;wireguardif_tx_packet_note(3,pub,16,0);pub[1]=0;wireguardif_tx_packet_note(3,pub,15,0);wireguardif_tx_packet_note(3,NULL,16,0);assert(counts[3]==before);
 }else{assert(!syn.syn && !reads);wireguardif_tx_pbuf_note(3,&head,1);assert(!reads);}
 microlink_t ml={0};errno=EPIPE;
 assert(ml_derp_queue_send(&ml,key,pub,16)==0);assert(send_calls==1 && errno==EPIPE);free(queued.data);
 alloc_fail=1;assert(ml_derp_queue_send(&ml,key,pub,16)==ESP_ERR_NO_MEM);assert(derp_send_packet(&ml,key,pub,16)==-1);alloc_fail=0;
 send_result=0;assert(ml_derp_queue_send(&ml,key,pub,16)==ESP_ERR_TIMEOUT);assert(send_calls==5);
 assert(ml_derp_queue_send(NULL,key,pub,16)==ESP_ERR_INVALID_ARG);
 frame_result=0;assert(derp_send_packet(&ml,key,pub,16)==0);frame_result=-1;assert(derp_send_packet(&ml,key,pub,16)==-1);assert(frame_calls==2);
 if(HOOK_PRESENT)assert(counts[4]==4 && counts[6]==3 && (int32_t)fields[7]==-1);
 puts("WG TX trace production helper/DERP fault shim: PASS");return 0;
}
'''
    return prefix+header+'\n#define HOOK_PRESENT '+str(int(hook))+'\n'+helpers+functions+(hook_def if hook else '')+suffix
class Tests(unittest.TestCase):
 def test_source_stage_coverage(self):
  wg=(SRC/'wireguardif.c').read_text(encoding="utf8");derp=(BASE/'src/ml_derp.c').read_text(encoding="utf8");mgr=(BASE/'src/ml_wg_mgr.c').read_text(encoding="utf8")
  self.assertIn('wireguardif_tx_syn_note(1, wireguardif_tx_syn(q), NULL, 0);',wg)
  for reason in range(1,7):self.assertRegex(wg,r'wireguardif_tx_syn_note\(7,[^;]*, '+str(reason)+r'\);')
  block=function(wg,'wireguardif_output_to_peer')
  self.assertLess(block.index('wireguard_encrypt_packet('),block.index('wireguardif_tx_syn_note(2,'));self.assertLess(block.index('wireguardif_tx_syn_note(2,'),block.index('result = wireguardif_peer_output('))
  self.assertIn('wireguardif_tx_pbuf_note(3, q, 0)',wg);self.assertIn('wireguardif_tx_pbuf_note(3, q, 1)',wg)
  self.assertIn('wireguardif_tx_packet_note(4, data, len, ERR_CONN)',mgr)
  for result in ['0 : 1','2','3']:self.assertIn('wireguardif_tx_packet_note(5,',derp);self.assertIn(', '+result+');' if result!='0 : 1' else 'ml->derp.connected ? 0 : 1);',derp)
  self.assertIn('wireguardif_tx_packet_note(6, data, len, ret);',function(derp,'derp_send_packet'))
 def test_native(self):
  cc=os.environ.get('WG_TX_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
  if not cc:self.skipTest('host compiler unavailable; exported harness can run on native host')
  self.run_compiler(cc,False)
 def test_syntax(self):
  cc=next(iter(Path.home().glob('.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-gcc.exe')),None)
  if not cc:self.skipTest('installed syntax compiler unavailable')
  self.run_compiler(str(cc),True)
 def run_compiler(self,cc,syntax):
  for hook in [False,True]:
   with tempfile.TemporaryDirectory() as d:
    p=Path(d)/'tx.c';exe=Path(d)/('tx.exe' if os.name=='nt' else 'tx');p.write_text(native_harness(hook),encoding="utf8")
    # Darwin host ABI link shim: permit the intentionally absent weak import.
    # No production source/helper or ESP link flags are changed.
    link_shim=['-Wl,-U,_mosaico_wg_tx_trace'] if not syntax and not hook and sys.platform=='darwin' else []
    subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror',str(p)]+(['-fsyntax-only'] if syntax else link_shim+['-o',str(exe)]),check=True)
    if not syntax:subprocess.run([str(exe)],check=True)
if __name__=='__main__':
 if len(sys.argv)==3 and sys.argv[1]=='--export-native-harness':Path(sys.argv[2]).write_text(native_harness(),encoding="utf8")
 else:unittest.main()

