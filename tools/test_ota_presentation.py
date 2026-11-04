"""Execute the production phase/layout model; no device or screenshot claims."""
from pathlib import Path
import subprocess
import tempfile
import unittest

R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'

class OtaPresentationTests(unittest.TestCase):
    def test_actual_model_progress_visibility_and_all_rectangles(self):
        code=r'''
#include "ota_presentation_model.h"
#include <initializer_list>
using namespace mosaico_ota_view;
constexpr void text(char* out,const char* in) {while(*in)*out++=*in++;*out=0;}
constexpr MosaicoOta::UiSnapshot sample() {
 MosaicoOta::UiSnapshot s{};
 text(s.currentVersion,"0.13.95-r62-mosaico-ota");text(s.targetVersion,"0.13.95-r63-mosaico-ota");
 text(s.sha256,"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
 text(s.signatureShort,"bbbbbbbbbbbbbbbb");s.currentSlot=0;s.targetSlot=1;s.size=3852320;
 s.received=1926160;s.transferSize=60296;s.transferReceived=30148;
 s.deltaTransfer=true;s.modeKnown=true;s.signatureVerified=true;return s;
}
constexpr bool fits(Model m) {
 Rect visible[20]{};unsigned n=0;
 visible[n++]=m.phase;visible[n++]=m.action;
 if(m.title)visible[n++]={0,0,170,56};
 if(m.mode)visible[n++]=m.badge;
 if(m.current)visible[n++]=m.currentBox;
 if(m.target)visible[n++]=m.targetBox;
 if(m.loader||m.screen==Screen::Ready||m.screen==Screen::Reboot||m.screen==Screen::Complete)visible[n++]=m.activity;
 if(m.numeric){visible[n++]=m.percent;visible[n++]=m.meter;}
 if(m.bytes)visible[n++]=m.byteBox;
 if(m.hash){visible[n++]=m.hashBox;visible[n++]=m.imageIcon;}
 if(m.signature){visible[n++]=m.signatureBox;visible[n++]=m.signatureIcon;}
 for(unsigned i=0;i<m.slots;++i)visible[n++]=m.slot[i];
 if(m.slots==2)visible[n++]=m.arrow;
 for(unsigned i=0;i<n;++i) {
  if(!bounds(visible[i]))return false;
  for(unsigned j=i+1;j<n;++j)if(overlaps(visible[i],visible[j]))return false;
 }
 return true;
}
constexpr bool all() {
 for(int stage=0;stage<=int(Stage::Failed);++stage)
  for(int work=0;work<=int(Work::Verifying);++work)
   for(int delta=0;delta<2;++delta)for(int knownMode=0;knownMode<2;++knownMode)
    for(int slot=-1;slot<=1;++slot) {
     auto s=sample();s.stage=Stage(stage);s.workPhase=Work(work);s.deltaTransfer=delta;s.modeKnown=knownMode;s.currentSlot=slot;
     if(!fits(present(s)))return false;
    }
 auto s=sample();s.stage=Stage::Downloading;
 for(auto w:{Work::Preparing,Work::Negotiating}) {
  s.workPhase=w;auto m=present(s);
  if(m.numeric||!m.loader||m.mode||m.hash||m.signature)return false;
 }
 s.signatureVerified=false;auto unbound=present(s);
 if(unbound.target||!unbound.current||unbound.mode)return false;
 s.signatureVerified=true;
 s.workPhase=Work::Downloading;auto down=present(s);
 if(!down.numeric||down.loader||down.bp!=5000||down.done!=30148||down.total!=60296||!down.mode)return false;
 s.stage=Stage::Verifying;s.workPhase=Work::Merging;s.transferReceived=s.transferSize;
 auto merge=present(s);if(merge.bp!=5000||merge.done!=1926160||merge.total!=3852320||!merge.numeric||merge.loader)return false;
 s.workPhase=Work::Verifying;auto verify=present(s);if(verify.numeric||!verify.loader||!verify.hash||verify.signature)return false;
 s.stage=Stage::ReadyInstall;s.workPhase=Work::None;s.imageVerified=true;
 auto ready=present(s);if(!ready.hash||!ready.signature||ready.loader||ready.numeric||!ready.mode)return false;
 s.stage=Stage::Downloading;s.workPhase=Work::Downloading;s.deltaTransfer=false;s.received=3852320;
 auto full=present(s);if(full.total!=3852320||full.bp!=10000||!full.mode)return false;
 s.stage=Stage::Complete;s.modeKnown=false;s.currentSlot=s.targetSlot=1;
 auto done=present(s);if(done.mode||done.slots!=1||done.current||!done.targetIsCurrent)return false;
 for(int current=-1;current<=1;++current)for(int target=-1;target<=1;++target) {
  s=sample();s.stage=Stage::Downloading;s.workPhase=Work::Downloading;s.currentSlot=current;s.targetSlot=target;
  const unsigned expected=current<0?0U:target>=0&&target!=current?2U:1U;
  if(present(s).slots!=expected)return false;
 }
 s=sample();s.stage=Stage::Idle;auto idle=present(s);
 if(idle.target||idle.mode||idle.hash||idle.signature||idle.numeric||idle.slots!=1)return false;
 s.stage=Stage::WaitingPower;
 if(present(s).screen!=Screen::Offer)return false;
 s.imageVerified=true;if(present(s).screen!=Screen::Ready)return false;
 s.imageVerified=false;s.signatureVerified=false;if(present(s).screen!=Screen::Current)return false;
 MosaicoOta::UiSnapshot unseen{};if(present(unseen).slots)return false;
 return fraction(UINT32_MAX,UINT32_MAX)==10000 && fraction(1,0)==0;
}
static_assert(all(),"Every visible OTA component must fit, avoid overlap and reflect its real stage metric");
'''
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        self.assertTrue(compilers)
        with tempfile.TemporaryDirectory(dir=R/'.artifacts') as directory:
            src=Path(directory)/'ota_layout.cpp';src.write_text(code,encoding='utf8')
            p=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only','-fconstexpr-ops-limit=100000000',
                              '-I'+str(V),'-I'+str(R/'main'),str(src)],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)

    def test_view_consumes_model_without_altering_install_controls(self):
        cpp=(V/'view_mosaico.cpp').read_text(encoding='utf8')
        body=cpp.split('void CodexMicroView::renderOta() {',1)[1].split('void CodexMicroView::touchEvent(',1)[0]
        self.assertIn('const auto view = present(_ota);',body)
        self.assertIn('view.loader?Icon::Refresh:Icon::Check',body)
        self.assertIn('_ota.deltaTransfer?"DELTA":"FULL"',body)
        self.assertIn('show(_otaModeBadge,view.mode)',body)
        self.assertIn('box(_otaButton,view.action)',body)
        self.assertIn('renderOtaAction();',body)
        self.assertNotIn('approveInstall(',body)
        self.assertIn('% 1600U) * 360U / 1600U',cpp)

if __name__=='__main__':unittest.main()
