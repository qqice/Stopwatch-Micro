#pragma once
#include "mosaico_gauge_recovery_model.h"
#include "mosaico_gauge_scale_journal_model.h" // byte codec/CRC helpers only; no GSJ admission

// Independent nominal-65 contrast experiment. Nothing here asserts calibrated
// physical units, publishes factor=1, changes SOC/OCV, or admits hardware itself.
namespace mosaico_gauge_contrast {
namespace recovery=mosaico_gauge_recovery;
namespace bytes=mosaico_gauge_scale_journal;
inline constexpr size_t rawSize=222;
using Raw=std::array<uint8_t,rawSize>;
enum class Arm:uint8_t {C=1,B=2};
using Phase=recovery::Phase;
struct Record {
    std::array<uint8_t,6> mac{};
    recovery::Identity identity{0x0220,0x0003,4};
    uint32_t generation=0;uint64_t epoch=0;
    Phase phase=Phase::Original;Arm arm=Arm::C;Raw source{};
};
inline constexpr uint32_t canonicalSourceCrc=0x70a767beU;
inline constexpr uint64_t firmwareLeaseMs=720000,hostLeaseMs=660000;
inline constexpr bool physicalUnitsTrusted=false;
// Exact original public131 from actual-public131-backup.json, not a guessed
// factory profile. Sorted layout widths determine packing; unused bytes are 0.
inline constexpr Raw sourceFixture={
0xff,0xf0,0x0b,0xd6,0x7e,0x73,0xb6,0x45,0x93,0x0a,0xa5,0x22,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xcb,0xd4,0x1a,0x05,0xd4,0x86,0x4a,0xc6,0xb4,0xc2,0x6e,0x2b,0x03,0x7c,0x01,0x48,0xfd,0xa3,0xf6,0x75,0x12,0x58,0x2d,0xb7,0x1c,0x98,0x02,0xd3,0xff,0xb9,0x30,0x01,0x00,0xef,0x05,0x11,0x00,0x00,0x01,0xc2,0x00,0x32,0x00,0xc8,0x10,0x68,0x00,0x64,0x04,0x84,0x18,0x00,0x01,0x09,0x00,0x00,0x96,0x00,0xaf,0x02,0x20,0x00,0x0a,0x05,0x00,0x32,0x01,0xc2,0x14,0x14,0x00,0x3c,0x00,0x4b,0x00,0x28,0x00,0x3c,0x3c,0x01,0x02,0x26,0x02,0x01,0xf4,0x02,0x58,0x02,0x02,0x26,0xf6,0x0c,0x4e,0x02,0x0c,0xb2,0x02,0xbc,0x77,0x05,0xdc,0x14,0x00,0x00,0x00,0xc8,0x00,0x00,0x64,0x64,0x08,0x0e,0x74,0x00,0x64,0x1f,0x40,0x02,0x3c,0x5a,0x0c,0x8c,0x8c,0x0b,0xb8,0x0c,0x1c,0x00,0x05,0x10,0x68,0x10,0x04,0x64,0x5f,0x0c,0x80,0x0c,0xe4,0x06,0x08,0x10,0x68,0x10,0x04,0x64,0x5f,0x00,0x10,0x2a,0x0b,0xb8,0x0b,0xb8,0x0e,0x74,0x00,0x64,0x0e,0x9f,0x00,0x95,0x03,0x63,0x0f,0xbe,0x01,0x3c,0x09,0x00,0x00,0x0b,0xd7,0x01,0x0d,0x39,0x01,0x0d,0xad,0x01,0x10,0x4d,0x0f,0xcb,0x0f,0x55,0x0e,0xed,0x0e,0x8d,0x0e,0x48,0x0e,0x23,0x0d,0xfe,0x0d,0xbb,0x0d,0x6f,0x0a,0x99};
constexpr size_t rawOffset(size_t index){size_t n=0;for(size_t i=0;i<index;++i)n+=recovery::layout[i].width;return n;}
constexpr uint32_t rawDigest(const Raw& raw){
    uint32_t c=0xffffffffU;
    for(size_t i=0;i<recovery::fieldCount;++i){const auto f=recovery::layout[i];
        for(size_t j=0;j<7;++j){const uint8_t v=j<2?uint8_t(f.address>>(8*j)):j==2?f.width:j-3<f.width?raw[rawOffset(i)+j-3]:0;
            c^=v;for(unsigned k=0;k<8;++k)c=(c>>1)^((c&1)?0xedb88320U:0);}}
    return ~c;
}
static_assert(rawDigest(sourceFixture)==canonicalSourceCrc,"reviewed full131 source");
constexpr bool owned(uint16_t address){return address==0x929b||address==0x929d||address==0x929f;}
constexpr bool validArm(Arm arm){return arm==Arm::C||arm==Arm::B;}
constexpr bool shape(const Record& r){
    bool nonzero=false;for(auto v:r.mac)nonzero|=v!=0;
    return nonzero&&!(r.mac[0]&1)&&recovery::knownIdentity(r.identity)&&r.generation&&r.epoch&&
        recovery::validPhase(r.phase)&&validArm(r.arm)&&r.source==sourceFixture;
}
constexpr Raw targetRaw(const Record& r){
    auto out=r.source;
    for(size_t i=0;i<recovery::fieldCount;++i){const auto f=recovery::layout[i];if(!owned(f.address))continue;
        const uint16_t v=f.address==0x929b?(r.arm==Arm::C?0x112a:0x102a):65;
        const auto n=rawOffset(i);out[n]=uint8_t(v>>8);out[n+1]=uint8_t(v);}
    return out;
}
enum class Image:uint8_t {Invalid,Original,Target,Mixed,Unexpected};
struct Observation {Raw raw{};bool authenticated=false,sealed=false,cfg0=false;};
constexpr Image classify(const Record& r,const Observation& o){
    if(!shape(r)||!o.authenticated)return Image::Invalid;
    const auto target=targetRaw(r);bool original=true,applied=true;
    for(size_t i=0;i<recovery::fieldCount;++i){bool a=true,b=true;const auto n=rawOffset(i);
        for(size_t j=0;j<recovery::layout[i].width;++j){a&=o.raw[n+j]==r.source[n+j];b&=o.raw[n+j]==target[n+j];}
        if(!a&&!b)return Image::Unexpected;
        original&=a;applied&=b;}
    return original?Image::Original:applied?Image::Target:Image::Mixed;
}
// The full field must equal original OR this arm's own derived target. No byte
// mixing, other-arm target, or unowned/third value can ever authorize a write.
constexpr bool writable(const Record& r,const Observation& o,uint16_t address,bool restoring){
    const auto image=classify(r,o);
    return owned(address)&&image!=Image::Invalid&&image!=Image::Unexpected&&
        (restoring?r.phase!=Phase::Original:r.phase==Phase::Pending);
}
constexpr bool transition(const Record& a,const Record& b){
    if(!shape(a)||!shape(b)||a.mac!=b.mac||!recovery::sameIdentity(a.identity,b.identity)||a.source!=b.source||
       a.generation==UINT32_MAX||b.generation!=a.generation+1||a.epoch==UINT64_MAX||b.epoch!=a.epoch+1)return false;
    if(a.phase==Phase::Original&&b.phase==Phase::Original)return true; // sole arm-switch point
    if(a.arm!=b.arm)return false;
    return (a.phase==Phase::Original&&b.phase==Phase::Pending)||
        (a.phase==Phase::Pending&&(b.phase==Phase::Applied||b.phase==Phase::Restoring))||
        (a.phase==Phase::Applied&&b.phase==Phase::Restoring)||
        (a.phase==Phase::Restoring&&b.phase==Phase::Original);
}
struct Lease {uint64_t startedMs=0;Arm arm=Arm::C;bool begun=false;};
constexpr bool beginLease(Lease& l,Arm arm,uint64_t now){if(l.begun||!validArm(arm))return false;l={now,arm,true};return true;}
constexpr bool leaseActive(const Lease& l,Arm arm,uint64_t now){return l.begun&&l.arm==arm&&now>=l.startedMs&&now-l.startedMs<firmwareLeaseMs;}
constexpr bool restoreRequired(const Record& r,const Observation& o){return shape(r)&&(r.phase!=Phase::Original||classify(r,o)!=Image::Original);}
} // namespace mosaico_gauge_contrast

