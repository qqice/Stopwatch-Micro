"""Actual-source OTA awake/lock guards; no device or OTA safety changes."""
import re, subprocess, tempfile, unittest
from pathlib import Path

R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'
CPP=(V/'view_mosaico.cpp').read_text(encoding='utf8')
OTA=(R/'main/ota/mosaico_ota.h').read_text(encoding='utf8')

class OtaAwakeUiTests(unittest.TestCase):
    def test_actual_idle_lock_and_page_entry_functions(self):
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:self.skipTest('embedded compiler unavailable')
        methods=[]
        for name in ('otaBusy','otaKeepAwake','setPageForDebug'):
            start=CPP.index('bool CodexMicroView::'+name+'(')
            body=CPP[start:CPP.index('\n}',start)+2].replace('lv_tick_get()','_now')
            methods.append('constexpr '+body)
        update=CPP.split('void CodexMicroView::update(',1)[1]
        start=update.index('    tick = lv_tick_get();')
        end=update.index('    // Revision checks',start)
        idle=update[start:end].replace('lv_tick_get()','_now')
        start=CPP.index('void CodexMicroView::lockDisplay()')
        lock=CPP[start:CPP.index('    cancelOrientation();',start)]
        lock='constexpr '+lock+'    ++lockCount; _locked = true;\n}\n'
        stages=re.search(r'enum class UiStage[^;]+;',OTA)[0]
        code=r'''
#include <algorithm>
#include <cstdint>
namespace MosaicoOta {
'''+stages+r'''
constexpr bool busy() { return BACKEND_BUSY; }
}
enum { LV_OBJ_FLAG_HIDDEN=1 };
struct lv_obj_t { bool hidden=false; };
constexpr void lv_obj_add_flag(lv_obj_t* o,int) { o->hidden=true; }
constexpr void lv_obj_remove_flag(lv_obj_t* o,int) { o->hidden=false; }
struct CodexMicroView {
 enum class Page { Command,History,Agent,OTA,Settings,Sessions };
 struct Snapshot { MosaicoOta::UiStage stage=MosaicoOta::UiStage::Idle; } _ota;
 struct Config { uint32_t chargeTimeoutSeconds=15,batteryTimeoutSeconds=15; };
 struct Settings { Config config; } _displaySettings;
 struct Quota { bool truncated=false; } quota;
 Quota* _quota=&quota;
 Page _page=Page::Command,_otaReturn=Page::Command;
 bool _locked=false,_rotationFault=false,_otaApproved=false,_chargeProfile=false;
 bool _touchTracking=false,_swipeConsumed=false,_settingsOpen=false;
 constexpr void openSettings() { _settingsOpen=true; }
 constexpr void closeSettings(bool=true) { _settingsOpen=false; }
 uint32_t _now=100,_activity=100; int64_t _clockMinute=0;
 int lockCount=0,wakeCount=0;lv_obj_t root;
 lv_obj_t* _sessionsPage=&root;lv_obj_t* _otaPage=&root;lv_obj_t* _settingsPage=&root;lv_obj_t* _quotaPage=&root;
 lv_obj_t* _historyPage=&root;lv_obj_t* _footer=&root;lv_obj_t* _clockIcon=&root;
 lv_obj_t* _bucketCount=&root;lv_obj_t* _clockDate=&root;
 constexpr bool ready() const { return true; }
 constexpr void cancelOrientation() {}
 constexpr void cancelPageSlide() {}
 constexpr void renderOta() {}
 constexpr void renderSettings() {}
 constexpr void renderSessions() {}
 constexpr void refreshSessionsLease() {}
 constexpr void stopAnimations() {}
 constexpr void refreshHistory() {}
 constexpr void wakeDisplay() { ++wakeCount;_locked=false;_activity=_now; }
 constexpr bool otaBusy() const;
 constexpr bool otaKeepAwake() const;
 constexpr bool setPageForDebug(Page page);
 constexpr void lockDisplay();
 constexpr void compare(bool interacting=false) {
  uint32_t tick=_now-3;
'''+idle+r'''
 }
};
'''+ '\n'.join(methods)+lock+r'''
constexpr bool cases() {
 using S=MosaicoOta::UiStage;using P=CodexMicroView::Page;
 for(S stage : {S::Idle,S::Checking,S::Available,S::WaitingPower,S::Downloading,S::Verifying,
               S::ReadyInstall,S::Installing,S::ReadyReboot,S::BootChecking,S::Complete,S::Failed}) {
  for(bool charge : {false,true}) {
   CodexMicroView v;v._page=P::OTA;v._ota.stage=stage;v._chargeProfile=charge;
   v._now=1000000;v.compare();
   if(v._locked || v.lockCount || v._activity!=v._now)return false;
   v.lockDisplay();if(v._locked || v.lockCount)return false; // Debug lock denied on every OTA stage.
  }
 }
 CodexMicroView entered;entered._locked=true;
 if(!entered.setPageForDebug(P::OTA) || entered._locked || entered.wakeCount!=1)return false;
 CodexMicroView fault;fault._locked=true;fault._rotationFault=true;
 if(fault.setPageForDebug(P::OTA) || !fault._locked || fault.wakeCount)return false;
 CodexMicroView queue;queue._now=1000000;queue._ota.stage=S::Idle;
 if(MosaicoOta::busy()) {
  queue.compare();queue.lockDisplay();
  return !queue._locked && !queue.lockCount && queue._activity==queue._now;
 }
 // Local busy protects a stale non-OTA page independently of backend busy.
 CodexMicroView local;local._ota.stage=S::Downloading;local._now=1000000;local.compare();
 if(local._locked || local._activity!=local._now)return false;
 CodexMicroView latch;latch._otaApproved=true;latch._now=1000000;latch.compare();if(latch._locked)return false;
 // Leaving a terminal OTA page starts the entire configured battery countdown.
 CodexMicroView left;left._page=P::OTA;left._ota.stage=S::Complete;left._now=1000000;
 if(!left.setPageForDebug(P::Command))return false;
 left._now+=14999;left.compare();if(left._locked)return false;
 ++left._now;left.compare();if(!left._locked || left.lockCount!=1)return false;
 for(uint32_t seconds : {15U,30U,45U,60U}) {
  CodexMicroView battery;battery._page=P::Sessions;battery._displaySettings.config.batteryTimeoutSeconds=seconds;
  battery._now=battery._activity+seconds*1000-1;battery.compare();if(battery._locked)return false;
  ++battery._now;battery.compare();if(!battery._locked)return false;
 }
 CodexMicroView never;never._chargeProfile=true;never._displaySettings.config.chargeTimeoutSeconds=0;
 never._now=1000000;never.compare();if(never._locked)return false;
 CodexMicroView wrap;wrap._activity=0xfffffff0U;wrap._now=wrap._activity+14999U;
 wrap.compare();if(wrap._locked)return false;
 ++wrap._now;wrap.compare();if(!wrap._locked)return false;
 CodexMicroView future;future._activity=101;future.compare();
 return !future._locked;
}
static_assert(cases(),"all OTA pages and authoritative queue gaps stay awake; ordinary timeout, fault and wrap rules remain intact");
'''
        with tempfile.TemporaryDirectory() as directory:
            log=[]
            for authoritative in (False,True):
                source=Path(directory)/('queue.cpp' if authoritative else 'normal.cpp')
                source.write_text('#define BACKEND_BUSY '+('true' if authoritative else 'false')+'\n'+code,encoding='utf8')
                result=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
                log.append(source.name+'\n'+result.stdout+result.stderr)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            (R/'.artifacts/mosaico/ota-awake-ui-harness.log').write_text('\n'.join(log),encoding='utf8')

if __name__=='__main__':unittest.main()
