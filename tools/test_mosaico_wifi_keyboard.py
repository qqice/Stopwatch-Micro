"""Offline actual-callback regression harness; not physical touch acceptance."""
from pathlib import Path
import re
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
        self.assertIn('wifiFieldEvent, LV_EVENT_ALL', fields)
        self.assertIn('_wifiKeyboard = lv_keyboard_create(_settingsPage)', init)
        self.assertIn('panel(_wifiKeyboard, 0, 204, 440, 198)', init)
        self.assertLessEqual(64 + 402 + 2, 480)
        self.assertLessEqual(56 + 70 + 48, 204)
        self.assertIn('lv_textarea_set_password_show_time(_wifiPassword, 0)', init)
        close = CPP.split('void CodexMicroView::closeSettings(', 1)[1].split('void CodexMicroView::showSettingsDetail', 1)[0]
        self.assertIn('lv_keyboard_set_textarea(_wifiKeyboard, nullptr)', close)
        self.assertIn('lv_textarea_set_text(_wifiPassword, "")', close)
        self.assertNotIn('lv_event_stop_', CPP)
        select = CPP.split('void CodexMicroView::wifiSelectionEvent(', 1)[1].split('void CodexMicroView::wifiActionEvent', 1)[0]
        self.assertIn('wifi.names[index]', select)
        self.assertNotIn('password', select.lower().replace('_wifipassword', ''))
        self.assertIn('lv_textarea_set_text(self->_wifiPassword, "")', select)
        self.assertIn('requestWifiForget(ssid)', CPP)
        self.assertIn('wifi.count > 1', CPP)

    def test_actual_wireless_layout_and_draft_refresh(self):
        init = CPP.split('void CodexMicroView::initSettings()', 1)[1].split('void CodexMicroView::settingsDetailExec', 1)[0]
        rects = {}
        for name in ('Profiles', 'NewButton', 'ForgetButton', 'Ssid', 'Password', 'OpenButton', 'SaveButton', 'RestartButton', 'Keyboard'):
            match = re.search(r'panel\(_wifi' + name + r', (\d+), (\d+), (\d+), (\d+)\)', init)
            self.assertIsNotNone(match, name)
            rects[name] = tuple(map(int, match.groups()))
        match = re.search(r'label\(wireless, (\d+), (\d+), (\d+), "",', init)
        rects['SaveState'] = (*map(int, match.groups()), 18)
        rects.update({'Radio1': (0, 8, 440, 54), 'Radio2': (0, 70, 440, 54)})
        def check(rectangles):
            for x, y, w, h in rectangles:
                self.assertTrue(0 <= x and 0 <= y and x+w <= 480 and y+h <= 480)
            for i, (x, y, w, h) in enumerate(rectangles):
                for a, b, c, d in rectangles[i+1:]:
                    self.assertTrue(x+w <= a or a+c <= x or y+h <= b or b+d <= y)
        hidden = [(20+x, 120+y, w, h) for name, (x,y,w,h) in rects.items() if name != 'Keyboard']
        keyboard = rects['Keyboard']
        active = re.search(r'place\(field, (\d+), (\d+)\)', CPP)
        shown = [(20+int(active[1]), 120+int(active[2]), 416, 48), (20+keyboard[0], 64+keyboard[1], keyboard[2], keyboard[3])]
        for layout in (hidden, shown):
            for rotation in range(4):
                check(layout)
                layout = [(480-y-h, x, h, w) for x,y,w,h in layout]
        for name in ('Profiles', 'NewButton', 'ForgetButton'):
            self.assertEqual(rects[name][3], 48)
        refresh = CPP.split('if (_settingsOpen && _settingsDetail == 4)', 1)[1].split('if (!_locked) refreshClock', 1)[0]
        self.assertNotIn('lv_textarea_set_text', refresh)
        self.assertNotIn('lv_dropdown_set_text', refresh)
        self.assertIn('wifi.count > 1 && !_wifiNewProfile', refresh)
        self.assertIn('lv_obj_set_style_text_color(_wifiForgetButton, lv_color_hex(Gray), LV_STATE_DISABLED)', init)
        self.assertNotIn('lv_obj_set_style_text_color(forget,', init)
        self.assertIn('lv_obj_remove_local_style_prop(forget, LV_STYLE_TEXT_COLOR, 0)', init)
        self.assertNotIn('lv_obj_set_style_opa(', init)
        # Pointer list selection sends VALUE_CHANGED even for the previously selected row.
        dropdown = (ROOT / 'components/lvgl/src/widgets/dropdown/lv_dropdown.c').read_text(encoding='utf8')
        release = dropdown.split('static lv_result_t list_release_handler(lv_obj_t * list_obj)', 1)[1]
        release = release[release.index('{'):].split('static void list_press_handler', 1)[0]
        self.assertIn('lv_obj_send_event(dropdown_obj, LV_EVENT_VALUE_CHANGED, &id)', release)

    def test_numeric_bridge_never_reads_widgets_or_secrets(self):
        reader = CPP.split('bool CodexMicroView::wifiEditorDebugSnapshot(', 1)[1].split('void CodexMicroView::cancelWifiEditorPending', 1)[0]
        self.assertNotIn('lv_', reader)
        self.assertNotIn('password', reader.lower())
        self.assertIn('attempt < 3', reader)
        self.assertIn('std::atomic<uint32_t>::is_always_lock_free', CPP)
        bridge = (ROOT / 'main/apps/app_codex_micro/app_codex_micro.cpp').read_text().split('bool AppCodexMicro::debugWifiEditorSnapshot(', 1)[1]
        self.assertNotIn('_view', bridge)
        self.assertIn('CodexMicroView::wifiEditorDebugSnapshot(out)', bridge)

    def test_actual_touch_and_field_callbacks(self):
        compilers = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:
            self.skipTest('embedded compiler unavailable')
        methods = []
        for name in ('wifiEditorGuards', 'cancelWifiEditorPending', 'serviceWifiEditorPending', 'wifiFieldEvent', 'touchEvent', 'wifiSelectionEvent', 'wifiActionEvent'):
            start = CPP.index(('uint32_t' if name == 'wifiEditorGuards' else 'void') + ' CodexMicroView::' + name + '(')
            body = CPP[start:CPP.index('\n}', start) + 2]
            body = body.replace('std::strlen', 'textLength').replace('std::strcmp', 'textCompare')
            body = body.replace('GetNetworkQuota()', 'self->network')
            methods.append('constexpr ' + body)
        harness = r'''
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <initializer_list>
using std::size_t;
enum { LV_EVENT_PRESSED, LV_EVENT_PRESSING, LV_EVENT_RELEASED, LV_EVENT_PRESS_LOST,
       LV_EVENT_CLICKED, LV_EVENT_VALUE_CHANGED, LV_EVENT_READY, LV_EVENT_CANCEL };
enum lv_obj_flag_t { LV_OBJ_FLAG_HIDDEN=1 };
enum { LV_STATE_DISABLED=1 };
enum { LV_INDEV_TYPE_POINTER, LV_INDEV_STATE_RELEASED, LV_INDEV_STATE_PRESSED };
struct lv_point_t { int x=0,y=0; };
struct lv_indev_t { lv_point_t point; int state=LV_INDEV_STATE_RELEASED; bool scrolling=false; };
struct lv_obj_t { lv_obj_t* parent=nullptr; int flags=0,x=0,y=0; bool empty=false; lv_obj_t* editor=nullptr; char text[65]{}; unsigned selected=0; int states=0; const char* dropdownText=nullptr; };
struct CodexMicroView;
struct lv_event_t { CodexMicroView* owner; int code; lv_obj_t* target; lv_obj_t* current; lv_indev_t* input; };
constexpr auto lv_event_get_user_data(lv_event_t* e) { return e->owner; }
constexpr auto lv_event_get_code(lv_event_t* e) { return e->code; }
constexpr auto lv_event_get_target(lv_event_t* e) { return e->target; }
constexpr auto lv_event_get_current_target(lv_event_t* e) { return e->current; }
constexpr auto lv_event_get_indev(lv_event_t* e) { return e->input; }
constexpr int lv_indev_get_type(lv_indev_t*) { return LV_INDEV_TYPE_POINTER; }
constexpr int lv_indev_get_state(lv_indev_t* i) { return i->state; }
lv_obj_t scrollMock{};
constexpr lv_obj_t* lv_indev_get_scroll_obj(lv_indev_t* i) { return i->scrolling ? &scrollMock : nullptr; }
constexpr void lv_indev_get_point(lv_indev_t* i,lv_point_t* p) { *p=i->point; }
constexpr bool lv_obj_has_state(lv_obj_t* o,int state) { return o->states&state; }
constexpr void lv_obj_add_state(lv_obj_t* o,int state) { o->states|=state; }
constexpr void lv_obj_remove_state(lv_obj_t* o,int state) { o->states&=~state; }
constexpr void lv_dropdown_set_text(lv_obj_t* o,const char* text) { o->dropdownText=text; }
constexpr auto lv_obj_get_parent(lv_obj_t* o) { return o->parent; }
constexpr bool lv_obj_has_flag(lv_obj_t* o,lv_obj_flag_t f) { return o->flags&f; }
constexpr void lv_obj_add_flag(lv_obj_t* o,lv_obj_flag_t f) { o->flags|=f; }
constexpr void lv_obj_remove_flag(lv_obj_t* o,lv_obj_flag_t f) { o->flags&=~f; }
constexpr void lv_keyboard_set_textarea(lv_obj_t* k,lv_obj_t* e) { k->editor=e; }
constexpr void lv_textarea_set_text(lv_obj_t* o,const char* s) { size_t i=0;for(;s[i]&&i<64;++i)o->text[i]=s[i];o->text[i]=0;o->empty=!i; }
constexpr auto lv_textarea_get_text(lv_obj_t* o) { return o->text; }
constexpr void lv_label_set_text(lv_obj_t* o,const char* s) { lv_textarea_set_text(o,s); }
constexpr unsigned lv_dropdown_get_selected(lv_obj_t* o) { return o->selected; }
constexpr void lv_dropdown_get_selected_str(lv_obj_t*,char* s,size_t) { s[0]=0; }
constexpr void lv_obj_set_style_border_width(lv_obj_t*,int,int) {}
constexpr void lv_obj_set_style_border_color(lv_obj_t*,int,int) {}
constexpr int lv_color_hex(int c) { return c; }
constexpr int Orange=1;
constexpr size_t textLength(const char* s) { size_t n=0;while(s[n])++n;return n; }
constexpr int textCompare(const char* a,const char* b) { size_t i=0;while(a[i]&&a[i]==b[i])++i;return a[i]-b[i]; }
struct WifiSettingsSnapshot { char names[6][33]={"saved","other"}; unsigned count=2; };
struct Network {
 bool queued=false;bool forgotten=false;
 constexpr bool wifiSettingsSnapshot(WifiSettingsSnapshot&) { return true; }
 constexpr bool requestWifiCredentials(const char*,const char*) { queued=true;return true; }
 constexpr bool requestWifiForget(const char*) { forgotten=true;return true; }
 constexpr bool requestWifiRestart() { return true; }
};
constexpr void lv_obj_move_foreground(lv_obj_t*) {}
constexpr void lv_dropdown_close(lv_obj_t*) {}
constexpr void place(lv_obj_t* o,int x,int y) { o->x=x;o->y=y; }
constexpr unsigned lv_tick_get() { return 10; }
struct CodexMicroView {
 enum class RotationPhase { Idle,FadeOut };
 RotationPhase _rotationPhase=RotationPhase::Idle;
 bool _settingsOpen=true,_settingsAnimating=false,_locked=false,_suppressed=false,_rotationFault=false,busy=false;
 bool _touchTracking=false,_swipeConsumed=true,_touchOnEditor=false,_wifiOpenNetwork=false,_wifiNewProfile=false;
 unsigned _settingsDetail=4,_activity=0;
 lv_point_t _touchStart;
 lv_obj_t page{},ssid{&page},password{&page},keyboard{&page,1},child{&ssid},rows[8]{};
 lv_obj_t* _wifiSsid=&ssid; lv_obj_t* _wifiPassword=&password; lv_obj_t* _wifiKeyboard=&keyboard;
 lv_obj_t* _settingsPage=&page; lv_obj_t* _slideTo=nullptr;
 lv_obj_t profiles{&page},newButton{},forget{},open{},save{},restart{},state{};
 lv_obj_t* _wifiNewButton=&newButton;lv_obj_t* _wifiProfiles=&profiles;lv_obj_t* _wifiForgetButton=&forget;lv_obj_t* _wifiOpenButton=&open;
 lv_obj_t* _wifiSaveButton=&save;lv_obj_t* _wifiRestartButton=&restart;lv_obj_t* _wifiSaveState=&state;
 lv_obj_t* _settingsRows[8]={&rows[0],&rows[1],&rows[2],&rows[3],&rows[4],&rows[5],&rows[6],&rows[7]};
 int navigation=0,closed=0;
 Network network;
 constexpr Network& GetNetworkQuota() { return network; }
 constexpr bool otaBusy() const { return busy; }
 constexpr void openSettings() { _settingsOpen=true; }
 constexpr void closeSettings() { ++closed; }
 constexpr void navigatePage(int) { ++navigation; }
 struct Debug { uint32_t presses=0,clicks=0,releases=0,field=0; } _wifiEditorDebug;
 lv_obj_t* _wifiEditorPending=nullptr; lv_indev_t* _wifiEditorInput=nullptr;
 lv_point_t _wifiEditorStart{}; bool _wifiEditorReleased=false;
 constexpr uint32_t wifiEditorGuards() const;
 constexpr void cancelWifiEditorPending();
 constexpr void serviceWifiEditorPending(bool);
 constexpr void publishWifiEditorDebug() {}
 static constexpr void wifiFieldEvent(lv_event_t*);
 static constexpr void touchEvent(lv_event_t*);
 static constexpr void wifiSelectionEvent(lv_event_t*);
 static constexpr void wifiActionEvent(lv_event_t*);
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
 if(v.keyboard.editor || !v._wifiEditorPending)return false;
 v.serviceWifiEditorPending(false);
 if(v.keyboard.editor!=&v.ssid || v.keyboard.flags || v.ssid.y!=70 || !(v.password.flags&1))return false;
 for(auto* o:{v._wifiProfiles,v._wifiNewButton,v._wifiForgetButton,v._wifiOpenButton,v._wifiSaveButton,v._wifiRestartButton,v._wifiSaveState})
  if(!(o->flags&1))return false;
 e.code=LV_EVENT_READY;e.current=&v.keyboard;v.wifiFieldEvent(&e);
 if(v.keyboard.editor || !(v.keyboard.flags&1) || v.ssid.y!=178 || v.password.y!=232 || v.password.flags)return false;
 for(auto* o:{v._wifiProfiles,v._wifiNewButton,v._wifiForgetButton,v._wifiOpenButton,v._wifiSaveButton,v._wifiRestartButton,v._wifiSaveState})
  if(o->flags&1)return false;
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
 e.code=LV_EVENT_CLICKED;e.current=&v.password;v.wifiFieldEvent(&e);v.serviceWifiEditorPending(false);
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
constexpr bool releaseOnly() {
 CodexMicroView v;lv_indev_t input{{200,280},LV_INDEV_STATE_PRESSED};
 lv_event_t e{&v,LV_EVENT_PRESSED,&v.child,&v.ssid,&input};
 v.wifiFieldEvent(&e);e.current=&v.page;v.touchEvent(&e);
 v.serviceWifiEditorPending(true);
 if(v.keyboard.editor || v.ssid.y || !v._wifiEditorPending)return false;
 e.current=&v.ssid;e.code=LV_EVENT_RELEASED;v.wifiFieldEvent(&e);
 v.serviceWifiEditorPending(true);if(v.keyboard.editor)return false;
 input.state=LV_INDEV_STATE_RELEASED;e.current=&v.page;v.touchEvent(&e);
 v.serviceWifiEditorPending(false);
 if(v.keyboard.editor!=&v.ssid || v._wifiEditorPending)return false;
 e.current=&v.keyboard;e.code=LV_EVENT_CANCEL;v.wifiFieldEvent(&e);
 // PRESS_LOST cancels an armed contact even without sheet touch tracking.
 e.current=&v.password;e.code=LV_EVENT_PRESSED;v.wifiFieldEvent(&e);
 e.code=LV_EVENT_PRESS_LOST;v.wifiFieldEvent(&e);
 e.code=LV_EVENT_CLICKED;v.wifiFieldEvent(&e);v.serviceWifiEditorPending(false);
 if(v.keyboard.editor || v._wifiEditorPending)return false;
 // Release movement and LVGL scrolling reject activation.
 for(int scroll=0;scroll<2;++scroll) {
  v._swipeConsumed=false;input.point={200,280};input.scrolling=false;
  e.code=LV_EVENT_PRESSED;v.wifiFieldEvent(&e);
  input.point.y=scroll?280:320;input.scrolling=scroll;
  e.code=LV_EVENT_RELEASED;v.wifiFieldEvent(&e);v.serviceWifiEditorPending(false);
  if(v.keyboard.editor || v._wifiEditorPending)return false;
 }
 input.scrolling=false;v._swipeConsumed=false;
 // Pending release canceled if a guard appears before GUI servicing.
 for(int guard=0;guard<8;++guard) {
  e.code=LV_EVENT_PRESSED;v.wifiFieldEvent(&e);
  e.code=LV_EVENT_RELEASED;v.wifiFieldEvent(&e);
  v._locked=guard==0;v._suppressed=guard==1;v.busy=guard==2;v._settingsAnimating=guard==3;
  v._rotationFault=guard==4;v._rotationPhase=guard==5?CodexMicroView::RotationPhase::FadeOut:CodexMicroView::RotationPhase::Idle;
  v._settingsOpen=guard!=6;v._settingsDetail=guard==7?0:4;
  v.serviceWifiEditorPending(false);if(v.keyboard.editor || v._wifiEditorPending)return false;
  v._locked=v._suppressed=v.busy=v._settingsAnimating=v._rotationFault=false;
  v._rotationPhase=CodexMicroView::RotationPhase::Idle;v._settingsOpen=true;v._settingsDetail=4;
 }
 // Synthetic click without pointer cannot queue opening.
 e.input=nullptr;e.code=LV_EVENT_CLICKED;v.wifiFieldEvent(&e);return !v._wifiEditorPending;
}
static_assert(releaseOnly(),"release-only arming defers layout, cancels loss, drag, scrolling and all lifecycle guards");
static_assert(exercise(),"actual editor callbacks preserve contact boundaries and keyboard lifecycle");
constexpr bool profiles() {
 CodexMicroView v;v._swipeConsumed=false;lv_event_t e{&v,LV_EVENT_VALUE_CHANGED,&v.profiles,&v.profiles,nullptr};
 lv_textarea_set_text(&v.password,"not-returned-to-list");
 v.profiles.selected=1;v.wifiSelectionEvent(&e);
 if(textCompare(v.ssid.text,"other") || !v.password.empty)return false;
 v.profiles.selected=6;v.wifiSelectionEvent(&e);
 if(textCompare(v.ssid.text,"other"))return false;
 // Saved SSIDs accept an empty editor: secret retention is the backend contract.
 v._swipeConsumed=false;e.code=LV_EVENT_CLICKED;e.target=&v.save;e.current=&v.save;
 v.wifiActionEvent(&e);if(!v.network.queued)return false;
 // A new secure SSID cannot be queued without a password or explicit OPEN.
 v.network.queued=false;lv_textarea_set_text(&v.ssid,"new");v.wifiActionEvent(&e);
 if(v.network.queued)return false;
 v._wifiOpenNetwork=true;v.wifiActionEvent(&e);if(!v.network.queued)return false;
 e.target=&v.forget;v.wifiActionEvent(&e);return v.network.forgotten;
}
constexpr bool newDraft() {
 CodexMicroView v; v._swipeConsumed=false;
 lv_textarea_set_text(&v.ssid,"saved");lv_textarea_set_text(&v.password,"secret");v._wifiOpenNetwork=true;
 lv_event_t e{&v,LV_EVENT_CLICKED,&v.newButton,&v.newButton,nullptr};
 // NEW obeys every action lifecycle guard, including stale swipes and the keyboard.
 for(int guard=0;guard<10;++guard) {
  v._locked=guard==0;v._suppressed=guard==1;v.busy=guard==2;v._settingsAnimating=guard==3;
  v._rotationFault=guard==4;v._rotationPhase=guard==5?CodexMicroView::RotationPhase::FadeOut:CodexMicroView::RotationPhase::Idle;
  v._settingsOpen=guard!=6;v._settingsDetail=guard==7?0:4;v._swipeConsumed=guard==8;v.keyboard.flags=guard==9?0:1;
  v.wifiActionEvent(&e);if(v._wifiNewProfile || textCompare(v.ssid.text,"saved"))return false;
 }
 v._locked=v._suppressed=v.busy=v._settingsAnimating=v._rotationFault=v._swipeConsumed=false;
 v._rotationPhase=CodexMicroView::RotationPhase::Idle;v._settingsOpen=true;v._settingsDetail=4;v.keyboard.flags=1;
 v._wifiEditorPending=&v.password;v.wifiActionEvent(&e);
 if(!v._wifiNewProfile || !v.ssid.empty || !v.password.empty || v._wifiOpenNetwork || v._wifiEditorPending)return false;
 if(!v.profiles.dropdownText || !(v.forget.states&LV_STATE_DISABLED) || v.network.queued || v.network.forgotten)return false;
 e.target=&v.forget;v.wifiActionEvent(&e);if(v.network.forgotten)return false;
 // NEW does not shortcut the accepted press/release keyboard guard.
 lv_indev_t input{{200,280},LV_INDEV_STATE_PRESSED};e.input=&input;e.target=&v.ssid;e.current=&v.ssid;e.code=LV_EVENT_PRESSED;
 v.wifiFieldEvent(&e);e.current=&v.page;v.touchEvent(&e);v.serviceWifiEditorPending(true);
 if(v.keyboard.editor)return false;
 e.current=&v.ssid;e.code=LV_EVENT_RELEASED;v.wifiFieldEvent(&e);input.state=LV_INDEV_STATE_RELEASED;
 e.current=&v.page;v.touchEvent(&e);v.serviceWifiEditorPending(false);
 if(v.keyboard.editor!=&v.ssid || !(v.newButton.flags&1))return false;
 e.current=&v.keyboard;e.code=LV_EVENT_READY;v.wifiFieldEvent(&e);
 if(!v._wifiNewProfile || !v.ssid.empty || v.newButton.flags)return false;
 // Selecting the prior saved profile exits the draft without exposing its secret.
 e.target=&v.profiles;e.current=&v.profiles;e.code=LV_EVENT_VALUE_CHANGED;v.profiles.selected=0;v.wifiSelectionEvent(&e);
 if(v._wifiNewProfile || v.profiles.dropdownText || textCompare(v.ssid.text,"saved") || !v.password.empty)return false;
 // A new secure draft stays a draft until accepted SAVE; open/new is the existing upsert request.
 e.target=&v.newButton;e.code=LV_EVENT_CLICKED;v.wifiActionEvent(&e);lv_textarea_set_text(&v.ssid,"new");
 e.target=&v.save;v.wifiActionEvent(&e);if(v.network.queued || !v._wifiNewProfile)return false;
 lv_textarea_set_text(&v.password,"12345678");v.wifiActionEvent(&e);
 return v.network.queued && !v.network.forgotten && !v._wifiNewProfile && v.password.empty && !v.profiles.dropdownText;
}
static_assert(newDraft(),"NEW draft is RAM-only, lifecycle guarded, keyboard safe and save/selection exits correctly");
static_assert(profiles(),"selection never reads a secret and saved/new/open queues retain policy");
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
