"""Offline actual-callback regression harness; not physical touch acceptance."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CPP = (ROOT / 'main/apps/app_codex_micro/view/view_mosaico.cpp').read_text(encoding='utf8')


class WifiKeyboardTests(unittest.TestCase):
    def test_fixed_touch_geometry_and_event_wiring(self):
        init = CPP.split('void CodexMicroView::initSettings()', 1)[1].split('void CodexMicroView::settingsDetailExec', 1)[0]
        fields = init.split('for (auto* field :', 1)[1].split('lv_textarea_set_max_length', 1)[0]
        self.assertIn('lv_obj_set_height(field, 48)', fields)
        self.assertLess(fields.index('lv_textarea_set_one_line'), fields.index('lv_obj_set_height(field, 48)'))
        self.assertIn('lv_obj_remove_flag(field, LV_OBJ_FLAG_SCROLL_ON_FOCUS)', fields)
        self.assertIn('wifiFieldEvent, LV_EVENT_CLICKED', fields)
        self.assertIn('_wifiKeyboard = lv_keyboard_create(_settingsPage)', init)
        self.assertIn('panel(_wifiKeyboard, 0, 204, 440, 198)', init)
        self.assertLessEqual(64 + 402 + 2, 480)
        self.assertLessEqual(56 + 70 + 48, 204)
        self.assertIn('lv_textarea_set_password_show_time(_wifiPassword, 0)', init)
        close = CPP.split('void CodexMicroView::closeSettings(', 1)[1].split('void CodexMicroView::showSettingsDetail', 1)[0]
        self.assertIn('lv_keyboard_set_textarea(_wifiKeyboard, nullptr)', close)
        self.assertIn('lv_textarea_set_text(_wifiPassword, "")', close)
        self.assertNotIn('lv_event_stop_', CPP)

    def test_actual_touch_and_field_callbacks(self):
        compilers = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:
            self.skipTest('embedded compiler unavailable')
        methods = []
        for name in ('wifiFieldEvent', 'touchEvent'):
            start = CPP.index('void CodexMicroView::' + name + '(')
            methods.append('constexpr ' + CPP[start:CPP.index('\n}', start) + 2])
        harness = r'''
#include <cstddef>
#include <cstdlib>
#include <initializer_list>
using std::size_t;
enum { LV_EVENT_PRESSED, LV_EVENT_PRESSING, LV_EVENT_RELEASED, LV_EVENT_PRESS_LOST,
       LV_EVENT_CLICKED, LV_EVENT_VALUE_CHANGED, LV_EVENT_READY, LV_EVENT_CANCEL };
enum lv_obj_flag_t { LV_OBJ_FLAG_HIDDEN=1 };
enum { LV_INDEV_TYPE_POINTER };
struct lv_point_t { int x=0,y=0; };
struct lv_indev_t { lv_point_t point; };
struct lv_obj_t { lv_obj_t* parent=nullptr; int flags=0,x=0,y=0; bool empty=false; lv_obj_t* editor=nullptr; };
struct CodexMicroView;
struct lv_event_t { CodexMicroView* owner; int code; lv_obj_t* target; lv_obj_t* current; lv_indev_t* input; };
constexpr auto lv_event_get_user_data(lv_event_t* e) { return e->owner; }
constexpr auto lv_event_get_code(lv_event_t* e) { return e->code; }
constexpr auto lv_event_get_target(lv_event_t* e) { return e->target; }
constexpr auto lv_event_get_current_target(lv_event_t* e) { return e->current; }
constexpr auto lv_event_get_indev(lv_event_t* e) { return e->input; }
constexpr int lv_indev_get_type(lv_indev_t*) { return LV_INDEV_TYPE_POINTER; }
constexpr void lv_indev_get_point(lv_indev_t* i,lv_point_t* p) { *p=i->point; }
constexpr auto lv_obj_get_parent(lv_obj_t* o) { return o->parent; }
constexpr bool lv_obj_has_flag(lv_obj_t* o,lv_obj_flag_t f) { return o->flags&f; }
constexpr void lv_obj_add_flag(lv_obj_t* o,lv_obj_flag_t f) { o->flags|=f; }
constexpr void lv_obj_remove_flag(lv_obj_t* o,lv_obj_flag_t f) { o->flags&=~f; }
constexpr void lv_keyboard_set_textarea(lv_obj_t* k,lv_obj_t* e) { k->editor=e; }
constexpr void lv_textarea_set_text(lv_obj_t* o,const char*) { o->empty=true; }
constexpr void lv_obj_move_foreground(lv_obj_t*) {}
constexpr void place(lv_obj_t* o,int x,int y) { o->x=x;o->y=y; }
constexpr unsigned lv_tick_get() { return 10; }
struct CodexMicroView {
 enum class RotationPhase { Idle,FadeOut };
 RotationPhase _rotationPhase=RotationPhase::Idle;
 bool _settingsOpen=true,_settingsAnimating=false,_locked=false,_suppressed=false,_rotationFault=false,busy=false;
 bool _touchTracking=false,_swipeConsumed=true,_touchOnEditor=false;
 unsigned _settingsDetail=4,_activity=0;
 lv_point_t _touchStart;
 lv_obj_t page{},ssid{&page},password{&page},keyboard{&page,1},child{&ssid},rows[8]{};
 lv_obj_t* _wifiSsid=&ssid; lv_obj_t* _wifiPassword=&password; lv_obj_t* _wifiKeyboard=&keyboard;
 lv_obj_t* _settingsPage=&page; lv_obj_t* _slideTo=nullptr;
 lv_obj_t* _settingsRows[8]={&rows[0],&rows[1],&rows[2],&rows[3],&rows[4],&rows[5],&rows[6],&rows[7]};
 int navigation=0,closed=0;
 constexpr bool otaBusy() { return busy; }
 constexpr void openSettings() { _settingsOpen=true; }
 constexpr void closeSettings() { ++closed; }
 constexpr void navigatePage(int) { ++navigation; }
 static constexpr void wifiFieldEvent(lv_event_t*);
 static constexpr void touchEvent(lv_event_t*);
};
'''
        harness += '\n'.join(methods)
        harness += r'''
constexpr bool exercise() {
 CodexMicroView v; lv_indev_t input{{200,280}};
 lv_event_t e{&v,LV_EVENT_PRESSED,&v.child,&v.page,&input};
 v.touchEvent(&e);
 if(!v._touchOnEditor || v._swipeConsumed) return false;
 e.code=LV_EVENT_RELEASED;v.touchEvent(&e);
 e.code=LV_EVENT_CLICKED;e.current=&v.ssid;v.wifiFieldEvent(&e);
 if(v.keyboard.editor!=&v.ssid || v.keyboard.flags || v.ssid.y!=70 || !(v.password.flags&1))return false;
 e.code=LV_EVENT_READY;e.current=&v.keyboard;v.wifiFieldEvent(&e);
 if(v.keyboard.editor || !(v.keyboard.flags&1) || v.ssid.y!=142 || v.password.y!=198 || v.password.flags)return false;
 // First-contact drags on either textarea must not navigate or close settings.
 for(auto* field:{&v.ssid,&v.password}) {
  input.point={200,280};e.target=field;e.current=&v.page;e.code=LV_EVENT_PRESSED;v.touchEvent(&e);
  input.point={200,400};e.code=LV_EVENT_PRESSING;v.touchEvent(&e);
  e.code=LV_EVENT_PRESS_LOST;v.touchEvent(&e);
  if(v.navigation || v.closed || v._touchTracking || !v._swipeConsumed)return false;
  e.code=LV_EVENT_CLICKED;e.current=field;v.wifiFieldEvent(&e);
  if(v.keyboard.editor)return false;
 }
 // A new contact resets the consumed drag and opens the password editor.
 input.point={200,340};e.code=LV_EVENT_PRESSED;e.target=&v.password;e.current=&v.page;v.touchEvent(&e);
 e.code=LV_EVENT_RELEASED;v.touchEvent(&e);
 e.code=LV_EVENT_CLICKED;e.current=&v.password;v.wifiFieldEvent(&e);
 if(v.keyboard.editor!=&v.password)return false;
 e.code=LV_EVENT_CANCEL;e.current=&v.keyboard;v.wifiFieldEvent(&e);
 if(!v.password.empty || v.keyboard.editor || !(v.keyboard.flags&1))return false;
 // Stale clicks cannot activate an editor during rotation or input suppression.
 e.code=LV_EVENT_CLICKED;e.current=&v.password;
 for(int guard=0;guard<5;++guard) {
  v._locked=guard==0;v._suppressed=guard==1;v.busy=guard==2;
  v._settingsAnimating=guard==3;v._rotationPhase=guard==4?CodexMicroView::RotationPhase::FadeOut:CodexMicroView::RotationPhase::Idle;
  v.wifiFieldEvent(&e);
  v._touchTracking=true;e.code=LV_EVENT_PRESS_LOST;v.touchEvent(&e);e.code=LV_EVENT_CLICKED;
  if(v._touchTracking || !v._swipeConsumed)return false;
  if(v.keyboard.editor)return false;
 }
 return true;
}
static_assert(exercise(),"actual editor callbacks preserve contact boundaries and keyboard lifecycle");
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'keyboard.cpp'
            source.write_text(harness, encoding='utf8')
            result = subprocess.run([str(compilers[-1]), '-std=c++17', '-fsyntax-only', str(source)], capture_output=True, text=True)
            log = ROOT / '.artifacts/mosaico/wifi-keyboard-source-harness.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(result.stdout + result.stderr, encoding='utf8')
            self.assertEqual(result.returncode, 0, str(log))


if __name__ == '__main__':
    unittest.main()
