#pragma once
#include "mosaico_gauge_contrast_model.h"

namespace mosaico_gauge_contrast_journal {
using namespace mosaico_gauge_contrast;
inline constexpr size_t headerSize=64,blobSize=320;
using Blob=std::array<uint8_t,blobSize>;
// IDF blob: one blob-index entry + ceil(bytes/32) data + one header per
// chunk. A 320-byte blob can span at most 2 pages/chunks: conservative 13.
// Two live slots + one replacement + namespace + 16 headroom = 56. No credit
// from free_entries/reserved space or from presumed erased old records.
inline constexpr size_t entriesPerBlob=(blobSize+31)/32+3;
inline constexpr size_t initialRequiredEntries=3*entriesPerBlob+1+16;
static_assert(blobSize<=384&&initialRequiredEntries==56);
constexpr bool encode(const Record& r,Blob& b){
    if(!shape(r))return false;
    b={};b[0]='G';b[1]='C';b[2]='T';b[3]='1';bytes::put(b.data()+4,1,2);bytes::put(b.data()+6,blobSize,2);
    for(size_t i=0;i<6;++i)b[8+i]=r.mac[i];
    bytes::put(b.data()+14,r.identity.device,2);bytes::put(b.data()+16,r.identity.firmware,2);b[18]=r.identity.build;
    b[19]=uint8_t(r.phase);b[20]=uint8_t(r.arm);b[21]=uint8_t(recovery::Layout::Public131V1);
    bytes::put(b.data()+24,r.generation,4);bytes::put(b.data()+28,r.epoch,8);
    bytes::put(b.data()+36,recovery::fieldCount,2);bytes::put(b.data()+38,rawSize,2);
    bytes::put(b.data()+40,canonicalSourceCrc,4);bytes::put(b.data()+44,bytes::layoutFingerprint(),4);
    for(size_t i=0;i<rawSize;++i)b[headerSize+i]=r.source[i];
    bytes::put(b.data()+blobSize-4,bytes::crc32(b.data(),blobSize-4),4);return true;
}
constexpr bool decode(const Blob& b,const std::array<uint8_t,6>& mac,Record& out){
    if(b[0]!='G'||b[1]!='C'||b[2]!='T'||b[3]!='1'||bytes::get(b.data()+4,2)!=1||bytes::get(b.data()+6,2)!=blobSize||
       bytes::get(b.data()+blobSize-4,4)!=bytes::crc32(b.data(),blobSize-4))return false;
    for(size_t i=0;i<6;++i)if(b[8+i]!=mac[i])return false;
    if(b[21]!=uint8_t(recovery::Layout::Public131V1)||b[22]||b[23]||
       bytes::get(b.data()+36,2)!=recovery::fieldCount||bytes::get(b.data()+38,2)!=rawSize||
       bytes::get(b.data()+40,4)!=canonicalSourceCrc||bytes::get(b.data()+44,4)!=bytes::layoutFingerprint())return false;
    for(size_t i=48;i<headerSize;++i)if(b[i])return false;
    for(size_t i=headerSize+rawSize;i<blobSize-4;++i)if(b[i])return false;
    Record r{};r.mac=mac;r.identity={uint16_t(bytes::get(b.data()+14,2)),uint16_t(bytes::get(b.data()+16,2)),b[18]};
    r.phase=Phase(b[19]);r.arm=Arm(b[20]);r.generation=uint32_t(bytes::get(b.data()+24,4));r.epoch=bytes::get(b.data()+28,8);
    for(size_t i=0;i<rawSize;++i)r.source[i]=b[headerSize+i];
    if(!shape(r))return false;
    out=r;return true;
}
enum class ReadResult:uint8_t {Missing,Present,Error};
struct Store {
    void* context=nullptr;
    ReadResult (*readFn)(void*,unsigned,Blob&)=nullptr;
    bool (*writeFn)(void*,unsigned,const Blob&)=nullptr;
    bool (*commitFn)(void*)=nullptr;
    bool (*capacityFn)(void*,size_t&)=nullptr;
    ReadResult read(unsigned i,Blob& b){return readFn?readFn(context,i,b):ReadResult::Error;}
    bool write(unsigned i,const Blob& b){return writeFn&&writeFn(context,i,b);}
    bool commit(){return commitFn&&commitFn(context);}
    bool available(size_t& n){return capacityFn&&capacityFn(context,n);}
};
enum class Status:uint8_t {Missing,Ready,Corrupt,IoError,Ambiguous,Rejected,NoSpace,WriteFailed,CommitFailed,ReadbackFailed};
struct Loaded {Status status=Status::Missing;std::array<Record,2> records{};std::array<bool,2> valid{};int selected=-1;};
struct Workspace {Loaded loaded{};Blob io{},encoded{};Record scratch{};};
struct Admission {bool transactionHeld=false,otaLeaseHeld=false,stableVoltage=false;};
template<class Backend> constexpr Status loadInto(Backend& store,const std::array<uint8_t,6>& mac,Workspace& w){
    auto& l=w.loaded;l.status=Status::Missing;l.valid={false,false};l.selected=-1;
    bool corrupt=false,io=false;unsigned present=0;
    for(unsigned i=0;i<2;++i){const auto r=store.read(i,w.io);if(r==ReadResult::Missing)continue;++present;
        if(r==ReadResult::Error){io=true;continue;}l.valid[i]=decode(w.io,mac,l.records[i]);corrupt|=!l.valid[i];}
    if(io)return l.status=Status::IoError;
    if(corrupt)return l.status=Status::Corrupt;
    if(!present)return l.status;
    if(present==1){const unsigned i=l.valid[0]?0:1;
        if(l.records[i].generation!=1||l.records[i].phase!=Phase::Original)return l.status=Status::Ambiguous;
        l.selected=int(i);return l.status=Status::Ready;}
    const auto a=l.records[0].generation,b=l.records[1].generation;
    if(a==b)return l.status=Status::Ambiguous;
    const unsigned newer=a>b?0:1;
    if(!transition(l.records[1-newer],l.records[newer]))return l.status=Status::Ambiguous;
    l.selected=int(newer);return l.status=Status::Ready;
}
template<class Backend> constexpr Status saveInto(Backend& store,const Record& next,const Admission& admission,Workspace& w){
    if(&next==&w.loaded.records[0]||&next==&w.loaded.records[1]||
       !admission.transactionHeld||!admission.otaLeaseHeld||!admission.stableVoltage||!encode(next,w.encoded))return Status::Rejected;
    loadInto(store,next.mac,w);unsigned slot=0;
    if(w.loaded.status==Status::Missing){
        if(next.generation!=1||next.phase!=Phase::Original)return Status::Rejected;
        size_t n=0;if(!store.available(n))return Status::IoError;
        if(n<initialRequiredEntries)return Status::NoSpace;
    }else if(w.loaded.status==Status::Ready){
        if(!transition(w.loaded.records[size_t(w.loaded.selected)],next))return Status::Rejected;
        slot=1-unsigned(w.loaded.selected);
    }else return w.loaded.status;
    if(!store.write(slot,w.encoded))return Status::WriteFailed;
    if(!store.commit())return Status::CommitFailed;
    if(store.read(slot,w.io)!=ReadResult::Present||w.io!=w.encoded||!decode(w.io,next.mac,w.scratch))return Status::ReadbackFailed;
    return Status::Ready; // caller must reload; loaded never aliases next
}
} // namespace mosaico_gauge_contrast_journal
