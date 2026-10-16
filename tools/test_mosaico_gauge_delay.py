"""Compile the actual wait helper against phase-aware simulated RTOS ticks."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILERS = list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))


class GaugeDelayTests(unittest.TestCase):
    def test_actual_deadline_wait(self):
        if not COMPILERS: self.skipTest('configured cross compiler unavailable')
        source = (ROOT / 'main/hal/mosaico/hal_mosaico.cpp').read_text()
        block = source.split('void gauge_delay_ms(uint32_t ms)', 1)[1].split('\nbool gauge_read(', 1)[0]
        code = r'''
#include <algorithm>
#include <cstdint>
#define pdMS_TO_TICKS(ms) ((ms)*hz/1000)
using TickType_t=uint32_t;
struct Simulation {
 uint32_t hz; int64_t now, lastFeed; unsigned feeds=0, sleeps=0; bool safe=true;
 constexpr int64_t esp_timer_get_time() const {return now;}
 constexpr void gauge_feed() {if(now-lastFeed>101000)safe=false;lastFeed=now;++feeds;}
 constexpr void vTaskDelay(TickType_t ticks) {
  if(!ticks){safe=false;return;}
  now=(now/(1000000/hz)+ticks)*(1000000/hz);++sleeps;
 }
 constexpr void gauge_delay_ms(uint32_t ms)
''' + block + r'''
};
constexpr bool cases() {
 for(uint32_t hz:{100U,1000U}) {
  for(int64_t phase:{0LL,1LL,499LL,999LL,9999LL}) {
   for(uint32_t ms:{0U,1U,20U,50U,100U,2000U,5000U}) {
    Simulation s{hz,phase,phase};s.gauge_delay_ms(ms);
    if(!s.safe||!s.feeds||s.now-phase<int64_t(ms)*1000)return false;
    if(s.now-phase>int64_t(ms)*1000+1000000/hz)return false;
    if(ms && !s.sleeps)return false;
   }
  }
 }
 return true;
}
static_assert(cases(),"minimum delay, tick phase, bounded watchdog gaps");
'''
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / 'wait.cpp'
            src.write_text(code)
            result = subprocess.run([str(COMPILERS[0]), '-std=c++17', '-fsyntax-only', str(src)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        # No device sequence or requested dwell was shortened.
        legacy_return = source.split('bool gauge_return_access(', 1)[1].split('esp_err_t gauge_load_reload(', 1)[0]
        legacy_access = source.split('bool Hal::gaugeAccess(', 1)[1].split('void Hal::i2c_init()', 1)[0]
        self.assertEqual(legacy_return.count('gauge_delay_ms(5000)'), 1)
        self.assertEqual(legacy_access.count('gauge_delay_ms(5000)'), 2)
        backend = source.split('struct GaugeFccBackend {', 1)[1].split('struct GaugeThresholdBackend', 1)[0]
        self.assertEqual(backend.count('gauge_delay_ms(5000)'), 3)
        for method, following in [('sendUnsealPair', 'sendFullPair'),
                                  ('sendFullPair', 'dmSnapshot'), ('reseal', 'verifyTarget')]:
            phase = backend.split('bool ' + method + '(', 1)[1].split('bool ' + following + '(', 1)[0]
            self.assertEqual(phase.count('gauge_delay_ms(5000)'), 1)
        self.assertIn('gauge_delay_ms(2000); // TI:', source)


if __name__ == '__main__':
    unittest.main()
