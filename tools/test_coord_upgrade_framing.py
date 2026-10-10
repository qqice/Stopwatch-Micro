"""Export actual bounded HTTP upgrade receive and msg2 header-copy fragments."""
import os, shutil, subprocess, tempfile, unittest
from pathlib import Path
from test_derp_tx_backpressure import function
from test_coord_noise_framing import BASE

def native_harness():
    src=(BASE/'src/ml_coord.c').read_text(encoding='utf8')
    stats=(BASE/'components/wireguard_lwip/src/wireguardif_rx_stats.h').read_text(encoding='utf8')
    enum=src[src.index('typedef enum { COORD_RX_FATAL'):].split(';',1)[0]+';\n'
    functions=function(src,'coord_recv')+function(src,'coord_recv_upgrade')
    body=src[src.index('    int body_len = total - (body_start - resp);'):].split('    /* Parse 3-byte Noise frame header */',1)[0]
    functions+='static int msg2_header(microlink_t *ml, uint8_t *resp, int total) {\nuint8_t *body_start=(uint8_t*)strstr((char*)resp,"\\r\\n\\r\\n")+4;\n'+body+'assert(body_buf_len>=3 && body_buf[0]==2 && body_buf[1]==0 && body_buf[2]==48); free(body_buf); return 0; }\n'
    prefix=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <sys/types.h>
#include <sys/time.h>
#define SOL_SOCKET 1
#define SO_RCVTIMEO 20
static const char *TAG="shim";
static void log_shim(const char *tag,const char *fmt,...) { (void)tag;(void)fmt; }
#define ESP_LOGI(...) log_shim(__VA_ARGS__)
#define ESP_LOGE(...) log_shim(__VA_ARGS__)
#define pdMS_TO_TICKS(x) (x)
typedef struct { int coord_sock; } microlink_t;
static uint8_t wire[4096]; static unsigned pos,head,tail,allocs,fail_alloc,outstanding;
static uint64_t elapsed; static int events[4096]; static unsigned durations[4096];
static uint64_t timeouts[4096]; static unsigned timeout_count;
static uint64_t ml_get_time_ms(void) { return elapsed; }
static void vTaskDelay(unsigned ms) { elapsed+=ms; }
static int ml_recv(int fd,uint8_t *b,size_t len,int flags) {
 (void)fd;(void)flags; assert(head<tail); int n=events[head]; elapsed+=durations[head++];
 if(n<0) { errno=EAGAIN;return -1; } assert((size_t)n<=len); memcpy(b,wire+pos,n);pos+=n;return n;
}
static int ml_setsockopt(int fd,int l,int o,const void *v,size_t n) {
 (void)fd;(void)l;(void)o;(void)n; const struct timeval *tv=v;timeouts[timeout_count++]=tv->tv_sec*1000+tv->tv_usec/1000;return 0;
}
static void *ml_psram_malloc(size_t n) {
 if(++allocs==fail_alloc) { return NULL; }
 uint8_t *p=malloc(n+sizeof(size_t)+16);assert(p);memcpy(p,&n,sizeof(n)); memset(p+sizeof(n)+n,0xA5,16);++outstanding;return p+sizeof(n);
}
static void free_shim(void *p) {
 if(!p) { return; }
 size_t n;uint8_t *b=(uint8_t*)p-sizeof(n);memcpy(&n,b,sizeof(n));for(unsigned i=0;i<16;i++)assert(b[sizeof(n)+n+i]==0xA5);assert(outstanding);--outstanding;free(b);
}
#define free free_shim
'''
    suffix=r'''
static microlink_t client;
static void reset(void) { assert(!outstanding);pos=head=tail=allocs=fail_alloc=timeout_count=0;elapsed=0;errno=EAGAIN;memset(wireguardif_rx_counters,0,sizeof(wireguardif_rx_counters)); }
static void add(int n,unsigned ms) {events[tail]=n;durations[tail++]=ms;}
static int upgrade(void) {uint8_t b[2048];int n=coord_recv_upgrade(&client,b,sizeof(b));assert(timeouts[timeout_count-1]==10000);if(n>0)assert(!memcmp(b,wire,n));return n;}
int main(void) {
 wireguardif_rx_store(WG_RX_CTRL_LAST_REASON,0);assert(!wireguardif_rx_is_synack(NULL,0));
 const char *header="HTTP/1.1 101 Switching Protocols\r\nUpgrade: tailscale-control-protocol\r\n\r\n";unsigned len=strlen(header);
 reset();memcpy(wire,header,len);add(1,1);for(unsigned i=1;i<len;i++)add(1,1);assert(upgrade()==(int)len && head==len);assert(wireguardif_rx_counters[WG_RX_CTRL_UPGRADE_FRAGMENTED]==1);assert(timeouts[1]==9999);
 for(unsigned tailbytes=1;tailbytes<=2;tailbytes++) {
  reset();memcpy(wire,header,len);wire[len]=2;wire[len+1]=0;wire[len+2]=48;add(len+tailbytes,1);add(3-tailbytes,1);
  uint8_t *r=ml_psram_malloc(2048);int n=coord_recv_upgrade(&client,r,2048);assert(n==(int)(len+tailbytes));assert(msg2_header(&client,r,n)==0 && !outstanding && pos==len+3);
 }
 reset();memset(wire,'x',2047);add(2047,0);assert(upgrade()==-1 && head==1);
 reset();add(0,0);assert(upgrade()==-1 && wireguardif_rx_counters[WG_RX_CTRL_RX_EOF]==1);
 reset();add(-1,0);assert(upgrade()==-1 && head==1);
 reset();memcpy(wire,header,len);add(1,1);add(0,0);assert(upgrade()==-1 && head==2);
 reset();memcpy(wire,header,len);add(1,9900);add(1,100);assert(upgrade()==-1 && head==2 && timeouts[1]==100);
 reset();const char *bad="HTTP/1.1 200 X-101\r\n\r\n";memcpy(wire,bad,strlen(bad));add(strlen(bad),0);assert(upgrade()==-1);
 reset();memcpy(wire,header,len);wire[len]=2;wire[len+1]=0;wire[len+2]=48;add(len+1,0);uint8_t *r=ml_psram_malloc(2048);int n=coord_recv_upgrade(&client,r,2048);fail_alloc=2;assert(msg2_header(&client,r,n)==-1 && !outstanding && head==1);
 puts("coord HTTP upgrade production fault shim: PASS");return 0;
}
'''
    return prefix+stats+'\nuint32_t wireguardif_rx_counters[WG_RX_COUNT];\n'+enum+functions+suffix

class Tests(unittest.TestCase):
    def test_native(self):
        cc=os.environ.get('COORD_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
        if not cc:self.skipTest('native compiler unavailable; export for Mac')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'up.c';exe=Path(d)/'up';p.write_text(native_harness(),encoding='utf8')
            subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror',str(p),'-o',str(exe)],check=True);subprocess.run([str(exe)],check=True)
    def test_syntax(self):
        cc=next(iter(Path.home().glob('.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-gcc.exe')),None)
        if not cc:self.skipTest('syntax compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'up.c';p.write_text(native_harness(),encoding='utf8');subprocess.run([str(cc),'-std=c11','-Wall','-Wextra','-Werror','-fsyntax-only',str(p)],check=True)
if __name__=='__main__':
    import sys
    if len(sys.argv)==3 and sys.argv[1]=='--export-native-harness':Path(sys.argv[2]).write_text(native_harness(),encoding='utf8')
    else:unittest.main()
