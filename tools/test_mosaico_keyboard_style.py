"""Bounded shared-model and actual LVGL callback/API syntax regressions."""
import re,subprocess,tempfile,unittest
from pathlib import Path
from preview_mosaico_keyboard import export,R,MODEL
V=R/'main/apps/app_codex_micro/view/view_mosaico.cpp'
class KeyboardStyleTests(unittest.TestCase):
 def test_shared_geometry_no_overlap(self):
  meta,keys=export();self.assertEqual(meta,(8,20,20,268,440,198))
  for mode in ('lc','uc','spec','num'):
   items=[k for k in keys if k['mode']==mode]
   self.assertEqual(len([k for k in items if k['label']=='GO']),1)
   for i,k in enumerate(items):
    x,y,w,h=k['rect'];self.assertGreater(w,0);self.assertGreater(h,20)
    self.assertTrue(20<=x and 268<=y and x+w+2<=480 and y+h+2<=480)
    for q in items[i+1:]:
     a,b,c,d=q['rect'];self.assertTrue(x+w<=a or a+c<=x or y+h<=b or b+d<=y)
   self.assertEqual(next(k['color'] for k in items if k['label']=='GO'),0xd18c37)
 def test_actual_event_api_and_pointer_gate(self):
  source=V.read_text();callback=source.split('void CodexMicroView::wifiKeyboardEvent(',1)[1].split('void CodexMicroView::wifiFieldEvent(',1)[0]
  style=source.split('    using namespace mosaico_keyboard;',1)[1].split('    lv_obj_add_event_cb(_wifiKeyboard, wifiFieldEvent',1)[0]
  code=f'#include "{MODEL.as_posix()}"\n#include <lvgl.h>\n#include <cstring>\n#include <initializer_list>\n'+'''
struct HAL {void vibrate(unsigned,unsigned);}; HAL& GetHAL();
struct CodexMicroView {lv_obj_t* _wifiKeyboard; mosaico_keyboard::PointerHaptic _wifiKeyboardHaptic;unsigned _activity;unsigned wifiEditorGuards();static void wifiKeyboardEvent(lv_event_t*);void style();};
void CodexMicroView::style(){using namespace mosaico_keyboard;
'''+style+'}\nvoid CodexMicroView::wifiKeyboardEvent('+callback+'''
constexpr bool gates(){
 using mosaico_keyboard::PointerHaptic;
 PointerHaptic h;
 if(h.select(true,true,true))return false;
 h.begin(false,true);if(h.select(true,true,true))return false;
 h.begin(true,false);if(h.select(true,true,true))return false;
 h.begin(true,true);if(h.select(false,true,true)||h.select(true,false,true)||h.select(true,true,false))return false;
 if(!h.select(true,true,true)||h.select(true,true,true))return false;
 h.end();if(h.select(true,true,true))return false;
 h.begin(true,true);return h.select(true,true,true);
}
static_assert(gates(),"one haptic per real allowed pointer contact");
'''
  compiler=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))[-1]
  out=R/'artifacts/keyboard-style';out.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'keyboard.cpp';p.write_text(code)
   run=subprocess.run([str(compiler),'-std=c++17','-Wall','-Wextra','-Werror','-fsyntax-only','-DLV_CONF_SKIP','-DLV_FONT_MONTSERRAT_20=1','-I'+str(R/'components/lvgl'),str(p)],capture_output=True,text=True)
   (out/'keyboard-api-syntax.log').write_text(run.stdout+run.stderr)
   self.assertEqual(run.returncode,0,run.stdout+run.stderr)
 def test_default_contract_and_no_bubble(self):
  s=V.read_text();self.assertNotIn('lv_keyboard_set_map(',s);self.assertNotIn('lv_buttonmatrix_set_ctrl_map(',s)
  self.assertIn('lv_obj_remove_flag(_wifiKeyboard, LV_OBJ_FLAG_EVENT_BUBBLE)',s)
  self.assertNotRegex(s,r'lv_obj_add_flag\(_wifiKeyboard,[^;]*LV_OBJ_FLAG_EVENT_BUBBLE')
  self.assertIn('lv_textarea_set_max_length(_wifiSsid, 32)',s);self.assertIn('lv_textarea_set_max_length(_wifiPassword, 64)',s)
  self.assertIn('wifiKeyboardEvent, static_cast<lv_event_code_t>(LV_EVENT_VALUE_CHANGED | LV_EVENT_PREPROCESS)',s)
if __name__=='__main__':unittest.main()
