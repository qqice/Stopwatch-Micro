"""Device-free codec execution and actual runtime backend syntax/boundary tests."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
COMPILERS=list(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))

class BackendTests(unittest.TestCase):
    def compile(self,code,extra=()):
        if not COMPILERS:self.skipTest('configured cross compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'test.cpp';p.write_text(code)
            r=subprocess.run([str(COMPILERS[0]),'-std=c++17','-Werror=format','-Werror=return-type','-fsyntax-only','-I',str(ROOT/'main/hal/mosaico'),str(p)]+list(extra),capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)

    def test_canonical_codec_actual_static_execution(self):
        self.compile(r'''
#include "mosaico_fcc_limit_journal_codec.h"
#include "mosaico_gauge_model.h"
using namespace mosaico_fcc_limit;
constexpr bool cases(){
 Journal j{};j.mac[0]=42;Record r{};Journal out{};
 if(!encode(j,r)||!decode(r,j.mac,out))return false;
 j.fullAccessVerified=true;j.backupValid=true;j.before={0x102a,65,100};j.target={0x112a,65,65};
 j.intents=Unseal|Full|Enter|Config|Fcc|TargetVerified|Exit|Seal;j.state=State::Verified;
 if(!encode(j,r)||!decode(r,j.mac,out)||!same(out.before,j.before)||!same(out.target,j.target)||out.intents!=j.intents||!out.fullAccessVerified||!out.backupValid)return false;
 Record round{};if(!encode(out,round)||!equalRecord(r,round))return false;
 for(unsigned i=0;i<48;++i){r[i]^=1;if(decode(r,j.mac,out))return false;r[i]^=1;}
 uint8_t wrong[6]={43};if(decode(r,wrong,out))return false;
 if(!verifiedRuntimePermission(j))return false;
 j.intents|=RestoreFcc;if(verifiedRuntimePermission(j))return false;j.intents&=~RestoreFcc;
 j.before.dc=3000;j.before.fcc=3000;j.intents|=Dc;
 if(encode(j,r)||!encode(j,r,Mode::ColdFactory)||decode(r,j.mac,out)||!decode(r,j.mac,out,Mode::ColdFactory))return false;
 if(verifiedRuntimePermission(j))return false;
 j.target.config=0x112b;if(encode(j,r,Mode::ColdFactory))return false;
 return true;
}
static_assert(cases(),"canonical CRC, complete fields, corruption and MAC binding");
static_assert(mosaico_gauge::MaxReasonableFcc==78,"legacy guard unchanged");
static_assert(!mosaico_gauge::reasonableFcc(100),"legacy 100 rejected");
''')

    def test_actual_backend_and_public_method_compile(self):
        source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        backend='struct GaugeFccBackend {'+source.split('struct GaugeFccBackend {',1)[1].split('\n} // namespace',1)[0]
        status='bool Hal::gaugeFccLimitStatus('+source.split('bool Hal::gaugeFccLimitStatus(',1)[1].split('\nbool Hal::gaugeFccLimitRepair(',1)[0]
        method='bool Hal::gaugeFccLimitRepair('+source.split('bool Hal::gaugeFccLimitRepair(',1)[1].split('\nbool Hal::gaugeReconcileNominal(',1)[0]
        hook='    // A permanent, authenticated runtime repair profile replaces'+source.split('    // A permanent, authenticated runtime repair profile replaces',1)[1].split('    // Permission begins only AFTER',1)[0]
        self.compile(r'''
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <cstdio>
#include <array>
#include <algorithm>
#include "mosaico_gauge_model.h"
#include "mosaico_fcc_limit_journal_codec.h"
struct esp_mutex{};namespace std {struct mutex{};struct recursive_mutex{};template<class T>struct lock_guard{lock_guard(T&){}};}
std::mutex battery_mutex;std::recursive_mutex gauge_transaction_mutex;
using esp_err_t=int;using nvs_handle_t=int;
constexpr int ESP_OK=0,ESP_ERR_NVS_NOT_FOUND=1,NVS_READONLY=0,NVS_READWRITE=1;
esp_err_t nvs_open(const char*,int,nvs_handle_t*);esp_err_t nvs_get_blob(nvs_handle_t,const char*,void*,size_t*);
void nvs_close(nvs_handle_t);esp_err_t nvs_set_blob(nvs_handle_t,const char*,const void*,size_t);esp_err_t nvs_commit(nvs_handle_t);
struct Hal{enum class GaugeBootReloadStatus{Applied,Critical};struct GaugeBootReloadInfo{bool attempted=false;};std::array<uint8_t,6> getFactoryMac();bool gaugeFccLimitRepair(char*,size_t);bool gaugeFccLimitStatus(char*,size_t);};
Hal::GaugeBootReloadInfo gauge_boot_info;Hal& GetHAL();
struct Power{bool locked,wifiRunning;int phase;};struct Network{Power powerStats();};Network& GetNetworkQuota();
struct BleStats{bool advertising,connected;};struct Ble{BleStats diagnostics();};Ble& GetCodexMicroBle();
namespace MosaicoOta{enum class UiStage{BootChecking};struct UiSnapshot{UiStage stage;};bool copyUiSnapshot(UiSnapshot&);bool busy();bool healthPending();}
int gauge;
int64_t esp_timer_get_time();void gauge_feed();void gauge_delay_ms(uint32_t);bool gauge_word(uint8_t,uint16_t&);bool gauge_identity();
bool gauge_mac_read(uint16_t,uint8_t*);template<class R>bool gauge_profile_read_stable(R,uint16_t(&)[3],const char*&);
bool gauge_wait_security(uint8_t);bool gauge_wait_config(bool);bool gauge_exit_config();
enum class GaugeCommand{EnterConfig};bool gauge_control(GaugeCommand);
enum class AccessWord{UnsealFirst,UnsealSecond,Full,Seal};bool gauge_access_word(AccessWord);
esp_err_t gauge_load_reload(mosaico_gauge::ReloadJournal&,const uint8_t*);
esp_err_t gauge_load_journal(mosaico_gauge::Journal&,const uint8_t*);
esp_err_t gauge_load_access(mosaico_gauge::AccessJournal&,const uint8_t*);
esp_err_t i2c_master_transmit(int,const uint8_t*,size_t,int);
bool gauge_store_reload(mosaico_gauge::ReloadJournal&,const uint8_t*);
''' +backend+status+method+r'''
Hal::GaugeBootReloadStatus actualColdHook(){
 using namespace mosaico_gauge;using Status=Hal::GaugeBootReloadStatus;
 ReloadJournal latch{};auto mac=GetHAL().getFactoryMac();
 auto finish=[](Status s,const char*){return s;};
'''+hook+'''return Status::Critical;}''')

    def test_actual_protective_reseal_faults(self):
        source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        reseal='constexpr bool reseal() {'+source.split('    bool reseal() {',1)[1].split('    bool verifyTarget(',1)[0]
        self.compile(r'''
#include "mosaico_fcc_limit_repair_model.h"
using namespace mosaico_fcc_limit;
enum class AccessWord{Seal};
struct Fake {
 int mode, reads=0, seals=0, delays=0, waits=0;
 constexpr bool reject(const char*){return false;}
 constexpr bool observedSecurity(Security& s){
  ++reads;s={1,false,false,true};
  if(mode==0)s.sec=3;
  if(mode==1 || (mode==4&&reads==2))s.cfg=true;
  if(mode==2 || (mode==5&&reads==2))return false;
  if(mode==6)s.cal=true;
  if(mode==7)s.initialized=false;
  if(mode==8)s.sec=0;
  if(mode==9)s.sec=2;
  if(mode==10&&reads==2)s.sec=3;
  return true;
 }
 constexpr void gauge_delay_ms(uint32_t ms){if(ms==5000)++delays;}
 constexpr bool gauge_access_word(AccessWord){++seals;return true;}
 constexpr bool gauge_wait_security(uint8_t s){++waits;return s==3;}
'''+reseal+r'''
};
constexpr bool cases(){
 for(int mode=0;mode<=10;++mode){Fake f{mode};bool ok=f.reseal();
  if(mode==0){if(!ok||f.seals||f.delays)return false;}
  else if(mode==3||mode==9){if(!ok||f.seals!=1||f.delays!=1||f.reads!=2||f.waits!=1)return false;}
  else if(mode==10){if(!ok||f.seals||f.delays!=1||f.reads!=2||f.waits)return false;}
  else if(ok||f.seals)return false;
 }
 return true;
}
static_assert(cases(),"no Seal wire for CFG/CAL/unknown/read failure, before and after grace");
''')

    def test_actual_nvs_methods_fault_execution(self):
        source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        methods='    mosaico_fcc_limit::Load loadRaw('+source.split('    mosaico_fcc_limit::Load loadRaw(',1)[1].split('    bool observedSecurity(',1)[0]
        methods=methods.replace('    mosaico_fcc_limit::Load ', '    constexpr mosaico_fcc_limit::Load ').replace('    bool ', '    constexpr bool ')
        self.compile(r'''
#include <array>
#include "mosaico_fcc_limit_journal_codec.h"
namespace std{constexpr int strcmp(const char* a,const char* b){while(*a&&*a==*b){++a;++b;}return *a-*b;}}
using namespace mosaico_fcc_limit;
using esp_err_t=int;using nvs_handle_t=int;
constexpr int ESP_OK=0,ESP_ERR_NVS_NOT_FOUND=1,NVS_READONLY=0,NVS_READWRITE=1;
struct Fake {
 int fault;bool persisted=false;int commits=0,opens=0;Record disk{};
 Mode mode=Mode::Runtime;Journal saved{};bool initialColdLoad=true;const char* why="start";
 constexpr bool reject(const char* whyIn){why=whyIn;return false;}
 constexpr bool cold()const{return false;}
 constexpr const char* key()const{return journalKey;}
 constexpr Fake& GetHAL(){return *this;}
 constexpr std::array<uint8_t,6> getFactoryMac(){return {42,0,0,0,0,0};}
 constexpr void gauge_feed(){}
 constexpr esp_err_t nvs_open(const char*,int access,int* h){++opens;*h=1;if(fault==1||(fault==8&&access==NVS_READONLY))return 2;return 0;}
 constexpr esp_err_t nvs_set_blob(int,const char*,const Record& r,size_t){if(fault==2)return 2;for(unsigned i=0;i<48;++i)disk[i]=r[i];return 0;}
 constexpr esp_err_t nvs_commit(int){++commits;if(fault==3)return 2;persisted=true;if(fault==11)return 2;return 0;}
 constexpr void nvs_close(int){}
 constexpr esp_err_t nvs_get_blob(int,const char*,Record& r,size_t* size){
  if(!persisted||fault==4)return ESP_ERR_NVS_NOT_FOUND;
  for(unsigned i=0;i<48;++i)r[i]=disk[i];
  if(fault==5)r[20]^=1;
  if(fault==6)*size=47;
  if(fault==7){ // authenticated, correctly bound but DIFFERENT canonical journal
   Journal changed{};uint8_t mac[6]={42};if(!decode(r,mac,changed))return 2;
   changed.intents|=Full;if(!encode(changed,r))return 2;
  }
  if(fault==9 || fault==10){
   if(fault==9)r[12]^=1;else r[11]=1; // wrong MAC or mode, valid CRC
   uint32_t crc=recordCrc(r,44);for(unsigned i=0;i<4;++i)r[44+i]=crc>>(8*i);
  }
  return ESP_OK;
 }
'''+methods+r'''
};
constexpr bool cases(){
 for(int fault=0;fault<=11;++fault){Fake f{fault};Journal j{};j.mac[0]=42;j.intents=Unseal;
  bool ok=f.journalSaveReadback(journalKey,j);
  if(ok!=(fault==0))return false;
  if(ok&&(f.commits!=1||f.opens!=2||f.saved.intents!=Unseal||f.why[0]!='s'))return false;
 }
 Fake f{0};Journal j{};if(f.journalLoad(journalKey,j)!=Load::Absent)return false;
 j.mac[0]=42;j.intents=Unseal;
 if(!f.journalSaveReadback(journalKey,j)||f.journalLoad(journalKey,j)!=Load::Present)return false;
 f.disk[0]^=1;if(f.journalLoad(journalKey,j)!=Load::Invalid)return false;
 return true;
}
static_assert(cases(),"actual commit/reopen/auth/canonical compare, corrupt present never absent");
''')

    def test_actual_qualification_loop_and_recent_proof(self):
        source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        methods='    bool prepareRuntimeQuietQualification() {'+source.split('    bool prepareRuntimeQuietQualification() {',1)[1].split('    bool identity(',1)[0]
        methods=methods.replace('    bool ', '    constexpr bool ').replace('std::lock_guard<std::mutex>', 'TestLock')
        self.compile(r'''
#include <algorithm>
#include "mosaico_fcc_limit_repair_model.h"
using namespace mosaico_fcc_limit;
struct Mutex{int* held;};
struct TestLock{int* held;constexpr TestLock(Mutex& m):held(m.held){++*held;}constexpr ~TestLock(){--*held;}};
struct Fake {
 int fault=0,held=0,samples=0,waits=0;int64_t now=0;bool coldMode=false,waitWhileLocked=false;
 QuietQualification qualification{};Mutex battery_mutex{&held};
 constexpr bool cold()const{return coldMode;}
 constexpr bool reject(const char*){return false;}
 constexpr Load loadRaw(const char*,Journal&){return fault==2?Load::Present:Load::Absent;}
 constexpr bool identity(Identity& id){id.device=0x0220;id.historicalNominalProof=id.historicalAccessProof=true;return fault!=1;}
 constexpr bool quietSnapshot(Quiet& q){
  ++samples;q={0x0026,58,4200,65,100,2982,0,0};
  if(now>=60000000){
   if(fault==3||fault==8)return false; // read / runtime gate failure
   if(fault==5)q.operation|=0x0400;
   if(fault==6)q.current=4;
   if(fault==7)q.operation=0x0024;
   if(fault==12)q.soc=0;
   if(fault==13)q.mv=4149;
  }
  return true;
 }
 constexpr bool observedSecurity(Security& s){s={3,false,false,true};return !(fault==4&&now>=60000000);}
 constexpr int64_t esp_timer_get_time()const{return now;}
 constexpr void gauge_delay_ms(uint32_t ms){
  if(held)waitWhileLocked=true;
  ++waits;now+=int64_t(ms)*1000;
  if(waits==60){if(fault==9)now-=2000000;if(fault==10)now+=2000000;}
 }
 constexpr bool physical(bool cfg,uint8_t sec){return !cfg&&sec==3&&fault!=14;}
'''+methods+r'''
};
constexpr bool cases(){
 Fake good;if(!good.prepareRuntimeQuietQualification()||good.now!=120000000||good.samples!=121||good.waits!=120||good.waitWhileLocked||good.held)return false;
 if(!good.qualifyRuntimeQuiet())return false;
 good.now=122500001;if(good.qualifyRuntimeQuiet())return false;
 for(int fault:{1,2,3,4,5,6,7,8,9,10,12,13}){
  Fake f;f.fault=fault;if(f.prepareRuntimeQuietQualification()||f.waitWhileLocked||f.held||f.now>62000000)return false;
  if(fault>=3&&!f.qualification.failed)return false;
 }
 Fake fresh;fresh.fault=14;if(!fresh.prepareRuntimeQuietQualification()||fresh.qualifyRuntimeQuiet())return false;
 Fake cold;cold.coldMode=true;if(!cold.prepareRuntimeQuietQualification()||!cold.qualifyRuntimeQuiet()||cold.samples||cold.waits||cold.now)return false;
 return true;
}
static_assert(cases(),"actual 120s qualification, failed reads/state/load/gap/backward time, short sample locks");
''',extra=('-std=c++20',))
        method=source.split('bool Hal::gaugeFccLimitRepair(',1)[1].split('bool Hal::gaugeReconcileNominal(',1)[0]
        self.assertLess(method.index('backend.prepareRuntimeQuietQualification()'),method.index('std::lock_guard<std::mutex> battery'))
        self.assertLess(method.index('std::lock_guard<std::mutex> battery'),method.index('coordinator.run()'))
        prepare=source.split('bool prepareRuntimeQuietQualification()',1)[1].split('bool qualifyRuntimeQuiet()',1)[0]
        for forbidden in ('journalSave', 'gauge_access_word', 'writeParameter', 'gauge_control'):
            self.assertNotIn(forbidden,prepare)

    def test_status_readonly_and_warning_clean_codec(self):
        self.compile('#include "mosaico_fcc_limit_journal_codec.h"\n',extra=('-Wall','-Wextra','-Werror'))
        source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        status=source.split('bool Hal::gaugeFccLimitStatus(',1)[1].split('bool Hal::gaugeFccLimitRepair(',1)[0]
        for forbidden in ('gauge_word(', 'gauge_mac_', 'journalSave', 'nvs_set', 'gauge_access', 'gauge_control'):
            self.assertNotIn(forbidden,status)
        self.assertIn('runtimeReader.loadRaw(journalKey,runtime)',status)
        self.assertIn('coldReader.loadRaw(coldJournalKey,cold)',status)
        self.assertIn('present?static_cast<unsigned>(j.state):0U',status)
        self.assertIn('loaded==Load::Absent?"absent":"invalid"',status)
        self.assertIn('std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex)',status)
        serial=(ROOT/'main/debug/serial_debug.cpp').read_text()
        command=serial.split('if (std::strcmp(command, "gauge-fcc-status") == 0)',1)[1].split('if (std::strcmp(command, "gauge-fcc-limit") == 0)',1)[0]
        self.assertIn('expected=no_arguments readonly=1',command)
        self.assertIn('gaugeFccLimitStatus',command)
        transport=(ROOT/'main/debug/serial_debug_transport.h').read_text()
        self.assertIn('"gauge-fcc-status"',transport)
        self.assertNotIn('"gauge-fcc-limit"',transport)

    def test_closed_source_boundaries(self):
        source=(ROOT/'main/hal/mosaico/hal_mosaico.cpp').read_text()
        b=source.split('struct GaugeFccBackend {',1)[1].split('\n} // namespace',1)[0]
        for forbidden in ('gaugeAccess(', 'gaugeSetNominalCapacity(', 'gauge_store_access(', 'gauge_store_journal(', 'gauge_store_reload(', 'sample_battery_locked('):self.assertNotIn(forbidden,b)
        self.assertIn('enum class Parameter : uint16_t { Config=0x929B,Fcc=0x929D,Dc=0x929F };',b)
        self.assertIn('gauge_delay_ms(5000)',b)
        snapshot=b.split('bool dmSnapshot(',1)[1].split('bool enterCfg()',1)[0]
        self.assertLess(snapshot.index('if(!gauge_profile_read_stable'),snapshot.index('lastObservedDm=dm'))
        self.assertIn('observedValid=true;',snapshot)
        self.assertIn('cold()?writeParameter(Parameter::Dc,value):reject("runtime_dc_write_forbidden")',b)
        hook=source.split('// A permanent, authenticated runtime repair profile replaces',1)[1].split('// Permission begins only AFTER',1)[0]
        self.assertIn('Mode::ColdFactory',hook);self.assertIn('Status::Critical',hook)
        self.assertIn('ReloadState::Failed',hook);self.assertIn('return finish(',hook)
        self.assertIn('verifiedRuntimePermission(permission)',hook)
        self.assertLess(source.index('gauge_boot_info.attempted = true'),source.index('// A permanent, authenticated runtime repair profile replaces'))
        self.assertIn('if(!s.cfg)return s.initialized&&!s.cal',b)
        serial=(ROOT/'main/debug/serial_debug.cpp').read_text()
        cmd=serial.split('if (std::strcmp(command, "gauge-fcc-limit") == 0)',1)[1].split('if (std::strcmp(command, "gauge-profile") == 0)',1)[0]
        self.assertIn('_reply_uart',cmd);self.assertIn('"CONFIRM"',cmd);self.assertIn('"apply"',cmd)
        self.assertNotIn('"gauge-fcc-limit"',(ROOT/'main/debug/serial_debug_transport.h').read_text())
        model=(ROOT/'main/hal/mosaico/mosaico_gauge_model.h').read_text()
        self.assertIn('MaxReasonableFcc = 78;',model)
        self.assertIn('enum class Field : uint16_t { InitialFcc = 0x929D, Design = 0x929F };',model)

if __name__=='__main__':unittest.main()
