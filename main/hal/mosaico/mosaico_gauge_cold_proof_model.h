#pragma once
#include "mosaico_gauge_cold_capsule.h"

// Structural codec/hash INPUTS only. No SHA, crypto, SDK/flash access, storage
// IO, key operation or parameter authority. canonical() is NOT verification.
// Trusted wrapper MUST independently parse the exact pair bytes with duplicate
// key rejection, strict objects/roster, normal+GCT signatures, actual full images
// and SDK_VALID checks, plus the session signature and runtime hardware binding.
namespace mosaico_gauge_cold_proof {
namespace capsule=mosaico_gauge_cold_capsule;
namespace trial=mosaico_gauge_cold_trial;
inline constexpr size_t HeaderSize=56,BindingSize=188,PayloadMax=1536,PairRawMax=1050;
inline constexpr uint16_t Version=1;
inline constexpr uint32_t Magic=0x31504347U; // GCP1 LE
inline constexpr trial::Digest ContractSha={0xec,0x39,0xcc,0x75,0xa1,0x80,0x30,0x90,0x47,0x71,0xa3,0xf4,0xfd,0xc4,0x6c,0x50,
    0xa9,0x56,0x4e,0x92,0x6f,0x45,0x0c,0xdb,0xc6,0x67,0x11,0x59,0x25,0xdc,0x0b,0xa2};
inline constexpr char SessionDomain[]="MOSAICO-GAUGE-COLD-CAP-v1\n";
inline constexpr char SourceDomain[]="MOSAICO-GAUGE-COLD-SOURCE-v1\n";
using BindingInput=capsule::BindingBytes;
using SessionInput=std::array<uint8_t,sizeof(SessionDomain)-1+96>;
using SourceRaw=std::array<uint8_t,222>;
using SourceReceiptInput=std::array<uint8_t,sizeof(SourceDomain)-1+23+222>;
static_assert(BindingSize==capsule::BindingWireSize&&PayloadMax==mosaico_gauge_cold_store::PayloadMax);
struct Bytes {const uint8_t* data=nullptr;size_t size=0;constexpr uint8_t operator[](size_t i)const{return data[i];}};
struct View {
    size_t totalSize=0,bindingOffset=HeaderSize,pairOffset=HeaderSize+BindingSize,signatureOffset=0;
    Bytes binding{},nonce{},pairRaw{},signature{};
};
enum class Error:uint8_t {None,Bounds,Header,Reserved,Binding,Pair,Der,Alias};
struct Result {
    Error error=Error::Bounds;View view{};
    constexpr bool canonical()const{return error==Error::None;}
    static constexpr bool signatureVerified=false,sdkVerified=false,actualFlashVerified=false;
};
namespace detail {
// Internal only: caller must prove data != nullptr, 1 <= n <= 8 and
// at <= size, n <= size-at before calling. decode uses fixed header offsets
// only after its minimum complete-payload bounds check.
constexpr uint64_t uncheckedGet(Bytes bytes,size_t at,unsigned n){
    uint64_t out=0;for(unsigned i=0;i<n;++i)out|=uint64_t(bytes[at+i])<<(8*i);return out;
}
} // namespace detail
// Public bounded LE read. Failure never changes out; subtraction avoids
// overflowing at+n even for SIZE_MAX inputs. Bytes must describe live storage.
constexpr bool checkedGet(Bytes bytes,size_t at,unsigned n,uint64_t& out){
    if(!bytes.data||n<1||n>8||at>bytes.size||n>bytes.size-at)return false;
    out=detail::uncheckedGet(bytes,at,n);return true;
}
constexpr bool productionBinding(const trial::Binding& b){
    return b.recovery==trial::RecoveryBinding::SlotHashes&&b.source==trial::sourceCrc&&b.target==trial::targetCrc&&trial::validBinding(b);
}
constexpr bool bindingHashInput(const trial::Binding& b,BindingInput& out){
    return productionBinding(b)&&capsule::encodeBinding(b,out);
}
constexpr bool pairShape(Bytes pair){
    if(!pair.data||!pair.size||pair.size>PairRawMax)return false;
    for(size_t i=0;i<pair.size;++i)if(pair[i]==0)return false;
    return true; // JSON/signatures MUST be checked by the outer trusted wrapper.
}
constexpr bool derShape(Bytes signature){
    if(!signature.data||signature.size<8||signature.size>72||signature[0]!=0x30||signature[1]!=signature.size-2)return false;
    size_t at=2;
    for(unsigned integer=0;integer<2;++integer){
        if(at+2>signature.size||signature[at]!=2)return false;
        const size_t count=signature[at+1];at+=2;
        if(!count||count>33||count>signature.size-at||signature[at]&0x80||
           (count>1&&signature[at]==0&&!(signature[at+1]&0x80)))return false;
        bool nonzero=false;for(size_t i=0;i<count;++i)nonzero|=signature[at+i]!=0;
        if(!nonzero)return false;
        at+=count;
    }
    return at==signature.size; // DER shape only; not P256 scalar/signature validity.
}
template<size_t N> constexpr Result decode(const std::array<uint8_t,N>& payload,size_t used,const trial::Binding& expected){
    Result result{};
    if(used>N||used>PayloadMax||used<HeaderSize+BindingSize+1+8)return result;
    const Bytes all{payload.data(),used};
    if(detail::uncheckedGet(all,0,4)!=Magic||detail::uncheckedGet(all,4,2)!=Version||detail::uncheckedGet(all,6,2)!=HeaderSize||detail::uncheckedGet(all,8,2)!=used||detail::uncheckedGet(all,10,2)!=BindingSize){result.error=Error::Header;return result;}
    for(size_t i=0;i<32;++i)if(payload[16+i]!=ContractSha[i]){result.error=Error::Header;return result;}
    for(size_t i=48;i<HeaderSize;++i)if(payload[i]){result.error=Error::Reserved;return result;}
    const size_t pairSize=size_t(detail::uncheckedGet(all,12,2)),signatureSize=size_t(detail::uncheckedGet(all,14,2));
    // Bound the16-bit lengths BEFORE addition/subtraction; used is exact EOF,
    // not capacity. Extra logical padding/trailing bytes have no valid alias.
    if(!pairSize||pairSize>PairRawMax||signatureSize<8||signatureSize>72||
       used!=HeaderSize+BindingSize+pairSize+signatureSize){result.error=Error::Bounds;return result;}
    BindingInput canonical{};
    if(!bindingHashInput(expected,canonical)){result.error=Error::Binding;return result;}
    for(size_t i=0;i<BindingSize;++i)if(payload[HeaderSize+i]!=canonical[i]){result.error=Error::Binding;return result;}
    const size_t pairOffset=HeaderSize+BindingSize,signatureOffset=pairOffset+pairSize;
    const Bytes pair{payload.data()+pairOffset,pairSize},signature{payload.data()+signatureOffset,signatureSize};
    if(!pairShape(pair)){result.error=Error::Pair;return result;}
    if(!derShape(signature)){result.error=Error::Der;return result;}
    result.error=Error::None;result.view={used,HeaderSize,pairOffset,signatureOffset,
        {payload.data()+HeaderSize,BindingSize},{payload.data()+HeaderSize+8+32,16},pair,signature};
    return result;
}
// Caller owns the output buffer (normally PSRAM/store::Record.payload). No
// large local payload copy. Inputs must not overlap it; overlapping contiguous spans reject.
template<size_t N> constexpr Error encode(const trial::Binding& binding,Bytes pair,Bytes signature,std::array<uint8_t,N>& out,size_t& used){
    used=0;
    if(!pairShape(pair))return Error::Pair;
    if(!derShape(signature))return Error::Der;
    const size_t total=HeaderSize+BindingSize+pair.size+signature.size;
    if(total>N||total>PayloadMax)return Error::Bounds;
    BindingInput wire{};if(!bindingHashInput(binding,wire))return Error::Binding;
    for(size_t i=0;i<N;++i)if(pair.data==out.data()+i||signature.data==out.data()+i)return Error::Alias;
    for(size_t i=0;i<pair.size;++i)if(pair.data+i==out.data())return Error::Alias;
    for(size_t i=0;i<signature.size;++i)if(signature.data+i==out.data())return Error::Alias;
    out.fill(0);capsule::put(out,0,Magic,4);capsule::put(out,4,Version,2);capsule::put(out,6,HeaderSize,2);
    capsule::put(out,8,total,2);capsule::put(out,10,BindingSize,2);capsule::put(out,12,pair.size,2);capsule::put(out,14,signature.size,2);
    capsule::putArray(out,16,ContractSha);
    for(size_t i=0;i<BindingSize;++i)out[HeaderSize+i]=wire[i];
    for(size_t i=0;i<pair.size;++i)out[HeaderSize+BindingSize+i]=pair[i];
    for(size_t i=0;i<signature.size;++i)out[HeaderSize+BindingSize+pair.size+i]=signature[i];
    used=total;return Error::None;
}
// SHA256 must be computed by the trusted wrapper over the COMPLETE arrays.
// Input digests here must be independently computed, not copied from NVS or
// inferred from decode success. This function computes NO SHA/signature.
constexpr bool sessionMessage(const trial::Digest& bindingSha,const trial::Digest& pairSha,SessionInput& out){
    if(!trial::nonzero(bindingSha)||!trial::nonzero(pairSha))return false;
    size_t at=0;
    for(size_t i=0;i<sizeof(SessionDomain)-1;++i)out[at++]=uint8_t(SessionDomain[i]);
    for(auto v:ContractSha)out[at++]=v;
    for(auto v:bindingSha)out[at++]=v;
    for(auto v:pairSha)out[at++]=v;
    return at==out.size();
}
// Merely serializes the exact source-receipt hash INPUT. Wrapper must first
// establish the complete authenticated closed source fixture/model; raw222's
// type/Binding CRC constants alone are not proof of that or current hardware.
constexpr bool sourceReceiptHashInput(const trial::Binding& binding,const SourceRaw& raw222,SourceReceiptInput& out){
    if(!productionBinding(binding))return false;
    size_t at=0;for(size_t i=0;i<sizeof(SourceDomain)-1;++i)out[at++]=uint8_t(SourceDomain[i]);
    for(auto v:binding.mac)out[at++]=v;
    capsule::put(out,at,binding.identity.device,2);at+=2;capsule::put(out,at,binding.identity.firmware,2);at+=2;out[at++]=binding.identity.build;
    capsule::put(out,at,binding.originalGctGeneration,4);at+=4;capsule::put(out,at,binding.originalGctEpoch,8);at+=8;
    for(auto v:raw222)out[at++]=v;
    return at==out.size();
}
// Views reference the original immutable payload. Keep it alive and unchanged
// through all hashing/parsing/verification. Outer store nonce/binding32 checks
// remain separate requirements; this header grants no operation authority.
} // namespace mosaico_gauge_cold_proof
