#pragma once
#include "mosaico_gauge_model.h"
#include "mosaico_fcc_limit_journal_codec.h"
#include <array>
namespace mosaico_native_recovery {
struct Evidence { std::array<uint8_t,28> reload{}; mosaico_fcc_limit::Record cold{},runtime{}; };
inline bool valid(const Evidence& e,const uint8_t* mac){
 mosaico_gauge::ReloadJournal r{};std::memcpy(&r,e.reload.data(),sizeof(r));
 mosaico_fcc_limit::Journal cold{},runtime{};
 return mosaico_gauge::validReloadJournal(r,mac)&&
  mosaico_fcc_limit::decode(e.cold,mac,cold,mosaico_fcc_limit::Mode::ColdFactory)&&
  mosaico_fcc_limit::decode(e.runtime,mac,runtime);
}
inline bool exact(const Evidence& e,const uint8_t* mac){
 if(!valid(e,mac))return false;
 mosaico_gauge::ReloadJournal r{};std::memcpy(&r,e.reload.data(),sizeof(r));
 mosaico_fcc_limit::Journal c{},v{};
 if(!mosaico_fcc_limit::decode(e.cold,mac,c,mosaico_fcc_limit::Mode::ColdFactory)||!mosaico_fcc_limit::decode(e.runtime,mac,v))return false;
 using namespace mosaico_fcc_limit;
 return r.state==uint8_t(mosaico_gauge::ReloadState::Failed)&&c.state==State::Failed&&v.state==State::Verified&&
  c.intents==1951&&v.intents==927&&c.backupValid&&c.fullAccessVerified&&v.backupValid&&v.fullAccessVerified&&
  same(c.before,{0x102a,3000,3000})&&same(v.before,{0x102a,65,100})&&
  same(c.target,{0x112a,65,65})&&same(v.target,{0x112a,65,65});
}
// Fixed order reload, cold, runtime; uint32 little-endian length precedes raw.
// CRC is a backup acknowledgement token, NOT authentication.
inline uint32_t digest(const Evidence& e){
 uint32_t c=0xffffffffU;auto byte=[&](uint8_t v){c^=v;for(unsigned b=0;b<8;++b)c=(c>>1)^((c&1)?0xedb88320U:0);};
 auto block=[&](const uint8_t* p,size_t n){for(unsigned j=0;j<4;++j)byte(uint8_t(n>>(8*j)));for(size_t i=0;i<n;++i)byte(p[i]);};
 block(e.reload.data(),28);block(e.cold,48);block(e.runtime,48);return ~c;
}
struct Proof {
 bool ready=false,original=false,verified=false,durable=false,complete=false,sameGeneration=false;
 uint32_t sourceCrc=0,generation=0;int64_t preparedUs=0,nowUs=0;
};
constexpr bool fresh(const Proof& p){return p.ready&&p.original&&p.verified&&p.durable&&p.complete&&p.sameGeneration&&
 p.sourceCrc==0x91336fc0U&&p.generation&&p.preparedUs>0&&p.nowUs>=p.preparedUs&&p.nowUs-p.preparedUs<=120000000;}
}
