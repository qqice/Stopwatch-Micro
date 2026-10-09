"""Offline diagnostics/source harness only; never opens a serial port or changes transport policy."""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

R = Path(__file__).resolve().parents[1]
CPP = (R / 'main/host/tailscale_transport.cpp').read_text(encoding='utf8')
HDR = (R / 'main/host/tailscale_transport.h').read_text(encoding='utf8')
SERIAL = (R / 'main/debug/serial_debug.cpp').read_text(encoding='utf8')
FETCH = CPP.split('bool TailnetQuota::fetch(', 1)[1]


class TailnetFetchDiagnosticsTests(unittest.TestCase):
    def test_failure_stages_and_original_policy(self):
        for stage in ('NotReady', 'Input', 'Connect', 'Send', 'Allocation', 'Receive',
                      'HttpStatus', 'Header', 'Oversize', 'Deadline', 'Incomplete', 'Success'):
            self.assertIn('FetchStage::' + stage, FETCH)
        self.assertIn('if (count < 0) { failure = FetchStage::Receive; break; }', FETCH)
        self.assertIn('!ok ? FetchStage::Send : !response ? FetchStage::Allocation', FETCH)
        self.assertIn('microlink_tcp_connect(_client, _peer, _port, 7000)', FETCH)
        self.assertIn('esp_timer_get_time() + 10000000', FETCH)
        self.assertIn('capacity + 2047 - used, 2000', FETCH)
        self.assertIn('heap_caps_calloc(1, capacity + 2048, MALLOC_CAP_SPIRAM)', FETCH)
        self.assertIn('std::memcpy(body, payload, contentLength)', FETCH)
        self.assertEqual(FETCH.count('microlink_tcp_connect('), 1)
        self.assertEqual(FETCH.count('microlink_tcp_close('), 1)
        self.assertNotIn('ESP_LOG', FETCH)
        self.assertNotIn('vTaskDelay', FETCH)

    def test_snapshot_and_output_are_numeric_only(self):
        diagnostic = HDR.split('struct FetchDiagnostics {', 1)[1].split('};', 1)[0]
        self.assertNotIn('char', diagnostic)
        output = SERIAL.split('DBG TAIL_FETCH ', 1)[1].split(');', 1)[0]
        self.assertNotIn('%s', output)
        for secret in ('token', '_path', '_host', '_key', 'body', 'response'):
            self.assertNotIn(secret, output)
        locks = re.findall(r'portENTER_CRITICAL\(&fetchDiagnosticsMux\);(.*?)'
                           r'portEXIT_CRITICAL\(&fetchDiagnosticsMux\);', CPP, re.S)
        self.assertEqual(len(locks), 2)
        for body in locks:
            self.assertIn('_fetch_diagnostics', body)
            self.assertNotIn('microlink', body)
            self.assertNotIn('esp_timer', body)
        self.assertIn('if (index >= 4) return {};', CPP)

    def test_compiled_actual_source_and_boundary_model(self):
        compilers = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob(
            '*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        self.assertTrue(compilers, 'embedded compiler required for this source-only test')
        methods = CPP[CPP.index('TailnetQuota::FetchDiagnostics TailnetQuota::fetchDiagnostics('):]
        elapsed = re.search(r'diagnostic.elapsedMs = (.*?);', FETCH)[1]
        fallback = re.search(r'failure = (used >= capacity \+ 2047.*?);', FETCH, re.S)[1]
        classification = re.search(r'const FetchPath kind = (.*?);', FETCH, re.S)[1]
        # Compile the actual functions with SDK declarations, not a full HAL build.
        harness = r'''
#include "tailscale_transport.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <strings.h>
struct microlink_s {};
using portMUX_TYPE = int;
portMUX_TYPE fetchDiagnosticsMux = 0;
#define portENTER_CRITICAL(x) ((void)(x))
#define portEXIT_CRITICAL(x) ((void)(x))
constexpr int ESP_OK = 0, MALLOC_CAP_SPIRAM = 1;
int64_t esp_timer_get_time();
bool microlink_is_connected(microlink_s*);
void* microlink_tcp_connect(microlink_s*, uint32_t, uint16_t, int);
int microlink_tcp_send(void*, const char*, int);
int microlink_tcp_recv(void*, char*, size_t, int);
void microlink_tcp_close(void*);
void* heap_caps_calloc(size_t, size_t, int);
const char* strcasestr(const char*, const char*);
'''
        harness += methods
        harness += '\nusing FetchStage = TailnetQuota::FetchStage;\n'
        harness += '\nusing FetchPath = TailnetQuota::FetchPath;\n'
        # Evaluate actual diagnostic expressions at compile time for numeric boundaries.
        harness += 'constexpr uint32_t elapsed(int64_t ms) { return ' + elapsed + '; }\n'
        harness += ('constexpr FetchStage fallback(size_t used, size_t capacity, int64_t now, int64_t deadline) '
                    '{ return ' + fallback.replace('esp_timer_get_time()', 'now') + '; }\n')
        # std::strcmp is not constexpr in C++17; use a tiny equivalent adapter in this model only.
        harness += r'''
constexpr int cmp(const char* a, const char* b, size_t limit = SIZE_MAX) {
    for(size_t i=0; i<limit; ++i) { if(a[i]!=b[i]) return 1; if(!a[i]) return 0; }
    return 0;
}
'''
        harness += ('constexpr FetchPath classify(const char* path, const char* pathOverride) { return ' +
                    classification.replace('std::strcmp', 'cmp').replace('std::strncmp', 'cmp') + '; }\n')
        harness += r'''
static_assert(elapsed(-1)==0 && elapsed(0)==0 && elapsed(17000)==17000);
static_assert(elapsed(int64_t(UINT32_MAX)+1)==UINT32_MAX);
static_assert(fallback(1,8193,100,100)==FetchStage::Deadline);
static_assert(fallback(1,8193,99,100)==FetchStage::Incomplete);
static_assert(fallback(8193+2047,8193,99,100)==FetchStage::Oversize);
static_assert(classify("/v1/history","/v1/history")==FetchPath::History);
static_assert(classify("/v2/history","/v2/history")==FetchPath::History);
static_assert(classify("/v2/status","/v2/status")==FetchPath::Quota);
static_assert(classify("/v1/quota",nullptr)==FetchPath::Quota);
static_assert(classify("/v1/ota/manifest","/v1/ota/manifest")==FetchPath::Ota);
static_assert(classify("/v1/ota/chunk?sha256=redacted","/v1/ota/chunk")==FetchPath::Ota);
static_assert(classify("/unknown","/unknown")==FetchPath::Other);
static_assert(unsigned(FetchStage::None)==0 && unsigned(FetchStage::Success)==12);
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'diagnostics.cpp'
            source.write_text(harness, encoding='utf8')
            result = subprocess.run([str(compilers[-1]), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                                     '-fsyntax-only', '-I', str(R / 'main/host'), str(source)],
                                    capture_output=True, text=True)
        log = R / '.artifacts/mosaico/tailnet-fetch-diagnostics-source-harness.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(result.stdout + result.stderr, encoding='utf8')
        self.assertEqual(result.returncode, 0, 'source harness failed: ' + str(log))


if __name__ == '__main__':
    unittest.main()
