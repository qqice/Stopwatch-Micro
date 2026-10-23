#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

// Pure prospective one-shot protocol; no storage codec, commands, IC/NVS/NAND
// operation, physical power automation, or physical-unit calibration is here.
// Digest and durability attestations must come from the trusted wrapper, not
// from standard65 readings or a guessed/default/legacy journal state.
namespace mosaico_gauge_cold_trial {
inline constexpr uint16_t protocolVersion=1;
inline constexpr uint32_t sourceCrc=0x70a767beU,targetCrc=0x91336fc0U;
inline constexpr uint64_t armedLimitMs=720000,handoffGraceMs=30000,maxCaptureMs=60000,resumedStartupLimitMs=120000;
inline constexpr uint64_t armedRestoreTriggerMs=600000,resumedCaptureLimitMs=60000;
// Restore triggers reserve120s/60s BEFORE the unchanged720s/120s hard gates.
// They do not guarantee any OS/storage/I2C completion duration or successful recovery.
inline constexpr bool physicalUnitsTrusted=false,offWallClockGuaranteed=false;
using Nonce=std::array<uint8_t,16>;
using Digest=std::array<uint8_t,32>;
enum class Phase:uint8_t {Armed=1,HandedOff=2,CaptureClaimed=3,Captured=4,CaptureFailed=5,Restoring=6,Restored=7,BlockedUnknown=8};
enum class RecoveryBinding:uint8_t {SlotHashes=1,ContractDigest=2};
enum class CaptureResult:uint8_t {None,Success,KnownTimeout,KnownOverflow,KnownReadFailure,Interrupted,RejectedStartup,StartupWindowExpired};
enum class StartupKind:uint8_t {Unknown,PowerOn,Software,Watchdog,Brownout};
// PowerOn is the trusted MCU reset classification only. It does not prove
// gauge-rail/battery interruption, OFF duration, or a physically cold cell.
enum class Image:uint8_t {Unknown,Source131,TargetC131,Owned3Mixed,Unexpected};
enum class Load:uint8_t {Missing,Present,Corrupt,IoUnknown};
enum class Commit:uint8_t {ExactDurable,NotWritten,UnknownOrTorn};
struct Identity {uint16_t device=0x0220,firmware=0x0003;uint8_t build=4;};
// Runtime creates a nonzero128-bit TRNG boot nonce once per startup. verified
// is a trusted-source assertion, NOT BootTraceCount/NVS-counter authentication.
// Collisions are a128-bit probabilistic boundary; this is not proof of OFF.
struct BootToken {Nonce nonce{};bool verified=false;};
struct Binding {
    std::array<uint8_t,6> mac{};Identity identity{};
    uint32_t source=sourceCrc,target=targetCrc,originalGctGeneration=0;
    uint64_t originalGctEpoch=0;Nonce nonce{};
    RecoveryBinding recovery=RecoveryBinding::SlotHashes;
    std::array<Digest,2> slotHashes{};Digest contractDigest{},source131ReceiptDigest{};
    uint32_t captureBudgetMs=30000; // immutable one-shot monotonic budget, <=60s
};
struct Record {
    uint16_t schema=protocolVersion;Binding binding{};
    uint32_t sequence=0;Phase phase=Phase::Armed;
    BootToken armedBoot{},claimedBoot{};
    uint64_t armedMs=0,handoffMs=0,claimMs=0;
    CaptureResult capture=CaptureResult::None;Digest evidenceDigest{};
};
struct Safety {
    bool identityVerified=false,originalGctBindingVerified=false,securityKnown=false;
    // Trusted wrapper must check a retained durable attempt record: no pending
    // or unknown key/IC-write ACK. A new Session or SEALED standard reading does
    // NOT establish this assertion or clear an unresolved attempt after crash.
    bool operationKnownSafeForInspection=false;
    bool durableAuthenticatedSource131=false,reviewedOwned3Model=false;
    bool twoRecoverySlotsSdkValid=false,sealed=false,cfg0=false,cal0=false;
    uint16_t voltageMv=0,temperatureDK=0;
    // Distinct attestation: standard65 alone NEVER implies fresh full131.
    bool targetStandard65=false,freshAuthenticated131=false,calibrationUnchanged=false;
    Image image=Image::Unknown;
};
struct Context {
    Binding expected{};BootToken boot{};uint64_t nowMs=0;Safety safety{};
    StartupKind startupKind=StartupKind::Unknown;
    bool recordDurable=false,nonceReservedFresh=false,legacyFallbackRequested=false;
};
// Exclusive current-boot volatile latch. Do NOT reset within a startup. On
// reboot startup() handles a persisted claim as interrupted, never recaptures.
struct Session {bool applyReleased=false,claimReleased=false,captureFinished=false,inspectionReleased=false,restoreReleased=false,blocked=false;};
struct Proof {
    Commit result=Commit::UnknownOrTorn;
    bool fullRecordReadbackIdentical=false,source131StillDurable=false;
    uint32_t sequence=0;Phase phase=Phase::BlockedUnknown;Nonce nonce{};
};
enum class Action:uint8_t {
    Reject,BlockedUnknown,NoColdTrial,PersistRecord,KeepArmed720,AwaitPowerOffGrace,
    ApplyC65Owned3,ReadOnlyCapture,FailureEvidenceRequired,RestoreRequired,InspectForRestore,RestoreOriginalOwned3,Complete
};
enum class Error:uint8_t {None,Shape,Binding,Boot,Nonce,Phase,Sequence,Durability,Unknown,LegacyFallback,Safety,Evidence,Expired,Replay};
struct Decision {
    Action action=Action::Reject;Error error=Error::None;Record next{};bool hasNext=false;
    bool publishPhysicalUnits=false; // never true, including Restored
};
template<class A> constexpr bool nonzero(const A& a){for(auto v:a)if(v)return true;return false;}
constexpr bool identity(Identity a){return a.device==0x0220&&a.firmware==0x0003&&a.build==4;}
constexpr bool sameIdentity(Identity a,Identity b){return a.device==b.device&&a.firmware==b.firmware&&a.build==b.build;}
constexpr bool sameBoot(const BootToken& a,const BootToken& b){return a.verified&&b.verified&&nonzero(a.nonce)&&a.nonce==b.nonce;}
constexpr bool validBoot(const BootToken& b){return b.verified&&nonzero(b.nonce);}
constexpr bool emptyBoot(const BootToken& b){return !b.verified&&!nonzero(b.nonce);}
constexpr bool sameBinding(const Binding& a,const Binding& b){
    return a.mac==b.mac&&sameIdentity(a.identity,b.identity)&&a.source==b.source&&a.target==b.target&&
        a.originalGctGeneration==b.originalGctGeneration&&a.originalGctEpoch==b.originalGctEpoch&&a.nonce==b.nonce&&
        a.recovery==b.recovery&&a.slotHashes==b.slotHashes&&a.contractDigest==b.contractDigest&&
        a.source131ReceiptDigest==b.source131ReceiptDigest&&a.captureBudgetMs==b.captureBudgetMs;
}
constexpr bool validBinding(const Binding& b){
    if(!nonzero(b.mac)||(b.mac[0]&1)||!identity(b.identity)||b.source!=sourceCrc||b.target!=targetCrc||
       !b.originalGctGeneration||!b.originalGctEpoch||!nonzero(b.nonce)||!nonzero(b.source131ReceiptDigest)||
       !b.captureBudgetMs||b.captureBudgetMs>maxCaptureMs)return false;
    if(b.recovery==RecoveryBinding::SlotHashes)return nonzero(b.slotHashes[0])&&nonzero(b.slotHashes[1])&&
        b.slotHashes[0]!=b.slotHashes[1]&&!nonzero(b.contractDigest);
    return b.recovery==RecoveryBinding::ContractDigest&&nonzero(b.contractDigest)&&
        !nonzero(b.slotHashes[0])&&!nonzero(b.slotHashes[1]);
}
constexpr bool knownFailure(CaptureResult result){return result==CaptureResult::KnownTimeout||result==CaptureResult::KnownOverflow||
    result==CaptureResult::KnownReadFailure||result==CaptureResult::Interrupted||result==CaptureResult::RejectedStartup||result==CaptureResult::StartupWindowExpired;}
constexpr bool unclaimedFailure(CaptureResult r){return r==CaptureResult::RejectedStartup||r==CaptureResult::StartupWindowExpired;}
constexpr bool terminal(Phase p){return p==Phase::Restored||p==Phase::BlockedUnknown;}
constexpr bool reachableSequence(const Record& r,bool claimed){
    const bool handed=r.handoffMs>=r.armedMs&&r.handoffMs-r.armedMs<armedRestoreTriggerMs;
    if(r.phase==Phase::Armed)return r.sequence==1;
    if(r.phase==Phase::HandedOff)return r.sequence==2;
    if(r.phase==Phase::CaptureClaimed)return r.sequence==3;
    if(r.phase==Phase::Captured)return r.sequence==4;
    if(r.phase==Phase::CaptureFailed)return r.sequence==(unclaimedFailure(r.capture)?3U:4U);
    if(r.phase==Phase::Restoring||r.phase==Phase::Restored){
        const uint32_t extra=r.phase==Phase::Restored?1:0;
        if(r.capture==CaptureResult::None)return (r.sequence==2+extra&&r.handoffMs==0)||
            (r.sequence==3+extra&&handed);
        return r.sequence==(unclaimedFailure(r.capture)?4U:5U)+extra;
    }
    if(r.phase!=Phase::BlockedUnknown)return false;
    if(r.capture==CaptureResult::None){
        if(claimed)return r.sequence==4;
        return (r.sequence==2&&r.handoffMs==0)||r.sequence==3||(r.sequence==4&&handed);
    }
    return unclaimedFailure(r.capture)?r.sequence==4||r.sequence==5:r.sequence==5||r.sequence==6;
}
constexpr bool shape(const Record& r){
    if(r.schema!=protocolVersion||!r.sequence||!validBinding(r.binding)||!validBoot(r.armedBoot)||
       uint8_t(r.phase)<1||uint8_t(r.phase)>8||
       !(r.capture==CaptureResult::None||r.capture==CaptureResult::Success||knownFailure(r.capture)))return false;
    if((r.capture==CaptureResult::None&&nonzero(r.evidenceDigest))||
       (r.capture!=CaptureResult::None&&!nonzero(r.evidenceDigest)))return false;
    const bool claimed=validBoot(r.claimedBoot)&&!sameBoot(r.armedBoot,r.claimedBoot);
    if(!claimed&&!emptyBoot(r.claimedBoot))return false;
    if(r.handoffMs&&(r.handoffMs<r.armedMs||r.handoffMs-r.armedMs>=armedRestoreTriggerMs))return false;
    if(!claimed&&r.claimMs)return false;
    if(claimed&&r.claimMs>=resumedCaptureLimitMs)return false;
    if((r.phase==Phase::HandedOff||claimed||unclaimedFailure(r.capture))&&
       (r.handoffMs<r.armedMs||r.handoffMs-r.armedMs>=armedRestoreTriggerMs))return false;
    if(!reachableSequence(r,claimed))return false;
    if(r.phase==Phase::Armed||r.phase==Phase::HandedOff){
        if(!emptyBoot(r.claimedBoot)||r.claimMs||r.capture!=CaptureResult::None||nonzero(r.evidenceDigest))return false;
        return r.phase==Phase::Armed?r.handoffMs==0:true;
    }
    if(r.phase==Phase::CaptureClaimed)return claimed&&r.capture==CaptureResult::None&&!nonzero(r.evidenceDigest);
    if(r.phase==Phase::Captured)return claimed&&r.capture==CaptureResult::Success&&nonzero(r.evidenceDigest);
    if(r.phase==Phase::CaptureFailed)return knownFailure(r.capture)&&nonzero(r.evidenceDigest)&&
        (unclaimedFailure(r.capture)?!claimed:claimed);
    if(r.phase==Phase::Restoring||r.phase==Phase::Restored)return
        (r.capture==CaptureResult::None&&!nonzero(r.evidenceDigest)&&!claimed)||
        ((r.capture==CaptureResult::Success||knownFailure(r.capture))&&nonzero(r.evidenceDigest)&&
            (unclaimedFailure(r.capture)?!claimed:claimed));
    // BlockedUnknown must preserve the same owned/unclaimed outcome shape too;
    // it is not a place to encode an unknown enum or erase prior evidence.
    return r.capture==CaptureResult::None||
        (unclaimedFailure(r.capture)?!claimed:claimed);
}
constexpr bool initial(const Record& r){return shape(r)&&r.sequence==1&&r.phase==Phase::Armed;}
constexpr bool sameRecord(const Record& a,const Record& b){
    return a.schema==b.schema&&sameBinding(a.binding,b.binding)&&a.sequence==b.sequence&&a.phase==b.phase&&
        a.armedBoot.nonce==b.armedBoot.nonce&&a.armedBoot.verified==b.armedBoot.verified&&
        a.claimedBoot.nonce==b.claimedBoot.nonce&&a.claimedBoot.verified==b.claimedBoot.verified&&
        a.armedMs==b.armedMs&&a.handoffMs==b.handoffMs&&a.claimMs==b.claimMs&&a.capture==b.capture&&a.evidenceDigest==b.evidenceDigest;
}
constexpr bool transition(const Record& a,const Record& b){
    // Structural journal chain only. Contextual boot/safety authorization must
    // also use the planners below; do not write arbitrary phase-only records.
    if(!shape(a)||!shape(b)||terminal(a.phase)||!sameBinding(a.binding,b.binding)||a.sequence==UINT32_MAX||b.sequence!=a.sequence+1||
       !sameBoot(a.armedBoot,b.armedBoot)||a.armedMs!=b.armedMs)return false;
    if(!(a.phase==Phase::Armed&&b.phase==Phase::HandedOff)&&b.handoffMs!=a.handoffMs)return false;
    const bool createsClaim=a.phase==Phase::HandedOff&&b.phase==Phase::CaptureClaimed;
    if(!createsClaim&&(a.claimedBoot.nonce!=b.claimedBoot.nonce||a.claimedBoot.verified!=b.claimedBoot.verified||a.claimMs!=b.claimMs))return false;
    const bool addsEvidence=(a.phase==Phase::CaptureClaimed&&(b.phase==Phase::Captured||b.phase==Phase::CaptureFailed))||
        (a.phase==Phase::HandedOff&&b.phase==Phase::CaptureFailed&&unclaimedFailure(b.capture));
    if(!addsEvidence&&(a.capture!=b.capture||a.evidenceDigest!=b.evidenceDigest))return false;
    if(validBoot(a.claimedBoot)&&(!sameBoot(a.claimedBoot,b.claimedBoot)||b.claimMs!=a.claimMs))return false;
    if(nonzero(a.evidenceDigest)&&(a.evidenceDigest!=b.evidenceDigest||a.capture!=b.capture))return false;
    if(b.phase==Phase::BlockedUnknown)return a.handoffMs==b.handoffMs&&a.claimedBoot.nonce==b.claimedBoot.nonce&&
        a.claimedBoot.verified==b.claimedBoot.verified&&a.claimMs==b.claimMs&&a.capture==b.capture&&a.evidenceDigest==b.evidenceDigest;
    return (a.phase==Phase::Armed&&(b.phase==Phase::HandedOff||b.phase==Phase::Restoring))||
        (a.phase==Phase::HandedOff&&(b.phase==Phase::CaptureClaimed||b.phase==Phase::Restoring||b.phase==Phase::CaptureFailed))||
        (a.phase==Phase::CaptureClaimed&&(b.phase==Phase::Captured||b.phase==Phase::CaptureFailed))||
        ((a.phase==Phase::Captured||a.phase==Phase::CaptureFailed)&&b.phase==Phase::Restoring)||
        (a.phase==Phase::Restoring&&b.phase==Phase::Restored);
}
constexpr Decision result(Action a,Error e=Error::None){Decision d{};d.action=a;d.error=e;return d;}
constexpr Decision persist(const Record& r){auto d=result(Action::PersistRecord);d.next=r;d.hasNext=true;return d;}
constexpr Error checked(const Record& r,const Context& c){
    if(c.legacyFallbackRequested)return Error::LegacyFallback;
    if(!shape(r))return Error::Shape;
    if(!sameBinding(r.binding,c.expected))return Error::Binding;
    if(!validBoot(c.boot))return Error::Boot;
    if(!c.recordDurable)return Error::Durability;
    return Error::None;
}
constexpr bool safe(const Context& c,bool full131,bool requireClosed=true){
    const auto& s=c.safety;
    return s.identityVerified&&s.originalGctBindingVerified&&s.durableAuthenticatedSource131&&s.reviewedOwned3Model&&
        s.twoRecoverySlotsSdkValid&&s.securityKnown&&s.operationKnownSafeForInspection&&(!requireClosed||s.sealed)&&s.cfg0&&s.cal0&&s.voltageMv>=3500&&s.voltageMv<=4350&&
        s.temperatureDK>=2832&&s.temperatureDK<=3131&&(!full131||(s.freshAuthenticated131&&s.calibrationUnchanged&&
        (s.image==Image::Source131||s.image==Image::TargetC131||s.image==Image::Owned3Mixed)));
}
constexpr bool operationWindow(const Record& r,const Context& c){
    return sameBoot(r.armedBoot,c.boot)?c.nowMs>=r.armedMs&&c.nowMs-r.armedMs<armedLimitMs:c.nowMs<resumedStartupLimitMs;
}
constexpr Decision arm(Load loaded,const Binding& binding,const Context& c){
    if(loaded!=Load::Missing||!c.nonceReservedFresh||c.legacyFallbackRequested||!validBinding(binding)||
       !sameBinding(binding,c.expected)||!validBoot(c.boot)||!safe(c,true)||c.safety.voltageMv<4000||c.safety.image!=Image::Source131)
        return result(Action::BlockedUnknown,Error::Nonce);
    Record r{};r.binding=binding;r.sequence=1;r.armedBoot=c.boot;r.armedMs=c.nowMs;return persist(r);
}
constexpr Decision handoff(const Record& r,const Context& c){
    const auto error=checked(r,c);if(error!=Error::None)return result(Action::BlockedUnknown,error);
    if(r.phase!=Phase::Armed||!sameBoot(r.armedBoot,c.boot)||c.nowMs<r.armedMs||c.nowMs-r.armedMs>=armedRestoreTriggerMs||
       !safe(c,true)||c.safety.voltageMv<4000||c.safety.image!=Image::TargetC131)return result(Action::Reject,Error::Phase);
    auto next=r;++next.sequence;next.phase=Phase::HandedOff;next.handoffMs=c.nowMs;
    return transition(r,next)?persist(next):result(Action::BlockedUnknown,Error::Sequence);
}
constexpr Decision startup(Load loaded,const Record& r,const Context& c){
    if(c.legacyFallbackRequested)return result(Action::BlockedUnknown,Error::LegacyFallback);
    if(loaded==Load::Missing)return result(Action::NoColdTrial);
    if(loaded!=Load::Present)return result(Action::BlockedUnknown,Error::Unknown);
    const auto error=checked(r,c);if(error!=Error::None)return result(Action::BlockedUnknown,error);
    if(r.phase==Phase::BlockedUnknown)return result(Action::BlockedUnknown,Error::Unknown);
    if(r.phase==Phase::Restored)return result(Action::Complete);
    if(r.phase==Phase::Armed){
        if(sameBoot(r.armedBoot,c.boot)&&c.nowMs<r.armedMs)return result(Action::BlockedUnknown,Error::Boot);
        if(sameBoot(r.armedBoot,c.boot)&&c.nowMs>=r.armedMs&&c.nowMs-r.armedMs<armedRestoreTriggerMs)return result(Action::KeepArmed720);
        return result(Action::RestoreRequired);
    }
    if(r.phase==Phase::HandedOff){
        if(sameBoot(r.armedBoot,c.boot)){
            if(c.nowMs<r.armedMs||c.nowMs<r.handoffMs)return result(Action::BlockedUnknown,Error::Boot);
            if(c.nowMs>=r.armedMs&&c.nowMs>=r.handoffMs&&c.nowMs-r.armedMs<armedRestoreTriggerMs&&c.nowMs-r.handoffMs<handoffGraceMs)
                return result(Action::AwaitPowerOffGrace);
            return result(Action::RestoreRequired);
        }
        // First observed different boot, not an attestation of physical OFF or
        // exact boot ordinal. No capture data is read before durable claim.
        if(c.startupKind!=StartupKind::PowerOn)return result(Action::FailureEvidenceRequired,Error::Boot);
        if(!operationWindow(r,c)||c.nowMs>=resumedCaptureLimitMs)return result(Action::FailureEvidenceRequired,Error::Expired);
        auto next=r;++next.sequence;next.phase=Phase::CaptureClaimed;next.claimedBoot=c.boot;next.claimMs=c.nowMs;
        return transition(r,next)?persist(next):result(Action::BlockedUnknown,Error::Sequence);
    }
    if(r.phase==Phase::CaptureClaimed)return result(Action::FailureEvidenceRequired,Error::Evidence);
    return result(Action::RestoreRequired); // Captured/Failed/Restoring: never recapture.
}
constexpr Decision afterPersist(const Record& before,const Record& next,const Proof& p,const Context& c,Session& session,bool first=false){
    if(session.blocked)return result(Action::BlockedUnknown,Error::Unknown);
    if((first?!initial(next):!transition(before,next))||p.result!=Commit::ExactDurable||!p.fullRecordReadbackIdentical||
       !p.source131StillDurable||p.sequence!=next.sequence||p.phase!=next.phase||p.nonce!=next.binding.nonce||
       checked(next,c)!=Error::None){session.blocked=true;return result(Action::BlockedUnknown,Error::Durability);}
    if(next.phase==Phase::Armed){
        if(!sameBoot(next.armedBoot,c.boot)||!safe(c,true)||c.safety.image!=Image::Source131){session.blocked=true;return result(Action::BlockedUnknown,Error::Safety);}
        return startup(Load::Present,next,c);
    }
    if(next.phase==Phase::HandedOff)return startup(Load::Present,next,c);
    if(next.phase==Phase::CaptureClaimed){
        if(session.claimReleased||!sameBoot(next.claimedBoot,c.boot)||c.startupKind!=StartupKind::PowerOn||!safe(c,false)||!operationWindow(next,c)||
           c.nowMs<next.claimMs||c.nowMs-next.claimMs>=next.binding.captureBudgetMs||c.nowMs>=resumedCaptureLimitMs){session.blocked=true;return result(Action::BlockedUnknown,Error::Replay);}
        session.claimReleased=true;return result(Action::ReadOnlyCapture);
    }
    if(next.phase==Phase::Restored){
        if(!safe(c,true)||c.safety.image!=Image::Source131||!operationWindow(next,c)){session.blocked=true;return result(Action::BlockedUnknown,Error::Safety);}
        return result(Action::Complete);
    }
    if(next.phase==Phase::BlockedUnknown){session.blocked=true;return result(Action::BlockedUnknown);}
    return result(Action::RestoreRequired);
}
constexpr Decision authorizeApply(const Record& r,const Context& c,Session& session){
    const auto error=checked(r,c);if(error!=Error::None||session.blocked)return result(Action::BlockedUnknown,error);
    if(r.phase!=Phase::Armed||!sameBoot(r.armedBoot,c.boot)||!safe(c,true)||c.safety.voltageMv<4000||c.safety.image!=Image::Source131||
       c.nowMs<r.armedMs||c.nowMs-r.armedMs>=armedRestoreTriggerMs||session.applyReleased){session.blocked=true;return result(Action::BlockedUnknown,Error::Safety);}
    session.applyReleased=true;return result(Action::ApplyC65Owned3);
}
constexpr Decision capturePoll(const Record& r,const Context& c,const Session& session){
    const auto error=checked(r,c);if(error!=Error::None||session.blocked)return result(Action::BlockedUnknown,error);
    if(r.phase!=Phase::CaptureClaimed||!session.claimReleased||session.captureFinished||!sameBoot(r.claimedBoot,c.boot))return result(Action::BlockedUnknown,Error::Phase);
    if(c.nowMs<r.claimMs)return result(Action::BlockedUnknown,Error::Boot);
    return result(c.nowMs-r.claimMs>=r.binding.captureBudgetMs||!operationWindow(r,c)||c.nowMs>=resumedCaptureLimitMs?Action::FailureEvidenceRequired:Action::ReadOnlyCapture);
}
constexpr Decision captureEvidence(const Record& r,const Context& c,Session& session,CaptureResult captured,const Digest& digest){
    const auto error=checked(r,c);if(error!=Error::None||session.blocked)return result(Action::BlockedUnknown,error);
    if(r.phase!=Phase::CaptureClaimed||!sameBoot(r.claimedBoot,c.boot)||!session.claimReleased||session.captureFinished||
       !nonzero(digest)||!(captured==CaptureResult::Success||knownFailure(captured)))return result(Action::BlockedUnknown,Error::Evidence);
    if(captured==CaptureResult::Success&&(c.nowMs<r.claimMs||c.nowMs-r.claimMs>=r.binding.captureBudgetMs||!operationWindow(r,c)||c.nowMs>=resumedCaptureLimitMs))return result(Action::Reject,Error::Expired);
    if(captured==CaptureResult::Success&&(!safe(c,false)||!c.safety.targetStandard65)){session.blocked=true;return result(Action::BlockedUnknown,Error::Safety);}
    auto next=r;++next.sequence;next.phase=captured==CaptureResult::Success?Phase::Captured:Phase::CaptureFailed;
    next.capture=captured;next.evidenceDigest=digest;session.captureFinished=true;
    return transition(r,next)?persist(next):result(Action::BlockedUnknown,Error::Sequence);
}
constexpr Decision interruptedEvidence(const Record& r,const Context& c,const Digest& digest){
    const auto error=checked(r,c);if(error!=Error::None)return result(Action::BlockedUnknown,error);
    if(r.phase!=Phase::CaptureClaimed||!nonzero(digest))return result(Action::Reject,Error::Phase);
    auto next=r;++next.sequence;next.phase=Phase::CaptureFailed;next.capture=CaptureResult::Interrupted;next.evidenceDigest=digest;
    return transition(r,next)?persist(next):result(Action::BlockedUnknown,Error::Sequence);
}
constexpr Decision rejectedStartupEvidence(const Record& r,const Context& c,const Digest& digest){
    const auto error=checked(r,c);if(error!=Error::None)return result(Action::BlockedUnknown,error);
    if(r.phase!=Phase::HandedOff||sameBoot(r.armedBoot,c.boot)||
       (c.startupKind==StartupKind::PowerOn&&c.nowMs<resumedCaptureLimitMs)||!nonzero(digest))return result(Action::Reject,Error::Boot);
    auto next=r;++next.sequence;next.phase=Phase::CaptureFailed;
    next.capture=c.startupKind==StartupKind::PowerOn?CaptureResult::StartupWindowExpired:CaptureResult::RejectedStartup;
    next.evidenceDigest=digest;
    return transition(r,next)?persist(next):result(Action::BlockedUnknown,Error::Sequence);
}
constexpr Decision restoreIntent(const Record& r,const Context& c){
    const auto error=checked(r,c);if(error!=Error::None)return result(Action::BlockedUnknown,error);
    if(!safe(c,false)||!operationWindow(r,c))return result(Action::BlockedUnknown,Error::Safety);
    if(r.phase==Phase::CaptureClaimed)return result(Action::FailureEvidenceRequired,Error::Evidence);
    if(r.phase==Phase::HandedOff&&!sameBoot(r.armedBoot,c.boot))return result(Action::FailureEvidenceRequired,Error::Evidence);
    if(r.phase==Phase::Restoring)return result(Action::RestoreRequired);
    auto next=r;++next.sequence;next.phase=Phase::Restoring;
    return transition(r,next)?persist(next):result(Action::BlockedUnknown,Error::Phase);
}
constexpr Decision authorizeRestoreInspection(const Record& r,const Context& c,Session& session){
    const auto error=checked(r,c);if(error!=Error::None||session.blocked)return result(Action::BlockedUnknown,error);
    if(r.phase!=Phase::Restoring||!safe(c,false)||!operationWindow(r,c)||session.inspectionReleased){session.blocked=true;return result(Action::BlockedUnknown,Error::Safety);}
    session.inspectionReleased=true;return result(Action::InspectForRestore); // FA read only; NOT CFG/parameter admission.
}
constexpr Decision authorizeRestore(const Record& r,const Context& c,Session& session){
    const auto error=checked(r,c);if(error!=Error::None||session.blocked)return result(Action::BlockedUnknown,error);
    if(r.phase!=Phase::Restoring||!session.inspectionReleased||!safe(c,true,false)||!operationWindow(r,c)||session.restoreReleased){session.blocked=true;return result(Action::BlockedUnknown,Error::Safety);}
    // Capture outcomes (including known timeout/overflow) must already be in
    // the committed record, not merely buffered on UART or a host filesystem.
    session.restoreReleased=true;return result(Action::RestoreOriginalOwned3);
}
constexpr Decision restored(const Record& r,const Context& c){
    const auto error=checked(r,c);if(error!=Error::None)return result(Action::BlockedUnknown,error);
    if(r.phase!=Phase::Restoring||!safe(c,true)||!operationWindow(r,c)||c.safety.image!=Image::Source131)return result(Action::BlockedUnknown,Error::Safety);
    auto next=r;++next.sequence;next.phase=Phase::Restored;
    return transition(r,next)?persist(next):result(Action::BlockedUnknown,Error::Sequence);
}
constexpr Decision unknownOutcome(Session& session){session.blocked=true;return result(Action::BlockedUnknown,Error::Unknown);}
} // namespace mosaico_gauge_cold_trial
