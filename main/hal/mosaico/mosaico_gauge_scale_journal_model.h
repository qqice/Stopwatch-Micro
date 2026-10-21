#pragma once
#include "mosaico_gauge_recovery_model.h"

// Portable storage only: never authorizes a gauge write or physical telemetry.
// CRC32 detects corruption, not malicious tampering/authenticity.
namespace mosaico_gauge_scale_journal {
namespace recovery = mosaico_gauge_recovery;
inline constexpr size_t headerSize=64;
constexpr size_t rawImageSize(){size_t n=0;for(const auto& f:recovery::layout)n+=f.width;return n;}
inline constexpr size_t blobSize=headerSize+2*rawImageSize()+4;
static_assert(rawImageSize()==222&&blobSize==512,"lossless canonical public131 compact schema2");
using Blob=std::array<uint8_t,blobSize>;
struct Record { std::array<uint8_t,6> mac{}; recovery::Snapshot snapshot{}; };
constexpr uint32_t crc32(const uint8_t* p,size_t n) {
    uint32_t c=0xffffffffU;
    for(size_t i=0;i<n;++i){c^=p[i];for(unsigned j=0;j<8;++j)c=(c>>1)^((c&1)?0xedb88320U:0);}
    return ~c;
}
constexpr uint32_t layoutFingerprint(){
    uint32_t c=0xffffffffU;
    for(const auto& f:recovery::layout)for(unsigned i=0;i<3;++i){
        c^=i<2?uint8_t(f.address>>(8*i)):f.width;
        for(unsigned bit=0;bit<8;++bit)c=(c>>1)^((c&1)?0xedb88320U:0);
    }
    return ~c;
}
constexpr size_t payloadOffset(size_t index){size_t n=headerSize;for(size_t i=0;i<index;++i)n+=2*recovery::layout[i].width;return n;}
constexpr void put(uint8_t* p,uint64_t v,size_t n){for(size_t i=0;i<n;++i)p[i]=uint8_t(v>>(8*i));}
constexpr uint64_t get(const uint8_t* p,size_t n){uint64_t v=0;for(size_t i=0;i<n;++i)v|=uint64_t(p[i])<<(8*i);return v;}
constexpr recovery::Binding binding(const std::array<uint8_t,6>& mac){
    recovery::Binding b{};for(size_t i=0;i<6;++i)b.bytes[i]=mac[i];return b;
}
constexpr bool shape(const Record& r){
    const auto& s=r.snapshot;recovery::Context c{};
    c.identity=s.identity;c.binding=binding(r.mac);c.layoutId=recovery::Layout::Public131V1;
    c.sourceCrcAttested=c.reviewedTargetAttested=true;
    // MAC must be nonzero, unicast, not a locally invented opaque binding.
    return !(r.mac[0]&1)&&recovery::validate(s,c)==recovery::Error::None;
}
// Digest includes each canonical key, width and exact used/zero-padded raw bytes.
constexpr uint32_t imageDigest(const Record& r,bool target){
    uint32_t c=0xffffffffU;
    for(const auto& f:r.snapshot.fields){
        for(size_t j=0;j<7;++j){const uint8_t v=j<2?uint8_t(f.address>>(8*j)):j==2?f.width:target?f.target[j-3]:f.original[j-3];
            c^=v;for(unsigned bit=0;bit<8;++bit)c=(c>>1)^((c&1)?0xedb88320U:0);}
    }
    return ~c;
}
constexpr bool encode(const Record& r,Blob& out){
    if(!shape(r))return false;
    auto& b=out;for(auto& v:b)v=0;
    b[0]='G';b[1]='S';b[2]='J';b[3]='2';put(b.data()+4,2,2);put(b.data()+6,blobSize,2);
    for(size_t i=0;i<6;++i)b[8+i]=r.mac[i];
    const auto& s=r.snapshot;
    put(b.data()+14,s.identity.device,2);put(b.data()+16,s.identity.firmware,2);b[18]=s.identity.build;
    b[19]=uint8_t(s.phase);b[20]=uint8_t(s.scale);put(b.data()+22,uint8_t(s.layoutId),2);
    put(b.data()+24,s.generation,4);put(b.data()+28,s.epoch,8);put(b.data()+36,s.count,2);
    put(b.data()+40,imageDigest(r,false),4);put(b.data()+44,imageDigest(r,true),4);
    put(b.data()+48,layoutFingerprint(),4);
    size_t n=headerSize;for(const auto& f:s.fields){
        for(size_t j=0;j<f.width;++j)b[n++]=f.original[j];
        for(size_t j=0;j<f.width;++j)b[n++]=f.target[j];}
    put(b.data()+blobSize-4,crc32(b.data(),blobSize-4),4);return true;
}
// On failure out is untrusted/unspecified. Expected MAC is mandatory, never inferred from NVS.
constexpr bool decode(const Blob& b,const std::array<uint8_t,6>& mac,Record& out){
    if(b[0]!='G'||b[1]!='S'||b[2]!='J'||b[3]!='2'||get(b.data()+4,2)!=2||get(b.data()+6,2)!=blobSize||
       get(b.data()+blobSize-4,4)!=crc32(b.data(),blobSize-4))return false;
    for(size_t i=0;i<6;++i)if(b[8+i]!=mac[i])return false;
    if(b[21]||b[38]||b[39])return false;
    if(get(b.data()+48,4)!=layoutFingerprint())return false;
    for(size_t i=52;i<headerSize;++i)if(b[i])return false;
    auto& r=out;r.mac=mac;auto& s=r.snapshot;s.binding=binding(mac);
    s.identity={uint16_t(get(b.data()+14,2)),uint16_t(get(b.data()+16,2)),b[18]};
    s.phase=recovery::Phase(b[19]);s.scale=recovery::Scale(b[20]);
    // Reject high-byte aliases rather than narrowing malformed layout IDs.
    if(get(b.data()+22,2)!=uint8_t(recovery::Layout::Public131V1))return false;
    s.layoutId=recovery::Layout::Public131V1;s.generation=uint32_t(get(b.data()+24,4));
    s.epoch=get(b.data()+28,8);s.count=size_t(get(b.data()+36,2));
    size_t n=headerSize;
    for(size_t i=0;i<recovery::fieldCount;++i){auto& f=s.fields[i];
        f.address=recovery::layout[i].address;f.width=recovery::layout[i].width;
        for(auto& v:f.original)v=0;
        for(auto& v:f.target)v=0;
        for(size_t j=0;j<f.width;++j)f.original[j]=b[n++];
        for(size_t j=0;j<f.width;++j)f.target[j]=b[n++];}
    if(!shape(r)||get(b.data()+40,4)!=imageDigest(r,false)||get(b.data()+44,4)!=imageDigest(r,true))return false;
    return true;
}
constexpr bool sameRecoveryImage(const Record& a,const Record& b){
    if(a.mac!=b.mac||!recovery::sameIdentity(a.snapshot.identity,b.snapshot.identity)||a.snapshot.scale!=b.snapshot.scale)return false;
    for(size_t i=0;i<recovery::fieldCount;++i){const auto& x=a.snapshot.fields[i];const auto& y=b.snapshot.fields[i];
        if(x.address!=y.address||x.width!=y.width)return false;
        for(size_t j=0;j<4;++j)if(x.original[j]!=y.original[j]||x.target[j]!=y.target[j])return false;}
    return true;
}
constexpr bool transition(const Record& a,const Record& b){
    using recovery::Phase;const auto& x=a.snapshot;const auto& y=b.snapshot;
    if(!shape(a)||!shape(b)||!sameRecoveryImage(a,b)||x.generation==UINT32_MAX||y.generation!=x.generation+1||
       !recovery::advancing(x.epoch,y.epoch))return false;
    return (x.phase==Phase::Original&&(y.phase==Phase::Pending||y.phase==Phase::Restoring))||
        (x.phase==Phase::Pending&&(y.phase==Phase::Applied||y.phase==Phase::Restoring))||
        (x.phase==Phase::Applied&&y.phase==Phase::Restoring)||
        (x.phase==Phase::Restoring&&y.phase==Phase::Original);
}
enum class ReadResult:uint8_t {Missing,Present,Error};
// A read must return Present only for an exact blobSize blob. Other sizes = Error.
struct Store {
    void* context=nullptr;
    ReadResult (*readFn)(void*,unsigned,Blob&)=nullptr;
    bool (*writeFn)(void*,unsigned,const Blob&)=nullptr;
    bool (*commitFn)(void*)=nullptr;
    ReadResult read(unsigned i,Blob& b){return readFn?readFn(context,i,b):ReadResult::Error;}
    bool write(unsigned i,const Blob& b){return writeFn&&writeFn(context,i,b);}
    bool commit(){return commitFn&&commitFn(context);}
};
enum class Status:uint8_t {Missing,Ready,Corrupt,IoError,Ambiguous,Rejected,WriteFailed,CommitFailed,ReadbackFailed};
struct Loaded {
    Status status=Status::Missing;
    std::array<Record,2> records{};
    std::array<bool,2> valid{}; // Retain each decodable recovery image even on fail-closed.
    int selected=-1; // Never use a record as current unless status == Ready.
};
// Caller-owned scratch; no nested full records/blobs on embedded task stacks.
// Exclusive ownership for the complete transaction; not reentrant/thread safe.
struct Workspace {Loaded loaded{};Blob io{},encoded{};Record scratch{};};
template<class Backend> constexpr Status loadInto(Backend& store,const std::array<uint8_t,6>& mac,Workspace& work){
    auto& l=work.loaded;l.status=Status::Missing;l.valid={false,false};l.selected=-1;
    bool corrupt=false,io=false;unsigned present=0;
    for(unsigned i=0;i<2;++i){auto& b=work.io;const auto r=store.read(i,b);
        if(r==ReadResult::Missing)continue;
        ++present;
        if(r==ReadResult::Error){io=true;continue;}
        l.valid[i]=decode(b,mac,l.records[i]);if(!l.valid[i])corrupt=true;}
    if(io){l.status=Status::IoError;return l.status;}if(corrupt){l.status=Status::Corrupt;return l.status;}
    if(!present)return l.status;
    if(present==1){const unsigned i=l.valid[0]?0:1;
        // Only the initial, pre-mutation Original record may stand alone.
        if(l.records[i].snapshot.generation!=1||l.records[i].snapshot.phase!=recovery::Phase::Original){l.status=Status::Ambiguous;return l.status;}
        l.selected=int(i);l.status=Status::Ready;return l.status;}
    const auto g0=l.records[0].snapshot.generation,g1=l.records[1].snapshot.generation;
    if(g0==g1){l.status=Status::Ambiguous;return l.status;}
    const unsigned newer=g0>g1?0:1;
    if(!transition(l.records[1-newer],l.records[newer])){l.status=Status::Ambiguous;return l.status;}
    l.status=Status::Ready;l.selected=int(newer);return l.status;
}
// Caller attestations, not locks/capabilities. HAL must hold these protections
// throughout load/write/commit/readback. No API erases or repairs corrupt slots.
struct WriteAdmission {bool gaugeTransactionHeld=false,otaLeaseHeld=false,stableVoltage=false;};
template<class Backend> constexpr Status saveInto(Backend& store,const Record& next,const WriteAdmission& admission,Workspace& work){
    if(&next==&work.loaded.records[0]||&next==&work.loaded.records[1])return Status::Rejected;
    if(!admission.gaugeTransactionHeld||!admission.otaLeaseHeld||!admission.stableVoltage)return Status::Rejected;
    auto& encoded=work.encoded;if(!encode(next,encoded))return Status::Rejected;
    loadInto(store,next.mac,work);const auto& old=work.loaded;unsigned slot=0;
    if(old.status==Status::Missing){if(next.snapshot.generation!=1||next.snapshot.phase!=recovery::Phase::Original)return Status::Rejected;}
    else if(old.status==Status::Ready){if(!transition(old.records[size_t(old.selected)],next))return Status::Rejected;slot=1-unsigned(old.selected);}
    else return old.status;
    if(!store.write(slot,encoded))return Status::WriteFailed;
    if(!store.commit())return Status::CommitFailed;
    auto& readback=work.io;auto& decoded=work.scratch;
    if(store.read(slot,readback)!=ReadResult::Present||readback!=encoded||!decode(readback,next.mac,decoded))return Status::ReadbackFailed;
    return Status::Ready;
}
// next may use work.scratch, but may NOT alias work.loaded.records (rejected).
// Portable test conveniences only: embedded callers MUST use caller-owned Workspace.
#ifndef ESP_PLATFORM
template<class Backend> constexpr Loaded load(Backend& store,const std::array<uint8_t,6>& mac){Workspace w{};loadInto(store,mac,w);return w.loaded;}
template<class Backend> constexpr Status save(Backend& store,const Record& next,const WriteAdmission& admission){Workspace w{};return saveInto(store,next,admission,w);}
#endif
} // namespace mosaico_gauge_scale_journal
