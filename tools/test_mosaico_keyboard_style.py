"""Bounded shared-model and actual LVGL callback/API syntax regressions."""
import json,re,subprocess,tempfile,unittest
from pathlib import Path
from preview_mosaico_keyboard import export,specs,R,MODEL
V=R/'main/apps/app_codex_micro/view/view_mosaico.cpp'
class KeyboardStyleTests(unittest.TestCase):
 def test_shared_geometry_no_overlap(self):
  meta,keys=export();self.assertEqual(meta,(8,24,0,231,480,233,18,24,164,432,50))
  for mode in ('lc','uc','spec','extra'):
   items=[k for k in keys if k['mode']==mode]
   self.assertEqual(len([k for k in items if k['label']=='GO']),1)
   for i,k in enumerate(items):
    x,y,w,h=k['rect'];self.assertGreater(w,0);self.assertGreaterEqual(h,48)
    self.assertTrue(16<=x and 231<=y and x+w<=464 and y+h<=464)
    for q in items[i+1:]:
     a,b,c,d=q['rect'];self.assertTrue(x+w<=a or a+c<=x or y+h<=b or b+d<=y)
   self.assertEqual(next(k['color'] for k in items if k['label']=='GO'),0xd18c37)
 def test_factory_bbox_tolerance(self):
  factory=json.loads((R/'.artifacts/references/esp-gsp-manager/factory-key-rects.json').read_text())
  actual=[k for k in export()[1] if k['mode']=='lc']
  self.assertEqual(len(actual),len(factory)-1)
  for key,ref in zip(actual,factory[1:]):
   for value,axis in zip(key['rect'],('x','y','w','h')):
    self.assertLessEqual(abs(value-ref[axis]),2,(key['label'],axis))
 def test_actual_event_api_and_pointer_gate(self):
  source=V.read_text();callback=source.split('void CodexMicroView::wifiKeyboardEvent(',1)[1].split('void CodexMicroView::wifiFieldEvent(',1)[0]
  field_callback='void CodexMicroView::wifiFieldEvent('+source.split('void CodexMicroView::wifiFieldEvent(',1)[1].split('void CodexMicroView::wifiSelectionEvent(',1)[0]
  controls='void CodexMicroView::restoreWifiEditor()'+source.split('void CodexMicroView::restoreWifiEditor()',1)[1].split('void CodexMicroView::wifiKeyboardEvent',1)[0]
  style=source.split('    using namespace mosaico_keyboard;',1)[1].split('    lv_obj_add_event_cb(_wifiKeyboard, wifiFieldEvent',1)[0]
  code=f'#include <lvgl.h>\n#include "{MODEL.as_posix()}"\n#include <cstring>\n#include <cstdlib>\n#include <initializer_list>\n'+'''
struct HAL {void vibrate(unsigned,unsigned);}; HAL& GetHAL();
void panel(lv_obj_t*,int,int,int,int); void place(lv_obj_t*,int,int);
struct CodexMicroView {struct Debug {unsigned presses=0,releases=0,clicks=0,field=0;} _wifiEditorDebug; lv_obj_t* _wifiEditorPending; lv_indev_t* _wifiEditorInput; lv_point_t _wifiEditorStart; bool _swipeConsumed,_wifiEditorReleased; static void wifiFieldEvent(lv_event_t*); lv_obj_t* _wifiKeyboard; lv_obj_t *_wifiEditorOverlay,*_wifiSsid,*_wifiPassword,*_wifiEditorCancel,*_wifiEditorClear,*_wifiSymbolPage,*_wifiSymbolPageLabel,*_wifiProfiles,*_wifiNewButton,*_wifiForgetButton,*_wifiOpenButton,*_wifiSaveButton,*_wifiRestartButton,*_wifiSaveState; lv_obj_t* _settingsDetails[4]; lv_obj_t* _settingsRows[8]; void cancelWifiEditorPending(); void restoreWifiEditor(); void publishWifiEditorDebug(); static void wifiEditorControlEvent(lv_event_t*); mosaico_keyboard::PointerHaptic _wifiKeyboardHaptic;unsigned _activity;unsigned wifiEditorGuards();static void wifiKeyboardEvent(lv_event_t*);void style();};
void CodexMicroView::style(){using namespace mosaico_keyboard;
'''+style+'}\nvoid CodexMicroView::wifiKeyboardEvent('+callback+controls+field_callback+'''
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
// Sequence harness uses the production gate, not a source-string approximation.
// Rotation completes with the root swipe latch set; editor contacts cannot clear it.
struct EditorSequence {
 unsigned guards = 0; bool overlay = false, password = true, pending = false;
 constexpr void openAfterRelease() {
  if(!mosaico_keyboard::fieldEventGuards(guards, false)) overlay = true;
 }
 constexpr void rotationStart() { guards |= 64U; }
 constexpr void rotationComplete() { guards = (guards & ~64U) | 512U; }
 constexpr void finish(bool cancel) {
  if(mosaico_keyboard::fieldEventGuards(guards, true)) { pending = false; return; }
  overlay = false; pending = false; if(cancel) password = false;
 }
};
constexpr bool rotateBeforeGoOrCancel() {
 for(bool cancel : {false, true}) {
  EditorSequence e; e.openAfterRelease(); if(!e.overlay)return false;
  e.rotationStart(); e.finish(cancel); if(!e.overlay || !e.password)return false;
  e.rotationComplete(); e.finish(cancel);
  if(e.overlay || e.pending || e.password == cancel)return false;
  // The latch is ignored only for completion, never a new opening.
  e.openAfterRelease(); if(e.overlay)return false;
 }
 // Every non-swipe context guard still blocks READY and CANCEL after rotation.
 for(unsigned guard : {1U,2U,4U,8U,16U,32U,64U,128U,256U}) {
  for(bool cancel : {false, true}) {
   EditorSequence e; e.openAfterRelease(); e.rotationStart(); e.rotationComplete();
   e.guards |= guard; e.finish(cancel); if(!e.overlay || !e.password)return false;
  }
 }
 return true;
}
static_assert(rotateBeforeGoOrCancel(),"post-rotation GO/CANCEL completes; unsafe contexts and opening stay gated");
'''
  compiler=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))[-1]
  out=R/'artifacts/keyboard-style';out.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'keyboard.cpp';p.write_text(code)
   run=subprocess.run([str(compiler),'-std=c++17','-Wall','-Wextra','-Werror','-fsyntax-only','-DLV_CONF_SKIP','-DLV_FONT_MONTSERRAT_18=1','-DLV_FONT_MONTSERRAT_24=1','-I'+str(R/'components/lvgl'),str(p)],capture_output=True,text=True)
   (out/'keyboard-api-syntax.log').write_text(run.stdout+run.stderr)
   self.assertEqual(run.returncode,0,run.stdout+run.stderr)
 def test_factory_rows_ascii_and_editor_controls(self):
  modes=dict(specs())
  self.assertEqual([len(row) for row in modes['lc']],[10,9,9,3])
  self.assertEqual([len(row) for row in modes['spec']],[10,10,10,4])
  self.assertEqual([w for _,w,_ in modes['lc'][2]],[3,2,2,2,2,2,2,2,3])
  self.assertEqual([w for _,w,_ in modes['spec'][3]],[4,9,3,4])
  chars={label for rows in modes.values() for row in rows for label,_,_ in row if len(label)==1}
  self.assertEqual(chars,{chr(i) for i in range(32,127)})
  s=V.read_text()
  self.assertIn('lv_obj_create(_root); panel(_wifiEditorOverlay, 0, 0, 480, 480)',s)
  self.assertIn('lv_obj_set_parent(field, _wifiEditorOverlay)',s)
  self.assertIn('lv_obj_set_parent(field, _settingsDetails[3]); lv_obj_set_size(field, 416, 48)',s)
  self.assertIn('lv_textarea_set_text(self->_wifiPassword, "")',s)
  self.assertIn('lv_keyboard_get_textarea(self->_wifiKeyboard)) lv_textarea_set_text(field, "")',s)
  self.assertIn('target == self->_wifiEditorCancel',s)
  self.assertIn('LV_KEYBOARD_MODE_USER_1 : LV_KEYBOARD_MODE_SPECIAL',s)
  self.assertIn('lv_strdup(display)',s);self.assertIn('lv_free(const_cast<char*>(label->text))',s)
  self.assertIn('mosaico_keyboard::fieldEventGuards(self->wifiEditorGuards(),',s)
  self.assertIn('serviceWifiEditorPending(bool touching)',s)
  self.assertIn('_wifiEditorReleased || touching',s)
 def test_default_contract_and_no_bubble(self):
  s=V.read_text();self.assertEqual(s.count('lv_keyboard_set_map('),4)
  self.assertIn('lv_obj_remove_flag(_wifiKeyboard, LV_OBJ_FLAG_EVENT_BUBBLE)',s)
  self.assertNotRegex(s,r'lv_obj_add_flag\(_wifiKeyboard,[^;]*LV_OBJ_FLAG_EVENT_BUBBLE')
  self.assertIn('lv_textarea_set_max_length(_wifiSsid, 32)',s);self.assertIn('lv_textarea_set_max_length(_wifiPassword, 64)',s)
  self.assertIn('wifiKeyboardEvent, static_cast<lv_event_code_t>(LV_EVENT_VALUE_CHANGED | LV_EVENT_PREPROCESS)',s)
if __name__=='__main__':unittest.main()
