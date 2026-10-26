"""Offline regression for old ledger caches blocking normal official boot."""
from pathlib import Path
import re,subprocess,tempfile,unittest
R=Path(__file__).resolve().parents[1]
HAL=R/'main/hal/mosaico/hal_mosaico.cpp'
POLL=R/'main/hal/mosaico/mosaico_gauge_scale_trial.inc'

class LegacyCacheTests(unittest.TestCase):
    def test_actual_helper_initializes_all_without_clearing_real_holds(self):
        source=HAL.read_text(encoding='utf8')
        helper=re.search(r'void gauge_legacy_cache_init_locked\(\)\n\{[^}]+\}',source).group()
        self.assertEqual(helper.count('_load_locked()'),3)
        for forbidden in ('nvs_set','gauge_word','gauge_control','restore','apply','store('):self.assertNotIn(forbidden,helper)
        code='''struct Cache {
 bool known=false,hold=true,pending=false; int reads=0;
 constexpr int load(){if(!known){known=true;++reads;hold=pending;}return 0;}
};
struct Probe {
 Cache old,factory,contrast;
 constexpr int scale_load_locked(){return old.load();}
 constexpr int factory_scale_load_locked(){return factory.load();}
 constexpr int contrast_load_locked(){return contrast.load();}
 constexpr bool held(){return old.hold||factory.hold||contrast.hold;}
'''+helper.replace('void gauge_legacy','constexpr void gauge_legacy')+'''
};
constexpr bool safeOriginal(){
 Probe p;if(!p.held())return false;
 p.gauge_legacy_cache_init_locked();if(p.held())return false;
 p.gauge_legacy_cache_init_locked();
 return p.old.reads==1&&p.factory.reads==1&&p.contrast.reads==1;
}
constexpr bool realPending(){
 Probe p;p.factory.pending=true;p.gauge_legacy_cache_init_locked();
 return p.held()&&p.factory.hold&&!p.old.hold&&!p.contrast.hold;
}
static_assert(safeOriginal());static_assert(realPending());
'''
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:self.skipTest('cross compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'cache.cpp';p.write_text(code,encoding='utf8')
            q=subprocess.run([str(compilers[-1]),'-std=c++20','-fsyntax-only',str(p)],capture_output=True,text=True)
            self.assertEqual(q.returncode,0,q.stderr)

    def test_boot_and_poll_cannot_short_circuit_initialization(self):
        source=HAL.read_text(encoding='utf8')
        boot=source.split('Hal::GaugeBootReloadStatus Hal::gaugeBootReload(',1)[1].split('Hal::MosaicoClockDiagnostics',1)[0]
        self.assertLess(boot.index('gauge_legacy_cache_init_locked();'),boot.index('if(!lease.held)'))
        self.assertLess(boot.index('if (gaugeScaleTrialActive())'),boot.index('gauge_official_apply_locked(capacity,details,sizeof(details),capacity==130)'))
        self.assertIn('boot_official_active_or_unknown_experiment_no_write',boot)
        poll=POLL.read_text(encoding='utf8').split('void Hal::gaugeScalePoll()',1)[1]
        self.assertLess(poll.index('gauge_legacy_cache_init_locked();'),poll.index('contrast_poll_locked()'))
        self.assertLess(poll.index('gauge_legacy_cache_init_locked();'),poll.index('!scale_runtime'))

if __name__=='__main__':unittest.main()
