#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

namespace MosaicoNandStore::Model {
constexpr size_t PageSize=2048,PayloadSize=3108,SlotCount=2,RequiredPages=8;
using Page=std::array<uint8_t,PageSize>;
using Payload=std::array<uint8_t,PayloadSize>;
enum class Code { Ok, NotFormatted, Empty, Corrupt, Io, GenerationExhausted, Verify };
enum class Stage { Marker, CommitRead, PayloadRead, PayloadWrite, PayloadSync, PayloadVerify, CommitWrite, CommitSync, CommitVerify, FormatWrite, FormatSync, FormatVerify };
struct Result {Code code=Code::Ok;Stage stage=Stage::Marker;uint64_t generation=0;int slot=-1;unsigned markerCopies=0;};
struct Scratch {Page page{},verify{};Payload payload{};}; // Must be PSRAM in firmware.
struct Slot {bool valid=false;uint64_t generation=0;uint32_t payloadCrc=0;};
constexpr uint32_t MarkerMagic=0x31534e4d,CommitMagic=0x3154434d;
template<size_t N> constexpr void put(std::array<uint8_t,N>& b,size_t p,uint64_t v,size_t n){for(size_t i=0;i<n;++i)b[p+i]=uint8_t(v>>(8*i));}
template<size_t N> constexpr uint64_t get(const std::array<uint8_t,N>& b,size_t p,size_t n){uint64_t v=0;for(size_t i=0;i<n;++i)v|=uint64_t(b[p+i])<<(8*i);return v;}
template<size_t N> constexpr uint32_t crc(const std::array<uint8_t,N>& b,size_t length){
    uint32_t c=0xffffffffU;
    for(size_t i=0;i<length;++i){c^=b[i];for(unsigned j=0;j<8;++j)c=(c>>1)^(0xedb88320U&uint32_t(-int32_t(c&1)));}
    return ~c;
}
constexpr bool blank(const Page& p){for(auto v:p)if(v!=255)return false;return true;}
constexpr void marker(Page& p){
    p={};put(p,0,MarkerMagic,4);put(p,4,1,2);put(p,6,PageSize,2);put(p,8,PayloadSize,2);put(p,10,SlotCount,2);put(p,12,RequiredPages,4);put(p,PageSize-4,crc(p,PageSize-4),4);
}
constexpr bool validMarker(const Page& p){
    if(get(p,0,4)!=MarkerMagic||get(p,4,2)!=1||get(p,6,2)!=PageSize||get(p,8,2)!=PayloadSize||get(p,10,2)!=SlotCount||get(p,12,4)!=RequiredPages||get(p,PageSize-4,4)!=crc(p,PageSize-4))return false;
    for(size_t i=16;i<PageSize-4;++i){if(p[i])return false;}
    return true;
}
constexpr uint32_t base(unsigned slot){return 2+slot*3;}
constexpr void commit(Page& p,unsigned slot,uint64_t generation,uint32_t payloadCrc){
    p={};put(p,0,CommitMagic,4);put(p,4,1,2);put(p,6,PayloadSize,2);put(p,8,generation,8);put(p,16,payloadCrc,4);put(p,20,slot,4);put(p,PageSize-4,crc(p,PageSize-4),4);
}
constexpr Slot parseCommit(const Page& p,unsigned slot){
    if(get(p,0,4)!=CommitMagic||get(p,4,2)!=1||get(p,6,2)!=PayloadSize||!get(p,8,8)||get(p,20,4)!=slot||get(p,PageSize-4,4)!=crc(p,PageSize-4))return {};
    for(size_t i=24;i<PageSize-4;++i)if(p[i])return {};
    return {true,get(p,8,8),uint32_t(get(p,16,4))};
}
template<class IO> constexpr Code readPayload(IO& io,Scratch& s,unsigned slot,uint32_t expectedCrc){
    for(unsigned half=0;half<2;++half){
        if(!io.read(base(slot)+half,s.page))return Code::Io;
        for(size_t i=0;i<PageSize;++i){const size_t at=half*PageSize+i;if(at<PayloadSize)s.payload[at]=s.page[i];else if(s.page[i])return Code::Corrupt;}
    }
    return crc(s.payload,PayloadSize)==expectedCrc?Code::Ok:Code::Corrupt;
}
template<class IO> constexpr Result scan(IO& io,Scratch& s){
    Result result;bool nonblankMarker=false;
    for(unsigned copy=0;copy<2;++copy){
        if(!io.read(copy,s.page)){result.code=Code::Io;return result;}
        if(validMarker(s.page))++result.markerCopies;else if(!blank(s.page))nonblankMarker=true;
    }
    if(!result.markerCopies){result.code=nonblankMarker?Code::Corrupt:Code::NotFormatted;return result;}
    Slot slots[2]{};bool invalidCommit=false;
    for(unsigned slot=0;slot<2;++slot){
        result.stage=Stage::CommitRead;
        if(!io.read(base(slot)+2,s.page)){result.code=Code::Io;return result;}
        slots[slot]=parseCommit(s.page,slot);
        if(!slots[slot].valid){if(!blank(s.page))invalidCommit=true;continue;}
        result.stage=Stage::PayloadRead;
        const auto code=readPayload(io,s,slot,slots[slot].payloadCrc);
        if(code==Code::Io){result.code=code;return result;}
        if(code!=Code::Ok){slots[slot].valid=false;invalidCommit=true;}
    }
    if(!slots[0].valid&&!slots[1].valid){result.code=invalidCommit?Code::Corrupt:Code::Empty;return result;}
    if(slots[0].valid&&slots[1].valid&&slots[0].generation==slots[1].generation){result.code=Code::Corrupt;return result;}
    result.slot=(!slots[0].valid||(slots[1].valid&&slots[1].generation>slots[0].generation))?1:0;
    result.generation=slots[result.slot].generation;result.code=Code::Ok;return result;
}
template<class IO> constexpr Result load(IO& io,Scratch& s,Payload& out){
    auto result=scan(io,s);if(result.code!=Code::Ok)return result;
    result.stage=Stage::CommitRead;
    if(!io.read(base(unsigned(result.slot))+2,s.page)){result.code=Code::Io;return result;}
    const auto info=parseCommit(s.page,unsigned(result.slot));
    if(!info.valid||info.generation!=result.generation){result.code=Code::Verify;return result;}
    result.stage=Stage::PayloadRead;result.code=readPayload(io,s,unsigned(result.slot),info.payloadCrc);
    if(result.code==Code::Ok)out=s.payload;
    return result;
}
template<class IO> constexpr Result save(IO& io,Scratch& s,const Payload& input){
    auto result=scan(io,s);
    if(result.code!=Code::Ok&&result.code!=Code::Empty)return result;
    if(result.generation==UINT64_MAX){result.code=Code::GenerationExhausted;return result;}
    const unsigned slot=result.slot==0?1:0;const uint64_t generation=result.generation+1;
    const uint32_t expectedCrc=crc(input,PayloadSize);
    result.stage=Stage::PayloadWrite;
    for(unsigned half=0;half<2;++half){
        s.page={};for(size_t i=0;i<PageSize&&half*PageSize+i<PayloadSize;++i)s.page[i]=input[half*PageSize+i];
        if(!io.write(base(slot)+half,s.page)){result.code=Code::Io;return result;}
    }
    result.stage=Stage::PayloadSync;if(!io.sync()){result.code=Code::Io;return result;}
    result.stage=Stage::PayloadVerify;result.code=readPayload(io,s,slot,expectedCrc);
    if(result.code!=Code::Ok)return result;
    if(s.payload!=input){result.code=Code::Verify;return result;}
    commit(s.page,slot,generation,expectedCrc);result.stage=Stage::CommitWrite;
    if(!io.write(base(slot)+2,s.page)){result.code=Code::Io;return result;}
    result.stage=Stage::CommitSync;if(!io.sync()){result.code=Code::Io;return result;}
    result.stage=Stage::CommitVerify;
    if(!io.read(base(slot)+2,s.verify)){result.code=Code::Io;return result;}
    if(s.verify!=s.page){result.code=Code::Verify;return result;}
    result.code=readPayload(io,s,slot,expectedCrc);if(result.code!=Code::Ok)return result;
    if(s.payload!=input){result.code=Code::Verify;return result;}
    result.generation=generation;result.slot=int(slot);return result;
}
// Erase is deliberately outside the model: adapter uses official bad-block-aware
// spi_nand_erase_chip ONLY after explicit authorization. Never called by load/save.
template<class IO> constexpr Result writeFormatMarkers(IO& io,Scratch& s){
    Result result;marker(s.page);result.stage=Stage::FormatWrite;
    for(unsigned copy=0;copy<2;++copy)if(!io.write(copy,s.page)){result.code=Code::Io;return result;}
    result.stage=Stage::FormatSync;if(!io.sync()){result.code=Code::Io;return result;}
    result.stage=Stage::FormatVerify;
    for(unsigned copy=0;copy<2;++copy){
        if(!io.read(copy,s.verify)){result.code=Code::Io;return result;}
        if(s.verify!=s.page){result.code=Code::Verify;return result;}++result.markerCopies;
    }
    return result;
}
}
