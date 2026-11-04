"""Compile the actual terminal-return method and awake header geometry."""
from pathlib import Path
import subprocess
import tempfile
import unittest

R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'

class TerminalReturnTests(unittest.TestCase):
    def test_actual_terminal_return_and_header_alignment(self):
        text=(V/'view_mosaico.cpp').read_text(encoding='utf8')
        method=text.split('void CodexMicroView::serviceOtaTerminalReturn(',1)[1].split('void CodexMicroView::renderOta()',1)[0]
        method='constexpr void CodexMicroView::serviceOtaTerminalReturn('+method
        self.assertIn('MosaicoOta::busy()',method)
        method=method.replace('MosaicoOta::busy()','backendBusy')
        code=r'''
#include "ota_presentation_model.h"
#include "lock_session_geometry.h"
struct CodexMicroView {
 enum class Page {Command,History,OTA,Sessions};
 Page _page=Page::OTA;
 bool _locked=false,_suppressed=false,_rotationFault=false,_settingsOpen=false,_slideTo=false;
 bool _otaTerminalArmed=false,busy=false,backendBusy=false,blockPage=false;
 uint32_t _otaTerminalSince=0;
 MosaicoOta::UiStage _otaTerminalStage=MosaicoOta::UiStage::Idle;
 MosaicoOta::UiSnapshot _ota{};
 unsigned navigations=0;
 constexpr bool otaBusy() const {return busy;}
 constexpr bool setPageForDebug(Page p) {if(blockPage)return false;_page=p;++navigations;return true;}
 constexpr void serviceOtaTerminalReturn(uint32_t tick,bool interacting);
};
'''+method+r'''
constexpr bool cases() {
 using S=MosaicoOta::UiStage;using P=CodexMicroView::Page;
 for(auto stage:{S::Complete,S::Failed}) {
  CodexMicroView v;v._ota.stage=stage;
  v.serviceOtaTerminalReturn(100,false);v.serviceOtaTerminalReturn(15099,false);
  if(v.navigations)return false;
  v.serviceOtaTerminalReturn(15100,false);if(v._page!=P::Command||v.navigations!=1)return false;
  v.serviceOtaTerminalReturn(999999,false);if(v.navigations!=1)return false;
  v._page=P::OTA;v.serviceOtaTerminalReturn(1000000,false);
  if(v.navigations!=1)return false; // Reentry gets a new reading window.
 }
 for(auto stage:{S::Idle,S::Available,S::Checking,S::Downloading,S::Verifying,S::ReadyInstall,S::Installing,S::ReadyReboot,S::BootChecking}) {
  CodexMicroView v;v._ota.stage=stage;v.serviceOtaTerminalReturn(1,false);v.serviceOtaTerminalReturn(999999,false);
  if(v.navigations||v._otaTerminalArmed)return false;
 }
 CodexMicroView v;v._ota.stage=S::Failed;v.serviceOtaTerminalReturn(100,false);
 v.serviceOtaTerminalReturn(10000,true);v.serviceOtaTerminalReturn(20000,false);
 if(v.navigations)return false;
 v._settingsOpen=true;v.serviceOtaTerminalReturn(100000,false);if(v._otaTerminalArmed)return false;
 v._settingsOpen=false;v.serviceOtaTerminalReturn(100001,false);if(v.navigations)return false;
 v.busy=true;v.serviceOtaTerminalReturn(200000,false);if(v._otaTerminalArmed)return false;
 v.busy=false;v._ota.stage=S::Complete;v.serviceOtaTerminalReturn(200001,false);
 v.backendBusy=true;v.serviceOtaTerminalReturn(214998,false);if(v._otaTerminalArmed||v.navigations)return false;
 v.backendBusy=false;v.serviceOtaTerminalReturn(200001,false);
 v._ota.stage=S::Failed;v.serviceOtaTerminalReturn(214999,false);if(v.navigations)return false;
 v.serviceOtaTerminalReturn(229999,false);if(v.navigations!=1)return false;
 CodexMicroView wrap;wrap._ota.stage=S::Complete;wrap.serviceOtaTerminalReturn(UINT32_MAX-1000,false);
 wrap.serviceOtaTerminalReturn(13999,false);if(wrap.navigations!=1)return false;
 for(int width=1;width<=200;++width)for(int height:{16,20,24,28}) {
  const auto row=mosaico_lock_sessions::awakeBatteryRow(width,height);
  if(row.iconX+32!=460||row.textX+row.textWidth+8!=row.iconX||row.boltX+24+6!=row.textX||row.boltX<268)return false;
  if(row.textY<20||row.textY+height>52)return false;
 }
 return true;
}
static_assert(cases(),"Only idle OTA success/failure return after15s; preserve active/install/read interactions and fixed header anchor");
'''
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        self.assertTrue(compilers)
        with tempfile.TemporaryDirectory(dir=R/'.artifacts') as d:
            p=Path(d)/'terminal.cpp';p.write_text(code,encoding='utf8')
            result=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only','-I'+str(V),'-I'+str(R/'main'),str(p)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
