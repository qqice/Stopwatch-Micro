#pragma once
#include "mosaico_gauge_recovery_model.h"
#include <array>
namespace mosaico_factory_audit {
constexpr char nvsNamespace[]="gauge3kaudit",journalKey[]="f3_aud_v1";
constexpr size_t journalSize=64,backupSize=985;
constexpr size_t journalEntries=(journalSize+31)/32+3,backupEntries=(backupSize+31)/32+3;
constexpr size_t nextScaleEntries=73,capturePeak=3*journalEntries+2*backupEntries+1,finalAndNext=journalEntries+backupEntries+1+nextScaleEntries;
constexpr size_t requiredEntries=capturePeak>finalAndNext?capturePeak:finalAndNext;
static_assert(requiredEntries==113);
using JournalBytes=std::array<uint8_t,journalSize>;using BackupBytes=std::array<uint8_t,backupSize>;
enum class State:uint8_t {Pending=1,Completed=2,Failed=3};
enum Flag:uint16_t {UnsealIntent=1,FullIntent=2,SealIntent=4,UnsealVerified=8,FullVerified=16,ReadVerified=32,CloseVerified=64,BackupVerified=128};
struct Standard {uint16_t dc=0,fcc=0,op=0,rm=0,soc=0,mv=0,temp=0;int16_t current=0,average=0;};
struct Journal {State state=State::Pending;uint16_t flags=0,count=0;uint32_t generation=0,sourceCrc=0;std::array<uint8_t,6> mac{};Standard before{},after{};};
constexpr uint32_t crc(const uint8_t* p,size_t n){uint32_t c=0xffffffffU;for(size_t i=0;i<n;++i){c^=p[i];for(unsigned b=0;b<8;++b)c=(c>>1)^((c&1)?0xedb88320U:0);}return ~c;}
constexpr void put16(uint8_t* p,uint16_t v){p[0]=uint8_t(v);p[1]=uint8_t(v>>8);}
constexpr void put32(uint8_t* p,uint32_t v){for(unsigned i=0;i<4;++i)p[i]=uint8_t(v>>(8*i));}
constexpr uint16_t get16(const uint8_t* p){return uint16_t(p[0])|(uint16_t(p[1])<<8);}
constexpr uint32_t get32(const uint8_t* p){uint32_t v=0;for(unsigned i=0;i<4;++i)v|=uint32_t(p[i])<<(8*i);return v;}
constexpr bool safe(uint16_t op){return !(op&0x401)&&(op&0x20)&&((op>>1)&3)>=1;}
constexpr bool physical(const Standard& s,unsigned sec){return safe(s.op)&&((s.op>>1)&3)==sec&&s.dc==3000&&s.fcc==3000&&s.rm<=s.fcc&&s.soc<=100&&s.mv>=3600&&s.mv<=4350&&s.temp>=2832&&s.temp<=3131&&s.current>=-130&&s.current<=130&&s.average>=-130&&s.average<=130;}
constexpr bool shape(const Journal& j){return j.generation&&uint8_t(j.state)>=1&&uint8_t(j.state)<=3&&!(j.flags&~255)&&j.count<=131&&
 (j.state!=State::Completed||(j.flags==255&&j.count==131&&physical(j.before,3)&&physical(j.after,3)));}
constexpr bool encode(const Journal& j,JournalBytes& b){if(!shape(j))return false;b={};b[0]='F';b[1]='3';b[2]='A';b[3]='1';b[4]=1;b[5]=uint8_t(j.state);put16(b.data()+6,j.flags);put32(b.data()+8,j.generation);for(unsigned i=0;i<6;++i)b[12+i]=j.mac[i];put16(b.data()+18,0x220);put16(b.data()+20,3);put16(b.data()+22,4);put16(b.data()+24,j.count);put32(b.data()+28,j.sourceCrc);
 const Standard* ss[]={&j.before,&j.after};for(unsigned i=0;i<2;++i){const auto& s=*ss[i];auto* p=b.data()+32+i*14;put16(p,s.dc);put16(p+2,s.fcc);put16(p+4,s.op);put16(p+6,s.mv);put16(p+8,s.temp);put16(p+10,s.rm);p[12]=uint8_t(s.soc);}
 put32(b.data()+60,crc(b.data(),60));return true;}
constexpr bool decode(const JournalBytes& b,const std::array<uint8_t,6>& mac,Journal& j){
 if(b[0]!='F'||b[1]!='3'||b[2]!='A'||b[3]!='1'||b[4]!=1||get16(b.data()+18)!=0x220||get16(b.data()+20)!=3||get16(b.data()+22)!=4||b[26]||b[27]||get32(b.data()+60)!=crc(b.data(),60))return false;
 Journal v{};v.state=State(b[5]);v.flags=get16(b.data()+6);v.generation=get32(b.data()+8);v.count=get16(b.data()+24);v.sourceCrc=get32(b.data()+28);for(unsigned i=0;i<6;++i){v.mac[i]=b[12+i];if(v.mac[i]!=mac[i])return false;}
 Standard* ss[]={&v.before,&v.after};for(unsigned i=0;i<2;++i){auto& s=*ss[i];const auto* p=b.data()+32+i*14;s.dc=get16(p);s.fcc=get16(p+2);s.op=get16(p+4);s.mv=get16(p+6);s.temp=get16(p+8);s.rm=get16(p+10);s.soc=p[12];if(p[13])return false;}
 // Journal envelope records safe physical range; current/average were fresh
 // admission observations but are not reconstructed as historical measurements.
 if(!shape(v))return false;
 j=v;
 return true;
}
constexpr void initBackup(const Journal& j,BackupBytes& b){b={};b[0]='F';b[1]='3';b[2]='B';b[3]='1';b[4]=1;put32(b.data()+8,j.generation);for(unsigned i=0;i<6;++i)b[12+i]=j.mac[i];put16(b.data()+18,0x220);put16(b.data()+20,3);put16(b.data()+22,4);put16(b.data()+24,131);for(size_t i=0;i<131;++i){const auto& f=mosaico_gauge_recovery::layout[i];put16(b.data()+64+i*7,f.address);b[66+i*7]=f.width;}}
constexpr uint32_t sourceCrc(const BackupBytes& b){return crc(b.data()+64,917);}
constexpr void sealBackup(BackupBytes& b){put32(b.data()+28,sourceCrc(b));put32(b.data()+981,crc(b.data(),981));}
constexpr bool validBackup(const BackupBytes& b,const Journal& j){
 if(b[0]!='F'||b[1]!='3'||b[2]!='B'||b[3]!='1'||b[4]!=1||get32(b.data()+8)!=j.generation||get16(b.data()+18)!=0x220||get16(b.data()+20)!=3||get16(b.data()+22)!=4||get16(b.data()+24)!=131||get32(b.data()+28)!=j.sourceCrc||sourceCrc(b)!=j.sourceCrc||get32(b.data()+981)!=crc(b.data(),981))return false;
 for(unsigned i=0;i<6;++i)if(b[12+i]!=j.mac[i])return false;
 for(unsigned i=5;i<8;++i)if(b[i])return false;
 for(unsigned i=26;i<28;++i)if(b[i])return false;
 for(unsigned i=32;i<64;++i)if(b[i])return false;
 for(size_t i=0;i<131;++i){const auto& f=mosaico_gauge_recovery::layout[i];const auto* p=b.data()+64+i*7;if(get16(p)!=f.address||p[2]!=f.width)return false;for(unsigned k=f.width;k<4;++k)if(p[3+k])return false;}
 return true;
}
// Independent bounded flow. Never instantiate the native65/THA1 coordinator.
// Backend owns once-only key/Seal caps and closed transports, model owns order.
template<class B> bool capture(B& b){
 if(!b.admit())return false;
 if(!b.intent(UnsealIntent))return false;
 bool ok=b.unseal();if(ok)ok=b.intent(UnsealVerified|FullIntent)&&b.full();
 if(ok)ok=b.intent(FullVerified)&&b.read131();
 if(ok)ok=b.intent(ReadVerified);
 const bool sealIntent=b.intent(SealIntent);const bool closed=b.close();
 if(closed)b.mark(CloseVerified);
 ok=ok&&sealIntent&&closed&&b.final()&&b.backup();
 if(ok)b.mark(BackupVerified);
 return b.finish(ok)&&ok;
}
}
