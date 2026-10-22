#pragma once
#include "mosaico_factory_scale_storage_model.h"
// Entry accounting only, never an NVS reservation or hardware authorization.
namespace mosaico_factory_scale_budget {
namespace journal=mosaico_factory_scale_journal;
constexpr size_t prepareBase=74,revalidateBase=92,preKeyBase=54;
constexpr size_t persistedPerSlot=18,persistedCreditMaximum=2*persistedPerSlot;
static_assert(journal::blobSize==512);
// Only the active persistent versions are reusable. A new cross-page blob may
// need19 entries; transient versions, GC and access receipts retain their margin.
static_assert((journal::blobSize+31)/32+2==persistedPerSlot);
struct Usage {size_t entries=0;bool exactNamespace=false,exactKeys=false,exactLengths=false,stableStats=false;};
struct Budget {size_t base=0,reuse=0,required=0;};
constexpr bool pair(const journal::Loaded& l){
 if(l.status!=journal::Status::Ready||l.selected<0||l.selected>1||!l.valid[0]||!l.valid[1])return false;
 const auto i=size_t(l.selected),old=1-i;
 return journal::shape(l.records[0])&&journal::shape(l.records[1])&&
  l.records[i].snapshot.phase==journal::recovery::Phase::Original&&
  journal::sameRecoveryImage(l.records[0],l.records[1])&&journal::transition(l.records[old],l.records[i]);
}
constexpr size_t credit(const journal::Loaded& l,const Usage& u,bool clean){
 return clean&&pair(l)&&u.exactNamespace&&u.exactKeys&&u.exactLengths&&u.stableStats&&u.entries>=persistedCreditMaximum?
  (u.entries<persistedCreditMaximum?u.entries:persistedCreditMaximum):0;
}
constexpr Budget budget(size_t base,const journal::Loaded& l,const Usage& u,bool clean){
 const auto reuse=credit(l,u,clean);return {base,reuse,base>=reuse?base-reuse:base};
}
// Stack-local, one Apply invocation only. image points at the runtime's existing
// caller-owned record (not the reload workspace); no copied131-field stack object.
struct Proof {
 const journal::Record* image=nullptr;size_t entries=0;
 uint64_t epochs[2]{};uint32_t generations[2]{};int selected=-1;
 std::array<uint8_t,6> mac{};uint32_t sourceCrc=0,targetCrc=0;
};
static_assert(sizeof(Proof)<=64);
constexpr Proof proof(const journal::Loaded& l,const Usage& u,const journal::Record& owned,bool clean){
 Proof p{};
 if(!credit(l,u,clean)||!journal::shape(owned)||owned.snapshot.phase!=journal::recovery::Phase::Original||!journal::sameRecoveryImage(owned,l.records[size_t(l.selected)])||
    owned.snapshot.generation!=l.records[size_t(l.selected)].snapshot.generation||
    owned.snapshot.epoch!=l.records[size_t(l.selected)].snapshot.epoch)return p;
 p.image=&owned;p.entries=u.entries;p.selected=l.selected;
 p.mac=owned.mac;p.sourceCrc=journal::factory::digest(owned.snapshot,false);p.targetCrc=journal::factory::digest(owned.snapshot,true);
 for(unsigned i=0;i<2;++i){p.epochs[i]=l.records[i].snapshot.epoch;p.generations[i]=l.records[i].snapshot.generation;}
 return p;
}
constexpr bool identityMatches(const Proof& p,const journal::Loaded& l){
 if(!p.image||!pair(l)||p.selected!=l.selected||!journal::shape(*p.image)||p.mac!=p.image->mac||p.mac!=l.records[size_t(l.selected)].mac||
    p.sourceCrc!=journal::factory::digest(l.records[size_t(l.selected)].snapshot,false)||p.targetCrc!=journal::factory::digest(l.records[size_t(l.selected)].snapshot,true)||p.image->snapshot.phase!=journal::recovery::Phase::Original||
    p.image->snapshot.generation!=l.records[size_t(l.selected)].snapshot.generation||p.image->snapshot.epoch!=l.records[size_t(l.selected)].snapshot.epoch||
    !journal::sameRecoveryImage(*p.image,l.records[size_t(l.selected)]))return false;
 for(unsigned i=0;i<2;++i)if(p.epochs[i]!=l.records[i].snapshot.epoch||p.generations[i]!=l.records[i].snapshot.generation)return false;
 return true;
}
constexpr bool matches(const Proof& p,const journal::Loaded& l,const Usage& u,bool clean){
 return credit(l,u,clean)&&p.entries==u.entries&&identityMatches(p,l);
}
} // namespace mosaico_factory_scale_budget
