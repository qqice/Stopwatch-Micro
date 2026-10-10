"""Exact production external-socket RX fault shim; no SDK/network/device IO."""
import os, re, shutil, subprocess, tempfile, unittest
from pathlib import Path
from test_derp_tx_backpressure import function, BASE


def native_harness():
    derp=(BASE/'src/ml_derp.c').read_text(encoding='utf8')
    coord=(BASE/'src/ml_coord.c').read_text(encoding='utf8')
    stats=(BASE/'components/wireguard_lwip/src/wireguardif_rx_stats.h').read_text(encoding='utf8')
    enum=re.search(r'typedef enum \{ COORD_RX_FATAL.*?coord_rx_result_t;',coord).group()
    functions=function(derp,'ml_derp_bio_recv_timeout')+function(coord,'coord_recv')+function(coord,'coord_recv_upgrade')
    proactive=coord[coord.index('            int n = ml_recv(ml->coord_sock, extra_data, 1024, 0);'):].split('\n        }',1)[0]
    functions+='static int proactive_recv(microlink_t *ml) { uint8_t *extra_data=malloc(1024); int extra_len=0;\n'+proactive+'\n free(extra_data); return extra_len; }\n'
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
#define DERP_CONNECT_TIMEOUT_MS 10000
#define MBEDTLS_ERR_NET_INVALID_CONTEXT -100
#define MBEDTLS_ERR_SSL_TIMEOUT -101
#define MBEDTLS_ERR_NET_CONN_RESET -102
#define MBEDTLS_ERR_SSL_WANT_READ -103
#define MBEDTLS_ERR_NET_RECV_FAILED -104
#define SOL_SOCKET 1
#define SO_RCVTIMEO 20
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define pdMS_TO_TICKS(x) (x)
typedef struct { int coord_sock; } microlink_t;
static int next_ret,next_errno,io_calls; static uint64_t clock_ms;
static const char *wire_input;
static unsigned opt_calls; static struct timeval last_tv;
/* Deliberately clobber errno in the timestamp observer dependency. */
static uint64_t ml_get_time_ms(void) { errno=EDOM; return clock_ms; }
static void vTaskDelay(unsigned ms) { clock_ms+=ms; }
static int ml_setsockopt(int fd,int level,int option,const void *v,size_t len) {
 (void)fd;(void)level;(void)option;(void)len;last_tv=*(const struct timeval*)v;++opt_calls;return 0;
}
static int ml_recv(int fd,uint8_t *buf,size_t len,int flags) {
 (void)fd;(void)flags;++io_calls;errno=next_errno;
 if(next_ret>0) { assert((size_t)next_ret<=len);if(wire_input)memcpy(buf,wire_input,next_ret);else memset(buf,'x',next_ret); }
 return next_ret;
}
static int ml_read_sock(int fd,uint8_t *buf,size_t len) { return ml_recv(fd,buf,len,0); }
'''
    suffix=r'''
