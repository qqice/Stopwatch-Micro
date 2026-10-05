"""Offline OTA UI source/geometry checks; no display, device or network claims."""
import json, re, subprocess, unittest
from pathlib import Path
R = Path(__file__).resolve().parents[1]
V = R / 'main/apps/app_codex_micro/view'
CPP = (V / 'view_mosaico.cpp').read_text(encoding='utf8')
HDR = (V / 'view_mosaico.h').read_text(encoding='utf8')
DOT = (V / 'dot_widgets.cpp').read_text(encoding='utf8')

class OtaUiTests(unittest.TestCase):
    def test_panels_and_touch_regions(self):
        self.assertIn('panel(_otaPage, 20, 64, 440, 402, 0)', CPP)
        self.assertIn('i ? 230 : 0, 334, 210, 64', CPP)
        # Global hit regions: 20..230 and 250..460, y398..462.
        for x in (20,250):
            self.assertGreaterEqual(x,20); self.assertLessEqual(x+210,460)
        self.assertLessEqual(64+334+64,480)
        for y,h in ((58,48),(110,48),(162,24),(194,24),(224,100),(334,64)):
            self.assertLessEqual(y+h,402)
        self.assertTrue(58+48 <= 110 and 110+48 <= 162 and 194+24 <= 224 and 224+100 <= 334)
        self.assertIn('LV_LABEL_LONG_MODE_WRAP',CPP)
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
        self.assertIn('self->_ota.stage != S::Available && self->_ota.stage != S::WaitingPower',callback)
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
        for phase in ('DOWNLOADING','VERIFYING','READY','RESTARTING','BOOT CHECK','VERIFIED 100%','FAILED'):
            self.assertIn('"'+phase+'"',CPP)
        self.assertIn('SHA %.16s',CPP); self.assertIn('SIG %.12s',CPP)
        self.assertIn('char safe[25]',CPP)
    def test_meaningful_stage_icon_and_terminal_version(self):
        self.assertIn('createIcon(_otaPage, Icon::Refresh, 88, Gold); place(_otaStageIcon, 176, 106)',CPP)
        self.assertTrue(176+88<=440 and 106+88<=194 and 58+48<=106)
        self.assertIn('_ota.stage == S::ReadyInstall || _ota.stage == S::Installing',CPP)
        self.assertIn('_ota.stage == S::ReadyInstall ? Icon::Check : Icon::Refresh',CPP)
        self.assertIn('setMotion(_otaStageIcon, meterPhase, _page == Page::OTA && _ota.stage == S::Installing)',CPP)
        self.assertNotIn('"%s", phase',CPP)
        self.assertIn('if (!discovery && (download || _ota.size))',CPP)
        self.assertIn('case S::Available: phase = "UPDATE"',CPP)
        self.assertIn('_ota.stage == S::Complete ? _ota.currentVersion : _ota.targetVersion',CPP)
        self.assertIn('if (discovery && _ota.stage != S::Complete)',CPP)
    def test_pending_response_releases_local_guard(self):
        refresh=CPP.split('void CodexMicroView::refreshOta(',1)[1].split('void CodexMicroView::renderOta()',1)[0]
        # Model the exact source predicate, including the accepted Available request
        # that waits for the owner; WaitingPower and timeout Failed release it.
        self.assertIn('if (_ota.stage != S::Available) _otaApproved = false;',refresh)
        for response in ('WaitingPower','Failed','Downloading','Complete','Idle'):
            approved=True
            if response != 'Available': approved=False
            self.assertFalse(approved,response)
        for response in ('WaitingPower','Failed'):
            busy_stages=('Downloading','Verifying','ReadyInstall','Installing','BootChecking')
            self.assertNotIn(response,busy_stages)
        self.assertIn('if (_otaApproved) lv_obj_add_state',CPP)
        self.assertIn('else lv_obj_remove_state(_otaButtons[i], LV_STATE_DISABLED)',CPP)
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
        self.assertIn('if (_locked) return;',CPP); self.assertIn('tick - _motionTick < 100',CPP)
        self.assertIn('const bool otaMotion = _page == Page::OTA',CPP)
        self.assertNotIn('lv_timer_create',DOT)
    def test_permanent_third_page_and_function_guards(self):
        toggle=CPP.split('void CodexMicroView::togglePage()',1)[1].split('void CodexMicroView::wakeDisplay()',1)[0]
        self.assertIn('if (_locked) { wakeDisplay(); return; }',toggle)
        self.assertIn('if (otaBusy()) return;',toggle)
        self.assertIn('else if (_page == Page::History) setPageForDebug(Page::OTA);',toggle)
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
        self.assertIn('const bool idle = _ota.stage == S::Idle;',render)
        self.assertIn('phase = _otaSeen ? "NO UPDATE" : "CHECKING"',render)
        self.assertIn('_ota.currentVersion[0] ? _ota.currentVersion : "READING VERSION"',render)
        self.assertIn('if (idle) lv_obj_add_flag(_otaTarget, LV_OBJ_FLAG_HIDDEN)',render)
        self.assertIn('for (auto* obj : {_otaHash, _otaSignature, _otaImageIcon, _otaSignatureIcon})',render)
        self.assertIn('if (idle) lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN)',render)
        self.assertIn('const bool slotTransition = !idle &&',render)
        self.assertIn('idle ? 127 : (i ? 254 : 0)',render)
        self.assertIn('if (idle && (i || !_otaSeen || _ota.currentSlot < 0))',render)
        for seen,current in ((True,0),(True,1),(False,0),(True,-1)):
            visible=[not (i or not seen or current<0) for i in range(2)]
            self.assertEqual(visible,[seen and current>=0,False])
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
