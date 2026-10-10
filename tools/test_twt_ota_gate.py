import unittest, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class Gate(unittest.TestCase):
    def test_control_predicate_does_not_use_ui_lock(self):
        s=(ROOT/'main/host/network_quota.cpp').read_text().split('bool twtOtaBlocked()',1)[1].split('#endif',1)[0]
        self.assertIn('MosaicoOta::blocksTwt()',s)
        self.assertNotIn('copyUiSnapshot',s)
    def test_actual_atomic_gate_syntax_and_states(self):
        src=(ROOT/'main/ota/mosaico_ota.cpp').read_text()
        actual=src[src.index('bool blocksTwt()'):src.index('uint32_t requestAgeMs()',src.index('bool blocksTwt()'))].strip()
        # Bound extraction to actual function, independent of adjacent declarations.
        start=actual.index('{');depth=0;end=0
        for i in range(start,len(actual)):
            depth += (actual[i]=='{')-(actual[i]=='}')
            if not depth:end=i+1;break
        actual=actual[:end].replace('bool blocksTwt()','constexpr bool blocksTwt()')
        code=r"""enum class UiStage{Idle,Available,Downloading,Verifying,ReadyInstall,WaitingPower,Installing,ReadyReboot,BootChecking,Complete,Failed};
        template<class T>struct A{T v;constexpr T load()const{return v;}};
        struct Gate{A<bool> imageReady{false},selected{false};A<UiStage> statusStage{UiStage::Complete};bool busyValue=false;
        constexpr bool busy(){return busyValue;}
        """+actual+r"""};
        constexpr bool cases(){Gate g;if(g.blocksTwt())return false;
        g.busyValue=true;if(!g.blocksTwt())return false;g.busyValue=false;
        g.imageReady.v=true;g.statusStage.v=UiStage::ReadyInstall;if(!g.blocksTwt())return false;
        g.statusStage.v=UiStage::WaitingPower;if(!g.blocksTwt())return false;
        g.statusStage.v=UiStage::Installing;if(!g.blocksTwt())return false;
        g.imageReady.v=false;g.statusStage.v=UiStage::ReadyReboot;if(!g.blocksTwt())return false;
        g.statusStage.v=UiStage::BootChecking;if(!g.blocksTwt())return false;
        g.statusStage.v=UiStage::Complete;g.selected.v=true;if(!g.blocksTwt())return false;
        g.selected.v=false;return !g.blocksTwt();}
        static_assert(cases(),"actual gate blocks OTA control states, not UI lock contention");
        """
        compiler=next(iter(Path.home().glob('.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')),None)
        if not compiler:self.skipTest('installed target syntax compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'gate.cpp';p.write_text(code)
            subprocess.run([str(compiler),'-std=c++17','-Wall','-Wextra','-Werror','-fsyntax-only',str(p)],check=True)
if __name__=='__main__':unittest.main()
