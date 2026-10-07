"""Lock six-slot geometry, explicit cached colour and actual minute RAM-dispatch tests."""
import subprocess, tempfile, unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'
CPP=(V/'view_mosaico.cpp').read_text(encoding='utf8')

class LockSessionsUiTests(unittest.TestCase):
    def test_actual_geometry_and_cached_colour(self):
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:self.skipTest('embedded compiler unavailable')
        code='#include "'+(V/'lock_session_geometry.h').as_posix()+'"\n'+r'''
using namespace mosaico_lock_sessions;
using S=mosaico_sessions_ui::Status;
constexpr bool geometry() {
 int left=card(0).x,right=card(5).x+card(5).w;
 if(left+right!=480 || right-left!=384)return false;
 for(unsigned slot=0;slot<6;++slot) {
  auto b=card(slot);
  for(int dx:{-2,0,2})for(int dy:{-2,0,2}) {
   if(b.x+dx<0 || b.x+b.w+dx>480 || b.y+dy<0 || b.y+b.h+dy>480)return false;
  }
  if(12+29>b.w || 6+35>b.h)return false; // Centre dot-number has real pitch 5.
 }
 for(unsigned count=0;count<=6;++count)for(unsigned slot=0;slot<6;++slot) {
  const bool fresh=slot<count;
  if(color(S::Thinking,true,fresh,1000,1000)!=(fresh?0x9868CD:0x343A40))return false;
  if(color(S::Complete,true,fresh,1000,1000)!=(fresh?0xD18C37:0x343A40))return false;
 }
 for(S s:{S::Unknown,S::Unassigned,S::Idle,S::Wait,S::Error})if(color(s,true,true,1000,1000)!=0x343A40)return false;
 if(color(S::Thinking,false,true,1000,1000)!=0x343A40 || color(S::Thinking,true,true,61001,1000)!=0x343A40)return false;
 if(color(S::Thinking,true,true,1000,1001)!=0x343A40)return false; // Future timestamp cannot look fresh.
 if(color(S::Thinking,true,true,0x20,0xfffffff0U)!=0x9868CD)return false;
 for(int w=0;w<=120;++w)for(int h=1;h<=36;++h) {
  auto row=batteryRow(w,h);int right=row.textX+w;
  if(row.iconX+right<479 || row.iconX+right>480 || row.textX-row.iconX!=48)return false;
  if(row.textY<334 || row.textY+h>370 || row.textY*2+h<703 || row.textY*2+h>704)return false;
  if(row.iconX-2<0 || right+2>480)return false;
 }
 for(unsigned i=0;i<4;++i) {
  auto c=corner(i);if(c.x-8<0 || c.x+8>53 || c.y-8<0 || c.y+8>47)return false;
 }
 return 370<392 && 440<448 && 448+18+2<480;
}
static_assert(geometry(),"six centred rounded cards, exact battery group centre, cache freshness and bounded clip margins");
'''
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'geometry.cpp';source.write_text(code,encoding='utf8')
            result=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
            (R/'.artifacts/mosaico/lock-sessions-geometry.log').write_text(result.stdout+result.stderr,encoding='utf8')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        border=CPP.split('void CodexMicroView::lockSessionBorderEvent(',1)[1].split('void CodexMicroView::requestLockedSessions()',1)[0]
        self.assertIn('line.dash_width=5;line.dash_gap=3',border)
        self.assertIn('lv_draw_arc(layer,&arc)',border)
        self.assertIn('arc.radius=8;arc.rounded=1',border)
        battery=CPP.split('void CodexMicroView::refreshBattery(',1)[1].split('void CodexMicroView::refreshClock',1)[0]
        self.assertIn('lv_text_get_size(&size,text,&lv_font_montserrat_20',battery)
        self.assertIn('batteryRow(size.x,size.y)',battery)
        render=CPP.split('void CodexMicroView::renderLockSessions()',1)[1].split('void CodexMicroView::initSessions()',1)[0]
        self.assertIn('_sessionBackend.freshnessKnownMask',render)
        self.assertIn('CACHED --',render);self.assertIn('CACHED %lus',render)
        for forbidden in ('wakeDisplay','setBackLightBrightness','GetCodexMicroBle','requestJson','sendKey','lv_timer_create'):
            self.assertNotIn(forbidden,border+render)

    def test_actual_minute_and_lock_dispatch(self):
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:self.skipTest('embedded compiler unavailable')
        start=CPP.index('void CodexMicroView::requestLockedSessions()')
        request=CPP[start:CPP.index('\n}',start)+2].replace('MosaicoSessions::requestLockedRefresh()','ramRequest()')
        update=CPP.split('void CodexMicroView::update(',1)[1]
        start=update.index('    const uint32_t refreshElapsed')
        minute=update[start:update.index('    } else if (!_locked)',start)]+'    }\n'
        code=r'''
#include <cstdint>
struct Hal { constexpr uint32_t millis(){return 100;} };
constexpr Hal GetHAL(){return {};}
struct CodexMicroView {
 bool _locked=true,_suppressed=false,_rotationFault=false,busy=false,_shiftPending=false,readyFlag=true;
 uint32_t _refresh=0,_lockRefreshCount=0;int requests=0,renders=0,quota=0,history=0;
 constexpr bool ready(){return readyFlag;}
 constexpr bool otaKeepAwake(){return busy;}
 constexpr void ramRequest(){++requests;}
 constexpr void renderLockSessions(){++renders;}
 constexpr void refreshQuota(uint32_t){++quota;}
 constexpr void refreshHistory(){++history;}
 constexpr void requestLockedSessions();
 constexpr void tick(uint32_t tick) {
'''+minute+r'''
 }
};
'''+ 'constexpr '+request+r'''
constexpr bool dispatch() {
 CodexMicroView v;v.requestLockedSessions();if(v.requests!=1)return false; // Entry request, RAM only.
 v.tick(59999);if(v.requests!=1 || v.renders)return false;
 v.tick(60000);if(v.requests!=2 || v.renders!=1 || v._lockRefreshCount!=1 || v.history)return false;
 v.tick(60001);if(v.requests!=2 || v.renders!=1)return false;
 for(int guard=0;guard<5;++guard) {
  CodexMicroView g;g._locked=guard!=0;g.busy=guard==1;g._suppressed=guard==2;g._rotationFault=guard==3;g.readyFlag=guard!=4;
  g.requestLockedSessions();if(g.requests)return false;
 }
 CodexMicroView wrap;wrap._refresh=0xfffffff0U;wrap.tick(wrap._refresh+60000U);if(wrap.requests!=1)return false;
 CodexMicroView future;future._refresh=101;future.tick(100);return !future.requests;
}
static_assert(dispatch(),"entry and minute only dispatch cached RAM requests, never wake or change brightness");
'''
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'dispatch.cpp';source.write_text(code,encoding='utf8')
            result=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
            (R/'.artifacts/mosaico/lock-sessions-dispatch.log').write_text(result.stdout+result.stderr,encoding='utf8')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        lock=CPP.split('void CodexMicroView::lockDisplay()',1)[1].split('bool CodexMicroView::lockForDebug()',1)[0]
        self.assertLess(lock.index('_locked = true'),lock.index('requestLockedSessions()'))
        self.assertIn('requestLockedSessions();renderLockSessions()',lock)
        self.assertNotIn('lv_timer_create',CPP)
        self.assertIn('if (_page == Page::Sessions && !_locked &&',CPP)

if __name__=='__main__':unittest.main()
