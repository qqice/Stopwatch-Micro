"""Bounded settings UI geometry and actual-source policy tests, no hardware claims."""
import re, subprocess, tempfile, unittest
from pathlib import Path

R = Path(__file__).resolve().parents[1]
V = R / 'main/apps/app_codex_micro/view'
CPP = (V / 'view_mosaico.cpp').read_text(encoding='utf8')
HDR = (V / 'view_mosaico.h').read_text(encoding='utf8')

class SettingsUiTests(unittest.TestCase):
    def test_default_config_is_not_permanently_pending(self):
        render = CPP.split('void CodexMicroView::renderSettings()', 1)[1].split('void CodexMicroView::settingsEvent', 1)[0]
        self.assertIn('const bool pending = _displaySettings.pending;', render)
        self.assertIn('_displaySettings.savedRevision == _displaySettings.revision ? Green : Gray', render)
        self.assertIn('"NEVER"', render)

    def test_geometry_and_async_boundary(self):
        self.assertIn('Page::Command, Page::History, Page::OTA, Page::Sessions', CPP)
        self.assertIn('(index + (direction > 0 ? 1 : 3)) % 4', CPP)
        self.assertIn('if (page == Page::Settings) { openSettings(); return _settingsOpen; }', CPP)
        self.assertNotIn('_page = Page::Settings', CPP)
        # Exact source-driven tile/detail geometry, including burn-in offsets.
        for i in range(4):
            x,y,w,h=12+(i%2)*218,64+56+8+(i//2)*166,206,154
            self.assertLessEqual(x+w,440);self.assertLessEqual(y+h+2,480)
        for rows in (2,3,1,2):
            for i in range(rows):
                boxes=[(12,8+i*62+16,150,24),(168,8+i*62+14,112,28),
                       (292,8+i*62,64,54),(364,8+i*62,64,54)]
                for x,y,w,h in boxes:
                    self.assertLessEqual(x+w,440);self.assertLessEqual(y+h,346)
                self.assertLessEqual(12+150,168);self.assertLessEqual(168+112,292)
        for x,y,w,h in ((12,142,416,48),(12,198,416,48),(12,256,128,48),
                        (156,256,128,48),(300,256,128,48),(12,314,416,24)):
            self.assertLessEqual(x+w,440);self.assertLessEqual(y+h,346)
        self.assertLessEqual(56+70+48,204) # Active editor does not overlap keyboard.
        self.assertEqual(204+198,402)
        for token in ('lv_textarea_set_password_mode(_wifiPassword, true)',
                      'lv_textarea_set_password_show_time(_wifiPassword, 0)',
                      'lv_textarea_set_max_length(_wifiSsid, 32)',
                      'lv_textarea_set_max_length(_wifiPassword, 64)',
                      '!self->_wifiOpenNetwork && !password[0]',
                      'std::strlen(ssid) > 32', 'std::strlen(password) > 64',
                      'requestWifiCredentials(ssid, self->_wifiOpenNetwork ? "" : password)',
                      'lv_textarea_set_text(_wifiPassword, "")', 'SAVE ERROR'):
            self.assertIn(token,CPP)
        callback=CPP.split('void CodexMicroView::settingsEvent(',1)[1].split('void CodexMicroView::refreshDisplaySettings()',1)[0]
        for forbidden in ('nvs_', 'setBackLightBrightness', 'service()', 'batteryTelemetry', 'lv_timer_create'):
            self.assertNotIn(forbidden,callback)
        toggle=CPP.split('void CodexMicroView::togglePage()',1)[1].split('void CodexMicroView::wakeDisplay()',1)[0]
        self.assertIn('closeSettings(); return;',toggle)
        touch=CPP.split('void CodexMicroView::touchEvent(',1)[1].split('void CodexMicroView::cellEvent',1)[0]
        self.assertIn('dy < 0 && !self->_settingsOpen',touch)
        self.assertIn('dy > 0 && self->_settingsOpen',touch)
        self.assertIn('if (self->_touchOnEditor)',touch)

    def test_actual_settings_profile_and_shift_functions(self):
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:self.skipTest('embedded compiler unavailable')
        methods=[]
        for name in ('settingsEvent','refreshDisplaySettings','applyBurnInShift'):
            start=CPP.index('void CodexMicroView::'+name+'(')
            body=CPP[start:CPP.index('\n}',start)+2]
            body=body.replace('lv_event_t* event','Hit* event')
            body=body.replace('MosaicoDisplay::snapshot(next)','_snapshot(next)')
            body=body.replace('MosaicoDisplay::request(config)','self->_request(config)')
            body=body.replace('GetHAL()', '_hal').replace('tud_mounted()', '_usb')
            body=body.replace('lv_tick_get()', 'self->_now' if name=='settingsEvent' else '_now')
            body=body.replace('static constexpr', 'constexpr') # C++17 constexpr adapter: identical constant tables.
            methods.append('constexpr '+body)
        harness='#include "'+(R/'main/host/mosaico_display_settings_model.h').as_posix()+'"\n'
        harness+='#include "'+(V/'charge_supply_state.h').as_posix()+'"\n'
        harness+=r'''
#include <algorithm>
#include <cstddef>
using std::size_t;
struct lv_obj_t { int x=0,y=0; };
constexpr void lv_obj_set_pos(lv_obj_t* o,int x,int y) { o->x=x;o->y=y; }
struct Hal { int brightness=-1,calls=0; bool saved=false;
 constexpr void setBackLightBrightness(int value,bool save) { brightness=value;saved=save;++calls; }
};
struct CodexMicroView {
 enum class Page { Command,History,Agent,OTA,Settings };
 enum class RotationPhase { Idle,FadeOut,WaitBlack,WaitRotated,FadeIn };
 struct Hit { CodexMicroView* owner; size_t index; };
 Page _page=Page::Settings; RotationPhase _rotationPhase=RotationPhase::Idle;
 bool _rotationFault=false,_locked=false,_suppressed=false,_swipeConsumed=false,busy=false;
 bool _settingsRequestFailed=false,_chargeProfile=false,_profileSeen=false,_settingsOpen=true,_settingsAnimating=false;
 mosaico_charge::ChargeSupplyState _chargeSupply{};
 bool _shiftPending=false,_touchTracking=false,_usb=false,snapshotReady=true,accept=true;
 unsigned _activity=0,_now=123,_shiftIndex=0;
 int _brightness=80,_appliedBrightness=-1,renders=0,requests=0;
 MosaicoDisplay::Snapshot _displaySettings{},bank{};
 Hal _hal; lv_obj_t root{};lv_obj_t* _root=&root;lv_obj_t* _slideTo=nullptr;lv_obj_t* _settingsPage=&root;
 constexpr bool otaBusy() { return busy; }
 constexpr void renderSettings() { ++renders; }
 constexpr bool _snapshot(MosaicoDisplay::Snapshot& out) { if(!snapshotReady)return false;out=bank;return true; }
 constexpr bool _request(MosaicoDisplay::Config c) {
  ++requests;if(!accept)return false;
  bank.config=MosaicoDisplay::sanitize(c);++bank.revision;bank.pending=true;return true;
 }
 static constexpr void settingsEvent(Hit*);
 constexpr void refreshDisplaySettings();
 constexpr void applyBurnInShift(bool);
};
constexpr CodexMicroView::Hit* lv_event_get_user_data(CodexMicroView::Hit* e) { return e; }
'''+ '\n'.join(methods)+r'''
constexpr bool controls() {
 CodexMicroView v;v.refreshDisplaySettings();
 for(int row=0;row<8;++row) {
  CodexMicroView::Hit h{&v,size_t(row*2+1)};
  for(int repeat=0;repeat<25;++repeat) {
   v.settingsEvent(&h);
   auto c=v._displaySettings.config;
   if(c.batteryTimeoutSeconds>60 || !c.batteryTimeoutSeconds || c.chargeBrightness<10 || c.chargeBrightness>100 ||
      c.batteryBrightness<10 || c.batteryBrightness>100 || c.lockBrightness>100 || c.lockWifiMinutes<1 || c.lockWifiMinutes>60 || c.lockBleMinutes<1 || c.lockBleMinutes>60)return false;
  }
  h.index=row*2;for(int repeat=0;repeat<25;++repeat)v.settingsEvent(&h);
 }
 if(v._displaySettings.config.burnIn || v._displaySettings.config.lockBrightness!=0)return false;
 CodexMicroView::Hit h{&v,5};
 for(int guard=0;guard<7;++guard) {
  v._locked=guard==0;v._suppressed=guard==1;v._swipeConsumed=guard==2;
  v._slideTo=guard==3?&v.root:nullptr;v.busy=guard==4;v._rotationFault=guard==5;
  v._rotationPhase=guard==6?CodexMicroView::RotationPhase::FadeOut:CodexMicroView::RotationPhase::Idle;
  int before=v.requests;v.settingsEvent(&h);if(before!=v.requests)return false;
 }
 return !v._hal.saved;
}
constexpr bool profiles() {
 CodexMicroView v;v.refreshDisplaySettings();
 if(v._hal.brightness!=80 || v._activity!=123)return false;
 v._now=124;v.refreshDisplaySettings();if(v._activity!=123 || v._hal.calls!=1)return false;
 v._usb=true;v.bank.config.chargeBrightness=95;++v.bank.revision;v.refreshDisplaySettings();
 if(v._hal.brightness!=80 || v._activity!=123 || v._chargeProfile)return false; // USB is not charge-profile evidence.
 v._chargeSupply.update(true,4,99);v.refreshDisplaySettings();
 if(v._hal.brightness!=95 || v._activity!=124 || !v._chargeProfile)return false;
 v._now=125;v.refreshDisplaySettings();if(v._activity!=124)return false;
 v._usb=false;v._chargeSupply.update(true,0,100);v.refreshDisplaySettings();if(v._activity!=124 || !v._chargeProfile)return false;
 v._chargeSupply.update(true,-4,100);v.bank.config.batteryBrightness=35;++v.bank.revision;v.refreshDisplaySettings();
 if(v._hal.brightness!=35 || v._activity!=125 || v._chargeProfile)return false;
 v._locked=true;v.bank.config.lockBrightness=0;++v.bank.revision;v.refreshDisplaySettings();
 if(v._hal.brightness!=0)return false;
 v._locked=false;v.refreshDisplaySettings();if(v._hal.brightness!=35)return false;
 v.bank.config.batteryTimeoutSeconds=999;v.refreshDisplaySettings();if(v._displaySettings.config.batteryTimeoutSeconds!=60)return false;
 v.snapshotReady=false;v.bank.config.batteryBrightness=99;v.refreshDisplaySettings();
 return v._hal.brightness==35 && !v._hal.saved;
}
constexpr bool shift() {
 CodexMicroView v;bool centre=false;
 for(int step=0;step<9;++step) {
  v._shiftPending=true;v.applyBurnInShift(false);
  if(v.root.x < -2 || v.root.x>2 || v.root.y < -2 || v.root.y>2 || v._shiftPending)return false;
  centre=centre || (!v.root.x && !v.root.y);
 }
 if(!centre)return false;
 for(int guard=0;guard<7;++guard) {
  v._touchTracking=guard==1;v._slideTo=guard==2?&v.root:nullptr;
  v._rotationPhase=guard==3?CodexMicroView::RotationPhase::FadeOut:CodexMicroView::RotationPhase::Idle;
  v._rotationFault=guard==4;v._suppressed=guard==5;v.busy=guard==6;
  v._shiftPending=true;v.applyBurnInShift(guard==0);if(!v._shiftPending)return false;
 }
 v._touchTracking=false;v._slideTo=nullptr;v._rotationPhase=CodexMicroView::RotationPhase::Idle;
 v._rotationFault=false;v._suppressed=false;v.busy=false;v._locked=true;
 v._displaySettings.config.burnIn=false;v.applyBurnInShift(false);
 return !v.root.x && !v.root.y && !v._shiftPending;
}
static_assert(controls(),"eight controls stay bounded and reject suppressed input");
static_assert(profiles(),"profile changes reset activity once, live brightness and contention are safe");
static_assert(shift(),"bounded scan defers unsafe movement and disabled shift returns to origin");
'''
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'settings.cpp';source.write_text(harness,encoding='utf8')
            result=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
            log=R/'.artifacts/mosaico/settings-ui-source-harness.log';log.write_text(result.stdout+result.stderr,encoding='utf8')
            self.assertEqual(result.returncode,0,'source harness failed: '+str(log)+'\n'+result.stderr)

if __name__=='__main__':unittest.main()
