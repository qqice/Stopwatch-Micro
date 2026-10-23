#pragma once
#include "mosaico_gauge_cold_capsule.h"
#include "mosaico_gauge_cold_evidence_model.h"
#include <type_traits>

// Atomically stored metadata + raw evidence. Hasher is a trusted SHA256
// adapter; this header intentionally does NOT implement cryptography or IO.
namespace mosaico_gauge_cold_envelope {
namespace capsule=mosaico_gauge_cold_capsule;
namespace trial=mosaico_gauge_cold_trial;
namespace store=mosaico_gauge_cold_store;
namespace evidence=mosaico_gauge_cold_evidence;
inline constexpr size_t HeaderSize=48,RawOffset=HeaderSize+capsule::WireSize,MaxFrames=2;
inline constexpr uint32_t Magic=0x31454342;
static_assert(RawOffset+MaxFrames*evidence::WireSize<=store::PayloadMax);
using Frames=std::array<evidence::Wire,MaxFrames>;
struct Decoded {trial::Record metadata{};Frames frames{};uint8_t count=0;trial::Digest rawHash{};};
// Production callers allocate one workspace per exclusive operation (PSRAM in
// runtime). No global scratch. before/after stay distinct through transitions.
struct Workspace {
    store::Record inner{},staged{};
    Decoded candidate{},before{},after{};
    evidence::Frame first{},last{};
    // Synchronous reentrancy latch, NOT a mutex. Cross-task ownership must
    // remain exclusive in the caller (normally the existing runtime lock).
    bool busy=false;
};
namespace detail {
struct Use {
    Workspace& workspace;bool acquired;
    constexpr explicit Use(Workspace& w):workspace(w),acquired(!w.busy){if(acquired)w.busy=true;}
    constexpr ~Use(){if(acquired)workspace.busy=false;}
};
} // namespace detail
constexpr bool sameNonce(const evidence::Frame& f,const trial::BootToken& token){return token.verified&&f.bootNonce==token.nonce;}
constexpr bool completeRead(const evidence::Frame& f){
    return (f.standard.validMask&mosaico_gauge_status::AllRaw)==mosaico_gauge_status::AllRaw&&
        f.engine.validMask==0x1fff&&f.engine.echo73==0x73&&f.engine.echo76==0x76&&f.engine.echo02==2&&
        mosaico_gauge_engine::beWord(f.engine.rawIdentity,0)==0x0220&&
        mosaico_gauge_engine::beWord(f.engine.rawIdentity,2)==3&&mosaico_gauge_engine::beWord(f.engine.rawIdentity,4)==4;
}
constexpr bool frameRules(const Decoded& d,evidence::Frame& first,evidence::Frame& last){
    if(&first==&last)return false;
    const auto& r=d.metadata;
    if(!d.count)return r.capture==trial::CaptureResult::None&&!trial::nonzero(d.rawHash);
    if(d.count>MaxFrames||!trial::nonzero(d.rawHash))return false;
    if(!evidence::decode(d.frames[0],first)||!evidence::decode(d.frames[d.count-1],last))return false;
    if(r.capture==trial::CaptureResult::None){
        // An initial prefetch may already be durable with the consumed claim.
        // It is NOT a successful capture and can never authorize restoration.
        return (r.phase==trial::Phase::CaptureClaimed||r.phase==trial::Phase::BlockedUnknown)&&d.count==1&&
            first.kind==evidence::Kind::Initial&&sameNonce(first,r.claimedBoot);
    }
    if(r.evidenceDigest!=d.rawHash)return false;
    if(r.capture==trial::CaptureResult::Success){
        if(d.count!=2||first.kind!=evidence::Kind::Initial||last.kind!=evidence::Kind::Final||
           !sameNonce(first,r.claimedBoot)||!sameNonce(last,r.claimedBoot)||!completeRead(first)||!completeRead(last))return false;
        // Early RAM prefetch may precede durable claim; never fabricate its time.
        const auto budgetEnd=r.claimMs+r.binding.captureBudgetMs; // shape bounds both below120s.
        const auto limit=(budgetEnd<trial::resumedCaptureLimitMs?budgetEnd:trial::resumedCaptureLimitMs)*1000;
        const auto claim=r.claimMs*1000;
        return first.standard.sampledUs<limit&&first.engine.sampledUs<limit&&last.standard.sampledUs<limit&&last.engine.sampledUs<limit&&
            last.standard.sampledUs>=claim&&last.engine.sampledUs>=claim&&
            first.standard.sampledUs<=last.standard.sampledUs&&first.standard.sampledUs<=last.engine.sampledUs&&
            first.engine.sampledUs<=last.standard.sampledUs&&first.engine.sampledUs<=last.engine.sampledUs;
    }
    if(!trial::knownFailure(r.capture)||last.kind!=evidence::Kind::Failure)return false;
    if(d.count==2&&(first.kind!=evidence::Kind::Initial||!sameNonce(first,r.claimedBoot)))return false;
    if(r.capture==trial::CaptureResult::Interrupted)return last.bootNonce!=r.armedBoot.nonce;
    if(r.capture==trial::CaptureResult::RejectedStartup||r.capture==trial::CaptureResult::StartupWindowExpired)
        return d.count==1&&last.bootNonce!=r.armedBoot.nonce;
    return sameNonce(last,r.claimedBoot);
}
// Convenience scratch-on-stack variant; not for production call chains.
constexpr bool frameRules(const Decoded& d){evidence::Frame first{},last{};return frameRules(d,first,last);}

template<class Hasher> constexpr bool rawDigest(const store::Record& record,uint8_t count,const Hasher& hash,trial::Digest& digest){
    digest={};
    if(count>MaxFrames)return false;
    return !count||hash(record.payload.data()+RawOffset,size_t(count)*evidence::WireSize,digest);
}
namespace detail {
constexpr bool bindingDigestAlias(const trial::Digest& digest,const trial::Binding& binding){
    return &digest==&binding.slotHashes[0]||&digest==&binding.slotHashes[1]||
        &digest==&binding.contractDigest||&digest==&binding.source131ReceiptDigest;
}
constexpr bool decodedTrustedAlias(const trial::Binding& expected,const trial::Digest& digest,const Decoded& decoded){
    return &expected==&decoded.metadata.binding||&digest==&decoded.rawHash||
        &digest==&decoded.metadata.evidenceDigest||bindingDigestAlias(digest,decoded.metadata.binding);
}
constexpr bool commonAlias(const trial::Binding& expected,const trial::Digest& bindingDigest,const Workspace& w){
    // Trusted inputs must remain external to ALL reusable scratch, including
    // before/after retained across Policy decodes. In particular inner.binding
    // is overwritten before capsule::decode and cannot be its own trust anchor.
    return &bindingDigest==&w.inner.binding||&bindingDigest==&w.staged.binding||
        decodedTrustedAlias(expected,bindingDigest,w.candidate)||
        decodedTrustedAlias(expected,bindingDigest,w.before)||decodedTrustedAlias(expected,bindingDigest,w.after);
}
// Internal implementations require exclusive Use and non-aliased arguments.
// All potentially large temporary records are fields of the caller workspace.
template<class Hasher> constexpr bool encodeInto(const trial::Record& metadata,const Frames& frames,uint8_t count,
    const trial::Binding& expected,const trial::Digest& bindingDigest,const Hasher& hash,store::Record& out,Workspace& w){
    if(count>MaxFrames)return false;
    if(!capsule::encode(metadata,expected,bindingDigest,w.inner))return false;
    auto& staged=w.staged;staged=w.inner;staged.payload={};staged.size=uint16_t(RawOffset+size_t(count)*evidence::WireSize);
    auto& p=staged.payload;capsule::put(p,0,Magic,4);capsule::put(p,4,1,2);capsule::put(p,6,HeaderSize,2);
    capsule::put(p,8,capsule::WireSize,2);capsule::put(p,10,evidence::WireSize,2);p[12]=count;
    for(size_t i=0;i<capsule::WireSize;++i)p[HeaderSize+i]=w.inner.payload[i];
    for(size_t i=0;i<count;++i)for(size_t j=0;j<evidence::WireSize;++j)p[RawOffset+i*evidence::WireSize+j]=frames[i][j];
    auto& d=w.candidate;d.metadata=metadata;d.frames=frames;d.count=count;
    if(!rawDigest(staged,count,hash,d.rawHash))return false;
    capsule::putArray(p,16,d.rawHash);
    if(!store::shape(staged)||!frameRules(d,w.first,w.last))return false;
    out=staged;return true;
}
template<class Hasher> constexpr bool decodeInto(const store::Record& in,const trial::Binding& expected,
    const trial::Digest& bindingDigest,const Hasher& hash,Decoded& out,Workspace& w){
    if(!store::shape(in)||in.size<RawOffset)return false;
    const auto& p=in.payload;const auto count=p[12];
    if(capsule::get(p,0,4)!=Magic||capsule::get(p,4,2)!=1||capsule::get(p,6,2)!=HeaderSize||
       capsule::get(p,8,2)!=capsule::WireSize||capsule::get(p,10,2)!=evidence::WireSize||count>MaxFrames||p[13]||p[14]||p[15]||
       in.size!=RawOffset+size_t(count)*evidence::WireSize)return false;
    auto& inner=w.inner;inner.payload.fill(0);inner.sequence=in.sequence;inner.phase=in.phase;inner.nonce=in.nonce;inner.binding=in.binding;inner.size=capsule::WireSize;
    for(size_t i=0;i<capsule::WireSize;++i)inner.payload[i]=p[HeaderSize+i];
    auto& d=w.candidate;d.count=count;for(auto& frame:d.frames)frame.fill(0);d.rawHash.fill(0);
    if(!capsule::decode(inner,expected,bindingDigest,d.metadata))return false;
    for(size_t i=0;i<count;++i)for(size_t j=0;j<evidence::WireSize;++j)d.frames[i][j]=p[RawOffset+i*evidence::WireSize+j];
    trial::Digest actual{};capsule::getArray(p,16,d.rawHash);
    if(!rawDigest(in,count,hash,actual)||actual!=d.rawHash||!frameRules(d,w.first,w.last))return false;
    out=d;return true;
}
constexpr bool decodeAlias(const store::Record& in,const trial::Binding& expected,const trial::Digest& digest,const Decoded& out,const Workspace& w){
    return &in==&w.inner||&in==&w.staged||&out==&w.candidate||commonAlias(expected,digest,w);
}
constexpr bool transitionDecoded(const Decoded& a,const Decoded& b){
    if(!trial::transition(a.metadata,b.metadata)||b.count<a.count)return false;
    for(size_t i=0;i<a.count;++i)if(a.frames[i]!=b.frames[i])return false;
    if(b.count==a.count)return true;
    return (a.metadata.phase==trial::Phase::HandedOff&&b.metadata.phase==trial::Phase::CaptureClaimed)||
        (a.metadata.capture==trial::CaptureResult::None&&b.metadata.capture!=trial::CaptureResult::None&&
         (b.metadata.phase==trial::Phase::Captured||b.metadata.phase==trial::Phase::CaptureFailed));
}
} // namespace detail

// Explicit-workspace production overloads. Inputs/output must not alias mutable
// scratch (checked below). Failures leave caller output unchanged. Reentrant
// same-workspace use is refused; independently owned workspaces may nest.
template<class Hasher> constexpr bool encode(const trial::Record& metadata,const Frames& frames,uint8_t count,
    const trial::Binding& expected,const trial::Digest& bindingDigest,const Hasher& hash,store::Record& out,Workspace& w){
    if(w.busy||&out==&w.inner||&out==&w.staged||&metadata==&w.candidate.metadata||&frames==&w.candidate.frames||detail::commonAlias(expected,bindingDigest,w))return false;
    detail::Use use(w);return detail::encodeInto(metadata,frames,count,expected,bindingDigest,hash,out,w);
}
template<class Hasher> constexpr bool decode(const store::Record& in,const trial::Binding& expected,
    const trial::Digest& bindingDigest,const Hasher& hash,Decoded& out,Workspace& w){
    if(w.busy||detail::decodeAlias(in,expected,bindingDigest,out,w))return false;
    detail::Use use(w);return detail::decodeInto(in,expected,bindingDigest,hash,out,w);
}
// Compatibility/convenience wrappers allocate large scratch on the stack.
// Tests/non-production only: runtime MUST use explicit caller-owned Workspace.
template<class Hasher> constexpr bool encode(const trial::Record& metadata,const Frames& frames,uint8_t count,
    const trial::Binding& expected,const trial::Digest& bindingDigest,const Hasher& hash,store::Record& out){
    Workspace workspace{};return encode(metadata,frames,count,expected,bindingDigest,hash,out,workspace);
}
template<class Hasher> constexpr bool decode(const store::Record& in,const trial::Binding& expected,
    const trial::Digest& bindingDigest,const Hasher& hash,Decoded& out){
    Workspace workspace{};return decode(in,expected,bindingDigest,hash,out,workspace);
}

template<class Hasher> struct Policy;
namespace detail {
// Keep the large compatibility frame out of EVERY production Policy frame,
// even when the compiler cannot prove workspace != nullptr at the call site.
template<class Hasher> [[gnu::noinline]] bool convenienceValid(const Policy<Hasher>& policy,const store::Record& record);
template<class Hasher> [[gnu::noinline]] bool convenienceInitial(const Policy<Hasher>& policy,const store::Record& record);
template<class Hasher> [[gnu::noinline]] bool convenienceTransition(const Policy<Hasher>& policy,const store::Record& old,const store::Record& next);
}
template<class Hasher> struct Policy {
    trial::Binding expected{};trial::Digest bindingDigest{};Hasher hash{};
    Workspace* workspace=nullptr; // Required in production; null is convenience only.
    constexpr bool valid(const store::Record& r)const{
        if(!workspace){
            if(std::is_constant_evaluated()){Workspace local{};Policy owned{expected,bindingDigest,hash,&local};return owned.valid(r);}
            return detail::convenienceValid(*this,r);
        }
        auto& w=*workspace;if(w.busy||detail::decodeAlias(r,expected,bindingDigest,w.before,w))return false;
        detail::Use use(w);return detail::decodeInto(r,expected,bindingDigest,hash,w.before,w);
    }
    constexpr bool initial(const store::Record& r)const{
        if(!workspace){
            if(std::is_constant_evaluated()){Workspace local{};Policy owned{expected,bindingDigest,hash,&local};return owned.initial(r);}
            return detail::convenienceInitial(*this,r);
        }
        auto& w=*workspace;if(w.busy||detail::decodeAlias(r,expected,bindingDigest,w.before,w))return false;
        detail::Use use(w);return detail::decodeInto(r,expected,bindingDigest,hash,w.before,w)&&trial::initial(w.before.metadata)&&!w.before.count;
    }
    constexpr bool transition(const store::Record& old,const store::Record& next)const{
        if(!workspace){
            if(std::is_constant_evaluated()){Workspace local{};Policy owned{expected,bindingDigest,hash,&local};return owned.transition(old,next);}
            return detail::convenienceTransition(*this,old,next);
        }
        auto& w=*workspace;
        if(w.busy||detail::decodeAlias(old,expected,bindingDigest,w.before,w)||detail::decodeAlias(next,expected,bindingDigest,w.after,w))return false;
        detail::Use use(w);
        return detail::decodeInto(old,expected,bindingDigest,hash,w.before,w)&&detail::decodeInto(next,expected,bindingDigest,hash,w.after,w)&&detail::transitionDecoded(w.before,w.after);
    }
};
namespace detail {
template<class Hasher> [[gnu::noinline]] bool convenienceValid(const Policy<Hasher>& policy,const store::Record& record){
    Workspace local{};auto owned=policy;owned.workspace=&local;return owned.valid(record);
}
template<class Hasher> [[gnu::noinline]] bool convenienceInitial(const Policy<Hasher>& policy,const store::Record& record){
    Workspace local{};auto owned=policy;owned.workspace=&local;return owned.initial(record);
}
template<class Hasher> [[gnu::noinline]] bool convenienceTransition(const Policy<Hasher>& policy,const store::Record& old,const store::Record& next){
    Workspace local{};auto owned=policy;owned.workspace=&local;return owned.transition(old,next);
}
} // namespace detail
} // namespace mosaico_gauge_cold_envelope
