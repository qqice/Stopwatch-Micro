#pragma once
#include "mosaico_threshold_audit_model.h"
#include "mosaico_gauge_calibration_read_model.h"
#include "mosaico_gauge_public_extra_read_model.h"
#include <array>

namespace mosaico_near {
constexpr uint16_t address=0x926B;
constexpr size_t index=19, count=131;
enum Phase:uint8_t {Pending=1,Active=2,Restored=3,Failed=4};
using Record=std::array<uint8_t,24>;
using Snapshot=std::array<uint32_t,count>;
constexpr bool allowed(uint16_t value){return value==200||value==5;}
constexpr void seal(Record& r){const auto crc=mosaico_threshold::crc32(r.data(),20);for(unsigned i=0;i<4;++i)r[20+i]=crc>>(8*i);}
constexpr Record make(const uint8_t* mac,uint16_t target,Phase phase){
 Record r{};r[0]='N';r[1]='F';r[2]=1;r[3]=phase;
 for(unsigned i=0;i<6;++i)r[4+i]=mac[i];
 r[10]=200;r[12]=target;r[13]=target>>8;r[14]=0x6b;r[15]=0x92;r[16]=0x20;r[17]=0x02;
 seal(r);return r;
}
constexpr bool valid(const Record& r,const uint8_t* mac){
 auto copy=r;seal(copy);if(copy!=r||r[0]!='N'||r[1]!='F'||r[2]!=1||r[3]<Pending||r[3]>Failed||
 r[10]!=200||r[11]||!allowed(r[12]|(uint16_t(r[13])<<8))||r[14]!=0x6b||r[15]!=0x92||r[16]!=0x20||r[17]!=0x02||r[18]||r[19])return false;
 for(unsigned i=0;i<6;++i)if(r[4+i]!=mac[i])return false;
 return true;
}
constexpr uint16_t target(const Record& r){return r[12]|(uint16_t(r[13])<<8);}
constexpr bool unchanged(const Snapshot& a,const Snapshot& b){for(size_t i=0;i<count;++i)if(i!=index&&a[i]!=b[i])return false;return true;}
struct Field {uint16_t address;const char* type;unsigned width;};
constexpr Field field(size_t i){
 if(i<59){const auto f=mosaico_threshold::fields[i];using T=mosaico_threshold::Type;return {uint16_t(f.selector),f.type==T::I2?"I2":f.type==T::U1?"U1":f.type==T::H1?"H1":f.type==T::U2?"U2":"H2",f.type==T::U1||f.type==T::H1?1U:2U};}
 if(i<66){const auto f=mosaico_calibration::fields[i-59];using T=mosaico_calibration::Type;return {uint16_t(f.selector),f.type==T::F4?"F4":f.type==T::I2?"I2":f.type==T::I1?"I1":"U1",f.width};}
 const auto f=mosaico_public_extra::fields[i-66];return {uint16_t(f.selector),mosaico_public_extra::typeName(f.type),f.width};
}
static_assert(uint16_t(mosaico_threshold::fields[index].selector)==address);
} // namespace mosaico_near
