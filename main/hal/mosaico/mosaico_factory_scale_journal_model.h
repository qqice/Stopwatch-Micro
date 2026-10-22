#pragma once
#include "mosaico_factory_scale_model.h"
#include "mosaico_gauge_scale_journal_model.h"

// OFFLINE codec/chain model ONLY. No NVS, durable backend, locks, device writer,
// or proof of image signatures. CRC detects corruption, NOT authenticity.
// Schema3 Original means factory3000 raw rollback, UNKNOWN/UNTRUSTED units.
// It must NEVER be interpreted as native65 or factor1 physical telemetry.
namespace mosaico_factory_scale_journal {
namespace factory=mosaico_factory_scale;
namespace recovery=mosaico_gauge_recovery;
inline constexpr bool durableBackendImplemented=false,readyForHardware=false;
inline constexpr size_t blobSize=512;
using Blob=std::array<uint8_t,blobSize>;
struct Record {
    factory::Profile profile=factory::Profile::Unknown;
    std::array<uint8_t,6> mac{};
    recovery::Snapshot snapshot{};
};
// Caller-owned scratch; never place nested full snapshots on embedded stacks.
// This is an explicitly bounded wire-codec adapter, NOT an old Record alias or
// old physical-unit admission. No external Envelope proof flags are serialized.
struct Workspace {
    mosaico_gauge_scale_journal::Record translated{};
    mosaico_gauge_scale_journal::Blob bytes{};
};
constexpr bool shape(const Record& r){
    const auto& s=r.snapshot;
    if(r.profile!=factory::Profile::Factory3000RollbackToNominal65Virtual130||
       !recovery::knownIdentity(s.identity)||!s.generation||!s.epoch||
       !recovery::validPhase(s.phase)||s.layoutId!=recovery::Layout::Public131V1||
       (r.mac[0]&1)||!factory::closedImageTemplate(s)){return false;}
    const auto binding=mosaico_gauge_scale_journal::binding(r.mac);
    return recovery::nonzero(binding)&&recovery::sameBinding(s.binding,binding);
}
constexpr bool encode(const Record& r,Blob& out,Workspace& work){
    if(!shape(r)){return false;}
    work.translated.mac=r.mac;work.translated.snapshot=r.snapshot;
    if(!mosaico_gauge_scale_journal::encode(work.translated,work.bytes)){return false;}
    work.bytes[3]='3';mosaico_gauge_scale_journal::put(work.bytes.data()+4,3,2);
    work.bytes[21]=uint8_t(r.profile);
    mosaico_gauge_scale_journal::put(work.bytes.data()+blobSize-4,
        mosaico_gauge_scale_journal::crc32(work.bytes.data(),blobSize-4),4);
    out=work.bytes;return true;
}
// Wrong schema/profile is rejected BEFORE translation. Old schema2 decoder
// independently rejects these GSJ3 bytes. MAC is supplied, never inferred.
constexpr bool decode(const Blob& in,const std::array<uint8_t,6>& expectedMac,Record& out,Workspace& work){
    using namespace mosaico_gauge_scale_journal;
    // Translation must never mutate an input evidence blob through scratch alias.
    if(&in==&work.bytes){return false;}
    if(in[0]!='G'||in[1]!='S'||in[2]!='J'||in[3]!='3'||get(in.data()+4,2)!=3||
       get(in.data()+6,2)!=blobSize||in[21]!=uint8_t(factory::Profile::Factory3000RollbackToNominal65Virtual130)||
       get(in.data()+blobSize-4,4)!=crc32(in.data(),blobSize-4)){return false;}
    work.bytes=in;work.bytes[3]='2';put(work.bytes.data()+4,2,2);work.bytes[21]=0;
    put(work.bytes.data()+blobSize-4,crc32(work.bytes.data(),blobSize-4),4);
    if(!mosaico_gauge_scale_journal::decode(work.bytes,expectedMac,work.translated)){return false;}
    // Reuse caller-owned translated snapshot; no full local Record is needed.
    // On failure out is untrusted/unspecified, never usable without true return.
    out.profile=factory::Profile::Factory3000RollbackToNominal65Virtual130;
    out.mac=work.translated.mac;out.snapshot=work.translated.snapshot;
    return shape(out);
}
constexpr bool sameRecoveryImage(const Record& a,const Record& b){
    if(a.profile!=b.profile||a.mac!=b.mac||
       !recovery::sameIdentity(a.snapshot.identity,b.snapshot.identity)||
       !recovery::sameBinding(a.snapshot.binding,b.snapshot.binding)||
       a.snapshot.layoutId!=b.snapshot.layoutId||a.snapshot.scale!=b.snapshot.scale||
       a.snapshot.count!=b.snapshot.count){return false;}
    for(size_t i=0;i<recovery::fieldCount;++i){const auto& x=a.snapshot.fields[i];const auto& y=b.snapshot.fields[i];
        if(x.address!=y.address||x.width!=y.width){return false;}
        for(unsigned j=0;j<4;++j){if(x.original[j]!=y.original[j]||x.target[j]!=y.target[j]){return false;}}
    }return true;
}
constexpr bool initialRecord(const Record& r){
    return shape(r)&&r.snapshot.generation==1&&r.snapshot.phase==recovery::Phase::Original;
}
constexpr bool transition(const Record& a,const Record& b){
    using recovery::Phase;const auto& x=a.snapshot;const auto& y=b.snapshot;
    if(!shape(a)||!shape(b)||!sameRecoveryImage(a,b)||x.generation==UINT32_MAX||
       y.generation!=x.generation+1||!recovery::advancing(x.epoch,y.epoch)){return false;}
    return (x.phase==Phase::Original&&(y.phase==Phase::Pending||y.phase==Phase::Restoring))||
        (x.phase==Phase::Pending&&(y.phase==Phase::Applied||y.phase==Phase::Restoring))||
        (x.phase==Phase::Applied&&y.phase==Phase::Restoring)||
        (x.phase==Phase::Restoring&&y.phase==Phase::Original);
}
enum class ChainStatus:uint8_t {MissingFailClosed,InitialRecord,Sequential,Rejected};
// Pure structural history validation, NOT a slot reader/selector or durability
// assertion. An absent initial record cannot legitimize Pending/Applied data.
constexpr ChainStatus validateChain(const Record* previous,const Record* current){
    if(!previous&&!current){return ChainStatus::MissingFailClosed;}
    if(!previous){return initialRecord(*current)?ChainStatus::InitialRecord:ChainStatus::Rejected;}
    if(!current){return ChainStatus::Rejected;}
    return transition(*previous,*current)?ChainStatus::Sequential:ChainStatus::Rejected;
}
static_assert(blobSize==mosaico_gauge_scale_journal::blobSize);
} // namespace mosaico_factory_scale_journal
