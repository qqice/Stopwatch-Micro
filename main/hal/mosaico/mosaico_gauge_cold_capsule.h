#pragma once
#include "mosaico_gauge_cold_trial_model.h"
#include "mosaico_gauge_cold_store_model.h"

// Metadata codec only. No SHA256 implementation, NAND IO, gauge admission or raw
// evidence body storage. CRC in the outer store detects damage, NOT authenticity.
namespace mosaico_gauge_cold_capsule {
namespace trial=mosaico_gauge_cold_trial;
namespace store=mosaico_gauge_cold_store;
inline constexpr size_t WireSize=288,BindingWireSize=188;
inline constexpr uint32_t Magic=0x314d4347U,BindingMagic=0x31424347U; // GCM1/GCB1 LE
inline constexpr uint16_t CodecVersion=1;
using BindingBytes=std::array<uint8_t,BindingWireSize>;
static_assert(WireSize<=store::PayloadMax);

template<size_t N> constexpr void put(std::array<uint8_t,N>& out,size_t at,uint64_t value,unsigned width){
    for(unsigned i=0;i<width;++i)out[at+i]=uint8_t(value>>(8*i));
}
template<size_t N> constexpr uint64_t get(const std::array<uint8_t,N>& in,size_t at,unsigned width){
    uint64_t value=0;for(unsigned i=0;i<width;++i)value|=uint64_t(in[at+i])<<(8*i);return value;
}
template<size_t N,size_t M> constexpr void putArray(std::array<uint8_t,N>& out,size_t at,const std::array<uint8_t,M>& bytes){
    for(size_t i=0;i<M;++i)out[at+i]=bytes[i];
}
template<size_t N,size_t M> constexpr void getArray(const std::array<uint8_t,N>& in,size_t at,std::array<uint8_t,M>& bytes){
    for(size_t i=0;i<M;++i)bytes[i]=in[at+i];
}
// Complete 180-byte Binding, fixed layout; no native struct representation.
template<size_t N> constexpr void writeBinding(std::array<uint8_t,N>& out,size_t at,const trial::Binding& b){
    putArray(out,at,b.mac);put(out,at+6,b.identity.device,2);put(out,at+8,b.identity.firmware,2);out[at+10]=b.identity.build;
    out[at+11]=uint8_t(b.recovery);put(out,at+12,b.source,4);put(out,at+16,b.target,4);put(out,at+20,b.originalGctGeneration,4);
    put(out,at+24,b.originalGctEpoch,8);putArray(out,at+32,b.nonce);
    putArray(out,at+48,b.slotHashes[0]);putArray(out,at+80,b.slotHashes[1]);putArray(out,at+112,b.contractDigest);
    putArray(out,at+144,b.source131ReceiptDigest);put(out,at+176,b.captureBudgetMs,4);
}
template<size_t N> constexpr void readBinding(const std::array<uint8_t,N>& in,size_t at,trial::Binding& b){
    getArray(in,at,b.mac);b.identity={uint16_t(get(in,at+6,2)),uint16_t(get(in,at+8,2)),in[at+10]};
    b.recovery=trial::RecoveryBinding(in[at+11]);b.source=uint32_t(get(in,at+12,4));b.target=uint32_t(get(in,at+16,4));
    b.originalGctGeneration=uint32_t(get(in,at+20,4));b.originalGctEpoch=get(in,at+24,8);getArray(in,at+32,b.nonce);
    getArray(in,at+48,b.slotHashes[0]);getArray(in,at+80,b.slotHashes[1]);getArray(in,at+112,b.contractDigest);
    getArray(in,at+144,b.source131ReceiptDigest);b.captureBudgetMs=uint32_t(get(in,at+176,4));
}
// SHA256 input for the trusted wrapper: domain, protocol schema, exact size and
// EVERY Binding field (including both full hashes or full contract digest).
constexpr bool encodeBinding(const trial::Binding& b,BindingBytes& out){
    if(!trial::validBinding(b))return false;
    out.fill(0);put(out,0,BindingMagic,4);put(out,4,trial::protocolVersion,2);put(out,6,BindingWireSize,2);writeBinding(out,8,b);return true;
}
constexpr bool canonical(const trial::Record& r){
    // Enforce the wire enum independently of the typed model's terminal-state
    // shape rules. Unknown outcomes must never become persisted metadata.
    if(r.capture!=trial::CaptureResult::None&&r.capture!=trial::CaptureResult::Success&&!trial::knownFailure(r.capture))return false;
    if(!trial::shape(r))return false;
    // None is canonically no evidence; terminal Unknown cannot smuggle a digest
    // without a capture outcome. No inference of durable raw evidence is made.
    return (r.capture==trial::CaptureResult::None)==!trial::nonzero(r.evidenceDigest);
}
// Caller MUST establish trustedBindingDigest == SHA256(encodeBinding(expected))
// through its trusted crypto path. This codec does NOT calculate/check that
// cryptographic relation. It checks all32 outer digest bytes AND independently
// compares EVERY decoded Binding field with expected; a digest alone cannot
// substitute for expected. Inputs/outputs must not alias the store payload.
constexpr bool encode(const trial::Record& r,const trial::Binding& expected,const trial::Digest& trustedBindingDigest,store::Record& out){
    if(!canonical(r)||!trial::validBinding(expected)||!trial::sameBinding(r.binding,expected)||!trial::nonzero(trustedBindingDigest))return false;
    out.sequence=r.sequence;out.phase=uint8_t(r.phase);out.nonce=r.binding.nonce;out.binding=trustedBindingDigest;out.size=WireSize;out.payload.fill(0);
    auto& p=out.payload;put(p,0,Magic,4);put(p,4,CodecVersion,2);put(p,6,WireSize,2);put(p,8,r.schema,2);p[10]=uint8_t(r.phase);p[11]=uint8_t(r.capture);put(p,12,r.sequence,4);
    writeBinding(p,16,r.binding);putArray(p,196,r.armedBoot.nonce);p[212]=r.armedBoot.verified?1:0;
    putArray(p,213,r.claimedBoot.nonce);p[229]=r.claimedBoot.verified?1:0; //230..231 reserved zero
    put(p,232,r.armedMs,8);put(p,240,r.handoffMs,8);put(p,248,r.claimMs,8);putArray(p,256,r.evidenceDigest);
    return store::shape(out);
}
constexpr bool decode(const store::Record& in,const trial::Binding& expected,const trial::Digest& trustedBindingDigest,trial::Record& out){
    if(!store::shape(in)||in.size!=WireSize||!trial::validBinding(expected)||!trial::nonzero(trustedBindingDigest)||in.binding!=trustedBindingDigest)return false;
    const auto& p=in.payload;
    if(get(p,0,4)!=Magic||get(p,4,2)!=CodecVersion||get(p,6,2)!=WireSize||p[212]>1||p[229]>1||p[230]||p[231])return false;
    trial::Record r{};r.schema=uint16_t(get(p,8,2));r.phase=trial::Phase(p[10]);r.capture=trial::CaptureResult(p[11]);r.sequence=uint32_t(get(p,12,4));readBinding(p,16,r.binding);
    getArray(p,196,r.armedBoot.nonce);r.armedBoot.verified=p[212]!=0;getArray(p,213,r.claimedBoot.nonce);r.claimedBoot.verified=p[229]!=0;
    r.armedMs=get(p,232,8);r.handoffMs=get(p,240,8);r.claimMs=get(p,248,8);getArray(p,256,r.evidenceDigest);
    if(in.sequence!=r.sequence||in.phase!=uint8_t(r.phase)||in.nonce!=r.binding.nonce||!trial::sameBinding(r.binding,expected)||!canonical(r))return false;
    out=r;return true;
}
// Production structural storage Policy, NOT contextual IC authorization. The
// caller must also use trial planners+durable Proof. Each Policy is tied to one
// complete expected Binding and its externally verified full SHA256 digest.
struct Policy {
    trial::Binding expected{};trial::Digest trustedBindingDigest{};
    constexpr bool valid(const store::Record& r)const{trial::Record t{};return decode(r,expected,trustedBindingDigest,t);}
    constexpr bool initial(const store::Record& r)const{trial::Record t{};return decode(r,expected,trustedBindingDigest,t)&&trial::initial(t);}
    constexpr bool transition(const store::Record& a,const store::Record& b)const{
        trial::Record before{},after{};
        return decode(a,expected,trustedBindingDigest,before)&&decode(b,expected,trustedBindingDigest,after)&&trial::transition(before,after);
    }
};
} // namespace mosaico_gauge_cold_capsule
