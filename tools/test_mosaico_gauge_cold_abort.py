"""Pure owned-forward-abort admission, not a runtime key/ACK attestation."""
import unittest
import test_mosaico_gauge_cold_trial as base

PREFIX = r'''
#include "mosaico_gauge_cold_abort_model.h"
namespace abort_model=mosaico_gauge_cold_abort;
constexpr m::Context abortContext(){auto c=context();c.nowMs=500;c.safety.securityLevel=1;
 c.safety.sealed=false;c.safety.cfg0=false;c.safety.operationKnownSafeForInspection=false;
 c.safety.image=m::Image::Owned3Mixed;return c;}
constexpr m::Session abortSession(){m::Session s{};s.applyReleased=true;return s;}
constexpr abort_model::Witness abortWitness(bool restoring=false){abort_model::Witness w{};
 w.exactCurrentOwner=w.forwardOperation=w.allMutatorAcksKnown=w.noSubsequentWriter=true;
 w.parentPhase=restoring?abort_model::ParentPhase::Restoring:abort_model::ParentPhase::Pending;
 w.parentGeneration=restoring?21:20;w.parentEpoch=restoring?21:20;w.originalStartedMs=100;return w;}
'''


class ColdAbortTests(unittest.TestCase):
    def compile(self, code):
        base.ColdTrialTests.compile(self, PREFIX + code)

    def test_retained_FA_CFG1_rollback_requires_two_durable_intents_and_fresh131(self):
        self.compile(r'''
constexpr bool cases(){auto c=abortContext();auto s=abortSession();auto w=abortWitness();
 const auto r=armed();const auto decision=abort_model::intent(r,c,s,w);
 if(!decision.hasNext||decision.next.phase!=m::Phase::Restoring||!m::transition(r,decision.next))return false;
 // Ordinary closed-restore API remains closed, no generic CFG relaxation.
 if(m::restoreIntent(r,c).hasNext)return false;
 if(abort_model::inspect(decision.next,c,s,w).action!=m::Action::BlockedUnknown)return false;
 w=abortWitness(true);
 if(abort_model::restore(decision.next,c,s,w).action!=m::Action::BlockedUnknown)return false;
 if(abort_model::inspect(decision.next,c,s,w).action!=m::Action::InspectForRestore)return false;
 if(abort_model::inspect(decision.next,c,s,w).action!=m::Action::BlockedUnknown)return false;
 c.safety.freshAuthenticated131=false;
 if(abort_model::restore(decision.next,c,s,w).action!=m::Action::BlockedUnknown)return false;
 c.safety.freshAuthenticated131=true;
 if(abort_model::restore(decision.next,c,s,w).action!=m::Action::RestoreOriginalOwned3)return false;
 return abort_model::restore(decision.next,c,s,w).action==m::Action::BlockedUnknown;
}static_assert(cases());
''')

    def test_every_unknown_owner_ACK_boot_identity_calibration_or_clock_rejects(self):
        self.compile(r'''
constexpr bool cases(){for(unsigned fault=0;fault<32;++fault){auto c=abortContext();auto s=abortSession();auto w=abortWitness();auto r=armed();
 if(fault==0)w.exactCurrentOwner=false;
 if(fault==1)w.forwardOperation=false;
 if(fault==2)w.allMutatorAcksKnown=false;
 if(fault==3)w.noSubsequentWriter=false;
 if(fault==4)w.parentPhase=abort_model::ParentPhase::Unknown;
 if(fault==5)w.parentGeneration++;
 if(fault==6)w.parentEpoch++;
 if(fault==7)w.originalStartedMs++;
 if(fault==8)c.boot.nonce[0]++;
 if(fault==9)c.boot.verified=false;
 if(fault==10)c.nowMs=99;
 if(fault==11)c.nowMs=r.armedMs+m::armedLimitMs;
 if(fault==12)s.applyReleased=false;
 if(fault==13)s.blocked=true;
 if(fault==14)c.recordDurable=false;
 if(fault==15)c.safety.identityVerified=false;
 if(fault==16)c.safety.originalGctBindingVerified=false;
 if(fault==17)c.safety.durableAuthenticatedSource131=false;
 if(fault==18)c.safety.reviewedOwned3Model=false;
 if(fault==19)c.safety.twoRecoverySlotsSdkValid=false;
 if(fault==20)c.safety.securityKnown=false;
 if(fault==21)c.safety.securityLevel=3;
 if(fault==22)c.safety.sealed=true;
 if(fault==23)c.safety.cal0=false;
 if(fault==24)c.safety.voltageMv=3499;
 if(fault==25)c.safety.voltageMv=4351;
 if(fault==26)c.safety.temperatureDK=2831;
 if(fault==27)c.safety.temperatureDK=3132;
 if(fault==28)s.inspectionReleased=true;
 if(fault==29)s.restoreReleased=true;
 if(fault==30){r=handed();}if(fault==31)c.expected.nonce[0]++;
 if(abort_model::intent(r,c,s,w).hasNext)return false;
 }return true;}static_assert(cases());
''')

    def test_full131_only_source_target_or_owned_mixed_and_no_crossboot_session_reset(self):
        self.compile(r'''
constexpr bool cases(){for(unsigned image=0;image<5;++image){auto c=abortContext();auto s=abortSession();
 const auto r=abort_model::intent(armed(),c,s,abortWitness()).next;auto w=abortWitness(true);
 if(abort_model::inspect(r,c,s,w).action!=m::Action::InspectForRestore)return false;
 c.safety.image=m::Image(image);const auto decision=abort_model::restore(r,c,s,w);
 if((decision.action==m::Action::RestoreOriginalOwned3)!=(image>=1&&image<=3))return false;
 s=abortSession();
 if(abort_model::inspect(r,nextBoot(),s,w).action!=m::Action::BlockedUnknown)return false;
 s=abortSession();c=abortContext();c.safety.calibrationUnchanged=false;
 if(abort_model::inspect(r,c,s,w).action!=m::Action::InspectForRestore)return false;
 if(abort_model::restore(r,c,s,w).action!=m::Action::BlockedUnknown)return false;
 }return true;}static_assert(cases());
''')


if __name__ == '__main__':
    unittest.main()
