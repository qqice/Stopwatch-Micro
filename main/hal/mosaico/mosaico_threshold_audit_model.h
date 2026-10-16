#pragma once
#include <cstdint>
#include <cstddef>

namespace mosaico_threshold {
enum class Type:uint8_t {U1,H1,I2,U2,H2};
enum class Selector:uint16_t {
 Dc=0x929F,Fcc=0x929D,Config=0x929B,BatteryId=0x929A,Dsg=0x9228,Chg=0x922A,Quit=0x922C,Sleep=0x9217,
 ChargeV=0x91FD,ChargeI=0x91FB,TaperI=0x9201,TaperV=0x92A5,SysSetV=0x9240,SysSetTime=0x9242,SysClearV=0x9243,
 Edv0=0x92B4,Edv1=0x92B7,Edv2=0x92BA,BatteryLow=0x9251,NearFull=0x926B,Reserve=0x926D,
 FlagA=0x927F,FlagB=0x9281,DsgRelax=0x922E,ChgRelax=0x9230,QuitRelax=0x9231
};
struct Field {Selector selector;Type type;const char* name;};
constexpr Field fields[]={
 {Selector::Dc,Type::I2,"dc_i2"},{Selector::Fcc,Type::I2,"fcc_i2"},{Selector::Config,Type::H2,"cfg_h2"},
 {Selector::BatteryId,Type::U1,"bid_u1"},{Selector::Dsg,Type::I2,"dsg_i2"},{Selector::Chg,Type::I2,"chg_i2"},
 {Selector::Quit,Type::I2,"quit_i2"},{Selector::Sleep,Type::I2,"sleep_i2"},{Selector::ChargeV,Type::I2,"cv_i2"},
 {Selector::ChargeI,Type::I2,"ci_i2"},{Selector::TaperI,Type::I2,"ti_i2"},{Selector::TaperV,Type::I2,"tv_i2"},
 {Selector::SysSetV,Type::I2,"sysv_i2"},{Selector::SysSetTime,Type::U1,"syst_u1"},{Selector::SysClearV,Type::I2,"sysclr_i2"},
 {Selector::Edv0,Type::I2,"edv0_i2"},{Selector::Edv1,Type::I2,"edv1_i2"},{Selector::Edv2,Type::I2,"edv2_i2"},
 {Selector::BatteryLow,Type::U2,"low_u2"},{Selector::NearFull,Type::I2,"near_i2"},{Selector::Reserve,Type::I2,"reserve_i2"},
 {Selector::FlagA,Type::H2,"fa_h2"},{Selector::FlagB,Type::H1,"fb_h1"},{Selector::DsgRelax,Type::U2,"dr_u2"},
 {Selector::ChgRelax,Type::U1,"cr_u1"},{Selector::QuitRelax,Type::U1,"qr_u1"}};
constexpr size_t fieldCount=sizeof(fields)/sizeof(fields[0]);
constexpr bool allowed(Selector s){for(const auto& f:fields)if(f.selector==s)return true;return false;}
constexpr int32_t decoded(Type t,uint16_t raw){
 return t==Type::U1||t==Type::H1?raw>>8:(t==Type::I2?static_cast<int16_t>(raw):raw);
}
struct Values {uint16_t raw[fieldCount]{};uint8_t readable=0;bool anchors=false;};
// Reader MUST authenticate exact length/checksum/exclusive requested selector.
// U1/H1 consume first payload byte; two-byte DM words are big-endian.
template<class Reader> constexpr bool readStable(Reader read,uint16_t expectedConfig,Values& values) {
 Values fresh{};
 for(size_t i=0;i<fieldCount;++i){
  uint8_t a[36]{},b[36]{};
  if(!read(fields[i].selector,a)||!read(fields[i].selector,b)){values=fresh;return false;}
  const uint16_t first=(uint16_t(a[2])<<8)|a[3],second=(uint16_t(b[2])<<8)|b[3];
  if(decoded(fields[i].type,first)!=decoded(fields[i].type,second)){values=fresh;return false;}
  fresh.raw[i]=second;++fresh.readable;
  if(i==2){fresh.anchors=fresh.raw[0]==65&&fresh.raw[1]==65&&fresh.raw[2]==expectedConfig&&expectedConfig<=0x1fff;
   if(!fresh.anchors){values=fresh;return false;}}
 }
 values=fresh;return true;
}
constexpr uint32_t crc32(const uint8_t* p,size_t n){uint32_t c=0xffffffffU;for(size_t i=0;i<n;++i){c^=p[i];for(unsigned j=0;j<8;++j)c=(c>>1)^((c&1)?0xedb88320U:0);}return ~c;}
constexpr uint32_t valuesCrc(const Values& v){uint8_t b[fieldCount*2]{};for(size_t i=0;i<fieldCount;++i){b[i*2]=v.raw[i]>>8;b[i*2+1]=v.raw[i];}return crc32(b,sizeof(b));}
enum class State:uint8_t {Pending=1,Completed=2,Failed=3};
enum class Load:uint8_t {Absent,Present,Invalid};
enum Flag:uint16_t {UnsealIntent=1,FullIntent=2,SealIntent=4,UnsealVerified=8,FullVerified=16,ReadVerified=32,CloseVerified=64};
struct Journal {State state=State::Pending;uint16_t flags=0;uint32_t generation=0;uint8_t mac[6]{};uint8_t readCount=0;uint32_t snapshotCrc=0;};
using Record=uint8_t[48];
constexpr void put32(uint8_t* p,uint32_t v){for(unsigned i=0;i<4;++i)p[i]=v>>(8*i);}
constexpr uint32_t get32(const uint8_t* p){uint32_t v=0;for(unsigned i=0;i<4;++i)v|=uint32_t(p[i])<<(8*i);return v;}
constexpr bool shape(const Journal& j){return uint8_t(j.state)>=1&&uint8_t(j.state)<=3&&!(j.flags&~127)&&j.generation&&j.readCount<=fieldCount&&
 (j.state!=State::Completed||(j.flags==127&&j.readCount==fieldCount));}
constexpr bool encode(const Journal& j,Record& r){
 if(!shape(j))return false;
 for(auto& b:r)b=0;
 r[0]='T';r[1]='H';r[2]='A';r[3]='1';r[4]=1;r[5]=uint8_t(j.state);r[6]=j.flags;r[7]=j.flags>>8;put32(r+8,j.generation);
 for(unsigned i=0;i<6;++i)r[12+i]=j.mac[i];
 r[18]=0x20;r[19]=2;r[20]=1;r[21]=j.readCount;put32(r+24,j.snapshotCrc);put32(r+44,crc32(r,44));return true;
}
constexpr bool decode(const Record& r,const uint8_t mac[6],Journal& j){
 if(r[0]!='T'||r[1]!='H'||r[2]!='A'||r[3]!='1'||r[4]!=1||r[18]!=0x20||r[19]!=2||r[20]!=1||r[22]||r[23]||get32(r+44)!=crc32(r,44))return false;
 for(unsigned i=28;i<44;++i)if(r[i])return false;
 Journal v{};v.state=State(r[5]);v.flags=uint16_t(r[6])|(uint16_t(r[7])<<8);v.generation=get32(r+8);v.readCount=r[21];v.snapshotCrc=get32(r+24);
 for(unsigned i=0;i<6;++i){if(mac[i]!=r[12+i])return false;v.mac[i]=r[12+i];}
 if(!shape(v))return false;
 j=v;return true;
}
constexpr bool equal(const Record& a,const Record& b){for(unsigned i=0;i<48;++i)if(a[i]!=b[i])return false;return true;}
struct Result {bool success=false,attempted=false,readable=false,closed=false;Journal journal{};};
template<class Backend> class Coordinator {
 Backend& b;Journal j{};Result result{};
 constexpr bool save(){return b.saveReadback(j);}
public:
 constexpr explicit Coordinator(Backend& backend):b(backend){}
 constexpr Result run(){
  Journal old{};const auto loaded=b.load(old);
  if(b.wasAttempted()||loaded==Load::Invalid||(loaded==Load::Present&&old.state!=State::Completed)||
     (loaded==Load::Present&&old.generation==0xffffffffU)||!b.preflight(j.mac))return result;
  if(!b.claimAttempt())return result;
  result.attempted=true;j.generation=loaded==Load::Present?old.generation+1:1;j.flags=UnsealIntent;
  if(!save()){result.journal=j;return result;}
  bool ok=b.unseal();
  if(ok){j.flags|=UnsealVerified|FullIntent;ok=save()&&b.full();}
  if(ok){j.flags|=FullVerified;ok=save()&&b.readThresholds();}
  if(ok){j.flags|=ReadVerified;j.readCount=fieldCount;j.snapshotCrc=b.snapshotCrc();result.readable=true;}
  // Cleanup is mandatory after ANY access attempt, even if journaling fails.
  j.flags|=SealIntent;const bool closingIntent=save();
  result.closed=b.close();if(result.closed)j.flags|=CloseVerified;
  j.state=ok&&closingIntent&&result.closed?State::Completed:State::Failed;
  const bool recorded=save();result.success=j.state==State::Completed&&recorded;result.journal=j;return result;
 }
};
} // namespace mosaico_threshold
