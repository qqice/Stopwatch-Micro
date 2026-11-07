"""Offline production dependency keys and rendering integration guards."""
from pathlib import Path
import subprocess, unittest
R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'
RUN=R/'.artifacts/private/mosaico/view-update-efficiency-tests'
class ViewUpdateTests(unittest.TestCase):
 def test_production_dependency_keys(self):
  RUN.mkdir(parents=True,exist_ok=True)
  code=r'''#include "view_update_model.h"
using namespace mosaico_view_update;
constexpr bool test() {
 MosaicoOta::UiSnapshot a{},b{};
 a.currentSlot=b.currentSlot=0;a.targetSlot=b.targetSlot=1;
 for(unsigned i=0;i<100;++i) {
  b.revision=i;b.received=i;b.transferReceived=i;b.progressBasisPoints=i;b.size=i;b.transferSize=i;
  if(!sameOtaStatic(a,b))return false;
 }
 b=a;b.stage=MosaicoOta::UiStage::WaitingPower;if(sameOtaStatic(a,b))return false;
 b=a;b.workPhase=MosaicoOta::WorkPhase::Preparing;if(sameOtaStatic(a,b))return false;
 b=a;b.currentSlot=1;if(sameOtaStatic(a,b))return false;
 b=a;b.targetSlot=0;if(sameOtaStatic(a,b))return false;
 b=a;b.signatureVerified=true;if(sameOtaStatic(a,b))return false;
 b=a;b.imageVerified=true;if(sameOtaStatic(a,b))return false;
 b=a;b.modeKnown=true;if(sameOtaStatic(a,b))return false;
 b=a;b.deltaTransfer=true;if(sameOtaStatic(a,b))return false;
 b=a;b.currentVersion[0]='X';if(sameOtaStatic(a,b))return false;
 b=a;b.targetVersion[0]='X';if(sameOtaStatic(a,b))return false;
 b=a;b.sha256[0]='X';if(sameOtaStatic(a,b))return false;
 b=a;b.signatureShort[0]='X';if(sameOtaStatic(a,b))return false;
 b=a;b.error[0]='X';if(sameOtaStatic(a,b))return false;
 for(int stage=0;stage<=int(MosaicoOta::UiStage::Failed);++stage) {
  a.stage=MosaicoOta::UiStage(stage);b=a;
  if(!sameOtaStatic(a,b))return false;
 }
 if(quotaBodyCovered(false,false)||quotaBodyCovered(true,true)||quotaBodyCovered(false,true)||!quotaBodyCovered(true,false))return false;
 TrendKey k{100,2,4};
 if(!(k==TrendKey{100,2,4}) || k==TrendKey{101,2,4} || k==TrendKey{100,3,4} || k==TrendKey{100,2,5})return false;
 return true;
}
static_assert(test());
'''
  src=RUN/'keys.cpp';src.write_text(code)
  cxx=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))[-1]
  cmd=[str(cxx),'-std=c++17','-fsyntax-only','-I'+str(V),'-I'+str(R/'main'),str(src)]
  p=subprocess.run(cmd,capture_output=True,text=True)
  (RUN/'keys-compile.log').write_text(' '.join(cmd)+'\n'+p.stdout+p.stderr)
  self.assertEqual(p.returncode,0,p.stdout+p.stderr)
 def test_actual_render_integration(self):
  s=(V/'view_mosaico.cpp').read_text(encoding='utf8')
  body=s.split('void CodexMicroView::renderOta() {',1)[1].split('void CodexMicroView::touchEvent',1)[0]
  static=body.split('if (staticChanged) {',1)[1].split('_otaStaticSeen = true;',1)[0]
  for op in ['version(_otaCurrent','version(_otaTarget','lv_label_set_text(_otaHash','lv_label_set_text(_otaSignature','lv_label_set_text(_otaSlotNames','box(_otaButton','box(_otaPercent','box(_otaBytes']:
   self.assertIn(op,static)
  dynamic=body.split('_otaStaticSeen = true;',1)[1]
  self.assertIn('if(view.numeric)',dynamic);self.assertIn('if(view.bytes)',dynamic)
  self.assertIn('std::strcmp(lv_label_get_text(_otaBytes), text)',dynamic)
  self.assertIn('renderOtaAction();',dynamic)
  anim=s.split('void CodexMicroView::updateAnimations',1)[1].split('void CodexMicroView::refreshQuota',1)[0]
  self.assertIn('active = quotaActive && _quota->resetCreditsKnown',anim)
  self.assertIn('visible = quotaBodyActive',anim)
  self.assertIn('quotaBodyCovered(_settingsOpen, _settingsAnimating)',anim)
  select=s.split('void CodexMicroView::selectTrendAt',1)[1].split('void CodexMicroView::renderTrendHint',1)[0]
  self.assertLess(select.index('_trendRenderedKey == key'),select.index('renderTrendHint();'))
  for key in ['_historyRevision','trend.available','age > 600','_hourly','getDisplayOrientation()']:
   self.assertIn(key,select)
if __name__=='__main__':unittest.main(verbosity=2)
