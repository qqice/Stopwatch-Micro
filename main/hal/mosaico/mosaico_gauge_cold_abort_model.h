#pragma once
#include "mosaico_gauge_cold_trial_model.h"

// Narrow exception for an interrupted *current* forward operation whose every
// mutating ACK is known. It never opens access or reauthorizes a crashed attempt.
// The ordinary closed/boot restore planners intentionally remain unchanged.
namespace mosaico_gauge_cold_abort {
namespace trial=mosaico_gauge_cold_trial;
enum class ParentPhase:uint8_t {Unknown,Pending,Restoring};
struct Witness {
    bool exactCurrentOwner=false,forwardOperation=false,allMutatorAcksKnown=false;
    bool noSubsequentWriter=false;
    ParentPhase parentPhase=ParentPhase::Unknown;
    uint32_t parentGeneration=0;uint64_t parentEpoch=0,originalStartedMs=0;
};
constexpr bool owned(const trial::Record& r,const trial::Context& c,
                     const trial::Session& session,const Witness& w,bool restoring){
    const auto& s=c.safety;const auto& b=r.binding;
    const uint32_t increment=restoring?2:1;
    return trial::checked(r,c)==trial::Error::None&&!session.blocked&&session.applyReleased&&
        w.exactCurrentOwner&&w.forwardOperation&&w.allMutatorAcksKnown&&w.noSubsequentWriter&&
        w.parentPhase==(restoring?ParentPhase::Restoring:ParentPhase::Pending)&&
        b.originalGctGeneration<=UINT32_MAX-increment&&b.originalGctEpoch<=UINT64_MAX-increment&&
        w.parentGeneration==b.originalGctGeneration+increment&&w.parentEpoch==b.originalGctEpoch+increment&&
        trial::sameBoot(r.armedBoot,c.boot)&&w.originalStartedMs==r.armedMs&&
        c.nowMs>=r.armedMs&&c.nowMs-r.armedMs<trial::armedLimitMs&&
        r.phase==(restoring?trial::Phase::Restoring:trial::Phase::Armed)&&r.sequence==(restoring?2U:1U)&&
        !r.handoffMs&&trial::emptyBoot(r.claimedBoot)&&!r.claimMs&&
        r.capture==trial::CaptureResult::None&&!trial::nonzero(r.evidenceDigest)&&
        s.identityVerified&&s.originalGctBindingVerified&&s.durableAuthenticatedSource131&&
        s.reviewedOwned3Model&&s.twoRecoverySlotsSdkValid&&s.securityKnown&&s.securityLevel==1&&
        !s.sealed&&s.cal0&&s.voltageMv>=3500&&s.voltageMv<=4350&&
        s.temperatureDK>=2832&&s.temperatureDK<=3131;
}
constexpr trial::Decision intent(const trial::Record& r,const trial::Context& c,
                                 const trial::Session& session,const Witness& w){
    if(!owned(r,c,session,w,false)||session.inspectionReleased||session.restoreReleased)
        return trial::result(trial::Action::BlockedUnknown,trial::Error::Safety);
    auto next=r;++next.sequence;next.phase=trial::Phase::Restoring;
    return trial::transition(r,next)?trial::persist(next):trial::result(trial::Action::BlockedUnknown,trial::Error::Sequence);
}
// Call only AFTER the exact Restoring body has been saved and freshly read
// back. The existing GCT must also have advanced Pending->Restoring. SEC=1 is
// retained; the caller is forbidden to send any new unseal/full-access keys.
constexpr trial::Decision inspect(const trial::Record& r,const trial::Context& c,
                                  trial::Session& session,const Witness& w){
    if(!owned(r,c,session,w,true)||session.inspectionReleased||session.restoreReleased)
        return trial::result(trial::Action::BlockedUnknown,trial::Error::Safety);
    session.inspectionReleased=true;return trial::result(trial::Action::InspectForRestore);
}
constexpr trial::Decision restore(const trial::Record& r,const trial::Context& c,
                                  trial::Session& session,const Witness& w){
    const auto& s=c.safety;
    if(!owned(r,c,session,w,true)||!session.inspectionReleased||session.restoreReleased||
       !s.freshAuthenticated131||!s.calibrationUnchanged||
       !(s.image==trial::Image::Source131||s.image==trial::Image::TargetC131||s.image==trial::Image::Owned3Mixed))
        return trial::result(trial::Action::BlockedUnknown,trial::Error::Safety);
    session.restoreReleased=true;return trial::result(trial::Action::RestoreOriginalOwned3);
}
}
