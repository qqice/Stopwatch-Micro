/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstdint>
namespace TouchSleep {
enum class Phase : uint8_t { Off, Probe, Handshake1, Handshake2, HandshakeEcho, ModeWrite, ModeEcho, Settle, SleepWrite, SleepRequested, VerifyRelease, Complete, HeldClosed, FailedRestored, RebootPending };
constexpr const char* phaseName(Phase p) {
 switch(p) {
 case Phase::Off:return "off";case Phase::Probe:return "probe";case Phase::Handshake1:return "handshake_write1";
 case Phase::Handshake2:return "handshake_write2";case Phase::HandshakeEcho:return "handshake_echo";
 case Phase::ModeWrite:return "mode_write";case Phase::ModeEcho:return "mode_echo";case Phase::Settle:return "settle";
 case Phase::SleepWrite:return "sleep_write";case Phase::SleepRequested:return "sleep_requested_unverified";
 case Phase::VerifyRelease:return "verify_release";case Phase::Complete:return "complete";
 case Phase::HeldClosed:return "normal_verified_input_release_gated";case Phase::FailedRestored:return "failed_restored";case Phase::RebootPending:return "reboot_pending";
 }return "unknown";
}
struct AdmissionModel {
 bool touchReserved=false;uint32_t otaInFlight=0;
 constexpr bool reserveTouch(bool busy) {
  if(busy || touchReserved || otaInFlight)return false;
  touchReserved=true;return true;
 }
 constexpr bool enterOta() {if(touchReserved)return false;++otaInFlight;return true;}
 constexpr void leaveOta() {if(otaInFlight)--otaInFlight;}
 constexpr void releaseTouch() {touchReserved=false;}
};
struct Model {
 Phase phase=Phase::Off;
 bool consumed=false, restoring=false, dirty=false, failed=false, releaseProven=false, normalVerified=false, normalReportValid=false;
 uint32_t revision=0, writes=0, reads=0, entryAttempts=0, restoreAttempts=0;
 uint8_t echo=0, failedEcho=0;
 Phase errorPhase=Phase::Off, restoreErrorPhase=Phase::Off;
 uint16_t lastCommand=0;int32_t lastI2cError=0;
 int32_t error=0, restoreError=0;
 int64_t startedUs=0, sleepRequestUs=0, leaseUntilUs=0, restoreStartedUs=0, restoredUs=0, deadlineUs=0, restoreDeadlineUs=0;
 constexpr bool critical() const {return phase!=Phase::Off && phase!=Phase::Complete && phase!=Phase::HeldClosed && phase!=Phase::FailedRestored;}
 constexpr bool blocksTouch() const {return critical();}
 constexpr bool transition() const {return critical() && phase!=Phase::SleepRequested;}
 constexpr bool request(int64_t now) {
  if(consumed || critical())return false;
  consumed=true;phase=Phase::Probe;startedUs=now;++entryAttempts;++revision;return true;
 }
 constexpr void restore(int64_t now) {
  if(!critical() || restoring || phase==Phase::RebootPending)return;
  if(!dirty) {phase=Phase::Complete;++revision;return;}
  restoring=true;restoreStartedUs=now;restoreDeadlineUs=now+5000000;leaseUntilUs=0;++restoreAttempts;
  phase=Phase::Handshake1;++revision;
 }
 constexpr void fail(int64_t now,int32_t rc) {
  failed=true;if(!error) {error=rc;errorPhase=phase;failedEcho=echo;}
  if(restoring) {restoreError=rc;restoreErrorPhase=phase;phase=Phase::RebootPending;deadlineUs=now+1000000;++revision;}
  else if(dirty)restore(now);
  else {phase=Phase::FailedRestored;++revision;}
 }
 constexpr void tick(int64_t now,bool restoreNeeded) {
  if(restoreNeeded || (phase==Phase::SleepRequested && now>=leaseUntilUs))restore(now);
  if(restoring && critical() && phase!=Phase::RebootPending && now>=restoreDeadlineUs) {
   if(phase==Phase::VerifyRelease && normalVerified && normalReportValid) {
    phase=Phase::HeldClosed;restoredUs=now;++revision; // Normal controller, existing asynchronous input-release latch takes over.
   } else fail(now,-3);
  }
  if(phase==Phase::Settle && now>=deadlineUs) {phase=restoring?Phase::VerifyRelease:Phase::SleepWrite;++revision;}
 }
 constexpr uint16_t writeCommand() const {
  return phase==Phase::Handshake1 || phase==Phase::Handshake2 ? 0xD11E :
   phase==Phase::ModeWrite ? (restoring?0xD109:0xD101) : phase==Phase::SleepWrite ? 0xD105 : 0;
 }
 constexpr void beforeWrite() {lastCommand=writeCommand();dirty=true;++writes;}
 constexpr void complete(int64_t now,int32_t rc,uint8_t observed=0,bool released=false) {
  lastI2cError=rc;
  if(rc) {fail(now,rc);return;}
  // Recheck after bounded I2C: an action started before the deadline can finish after it.
  if(restoring && critical() && phase!=Phase::RebootPending && now>=restoreDeadlineUs) {
   if(phase==Phase::VerifyRelease && normalVerified && normalReportValid) {phase=Phase::HeldClosed;restoredUs=now;++revision;}
   else fail(now,-3);
   return;
  }
  switch(phase) {
  case Phase::Probe:phase=Phase::Handshake1;break;
  case Phase::Handshake1:phase=Phase::Handshake2;break;
  case Phase::Handshake2:phase=Phase::HandshakeEcho;break;
  case Phase::HandshakeEcho:echo=observed;if(echo!=0x1E){fail(now,-2);return;}phase=Phase::ModeWrite;break;
  case Phase::ModeWrite:phase=Phase::ModeEcho;break;
  case Phase::ModeEcho:echo=observed;if(echo!=(restoring?0x09:0x01)){fail(now,-2);return;}normalVerified=restoring;phase=Phase::Settle;deadlineUs=now+10000;break;
  case Phase::SleepWrite:sleepRequestUs=now;leaseUntilUs=now+120000000;phase=Phase::SleepRequested;break;
  case Phase::VerifyRelease:
   normalReportValid=true;
   if(!released)return; // Valid held-finger report is not a controller fault; keep release gate.
   releaseProven=true;restoredUs=now;phase=failed?Phase::FailedRestored:Phase::Complete;break;
  default:return;
  }++revision;
 }
};
}
