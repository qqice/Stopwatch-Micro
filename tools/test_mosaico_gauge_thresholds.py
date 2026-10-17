"""Closed threshold audit/codec/cleanup tests; no hardware or SDK build."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
COMPILERS=list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
class ThresholdTests(unittest.TestCase):
 def compile(self,code):
  if not COMPILERS:self.skipTest('configured cross compiler unavailable')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'threshold.cpp';p.write_text(code)
   r=subprocess.run([str(COMPILERS[0]),'-std=c++20','-Wall','-Wextra','-Werror','-fsyntax-only','-I',str(ROOT/'main/hal/mosaico'),str(p)],capture_output=True,text=True)
   self.assertEqual(r.returncode,0,r.stderr)
 def test_actual_model_closed_types_codec_and_every_failure(self):
  self.compile(r'''
#include <initializer_list>
#include "mosaico_threshold_audit_model.h"
using namespace mosaico_threshold;
constexpr bool reads(){
 if(fieldCount!=59||allowed(static_cast<Selector>(0x0000))||allowed(static_cast<Selector>(0x9184)))return false;
 constexpr uint16_t addresses[]={0x92a7,0x92a9,0x92ab,0x92ad,0x92af,0x92b1,0x92b2,0x92b3,
  0x92bd,0x92bf,0x92c1,0x92c3,0x92c5,0x92c7,0x92c9,0x92cb,0x92cd,0x92cf,0x92d1,
  0x9271,0x9272,0x9274,0x926f,0x9270,0x9268,0x9269,
  0x92a3,0x92b6,0x92b9,0x92bc,0x9276,0x927b,0x927c};
 for(size_t i=0;i<33;++i)if(uint16_t(fields[i+26].selector)!=addresses[i])return false;
 constexpr Type extraTypes[]={Type::I2,Type::U1,Type::U1,Type::U1,Type::I2,Type::U1,Type::U1};
 for(size_t i=0;i<7;++i)if(fields[i+52].type!=extraTypes[i])return false;
 for(uint16_t hole:{0x9253,0x9255,0x9257,0x9259})if(allowed(Selector(hole)))return false;
 for(size_t i=0;i<fieldCount;++i)for(size_t j=i+1;j<fieldCount;++j)if(fields[i].selector==fields[j].selector)return false;
 if(decoded(Type::I2,0xff9c)!=-100||decoded(Type::U2,0xff9c)!=65436||decoded(Type::U1,0x03ff)!=3||decoded(Type::H1,0x03aa)!=3)return false;
 for(int mode=0;mode<4;++mode){int calls=0;Values v{};
  bool ok=readStable([&](Selector s,uint8_t* b)constexpr{
   int i=calls++/2;if(s!=fields[i].selector)return false;
   uint16_t raw=i<2?65:(i==2?0x112a:0xff9c);
   if(mode==1&&i==0)raw=0x4100;
   if(mode==2&&i==6&&calls%2==0)++raw;
   if(mode==3&&i==7)return false;
   b[2]=raw>>8;b[3]=raw;return true;
  },0x112a,v);
  if(ok!=(mode==0))return false;
  if(ok&&(calls!=118||v.readable!=59||!v.anchors))return false;
  if(mode==1&&(calls!=6||v.anchors||v.readable!=3))return false;
 }
 Journal j{};j.mac[0]=42;j.generation=1;j.flags=UnsealIntent;Record r{};Journal out{};
 if(!encode(j,r)||!decode(r,j.mac,out))return false;
 for(unsigned i=0;i<48;++i){r[i]^=1;if(decode(r,j.mac,out))return false;r[i]^=1;}
 uint8_t wrong[6]={43};if(decode(r,wrong,out))return false;
 j.state=State::Completed;if(encode(j,r))return false;
 // Legacy completed snapshots retain their historical 26-field CRC/count, never relabeled.
 j.schema=1;j.flags=127;j.readCount=26;j.snapshotCrc=77;
 if(!encode(j,r)||r[4]!=1||r[20]!=1||!decode(r,j.mac,out)||out.schema!=1||out.readCount!=26||out.snapshotCrc!=77)return false;
 j.readCount=59;if(encode(j,r))return false;
 j.schema=2;j.readCount=26;if(encode(j,r))return false;
 j.schema=3;if(encode(j,r))return false;
 j.schema=2;
 j.flags=127;j.readCount=fieldCount;j.snapshotCrc=42;
 if(!encode(j,r)||!decode(r,j.mac,out)||out.flags!=127||out.readCount!=59||out.snapshotCrc!=42)return false;
 return true;
}
struct Fake{
 int step=0,fail=0,unseals=0,fulls=0,closes=0,reads=0;bool durable=false,attempted=false,wrong=false,loseAck=false,sticky=false,broken=false;
 Journal disk{};
 constexpr bool tick(){bool ok=++step!=fail;if(!ok&&sticky)broken=true;return ok;}
 constexpr Load load(Journal& j){if(!tick())return Load::Invalid;j=disk;return durable?Load::Present:Load::Absent;}
 constexpr bool wasAttempted(){return attempted;}
 constexpr bool claimAttempt(){if(attempted)return false;attempted=true;return true;}
 constexpr bool preflight(uint8_t mac[6]){mac[0]=42;return tick();}
 constexpr bool saveReadback(const Journal& j){bool ok=tick()&&!broken;if(ok||loseAck){disk=j;durable=true;}return ok;}
 constexpr bool authorized(unsigned bit){if(!durable||!(disk.flags&bit)||disk.mac[0]!=42)wrong=true;return tick();}
 constexpr bool unseal(){++unseals;return authorized(UnsealIntent);}
 constexpr bool full(){++fulls;return authorized(FullIntent);}
 constexpr bool readThresholds(){++reads;return tick();}
 constexpr uint32_t snapshotCrc(){return 42;}
 constexpr bool close(){++closes;return tick();}
};
constexpr Result run(Fake& f){Coordinator<Fake> c(f);return c.run();}
constexpr bool faults(){
 Fake good;auto result=run(good);
 if(!result.success||!result.closed||!result.readable||good.disk.state!=State::Completed||good.disk.flags!=127||good.unseals!=1||good.fulls!=1||good.closes!=1||good.wrong)return false;
 for(int point=1;point<=good.step;++point)for(int behavior=0;behavior<3;++behavior){
  Fake f;f.fail=point;f.loseAck=behavior==1;f.sticky=behavior==2;auto failed=run(f);
  if(failed.success||f.wrong||f.unseals>1||f.fulls>1||f.closes!=(f.unseals?1:0))return false;
  if(f.durable&&f.disk.state!=State::Completed){f.attempted=false;f.fail=0;int old=f.unseals;if(run(f).success||f.unseals!=old)return false;}
 }
 int old=good.unseals;if(run(good).success||good.unseals!=old)return false;
 good.attempted=false;good.fail=0;good.disk.schema=1;good.disk.readCount=26;
 if(!run(good).success||good.disk.generation!=2||good.disk.schema!=2||good.disk.readCount!=59)return false;
 for(auto state:{State::Pending,State::Failed}){Fake f;f.durable=true;f.disk=good.disk;f.disk.state=state;
  if(run(f).attempted||f.unseals)return false;}
 return true;
}
static_assert(reads(),"exact closed fields, U1/signed BE2 anchors, full CRC/MAC codec");
static_assert(faults(),"all boundaries ACK/NVS failures, no replay, one key attempt, unconditional cleanup");
''')
 def test_actual_cached_field_bounds_no_io(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
  getter='constexpr bool field('+source.split('bool Hal::gaugeThresholdAuditField(',1)[1].split('// Closed standard-command snapshot',1)[0]
  getter='\n'.join(line for line in getter.splitlines() if 'std::lock_guard<' not in line).replace('std::snprintf','print')
  self.compile(r'''
#include "mosaico_threshold_audit_model.h"
struct Fake{
 mosaico_threshold::Values gauge_threshold_cached{};uint32_t gauge_threshold_cached_generation=0;int prints=0;
 template<class... Args>constexpr int print(char*,size_t,const char*,Args...){++prints;return 100;}
'''+getter+r'''
};
constexpr bool cases(){
 Fake f;char out[160]{};
 if(f.field(0,nullptr,sizeof(out))||f.prints||f.field(0,out,159)||f.prints)return false;
 if(f.field(0,out,sizeof(out))||f.prints!=1)return false;
 f.gauge_threshold_cached.readable=59;f.gauge_threshold_cached_generation=2;
 for(size_t i=0;i<59;++i)if(!f.field(i,out,sizeof(out)))return false;
 if(f.field(59,out,sizeof(out))||f.field(size_t(-1),out,sizeof(out)))return false;
 f.gauge_threshold_cached.readable=58;
 if(f.field(0,out,sizeof(out)))return false;
 return true;
}
static_assert(cases(),"actual cache getter: complete generation only, bounds, buffer minimum, no I2C/NVS");
''')
 def test_actual_mac_reader_authentication(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
  reader='constexpr bool gauge_threshold_read('+source.split('bool gauge_threshold_read(',1)[1].split('// Explicit runtime backend',1)[0]
  model=(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').read_text()
  auth='inline uint8_t macChecksum('+model.split('inline uint8_t macChecksum(',1)[1].split('inline bool validJournal(',1)[0]
  auth=auth.replace('inline ','constexpr ')
  self.compile(r'''
#include "mosaico_threshold_audit_model.h"
namespace mosaico_gauge{
'''+auth+r'''
}
constexpr int ESP_OK=0;
struct Fake{
 int mode=0,writes=0,reads=0;bool gauge=true,wrong=false;uint8_t response[36]{};
 constexpr void gauge_feed(){}
 constexpr void gauge_delay_ms(uint32_t ms){if(ms!=20)wrong=true;}
 constexpr int i2c_master_transmit(bool,const uint8_t* p,size_t n,int timeout){
  ++writes;if(n!=3||p[0]!=0x3e||timeout!=20)wrong=true;
  response[0]=p[1];response[1]=p[2];response[2]=0;response[3]=65;response[35]=36;
  response[34]=mosaico_gauge::macChecksum(response,34);
  if(mode==0)response[0]=response[1]=0xff;
  if(mode==1)++response[34];
  if(mode==2)response[35]=6;
  if(mode==3)++response[0];
  return mode==4?2:0;
 }
 constexpr bool gauge_read(uint8_t reg,uint8_t* out,size_t n){++reads;if(n!=2)wrong=true;unsigned offset=reg-0x3e;for(unsigned i=0;i<n;++i)out[i]=response[offset+i];return true;}
'''+reader+r'''
};
constexpr bool cases(){
 for(int mode=0;mode<=5;++mode){Fake f;f.mode=mode;uint8_t block[36]{};
  bool ok=f.gauge_threshold_read(mosaico_threshold::Selector::Dc,block);
  if(ok!=(mode==0||mode==5)||f.wrong||f.writes!=1)return false;
 }
 Fake denied;uint8_t b[36]{};if(denied.gauge_threshold_read(static_cast<mosaico_threshold::Selector>(0x9184),b)||denied.writes||denied.reads)return false;
 return true;
}
static_assert(cases(),"actual selector ACK plus strict full length/checksum/echo, bounded FF FF quirk only");
''')
 def test_actual_guarded_close_never_seals_cfg_unknown(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
  close='constexpr bool close() {'+source.split('struct GaugeThresholdBackend {',1)[1].split('    bool close() {',1)[1].split('\n};\n\n} // namespace',1)[0]
  self.compile(r'''
#include <cstdint>
enum class AccessWord{Seal};
struct Fake{
 int mode=0,reads=0,seals=0,delays=0,controls=0;bool wrong=false,afterStandardValid=false;
 struct Standard{uint16_t op=0,dc=0,fcc=0;}after;
 constexpr bool reject(const char*){return false;}
 constexpr bool gauge_word(uint8_t reg,uint16_t& op){
  ++reads;if(reg!=0x3a)wrong=true;op=mode==0?0xa6:0xa2;
  if(mode==2||(mode==6&&reads==2))op|=0x400;
  if(mode==3||(mode==7&&reads==2))return false;
  if(mode==4)op|=1;
  if(mode==5)op=0xa0;
  if(mode==8&&reads==2)op=0xa6;
  return true;
 }
 constexpr void gauge_delay_ms(uint32_t ms){if(ms!=5000)wrong=true;++delays;}
 constexpr bool gauge_access_word(AccessWord){++seals;return mode!=10;}
 constexpr bool gauge_wait_security(uint8_t sec){return sec==3;}
 constexpr bool standard(Standard& s){s={0xa6,65,uint16_t(mode==9?100:65)};return true;}
 constexpr bool gauge_identity(){return true;}
 constexpr void readControl(){++controls;}
'''+close+r'''
};
constexpr bool cases(){
 for(int mode=0;mode<=10;++mode){Fake f;f.mode=mode;bool ok=f.close();
  if(f.wrong)return false;
  if(mode>=2&&mode<=7){if(ok||f.seals)return false;}
  else if(mode==9){if(ok||f.seals!=1)return false;}
  else if(!ok||f.seals!=(mode==0||mode==8?0:1))return false;
 }
 return true;
}
static_assert(cases(),"actual cleanup guarded before/after grace, no CFG/CAL/unknown seal, no key retries");
''')
 def test_actual_admission_capture_and_standard_batch_pacing(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text().split('struct GaugeThresholdBackend {',1)[1]
  methods='    bool standard(Standard& s) {'+source.split('    bool standard(Standard& s) {',1)[1].split('    void readControl()',1)[0]
  preflight='    bool preflight(uint8_t outMac[6]) {'+source.split('    bool preflight(uint8_t outMac[6]) {',1)[1].split('    bool unseal()',1)[0]
  methods=(methods+preflight).replace('    bool standard(', '    constexpr bool standard(').replace('    bool physical(', '    constexpr bool physical(').replace('    bool preflight(', '    constexpr bool preflight(')
  self.compile(r'''
#include <array>
#include "mosaico_gauge_model.h"
#include "mosaico_fcc_limit_journal_codec.h"
constexpr int ESP_OK=0;
struct GaugeFccBackend{
 constexpr mosaico_fcc_limit::Load loadRaw(const char*,mosaico_fcc_limit::Journal& j){
  using namespace mosaico_fcc_limit;j.state=State::Verified;j.backupValid=j.fullAccessVerified=true;
  j.before={0x102a,65,100};j.target={0x112a,65,65};j.intents=Unseal|Full|Enter|Config|Fcc|TargetVerified|Exit|Seal;
  return Load::Present;
 }
};
struct Fake {
 struct Standard{uint16_t op=0,soc=0,cycles=0,mv=0,temp=0,dc=0,fcc=0;int16_t current=0,average=0;}before;
 bool gauge=true,standardSeen=false,beforeStandardValid=false,wrong=false;
 int64_t now=0,lastStandardUs=0,previousCompleted=-1;int reads=0,waits=0,ids=0,controls=0;uint16_t expectedConfig=0;
 constexpr Fake& GetHAL(){return *this;}
 constexpr std::array<uint8_t,6> getFactoryMac(){return {42,0,0,0,0,0};}
 constexpr bool runtimeGate(){return true;}
 constexpr bool reject(const char*){return false;}
 constexpr int64_t esp_timer_get_time()const{return now;}
 constexpr void gauge_delay_ms(uint32_t ms){++waits;now+=int64_t(ms)*1000;}
 constexpr bool gauge_word(uint8_t reg,uint16_t& value){
  if(reads%9==0&&previousCompleted>=0&&now-previousCompleted<1000000)wrong=true;
  ++reads;now+=1000;if(reads%9==0)previousCompleted=now;
  switch(reg){case 0x3a:value=0xa6;break;case 0x2c:value=4;break;case 0x2a:value=11;break;case 0x08:value=3803;break;
   case 0x06:value=2982;break;case 0x3c:case 0x12:value=65;break;case 0x0c:case 0x14:value=70;break;default:return false;}
  return true;
 }
 constexpr bool gauge_identity(){++ids;return true;}
 constexpr void readControl(){++controls;}
 constexpr int gauge_load_journal(mosaico_gauge::Journal& j,const uint8_t*){
  using namespace mosaico_gauge;j.action=1;j.state=uint8_t(State::VerifiedSealedPrior3);
  j.targetDesign=j.targetFcc=j.lastDesign=j.lastFcc=65;return ESP_OK;
 }
 constexpr int gauge_load_access(mosaico_gauge::AccessJournal& a,const uint8_t*){
  using namespace mosaico_gauge;a.state=uint8_t(AccessState::Restored);a.priorSecurity=a.lastSecurity=3;
  a.unsealAttempted=a.unsealVerified=a.fullAttempted=a.fullVerified=1;return ESP_OK;
 }
'''+methods+r'''
};
constexpr bool cases(){
 Fake f;uint8_t mac[6]{};if(!f.preflight(mac)||f.reads!=9||f.waits||f.ids!=1||f.controls!=1||mac[0]!=42||!f.beforeStandardValid||f.before.mv!=3803||f.expectedConfig!=0x112a)return false;
 Fake::Standard after{};
 if(!f.standard(after)||f.reads!=18||f.waits!=1||f.wrong)return false;
 if(!f.standard(after)||f.reads!=27||f.waits!=2||f.wrong)return false;
 f.now=f.lastStandardUs-1;int old=f.reads;if(f.standard(after)||f.reads!=old)return false;
 return true;
}
static_assert(cases(),"actual preflight captures exactly one full batch, remaining closing/default batches spaced >=1s");
''')
 def test_actual_nvs_commit_readback_faults(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text().split('struct GaugeThresholdBackend {',1)[1]
  methods='    Load load('+source.split('    Load load(',1)[1].split('    bool preflight(',1)[0]
  methods=methods.replace('    Load ', '    constexpr Load ').replace('    bool ', '    constexpr bool ')
  self.compile(r'''
#include <array>
#include "mosaico_threshold_audit_model.h"
using namespace mosaico_threshold;
using nvs_handle_t=int;using esp_err_t=int;constexpr int ESP_OK=0,ESP_ERR_NVS_NOT_FOUND=1,NVS_READONLY=0,NVS_READWRITE=1;
struct Fake{
 using Load=mosaico_threshold::Load;using Journal=mosaico_threshold::Journal;
 static constexpr const char* Key="thresh_aud_v1";
 int fault=0,commits=0;bool persisted=false,journalLoaded=false;Load journalLoadState=Load::Absent;uint8_t lastReadJournalState=0;
 Record disk{};const char* why="start";
 constexpr Fake& GetHAL(){return *this;}
 constexpr std::array<uint8_t,6> getFactoryMac(){return {42,0,0,0,0,0};}
 constexpr bool reject(const char*){return false;}
 constexpr void gauge_feed(){}
 constexpr int nvs_open(const char*,int access,int* h){*h=1;return fault==1||(fault==8&&access==NVS_READONLY)?2:0;}
 constexpr int nvs_set_blob(int,const char*,const Record& r,size_t){if(fault==2)return 2;for(unsigned i=0;i<48;++i)disk[i]=r[i];return 0;}
 constexpr int nvs_commit(int){++commits;if(fault==3)return 2;persisted=true;return fault==11?2:0;}
 constexpr void nvs_close(int){}
 constexpr int nvs_get_blob(int,const char*,Record& r,size_t* n){
  if(!persisted||fault==4)return ESP_ERR_NVS_NOT_FOUND;
  for(unsigned i=0;i<48;++i)r[i]=disk[i];
  if(fault==5)r[24]^=1;
  if(fault==6)*n=47;
  if(fault==7){Journal changed{};uint8_t mac[6]={42};if(!decode(r,mac,changed))return 2;changed.flags|=FullIntent;if(!encode(changed,r))return 2;}
  if(fault==9||fault==10){if(fault==9)r[12]^=1;else r[4]=3;put32(r+44,crc32(r,44));}
  return 0;
 }
'''+methods+r'''
};
constexpr bool cases(){
 for(int fault=0;fault<=11;++fault){Fake f;f.fault=fault;Journal j{};j.mac[0]=42;j.generation=1;j.flags=UnsealIntent;
  bool ok=f.saveReadback(j);if(ok!=(fault==0))return false;
  if(ok&&(f.commits!=1||!f.persisted||f.lastReadJournalState!=uint8_t(State::Pending)))return false;
 }
 Fake f;Journal j{};if(f.load(j)!=Load::Absent)return false;j.mac[0]=42;j.generation=1;j.flags=UnsealIntent;
 if(!f.saveReadback(j)||f.load(j)!=Load::Present||j.state!=State::Pending)return false;
 f.disk[0]^=1;if(f.load(j)!=Load::Invalid)return false;
 return true;
}
static_assert(cases(),"actual separate journal commit/reopen CRC/MAC/schema/bytes and ambiguous ACK faults");
''')
 def test_actual_threshold_backend_public_method_compile_and_boundaries(self):
  source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
  backend='struct GaugeThresholdBackend {'+source.split('struct GaugeThresholdBackend {',1)[1].split('\n} // namespace',1)[0]
  method='bool Hal::gaugeThresholdAudit('+source.split('bool Hal::gaugeThresholdAudit(',1)[1].split('// Closed standard-command snapshot',1)[0]
  self.compile(r'''
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <array>
#include "mosaico_threshold_audit_model.h"
#include "mosaico_gauge_model.h"
#include "mosaico_fcc_limit_journal_codec.h"
namespace std{struct mutex{};struct recursive_mutex{};template<class T>struct lock_guard{lock_guard(T&){}};}
std::mutex battery_mutex;std::recursive_mutex gauge_transaction_mutex;
using esp_err_t=int;using nvs_handle_t=int;
constexpr int ESP_OK=0,ESP_ERR_NVS_NOT_FOUND=1,NVS_READONLY=0,NVS_READWRITE=1;
int nvs_open(const char*,int,int*);int nvs_get_blob(int,const char*,void*,size_t*);void nvs_close(int);int nvs_set_blob(int,const char*,const void*,size_t);int nvs_commit(int);
mosaico_threshold::Values gauge_threshold_cached{};uint32_t gauge_threshold_cached_generation=0;
bool gauge,gauge_threshold_audit_attempted;bool gauge_word(uint8_t,uint16_t&);bool gauge_identity();
struct Power{bool locked,wifiRunning;int phase;};struct Network{Power powerStats();};Network& GetNetworkQuota();
struct BleStats{bool advertising,connected;};struct Ble{BleStats diagnostics();};Ble& GetCodexMicroBle();
namespace MosaicoOta{bool busy();bool healthPending();}
void gauge_feed();void gauge_delay_ms(uint32_t);int64_t esp_timer_get_time();
enum class AccessWord{UnsealFirst,UnsealSecond,Full,Seal};bool gauge_access_word(AccessWord);bool gauge_wait_security(uint8_t);
struct Hal{std::array<uint8_t,6> getFactoryMac();bool gaugeThresholdAudit(bool,char*,size_t);bool gaugeThresholdAuditField(size_t,char*,size_t);};Hal& GetHAL();
int gauge_load_journal(mosaico_gauge::Journal&,const uint8_t*);int gauge_load_access(mosaico_gauge::AccessJournal&,const uint8_t*);
struct GaugeFccBackend{mosaico_fcc_limit::Load loadRaw(const char*,mosaico_fcc_limit::Journal&);};
bool gauge_threshold_read(mosaico_threshold::Selector,uint8_t*);
'''+backend+method)
  for forbidden in ('gauge_control(', 'gauge_exit_config(', 'gauge_write_field(', 'gaugeSetNominalCapacity(', 'gaugeOcvMeasure(', 'sample_battery_locked('):self.assertNotIn(forbidden,backend+method)
  self.assertEqual(backend.count('gauge_delay_ms(5000)'),3)
  self.assertIn('"gaugeaudit"',backend);self.assertIn('"thresh_aud_v1"',backend)
  self.assertIn('bootHistoryEligible',backend);self.assertIn('verifiedRuntimePermission',backend)
  readonly=method.split('else if(gauge)',1)[1]
  self.assertNotIn('saveReadback(',readonly);self.assertNotIn('gauge_access_word(',readonly)
  getter=method.split('bool Hal::gaugeThresholdAuditField(',1)[1]
  for forbidden in ('gauge_word(', 'gauge_threshold_read(', 'nvs_', 'saveReadback(', 'gauge_access_word('):self.assertNotIn(forbidden,getter)
  self.assertIn('if(result.success){gauge_threshold_cached=backend.values;',method)
  self.assertIn('gauge_threshold_cached={};gauge_threshold_cached_generation=0;',method)
  serial=(ROOT/'main/debug/serial_debug.cpp').read_text()
  cmd=serial.split('if (std::strcmp(command,"gauge-thresholds")==0)',1)[1].split('if (std::strcmp(command, "gauge-ocv")',1)[0]
  self.assertIn('privileged&&_writer&&_writer->_reply_uart',cmd)
  trace=serial.split('if (std::strcmp(command,"gauge-trace")==0)',1)[1].split('if (std::strcmp(command,"gauge-thresholds")',1)[0]
  for token in ('armConfirm()','clearConfirm()','exportRows(','status(','queued=1 persisted=0 check_status_pending0=1'):self.assertIn(token,trace)
if __name__=='__main__':unittest.main()