static void setup(int ret,int err) { next_ret=ret;next_errno=err;wire_input=NULL;io_calls=0;opt_calls=0;clock_ms=UINT64_C(0x100000029);memset(wireguardif_rx_counters,0,sizeof(wireguardif_rx_counters)); }
static void check(unsigned base,int ret,int err) {
 assert(wireguardif_rx_counters[base]==1);
 assert(wireguardif_rx_counters[base+1]==(unsigned)(ret>0));
 assert(wireguardif_rx_counters[base+2]==(unsigned)(ret>0?ret:0));
 assert(wireguardif_rx_counters[base+3]==(unsigned)(ret<0 && (err==EAGAIN || err==EWOULDBLOCK)));
 assert(wireguardif_rx_counters[base+4]==(unsigned)(ret==0));
 assert(wireguardif_rx_counters[base+5]==(unsigned)(ret<0 && (err==ECONNRESET || err==EPIPE)));
 assert(wireguardif_rx_counters[base+6]==(unsigned)(ret<0 && err!=EAGAIN && err!=EWOULDBLOCK && err!=ECONNRESET && err!=EPIPE));
 assert(wireguardif_rx_counters[base+7]==(uint32_t)ret);
 assert(wireguardif_rx_counters[base+8]==41);
 unsigned other=base==WG_RX_DERP_SOCK_RX_CALLS?WG_RX_CTRL_SOCK_RX_CALLS:WG_RX_DERP_SOCK_RX_CALLS;
 for(unsigned i=0;i<9;i++)assert(wireguardif_rx_counters[other+i]==0);
}
int main(void) {
 assert(!wireguardif_rx_is_synack(NULL,0));
 const int errors[]={EAGAIN,EWOULDBLOCK,ECONNRESET,EPIPE,EINTR,EIO,0};
 int fd=3;uint8_t buf[1024];microlink_t ml={3};
 for(unsigned e=0;e<sizeof(errors)/sizeof(errors[0]);e++)for(int ret=-1;ret<=7;ret+=ret<0?1:7) {
  int err=errors[e];setup(ret,err);
  int expect=ret;
  if(ret<0)expect=(err==EAGAIN||err==EWOULDBLOCK)?MBEDTLS_ERR_SSL_TIMEOUT:(err==ECONNRESET||err==EPIPE)?MBEDTLS_ERR_NET_CONN_RESET:err==EINTR?MBEDTLS_ERR_SSL_WANT_READ:MBEDTLS_ERR_NET_RECV_FAILED;
  assert(ml_derp_bio_recv_timeout(&fd,buf,sizeof(buf),1234)==expect);
  assert(errno==err && io_calls==1 && opt_calls==1 && last_tv.tv_sec==1 && last_tv.tv_usec==234000);
  check(WG_RX_DERP_SOCK_RX_CALLS,ret,err);
  setup(ret,err);expect=ret>0?COORD_RX_COMPLETE:ret<0&&(err==EAGAIN||err==EWOULDBLOCK)?COORD_RX_IDLE:COORD_RX_FATAL;
  assert(coord_recv(&ml,buf,7)==expect);assert(errno==err && io_calls==1);check(WG_RX_CTRL_SOCK_RX_CALLS,ret,err);
  setup(ret,err);assert(proactive_recv(&ml)==(ret>0?ret:0));assert(errno==err && io_calls==1);check(WG_RX_CTRL_SOCK_RX_CALLS,ret,err);
  if(ret<=0) { setup(ret,err);assert(coord_recv_upgrade(&ml,buf,sizeof(buf))==-1);assert(errno==err && io_calls==1 && opt_calls==2 && last_tv.tv_sec==10);check(WG_RX_CTRL_SOCK_RX_CALLS,ret,err); }
 }
 const char *header="HTTP/1.1 101 Switching Protocols\r\nUpgrade: tailscale-control-protocol\r\n\r\n";
 setup((int)strlen(header),EPIPE);wire_input=header;
 assert(coord_recv_upgrade(&ml,buf,sizeof(buf))==(int)strlen(header));
 assert(io_calls==1 && opt_calls==2 && last_tv.tv_sec==10);
 check(WG_RX_CTRL_SOCK_RX_CALLS,(int)strlen(header),EPIPE);
 setup(0,0);fd=-1;assert(ml_derp_bio_recv_timeout(&fd,buf,sizeof(buf),0)==MBEDTLS_ERR_NET_INVALID_CONTEXT);assert(!io_calls && !opt_calls && !wireguardif_rx_counters[WG_RX_DERP_SOCK_RX_CALLS]);
 fd=3;setup(1,ECONNRESET);assert(ml_derp_bio_recv_timeout(&fd,buf,sizeof(buf),0)==1);assert(last_tv.tv_sec==10 && !last_tv.tv_usec);
 /* Independent roles and wrap: positive stale errno never creates an error. */
 wireguardif_rx_socket_note(WG_RX_SOCKET_CTRL_RAW,3,EPIPE,50);
 assert(wireguardif_rx_counters[WG_RX_CTRL_SOCK_RX_BYTES]==3 && wireguardif_rx_counters[WG_RX_DERP_SOCK_RX_BYTES]==1);
 wireguardif_rx_store(WG_RX_CTRL_SOCK_RX_BYTES,UINT32_MAX);
 wireguardif_rx_socket_note(WG_RX_SOCKET_CTRL_RAW,2,EPIPE,51);
 assert(wireguardif_rx_counters[WG_RX_CTRL_SOCK_RX_BYTES]==1);
 wireguardif_rx_socket_note(WG_RX_SOCKET_DERP_TLS,-123,EIO,52);
 assert(wireguardif_rx_counters[WG_RX_DERP_SOCK_RX_LAST_RET]==(uint32_t)-123);
 puts("external socket RX production fault shim: PASS");return 0;
}
'''
    return prefix+stats+'\nuint32_t wireguardif_rx_counters[WG_RX_COUNT];\n'+enum+functions+suffix


class Tests(unittest.TestCase):
    def test_callsites_capture_before_observer_restore_before_policy(self):
        for file,call,role,count in [('ml_derp.c','ml_read_sock','DERP_TLS',1),('ml_coord.c','ml_recv','CTRL_RAW',3)]:
            src=(BASE/'src'/file).read_text(encoding='utf8')
            pattern=(r'(?m)^[ \t]*int (ret|n) = (?:\(int\))?'+call+r'\([^\n]+;\n'
                     r'[ \t]*int saved_errno = errno;\n'
                     r'[ \t]*wireguardif_rx_socket_note\(WG_RX_SOCKET_'+role+r', \1, saved_errno, \(uint32_t\)ml_get_time_ms\(\)\);\n'
                     r'[ \t]*errno = saved_errno;')
            self.assertEqual(len(re.findall(pattern,src)),count)
            self.assertEqual(len(re.findall(r'\b'+call+r'\(',src)),count)
    def test_native(self):
        cc=os.environ.get('SOCKET_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
        if not cc:self.skipTest('host compiler unavailable; export for Mac execution')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'sock.c';exe=Path(d)/'sock';p.write_text(native_harness(),encoding='utf8')
            subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror',str(p),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)
    def test_syntax(self):
        cc=next(iter(Path.home().glob('.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-gcc.exe')),None)
        if not cc:self.skipTest('installed syntax compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'sock.c';p.write_text(native_harness(),encoding='utf8')
            subprocess.run([str(cc),'-std=c11','-Wall','-Wextra','-Werror','-fsyntax-only',str(p)],check=True)

if __name__=='__main__':unittest.main()
