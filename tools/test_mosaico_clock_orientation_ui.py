"""Offline source/geometry and constexpr-state tests; not physical display acceptance."""
import datetime, re, subprocess, tempfile, unittest
from pathlib import Path

R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'
CPP=(V/'view_mosaico.cpp').read_text(encoding='utf8')
HDR=(V/'view_mosaico.h').read_text(encoding='utf8')
DOT=(V/'dot_widgets.cpp').read_text(encoding='utf8')
CLOCK=(R/'main/host/system_clock.h').read_text(encoding='utf8')

class ClockOrientationUiTests(unittest.TestCase):
    def test_clock_source_geometry_and_real_colon(self):
        def values(pattern):
            m=re.search(pattern,CPP);self.assertIsNotNone(m,pattern)
            return tuple(map(int,m.groups()))
        w,h,p=values(r'_footer = createText\(_root, (\d+), (\d+), (\d+)\)')
        x,y=values(r'place\(_footer, (\d+), (\d+)\)')
        ix,iy=values(r'place\(_clockIcon, (\d+), (\d+)\)')
        size,=values(r'createIcon\(_root, Icon::Clock, (\d+), Gray\)')
        dx,dy,dw=values(r'_clockDate = label\(_root, (\d+), (\d+), (\d+)')
        dh,=values(r'lv_obj_set_height\(_clockDate, (\d+)\)')
        bx,by,bw=values(r'_batteryCapacity = label\(_root, (\d+), (\d+), (\d+)')
        bh,=values(r'lv_obj_set_height\(_batteryCapacity, (\d+)\)')
        qy,qh=values(r'panel\(_quotaPage, 20, (\d+), 440, (\d+), 0\)')
        tx,ty,tw=values(r'_bucketCount = label\(_root, (\d+), (\d+), (\d+)')
        regions=((x,y,w,h),(ix,iy,size,size),(dx,dy,dw,dh),(bx,by,bw,bh))
        for a in regions:
            self.assertGreaterEqual(min(a),0);self.assertLessEqual(a[0]+a[2],480);self.assertLessEqual(a[1]+a[3],480)
        # Icon slightly touches the clock object's unused left margin; only the
        # actual source-glyph dot extent is relevant to drawn-text collision.
        pitch=min(p,w//29,h//7);diameter=max(1,pitch*7//10)
        drawn_w=28*pitch+diameter;drawn_h=6*pitch+diameter
        left=x+(w-drawn_w)//2;top=y+(h-drawn_h)//2
        self.assertLessEqual(ix+size,left)
        self.assertLessEqual(left+drawn_w,dx)
        self.assertLessEqual(dx+dw,bx)
        self.assertLessEqual(top+drawn_h,480)
        self.assertLessEqual(qy+qh,y)
        self.assertLessEqual(x+w,tx);self.assertLessEqual(tx+tw,480)
        # Truncated count overlaps date/capacity; both are intentionally hidden.
        self.assertLess(tx,dx+dw);self.assertLess(tx,bx+bw)
        self.assertIn('_clockMinute >= 0 && !_quota->truncated',CPP)
        self.assertIn('_capacityKnown && _page == Page::Command && !_quota->truncated',CPP)
        motion=CPP.split('void CodexMicroView::updateAnimations(',1)[1].split('void CodexMicroView::refreshQuota(',1)[0]
        self.assertNotIn('< 370',motion);self.assertIn('lv_obj_get_height(_quotaPage)',motion)
        glyphs={key:tuple(map(int,rows.split(','))) for key,rows in re.findall(r"\{'(.)',\{([\d,]+)\}\}",DOT)}
        self.assertEqual(glyphs[':'],(0,4,4,0,4,4,0))
        self.assertIn("static_assert(uiGlyph(':').key == ':'",DOT)
        clock=CPP.split('void CodexMicroView::refreshClock(',1)[1].split('void CodexMicroView::cancelOrientation()',1)[0]
        lower=int(re.search(r'FirstValidEpoch = (\d+)LL',CLOCK)[1])
        upper=int(re.search(r'LastValidEpoch = (\d+)LL',CLOCK)[1])
        self.assertEqual(datetime.datetime.fromtimestamp(lower,datetime.timezone.utc).year,2024)
        self.assertEqual(datetime.datetime.fromtimestamp(upper,datetime.timezone.utc).year,2100)
        self.assertIn('MosaicoClock::snapshot()',clock);self.assertIn('MosaicoClock::shanghaiTime(systemClock.epoch, local)',clock)
        self.assertIn('epoch + 8 * 3600',CLOCK);self.assertIn('gmtime_r(&local, &out)',CLOCK)
        self.assertIn('minute == _clockMinute',clock)
        self.assertIn('_locked ? 60000U : 1000U',clock)
        self.assertIn('"--:--"',clock);self.assertIn('!_quota->truncated',clock)
        self.assertNotIn('setenv(',CPP);self.assertNotIn('tzset(',CPP);self.assertNotIn('nvs_',clock)
        self.assertNotIn('lv_timer_create',CPP)

    def test_orientation_cleanup_and_low_memory_boundary(self):
        for name in ('~CodexMicroView','setPageForDebug','lockDisplay','setInputSuppressed'):
            start=CPP.index('CodexMicroView::'+name+'(')
            body=CPP[start:CPP.index('\n}',start)]
            self.assertIn('cancelOrientation()',body)
        self.assertIn('cancelOrientation(); GetHAL().setMotionIdle(true);',CPP)
        self.assertNotIn('lv_obj_set_style_opa(',CPP)
        self.assertNotIn('lv_snapshot',CPP)
        self.assertIn('panel(_rotationCurtain, 0, 0, 480, 480, 0)',CPP)
        self.assertIn('lv_obj_set_style_radius(_rotationCurtain, 0, 0)',CPP)
        self.assertIn('fadeIn ? 160 : 120',CPP)
        self.assertNotIn('lv_display_rotate_point',CPP)
        for name in ('touchEvent','cellEvent','modeEvent','otaEvent','navigatePage','togglePage','wakeDisplay','lockDisplay','setPageForDebug','showHistory','selectHistory'):
            start=CPP.index('CodexMicroView::'+name+'(')
            self.assertIn('_rotationFault',CPP[start:CPP.index('\n}',start)])
        self.assertIn('_rotationFault = !GetHAL().isDisplayOrientationHealthy()',CPP)

    def test_actual_orientation_functions_constexpr(self):
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:self.skipTest('embedded compiler unavailable')
        methods=[]
        for name in ('cancelOrientation','orientationExec','orientationCompleted','startOrientationFade','updateOrientation'):
            start=CPP.index('void CodexMicroView::'+name+'(');end=CPP.index('\n}',start)+2
            # Typed owner / instance adapters let C++17 evaluate the actual
            # production bodies. LVGL, frames and HAL are deterministic stubs.
            body=CPP[start:end].replace('void* owner','CodexMicroView* owner').replace('GetHAL()','_hal')
            body=body.replace('GetDisplayFrameCount()','self->_frames' if name=='orientationCompleted' else '_frames')
            methods.append('constexpr '+body)
        harness=r'''
using int32_t=int; using lv_opa_t=unsigned char;
enum { LV_OPA_TRANSP=0,LV_OPA_COVER=255,LV_OBJ_FLAG_HIDDEN=1 };
struct CodexMicroView;
struct lv_obj_t { bool hidden=true; int opacity=0,invalidations=0; };
struct lv_anim_t { CodexMicroView* var=nullptr; int duration=0; };
constexpr void lv_obj_add_flag(lv_obj_t* o,int) { o->hidden=true; }
constexpr void lv_obj_remove_flag(lv_obj_t* o,int) { o->hidden=false; }
constexpr void lv_obj_set_style_bg_opa(lv_obj_t* o,int p,int) { o->opacity=p; }
constexpr lv_obj_t* lv_obj_get_parent(lv_obj_t* o) { return o; }
constexpr void lv_obj_invalidate(lv_obj_t* o) { ++o->invalidations; }
constexpr void lv_obj_move_foreground(lv_obj_t*) {}
constexpr void lv_anim_delete(CodexMicroView*,void(*)(CodexMicroView*,int)) {}
constexpr void lv_anim_init(lv_anim_t* a) { *a={}; }
constexpr void lv_anim_set_var(lv_anim_t* a,CodexMicroView* v) { a->var=v; }
constexpr void lv_anim_set_exec_cb(lv_anim_t*,void(*)(CodexMicroView*,int)) {}
constexpr void lv_anim_set_values(lv_anim_t*,int,int) {}
constexpr void lv_anim_set_duration(lv_anim_t* a,int d) { a->duration=d; }
constexpr void lv_anim_set_completed_cb(lv_anim_t*,void(*)(lv_anim_t*)) {}
constexpr void lv_anim_start(lv_anim_t*) {}
struct FakeHal {
 struct Motion { bool available=true,idle=false,valid=true; unsigned generation=1; unsigned short degrees=90; } motion;
 bool idle=false,fail=false,healthy=true,rollbackFailed=false; unsigned short degrees=0; int applied=0;
 constexpr void setMotionIdle(bool v) { idle=v; }
 constexpr Motion motionOrientation() { return motion; }
 constexpr unsigned short getDisplayOrientation() { return degrees; }
 constexpr bool isDisplayOrientationHealthy() { return healthy; }
 constexpr bool setDisplayOrientation(unsigned short v) { ++applied; if(fail){healthy=!rollbackFailed;return false;} degrees=v; return true; }
};
struct CodexMicroView {
 enum class RotationPhase { Idle,FadeOut,WaitBlack,WaitRotated,FadeIn };
 RotationPhase _rotationPhase=RotationPhase::Idle;
 lv_obj_t curtain{},root{};
 lv_obj_t* _rotationCurtain=&curtain; lv_obj_t* _root=&root; lv_obj_t* _slideTo=nullptr;
 bool _rotationFault=false,_locked=false,_suppressed=false,busy=false,_touchTracking=false,_swipeConsumed=false;
 unsigned _motionGeneration=~0U,_rotationGeneration=0,_rotationFrame=0,_frames=0; unsigned short _rotationTarget=0;
 FakeHal _hal;
 constexpr bool otaBusy() { return busy; }
 constexpr void cancelOrientation();
 static constexpr void orientationExec(CodexMicroView*,int);
 static constexpr void orientationCompleted(lv_anim_t*);
 constexpr void startOrientationFade(bool);
 constexpr void updateOrientation(bool);
};
'''
        harness+='\n'.join(methods)+r'''
constexpr bool lifecycle() {
 CodexMicroView v; lv_anim_t a{&v};
 v.updateOrientation(true); if(v._rotationPhase!=CodexMicroView::RotationPhase::Idle)return false;
 v._slideTo=&v.root; v.updateOrientation(false); if(!v.curtain.hidden)return false;
 v._slideTo=nullptr; v.updateOrientation(false);
 if(v._rotationPhase!=CodexMicroView::RotationPhase::FadeOut || v.curtain.hidden)return false;
 v.orientationExec(&v,255); v.orientationCompleted(&a);
 if(v._rotationPhase!=CodexMicroView::RotationPhase::WaitBlack || v.root.invalidations!=1)return false;
 v.updateOrientation(false); if(v._hal.applied)return false;
 ++v._frames; v.updateOrientation(false);
 if(v._rotationPhase!=CodexMicroView::RotationPhase::WaitRotated || v._hal.degrees!=90 || v._hal.applied!=1)return false;
 v.updateOrientation(false); if(v._rotationPhase!=CodexMicroView::RotationPhase::WaitRotated)return false;
 ++v._frames; v.updateOrientation(false);
 if(v._rotationPhase!=CodexMicroView::RotationPhase::FadeIn)return false;
 v.orientationExec(&v,0); v.orientationCompleted(&a);
 if(!v.curtain.hidden || v._rotationPhase!=CodexMicroView::RotationPhase::Idle || !v._swipeConsumed)return false;
 v.updateOrientation(false); return v.curtain.hidden && v._hal.applied==1;
}
constexpr bool cancellations() {
 for(int reason=0;reason<5;++reason) {
  CodexMicroView v; lv_anim_t a{&v}; v.updateOrientation(false);
  v.orientationExec(&v,255); v.orientationCompleted(&a); ++v._frames;
  v._locked=reason==0; v._suppressed=reason==1; v.busy=reason==2;
  if(reason==3)v._slideTo=&v.root;
  v.updateOrientation(reason==4);
  if(!v.curtain.hidden || v._rotationPhase!=CodexMicroView::RotationPhase::Idle || v._hal.applied)return false;
  if(reason<3 && !v._hal.idle)return false;
  v._locked=false;v._suppressed=false;v.busy=false;v._slideTo=nullptr;
  v.updateOrientation(false);
  if(v._rotationPhase!=CodexMicroView::RotationPhase::FadeOut)return false; // Deferred candidate can retry after input ends.
 }
 CodexMicroView failed; lv_anim_t a{&failed};failed._hal.fail=true;
 failed.updateOrientation(false);failed.orientationExec(&failed,255);failed.orientationCompleted(&a);
 ++failed._frames;failed.updateOrientation(false);
 return failed.curtain.hidden && failed._hal.degrees==0;
}
constexpr bool failedRollbackStaysBlack() {
 CodexMicroView v;lv_anim_t a{&v};v._hal.fail=true;v._hal.rollbackFailed=true;
 v.updateOrientation(false);v.orientationExec(&v,255);v.orientationCompleted(&a);
 ++v._frames;v.updateOrientation(false);
 if(!v._rotationFault || v.curtain.hidden || v.curtain.opacity!=255 || v._hal.applied!=1)return false;
 // Late animation completion, suppression/OTA/lock cleanup cannot expose UI.
 v.orientationExec(&v,0);v.orientationCompleted(&a);
 v._suppressed=true;v.busy=true;v._locked=true;v.cancelOrientation();v.updateOrientation(true);
 if(v.curtain.hidden || v.curtain.opacity!=255 || !v._hal.idle || !v._swipeConsumed || v._touchTracking)return false;
 // A later healthy HAL flag alone is not an explicit restore operation.
 v._hal.healthy=true;v._hal.fail=false;v._hal.motion.generation=2;
 v._suppressed=false;v.busy=false;v._locked=false;v.updateOrientation(false);
 return !v.curtain.hidden && v.curtain.opacity==255 && v._hal.applied==1 && v._rotationFault;
}
static_assert(lifecycle(),"curtain waits for black frame before rotation and rotated frame before fade-in");
static_assert(cancellations(),"touch, slide, lock, OTA, suppression and hardware failure cancel safely");
static_assert(failedRollbackStaysBlack(),"failed rollback is sticky black and never retries or auto restores");
'''
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'orientation.cpp';source.write_text(harness,encoding='utf8')
            result=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
            log=R/'.artifacts/mosaico/orientation-ui-source-harness.log';log.write_text(result.stdout+result.stderr,encoding='utf8')
            self.assertEqual(result.returncode,0,'source harness failed: '+str(log))

if __name__=='__main__':unittest.main()
