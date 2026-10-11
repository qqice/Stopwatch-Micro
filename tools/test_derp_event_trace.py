"""Exact DERP lifecycle functions in a numeric fault shim; no SDK/device IO."""
import os, re, shutil, subprocess, tempfile, unittest
from pathlib import Path
from test_derp_tx_backpressure import BASE, function


def native_harness():
    src=(BASE/'src/ml_derp.c').read_text(encoding='utf8')
    header=(BASE/'components/wireguard_lwip/src/wireguardif_rx_stats.h').read_text(encoding='utf8')
    observer=function(header,'wireguardif_derp_note').replace('WG_RX_INLINE','static inline')
    state=src[src.index('static uint32_t derp_trace_stage'):src.index('/* Timeout for DERP')]
    # No network APIs: all boundary calls below are controlled numeric mocks.
    prefix=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
#include <errno.h>
#include <sys/time.h>
typedef int esp_err_t; typedef unsigned EventBits_t;
typedef struct { char hostname[80],cert_name[80],ipv4[80],ipv6[80]; int derp_port; bool stun_only; } ml_derp_node_t;
typedef struct { unsigned region_id; int node_count; ml_derp_node_t nodes[1]; } ml_derp_region_t;
typedef struct { int derp_map_lock,events,derp_region_count,derp_node_cursor;
 unsigned priority_peer_derp_region,derp_home_region;
 ml_derp_region_t derp_regions[1]; uint8_t wg_private_key[32],wg_public_key[32];
 struct { int sockfd,ssl,ssl_conf; uint8_t cert_pin[32]; bool connected;
 unsigned connected_region; uint64_t last_recv_ms; } derp;
} microlink_t;
struct addrinfo { int ai_family,ai_socktype,ai_flags; void *ai_addr; size_t ai_addrlen; struct addrinfo *ai_next; };
#define TAG "mock"
#define ML_DERP_HOST "mock"
#define ML_DERP_PORT 443
#define ML_DERP_REGION 41
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_STATE -20
#define ESP_ERR_NO_MEM -21
#define ESP_ERR_TIMEOUT -22
#define MBEDTLS_ERR_SSL_TIMEOUT -101
#define MBEDTLS_ERR_SSL_WANT_READ -102
#define MBEDTLS_ERR_SSL_WANT_WRITE -103
#define MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY -104
#define DERP_CONNECT_TIMEOUT_MS 10000
#define DERP_FRAME_SERVER_KEY 1
#define DERP_FRAME_CLIENT_INFO 2
#define DERP_FRAME_SERVER_INFO 3
#define DERP_FRAME_NOTE_PREFERRED 7
#define NACL_BOX_NONCEBYTES 24
#define NACL_BOX_MACBYTES 16
#define AF_INET 1
#define AF_INET6 2
#define AF_UNSPEC 3
#define SOCK_STREAM 1
#define AI_NUMERICHOST 1
#define SOL_SOCKET 1
#define SO_SNDTIMEO 2
#define SO_RCVTIMEO 3
#define F_GETFL 0
#define F_SETFL 1
#define O_NONBLOCK 1
#define MBEDTLS_SSL_IS_CLIENT 1
#define MBEDTLS_SSL_TRANSPORT_STREAM 1
#define MBEDTLS_SSL_PRESET_DEFAULT 1
#define MBEDTLS_SSL_VERIFY_REQUIRED 1
#define ML_EVT_DERP_CONNECTED 1
#define WG_RX_DERP_UPGRADE_SHORT_ERROR 1
#define WG_RX_DERP_IGNORED_TX_FAIL 2
#define WG_DERP_TX_HTTP_UPGRADE 3
#define portMAX_DELAY 0
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
#define ESP_LOGI(...) shim_log(__VA_ARGS__)
static void shim_log(const char *tag,const char *format,...) { (void)tag;(void)format; }
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
extern void mosaico_derp_trace(uint32_t,uint32_t,int32_t,int32_t,uint32_t,int32_t) __attribute__((weak));
static uint32_t kinds[128],stages[128],regions[128]; static int results[128],hints[128],fds[128]; static unsigned used;
static uint64_t now_ms;
static int fault,read_index,alloc_index,write_index,closes,coord_checks; static microlink_t client;
static uint64_t ml_get_time_ms(void) { return now_ms; }
static int64_t esp_timer_get_time(void) { return (int64_t)now_ms*1000; }
static void vTaskDelay(unsigned ms) { now_ms+=ms; }
static void wireguardif_rx_count(unsigned n) { (void)n; }
static void derp_tx_observe(unsigned n,int ret) { (void)n;(void)ret; }
static int xSemaphoreTake(int lock,unsigned wait) { (void)lock;(void)wait;return fault==1?0:1; }
static void xSemaphoreGive(int lock) { (void)lock; }
static bool derp_coord_ready_locked(microlink_t *ml) { (void)ml;return fault!=2; }
static bool derp_coord_ready(microlink_t *ml) { (void)ml;++coord_checks;return fault!=25; }
static void xEventGroupSetBits(int ev,unsigned bits) { (void)ev;(void)bits; }
static int ml_close_sock(int fd) { assert(fd==8);++closes;return 0; }
static int ml_getaddrinfo(const char *host,const char *port,struct addrinfo *h,struct addrinfo **out) {
 (void)host;(void)port;(void)h;static struct addrinfo ai={1,1,0,NULL,0,NULL};
 errno=EIO;if(fault==5)return -505;*out=fault==6?NULL:&ai;return 0;
}
static void ml_freeaddrinfo(struct addrinfo *ai) { (void)ai; }
static int ml_socket(int family,int kind,int proto) { (void)family;(void)kind;(void)proto;errno=EMFILE;return fault==7?-1:8; }
static int ml_connect(int fd,void *addr,size_t len) { (void)fd;(void)addr;(void)len;errno=ECONNREFUSED;return fault==8?-808:0; }
static int ml_setsockopt(int fd,int level,int opt,const void *v,size_t len) { (void)fd;(void)level;(void)opt;(void)v;(void)len;return 0; }
static int ml_fcntl(int fd,int cmd,int flags) { (void)fd;(void)cmd;(void)flags;return 0; }
static void mbedtls_ssl_init(int *p) { *p=0; }
static void mbedtls_ssl_config_init(int *p) { *p=0; }
static int mbedtls_ssl_config_defaults(int *p,int a,int b,int c) { (void)p;(void)a;(void)b;(void)c;return fault==9?-909:0; }
static int esp_crt_bundle_attach(int *p) { (void)p;return fault==10?-1010:0; }
static void verify_derp_pin(void) {}
static void mbedtls_ssl_conf_verify(int *p,void (*cb)(void),uint8_t *pin) { (void)p;(void)cb;(void)pin; }
static void mbedtls_ssl_conf_authmode(int *p,int n) { (void)p;(void)n; }
static void mbedtls_ssl_conf_read_timeout(int *p,int n) { (void)p;(void)n; }
static int mbedtls_ssl_setup(int *a,int *b) { (void)a;(void)b;return fault==11?-1111:0; }
static int mbedtls_ssl_set_hostname(int *p,const char *h) { (void)p;(void)h;return fault==12?-1212:0; }
static int ml_derp_bio_send(void *c,const unsigned char *p,size_t n) { (void)c;(void)p;(void)n;return 0; }
static int ml_derp_bio_recv_timeout(void *c,unsigned char *p,size_t n,uint32_t t) { (void)c;(void)p;(void)n;(void)t;return 0; }
static void mbedtls_ssl_set_bio(int *p,int *fd,int (*send)(void*,const unsigned char*,size_t),void *recv,int (*timed)(void*,unsigned char*,size_t,uint32_t)) { (void)p;(void)fd;(void)send;(void)recv;(void)timed; }
static int mbedtls_ssl_handshake(int *p) { (void)p;return fault==13?-1313:0; }
static void mbedtls_strerror(int ret,char *out,size_t n) { (void)ret;memset(out,0,n); }
static int mbedtls_ssl_write(int *p,const uint8_t *data,size_t n) { (void)p;(void)data;return fault==14?-1414:fault==31?1:fault==32?0:(int)n; }
static int mbedtls_ssl_read(int *p,uint8_t *out,size_t n) {
 (void)p;static const uint8_t reply[]={ 'H','T','T','P','/','1','.','1',' ','1','0','1','\r','\n','\r','\n', 1,0,0,0,40, 0x44,0x45,0x52,0x50,0xf0,0x9f,0x94,0x91, 0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0, 3,0,0,0,0 };
 errno=EIO;
 if(fault==15) {now_ms+=10001;return MBEDTLS_ERR_SSL_TIMEOUT;}
 if(fault==16)return -1616;
 if(fault==17)return 0;
 if(fault==19 && read_index>=16)return -1919;
 if(fault==21 && read_index>=21)return -2121;
 if(fault==22 && read_index>=29)return -2222;
 if(fault==26 && read_index>=61)return -2626;
 assert((size_t)read_index+n<=sizeof(reply));memcpy(out,reply+read_index,n);
 if(fault==18 && read_index==9)out[0]='4';
 if(fault==20 && read_index==16)out[0]=99;
 if(fault==23 && read_index==21)out[0]=0;
 read_index+=(int)n;return (int)n;
}
static void esp_fill_random(void *p,size_t n) { memset(p,1,n); }
static void *shim_malloc(size_t n) { ++alloc_index;if((fault==27&&alloc_index==1)||(fault==28&&alloc_index==2))return NULL;return malloc(n); }
#define malloc shim_malloc
static int nacl_box(uint8_t *out,const uint8_t *in,size_t n,const uint8_t *nonce,const uint8_t *key,const uint8_t *secret) { (void)out;(void)in;(void)n;(void)nonce;(void)key;(void)secret;return fault==24?-2424:0; }
static int derp_write_frame(microlink_t *ml,uint8_t type,const uint8_t *p,uint32_t n);
'''
    mocks=r'''
