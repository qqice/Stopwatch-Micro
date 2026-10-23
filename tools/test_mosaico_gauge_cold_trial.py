"""Pure constexpr cold-trial decisions/faults; no device, SDK or storage IO."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
FIXTURE = r'''
#include "mosaico_gauge_cold_trial_model.h"
namespace m=mosaico_gauge_cold_trial;
constexpr m::Binding binding(){m::Binding b{};b.mac={0x24,1,2,3,4,5};b.originalGctGeneration=19;b.originalGctEpoch=19;
 b.nonce[0]=1;b.slotHashes[0][0]=1;b.slotHashes[1][0]=2;b.source131ReceiptDigest[0]=3;return b;}
constexpr m::Context context(){m::Context c{};c.expected=binding();c.boot.nonce[0]=10;c.boot.verified=true;c.nowMs=100;
 c.startupKind=m::StartupKind::PowerOn;c.recordDurable=c.nonceReservedFresh=true;
 auto& s=c.safety;s.identityVerified=s.originalGctBindingVerified=s.securityKnown=s.durableAuthenticatedSource131=true;
 s.reviewedOwned3Model=s.twoRecoverySlotsSdkValid=s.sealed=s.cfg0=s.cal0=s.targetStandard65=s.operationKnownSafeForInspection=true;
 s.voltageMv=4200;s.temperatureDK=3000;s.freshAuthenticated131=s.calibrationUnchanged=true;s.image=m::Image::Source131;return c;}
constexpr m::Record armed(){return m::arm(m::Load::Missing,binding(),context()).next;}
constexpr m::Record handed(){auto c=context();c.nowMs=200;c.safety.image=m::Image::TargetC131;return m::handoff(armed(),c).next;}
constexpr m::Context nextBoot(){auto c=context();c.boot.nonce[0]=11;c.nowMs=10;c.safety.image=m::Image::Unknown;
 c.safety.freshAuthenticated131=false;return c;}
constexpr m::Record claimed(){return m::startup(m::Load::Present,handed(),nextBoot()).next;}
constexpr m::Proof proof(const m::Record& r){return {m::Commit::ExactDurable,true,true,r.sequence,r.phase,r.binding.nonce};}
constexpr m::Digest evidence(){m::Digest d{};d[0]=77;return d;}
constexpr m::Record captured(m::CaptureResult result=m::CaptureResult::Success){m::Session s{};s.claimReleased=true;
 return m::captureEvidence(claimed(),nextBoot(),s,result,evidence()).next;}
constexpr m::Record restoring(){auto c=nextBoot();return m::restoreIntent(captured(),c).next;}
'''


class ColdTrialTests(unittest.TestCase):
    def compile(self, code):
        directory = ROOT / 'artifacts/gauge-cold-trial-tests'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            source = Path(temporary) / 'cold.cpp'
            source.write_text(FIXTURE + code)
            result = subprocess.run([str(COMPILER), '-std=c++20', '-Wall', '-Wextra', '-Werror',
                '-fconstexpr-ops-limit=100000000', '-fsyntax-only', '-I' + str(ROOT / 'main/hal/mosaico'), str(source)],
                capture_output=True, text=True)
            log = directory / (self._testMethodName + '.log')
            log.write_text(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, str(log))

    def test_binding_schema_nonce_and_independent_protocol(self):
        self.compile(r'''
constexpr bool cases(){const auto r=armed();if(!m::initial(r)||!m::shape(handed())||!m::shape(claimed())||!m::shape(captured())||!m::shape(restoring()))return false;
 auto c=context();auto b=binding();if(m::arm(m::Load::Present,b,c).action!=m::Action::BlockedUnknown)return false;
 c.nonceReservedFresh=false;if(m::arm(m::Load::Missing,b,c).action!=m::Action::BlockedUnknown)return false;
 for(unsigned i=0;i<16;++i){auto x=r;x.binding.nonce[i]^=1;if(m::startup(m::Load::Present,x,context()).action!=m::Action::BlockedUnknown)return false;}
 for(unsigned i=0;i<6;++i){auto x=r;x.binding.mac[i]^=2;if(m::startup(m::Load::Present,x,context()).action!=m::Action::BlockedUnknown)return false;}
 auto x=r;x.schema=2;if(m::shape(x))return false;x=r;x.binding.identity.build=3;if(m::shape(x))return false;
 x=r;x.binding.source^=1;if(m::shape(x))return false;x=r;x.binding.target^=1;if(m::shape(x))return false;
 x=r;x.binding.originalGctGeneration++;if(m::startup(m::Load::Present,x,context()).action!=m::Action::BlockedUnknown)return false;
 x=r;x.binding.originalGctEpoch++;if(m::startup(m::Load::Present,x,context()).action!=m::Action::BlockedUnknown)return false;
 for(unsigned slot=0;slot<2;++slot){x=r;x.binding.slotHashes[slot][0]^=4;if(m::startup(m::Load::Present,x,context()).action!=m::Action::BlockedUnknown)return false;}
 b=binding();b.recovery=m::RecoveryBinding::ContractDigest;b.slotHashes={};b.contractDigest[0]=1;if(!m::validBinding(b))return false;
 b.slotHashes[0][0]=1;if(m::validBinding(b))return false;
 return !m::physicalUnitsTrusted&&!m::offWallClockGuaranteed;
}static_assert(cases());
''')

    def test_armed720_handoff30_no_off_wallclock_or_same_boot_consumption(self):
        self.compile(r'''
constexpr bool cases(){auto c=context();const auto a=armed(),h=handed();
 if(m::startup(m::Load::Present,a,c).action!=m::Action::KeepArmed720)return false;
 c.nowMs=a.armedMs+m::armedLimitMs;if(m::startup(m::Load::Present,a,c).action!=m::Action::RestoreRequired)return false;
 c.nowMs=h.handoffMs+29999;if(m::startup(m::Load::Present,h,c).action!=m::Action::AwaitPowerOffGrace)return false;
 c.nowMs=h.handoffMs+30000;if(m::startup(m::Load::Present,h,c).action!=m::Action::RestoreRequired)return false;
 auto late=a;c=context();c.safety.image=m::Image::TargetC131;c.nowMs=a.armedMs+719999;
 auto d=m::handoff(late,c);if(d.action!=m::Action::Reject)return false;
 c.nowMs=a.armedMs+590000;d=m::handoff(late,c);if(!d.hasNext)return false;c.nowMs=a.armedMs+600000;
 if(m::startup(m::Load::Present,d.next,c).action!=m::Action::RestoreRequired)return false;
 c=context();c.nowMs=99;if(m::startup(m::Load::Present,a,c).action!=m::Action::BlockedUnknown)return false;
 c=nextBoot();c.nowMs=0;auto claim=m::startup(m::Load::Present,h,c);
 if(!claim.hasNext||claim.next.phase!=m::Phase::CaptureClaimed||claim.next.claimMs!=0)return false;
 for(auto kind:{m::StartupKind::Software,m::StartupKind::Watchdog,m::StartupKind::Brownout,m::StartupKind::Unknown}){
 c.startupKind=kind;if(m::startup(m::Load::Present,h,c).action!=m::Action::FailureEvidenceRequired)return false;
 auto fail=m::rejectedStartupEvidence(h,c,evidence());if(!fail.hasNext||fail.next.capture!=m::CaptureResult::RejectedStartup)return false;}
 return true;
}static_assert(cases());
''')

    def test_exact_commit_claim_once_and_torn_commit_never_releases_capture_or_keys(self):
        self.compile(r'''
constexpr bool cases(){const auto a=armed(),h=handed(),r=claimed();auto c=nextBoot();
 for(auto failure:{m::Commit::NotWritten,m::Commit::UnknownOrTorn}){auto p=proof(r);p.result=failure;m::Session session{};
 if(m::afterPersist(h,r,p,c,session).action!=m::Action::BlockedUnknown||!session.blocked)return false;
 if(m::afterPersist(h,r,proof(r),c,session).action!=m::Action::BlockedUnknown)return false;}
 for(unsigned fault=0;fault<5;++fault){auto p=proof(r);if(fault==0)p.fullRecordReadbackIdentical=false;if(fault==1)p.source131StillDurable=false;
 if(fault==2)p.sequence++;
 if(fault==3)p.phase=m::Phase::Captured;
 if(fault==4)p.nonce[0]^=1;
 m::Session session{};
 if(m::afterPersist(h,r,p,c,session).action!=m::Action::BlockedUnknown)return false;}
 m::Session session{};if(m::afterPersist(h,r,proof(r),c,session).action!=m::Action::ReadOnlyCapture)return false;
 if(m::afterPersist(h,r,proof(r),c,session).action!=m::Action::BlockedUnknown)return false;
 if(m::startup(m::Load::Present,r,c).action!=m::Action::FailureEvidenceRequired)return false;
 c.boot.nonce[0]=12;c.nowMs=0;if(m::startup(m::Load::Present,r,c).action!=m::Action::FailureEvidenceRequired)return false;
 auto interrupted=m::interruptedEvidence(r,c,evidence());if(!interrupted.hasNext||interrupted.next.phase!=m::Phase::CaptureFailed)return false;
 c=context();m::Session armSession{};if(m::afterPersist({},a,proof(a),c,armSession,true).action!=m::Action::KeepArmed720)return false;
 c.safety.source131AfterArmedSequence=a.sequence;
 if(m::authorizeApply(a,c,armSession).action!=m::Action::ApplyC65Owned3||m::authorizeApply(a,c,armSession).action!=m::Action::BlockedUnknown)return false;
 return true;
}static_assert(cases());
''')

    def test_known_timeout_overflow_evidence_is_durable_before_restore_no_recapture(self):
        self.compile(r'''
constexpr bool cases(){const auto r=claimed();auto c=nextBoot();
 for(auto failure:{m::CaptureResult::KnownTimeout,m::CaptureResult::KnownOverflow,m::CaptureResult::KnownReadFailure}){
 m::Session session{};session.claimReleased=true;auto d=m::captureEvidence(r,c,session,failure,evidence());
 if(!d.hasNext||d.next.phase!=m::Phase::CaptureFailed||m::restoreIntent(r,c).action!=m::Action::FailureEvidenceRequired)return false;
 if(m::captureEvidence(r,c,session,failure,evidence()).action!=m::Action::BlockedUnknown)return false;
 if(m::afterPersist(r,d.next,proof(d.next),c,session).action!=m::Action::RestoreRequired)return false;
 if(m::startup(m::Load::Present,d.next,c).action!=m::Action::RestoreRequired)return false;
 auto restore=m::restoreIntent(d.next,c);if(!restore.hasNext||restore.next.evidenceDigest!=evidence()||restore.next.capture!=failure)return false;
 }
 m::Session session{};session.claimReleased=true;c.nowMs=r.claimMs+r.binding.captureBudgetMs;
 if(m::capturePoll(r,c,session).action!=m::Action::FailureEvidenceRequired)return false;
 if(m::captureEvidence(r,c,session,m::CaptureResult::Success,evidence()).action!=m::Action::Reject)return false;
 if(m::captureEvidence(r,c,session,m::CaptureResult::KnownTimeout,evidence()).action!=m::Action::PersistRecord)return false;
 session={};session.claimReleased=true;if(m::captureEvidence(r,nextBoot(),session,m::CaptureResult::Success,{}).action!=m::Action::BlockedUnknown)return false;
 return true;
}static_assert(cases());
''')

    def test_restore_inspection_then_fresh131_owned_write_gate_and_terminal_no_reuse(self):
        self.compile(r'''
constexpr bool cases(){const auto r=restoring();auto c=nextBoot();m::Session session{};
 if(m::authorizeRestoreInspection(r,c,session).action!=m::Action::InspectForRestore)return false;
 if(m::authorizeRestore(r,c,session).action!=m::Action::BlockedUnknown)return false; // standard65 NOT fresh131
 session={};if(m::authorizeRestoreInspection(r,c,session).action!=m::Action::InspectForRestore)return false;
 c.safety.sealed=false;c.safety.freshAuthenticated131=true;c.safety.image=m::Image::Owned3Mixed;
 if(m::authorizeRestore(r,c,session).action!=m::Action::RestoreOriginalOwned3)return false;
 if(m::authorizeRestore(r,c,session).action!=m::Action::BlockedUnknown)return false;
 session={};c=nextBoot();c.safety.securityKnown=false;
 if(m::authorizeRestoreInspection(r,c,session).action!=m::Action::BlockedUnknown)return false;
 for(auto image:{m::Image::Unknown,m::Image::Unexpected}){session={};c=nextBoot();m::authorizeRestoreInspection(r,c,session);
 c.safety.image=image;c.safety.freshAuthenticated131=true;if(m::authorizeRestore(r,c,session).action!=m::Action::BlockedUnknown)return false;}
 session={};c=nextBoot();m::authorizeRestoreInspection(r,c,session);c.safety.freshAuthenticated131=true;c.safety.image=m::Image::Source131;c.safety.calibrationUnchanged=false;
 if(m::authorizeRestore(r,c,session).action!=m::Action::BlockedUnknown)return false;
 c=context();c.boot=nextBoot().boot;c.safety.image=m::Image::Source131;auto done=m::restored(r,c);
 if(!done.hasNext||done.next.phase!=m::Phase::Restored||done.next.evidenceDigest!=r.evidenceDigest)return false;
 if(m::transition(done.next,armed())||m::arm(m::Load::Present,binding(),c).action!=m::Action::BlockedUnknown)return false;
 return m::startup(m::Load::Present,done.next,c).action==m::Action::Complete;
}static_assert(cases());
''')

    def test_unknown_storage_binding_security_and_no_legacy_fallback(self):
        self.compile(r'''
constexpr bool cases(){auto c=context();const auto r=armed();
 for(auto load:{m::Load::Corrupt,m::Load::IoUnknown})if(m::startup(load,r,c).action!=m::Action::BlockedUnknown)return false;
 if(m::startup(m::Load::Missing,r,c).action!=m::Action::NoColdTrial)return false;
 c.legacyFallbackRequested=true;for(auto load:{m::Load::Missing,m::Load::Present,m::Load::Corrupt})if(m::startup(load,r,c).action!=m::Action::BlockedUnknown)return false;
 c=context();c.boot.verified=false;if(m::startup(m::Load::Present,r,c).action!=m::Action::BlockedUnknown)return false;
 c=context();c.recordDurable=false;if(m::startup(m::Load::Present,r,c).action!=m::Action::BlockedUnknown)return false;
 for(unsigned fault=0;fault<10;++fault){c=nextBoot();if(fault==0)c.safety.identityVerified=false;if(fault==1)c.safety.originalGctBindingVerified=false;
 if(fault==2)c.safety.durableAuthenticatedSource131=false;
 if(fault==3)c.safety.reviewedOwned3Model=false;
 if(fault==4)c.safety.twoRecoverySlotsSdkValid=false;
 if(fault==5)c.safety.sealed=false;
 if(fault==6)c.safety.cfg0=false;
 if(fault==7)c.safety.cal0=false;
 if(fault==8)c.safety.voltageMv=3499;
 if(fault==9)c.safety.temperatureDK=3132;
 if(m::restoreIntent(captured(),c).action!=m::Action::BlockedUnknown)return false;}
 m::Session session{};m::unknownOutcome(session);c=context();if(m::authorizeApply(r,c,session).action!=m::Action::BlockedUnknown)return false;
 return true;
}static_assert(cases());
''')

    def test_apply4000_restore3500_and_absolute120s_startup_no_session_reset_waiver(self):
        self.compile(r'''
constexpr bool cases(){auto c=context();auto a=armed();m::Session session{};
 c.safety.voltageMv=3999;if(m::arm(m::Load::Missing,binding(),c).action!=m::Action::BlockedUnknown)return false;
 if(m::authorizeApply(a,c,session).action!=m::Action::BlockedUnknown)return false;
 c.safety.image=m::Image::TargetC131;c.nowMs=200;if(m::handoff(a,c).action!=m::Action::Reject)return false;
 c=context();c.safety.voltageMv=4000;c.safety.source131AfterArmedSequence=a.sequence;session={};if(m::authorizeApply(a,c,session).action!=m::Action::ApplyC65Owned3)return false;
 auto r=restoring();c=nextBoot();c.safety.voltageMv=3500;session={};
 if(m::authorizeRestoreInspection(r,c,session).action!=m::Action::InspectForRestore)return false;
 c.safety.sealed=false;c.safety.freshAuthenticated131=true;c.safety.image=m::Image::TargetC131;
 if(m::authorizeRestore(r,c,session).action!=m::Action::RestoreOriginalOwned3)return false;
 c=nextBoot();c.nowMs=119999;session={};if(m::authorizeRestoreInspection(r,c,session).action!=m::Action::InspectForRestore)return false;
 c.nowMs=120000;c.safety.freshAuthenticated131=true;c.safety.image=m::Image::Source131;
 if(m::authorizeRestore(r,c,session).action!=m::Action::BlockedUnknown||m::restored(r,c).action!=m::Action::BlockedUnknown)return false;
 c=nextBoot();c.nowMs=120000;if(m::startup(m::Load::Present,handed(),c).action!=m::Action::FailureEvidenceRequired)return false;
 if(m::restoreIntent(captured(),c).action!=m::Action::BlockedUnknown)return false;
 c=nextBoot();c.safety.operationKnownSafeForInspection=false;session={};
 if(m::authorizeRestoreInspection(r,c,session).action!=m::Action::BlockedUnknown)return false;
 session={};if(m::authorizeRestoreInspection(r,c,session).action!=m::Action::BlockedUnknown)return false;
 c=nextBoot();if(m::restoreIntent(handed(),c).action!=m::Action::FailureEvidenceRequired)return false;
 return true;
}static_assert(cases());
''')

    def test_transition_chain_metadata_evidence_immutability_and_terminals(self):
        self.compile(r'''
constexpr bool cases(){const auto a=armed(),h=handed(),c=claimed(),e=captured(),f=captured(m::CaptureResult::KnownOverflow),r=restoring();
 if(!m::transition(a,h)||!m::transition(h,c)||!m::transition(c,e)||!m::transition(c,f)||!m::transition(e,r))return false;
 auto x=e;x.sequence=c.sequence+2;if(m::transition(c,x))return false;
 x=e;x.claimMs++;if(m::transition(c,x))return false;
 x=r;x.evidenceDigest[1]=1;if(m::transition(e,x))return false;
 x=r;x.capture=m::CaptureResult::KnownTimeout;if(m::transition(e,x))return false;
 x=h;x.armedMs++;if(m::transition(a,x))return false;
 x=c;x.handoffMs++;if(m::transition(h,x))return false;
 x=f;x.claimedBoot={};x.claimMs=0;if(m::shape(x))return false;
 x=a;x.sequence=UINT32_MAX;auto y=x;y.sequence=0;y.phase=m::Phase::HandedOff;y.handoffMs=200;
 if(m::transition(x,y))return false;
 auto ctx=nextBoot();ctx.safety.freshAuthenticated131=true;ctx.safety.image=m::Image::Source131;
 const auto done=m::restored(r,ctx).next;if(!m::shape(done)||!m::terminal(done.phase))return false;
 x=done;x.sequence++;x.phase=m::Phase::Armed;if(m::transition(done,x))return false;
 x=r;x.sequence++;x.phase=m::Phase::BlockedUnknown;if(!m::transition(r,x)||m::transition(x,done))return false;
 return true;
}static_assert(cases());
''')

    def test_early_restore_reserves_late_startup_evidence_and_no_capture_lease_extension(self):
        self.compile(r'''
constexpr bool cases(){auto c=context();const auto a=armed();
 c.nowMs=a.armedMs+599999;if(m::startup(m::Load::Present,a,c).action!=m::Action::KeepArmed720)return false;
 c.nowMs=a.armedMs+600000;if(m::startup(m::Load::Present,a,c).action!=m::Action::RestoreRequired)return false;
 auto restore=m::restoreIntent(a,c);if(!restore.hasNext)return false;m::Session s{};
 if(m::authorizeRestoreInspection(restore.next,c,s).action!=m::Action::InspectForRestore)return false;
 c.nowMs=a.armedMs+720000;s={};if(m::authorizeRestoreInspection(restore.next,c,s).action!=m::Action::BlockedUnknown)return false;
 for(uint64_t late:{60000ULL,120000ULL}){c=nextBoot();c.nowMs=late;
 if(m::startup(m::Load::Present,handed(),c).action!=m::Action::FailureEvidenceRequired)return false;
 auto fail=m::rejectedStartupEvidence(handed(),c,evidence());
 if(!fail.hasNext||fail.next.capture!=m::CaptureResult::StartupWindowExpired||fail.next.phase!=m::Phase::CaptureFailed)return false;
 if(late==60000&&!m::restoreIntent(fail.next,c).hasNext)return false;
 if(late==120000&&m::restoreIntent(fail.next,c).action!=m::Action::BlockedUnknown)return false;}
 auto h=handed();h.binding.captureBudgetMs=60000;c=nextBoot();c.expected=h.binding;c.nowMs=59900;
 auto claim=m::startup(m::Load::Present,h,c);if(!claim.hasNext)return false;s={};c.nowMs=59950;
 if(m::afterPersist(h,claim.next,proof(claim.next),c,s).action!=m::Action::ReadOnlyCapture)return false;
 c.nowMs=60000;if(m::capturePoll(claim.next,c,s).action!=m::Action::FailureEvidenceRequired)return false;
 if(m::captureEvidence(claim.next,c,s,m::CaptureResult::Success,evidence()).action!=m::Action::Reject)return false;
 auto fail=m::captureEvidence(claim.next,c,s,m::CaptureResult::KnownTimeout,evidence());if(!fail.hasNext)return false;
 if(m::afterPersist(claim.next,fail.next,proof(fail.next),c,s).action!=m::Action::RestoreRequired)return false;
 restore=m::restoreIntent(fail.next,c);if(!restore.hasNext)return false;
 if(m::authorizeRestoreInspection(restore.next,c,s).action!=m::Action::InspectForRestore)return false;
 c.safety.freshAuthenticated131=true;c.safety.image=m::Image::TargetC131;c.safety.sealed=false;
 return m::authorizeRestore(restore.next,c,s).action==m::Action::RestoreOriginalOwned3;
}static_assert(cases());
''')

    def test_blocked_terminal_preserves_all_prior_capture_evidence_and_claim_metadata(self):
        self.compile(r'''
constexpr bool cases(){for(auto original:{captured(),captured(m::CaptureResult::KnownOverflow),restoring()}){
 auto blocked=original;++blocked.sequence;blocked.phase=m::Phase::BlockedUnknown;
 if(!m::transition(original,blocked))return false;
 for(unsigned field=0;field<6;++field){auto x=blocked;
 if(field==0)x.evidenceDigest={};
 if(field==1)x.evidenceDigest[1]^=1;
 if(field==2)x.capture=m::CaptureResult::KnownReadFailure;
 if(field==3)x.claimedBoot.nonce[1]^=1;
 if(field==4)x.claimMs++;
 if(field==5){x.claimedBoot={};x.claimMs=0;}
 if(m::transition(original,x))return false;
 }}return true;
}static_assert(cases());
''')

    def test_persistent_shape_and_transition_reject_api_unreachable_times_or_added_evidence(self):
        self.compile(r'''
constexpr bool cases(){const auto a=armed(),h=handed(),claim=claimed();
 for(uint64_t age:{600000ULL,650000ULL,719999ULL}){auto x=h;x.handoffMs=a.armedMs+age;
 if(m::shape(x)||m::transition(a,x))return false;}
 for(uint64_t time:{60000ULL,120000ULL,UINT64_MAX}){auto x=claim;x.claimMs=time;
 if(m::shape(x)||m::transition(h,x))return false;}
 auto atZero=claim;atZero.claimMs=0;if(!m::shape(atZero)||!m::transition(h,atZero))return false;
 auto fabricated=a;++fabricated.sequence;fabricated.phase=m::Phase::Restoring;fabricated.handoffMs=200;
 if(m::transition(a,fabricated))return false;
 fabricated=captured();fabricated.sequence=a.sequence+1;fabricated.phase=m::Phase::Restoring;
 if(m::transition(a,fabricated))return false;
 fabricated=captured(m::CaptureResult::KnownTimeout);fabricated.sequence=h.sequence+1;fabricated.phase=m::Phase::CaptureFailed;
 if(m::transition(h,fabricated))return false;
 auto noCapture=a;++noCapture.sequence;noCapture.phase=m::Phase::Restoring;
 if(!m::transition(a,noCapture))return false;
 fabricated=captured();fabricated.phase=m::Phase::Restored;fabricated.sequence=noCapture.sequence+1;
 if(m::transition(noCapture,fabricated))return false;
 return true;
}static_assert(cases());
''')

    def test_all_phases_reject_unknown_capture_enums_and_none_digest_aliases(self):
        self.compile(r'''
constexpr bool cases(){auto c=nextBoot();c.safety.freshAuthenticated131=true;c.safety.image=m::Image::Source131;
 const auto done=m::restored(restoring(),c).next;auto blocked=captured();blocked.sequence++;blocked.phase=m::Phase::BlockedUnknown;
 std::array<m::Record,8> phases{armed(),handed(),claimed(),captured(),captured(m::CaptureResult::KnownOverflow),restoring(),done,blocked};
 for(const auto& original:phases){if(!m::shape(original))return false;
 for(unsigned raw=0;raw<256;++raw){if(raw<=unsigned(m::CaptureResult::StartupWindowExpired))continue;
 auto x=original;x.capture=m::CaptureResult(raw);x.evidenceDigest=evidence();if(m::shape(x))return false;}
 auto x=original;x.capture=m::CaptureResult::None;x.evidenceDigest=evidence();if(m::shape(x))return false;
 x=original;x.capture=m::CaptureResult::KnownTimeout;x.evidenceDigest={};if(m::shape(x))return false;
 }
 for(unsigned raw=0;raw<256;++raw){if(raw>=1&&raw<=8)continue;auto x=armed();x.phase=m::Phase(raw);if(m::shape(x))return false;}
 return true;
}static_assert(cases());
''')

    def test_acyclic_phase_sequence_cannot_be_renumbered_into_fake_durable_history(self):
        self.compile(r'''
constexpr bool cases(){auto a=armed(),h=handed();a.sequence=98;h.sequence=99;
 if(m::shape(a)||m::shape(h)||m::transition(a,h))return false;
 auto c=nextBoot();if(m::startup(m::Load::Present,h,c).action!=m::Action::BlockedUnknown)return false;
 for(auto original:{armed(),handed(),claimed(),captured(),captured(m::CaptureResult::KnownTimeout)})
 for(uint32_t sequence=1;sequence<100;++sequence){if(sequence==original.sequence)continue;
 auto x=original;x.sequence=sequence;if(m::shape(x))return false;}
 c.nowMs=60000;const auto fail=m::rejectedStartupEvidence(handed(),c,evidence()).next;
 if(!m::shape(fail)||fail.sequence!=3)return false;
 c.nowMs=60001;auto rest=m::restoreIntent(fail,c);if(!rest.hasNext||rest.next.sequence!=4)return false;
 c.safety.freshAuthenticated131=true;c.safety.image=m::Image::Source131;
 const auto done=m::restored(rest.next,c);if(!done.hasNext||done.next.sequence!=5)return false;
 for(auto original:{fail,rest.next,done.next}){auto x=original;x.sequence+=90;if(m::shape(x))return false;}
 return true;
}static_assert(cases());
''')

    def test_owned_FA_source_arm_commit_then_second_live131_allows_existing_CFG_once(self):
        self.compile(r'''
constexpr bool cases(){auto c=context();c.nowMs=150;auto& s=c.safety;
 s.sealed=false;s.securityLevel=1;s.ownedGctPending=s.knownFullAccess=s.ownedGctDeadlineBound=true;s.ownedGctStartedMs=100;
 auto arm=m::arm(m::Load::Missing,binding(),c);if(!arm.hasNext||!m::initial(arm.next)||arm.next.armedMs!=100)return false;
 m::Session session{};c.nowMs=175;
 if(m::afterPersist({},arm.next,proof(arm.next),c,session,true).action!=m::Action::KeepArmed720)return false;
 auto premature=session;if(m::authorizeApply(arm.next,c,premature).action!=m::Action::BlockedUnknown)return false;
 s.source131AfterArmedSequence=arm.next.sequence;c.nowMs=180; // Wrapper's second live observe, AFTER NAND readback.
 if(m::authorizeApply(arm.next,c,session).action!=m::Action::ApplyC65Owned3)return false;
 if(m::authorizeApply(arm.next,c,session).action!=m::Action::BlockedUnknown)return false;
 c.nowMs=600100;if(m::startup(m::Load::Present,arm.next,c).action!=m::Action::RestoreRequired)return false;
 c.nowMs=720100;if(m::operationWindow(arm.next,c))return false; // Never a new720s window from NAND timestamp150.
 return true;
}static_assert(cases());
''')

    def test_owned_FA_unknown_SEC2_missing_pending_keys_calibration_or_stale_source_never_admits(self):
        self.compile(r'''
constexpr m::Context owned(){auto c=context();c.nowMs=150;c.safety.sealed=false;c.safety.securityLevel=1;
 c.safety.ownedGctPending=c.safety.knownFullAccess=c.safety.ownedGctDeadlineBound=true;c.safety.ownedGctStartedMs=100;return c;}
constexpr bool cases(){const auto r=m::arm(m::Load::Missing,binding(),owned()).next;
 for(unsigned fault=0;fault<15;++fault){auto c=owned();auto& s=c.safety;
 if(fault==0)s.securityKnown=false;
 if(fault==1)s.securityLevel=0;
 if(fault==2)s.securityLevel=2;
 if(fault==3)s.ownedGctPending=false;
 if(fault==4)s.knownFullAccess=false;
 if(fault==5)s.operationKnownSafeForInspection=false; // unresolved/unknown prior key ACK
 if(fault==6)s.calibrationUnchanged=false;
 if(fault==7)s.freshAuthenticated131=false;
 if(fault==8)s.image=m::Image::TargetC131;
 if(fault==9)s.cfg0=false;
 if(fault==10)s.cal0=false;
 if(fault==11)s.ownedGctDeadlineBound=false;
 if(fault==12)s.ownedGctStartedMs=151;
 if(fault==13)c.nowMs=600100;
 if(fault==14)s.sealed=true; // SEC1 cannot be relabeled SEALED to bypass owned admission
 if(m::arm(m::Load::Missing,binding(),c).action!=m::Action::BlockedUnknown)return false;
 m::Session session{};if(m::afterPersist({},r,proof(r),c,session,true).action!=m::Action::BlockedUnknown)return false;
 session={};s.source131AfterArmedSequence=r.sequence;if(m::authorizeApply(r,c,session).action!=m::Action::BlockedUnknown)return false;
 }
 auto c=owned();c.safety.source131AfterArmedSequence=2;m::Session session{};
 if(m::authorizeApply(r,c,session).action!=m::Action::BlockedUnknown)return false;
 c=context();c.safety.securityLevel=2;c.safety.source131AfterArmedSequence=1;session={};
 if(m::authorizeApply(armed(),c,session).action!=m::Action::BlockedUnknown)return false;
 c=owned();c.safety.ownedGctStartedMs=50;session={};c.safety.source131AfterArmedSequence=1;
 if(m::authorizeApply(r,c,session).action!=m::Action::BlockedUnknown)return false; // Existing lease earlier than stored Armed anchor.
 return true;
}static_assert(cases());
''')

    def test_sealed_existing_pending_never_reanchors_old_GCT_lease_as_new_cold_arm(self):
        self.compile(r'''
constexpr bool cases(){const auto existing=armed();
 for(uint64_t now:{150ULL,599000ULL,700100ULL}){auto c=context();c.nowMs=now;
 c.safety.sealed=true;c.safety.securityLevel=3;c.safety.knownFullAccess=false;
 c.safety.ownedGctPending=c.safety.ownedGctDeadlineBound=true;c.safety.ownedGctStartedMs=100;
 const auto d=m::arm(m::Load::Missing,binding(),c);
 if(d.action!=m::Action::BlockedUnknown||d.hasNext)return false;
 m::Session session{};if(m::afterPersist({},existing,proof(existing),c,session,true).action!=m::Action::BlockedUnknown)return false;
 c.safety.source131AfterArmedSequence=existing.sequence;session={};
 if(m::authorizeApply(existing,c,session).action!=m::Action::BlockedUnknown)return false;
 }
 auto c=context();c.safety.ownedGctDeadlineBound=true;c.safety.ownedGctStartedMs=100;
 if(m::arm(m::Load::Missing,binding(),c).action!=m::Action::BlockedUnknown)return false;
 return true;
}static_assert(cases());
''')


if __name__ == '__main__':
    unittest.main()
