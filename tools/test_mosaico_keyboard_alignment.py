"""Real LVGL default/alignment arithmetic regression; no device simulation claim."""
from pathlib import Path
import re,subprocess,tempfile,unittest
R=Path(__file__).resolve().parents[1]
class KeyboardAlignmentTests(unittest.TestCase):
 def test_explicit_alignment_precedes_offsets(self):
  s=(R/'main/apps/app_codex_micro/view/view_mosaico.cpp').read_text(encoding='utf8')
  block=s[s.index('_wifiKeyboard = lv_keyboard_create'):s.index('renderSettings();',s.index('_wifiKeyboard = lv_keyboard_create'))]
  self.assertLess(block.index('lv_obj_set_align(_wifiKeyboard, LV_ALIGN_TOP_LEFT)'),block.index('panel(_wifiKeyboard, 0, 204, 440, 198)'))
 def test_actual_lvgl_constructor_and_position_arithmetic(self):
  base=R/'components/lvgl/src'
  if not base.exists():self.skipTest('configured LVGL source unavailable')
  keyboard=(base/'widgets/keyboard/lv_keyboard.c').read_text(encoding='utf8')
  pos=(base/'core/lv_obj_pos.c').read_text(encoding='utf8')
  self.assertIn('lv_obj_align(obj, LV_ALIGN_BOTTOM_MID, 0, 0)',keyboard)
  setpos=pos.split('void lv_obj_set_pos(',1)[1].split('\n}',1)[0]
  self.assertNotIn('set_align',setpos);self.assertNotIn('LV_ALIGN_TOP_LEFT',setpos)
  # Execute the real LVGL alignment case bodies, not a hand-written mock default.
  cases=[]
  body=pos[pos.index('void lv_obj_refr_pos'):]; body=body[body.index('switch(align)',body.index('switch(align)')+1):]
  # Each case also occurs in alignment helpers; use the actual refresh-position switch.
  for name in ('TOP_LEFT','BOTTOM_MID'):
   m=re.search(r'case LV_ALIGN_'+name+r':(.*?)break;',body,re.S)
   self.assertIsNotNone(m,name);cases.append('case '+name+':'+m[1]+'break;')
  code='''enum Align {TOP_LEFT,BOTTOM_MID};
constexpr int y_at(Align a) {int x=0,y=204,pw=440,ph=402,w=440,h=198;bool rtl=false;
 switch(a) { CASES } return 64+y;}
static_assert(y_at(BOTTOM_MID)==472 && y_at(BOTTOM_MID)+198>480,"old keyboard clipped below display");
static_assert(y_at(TOP_LEFT)==268 && y_at(TOP_LEFT)+198+2<=480,"keyboard fits including burn-in margin");
static_assert(64+56+70+48<y_at(TOP_LEFT),"editor and keyboard do not overlap");
'''.replace('CASES','\n'.join(cases))
  compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
  if not compilers:self.skipTest('cross compiler unavailable')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'alignment.cpp';p.write_text(code,encoding='utf8')
   r=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(p)],capture_output=True,text=True)
   self.assertEqual(r.returncode,0,r.stdout+r.stderr)
if __name__=='__main__':unittest.main()
