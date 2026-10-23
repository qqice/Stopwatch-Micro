#pragma once
#include "mosaico_nand_store_model.h"
#include <limits>

// Storage ONLY. A reviewed protocol Policy must classify every state and
// transition. This codec neither authorizes gauge I/O nor changes GCT1.
namespace mosaico_gauge_cold_store {
namespace nand=MosaicoNandStore::Model;
using Page=nand::Page;
inline constexpr uint32_t PageBase=8,RequiredPages=10,Magic=0x31444347;
inline constexpr size_t PayloadMax=1536,PayloadOffset=80;
static_assert(PageBase>=nand::RequiredPages&&RequiredPages==PageBase+2&&RequiredPages<=16);
static_assert(PayloadOffset+PayloadMax<=nand::PageSize-4);
struct Record {
    uint64_t sequence=0;
    uint8_t phase=0;
    std::array<uint8_t,16> nonce{};
    std::array<uint8_t,32> binding{};
    uint16_t size=0;
    std::array<uint8_t,PayloadMax> payload{};
};
enum class Code {Empty,Ready,NotFormatted,Io,Corrupt,Ambiguous,Rejected,Verify};
struct Scratch {Page page{},verify{};Record records[2]{},decoded{};}; // PSRAM in firmware.
struct Loaded {Code code=Code::Empty;int selected=-1;};
template<size_t N> constexpr bool nonzero(const std::array<uint8_t,N>& a){for(auto v:a)if(v)return true;return false;}
constexpr bool sameBinding(const Record& a,const Record& b){return a.nonce==b.nonce&&a.binding==b.binding;}
constexpr bool shape(const Record& r){
    if(!r.sequence||!r.phase||!nonzero(r.nonce)||!nonzero(r.binding)||r.size>PayloadMax)return false;
    for(size_t i=r.size;i<PayloadMax;++i)if(r.payload[i])return false;
    return true;
}
constexpr bool encode(const Record& r,Page& p){
    if(!shape(r))return false;
    p={};nand::put(p,0,Magic,4);nand::put(p,4,1,2);nand::put(p,6,PayloadOffset,2);
    nand::put(p,8,r.sequence,8);p[16]=r.phase;
    for(size_t i=0;i<16;++i)p[24+i]=r.nonce[i];
    for(size_t i=0;i<32;++i)p[40+i]=r.binding[i];
    nand::put(p,72,r.size,2);
    for(size_t i=0;i<r.size;++i)p[PayloadOffset+i]=r.payload[i];
    nand::put(p,2044,nand::crc(p,2044),4);return true;
}
constexpr bool decode(const Page& p,Record& out){
    if(nand::get(p,0,4)!=Magic||nand::get(p,4,2)!=1||nand::get(p,6,2)!=PayloadOffset||
       nand::get(p,72,2)>PayloadMax||nand::get(p,2044,4)!=nand::crc(p,2044))return false;
    for(size_t i=17;i<24;++i)if(p[i])return false;
    for(size_t i=74;i<PayloadOffset;++i)if(p[i])return false;
    const size_t size=size_t(nand::get(p,72,2));
    for(size_t i=PayloadOffset+size;i<2044;++i)if(p[i])return false;
    Record r{};r.sequence=nand::get(p,8,8);r.phase=p[16];r.size=uint16_t(size);
    for(size_t i=0;i<16;++i)r.nonce[i]=p[24+i];
    for(size_t i=0;i<32;++i)r.binding[i]=p[40+i];
    for(size_t i=0;i<size;++i)r.payload[i]=p[PayloadOffset+i];
    if(!shape(r))return false;
    out=r;return true;
}
// IO.read/write/sync use existing owned Dhara map, never raw NAND addresses.
// Policy.initial/valid/transition also enforce the expected binding. Empty is
// not authorization to initialize; the explicit protocol admission is separate.
template<class IO,class Policy> constexpr Loaded load(IO& io,Scratch& s,const Policy& policy){
    bool marker=false;
    for(unsigned i=0;i<2;++i){
        if(!io.read(i,s.page))return {Code::Io,-1};
        if(nand::validMarker(s.page))marker=true;
        else if(!nand::blank(s.page))return {Code::Corrupt,-1};
    }
    if(!marker)return {Code::NotFormatted,-1};
    bool present[2]{};
    for(unsigned i=0;i<2;++i){
        if(!io.read(PageBase+i,s.page))return {Code::Io,-1};
        if(nand::blank(s.page))continue;
        if(!decode(s.page,s.records[i])||!policy.valid(s.records[i]))return {Code::Corrupt,-1};
        present[i]=true;
    }
    if(!present[0]&&!present[1])return {Code::Empty,-1};
    if(!present[0]||!present[1]){
        const unsigned i=present[0]?0:1;
        if(s.records[i].sequence!=1||!policy.initial(s.records[i]))return {Code::Ambiguous,-1};
        return {Code::Ready,int(i)};
    }
    const auto a=s.records[0].sequence,b=s.records[1].sequence;
    if(a==b)return {Code::Ambiguous,-1};
    const unsigned newer=a>b?0:1;const auto& old=s.records[1-newer];const auto& next=s.records[newer];
    if(old.sequence==UINT64_MAX||next.sequence!=old.sequence+1||!sameBinding(old,next)||
       (old.sequence==1&&!policy.initial(old))||!policy.transition(old,next))return {Code::Ambiguous,-1};
    return {Code::Ready,int(newer)};
}
template<class IO,class Policy> constexpr Code save(IO& io,Scratch& s,const Record& next,const Policy& policy,bool admitted){
    // Do not permit next to alias scratch: load() overwrites those records.
    if(!admitted||&next==&s.records[0]||&next==&s.records[1]||&next==&s.decoded||!shape(next)||!policy.valid(next))return Code::Rejected;
    const auto loaded=load(io,s,policy);unsigned target=0;
    if(loaded.code==Code::Empty){if(next.sequence!=1||!policy.initial(next))return Code::Rejected;}
    else if(loaded.code==Code::Ready){
        const auto& old=s.records[unsigned(loaded.selected)];
        if(old.sequence==UINT64_MAX||next.sequence!=old.sequence+1||!sameBinding(old,next)||!policy.transition(old,next))return Code::Rejected;
        target=1-unsigned(loaded.selected);
    }else return loaded.code;
    if(!encode(next,s.page))return Code::Rejected;
    if(!io.write(PageBase+target,s.page)||!io.sync()||!io.read(PageBase+target,s.verify))return Code::Io;
    if(s.page!=s.verify||!decode(s.verify,s.decoded)||!policy.valid(s.decoded))return Code::Verify;
    return Code::Ready;
}
} // namespace mosaico_gauge_cold_store