void mosaico_derp_trace(uint32_t kind,uint32_t stage,int32_t ret,int32_t hint,uint32_t region,int32_t fd) {
 assert(used<128);regions[used]=region;kinds[used]=kind;stages[used]=stage;results[used]=ret;hints[used]=hint;fds[used++]=fd;errno=EDOM;
}
static int derp_write_frame(microlink_t *ml,uint8_t type,const uint8_t *p,uint32_t n) {
 (void)type;(void)p;(void)n;++write_index;
 if(fault==29 && write_index==1) { derp_trace_failure(ml,-2929);return -1; }return 0;
}
'''
    functions=''.join(function(src,n) for n in ['derp_tls_read_all','derp_recv_frame_header','derp_connect_once'])
    cases=r'''
static void setup(int f) {
 memset(&client,0,sizeof(client));client.events=client.derp_map_lock=1;client.derp.sockfd=-1;
 fault=f;used=read_index=alloc_index=write_index=closes=coord_checks=0;now_ms=0;errno=EIO;
}
static void failure(int f,unsigned stage,int raw) {
 setup(f);assert(derp_connect_once(&client)!=ESP_OK);assert(used>=2);
 assert(kinds[0]==1);assert(kinds[used-1]==2);assert(stages[used-1]==stage);assert(results[used-1]==raw);
 assert(!client.derp.connected);
}
int main(void) {
 failure(1,1,ESP_ERR_INVALID_STATE);failure(2,1,ESP_ERR_INVALID_STATE);
 setup(3);client.priority_peer_derp_region=90;assert(derp_connect_once(&client)==ESP_FAIL);assert(stages[used-1]==1&&regions[used-1]==90);
 /* Present but unusable priority nodes remain map failures, not TCP. */
 for(int unusable=0;unusable<2;++unusable) {
  setup(30);client.priority_peer_derp_region=90;client.derp_region_count=1;
  client.derp_regions[0].region_id=90;client.derp_regions[0].node_count=1;
  client.derp_regions[0].nodes[0].stun_only=unusable!=0;
  if(unusable)strcpy(client.derp_regions[0].nodes[0].hostname,"mock");
  assert(derp_connect_once(&client)==ESP_FAIL);
  assert(stages[used-1]==1&&regions[used-1]==90&&closes==0);
 }
 setup(4);client.derp_region_count=1;client.derp_home_region=41;client.derp_regions[0].region_id=41;client.derp_regions[0].node_count=1;
 strcpy(client.derp_regions[0].nodes[0].hostname,"mock");strcpy(client.derp_regions[0].nodes[0].cert_name,"sha256-raw:bad");
 assert(derp_connect_once(&client)==ESP_FAIL);assert(stages[used-1]==4);
 failure(5,2,-505);failure(6,2,ESP_FAIL);failure(7,3,-1);failure(8,3,-808);
 failure(9,4,-909);failure(10,4,-1010);failure(11,4,-1111);failure(12,4,-1212);
 failure(13,5,-1313);failure(14,6,-1414);failure(15,7,MBEDTLS_ERR_SSL_TIMEOUT);
 failure(16,7,-1616);failure(17,7,0);failure(18,8,ESP_FAIL);
 failure(19,9,-1919);failure(20,9,ESP_FAIL);failure(21,9,-2121);failure(22,9,-2222);failure(23,9,ESP_FAIL);
 failure(24,10,-2424);failure(27,10,ESP_ERR_NO_MEM);failure(28,10,ESP_ERR_NO_MEM);failure(29,10,-2929);
 failure(25,12,ESP_ERR_INVALID_STATE);
 setup(0);assert(derp_connect_once(&client)==ESP_OK);assert(client.derp.connected&&client.derp.sockfd==8);
 assert(kinds[used-1]==3&&stages[used-1]==12);assert(read_index==66&&write_index==2&&closes==0);
 /* Short/zero upgrade writes are observed without changing continuation. */
 for(int short_case=31;short_case<=32;++short_case) {
  setup(short_case);assert(derp_connect_once(&client)==ESP_OK);
  assert(kinds[1]==2&&stages[1]==6&&results[1]==(short_case==31?1:0));
  assert(kinds[used-1]==3&&client.derp.connected&&closes==0);
 }
 /* Optional ServerInfo failure is observed but retains the original success. */
 setup(26);assert(derp_connect_once(&client)==ESP_OK);assert(kinds[1]==2&&stages[1]==11&&results[1]==-2626);assert(kinds[used-1]==3);
 errno=ECONNRESET;wireguardif_derp_note(2,19,-1,errno,0,8);assert(errno==ECONNRESET);
 assert(hints[used-1]==ECONNRESET&&fds[used-1]==8);
 derp_trace_stage=15;errno=EPIPE;derp_trace_failure(&client,-999);derp_trace_disconnect(&client);
 assert(kinds[used-1]==4&&stages[used-1]==15&&results[used-1]==-999&&errno==EPIPE);
 puts("DERP lifecycle exact connect fault shim: PASS");return 0;
}
'''
    return prefix+observer+'\n'+state+mocks+functions+cases


class Tests(unittest.TestCase):
    def test_native(self):
        cc=os.environ.get('DERP_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
        if not cc:self.skipTest('host compiler unavailable; export harness for native runtime')
        self.run_compiler(cc,False)
    def test_syntax(self):
        cc=next(iter(Path.home().glob('.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-gcc.exe')),None)
        if not cc:self.skipTest('installed syntax compiler unavailable')
        self.run_compiler(str(cc),True)
    def run_compiler(self,cc,syntax):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'trace.c';exe=Path(d)/'trace.exe';p.write_text(native_harness(),encoding='utf8')
            args=[cc,'-std=c11','-Wall','-Wextra','-Werror',str(p)]
            args+=['-fsyntax-only'] if syntax else ['-o',str(exe)]
            subprocess.run(args,check=True)
            if not syntax:subprocess.run([str(exe)],check=True)

if __name__=='__main__':
    import sys
    if len(sys.argv)==3 and sys.argv[1]=='--export-native-harness':Path(sys.argv[2]).write_text(native_harness(),encoding='utf8')
    else:unittest.main()
