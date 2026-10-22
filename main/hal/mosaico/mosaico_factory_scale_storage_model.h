#pragma once
#include "mosaico_factory_scale_journal_model.h"

// GSJ3 portable durable store. No physical unit or hardware admission proof.
// Caller must allocate StorageWorkspace once (prefer PSRAM), exclusively own it
// for the transaction, and keep next separate from all workspace record storage.
// Every failed save clears selected; successful save reloads both committed slots.
namespace mosaico_factory_scale_journal {
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
struct StorageWorkspace {Loaded loaded{};Blob io{},encoded{};Record scratch{};Workspace codec{};};
template<class Backend> constexpr Status loadInto(Backend& store,const std::array<uint8_t,6>& mac,StorageWorkspace& work){
    auto& l=work.loaded;l.status=Status::Missing;l.valid={false,false};l.selected=-1;
    bool corrupt=false,io=false;unsigned present=0;
    for(unsigned i=0;i<2;++i){auto& b=work.io;const auto r=store.read(i,b);
        if(r==ReadResult::Missing)continue;
        ++present;
        if(r==ReadResult::Error){io=true;continue;}
        l.valid[i]=decode(b,mac,l.records[i],work.codec);if(!l.valid[i])corrupt=true;}
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
inline constexpr Status fail(StorageWorkspace& work,Status status){work.loaded.status=status;work.loaded.selected=-1;return status;}
template<class Backend> constexpr Status saveInto(Backend& store,const Record& next,const WriteAdmission& admission,StorageWorkspace& work){
    if(&next==&work.loaded.records[0]||&next==&work.loaded.records[1]||&next==&work.scratch)return fail(work,Status::Rejected);
    if(!admission.gaugeTransactionHeld||!admission.otaLeaseHeld||!admission.stableVoltage)return fail(work,Status::Rejected);
    auto& encoded=work.encoded;if(!encode(next,encoded,work.codec))return fail(work,Status::Rejected);
    loadInto(store,next.mac,work);const auto& old=work.loaded;unsigned slot=0;
    if(old.status==Status::Missing){if(next.snapshot.generation!=1||next.snapshot.phase!=recovery::Phase::Original)return fail(work,Status::Rejected);}
    else if(old.status==Status::Ready){if(!transition(old.records[size_t(old.selected)],next))return fail(work,Status::Rejected);slot=1-unsigned(old.selected);}
    else return fail(work,old.status);
    if(!store.write(slot,encoded))return fail(work,Status::WriteFailed);
    if(!store.commit())return fail(work,Status::CommitFailed);
    auto& readback=work.io;auto& decoded=work.scratch;
    if(store.read(slot,readback)!=ReadResult::Present||readback!=encoded||!decode(readback,next.mac,decoded,work.codec))return fail(work,Status::ReadbackFailed);
    return loadInto(store,next.mac,work);
}

} // namespace mosaico_factory_scale_journal
