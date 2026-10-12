"""Compile-time pure scheduling checks; no board, build, or bank claims."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILERS = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))

class KeepaliveTests(unittest.TestCase):
    def test_backend_safety_structure(self):
        code = (ROOT / 'main/host/powerbank_keepalive.cpp').read_text()
        for prohibited in ('esp_restart(', 'esp_pm_configure(', 'esp_wifi_', 'nvs_', 'esp_timer_create(', 'vTaskSuspendAll('):
            self.assertNotIn(prohibited, code)
        for required in ('StandbySleep::pulseEligible()', 'p.cpuMHz==160', '!p.wifiRunning',
                         'MosaicoOta::healthPending()', 'ESP_PM_NO_LIGHT_SLEEP',
                         '~PulseLock() { release(); }', 'xTaskNotifyWait(',
                         'if(!valid(config)) {', 'sliceUntil=esp_timer_get_time()+500',
                         'PulseCompletion completion{version};',
                         'model.completePulse(esp_timer_get_time(),version);',
                         'if(!model.pulseAllowed(publishNow,version,true)) {',
                         'if(fatal || model.generation!=reservation)'):
            self.assertIn(required, code)
        publication = code[code.index('const auto publishNow='):code.index('const auto until=')]
        self.assertLess(publication.index('portENTER_CRITICAL(&mux);'),
                        publication.index('if(!model.pulseAllowed(publishNow,version,true))'))
        self.assertLess(publication.index('portEXIT_CRITICAL(&mux);return;'),
                        publication.index('stats.pulsing=true;'))
        self.assertNotIn('safe()', publication)

    def test_actual_model(self):
        if not COMPILERS:
            self.skipTest('cross compiler unavailable')
        code = r'''
#include "main/host/powerbank_keepalive_model.h"
using namespace PowerbankKeepalive;
constexpr bool cases() {
 AutoPolicy a;
 if(a.desired(0))return false;
 a.configure(true);a.observe(true,true,1);
 if(!a.desired(1)||!a.desired(90000001)||a.desired(90000002))return false;
 a.inhibited=true;a.observe(true,true,2);if(a.desired(2))return false;
 a.configure(false);a.configure(true);if(!a.desired(2))return false;
 a.observe(true,false,3);if(a.desired(3))return false;
 a.observe(true,true,4);if(!a.desired(4))return false;
 a.observe(false,true,5);if(a.desired(5))return false;
 Model automatic;automatic.startAutomatic(100);
 if(!automatic.automatic||!automatic.enabled(3600000000LL)||automatic.leaseUntil)return false;
 automatic.expire(3600000000LL);if(!automatic.enabled(3600000000LL))return false;
 const auto av=automatic.generation;automatic.off();
 if(automatic.enabled(3600000001LL)||automatic.pulseAllowed(3600000001LL,av,true))return false;
 Model m; Request r;
 if(m.enabled(0)||m.take(100000000,true)||m.pulseAllowed(0,0,true))return false;
 for(auto invalid : {Request{179,15,500},Request{1801,15,500},Request{180,4,500},
     Request{180,21,500},Request{180,15,99},Request{180,15,1001}}) {
   if(m.request(invalid,0)||m.enabled(0))return false;
 }
 if(!m.request(r,0)||m.take(14999999,true))return false;
 const auto version=m.generation;
 if(!m.take(15000000,true)||m.take(15000000,true))return false;
 if(!m.pulseAllowed(15000000,version,true)||m.pulseAllowed(15000000,version,false))return false;
 // Missed blocked slots are discarded even after a very long gap.
 if(m.take(95000000,false)||m.nextDue!=110000000)return false;
 if(m.take(95000001,true)||!m.take(110000000,true)||m.take(110000001,true))return false;
 if(m.take(180000000,true)||m.enabled(180000000)||m.pulseAllowed(180000000,version,true))return false;
 if(!m.request(Request{1800,20,1000},180000000))return false;
 const auto old=m.generation;
 if(!m.request(Request{180,5,100},180000001)||m.pulseAllowed(180000001,old,true))return false;
 auto until=m.leaseUntil;
 if(m.request(Request{0,0,0},180000001)||m.leaseUntil!=until)return false;
 m.off();if(m.enabled(180000001)||m.pulseAllowed(180000001,m.generation,true))return false;
 // 5s reservation, delayed start 9.95s, real end 10.2s: no burst at 10.2s.
 Model delayed;
 if(!delayed.request(Request{180,5,250},0)||!delayed.take(5000000,true))return false;
 const auto pulseVersion=delayed.generation;
 delayed.completePulse(10200000,pulseVersion);
 if(delayed.nextDue!=15200000||delayed.take(10200000,true)||delayed.take(15199999,true))return false;
 if(!delayed.take(15200000,true))return false;
 // Abort / early gate exit gets the same cooldown; cannot shorten a future slot.
 delayed.completePulse(15200001,pulseVersion);
 if(delayed.nextDue!=20200001||delayed.take(20200000,true))return false;
 delayed.completePulse(15200000,pulseVersion);
 if(delayed.nextDue!=20200001)return false;
 // An old attempt cannot overwrite a replacement configuration's schedule.
 if(!delayed.request(Request{180,15,500},16000000))return false;
 auto newDue=delayed.nextDue;
 delayed.completePulse(17000000,pulseVersion);
 if(delayed.nextDue!=newDue)return false;
 const auto newVersion=delayed.generation;
 delayed.completePulse(delayed.leaseUntil,newVersion);
 if(delayed.nextDue!=newDue)return false; // expired lease is not extended
 delayed.off();delayed.completePulse(20000000,newVersion);
 if(delayed.nextDue!=0 || delayed.leaseUntil)return false;
 // Permission can become stale before publishing Pulsing; revalidate under mux.
 Model publication;
 if(!publication.request(r,0))return false;
 auto publicationVersion=publication.generation;
 if(!publication.pulseAllowed(15000000,publicationVersion,true))return false;
 publication.off();
 if(publication.pulseAllowed(15000001,publicationVersion,true))return false;
 if(!publication.request(r,15000002))return false;
 publicationVersion=publication.generation;
 if(!publication.pulseAllowed(30000002,publicationVersion,true))return false;
 if(!publication.request(Request{180,5,250},30000003))return false;
 if(publication.pulseAllowed(30000004,publicationVersion,true))return false;
 // Even without a command, expiry between permission and publication blocks.
 publicationVersion=publication.generation;
 if(!publication.pulseAllowed(publication.leaseUntil-1,publicationVersion,true))return false;
 return !publication.pulseAllowed(publication.leaseUntil,publicationVersion,true);
}
static_assert(cases(),"off, bounds, finite lease, no backlog, reconfigure abort, fail closed");
'''
        code = '#include <initializer_list>\n' + code
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'model.cpp'
            source.write_text(code)
            result = subprocess.run([str(COMPILERS[-1]), '-std=c++17', '-fsyntax-only',
                                     '-I'+str(ROOT), str(source)], capture_output=True, text=True)
        log = ROOT / 'artifacts/powerbank-keepalive/model-test.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, str(log))

if __name__ == '__main__':
    unittest.main()
