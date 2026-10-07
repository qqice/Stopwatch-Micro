"""Bounded locked BLE receipt window; pure C++ execution, no hardware/build."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
COMPILER=Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
MODULE=(ROOT/'main/host/mosaico_session_monitor.cpp').read_text()
BLE=(ROOT/'main/hal/ble/codex_micro_ble.cpp').read_text()

class LockedRefreshTests(unittest.TestCase):
    def test_actual_cpp_window_receipt_and_last_change(self):
        code=r'''
#include "main/host/mosaico_session_model.h"
using namespace MosaicoSessions;
struct State {
 uint8_t knownMask=0;
 uint32_t connectionGeneration=0,threadStatusReceiptSequence=0,lastThreadStatusReceiptMs=0;
 std::array<uint32_t,6> lastThreadStatusMs{},threadStatusCompleteReceiptSequence{},threadStatusCompleteReceiptMs{};
};
constexpr bool test() {
 State state; clearKnown(state,10);
 for(unsigned i=0;i<6;++i) markKnown(state,i,500);
 markReceipt(state,63,63,500);
 LockedWindow w;
 if(!w.start(1000,10,state.threadStatusCompleteReceiptSequence)||w.count!=1||w.revision!=1) return false;
 // Old full-known cache and handshake are not a fresh full reply.
 w.observe(10,63,state.threadStatusCompleteReceiptSequence,state.threadStatusCompleteReceiptMs,true,true);
 if(w.freshMask) return false;
 // A same-value partial reply updates receipt, but not complete proof or LAST.
 markKnown(state,0,1100); markReceipt(state,1,0,1100);
 w.observe(10,63,state.threadStatusCompleteReceiptSequence,state.threadStatusCompleteReceiptMs,true,true);
 if(w.freshMask || state.lastThreadStatusMs[0]!=500 || state.lastThreadStatusReceiptMs!=1100) return false;
 markReceipt(state,1,1,1200);
 w.observe(10,63,state.threadStatusCompleteReceiptSequence,state.threadStatusCompleteReceiptMs,true,true);
 if(w.freshMask!=1) return false;
 markReceipt(state,62,62,1500);
 w.observe(10,63,state.threadStatusCompleteReceiptSequence,state.threadStatusCompleteReceiptMs,true,true);
 if(w.freshMask!=63) return false;
 w.finish(1500);
 if(w.active||w.timedOut||w.endedMs!=1500||w.revision!=2||w.count!=1) return false;
 if(w.start(60999,10,state.threadStatusCompleteReceiptSequence)) return false;
 if(!w.start(61000,10,state.threadStatusCompleteReceiptSequence)||w.start(61100,10,state.threadStatusCompleteReceiptSequence)) return false;
 // Same generation requires genuinely new per-slot full receipts.
 w.observe(10,63,state.threadStatusCompleteReceiptSequence,state.threadStatusCompleteReceiptMs,true,true);
 if(w.freshMask || w.expired(68999)||!w.expired(69000)) return false;
 w.finish(69000,true,0x107);
 if(!w.timedOut||w.error!=0x107||w.freshMask) return false;
 if(!w.start(121000,10,state.threadStatusCompleteReceiptSequence)) return false;
 markReceipt(state,3,3,121100);
 w.observe(10,63,state.threadStatusCompleteReceiptSequence,state.threadStatusCompleteReceiptMs,true,true);
 if(w.freshMask!=3) return false;
 // New host/generation cannot combine previous host slots.
 clearKnown(state,11);
 w.observe(11,0,state.threadStatusCompleteReceiptSequence,state.threadStatusCompleteReceiptMs,true,false);
 if(w.freshMask) return false;
 markKnown(state,4,121200); markReceipt(state,16,16,121200);
 w.observe(11,16,state.threadStatusCompleteReceiptSequence,state.threadStatusCompleteReceiptMs,true,true);
 if(w.freshMask!=16) return false;
 // Post-deadline full updates cannot fill remaining slots.
 for(unsigned i=0;i<6;++i) markKnown(state,i,129000);
 markReceipt(state,63,63,129000);
 w.observe(11,63,state.threadStatusCompleteReceiptSequence,state.threadStatusCompleteReceiptMs,true,true);
 if(w.freshMask!=16) return false;
 w.finish(129000,true,0x107);
 if(!w.timedOut||w.active) return false;
 // Wake/OTA cancellation closes immediately without freshness/TTL invention.
 if(!w.start(181000,11,state.threadStatusCompleteReceiptSequence)) return false;
 w.finish(181001,false,0); if(w.active||w.timedOut) return false;
 if(!w.start(241000,11,state.threadStatusCompleteReceiptSequence)) return false;
 w.finish(241001,false,-1); if(w.active||w.error!=-1) return false;
 // Wrap-safe start interval and deadline arithmetic.
 LockedWindow wrap; std::array<uint32_t,6> zero{};
 if(!wrap.start(0xfffffff0U,0,zero)||wrap.expired(0x1f2fU)||!wrap.expired(0x1f30U)) return false;
 return !radioAllowed(true,true,true,true,true) && radioAllowed(true,true,true,false,false);
}
static_assert(test());
'''
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'locked.cpp'; source.write_text(code)
            result=subprocess.run([str(COMPILER),'-std=c++17','-fsyntax-only','-I'+str(ROOT),str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/locked-refresh-constexpr.log'; log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_atomic_request_minute_window_and_cancellation_source(self):
        request=MODULE.split('void requestLockedRefresh()',1)[1].split('bool enabled()',1)[0]
        self.assertIn('lockedRefreshRequested.store(true)',request)
        for forbidden in ('GetCodexMicroBle','nvs_','GetHAL','esp_ble_'): self.assertNotIn(forbidden,request)
        self.assertIn('if (!locked || otaBusy || failed)',MODULE)
        self.assertIn('asked && locked && !otaBusy && !failed && hidReady',MODULE)
        self.assertIn('requested.load() || window.active, locked && !window.active, otaBusy',MODULE)
        self.assertIn('if (window.freshMask == 0x3f) window.finish',MODULE)
        self.assertIn('window.expired(now)',MODULE)
        self.assertIn('if (!value && !lockedRefreshActive.load()) effective.store(false)',MODULE)

    def test_cache_only_proven_full_slots_and_actual_capture(self):
        self.assertIn('lockedCache.knownMask = window.freshMask;',MODULE)
        self.assertIn('state.threadStatusCompleteReceiptMs[slot]',MODULE)
        self.assertIn('state.lastThreadStatusReceiptMs - window.startedMs < LockedWindow::DurationMs',MODULE)
        self.assertIn('state.connected && state.connectionGeneration != lockedCache.connectionGeneration',MODULE)
        self.assertIn('lockedCapturedMs = before.lastThreadStatusReceiptMs',MODULE)
        self.assertNotIn('lockedCapturedMs = now;',MODULE)
        self.assertNotIn('nvs_',MODULE)

    def test_complete_receipt_separate_from_last_and_handshake(self):
        update=BLE.split('bool CodexMicroBle::updateThreadLighting(',1)[1].split('void CodexMicroBle::updateLightingSide(',1)[0]
        self.assertIn('MosaicoSessions::markReceipt(_state, receiptMask, completeMask, now)',update)
        self.assertIn('MosaicoSessions::markKnown(_state, slot, now, semanticChanged)',update)
        self.assertIn('completeMask = 0, statusMask = 0',update)
        rpc=BLE.split('bool CodexMicroBle::markProtocolReady(',1)[1].split('void CodexMicroBle::recoverHalfOpenConnection(',1)[0]
        self.assertNotIn('markReceipt',rpc)

    def test_network_may_close_but_not_cancel_valid_locked_window(self):
        network=(ROOT/'main/host/network_quota.cpp').read_text()
        self.assertIn('if (!MosaicoSessions::enabled() || MosaicoOta::busy() || MosaicoOta::healthPending())',network)
        self.assertNotIn('if (locked || !MosaicoSessions::enabled()',network)
        self.assertIn('GetCodexMicroBle().requestRadioIdle(true);',network)
        header=(ROOT/'main/host/network_quota.h').read_text()
        getter=header.split('bool displayLocked()',1)[1].split('}',1)[0]
        self.assertIn('_locked.load()',getter)
        self.assertNotIn('_power_profile',getter)
        self.assertNotIn('MosaicoOta',getter)
        main=(ROOT/'main/main.cpp').read_text()
        self.assertIn('MosaicoSessions::service(GetNetworkQuota().displayLocked()',main)
        self.assertNotIn('MosaicoSessions::service(GetNetworkQuota().idleLocked()',main)

    def test_actual_owner_admission_fragment_retains_cooldown_request(self):
        admission=MODULE.split('if (asked && locked && !otaBusy && !failed && hidReady)',1)[1].split('\n    wasLocked',1)[0]
        # Exercise the actual owner admission branch with no RTOS/controller.
        code=r'''
#include "main/host/mosaico_session_model.h"
using MosaicoSessions::LockedWindow;
struct Request { bool pending=false; constexpr void store(bool p) { pending=p; } };
struct Before { uint32_t connectionGeneration=1; std::array<uint32_t,6> threadStatusCompleteReceiptSequence{}; };
constexpr void ownerAdmission(LockedWindow& window, Request& lockedRefreshRequested, const Before& before, uint32_t now) ADMISSION
constexpr bool test() {
 Before before;
 for(uint32_t delay : {0U,20U,100U}) {
  LockedWindow window; Request request;
  ownerAdmission(window,request,before,1000+delay); window.finish(2000+delay);
  ownerAdmission(window,request,before,61000);
  if(delay) {
   if(!request.pending||window.active||window.count!=1) return false;
   request.pending=false; ownerAdmission(window,request,before,61000+delay);
  }
  if(!window.active||request.pending||window.count!=2||window.startedMs!=61000+delay) return false;
  const uint32_t start=window.startedMs;
  ownerAdmission(window,request,before,start+1); // Active duplicate coalesces, not a future request.
  if(request.pending||window.count!=2) return false;
  window.finish(start+8000,true,0x107);
  if(window.endedMs-window.startedMs>8000 || window.startedMs-(1000+delay)<60000) return false;
 }
 // Exact reported scheduler edge: first 1020, request 61000 retained until 61020.
 LockedWindow window; Request request;
 ownerAdmission(window,request,before,1020); window.finish(9020,true,0x107);
 ownerAdmission(window,request,before,61000); if(!request.pending||window.active) return false;
 request.pending=false; ownerAdmission(window,request,before,61020);
 return window.active&&!request.pending&&window.startedMs==61020;
}
static_assert(test());
'''.replace('ADMISSION',admission)
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'admission.cpp'; source.write_text(code)
            result=subprocess.run([str(COMPILER),'-std=c++17','-fsyntax-only','-I'+str(ROOT),str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/locked-refresh-admission.log'; log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))
        cancel=MODULE.split('if (!locked || otaBusy || failed)',1)[1].split('if (locked && !wasLocked',1)[0]
        self.assertIn('lockedRefreshRequested.store(false);',cancel)

if __name__=='__main__': unittest.main()
