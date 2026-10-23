#pragma once
#include "mosaico_gauge_cold_capsule.h"
#include "mosaico_gauge_cold_evidence_model.h"

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
constexpr bool sameNonce(const evidence::Frame& f,const trial::BootToken& token){return token.verified&&f.bootNonce==token.nonce;}
constexpr bool completeRead(const evidence::Frame& f){
    return (f.standard.validMask&mosaico_gauge_status::AllRaw)==mosaico_gauge_status::AllRaw&&
        f.engine.validMask==0x1fff&&f.engine.echo73==0x73&&f.engine.echo76==0x76&&f.engine.echo02==2&&
        mosaico_gauge_engine::beWord(f.engine.rawIdentity,0)==0x0220&&
        mosaico_gauge_engine::beWord(f.engine.rawIdentity,2)==3&&mosaico_gauge_engine::beWord(f.engine.rawIdentity,4)==4;
}
constexpr bool frameRules(const Decoded& d){
    const auto& r=d.metadata;
    if(!d.count)return r.capture==trial::CaptureResult::None&&!trial::nonzero(d.rawHash);
    if(d.count>MaxFrames||!trial::nonzero(d.rawHash))return false;
    evidence::Frame first{},last{};
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
template<class Hasher> constexpr bool rawDigest(const store::Record& record,uint8_t count,const Hasher& hash,trial::Digest& digest){
    digest={};
    if(count>MaxFrames)return false;
    return !count||hash(record.payload.data()+RawOffset,size_t(count)*evidence::WireSize,digest);
}
template<class Hasher> constexpr bool encode(const trial::Record& metadata,const Frames& frames,uint8_t count,
    const trial::Binding& expected,const trial::Digest& bindingDigest,const Hasher& hash,store::Record& out){
    if(count>MaxFrames)return false;
    store::Record inner{};if(!capsule::encode(metadata,expected,bindingDigest,inner))return false;
    out=inner;out.payload={};out.size=uint16_t(RawOffset+size_t(count)*evidence::WireSize);
    auto& p=out.payload;capsule::put(p,0,Magic,4);capsule::put(p,4,1,2);capsule::put(p,6,HeaderSize,2);
    capsule::put(p,8,capsule::WireSize,2);capsule::put(p,10,evidence::WireSize,2);p[12]=count;
    for(size_t i=0;i<capsule::WireSize;++i)p[HeaderSize+i]=inner.payload[i];
    for(size_t i=0;i<count;++i)for(size_t j=0;j<evidence::WireSize;++j)p[RawOffset+i*evidence::WireSize+j]=frames[i][j];
    trial::Digest digest{};if(!rawDigest(out,count,hash,digest))return false;
    capsule::putArray(p,16,digest);
    return store::shape(out)&&frameRules({metadata,frames,count,digest});
}
template<class Hasher> constexpr bool decode(const store::Record& in,const trial::Binding& expected,
    const trial::Digest& bindingDigest,const Hasher& hash,Decoded& out){
    if(!store::shape(in)||in.size<RawOffset)return false;
    const auto& p=in.payload;const auto count=p[12];
    if(capsule::get(p,0,4)!=Magic||capsule::get(p,4,2)!=1||capsule::get(p,6,2)!=HeaderSize||
       capsule::get(p,8,2)!=capsule::WireSize||capsule::get(p,10,2)!=evidence::WireSize||count>MaxFrames||p[13]||p[14]||p[15]||
       in.size!=RawOffset+size_t(count)*evidence::WireSize)return false;
    store::Record inner{};inner.sequence=in.sequence;inner.phase=in.phase;inner.nonce=in.nonce;inner.binding=in.binding;inner.size=capsule::WireSize;
    for(size_t i=0;i<capsule::WireSize;++i)inner.payload[i]=p[HeaderSize+i];
    Decoded d{};d.count=count;
    if(!capsule::decode(inner,expected,bindingDigest,d.metadata))return false;
    for(size_t i=0;i<count;++i)for(size_t j=0;j<evidence::WireSize;++j)d.frames[i][j]=p[RawOffset+i*evidence::WireSize+j];
    capsule::getArray(p,16,d.rawHash);trial::Digest actual{};
    if(!rawDigest(in,count,hash,actual)||actual!=d.rawHash||!frameRules(d))return false;
    out=d;return true;
}
template<class Hasher> struct Policy {
    trial::Binding expected{};trial::Digest bindingDigest{};Hasher hash{};
    constexpr bool valid(const store::Record& r)const{Decoded d{};return decode(r,expected,bindingDigest,hash,d);}
    constexpr bool initial(const store::Record& r)const{Decoded d{};return decode(r,expected,bindingDigest,hash,d)&&trial::initial(d.metadata)&&!d.count;}
    constexpr bool transition(const store::Record& old,const store::Record& next)const{
        Decoded a{},b{};
        if(!decode(old,expected,bindingDigest,hash,a)||!decode(next,expected,bindingDigest,hash,b)||
           !trial::transition(a.metadata,b.metadata)||b.count<a.count)return false;
        for(size_t i=0;i<a.count;++i)if(a.frames[i]!=b.frames[i])return false;
        if(b.count==a.count)return true;
        // Exactly the consuming claim or capture/failure completion may append.
        return (a.metadata.phase==trial::Phase::HandedOff&&b.metadata.phase==trial::Phase::CaptureClaimed)||
            (a.metadata.capture==trial::CaptureResult::None&&b.metadata.capture!=trial::CaptureResult::None&&
             (b.metadata.phase==trial::Phase::Captured||b.metadata.phase==trial::Phase::CaptureFailed));
    }
};
} // namespace mosaico_gauge_cold_envelope
