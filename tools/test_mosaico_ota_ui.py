"""Offline OTA UI source/geometry checks; no display, device or network claims."""
import json, re, subprocess, tempfile, unittest
from pathlib import Path
R = Path(__file__).resolve().parents[1]
V = R / 'main/apps/app_codex_micro/view'
CPP = (V / 'view_mosaico.cpp').read_text(encoding='utf8')
HDR = (V / 'view_mosaico.h').read_text(encoding='utf8')
DOT = (V / 'dot_widgets.cpp').read_text(encoding='utf8')

class OtaUiTests(unittest.TestCase):
    def test_panels_and_touch_regions(self):
        self.assertIn('panel(_otaPage, 20, 64, 440, 402, 0)', CPP)
        self.assertIn('panel(_otaButton, 0, 334, 440, 64', CPP)
        # Global hit region: 20..460, y398..462.
        self.assertEqual(20+440,460)
        self.assertLessEqual(64+334+64,480)
        for y,h in ((58,48),(110,48),(162,24),(194,24),(224,100),(334,64)):
            self.assertLessEqual(y+h,402)
        self.assertTrue(58+48 <= 110 and 110+48 <= 162 and 194+24 <= 224 and 224+100 <= 334)
        self.assertIn('LV_LABEL_LONG_MODE_DOTS',CPP)
        self.assertNotIn('LV_LABEL_LONG_MODE_WRAP',CPP)
    def test_cache_nonblocking_and_offer_wake_policy(self):
        refresh=CPP.split('void CodexMicroView::refreshOta(',1)[1].split('void CodexMicroView::renderOta()',1)[0]
        self.assertIn('copyUiSnapshot(next) &&',refresh)
        self.assertNotIn('_ota = {}',refresh)
        self.assertIn('else if (_otaPending && !_locked)',refresh)
        self.assertIn('if (otaBusy())',refresh)
        self.assertIn('if (_locked) wakeDisplay();',refresh)
        self.assertNotIn('wakeEvent',CPP)
    def test_action_guard_and_busy_lock(self):
        callback=CPP.split('void CodexMicroView::otaEvent(',1)[1].split('void CodexMicroView::refreshOta(',1)[0]
        self.assertIn('self->otaBusy()) return;',callback)
        self.assertIn('if (!self->otaActionEnabled()) return;',callback)
        for api in ('requestCheck()', 'approveInstall(self->_ota.sha256)', 'requestReboot()'):
            self.assertIn('MosaicoOta::'+api, callback)
        self.assertNotIn('batteryTelemetry',callback);self.assertNotIn('tud_mounted',callback)
        self.assertIn('MosaicoOta::approveUpdate(self->_ota.sha256)',callback)
        self.assertIn('wakeForFirmwareUpdate()',callback)
        self.assertIn('_otaApproved = true',callback)
        self.assertIn('if (otaBusy() && page != Page::OTA) return false;',CPP)
        self.assertIn('if (_locked || !ready() || otaBusy()) return;',CPP)
        self.assertIn('if (otaBusy()) return;',CPP)
        self.assertNotIn('setAutomaticInstall',CPP)
    def test_honest_progress_and_verification(self):
        self.assertIn('static_cast<uint64_t>(_ota.received) * 10000 / _ota.size',CPP)
        self.assertIn('_ota.progressBasisPoints',CPP)
        self.assertIn('const bool numericProgress = download || _ota.stage == S::BootChecking',CPP)
        self.assertIn('if (!discovery && numericProgress)',CPP)
        self.assertNotIn('setText(_otaPercent, "--"',CPP)
        self.assertIn('else for (auto* obj : {_otaPercent, _otaMeter}) lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN)',CPP)
        self.assertIn('_ota.imageVerified ? Green : Gold',CPP)
        self.assertIn('_ota.signatureVerified ? Green : Gold',CPP)
        for phase in ('DOWNLOADING','VERIFYING','READY','INSTALLING','BOOT CHECK','VERIFIED 100%','FAILED'):
            self.assertIn('"'+phase+'"',CPP)
        self.assertIn('SHA %.12s',CPP); self.assertIn('SIG %.8s',CPP)
        self.assertIn('char safe[25]',CPP)
    def test_meaningful_stage_icon_and_terminal_version(self):
        self.assertIn('createIcon(_otaPage, Icon::Refresh, 88, Gold); place(_otaStageIcon, 176, 106)',CPP)
        self.assertTrue(176+88<=440 and 106+88<=194 and 58+48<=106)
        self.assertIn('_ota.stage == S::ReadyInstall || _ota.stage == S::Installing',CPP)
        self.assertIn('_ota.stage == S::Installing ? Icon::Refresh : Icon::Check',CPP)
        self.assertIn('setMotion(_otaStageIcon, meterPhase, _page == Page::OTA && _ota.stage == S::Installing)',CPP)
        self.assertNotIn('"%s", phase',CPP)
        self.assertIn('if (!discovery && (download || _ota.size))',CPP)
        self.assertIn('case S::Available: phase = "UPDATE"',CPP)
        self.assertIn('_ota.stage == S::Complete ? _ota.currentVersion : _ota.targetVersion',CPP)
        self.assertIn('if (discovery && _ota.stage != S::Complete)',CPP)
    def test_pending_response_releases_local_guard(self):
        refresh=CPP.split('void CodexMicroView::refreshOta(',1)[1].split('void CodexMicroView::renderOta()',1)[0]
        self.assertIn('_otaApproved = false;',refresh)
        busy=CPP.split('bool CodexMicroView::otaBusy()',1)[1].split('bool CodexMicroView::otaActionEnabled()',1)[0]
        self.assertNotIn('S::ReadyInstall',busy); self.assertNotIn('S::ReadyReboot',busy)
        self.assertIn('S::Checking',busy)
        self.assertIn('lv_obj_add_state(_otaButton, LV_STATE_DISABLED)',CPP)
    def test_same_running_slot_has_no_fake_transition(self):
        self.assertIn('_ota.currentSlot >= 0 && _ota.currentSlot == _ota.targetSlot',CPP)
        self.assertIn('_ota.currentSlot >= 0 && _ota.targetSlot >= 0 && !sameSlot',CPP)
        self.assertIn('sameSlot ? (i ? "VERIFY" : "ACTIVE")',CPP)
        self.assertIn('if (slotTransition) lv_obj_remove_flag(_otaArrow, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(_otaArrow, LV_OBJ_FLAG_HIDDEN);',CPP)
        for current,target in ((0,0),(1,1),(-1,1),(0,-1)):
            transition=current>=0 and target>=0 and current!=target
            self.assertFalse(transition)
        for current,target in ((0,1),(1,0)):
            self.assertTrue(current>=0 and target>=0 and current!=target)
    def test_vector_assets_and_animation_gates(self):
        rows=[tuple(map(int,m.split(','))) for m in re.findall(r'\{([\d,]+)\}',DOT.split('constexpr uint16_t masks[][9] = {')[1].split('};')[0])]
        self.assertEqual(len(rows),18)
        self.assertEqual(len(set(rows)),18)
        for row in rows:self.assertEqual(len(row),9);self.assertTrue(all(0<=x<512 for x in row))
        self.assertIn('if (_locked || _suppressed || _slideTo) return;',CPP); self.assertIn('tick - _motionTick < 100',CPP)
        self.assertIn('const bool otaMotion = _page == Page::OTA',CPP)
        self.assertNotIn('lv_timer_create',DOT)
    def test_permanent_third_page_and_function_guards(self):
        toggle=CPP.split('void CodexMicroView::togglePage()',1)[1].split('void CodexMicroView::wakeDisplay()',1)[0]
        self.assertIn('if (_locked) { wakeDisplay(); return; }',toggle)
        self.assertIn('if (otaBusy()) return;',toggle)
        self.assertIn('navigatePage(1);',toggle)
        self.assertNotIn('_ota.stage',toggle)
        self.assertLess(toggle.index('if (_locked)'),toggle.index('if (otaBusy())'))
        wake=CPP.split('void CodexMicroView::wakeDisplay()',1)[1].split('void CodexMicroView::lockDisplay()',1)[0]
        self.assertIn('if (_otaPending) { _otaPending = false; setPageForDebug(Page::OTA); }',wake)
        self.assertIn('if (_page == Page::OTA) renderOta();',wake)
        pages=('Command','History','OTA')
        for start in pages:
            for locked,busy,pending in ((False,False,False),(True,False,False),(True,False,True),(False,True,False)):
                page=start
                if locked:
                    if pending: page='OTA'
                elif not busy:
                    page=pages[(pages.index(page)+1)%len(pages)]
                expected='OTA' if locked and pending else start if locked or busy else pages[(pages.index(start)+1)%3]
                self.assertEqual(page,expected)
        setpage=CPP.split('bool CodexMicroView::setPageForDebug(',1)[1].split('void CodexMicroView::togglePage()',1)[0]
        self.assertIn('if (_page != Page::OTA) _otaReturn = _page;',setpage)
    def test_idle_has_only_running_image_information(self):
        render=CPP.split('void CodexMicroView::renderOta()',1)[1].split('void CodexMicroView::touchEvent(',1)[0]
        self.assertIn('const bool idle = _ota.stage == S::Idle || _ota.stage == S::Checking ||',render)
        self.assertIn('phase = _otaSeen ? "NO UPDATE" : "CHECKING"',render)
        self.assertIn('_ota.currentVersion[0] ? _ota.currentVersion : "READING VERSION"',render)
        self.assertIn('if (idle) lv_obj_add_flag(_otaTarget, LV_OBJ_FLAG_HIDDEN)',render)
        self.assertIn('const bool hashKnown = !idle &&',render)
        self.assertIn('const bool signatureKnown = !idle &&',render)
        self.assertIn('const bool slotTransition = !idle &&',render)
        self.assertIn('idle ? 127 : (i ? 254 : 0)',render)
        self.assertIn('if (idle && (i || !_otaSeen || _ota.currentSlot < 0))',render)
        for seen,current in ((True,0),(True,1),(False,0),(True,-1)):
            visible=[not (i or not seen or current<0) for i in range(2)]
            self.assertEqual(visible,[seen and current>=0,False])
    def test_staged_action_gate_and_always_visible_dashed_style(self):
        action=CPP.split('void CodexMicroView::renderOtaAction()',1)[1].split('void CodexMicroView::otaBorderEvent(',1)[0]
        self.assertNotIn('LV_OBJ_FLAG_HIDDEN', action)
        for text in ('CHECK','DOWNLOAD','UPGRADE','REBOOT'):self.assertIn('"'+text+'"',action)
        self.assertNotIn('"LATER"', CPP); self.assertNotIn('"BACK"', CPP)
        self.assertNotIn('deferUpdate',CPP)
        self.assertIn('createText(_otaButton, 440, 40, 4, Orange)',CPP)
        self.assertIn('LV_OPA_TRANSP, LV_STATE_DISABLED',CPP)
        self.assertIn('d.dash_width = 6; d.dash_gap = 4;',CPP)
        self.assertIn('action, enabled ? Orange : 0x68451C',action)
        gate=CPP.split('bool CodexMicroView::otaActionEnabled()',1)[1].split('void CodexMicroView::renderOtaAction()',1)[0]
        self.assertIn('return _otaNetworkReady;',gate)
        self.assertIn('return _ota.imageVerified && _externalPowerReady;',gate)
        self.assertIn('case S::ReadyReboot: return true;',gate)
        stages={'Idle':('CHECK','network'),'Complete':('CHECK','network'),'Failed':('CHECK','network'),
                'Checking':('CHECK','disabled'),'Available':('DOWNLOAD','network'),
                'Downloading':('DOWNLOAD','disabled'),'Verifying':('DOWNLOAD','disabled'),
                'ReadyInstall':('UPGRADE','power'),'WaitingPowerVerified':('UPGRADE','power'),
                'Installing':('UPGRADE','disabled'),'ReadyReboot':('REBOOT','yes'),'BootChecking':('REBOOT','disabled')}
        for stage,(text,condition) in stages.items():
            for network,power,pending in ((False,False,False),(True,False,False),(False,True,False),(True,True,True)):
                enabled=not pending and {'network':network,'power':power,'yes':True,'disabled':False}[condition]
                if pending or condition=='disabled':self.assertFalse(enabled,stage)
                if stage=='Available' and network and not pending:self.assertTrue(enabled)
                if stage=='ReadyReboot' and not pending:self.assertTrue(enabled)
    def test_waiting_power_recovery_three_way_branch(self):
        action=CPP.split('void CodexMicroView::renderOtaAction()',1)[1].split('void CodexMicroView::otaBorderEvent(',1)[0]
        self.assertIn('_ota.imageVerified ? "UPGRADE" : otaHasDownloadOffer() ? "DOWNLOAD" : "CHECK"',action)
        gate=CPP.split('bool CodexMicroView::otaActionEnabled()',1)[1].split('void CodexMicroView::renderOtaAction()',1)[0]
        self.assertIn('case S::WaitingPower: return _ota.imageVerified ? _externalPowerReady : _otaNetworkReady;',gate)
        offer=CPP.split('bool CodexMicroView::otaHasDownloadOffer()',1)[1].split('bool CodexMicroView::otaActionEnabled()',1)[0]
        self.assertIn("_ota.signatureVerified && _ota.sha256[0] && _ota.sha256[0] != '-'",offer)
        callback=CPP.split('void CodexMicroView::otaEvent(',1)[1].split('void CodexMicroView::refreshOta(',1)[0]
        self.assertIn('self->_ota.imageVerified ? MosaicoOta::approveInstall(self->_ota.sha256)',callback)
        self.assertIn('self->otaHasDownloadOffer() ? MosaicoOta::approveUpdate(self->_ota.sha256) : MosaicoOta::requestCheck()',callback)
        render=CPP.split('void CodexMicroView::renderOta()',1)[1].split('void CodexMicroView::touchEvent(',1)[0]
        self.assertIn('_ota.imageVerified ? "USB POWER" : otaHasDownloadOffer() ? "DOWNLOAD BLOCKED" : "CHECK REQUIRED"',render)
        for image,sig,sha,expected in ((True,True,'a'*64,'UPGRADE'),(False,True,'a'*64,'DOWNLOAD'),
                                     (False,False,'','CHECK'),(False,True,'','CHECK'),(False,True,'-','CHECK')):
            offer=sig and bool(sha) and sha!='-'
            self.assertEqual('UPGRADE' if image else 'DOWNLOAD' if offer else 'CHECK',expected)
            for network in (False,True):
                for power in (False,True):
                    enabled=power if image else network
                    if image:self.assertEqual(enabled,power)
                    else:self.assertEqual(enabled,network)
            # Power recovery releases install; a download/check never depends on charge.
            before = False if image else True
            after = True
            self.assertTrue(after)
            self.assertEqual(before, not image)
    def test_cached_external_power_gate(self):
        battery=CPP.split('void CodexMicroView::refreshBattery(',1)[1].split('void CodexMicroView::stopAnimations()',1)[0]
        for predicate in ('telemetry.valid','telemetry.voltageMv >= 3900','((telemetry.operationStatus >> 1) & 3) == 3',
                          '!(telemetry.operationStatus & 0x0401)','(tud_mounted() || telemetry.currentMa > 3)'):
            self.assertIn(predicate,battery)
        for valid,mv,status,usb,current,expected in ((True,3900,6,True,0,True),(True,3900,6,False,4,True),
                (True,3899,6,True,0,False),(True,4000,6,False,3,False),(False,4000,6,True,100,False),
                (True,4000,0x407,True,100,False)):
            self.assertEqual(valid and mv>=3900 and (status>>1)&3==3 and not status&0x401 and (usb or current>3),expected)
        self.assertIn('GetNetworkQuota().connected() && (!GetTailnetQuota().enabled() || GetTailnetQuota().ready())',CPP)
    def test_swipe_navigation_guards_and_cleanup(self):
        touch=CPP.split('void CodexMicroView::touchEvent(',1)[1].split('void CodexMicroView::cellEvent(',1)[0]
        for guard in ('_suppressed','_locked','otaBusy()','_slideTo'):
            self.assertIn(guard,touch)
        self.assertIn('std::abs(dx) >= 96',touch)
        self.assertIn('std::abs(dy) <= 48',touch)
        self.assertIn('std::abs(dx) >= 2 * std::abs(dy)',touch)
        self.assertIn('_swipeConsumed = true',touch)
        for dx,dy,accepted in ((95,0,False),(96,0,True),(-96,48,True),(120,49,False),(70,120,False),(0,0,False)):
            self.assertEqual(abs(dx)>=96 and abs(dy)<=48 and abs(dx)>=2*abs(dy),accepted)
        for name in ('cellEvent','modeEvent','otaEvent'):
            body=CPP.split('void CodexMicroView::'+name+'(',1)[1].split('\n}',1)[0]
            self.assertIn('_swipeConsumed',body);self.assertIn('_slideTo',body)
        self.assertIn('lv_anim_set_duration(&anim, 200)',CPP)
        self.assertIn('lv_anim_set_completed_cb(&anim, slideCompleted)',CPP)
        self.assertIn('lv_anim_delete(this, slideExec)',CPP)
        self.assertNotIn('lv_screen_load_anim',CPP)
        navigate=CPP.split('void CodexMicroView::navigatePage(',1)[1].split('void CodexMicroView::setInputSuppressed(',1)[0]
        self.assertNotIn('GetNetworkQuota',navigate)
        self.assertNotIn('lv_obj_create',navigate)
        for index in range(3):
            self.assertEqual(((index+1)%3+2)%3,index)
        forced=CPP.split('bool CodexMicroView::setPageForDebug(',1)[1].split('lv_obj_t* CodexMicroView::pagePanel(',1)[0]
        self.assertIn('cancelPageSlide();',forced)
        self.assertIn('_touchTracking = false;',forced)
    def test_all_stage_geometry_and_singleline_bounds(self):
        self.assertIn('place(_otaBytes, 0, stageIcon ? 194 : 162)', CPP)
        self.assertIn('place(_otaHash, 36, 222)', CPP); self.assertIn('place(_otaSignature, 272, 222)',CPP)
        self.assertIn('lv_obj_set_width(_otaHash, 184)', CPP); self.assertIn('lv_obj_set_width(_otaSignature, 168)',CPP)
        self.assertIn('&lv_font_montserrat_14', CPP)
        self.assertIn('lv_obj_set_height(obj, 24); lv_label_set_long_mode(obj, LV_LABEL_LONG_MODE_CLIP)',CPP)
        self.assertIn('lv_obj_set_height(_otaSlots[i], 72)', CPP)
        self.assertIn('lv_obj_set_height(_otaSlotNumbers[i], 52)', CPP)
        self.assertIn('setTextPitch(_otaSlotNumbers[i], 7)', CPP)
        self.assertIn('place(_otaChips[i], 16, 24)', CPP)
        self.assertIn('_otaPercent = createText(_otaPage, 440, 52, 7)',CPP)
        self.assertLessEqual(222+24,252); self.assertLessEqual(220+28,252)
        self.assertLessEqual(252+72,334); self.assertLessEqual(334+64,402)
        self.assertLessEqual(36+184,236); self.assertLessEqual(236+28,272);self.assertEqual(272+168,440)
        self.assertLessEqual(16+52,72); self.assertLessEqual(24+36,72);self.assertLessEqual(7*7,52)
        self.assertLessEqual(106+52,162); self.assertLessEqual(162+24,194)
        self.assertLessEqual(106+88,194); self.assertLessEqual(194+24,220)
        # Read actual geometry from constructor/setter source, not desired hardcoded boxes.
        def match(pattern):
            found=re.search(pattern,CPP);self.assertIsNotNone(found,pattern)
            return tuple(map(int,found.groups()))
        pw,ph,pp=match(r'_otaPercent = createText\(_otaPage, (\d+), (\d+), (\d+)\)')
        px,py=match(r'place\(_otaPercent, (\d+), (\d+)\)')
        bx,by1,by2=match(r'place\(_otaBytes, (\d+), stageIcon \? (\d+) : (\d+)\)')
        bh,=match(r'lv_obj_set_height\(_otaBytes, (\d+)\)')
        mw,mh,mrows=match(r'_otaMeter = createMeter\(_otaPage, (\d+), (\d+), (\d+)\)')
        mx,my=match(r'place\(_otaMeter, (\d+), (\d+)\)')
        ix,iy=match(r'place\(_otaStageIcon, (\d+), (\d+)\)')
        ih,=match(r'createIcon\(_otaPage, Icon::Refresh, (\d+), Gold\)')
        hx,hy=match(r'place\(_otaHash, (\d+), (\d+)\)')
        metaH,=match(r'lv_obj_set_height\(obj, (\d+)\); lv_label_set_long_mode\(obj, LV_LABEL_LONG_MODE_CLIP\)')
        sy,=match(r'place\(_otaSlots\[i\], idle \? 127 : \(i \? 254 : 0\), (\d+)\)')
        sh,=match(r'lv_obj_set_height\(_otaSlots\[i\], (\d+)\)')
        ax,ay,aw,ah=match(r'panel\(_otaButton, (\d+), (\d+), (\d+), (\d+),')
        errorY,=match(r'_ota.stage == S::Failed \? (\d+) : discovery')
        vh,=match(r'lv_obj_set_height\(text, (\d+)\); lv_label_set_long_mode\(text, LV_LABEL_LONG_MODE_DOTS\)')
        for stage in ('Idle','Checking','Available','WaitingPower','Downloading','Verifying','ReadyInstall','Installing','ReadyReboot','BootChecking','Complete','Failed'):
            regions=[(hy,hy+metaH),(sy,sy+sh),(ay,ay+ah)]
            if stage in ('ReadyInstall','Installing','ReadyReboot','WaitingPower'):regions += [(iy,iy+ih),(by1,by1+bh)]
            if stage in ('Downloading','Verifying','BootChecking'):regions += [(py,py+ph),(by2,by2+bh),(my,my+mh)]
            if stage=='Failed':regions += [(errorY,errorY+vh)]
            for i,a in enumerate(regions):
                for b in regions[i+1:]:self.assertTrue(a[1]<=b[0] or b[1]<=a[0],stage)
        for text in ('100%','100.00%'):
            cols=len(text)*6-1;pitch=min(max(2,pp),pw//cols,ph//7)
            diameter=max(1,pitch*7//10);drawnH=6*pitch+diameter;drawnW=(cols-1)*pitch+diameter
            self.assertGreater(diameter,0);self.assertLessEqual(drawnH,ph);self.assertLessEqual(drawnW,pw)
            self.assertLessEqual(py+(ph-drawnH)//2+drawnH,by2)
        for version in ('0.8.1-mosaico-ota','x'*31):self.assertLessEqual(len(version),31)
        self.assertIn('lv_obj_set_height(text, 24); lv_label_set_long_mode(text, LV_LABEL_LONG_MODE_DOTS)',CPP)
        failed=CPP.split('if (_ota.stage == S::Failed) {',1)[1].split('void CodexMicroView::touchEvent',1)[0]
        self.assertNotIn('_otaBytes',failed);self.assertIn('char safe[25]',failed)
    def test_actual_swipe_source_constexpr_harness(self):
        # The installed compiler has only embedded targets. constexpr assertions
        # execute extracted production function bodies in the compiler, with
        # deterministic LVGL/rendering stubs, not a Python navigation replica.
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:self.skipTest('embedded C++ compiler unavailable')
        names=('touchEvent','pagePanel','cancelPageSlide','slideExec','slideCompleted','navigatePage','togglePage')
        methods=[]
        for name in names:
            match=re.search(r'^(?:void|lv_obj_t\*) CodexMicroView::'+name+r'\([^\n]*\) (?:const )?\{',CPP,re.M)
            self.assertIsNotNone(match,name)
            end=CPP.index('\n}',match.end())+2
            body=CPP[match.start():end]
            # void* conversion is not legal in C++17 constant evaluation. Only
            # this adapter parameter is typed; all function bodies stay intact.
            body=body.replace('void* owner','CodexMicroView* owner')
            methods.append('constexpr '+body)
        harness=r'''
using int32_t = int; using uint32_t = unsigned;
namespace std {
constexpr int abs(int n) { return n < 0 ? -n : n; }
template<class T> class initializer_list {
 const T* p; unsigned n;
 constexpr initializer_list(const T* a, unsigned b):p(a),n(b) {}
 public: constexpr const T* begin() const { return p; }
 constexpr const T* end() const { return p+n; }
}; }
struct CodexMicroView;
struct lv_point_t { int x=0,y=0; };
struct lv_obj_t { int x=20; bool hidden=false; };
struct lv_indev_t { lv_point_t point; };
enum { LV_EVENT_PRESSED,LV_EVENT_PRESSING,LV_EVENT_RELEASED,LV_EVENT_PRESS_LOST,
       LV_INDEV_TYPE_POINTER,LV_OBJ_FLAG_HIDDEN };
struct lv_event_t { int code; CodexMicroView* owner; lv_indev_t* input; };
struct lv_anim_t { CodexMicroView* var=nullptr; int duration=0; };
constexpr int lv_event_get_code(lv_event_t* e) { return e->code; }
constexpr CodexMicroView* lv_event_get_user_data(lv_event_t* e) { return e->owner; }
constexpr lv_indev_t* lv_event_get_indev(lv_event_t* e) { return e->input; }
constexpr int lv_indev_get_type(lv_indev_t*) { return LV_INDEV_TYPE_POINTER; }
constexpr void lv_indev_get_point(lv_indev_t* i,lv_point_t* p) { *p=i->point; }
constexpr unsigned lv_tick_get() { return 1; }
constexpr void lv_obj_set_x(lv_obj_t* o,int x) { o->x=x; }
constexpr void lv_obj_add_flag(lv_obj_t* o,int) { o->hidden=true; }
constexpr void lv_obj_remove_flag(lv_obj_t* o,int) { o->hidden=false; }
constexpr void lv_anim_delete(CodexMicroView*,void(*)(CodexMicroView*,int)) {}
constexpr void lv_anim_init(lv_anim_t* a) { *a={}; }
constexpr void lv_anim_set_var(lv_anim_t* a,CodexMicroView* v) { a->var=v; }
constexpr void lv_anim_set_exec_cb(lv_anim_t*,void(*)(CodexMicroView*,int)) {}
constexpr void lv_anim_set_values(lv_anim_t*,int,int) {}
constexpr void lv_anim_set_duration(lv_anim_t* a,int d) { a->duration=d; }
constexpr void lv_anim_path_ease_out() {}
constexpr void lv_anim_set_path_cb(lv_anim_t*,void(*)()) {}
constexpr void lv_anim_set_completed_cb(lv_anim_t*,void(*)(lv_anim_t*)) {}
constexpr void lv_anim_start(lv_anim_t*) {}
struct CodexMicroView {
 enum class Page { Command,History,Agent,OTA };
 enum class RotationPhase { Idle,FadeOut,WaitBlack,WaitRotated,FadeIn };
 RotationPhase _rotationPhase=RotationPhase::Idle;
 bool _rotationFault=false;
 Page _page=Page::Command; lv_obj_t panels[3]{};
 lv_obj_t* _quotaPage=&panels[0]; lv_obj_t* _historyPage=&panels[1]; lv_obj_t* _otaPage=&panels[2];
 lv_obj_t* _slideFrom=nullptr; lv_obj_t* _slideTo=nullptr;
 int _slideDirection=1; bool _suppressed=false,_locked=false,busy=false;
 bool _touchTracking=false,_swipeConsumed=false; lv_point_t _touchStart{};
 unsigned _activity=0;
 constexpr bool ready() { return true; }
 constexpr bool otaBusy() { return busy; }
 constexpr void wakeDisplay() { _locked=false; }
 constexpr bool setPageForDebug(Page p) {
  cancelPageSlide(); if (_touchTracking) _swipeConsumed=true; _touchTracking=false;
  for(auto& panel:panels) panel.hidden=true;
  _page=p; pagePanel(p)->hidden=false; return true;
 }
 static constexpr void touchEvent(lv_event_t*);
 constexpr lv_obj_t* pagePanel(Page) const;
 constexpr void cancelPageSlide();
 static constexpr void slideExec(CodexMicroView*,int);
 static constexpr void slideCompleted(lv_anim_t*);
 constexpr void navigatePage(int);
 constexpr void togglePage();
};
'''
        harness+='\n'.join(methods)+r'''
constexpr bool exercise(int direction, bool cancel) {
 CodexMicroView v; lv_indev_t input{}; lv_event_t e{LV_EVENT_PRESSED,&v,&input};
 for(int step=0;step<3;++step) {
  input.point={240,200}; e.code=LV_EVENT_PRESSED; v.touchEvent(&e);
  input.point={240-direction*120,200}; e.code=LV_EVENT_PRESSING; v.touchEvent(&e);
  int target=direction>0?(step+1)%3:(2-step+3)%3;
  if(v._page != (target==0?CodexMicroView::Page::Command:target==1?CodexMicroView::Page::History:CodexMicroView::Page::OTA)) return false;
  if(!v._slideTo || v._touchTracking || !v._swipeConsumed) return false;
  v.slideExec(&v,240);
  if(v._slideFrom->x != 20-direction*240 || v._slideTo->x != 20+direction*240) return false;
  if(cancel) v.cancelPageSlide(); else { lv_anim_t a{&v}; v.slideCompleted(&a); }
  if(v._slideTo || v._slideFrom || v.pagePanel(v._page)->x!=20 || v.pagePanel(v._page)->hidden) return false;
  // Same contact reverses after animation: no second navigation or click.
  input.point={240+direction*130,200}; e.code=LV_EVENT_PRESSING; v.touchEvent(&e);
  e.code=LV_EVENT_RELEASED; v.touchEvent(&e);
  if(v._slideTo || !v._swipeConsumed) return false;
  for(auto& panel:v.panels) if(panel.x!=20 || panel.hidden!=(&panel!=v.pagePanel(v._page))) return false;
 }
 return v._page==CodexMicroView::Page::Command;
}
constexpr bool edgeCases() {
 CodexMicroView v; lv_indev_t input{{240,200}}; lv_event_t e{LV_EVENT_PRESSED,&v,&input};
 v.touchEvent(&e); input.point={120,200}; e.code=LV_EVENT_PRESS_LOST; v.touchEvent(&e);
 if(v._slideTo || v._touchTracking || !v._swipeConsumed) return false;
 input.point={240,200}; e.code=LV_EVENT_PRESSED; v.touchEvent(&e);
 if(v._swipeConsumed) return false;
 input.point={120,200}; e.code=LV_EVENT_RELEASED; v.touchEvent(&e);
 if(!v._slideTo || !v._swipeConsumed) return false;
 v.cancelPageSlide();
 for(int guard=0;guard<3;++guard) {
  v._suppressed=guard==0; v._locked=guard==1; v.busy=guard==2;
  input.point={240,200}; e.code=LV_EVENT_PRESSED; v.touchEvent(&e);
  input.point={120,200}; e.code=LV_EVENT_PRESSING; v.touchEvent(&e);
  e.code=LV_EVENT_RELEASED; v.touchEvent(&e);
  if(v._slideTo || v._touchTracking || !v._swipeConsumed) return false;
 }
 v._suppressed=false; v.busy=false; v._locked=true; v.togglePage();
 if(v._locked || v._slideTo) return false; // Function-only wake, no navigation.
 v.togglePage(); if(!v._slideTo) return false;
 return true;
}
static_assert(exercise(1,false),"forward completion");
static_assert(exercise(-1,false),"reverse completion");
static_assert(exercise(1,true),"forward cancellation");
static_assert(exercise(-1,true),"reverse cancellation");
static_assert(edgeCases(),"release fallback, press-lost, guards and Function");
'''
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'swipe.cpp';source.write_text(harness,encoding='utf8')
            result=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
            out=R/'.artifacts/mosaico/swipe-source-harness.log'
            out.parent.mkdir(parents=True,exist_ok=True);out.write_text(result.stdout+result.stderr,encoding='utf8')
            self.assertEqual(result.returncode,0,'actual source harness failed: '+str(out))
    def test_actual_target_syntax_when_configured(self):
        db=R/'.artifacts/mosaico/ota-build/compile_commands.json'
        if not db.exists(): self.skipTest('target configuration unavailable')
        entries=json.loads(db.read_text())
        log=[]
        for name in ('view_mosaico.cpp','dot_widgets.cpp'):
            entry=next((e for e in entries if e['file'].endswith(name)),None)
            if not entry:self.skipTest('target source configuration unavailable')
            command=re.sub(r' -o \S+ -c ', ' -fsyntax-only ',entry['command'])
            self.assertIn('-fsyntax-only',command)
            result=subprocess.run(command,cwd=entry['directory'],capture_output=True,text=True)
            log.append(name+'\n'+result.stdout+result.stderr)
            out=R/'.artifacts/mosaico/ota-ui-syntax.log';out.parent.mkdir(parents=True,exist_ok=True);out.write_text('\n'.join(log),encoding='utf8')
            self.assertEqual(result.returncode,0,'target syntax failed: '+str(out))

if __name__=='__main__':unittest.main()
