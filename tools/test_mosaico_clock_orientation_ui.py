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
        w,h,p=values(r'_footer = createText\(_root, (\d+), (\d+), (\d+), Purple\)')
        x,y=values(r'place\(_footer, (\d+), (\d+)\)')
        ix,iy=values(r'place\(_clockIcon, (\d+), (\d+)\)')
        size,=values(r'createIcon\(_root, Icon::Clock, (\d+), Purple\)')
        dw,dh,dp=values(r'_clockDate = createText\(_root, (\d+), (\d+), (\d+), Purple\)')
        dx,dy=values(r'place\(_clockDate, (\d+), (\d+)\)')
        qy,qh=values(r'panel\(_quotaPage, 20, (\d+), 440, (\d+), 0\)')
        tx,ty,tw=values(r'_bucketCount = label\(_root, (\d+), (\d+), (\d+)')
        regions=((x,y,w,h),(ix,iy,size,size),(dx,dy,dw,dh))
        for a in regions:
            # Leave one-pixel clearance for all four burn-in offsets.
            self.assertGreaterEqual(min(a),1);self.assertLessEqual(a[0]+a[2],479);self.assertLessEqual(a[1]+a[3],479)
        self.assertEqual((w,h,p),(dw,dh,dp))
        self.assertEqual(p,6)
        self.assertGreaterEqual(w//29,p);self.assertGreaterEqual(h//7,p)
        self.assertEqual(max(1,p*7//10),4)
        pitch=min(p,w//29,h//7);diameter=max(1,pitch*7//10)
        drawn_w=28*pitch+diameter;drawn_h=6*pitch+diameter
        left=x+(w-drawn_w)//2;top=y+(h-drawn_h)//2
        self.assertLessEqual(ix+size,left)
        self.assertLessEqual(x+w,dx)
        self.assertLessEqual(top+drawn_h,479)
        self.assertLessEqual(qy+qh,y)
        self.assertLessEqual(x+w,tx);self.assertLessEqual(tx+tw,479)
        # Truncated count replaces the date, never the HH:MM clock.
        self.assertLess(tx,dx+dw)
        self.assertIn('_clockMinute >= 0 && !_quota->truncated',CPP)
        self.assertNotIn('_batteryCapacity',CPP+HDR)
        self.assertNotIn('mAh',CPP)
        self.assertIn('setIcon(_batteryIcon, Icon::Battery',CPP)
        self.assertIn('lv_label_set_text(_battery, text)',CPP)
        self.assertIn('lv_obj_remove_flag(_boltIcon',CPP)
        self.assertIn('setText(_footer, clock, Purple)',CPP)
        self.assertIn('setText(_clockDate, date, Purple)',CPP)
        self.assertIn('setIcon(_clockIcon, Icon::Clock, Purple)',CPP)
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

    def test_lock_clock_minute_refresh_and_geometry(self):
        m=re.search(r'_lockClock = createText\(_lockPanel, (\d+), (\d+), (\d+), Orange\); place\(_lockClock, (\d+), (\d+)\)',CPP)
        self.assertIsNotNone(m)
        w,h,p,x,y=map(int,m.groups())
        self.assertEqual((w,h,p),(240,64,8))
        self.assertGreaterEqual(w//29,p);self.assertGreaterEqual(h//7,p)
        self.assertEqual(p*7//10,5)
        self.assertEqual(x+w//2,240)
        quota=re.search(r'_lockQuota = createText\(_lockPanel, (\d+), (\d+), (\d+)\); place\(_lockQuota, (\d+), (\d+)\)',CPP)
        qw,qh,qp,qx,qy=map(int,quota.groups())
        self.assertLessEqual(y+h,qy)
        self.assertLessEqual(qy+qh,252) # reset starts at y252, ends y300
        self.assertLessEqual(252+48,334) # battery starts at y334, ends y370
        for shiftx,shifty in ((-1,-1),(1,-1),(1,1),(-1,1)):
            for rx,ry,rw,rh in ((x,y,w,h),(qx,qy,qw,qh),(152,334,36,36),(204,338,200,24)):
                self.assertGreaterEqual(rx+shiftx,0);self.assertGreaterEqual(ry+shifty,0)
                self.assertLessEqual(rx+shiftx+rw,480);self.assertLessEqual(ry+shifty+rh,480)
        clock=CPP.split('void CodexMicroView::refreshClock(',1)[1].split('void CodexMicroView::cancelOrientation()',1)[0]
        self.assertIn('if (!force && minute == _clockMinute) return;',clock)
        self.assertIn('setText(_lockClock, clock, Orange)',clock)
        self.assertIn('char clock[6] = "--:--"',clock)
        lock=CPP.split('void CodexMicroView::lockDisplay()',1)[1].split('bool CodexMicroView::lockForDebug()',1)[0]
        self.assertIn('refreshClock(lv_tick_get(), true)',lock)
        self.assertIn('lv_obj_invalidate(_lockPanel)',lock)
        self.assertIn('lv_obj_move_foreground(_lockPanel)',lock)
        self.assertIn('lv_obj_add_flag(_overlay, LV_OBJ_FLAG_HIDDEN)',lock)
        self.assertNotIn('lv_obj_remove_flag(_overlay',lock)
        update=CPP.split('void CodexMicroView::update(',1)[1]
        self.assertIn('if (!_locked) refreshClock(tick)',update)
        self.assertIn('if (refreshElapsed >= 60000U && refreshElapsed < 0x80000000U)',update)
        self.assertIn('_shiftPending = true',update)
        self.assertNotIn('lv_timer_create',CPP)

    def test_actual_update_idle_and_refresh_timestamp_boundaries(self):
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:self.skipTest('embedded compiler unavailable')
        update=CPP.split('void CodexMicroView::update(',1)[1]
        start=update.index('    tick = lv_tick_get();')
        end=update.index('        _refresh = tick;',start)
        timing=update[start:end]
        # Actual production recaptures/unsigned elapsed guards, not a Python model.
        harness=r"""
#include <cstdint>
#include <algorithm>
struct View {
 uint32_t now=100, _activity=100, _refresh=100;
 bool _chargeProfile=false;
 struct Config { uint32_t chargeTimeoutSeconds=60,batteryTimeoutSeconds=60; };
 struct Display { Config config{}, effectiveConfig{}; uint32_t runtimeRevision=1; bool temporary=false; } _displaySettings;
 bool _locked=false; int lockCount=0, refreshCount=0;
 constexpr bool otaKeepAwake() { return false; }
 constexpr uint32_t lv_tick_get() { return now; }
 constexpr void lockDisplay() { ++lockCount; _locked=true; now+=3; _refresh=lv_tick_get(); }
 constexpr void setPageForDebug() { _activity=lv_tick_get(); }
 constexpr void refreshOta() { now+=3; setPageForDebug(); }
 constexpr void compare(bool interacting=false) {
  uint32_t tick=now-3; // The stale entry tick captured before callbacks.
"""+timing+r"""
   ++refreshCount;
  }
 }
};
constexpr bool cases() {
 View offer; offer.refreshOta();offer.compare();
 if(offer._locked || offer.refreshCount || offer._activity!=103)return false;
 View future;future._activity=104;future._refresh=104;future.compare();
 if(future._locked || future.refreshCount)return false;
 View idle;idle.now=60100;idle.compare();
 if(!idle._locked || idle.lockCount!=1 || idle.refreshCount || idle._refresh!=60103)return false;
 View before;before.now=60099;before.compare();
 if(before._locked || before.refreshCount)return false;
 View refresh;refresh._locked=true;refresh.now=60100;refresh.compare();
 if(refresh.refreshCount!=1)return false;
 View wrap;wrap._activity=0xfffffff0U;wrap._refresh=wrap._activity;
 wrap.now=wrap._activity+59999U;wrap.compare();
 if(wrap._locked || wrap.refreshCount)return false;
 wrap.now=wrap._activity+60000U;wrap.compare();
 if(!wrap._locked || wrap.refreshCount || wrap.lockCount!=1)return false;
 View wrapRefresh;wrapRefresh._locked=true;wrapRefresh._refresh=0xfffffff0U;
 wrapRefresh.now=wrapRefresh._refresh+60000U;wrapRefresh.compare();
 if(wrapRefresh.refreshCount!=1)return false;
 View never;never._chargeProfile=true;never._displaySettings.effectiveConfig.chargeTimeoutSeconds=0;
 never.now=600100;never.compare();if(never._locked)return false;
 View longCharge;longCharge._chargeProfile=true;longCharge._displaySettings.effectiveConfig.chargeTimeoutSeconds=600;
 longCharge.now=600099;longCharge.compare();if(longCharge._locked)return false;
 longCharge.now=600100;longCharge.compare();if(!longCharge._locked)return false;
 View shortBattery;shortBattery._displaySettings.effectiveConfig.batteryTimeoutSeconds=45;
 shortBattery.now=45099;shortBattery.compare();if(shortBattery._locked)return false;
 shortBattery.now=45100;shortBattery.compare();if(!shortBattery._locked)return false;
 View corruptBattery;corruptBattery._displaySettings.effectiveConfig.batteryTimeoutSeconds=600;
 corruptBattery.now=60100;corruptBattery.compare();
 return corruptBattery._locked && never._displaySettings.config.chargeTimeoutSeconds==60 &&
  longCharge._displaySettings.config.chargeTimeoutSeconds==60 && shortBattery._displaySettings.config.batteryTimeoutSeconds==60 &&
  corruptBattery._displaySettings.config.batteryTimeoutSeconds==60; // Effective timeouts never mutate the BASE fixture.
}
static_assert(cases(), "fresh callback ticks cannot cause instant lock or duplicate refresh; real minute and wrap work");
"""
        # Source call-chain evidence for CHECK -> Available -> newer activity.
        ota=CPP.split('void CodexMicroView::refreshOta(',1)[1].split('void CodexMicroView::renderOta()',1)[0]
        self.assertIn('_otaPending = false; setPageForDebug(Page::OTA)',ota)
        page=CPP.split('bool CodexMicroView::setPageForDebug(',1)[1].split('lv_obj_t* CodexMicroView::pagePanel',1)[0]
        self.assertIn('_activity = lv_tick_get()',page)
        self.assertLess(update.index('refreshOta(tick)'),start)
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'idle.cpp';source.write_text(harness,encoding='utf8')
            result=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            # Reintroduce the old stale-tick subtraction into the same harness:
            # the new regression must reject it, proving the test detects the bug.
            old=harness.replace('tick = lv_tick_get();','')
            old=old.replace(' && idleElapsed < 0x80000000U','').replace(' && refreshElapsed < 0x80000000U','')
            source.write_text(old,encoding='utf8')
            regression=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
            self.assertNotEqual(regression.returncode,0)
            self.assertIn('static assertion failed',regression.stderr)

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
 bool _rotationFault=false,_locked=false,_suppressed=false,busy=false,_touchTracking=false,_swipeConsumed=false,_settingsAnimating=false;
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
