#pragma once
#include "mosaico_gauge_status_model.h"
#include "mosaico_gauge_engine_model.h"
#include "mosaico_nand_store_model.h"
#include <array>

// Canonical raw evidence, NOT a successful-capture or parameter-write permit.
// Incomplete transport flags, mismatched snapshots and unknown native values
// are retained unchanged. Actual SHA256 and durable media proof belong outside.
namespace mosaico_gauge_cold_evidence {
namespace bytes=MosaicoNandStore::Model;
inline constexpr size_t WireSize=256;
using Wire=std::array<uint8_t,WireSize>;
enum class Kind:uint8_t {Initial=1,Final=2,Failure=3};
struct Frame {
    Kind kind=Kind::Failure;
    std::array<uint8_t,16> bootNonce{};
    uint32_t resetReason=0,error=0;
    mosaico_gauge_status::Snapshot standard{};
    mosaico_gauge_engine::Snapshot engine{};
};
constexpr bool unitShape(const mosaico_gauge_unit_tag::Tag& t){return t.factor==0&&!t.verified&&t.phase<=4;}
constexpr bool shape(const Frame& f){
    bool nonzero=false;for(auto v:f.bootNonce)nonzero|=v!=0;
    return nonzero&&uint8_t(f.kind)>=1&&uint8_t(f.kind)<=3&&
        f.standard.phase<=4&&f.standard.mode<=3&&!f.standard.reserved&&f.engine.phase<=4&&
        unitShape(f.standard.units)&&unitShape(f.engine.units);
}
constexpr void putTag(Wire& w,size_t at,const mosaico_gauge_unit_tag::Tag& t){
    bytes::put(w,at,t.epoch,8);bytes::put(w,at+8,t.generation,8);w[at+16]=t.phase;
    // factor=0,verified=false are schema invariants, not implicit physical1.
}
constexpr mosaico_gauge_unit_tag::Tag getTag(const Wire& w,size_t at){
    return {0,bytes::get(w,at,8),bytes::get(w,at+8,8),false,w[at+16]};
}
constexpr int16_t signedWord(uint16_t v){return int16_t(v>=0x8000?int32_t(v)-65536:int32_t(v));}
constexpr bool encode(const Frame& f,Wire& w){
    if(!shape(f))return false;
    w={};bytes::put(w,0,0x31454347,4);bytes::put(w,4,1,2);bytes::put(w,6,WireSize,2);
    bytes::put(w,8,f.resetReason,4);w[12]=uint8_t(f.kind);
    for(size_t i=0;i<16;++i)w[16+i]=f.bootNonce[i];
    const auto& s=f.standard;bytes::put(w,32,s.sampledUs,8);bytes::put(w,40,s.validMask,4);
    const uint16_t words[]={s.rawSoc,s.remainingMah,s.fullMah,s.designMah,s.voltageMv,uint16_t(s.currentMa),
        uint16_t(s.averageCurrentMa),s.rawCoulombMah,s.cycleCount,s.operationStatus,s.batteryStatus,s.gaugingStatus,s.rawEcho16};
    for(size_t i=0;i<13;++i)bytes::put(w,44+2*i,words[i],2);
    w[70]=s.phase;w[71]=s.outcome;w[72]=s.mode;putTag(w,74,s.units);
    const auto& e=f.engine;bytes::put(w,96,e.sampledUs,8);bytes::put(w,104,e.validMask,2);
    bytes::put(w,106,e.echo73,2);bytes::put(w,108,e.echo76,2);bytes::put(w,110,e.echo02,2);
    w[112]=e.phase;w[113]=e.outcome;
    for(size_t i=0;i<23;++i)w[114+i]=e.rawCedv[i];
    for(size_t i=0;i<17;++i)w[137+i]=e.rawSmooth[i];
    for(size_t i=0;i<11;++i)w[154+i]=e.rawIdentity[i];
    putTag(w,168,e.units);bytes::put(w,188,f.error,4);
    bytes::put(w,252,bytes::crc(w,252),4);return true;
}
constexpr bool decode(const Wire& w,Frame& f){
    if(bytes::get(w,0,4)!=0x31454347||bytes::get(w,4,2)!=1||bytes::get(w,6,2)!=WireSize||
       bytes::get(w,252,4)!=bytes::crc(w,252))return false;
    Frame next{};next.resetReason=uint32_t(bytes::get(w,8,4));next.kind=Kind(w[12]);
    for(size_t i=0;i<16;++i)next.bootNonce[i]=w[16+i];
    auto& s=next.standard;s.sampledUs=bytes::get(w,32,8);s.validMask=uint32_t(bytes::get(w,40,4));
    s.rawSoc=uint16_t(bytes::get(w,44,2));s.remainingMah=uint16_t(bytes::get(w,46,2));s.fullMah=uint16_t(bytes::get(w,48,2));
    s.designMah=uint16_t(bytes::get(w,50,2));s.voltageMv=uint16_t(bytes::get(w,52,2));
    s.currentMa=signedWord(uint16_t(bytes::get(w,54,2)));s.averageCurrentMa=signedWord(uint16_t(bytes::get(w,56,2)));
    s.rawCoulombMah=uint16_t(bytes::get(w,58,2));s.cycleCount=uint16_t(bytes::get(w,60,2));s.operationStatus=uint16_t(bytes::get(w,62,2));
    s.batteryStatus=uint16_t(bytes::get(w,64,2));s.gaugingStatus=uint16_t(bytes::get(w,66,2));s.rawEcho16=uint16_t(bytes::get(w,68,2));
    s.phase=w[70];s.outcome=w[71];s.mode=w[72];s.units=getTag(w,74);
    auto& e=next.engine;e.sampledUs=bytes::get(w,96,8);e.validMask=uint16_t(bytes::get(w,104,2));
    e.echo73=uint16_t(bytes::get(w,106,2));e.echo76=uint16_t(bytes::get(w,108,2));e.echo02=uint16_t(bytes::get(w,110,2));
    e.phase=w[112];e.outcome=w[113];
    for(size_t i=0;i<23;++i)e.rawCedv[i]=w[114+i];
    for(size_t i=0;i<17;++i)e.rawSmooth[i]=w[137+i];
    for(size_t i=0;i<11;++i)e.rawIdentity[i]=w[154+i];
    e.units=getTag(w,168);next.error=uint32_t(bytes::get(w,188,4));
    Wire canonical{};
    // Re-encoding catches ALL reserved bytes, aliases and implicit defaults.
    if(!encode(next,canonical)||canonical!=w)return false;
    f=next;return true;
}
} // namespace mosaico_gauge_cold_evidence
