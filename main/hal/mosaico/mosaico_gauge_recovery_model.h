#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include "mosaico_threshold_audit_model.h"
#include "mosaico_gauge_calibration_read_model.h"
#include "mosaico_gauge_public_extra_read_model.h"

// OFFLINE PLANNING ONLY. No storage format, CRC implementation, address-write
// API, hardware admission, flash/NVS operation or parameter write is provided.
// Attestations are caller responsibilities, NOT evidence manufactured here.
namespace mosaico_gauge_recovery {
inline constexpr bool readyForHardware=false;
inline constexpr size_t fieldCount=131;
struct LayoutField {uint16_t address=0;uint8_t width=0;};
constexpr std::array<LayoutField,fieldCount> publicLayout(){
    std::array<LayoutField,fieldCount> out{};size_t n=0;
    for(const auto& f:mosaico_threshold::fields){
        const bool byte=f.type==mosaico_threshold::Type::U1||f.type==mosaico_threshold::Type::H1;
        out[n++]={static_cast<uint16_t>(f.selector),static_cast<uint8_t>(byte?1:2)};
    }
    for(const auto& f:mosaico_calibration::fields)out[n++]={static_cast<uint16_t>(f.selector),f.width};
    for(const auto& f:mosaico_public_extra::fields)out[n++]={static_cast<uint16_t>(f.selector),f.width};
    for(size_t i=1;i<n;++i){auto f=out[i];size_t j=i;
        while(j&&out[j-1].address>f.address){out[j]=out[j-1];--j;}out[j]=f;
    }
    return out;
}
inline constexpr auto layout=publicLayout();
constexpr bool validCanonicalLayout(){
    if(mosaico_threshold::fieldCount+mosaico_calibration::fieldCount+mosaico_public_extra::fieldCount!=fieldCount)return false;
    for(size_t i=0;i<fieldCount;++i)
        if(!layout[i].address||(layout[i].width!=1&&layout[i].width!=2&&layout[i].width!=4)||
           (i&&layout[i-1].address>=layout[i].address))return false;
    return true;
}
static_assert(validCanonicalLayout(),"fixed sorted unique public131 read-layout only");
enum class Layout:uint8_t {Unknown,Public131V1};
enum class Phase:uint8_t {Original,Pending,Applied,Restoring};
enum class Scale:uint8_t {One=1,Two=2,Four=4};
struct Identity {uint16_t device=0,firmware=0;uint8_t build=0;};
struct Binding {uint8_t bytes[32]{};}; // Opaque nonzero device ID, never a key.
struct Field {uint16_t address=0;uint8_t width=0;uint8_t original[4]{},target[4]{};};
struct Snapshot {
    Identity identity{};Binding binding{};Layout layoutId=Layout::Unknown;
    uint32_t generation=0;Scale scale=Scale::One;uint64_t epoch=0;
    Phase phase=Phase::Original;size_t count=0;Field fields[fieldCount]{};
};
struct Observation {
    size_t count=0;uint8_t raw[fieldCount][4]{};
    bool authenticated=false,sealed=false,cfg0=false; // Caller attests fresh exact authenticated reads.
};
struct Context {
    Identity identity{};Binding binding{};Layout layoutId=Layout::Unknown;
    // CRC covers the exact complete source records; reviewedTarget covers every
    // target byte. Neither flag can be inferred from identity/profile guesses.
    bool sourceCrcAttested=false,reviewedTargetAttested=false;
};
struct DurableProof {
    // Caller must attest BOTH full original and pending/restore-intent records
    // durably committed and read back byte-identically, including identity,
    // binding, layout, all raw bytes, scale, generation and intended next epoch.
    // These booleans are NOT a CRC, storage-success check or implementation.
    bool originalDurable=false,originalReadbackIdentical=false;
    bool pendingDurable=false,pendingReadbackIdentical=false;
    bool journalCrcAttested=false;
    uint32_t generation=0;uint64_t intendedEpoch=0;
};
enum class Image:uint8_t {Invalid,Original,Target,Mixed,Unexpected};
enum class Action:uint8_t {FailClosed,OriginalNoWrite,RestoreRequired,WriteReviewedTarget,RestoreOriginal,MarkApplied,MarkOriginal};
enum class Error:uint8_t {None,Identity,Binding,Layout,Shape,Attestation,Unexpected,Phase,Epoch,Durability,NotOriginal,NotTarget,NotSealed};
struct Decision {
    Action action=Action::FailClosed;Error error=Error::None;
    Phase nextPhase=Phase::Original;uint64_t nextEpoch=0;
    bool publishPhysicalTelemetry=false;
    // Remains false for every plan: production/hardware integration is absent.
    bool parameterWritesAuthorized=false;
};
constexpr bool knownIdentity(Identity id){return id.device==0x0220&&id.firmware==0x0003&&id.build==4;}
constexpr bool sameIdentity(Identity a,Identity b){return a.device==b.device&&a.firmware==b.firmware&&a.build==b.build;}
constexpr bool nonzero(const Binding& b){for(auto byte:b.bytes)if(byte)return true;return false;}
constexpr bool sameBinding(const Binding& a,const Binding& b){for(size_t i=0;i<32;++i)if(a.bytes[i]!=b.bytes[i])return false;return true;}
constexpr bool validPhase(Phase p){return p==Phase::Original||p==Phase::Pending||p==Phase::Applied||p==Phase::Restoring;}
constexpr Error validate(const Snapshot& s,const Context& c){
    if(!knownIdentity(s.identity)||!knownIdentity(c.identity)||!sameIdentity(s.identity,c.identity))return Error::Identity;
    if(!nonzero(s.binding)||!nonzero(c.binding)||!sameBinding(s.binding,c.binding))return Error::Binding;
    if(s.layoutId!=Layout::Public131V1||c.layoutId!=s.layoutId)return Error::Layout;
    if(!s.generation||s.count!=fieldCount||!validPhase(s.phase)||
       (s.scale!=Scale::One&&s.scale!=Scale::Two&&s.scale!=Scale::Four))return Error::Shape;
    for(size_t i=0;i<fieldCount;++i){const auto& f=s.fields[i];
        if(f.address!=layout[i].address||f.width!=layout[i].width)return Error::Shape;
        // Canonical unused bytes prevent multiple encodings of the same value.
        for(size_t j=f.width;j<4;++j)if(f.original[j]||f.target[j])return Error::Shape;
    }
    if(!c.sourceCrcAttested||!c.reviewedTargetAttested)return Error::Attestation;
    return Error::None;
}
constexpr Image classify(const Snapshot& s,const Observation& o){
    if(s.count!=fieldCount||o.count!=fieldCount)return Image::Invalid;
    bool original=true,target=true;
    for(size_t i=0;i<fieldCount;++i){const auto& f=s.fields[i];
        if(f.address!=layout[i].address||f.width!=layout[i].width)return Image::Invalid;
        bool a=true,b=true;
        for(size_t j=0;j<f.width;++j){a&=o.raw[i][j]==f.original[j];b&=o.raw[i][j]==f.target[j];}
        if(!a&&!b)return Image::Unexpected; // Never overwrite an unknown third value.
        original&=a;target&=b;
    }
    return original?Image::Original:target?Image::Target:Image::Mixed;
}
constexpr bool matchesTarget(const Snapshot& s,const Observation& o){
    if(s.count!=fieldCount||o.count!=fieldCount)return false;
    for(size_t i=0;i<fieldCount;++i){
        if(s.fields[i].address!=layout[i].address||s.fields[i].width!=layout[i].width)return false;
        for(size_t j=0;j<s.fields[i].width;++j)if(o.raw[i][j]!=s.fields[i].target[j])return false;
    }
    return true; // Also handles scale-1/no-op targets identical to original.
}
constexpr Decision reject(Error error){Decision d{};d.error=error;return d;}
constexpr Decision plan(Action action,Phase phase,uint64_t epoch){Decision d{};d.action=action;d.nextPhase=phase;d.nextEpoch=epoch;return d;}
constexpr Error checkedImage(const Snapshot& s,const Context& c,const Observation& o){
    const auto valid=validate(s,c);if(valid!=Error::None)return valid;
    if(!o.authenticated)return Error::Attestation;
    const auto image=classify(s,o);
    return image==Image::Invalid?Error::Shape:image==Image::Unexpected?Error::Unexpected:Error::None;
}
constexpr bool advancing(uint64_t before,uint64_t after){return before!=UINT64_MAX&&after>before;}
constexpr bool durable(const Snapshot& s,const DurableProof& p,uint64_t nextEpoch){
    return p.originalDurable&&p.originalReadbackIdentical&&p.pendingDurable&&p.pendingReadbackIdentical&&
        p.journalCrcAttested&&p.generation==s.generation&&p.intendedEpoch==nextEpoch;
}
// Boot inspection NEVER authorizes a parameter write. Non-Original persisted
// states only request original restoration, including interrupted all-original
// and all-target images. Scaled physical telemetry is never published.
constexpr Decision startup(const Snapshot& s,const Context& c,const Observation& o){
    const auto error=checkedImage(s,c,o);if(error!=Error::None)return reject(error);
    if(s.phase!=Phase::Original||classify(s,o)!=Image::Original)return plan(Action::RestoreRequired,Phase::Restoring,s.epoch);
    if(!o.sealed||!o.cfg0)return reject(Error::NotSealed);
    auto d=plan(Action::OriginalNoWrite,Phase::Original,s.epoch);d.publishPhysicalTelemetry=true;return d;
}
// Explicit offline forward plan. Mixed images can ONLY take the restore path.
constexpr Decision apply(const Snapshot& s,const Context& c,const Observation& o,const DurableProof& p,uint64_t nextEpoch){
    const auto error=checkedImage(s,c,o);if(error!=Error::None)return reject(error);
    if(s.phase!=Phase::Original)return reject(Error::Phase);
    if(classify(s,o)!=Image::Original)return plan(Action::RestoreRequired,Phase::Restoring,s.epoch);
    if(!o.sealed||!o.cfg0)return reject(Error::NotSealed);
    if(!advancing(s.epoch,nextEpoch))return reject(Error::Epoch);
    if(!durable(s,p,nextEpoch))return reject(Error::Durability);
    return plan(Action::WriteReviewedTarget,Phase::Pending,nextEpoch);
}
constexpr Decision restore(const Snapshot& s,const Context& c,const Observation& o,const DurableProof& p,uint64_t nextEpoch){
    const auto error=checkedImage(s,c,o);if(error!=Error::None)return reject(error);
    if(!advancing(s.epoch,nextEpoch))return reject(Error::Epoch);
    if(!durable(s,p,nextEpoch))return reject(Error::Durability);
    return plan(Action::RestoreOriginal,Phase::Restoring,nextEpoch);
}
// Completion means a fresh full authenticated 131-field observation, SEALED and
// CFG0. Caller must persist/readback the resulting journal; this is only a plan.
constexpr Decision finishRestore(const Snapshot& s,const Context& c,const Observation& o,const DurableProof& p,uint64_t nextEpoch){
    const auto error=checkedImage(s,c,o);if(error!=Error::None)return reject(error);
    if(s.phase!=Phase::Restoring)return reject(Error::Phase);
    if(classify(s,o)!=Image::Original)return reject(Error::NotOriginal);
    if(!o.sealed||!o.cfg0)return reject(Error::NotSealed);
    if(!advancing(s.epoch,nextEpoch))return reject(Error::Epoch);
    if(!durable(s,p,nextEpoch))return reject(Error::Durability);
    return plan(Action::MarkOriginal,Phase::Original,nextEpoch);
}
constexpr Decision finishApply(const Snapshot& s,const Context& c,const Observation& o,const DurableProof& p,uint64_t nextEpoch){
    const auto error=checkedImage(s,c,o);if(error!=Error::None)return reject(error);
    if(s.phase!=Phase::Pending)return reject(Error::Phase);
    if(classify(s,o)==Image::Mixed)return plan(Action::RestoreRequired,Phase::Restoring,s.epoch);
    if(!matchesTarget(s,o))return reject(Error::NotTarget);
    if(!o.sealed||!o.cfg0)return reject(Error::NotSealed);
    if(!advancing(s.epoch,nextEpoch))return reject(Error::Epoch);
    if(!durable(s,p,nextEpoch))return reject(Error::Durability);
    return plan(Action::MarkApplied,Phase::Applied,nextEpoch);
}
} // namespace mosaico_gauge_recovery
