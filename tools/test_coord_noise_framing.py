"""Native fault shim exporting production RX functions (no SDK/device IO)."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_derp_tx_backpressure import function
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'components/microlink-source/components/microlink'

def native_harness():
    src = (BASE / 'src/ml_coord.c').read_text(encoding='utf8')
    stats = (BASE / 'components/wireguard_lwip/src/wireguardif_rx_stats.h').read_text(encoding='utf8')
    enum = re.search(r'typedef enum \{ COORD_RX_FATAL.*?coord_rx_result_t;', src).group()
    rx = ''.join(function(src, n) for n in ('coord_recv', 'noise_recv', 'do_h2_preface'))
    # Exact production poll entry through its receive-result exit. H2 parsing is
    # intentionally outside this RX classification fixture and remains unchanged.
    poll = src[src.index('static int poll_map_update('):].split('    /* Extract DATA frame payload')[0]
    poll += '    free(frame_buf); return 1;\n}\n'
    register = src[src.index('static int do_register('):].split('        ESP_LOGI(TAG, "RegisterResponse Noise frame')[0]
    register = register[register.index('        uint8_t *frame_buf = ml_psram_malloc(4096);'):]
    reg_fixture = "static int register_rx_exit(microlink_t *ml, ml_noise_state_t *noise) {\nuint8_t *h2_resp=ml_psram_malloc(16384), *resp_buf=ml_psram_malloc(8192);\nfor (;;) {\n" + register + "free(frame_buf); break; } free(h2_resp); free(resp_buf); return 0; }\n"
    mapping = src[src.index('static int do_fetch_peers('):] if 'static int do_fetch_peers(' in src else src[src.index('    bool got_end_stream = false;'):]
    mapping = mapping[mapping.index('        uint8_t *frame_buf = ml_psram_malloc(65536);'):].split('        /* Append decrypted data')[0]
    map_fixture = "static int map_rx_exit(microlink_t *ml, ml_noise_state_t *noise) {\nuint8_t *h2_recv=ml_psram_malloc(16384), *resp_buf=ml_psram_malloc(8192);\nstruct timeval rcv_tv = { .tv_sec=60, .tv_usec=0 }; for (;;) {\n" + mapping + "free(frame_buf); break; } free(h2_recv); free(resp_buf); return 0; }\n"
    prefix = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <assert.h>
#include <stdio.h>
#include <sys/types.h>
#ifdef _WIN32
#include <winsock2.h>
#else
#include <sys/select.h>
#endif
#define ESP_OK 0
static void shim_log(const char *tag, const char *fmt, ...) { (void)tag; (void)fmt; }
#define ESP_LOGI(...) shim_log(__VA_ARGS__)
#define ESP_LOGW(...) shim_log(__VA_ARGS__)
#define ESP_LOGE(...) shim_log(__VA_ARGS__)
#define pdMS_TO_TICKS(ms) (ms)
#define ML_H2_BUFFER_SIZE 524288
#ifndef SO_RCVTIMEO
#define SO_RCVTIMEO 20
#define SOL_SOCKET 1
#endif
static const char *TAG = "shim";
typedef struct { int coord_sock; } microlink_t;
typedef struct { uint8_t rx_key[32]; uint64_t rx_nonce; } ml_noise_state_t;
static unsigned head, tail, cursor, decrypts, delays, outstanding, allocs, fail_alloc, sends;
static int auth_fail, timeout;
static int events[4096]; static uint8_t wire[128];
static microlink_t client; static ml_noise_state_t state;
static int ml_recv(int fd, uint8_t *buf, size_t len, int flags) {
    (void)fd; (void)flags; assert(head < tail); int n = events[head++];
    if (n < 0) { errno = n == -1 ? EAGAIN : EIO; return -1; }
    assert((size_t)n <= len); if(n) { memcpy(buf, wire+cursor, n); cursor += n; } return n;
}
static void vTaskDelay(unsigned ms) { assert(ms == 10); ++delays; }
static void *ml_psram_malloc(size_t n) { ++allocs; if (allocs == fail_alloc) return NULL; ++outstanding; return malloc(n); }
static void shim_free(void *p) { if(p) { assert(outstanding); --outstanding; free(p); } }
#define free shim_free
static int ml_noise_decrypt(const uint8_t *key, uint64_t nonce, const void *ad, size_t al,
 const uint8_t *ct, size_t len, uint8_t *pt) {
    (void)key; (void)ad; (void)al; assert(nonce == state.rx_nonce); ++decrypts;
    assert(!memcmp(ct, wire+3, len)); if(auth_fail) return -1; memcpy(pt, ct, len-16); return ESP_OK;
}
static int64_t esp_timer_get_time(void) { return 0; }
static int ml_h2_build_preface(uint8_t *b, size_t n) { (void)b; (void)n; return 30; }
static int ml_h2_build_settings_ack(uint8_t *b, size_t n) { (void)b; (void)n; return 9; }
static int ml_h2_build_window_update(uint8_t *b, size_t n, int stream, uint32_t delta) { (void)b;(void)n;(void)stream;(void)delta; return 13; }
static int noise_send(microlink_t *m, ml_noise_state_t *s, const uint8_t *b, size_t n) { (void)m;(void)s;(void)b;(void)n; ++sends; return 0; }
static int ml_select_fds(int n, fd_set *r, void *w, void *e, struct timeval *t) { (void)n;(void)r;(void)w;(void)e;(void)t; return 1; }
static int ml_setsockopt(int fd,int l,int o,const void *v,size_t n) { (void)fd;(void)l;(void)o;(void)n; timeout=((const struct timeval*)v)->tv_sec; return 0; }
'''
    suffix = r'''
static void reset(void) {
    assert(outstanding == 0); head=tail=cursor=decrypts=delays=allocs=fail_alloc=sends=0;
    auth_fail=timeout=0; memset(&state,0,sizeof(state)); memset(wire,0,sizeof(wire));
    memset(wireguardif_rx_counters,0,sizeof(wireguardif_rx_counters)); errno=EAGAIN;
    wire[0]=4; wire[2]=20; for(unsigned i=3;i<23;i++) wire[i]=(uint8_t)i;
}
static void add(int n,unsigned count) { while(count--) events[tail++]=n; }
static int receive(void) { uint8_t out[64]; int n=noise_recv(&client,&state,out,sizeof(out));
    if(n>0) assert(!memcmp(out,wire+3,(size_t)n));
    assert(outstanding==0); return n; }
int main(void) {
    wireguardif_rx_store(WG_RX_CTRL_LAST_REASON, 0);
    assert(!wireguardif_rx_is_synack(NULL, 0));
    uint8_t out[64]; reset(); add(0,1); assert(receive()==-1 && head==1 && wireguardif_rx_counters[WG_RX_CTRL_RX_EOF]==1);
    reset(); add(-1,1); assert(receive()==0 && !decrypts && !state.rx_nonce);
    reset(); add(1,1);add(-1,1);add(2,1);add(20,1); assert(receive()==4 && decrypts==1 && state.rx_nonce==1 && delays==1);
    reset(); add(3,1);add(7,1);add(-1,1);add(13,1); assert(receive()==4 && decrypts==1 && state.rx_nonce==1 && delays==1);
    reset(); add(3,1);add(-1,2);add(20,1); assert(receive()==4 && decrypts==1 && delays==2);
    reset(); add(3,1);add(7,1);add(-1,301);add(13,1); assert(receive()==-1 && head==303 && cursor==10 && !decrypts && delays==300);
    assert(wireguardif_rx_counters[WG_RX_CTRL_RX_PARTIAL_TIMEOUT]==1);
    reset(); add(1,1);add(-1,301);add(2,1); assert(receive()==-1 && head==302 && cursor==1 && !decrypts);
    reset(); add(3,1);add(-1,301);add(20,1); assert(receive()==-1 && head==302 && cursor==3 && !decrypts && delays==300);
    reset(); add(3,1);add(0,1); assert(receive()==-1 && head==2 && !decrypts);
    reset(); wire[0]=3;add(3,1);assert(receive()==-1 && head==1 && wireguardif_rx_counters[WG_RX_CTRL_RX_INVALID]==1);
    reset(); wire[2]=15;add(3,1);assert(receive()==-1 && head==1);
    reset(); wire[1]=1;add(3,1);assert(receive()==-1 && head==1);
    reset(); fail_alloc=1;add(3,1);assert(receive()==-1 && head==1);
    reset(); auth_fail=1;add(3,1);add(20,1);assert(receive()==-1 && decrypts==1 && state.rx_nonce==0 && wireguardif_rx_counters[WG_RX_CTRL_RX_AUTH_FAIL]==1);
    reset(); wire[2]=16;add(3,1);add(16,1);assert(receive()==0 && decrypts==1 && state.rx_nonce==1);
    reset(); add(-2,1);assert(coord_recv(&client,out,3)==COORD_RX_FATAL);
    reset(); add(0,1);assert(do_h2_preface(&client,&state)==-1 && sends==1 && !decrypts);
    reset(); add(-1,1);assert(do_h2_preface(&client,&state)==0 && sends==1);
    reset(); add(0,1);assert(poll_map_update(&client,&state)==-1 && outstanding==0 && timeout==2);
    reset(); add(-1,1);assert(poll_map_update(&client,&state)==0 && outstanding==0 && timeout==2);
    reset(); wire[0]=3;add(3,1);assert(poll_map_update(&client,&state)==-1 && outstanding==0);
    reset(); fail_alloc=1;assert(poll_map_update(&client,&state)==-1 && head==0 && outstanding==0);
    reset(); add(3,1);add(20,1);assert(poll_map_update(&client,&state)==1 && outstanding==0 && state.rx_nonce==1);
    reset(); add(0,1); assert(register_rx_exit(&client,&state)==-1 && outstanding==0 && !decrypts);
    reset(); fail_alloc=3; assert(register_rx_exit(&client,&state)==-1 && outstanding==0 && !head);
    reset(); add(-1,1); assert(register_rx_exit(&client,&state)==0 && outstanding==0);
    reset(); add(0,1); assert(map_rx_exit(&client,&state)==-1 && outstanding==0 && timeout==5 && !decrypts);
    reset(); fail_alloc=3; assert(map_rx_exit(&client,&state)==-1 && outstanding==0 && timeout==5 && !head);
    puts("coord Noise RX production fault shim: PASS"); return 0;
}
'''
    return prefix + stats + '\nuint32_t wireguardif_rx_counters[WG_RX_COUNT];\n' + enum + '\n' + rx + poll + reg_fixture + map_fixture + suffix

class Tests(unittest.TestCase):
    def test_native(self):
        cc = os.environ.get('COORD_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
        if not cc: self.skipTest('native host compiler unavailable; export to native host')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'rx.c'; exe=Path(d)/('rx.exe' if os.name=='nt' else 'rx')
            p.write_text(native_harness(), encoding='utf8')
            subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror',str(p),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)
    def test_syntax(self):
        cc = os.environ.get('COORD_SYNTAX_CC') or next(iter(Path.home().glob('.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-gcc.exe')),None)
        if not cc: self.skipTest('syntax compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'rx.c'; p.write_text(native_harness(),encoding='utf8')
            subprocess.run([str(cc),'-std=c11','-Wall','-Wextra','-Werror','-fsyntax-only',str(p)],check=True)

if __name__=='__main__':
    import sys
    if len(sys.argv)==3 and sys.argv[1]=='--export-native-harness':
        Path(sys.argv[2]).write_text(native_harness(),encoding='utf8')
    else: unittest.main()
