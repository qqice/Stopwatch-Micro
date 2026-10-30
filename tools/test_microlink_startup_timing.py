"""Execute extracted production first-attempt timing helpers on RV32.
Clock/critical-section stubs only; no scheduler, firmware or hardware claim.
Run with C:/Espressif/tools/python/v6.1/venv/Scripts/python.exe.
"""
import hashlib
import re
import unittest
import test_microlink_peer_probe as base

SOURCE = base.ROOT / 'components/microlink-source/components/microlink/src/ml_coord.c'
HEADER = SOURCE.parent.parent / 'include/microlink.h'
RUN = base.ROOT / 'artifacts/microlink-startup-timing-tests'

PREFIX = r'''
typedef unsigned int uint32_t;typedef long long int64_t;
extern "C" void *memcpy(void *d,const void *s,unsigned n){for(unsigned i=0;i<n;++i)((char*)d)[i]=((const char*)s)[i];return d;}
extern "C" void *memset(void *d,int v,unsigned n){for(unsigned i=0;i<n;++i)((char*)d)[i]=v;return d;}
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
static unsigned enters,exits,depth,errors,clock_calls;
static int64_t clock_us;
static void enter(portMUX_TYPE*){++enters;if(depth++)++errors;}
static void leave(portMUX_TYPE*){++exits;if(depth!=1)++errors;--depth;}
#define portENTER_CRITICAL(x) enter(x)
#define portEXIT_CRITICAL(x) leave(x)
static int64_t esp_timer_get_time(){++clock_calls;if(depth!=1)++errors;return clock_us;}
'''
CASES = r'''
static int run_case(int id){
 s_startup_timing={1,0,{}};s_startup_capture_active=false;
 enters=exits=depth=errors=clock_calls=0;clock_us=0;
 microlink_startup_timing_t out;
 microlink_startup_timing_get(0);if(enters||exits||clock_calls)return 1;
 microlink_startup_timing_get(&out);
 if(out.version!=1||out.valid_mask||clock_calls||enters!=1||exits!=1)return 2;
 startup_timing_begin();if(s_startup_timing.valid_mask!=1||s_startup_timing.mark_ms[0])return 3;
 if(id<10){
  for(unsigned i=1;i<=(unsigned)id;++i){clock_us=i*1000000;startup_timing_mark(i);}
  auto saved=s_startup_timing;
  // Stop models actual reconnect/cleanup/disconnect hooks checked below.
  startup_timing_stop();clock_us=99000000;startup_timing_begin();
  for(unsigned i=0;i<10;++i)startup_timing_mark(i);
  if(s_startup_timing.valid_mask!=saved.valid_mask)return 4;
  for(unsigned i=0;i<10;++i)if(s_startup_timing.mark_ms[i]!=saved.mark_ms[i])return 5;
 }else if(id==10){
  startup_timing_mark(1);clock_us=999000;startup_timing_mark(1);
  if(s_startup_timing.mark_ms[1]||s_startup_timing.valid_mask!=3)return 6;
  startup_timing_mark(10);startup_timing_mark(32);startup_timing_mark(0xffffffff);
  if(s_startup_timing.valid_mask!=3)return 7;
 }else if(id==11){
  clock_us=4294967301000LL;startup_timing_mark(2);
  if(s_startup_timing.mark_ms[2]!=5)return 8;
 }else if(id==12){
  for(unsigned i=1;i<10;++i){clock_us=i*1000;startup_timing_mark(i);}
  if(s_startup_timing.valid_mask!=1023||s_startup_capture_active)return 9;
  clock_us=90000;startup_timing_begin();startup_timing_mark(9);
  if(s_startup_timing.mark_ms[9]!=9)return 10;
 }
 auto saved=s_startup_timing;bool active=s_startup_capture_active;
 unsigned clocks=clock_calls,old=enters;
 microlink_startup_timing_get(&out);microlink_startup_timing_get(&out);
 if(enters!=old+2||clock_calls!=clocks||s_startup_capture_active!=active)return 11;
 if(out.version!=saved.version||out.valid_mask!=saved.valid_mask)return 12;
 for(unsigned i=0;i<10;++i)if(out.mark_ms[i]!=saved.mark_ms[i]||s_startup_timing.mark_ms[i]!=saved.mark_ms[i])return 13;
 return errors||depth||enters!=exits?14:0;
}
extern "C" {volatile int test_results[13]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<13;++i)test_results[i]=run_case(i);test_done();}
'''


def fixture(source):
    header = HEADER.read_text(encoding='utf8')
    decl = re.search(r'typedef struct \{\s*uint32_t version;\s*uint32_t valid_mask;\s*uint32_t mark_ms\[10\];\s*\} microlink_startup_timing_t;', header).group()
    helpers = source[source.index('/* First-attempt startup timing:'):source.index('/* Effective control plane host:')]
    return PREFIX + decl + '\n' + helpers + CASES