namespace mosaico_gauge_contrast_trial {
enum class Result {Blocked,Applied,Restored,FailedClosed};
// Backend must revalidate the full source/own-target observation before keys,
// and before each owned write via writable(). intent is durable NVS+readback;
// complete requires sealed/CFG0 authenticated full131 and durable final phase.
// admit(false) checks leaseActive on the total per-arm firmware lease, not a
// renewable timer. Restore is never blocked by an expired apply deadline.
template<class Backend> constexpr Result restore(Backend& b){
    b.revoke();
    if(!b.admit(true)||!b.intent(true)){b.protect();return Result::FailedClosed;}
    if(!b.open(true)||!b.observe(true)||!b.enter()||!b.write(true)||!b.verify(true)||
       !b.exit()||!b.seal()||!b.final(true)||!b.complete(true)){b.protect();return Result::FailedClosed;}
    // Intentionally no publish(): nominal65 is not physical-unit attestation.
    return Result::Restored;
}
template<class Backend> constexpr Result apply(Backend& b){
    if(!b.admit(false))return Result::Blocked;
    b.revoke();
    if(!b.intent(false))return Result::FailedClosed; // no keys before Pending
    if(!b.open(false)||!b.observe(false)||!b.enter()||!b.write(false)||!b.verify(false)||
       !b.exit()||!b.seal()||!b.final(false)||!b.complete(false))return restore(b);
    return Result::Applied;
}
} // namespace mosaico_gauge_contrast_trial
