"""Actual TCP pin/bind/connect helpers with native fail-closed fault injection."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_derp_tx_backpressure import BASE, function
SOURCE = BASE / 'src/ml_tcp.c'


def native_harness():
    s = SOURCE.read_text(encoding='utf8')
    ctx = s[s.index('typedef struct {'):s.index('static void ml_tcp_vpn_snapshot')]
    # Only the VPN context typedef (the socket handle above is defined separately).
    helpers = ''.join(function(s, n) for n in ('ml_tcp_vpn_snapshot', 'ml_tcp_close_error', 'ml_tcp_bind_vpn'))
    diag = (BASE / 'include/microlink.h').read_text(encoding='utf8')
    diag = diag[diag.index('typedef enum {', diag.index('Numeric-only last completed connect')):diag.index('/**', diag.index('void microlink_tcp_get_last_connect'))]
    helpers += diag + s[s.index('/* Last completed request only;'):s.index('esp_err_t microlink_tcp_send')]
    trace_header = (BASE / 'components/wireguard_lwip/src/wireguardif_rx_stats.h').read_text(encoding='utf8')
    trace = trace_header[trace_header.index('extern void mosaico_wg_tx_trace'):trace_header.index('WG_RX_INLINE uint32_t wireguardif_tx_le32')]
    trace = trace.replace('WG_RX_INLINE', 'static inline')
    trace = trace.replace('__attribute__((weak));', 'HOST_WEAK;')
    return r'''
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <assert.h>
#include <stdio.h>
#include <stdarg.h>
#include <limits.h>
#define timeval fixture_timeval
#undef htonl
#undef htons
#if defined(__APPLE__)
#define HOST_WEAK __attribute__((weak_import))
#else
#define HOST_WEAK __attribute__((weak))
#endif
#define AF_INET 2
#define SOCK_STREAM 1
#define IPPROTO_TCP 6
#define SOL_SOCKET 1
#define SO_BINDTODEVICE 25
#define SO_SNDTIMEO 21
#define SO_RCVTIMEO 20
#define SO_KEEPALIVE 9
#define TCP_KEEPIDLE 4
#define TCP_KEEPINTVL 5
#define TCP_KEEPCNT 6
#define TCP_NODELAY 1
#define ML_STATE_CONNECTED 1
#define ERR_OK 0
#define NETIF_NO_INDEX 0
static int log_clobber;
#define ESP_LOGE(...) do { if (log_clobber) errno=EINVAL; } while(0)
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define pdMS_TO_TICKS(x) (x)
struct sockaddr { int dummy; };
struct sockaddr_in { int sin_family; unsigned short sin_port; struct { uint32_t s_addr; } sin_addr; };
struct timeval { unsigned tv_sec,tv_usec; };
struct ifreq { char ifr_name[6]; };
struct ip_addr { uint32_t addr; int v4; };
struct netif { void *state; struct netif *next; struct ip_addr ip_addr; unsigned char index; int up,link; char name[6]; };
#define ML_MAX_PEERS 4
#define WIREGUARD_MAX_PEERS 4
#define portTICK_PERIOD_MS 10
typedef unsigned TickType_t;
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
typedef struct { bool active; uint32_t vpn_ip; int wg_peer_index; uint8_t public_key[32]; } ml_peer_t;
typedef struct { int state; uint32_t vpn_ip; void *wg_netif; int peer_count; ml_peer_t peers[4]; } microlink_t;
struct wireguard_keypair { bool valid; };
struct wireguard_peer { bool valid; struct ip_addr ip; uint16_t port; uint8_t public_key[32]; struct wireguard_keypair curr_keypair,prev_keypair,next_keypair; };
struct wireguard_device { bool valid; struct wireguard_peer peers[4]; };
static struct wireguard_device dev;
#define F_GETFL 1
#define F_SETFL 2
#define O_NONBLOCK 4
#define SO_ERROR 55
typedef unsigned socklen_t;
typedef struct { unsigned bits; } fd_set;
#define FD_ZERO(p) ((p)->bits=0)
#define FD_SET(fd,p) ((p)->bits|=1u<<(fd))
static int64_t now_us;
static int tunnel_ready, async_connect, select_error, select_errno, select_interrupt;
static int64_t ready_at_us, select_duration_us, handshake_us;
static int restore_error, flags_by_fd[4], getsock_error;
static int fcntl(int fd,int op,...){
 if(op==F_GETFL)return flags_by_fd[fd];
 va_list v;va_start(v,op);int flags=va_arg(v,int);va_end(v);
 if(!flags && restore_error){errno=EIO;return -1;} flags_by_fd[fd]=flags;return 0;
}
static int select(int nf,void*r,fd_set*w,fd_set*e,struct timeval*tv){
 (void)nf;(void)r;(void)w;(void)e;
 int64_t budget=(int64_t)tv->tv_sec*1000000+tv->tv_usec;assert(budget>0);
 if(select_interrupt){--select_interrupt;now_us+=1000;errno=EINTR;return -1;}
 if(select_duration_us>=budget){now_us+=budget;return 0;}
 now_us+=select_duration_us;
 if(select_error){errno=select_errno;return -1;}return 1;
}
static int getsockopt(int fd,int level,int opt,void*v,socklen_t*n){(void)fd;(void)level;(void)opt;(void)n;if(getsock_error){errno=EIO;return -1;}*(int*)v=select_errno;return 0;}

typedef struct microlink_tcp_socket { microlink_t *ml;int fd;uint32_t peer_ip;uint16_t peer_port;bool connected; } microlink_tcp_socket_t;
static struct netif wg,sta,*netif_list;
static int owner,dispatch_calls,dispatch_fail,pin_fail,bind_fail,create_fail,connect_fail;
static int sockets,closed,pins,binds,connects,physical,change_at_pin,down_at_connect;
static unsigned pinned[4];
static unsigned notes;
static int last_result;
static uint32_t last_flags;
static uint32_t htonl(uint32_t x){return ((x&255)<<24)|((x&0xff00)<<8)|((x>>8)&0xff00)|(x>>24);}
static unsigned short htons(unsigned short x){return (unsigned short)((x<<8)|(x>>8));}
#define IP_IS_V4(a) ((a)->v4)
#define netif_ip4_addr(n) (&(n)->ip_addr)
#define ip4_addr_get_u32(a) ((a)->addr)
#define NETIF_FOREACH(n) for((n)=netif_list;(n)!=NULL;(n)=(n)->next)
#define LWIP_ASSERT_CORE_LOCKED() assert(owner)
static int netif_is_up(struct netif *n){assert(owner);return n->up;}
static int netif_is_link_up(struct netif *n){assert(owner);return n->link;}
static unsigned char netif_get_index(struct netif *n){assert(owner);return n->index;}
static char *netif_index_to_name(unsigned char idx,char *name){assert(owner);struct netif *n;NETIF_FOREACH(n){if(n->index==idx){strcpy(name,n->name);return name;}}return NULL;}
static struct netif *netif_find(const char *name){assert(owner);struct netif *n;NETIF_FOREACH(n){if(!strcmp(name,n->name))return n;}return NULL;}
static bool wireguardif_owner_current(void){return owner;}
static int wireguardif_owner_call(void (*fn)(void*),void *c){++dispatch_calls;if(dispatch_fail==dispatch_calls)return -1;assert(!owner);owner=1;fn(c);owner=0;return 0;}
static int socket(int a,int b,int c){assert(!owner);assert(a==AF_INET&&b==SOCK_STREAM&&c==IPPROTO_TCP);++sockets;if(create_fail==sockets){errno=EMFILE;return -1;}return sockets;}
static int close(int fd){assert(fd>0);++closed;errno=EBADF;return 0;}
static int setsockopt(int fd,int level,int opt,const void *v,size_t len){
 assert(!owner);(void)level;
 if(opt==SO_BINDTODEVICE){++pins;assert(len==sizeof(struct ifreq));assert(!strcmp(((const struct ifreq*)v)->ifr_name,"wg0"));if(pin_fail==pins){errno=ENOPROTOOPT;return -1;}pinned[fd]=wg.index;if(change_at_pin==pins)wg.up=0;}
 return 0;
}
static int bind(int fd,const struct sockaddr *v,size_t len){assert(!owner&&pinned[fd]==wg.index);assert(len==sizeof(struct sockaddr_in));assert(((const struct sockaddr_in*)v)->sin_addr.s_addr==wg.ip_addr.addr);++binds;if(bind_fail==binds){errno=EADDRNOTAVAIL;return -1;}return 0;}
static int connect(int fd,const struct sockaddr *v,size_t len){(void)v;(void)len;assert(!owner);++connects;if(down_at_connect)wg.up=0;if(!pinned[fd])++physical;assert(pinned[fd]==wg.index);if(async_connect){errno=EINPROGRESS;return -1;}if((connect_fail==connects||connect_fail==-1)||!wg.up){errno=ENETUNREACH;return -1;}return 0;}
static void ml_wg_mgr_trigger_handshake(microlink_t *m,uint32_t ip){(void)m;(void)ip;now_us+=handshake_us;}
static void ml_wg_mgr_send_cmm(microlink_t *m,uint32_t ip){(void)m;(void)ip;}
static bool ml_wg_mgr_peer_is_up(microlink_t *m,uint32_t ip){(void)m;(void)ip;return tunnel_ready || (ready_at_us && now_us>=ready_at_us);}
static void vTaskDelay(unsigned t){now_us+=(int64_t)t*portTICK_PERIOD_MS*1000;}
static int64_t esp_timer_get_time(void){return now_us;}
''' + trace + r'''
void mosaico_wg_tx_trace(uint32_t stage,uint32_t receiver,uint32_t lo,uint32_t hi,uint32_t sp,uint32_t dp,uint32_t seq,int32_t result){assert(stage==8&&!receiver&&!sp&&dp==80&&seq==0x64400002);assert(lo<=2);++notes;last_result=result;last_flags=hi;errno=EDOM;}
''' + ctx + helpers + r'''
static microlink_t reset(void){
 now_us=0;tunnel_ready=1;async_connect=select_error=select_errno=select_interrupt=0;
 ready_at_us=select_duration_us=handshake_us=0;restore_error=getsock_error=0;memset(flags_by_fd,0,sizeof(flags_by_fd));
 dev=(struct wireguard_device){.valid=true};dev.peers[0].valid=true;dev.peers[0].curr_keypair.valid=true;
 owner=dispatch_calls=dispatch_fail=pin_fail=bind_fail=create_fail=connect_fail=0;log_clobber=0;
 sockets=closed=pins=binds=connects=physical=change_at_pin=down_at_connect=0;notes=0;last_result=0;last_flags=0;memset(pinned,0,sizeof(pinned));
 sta=(struct netif){.index=1,.up=1,.link=1};strcpy(sta.name,"st0");
 wg=(struct netif){.state=&dev,.next=&sta,.ip_addr={htonl(0x64400001),1},.index=2,.up=1,.link=1};strcpy(wg.name,"wg0");netif_list=&wg;
 return (microlink_t){.state=ML_STATE_CONNECTED,.vpn_ip=0x64400001,.wg_netif=&wg,.peer_count=1,.peers={{.active=true,.vpn_ip=0x64400002,.wg_peer_index=0}}};
}
static void failure_result(microlink_t *m,int e,int fds,int calls,int bind_result){assert(!microlink_tcp_connect(m,0x64400002,80,10000));assert(errno==e);assert(sockets==fds&&closed==fds&&connects==calls&&!physical&&!owner);assert(notes==(unsigned)fds&&last_result==bind_result);}
static void failure(microlink_t *m,int e,int fds,int calls){failure_result(m,e,fds,calls,-e);}
int main(void){
 microlink_t m=reset();microlink_tcp_socket_t *s=microlink_tcp_connect(&m,0x64400002,80,10000);assert(s&&pins==1&&binds==1&&!closed&&!physical&&notes==1&&!last_result&&last_flags==7);close(s->fd);free(s);
 m=reset();connect_fail=1;s=microlink_tcp_connect(&m,0x64400002,80,10000);assert(s&&pins==2&&binds==2&&closed==1&&connects==2&&!physical&&notes==2&&!last_result);close(s->fd);free(s);
 m=reset();m.wg_netif=NULL;failure(&m,ENODEV,1,0);
 m=reset();m.wg_netif=&sta;failure(&m,EADDRNOTAVAIL,1,0);
 m=reset();m.vpn_ip=0;failure(&m,ENODEV,1,0);
 m=reset();netif_list=&sta;failure(&m,ENODEV,1,0);
 m=reset();wg.ip_addr.v4=0;failure(&m,EADDRNOTAVAIL,1,0);
 m=reset();wg.up=0;failure(&m,ENETDOWN,1,0);
 m=reset();wg.link=0;failure(&m,ENETDOWN,1,0);
 m=reset();wg.index=0;failure(&m,ENODEV,1,0);
 m=reset();pin_fail=1;failure(&m,ENOPROTOOPT,1,0);
 m=reset();bind_fail=1;failure(&m,EADDRNOTAVAIL,1,0);
 m=reset();dispatch_fail=1;failure(&m,EIO,1,0);
 m=reset();dispatch_fail=2;failure(&m,EIO,1,0);
 m=reset();change_at_pin=1;failure(&m,ENETDOWN,1,0);
 m=reset();connect_fail=1;pin_fail=2;failure(&m,ENOPROTOOPT,2,1);
 m=reset();connect_fail=1;bind_fail=2;failure(&m,EADDRNOTAVAIL,2,1);
 m=reset();connect_fail=1;dispatch_fail=3;failure(&m,EIO,2,1);
 m=reset();connect_fail=1;change_at_pin=2;failure(&m,ENETDOWN,2,1);
 /* Both pin/bind attempts succeed; subsequent connect failures must not
  * be misreported as phase8 binding errors. Retain errno/release/no-fallback. */
 m=reset();log_clobber=1;connect_fail=-1;failure_result(&m,ENETUNREACH,2,2,0);assert(pins==2&&binds==2&&last_flags==7);
 m=reset();log_clobber=1;create_fail=1;assert(!microlink_tcp_connect(&m,0x64400002,80,10000)&&errno==EMFILE&&sockets==1&&!closed&&!pins&&!notes);
 m=reset();connect_fail=1;create_fail=2;assert(!microlink_tcp_connect(&m,0x64400002,80,10000)&&errno==EMFILE&&sockets==2&&closed==1&&pins==1&&notes==1);
 m=reset();down_at_connect=1;failure(&m,ENETDOWN,2,1); /* no physical fallback, retry snapshot refuses down WG */
 m=reset();owner=1;assert(!microlink_tcp_connect(&m,0x64400002,80,10000)&&errno==EDEADLK&&!sockets);owner=0;
 puts("VPN pin/bind actual production helpers and initial/retry fault shim: PASS");return 0;
}
'''


class Tests(unittest.TestCase):
    def test_source_contract(self):
        s = SOURCE.read_text(encoding='utf8')
        self.assertIn('if (!ml_tcp_bind_vpn(fd, ml, dest_ip, dest_port, &d.netif_index)) {', s)
        self.assertIn('for (int attempt = 0; attempt < 2;', s)
        self.assertIn('SO_BINDTODEVICE, &c.iface, sizeof(c.iface)', s)
        self.assertNotIn('setsockopt(', function(s, 'ml_tcp_vpn_snapshot'))
        self.assertIn('LWIP_ASSERT_CORE_LOCKED()', function(s, 'ml_tcp_vpn_snapshot'))

    def test_native(self):
        cc = os.environ.get('VPN_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
        if not cc:
            self.skipTest('native host compiler unavailable; export harness to compiler host')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'vpn.c'; exe = Path(d)/('vpn.exe' if os.name == 'nt' else 'vpn')
            p.write_text(native_harness(), encoding='utf8')
            subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable', str(p), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_syntax(self):
        cc = os.environ.get('VPN_SYNTAX_CC') or next(iter(Path.home().glob('.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-gcc.exe')), None)
        if not cc:
            self.skipTest('syntax compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'vpn.c'; p.write_text(native_harness(), encoding='utf8')
            subprocess.run([str(cc), '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable', '-fsyntax-only', str(p)], check=True)


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--export-native-harness':
        Path(sys.argv[2]).write_text(native_harness(), encoding='utf8')
    else:
        unittest.main()
