"""Export exact production C functions to a native fault shim; no SDK/device IO."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'components/microlink-source/components/microlink'


def function(source, name):
    match = re.search(r'^(?:static )?[\w *]+\b' + name + r'\(', source, re.M)
    if not match:
        raise ValueError(name)
    start = source.index('{', match.start())
    depth = 1
    end = start + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end] + '\n'


def native_harness():
    src = (BASE / 'src/ml_derp.c').read_text(encoding='utf8')
    stats = (BASE / 'components/wireguard_lwip/src/wireguardif_rx_stats.h').read_text(encoding='utf8')
    snapshot = function((BASE / 'components/wireguard_lwip/src/wireguardif.c').read_text(encoding='utf8'), 'wireguardif_rx_stats')
    functions = ''.join(function(src, name) for name in (
        'ml_derp_bio_send', 'microlink_set_derp_tx_retry_budget_ms',
        'microlink_get_derp_tx_retry_budget_ms', 'derp_tx_observe',
        'derp_tls_write_all', 'derp_write_frame'))
    coord = (BASE / 'src/ml_coord.c').read_text(encoding='utf8')
    functions += function(coord, 'coord_observe')
    noise_start = coord.split('case COORD_NOISE_HANDSHAKE:', 1)[1].split('int handshake_ret', 1)[0]
    noise_start = re.search(r'wireguardif_rx_count\(WG_RX_CTRL_NOISE_START\);', noise_start)[0]
    reconnect = coord.split('case COORD_RECONNECTING:', 1)[1].split('xEventGroupClearBits', 1)[0]
    reconnect = reconnect[reconnect.index('wireguardif_rx_count'):]
    functions += 'static void coord_start_marker(void) {\n' + noise_start + '\n}\n'
    functions += 'static void coord_reconnect_marker(void) {\n' + reconnect + '\n}\n'
    prefix = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <errno.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
typedef int esp_err_t;
typedef struct { uint32_t derp_tx_retry_cap; struct { int ssl; } derp; } microlink_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG -20
#define MBEDTLS_ERR_SSL_WANT_READ -1
#define MBEDTLS_ERR_SSL_WANT_WRITE -2
#define MBEDTLS_ERR_SSL_TIMEOUT -3
#define MBEDTLS_ERR_NET_INVALID_CONTEXT -4
#define MBEDTLS_ERR_NET_CONN_RESET -5
#define MBEDTLS_ERR_NET_SEND_FAILED -6
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define pdMS_TO_TICKS(ms) (ms)
static unsigned delays, calls, head, tail, change_at;
static uint32_t elapsed, change_budget;
static int events[2048];
static microlink_t client;
uint32_t ml_get_time_ms(void) { return elapsed; }
void vTaskDelay(unsigned ms) { assert(ms == 10); ++delays; elapsed += ms; }
static int ml_write_sock(int fd, const unsigned char *data, size_t len) {
    (void)fd; (void)data; (void)len;
    assert(head < tail); int ret = events[head++];
    if (ret == -2) { errno = EAGAIN; return -1; }
    if (ret < 0) errno = EIO;
    return ret;
}
static int mbedtls_ssl_write(int *ssl, const uint8_t *data, size_t len);
'''
    suffix = r'''
static int mbedtls_ssl_write(int *ssl, const uint8_t *data, size_t len) {
    (void)ssl; ++calls;
    if (calls == change_at) assert(microlink_set_derp_tx_retry_budget_ms(&client, change_budget) == ESP_OK);
    /* WANT_READ/TIMEOUT originate in TLS, not the socket BIO. */
    assert(head < tail);
    if (events[head] == -1 || events[head] == -3) return events[head++];
    int fd = 1; int ret = ml_derp_bio_send(&fd, data, len);
    if (ret > 0) assert((size_t)ret <= len);
    return ret;
}
static void reset(void) {
    memset(&client, 0, sizeof(client));
    memset(wireguardif_rx_counters, 0, sizeof(wireguardif_rx_counters));
    delays = calls = head = tail = change_at = elapsed = 0;
}
static void add(int ret, unsigned n) { while (n--) { assert(tail < 2048); events[tail++] = ret; } }
static int frame(void) { static const uint8_t body[2] = {0,0}; return derp_write_frame(&client, 1, body, 2); }
int main(void) {
    _Static_assert(sizeof(uint32_t) == 4, "32-bit atomic fields required");
    _Static_assert(sizeof(wireguardif_rx_stats_t) == WG_RX_COUNT * sizeof(uint32_t), "stats enum/fields mismatch");
    assert(wireguardif_rx_is_synack(NULL, 0) == 0);
    reset(); assert(microlink_get_derp_tx_retry_budget_ms(&client) == 500);
    assert(microlink_get_derp_tx_retry_budget_ms(NULL) == 500);
    assert(microlink_set_derp_tx_retry_budget_ms(NULL,500) == ESP_ERR_INVALID_ARG);
    assert(microlink_set_derp_tx_retry_budget_ms(&client,501) == ESP_ERR_INVALID_ARG);
    /* Exact default: 50 WANT+delays succeeds, 51st delays then exhausts. */
    add(-2,50); add(5,1); add(-2,50); add(2,1); assert(frame() == 0);
    assert(delays == 100 && elapsed == 1000);
    reset(); add(-2,51); assert(frame() == -1 && delays == 51);
    wireguardif_rx_stats_t st = wireguardif_rx_stats();
    assert(st.derp_tx_retry_exhausted == 1 && st.derp_tx_want == 51);
    assert(st.derp_tx_last_stage == WG_DERP_TX_HEADER && (int32_t)st.derp_tx_last_ret == -2);
    /* Positive progress resets retry count within the same write. */
    reset(); add(-2,50); add(1,1); add(-2,50); add(4,1); add(2,1);
    assert(frame() == 0 && delays == 100);
    reset(); assert(microlink_set_derp_tx_retry_budget_ms(&client,5000) == ESP_OK);
    assert(microlink_get_derp_tx_retry_budget_ms(&client) == 5000);
    add(-2,500); add(5,1); add(2,1); assert(frame() == 0 && delays == 500);
    reset(); assert(microlink_set_derp_tx_retry_budget_ms(&client,5000) == ESP_OK);
    add(-2,501); assert(frame() == -1 && delays == 501);
    /* Setter during header must not widen current body's budget. Next frame does widen. */
    reset(); change_at = 1; change_budget = 5000;
    add(5,1); add(-2,51); assert(frame() == -1 && delays == 51);
    add(-2,500); add(5,1); add(2,1); assert(frame() == 0 && delays == 551);
    /* Also narrowing during header cannot shrink current body's snapshot. */
    reset(); assert(microlink_set_derp_tx_retry_budget_ms(&client,5000) == ESP_OK);
    change_at = 1; change_budget = 500; add(5,1); add(-2,500); add(2,1);
    assert(frame() == 0 && delays == 500);
    reset(); add(0,1); assert(frame() == -1 && calls == 1 && delays == 0);
    assert(wireguardif_rx_stats().derp_tx_zero == 1);
    reset(); add(-99,1); assert(frame() == -1 && calls == 1 && delays == 0);
    assert(wireguardif_rx_stats().derp_tx_fatal == 1);
    /* WANT_READ and TIMEOUT retain the original retry classification. */
    reset(); add(-1,1); add(-3,1); add(5,1); add(2,1); assert(frame() == 0 && delays == 2);
    /* Execute production observation helper and actual state-case markers:
     * MAP_POLL_FAIL -> RECONNECT -> NOISE_START preserves failure causality. */
    reset(); elapsed = 123;
    coord_observe(WG_RX_CTRL_MAP_POLL_FAIL, WG_CTRL_MAP_POLL_FAIL, -99);
    elapsed = 234; coord_reconnect_marker();
    elapsed = 345; coord_start_marker();
    st = wireguardif_rx_stats();
    assert(st.ctrl_map_poll_fail == 1 && st.ctrl_reconnect == 1 && st.ctrl_noise_start == 1);
    assert(st.ctrl_reconnect_ms == 234);
    assert(st.ctrl_last_reason == WG_CTRL_MAP_POLL_FAIL);
    assert((int32_t)st.ctrl_last_ret == -99 && st.ctrl_last_ms == 123);
    puts("DERP TX native production fault shim: PASS"); return 0;
}
'''
    return prefix + stats + '\nuint32_t wireguardif_rx_counters[WG_RX_COUNT];\n' + snapshot + functions + suffix


class Tests(unittest.TestCase):
    def test_native(self):
        cc = os.environ.get('DERP_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
        if not cc:
            self.skipTest('native host compiler unavailable; export to host with compiler')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'tx.c'; exe = Path(d)/('tx.exe' if os.name == 'nt' else 'tx')
            p.write_text(native_harness(), encoding='utf8')
            subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', str(p), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_syntax(self):
        cc = os.environ.get('DERP_SYNTAX_CC') or next(iter(Path.home().glob('.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-gcc.exe')), None)
        if not cc:
            self.skipTest('syntax compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'tx.c'; p.write_text(native_harness(), encoding='utf8')
            subprocess.run([str(cc), '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsyntax-only', str(p)], check=True)


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--export-native-harness':
        Path(sys.argv[2]).write_text(native_harness(), encoding='utf8')
    else:
        unittest.main()