class StartupTimingTests(unittest.TestCase):
    def test_actual_helpers_rv32(self):
        original_fixture, original_run = base.fixture, base.RUN
        try:
            base.fixture, base.RUN = fixture, RUN
            source = SOURCE.read_text(encoding='utf8')
            self.assertEqual(base.execute(source, 'first-attempt', 13), (0,) * 13)
            (RUN / 'first-attempt-execution.log').write_text(
                'Extracted production startup helpers, mocked clock/critical sections. '
                'No scheduler/network/hardware claim. source_sha256=' +
                hashlib.sha256(source.encode()).hexdigest() + '\n'
                '13 RV32 cases passed: partial failures at each stage, restart freeze, '
                'success freeze, duplicate stage, 0ms validity, bounds, wrap, NULL/getter purity, lock balance.\n', encoding='utf8')
        finally:
            base.fixture, base.RUN = original_fixture, original_run

    def test_source_binding_and_unchanged_waits(self):
        s = SOURCE.read_text(encoding='utf8')
        task = base.function(s, 'void ml_coord_task(')
        self.assertTrue(task.startswith('void ml_coord_task(void *arg) {\n    startup_timing_begin();'))
        self.assertEqual(task.count('startup_timing_begin();'), 1)
        for stage in range(1, 10):
            self.assertEqual(task.count(f'startup_timing_mark({stage});'), 1)
        bindings = [
            ('case COORD_STUN_PROBE:', 'case COORD_DNS_RESOLVE:', 'ml_stun_send_probe_ipv6(', 1),
            ('case COORD_TCP_CONNECT:', 'case COORD_NOISE_HANDSHAKE:', 'if (do_tcp_connect(ml) < 0)', 2),
            ('case COORD_NOISE_HANDSHAKE:', 'case COORD_H2_PREFACE:', 'process_proactive_frames(ml, &noise);', 3),
            ('case COORD_H2_PREFACE:', 'case COORD_REGISTER:', 'if (do_h2_preface(ml, &noise) < 0)', 4),
            ('case COORD_REGISTER:', 'case COORD_FETCH_PEERS:', 'if (do_register(ml, &noise) < 0)', 5),
            ('case COORD_FETCH_PEERS:', 'case COORD_LONG_POLL:', 'if (do_fetch_peers(ml, &noise) < 0)', 6)]
        for start, end, call, mark in bindings:
            block = task[task.index(start):task.index(end)]
            self.assertLess(block.index(call), block.index(f'startup_timing_mark({mark});'))
            if mark in (2, 4, 5, 6):
                self.assertRegex(block, r'state = COORD_RECONNECTING;\s*break;\s*}\s*startup_timing_mark\(' + str(mark) + r'\);')
        fetch = task[task.index('case COORD_FETCH_PEERS:'):task.index('case COORD_LONG_POLL:')]
        self.assertRegex(fetch, r'pdFALSE, pdTRUE, pdMS_TO_TICKS\(15000\)\);\s*}\s*startup_timing_mark\(7\);')
        self.assertLess(fetch.index('startup_timing_mark(6);'), fetch.index('if (!ml->derp.connected)'))
        self.assertLess(fetch.index('startup_timing_mark(7);'), fetch.index('do_start_long_poll('))
        self.assertRegex(fetch, r'do_send_endpoint_update\(ml, &noise\);\s*startup_timing_mark\(8\);')
        self.assertRegex(fetch, r'ml->state = ML_STATE_CONNECTED;\s*startup_timing_mark\(9\);')
        self.assertIn('case COORD_RECONNECTING:\n            startup_timing_stop();', task)
        self.assertIn('cleanup:\n    startup_timing_stop();', task)
        self.assertIn('case ML_CMD_DISCONNECT:\n                startup_timing_stop();', task)
        getter = base.function(s, 'void microlink_startup_timing_get(')
        self.assertIn('*out = s_startup_timing;', getter)
        for forbidden in ('esp_timer', 'malloc', 'ml->', 'send', 'socket', 'startup_timing_mark'):
            self.assertNotIn(forbidden, getter)
        for signature in ('static void startup_timing_begin(', 'static void startup_timing_mark(',
                          'static void startup_timing_stop(', 'void microlink_startup_timing_get('):
            body = base.function(s, signature)
            self.assertEqual(body.count('portENTER_CRITICAL(&s_startup_timing_mux);'), 1)
            self.assertEqual(body.count('portEXIT_CRITICAL(&s_startup_timing_mux);'), 1)


if __name__ == '__main__':
    unittest.main()
