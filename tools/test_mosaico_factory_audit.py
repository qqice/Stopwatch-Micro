"""Factory3k production inc, official Unicorn RV32, isolated external bindings only."""
from pathlib import Path
import subprocess,tempfile,unittest,struct,sys,hashlib,os
import test_mosaico_gauge_scale_backend as base
ROOT=base.ROOT
RUN=ROOT/'artifacts/factory-audit-tests'
FIXTURE=r'''
#include <mutex>
#include <cstdio>
#include <cstring>
#include "mosaico_gauge_model.h"
#include "mosaico_gauge_scale_journal_model.h"
#include "mosaico_gauge_engine_model.h"
#include "mosaico_gauge_status_model.h"
#include "mosaico_factory_audit_model.h"
#include "mosaico_native_recovery_model.h"
#include "nvs.h"
#include "esp_ota_ops.h"
namespace scale_recovery=mosaico_gauge_recovery;namespace native_recovery=mosaico_native_recovery;
static native_recovery::Evidence legacyEvidence;
struct Hal{using GaugeEngineSnapshot=mosaico_gauge_engine::Snapshot;std::array<uint8_t,6> getFactoryMac(){return {0x24,1,2,3,4,5};}bool gaugeFactoryAuditCapture(char*,size_t);bool gaugeFactoryAuditStatus(char*,size_t);bool gaugeFactoryAuditField(size_t,char*,size_t);};
static Hal hal;Hal& GetHAL(){return hal;}
static std::recursive_mutex gauge_transaction_mutex;static std::mutex battery_mutex;
static int fault=0,available=139,commits=0,sets=0,reads=0,unsealWords=0,fullWords=0,seals=0,dmReads=0,legacyWrites=0,allocs=0;
static bool gate=true,validApp=true,lease=true,gauge=true,fw=true;
static uint16_t op=0x26,dc=3000,fcc=3000,rm=3000,soc=100,mv=4200,temp=3000;static int16_t current=0,average=0;
static bool journalExists=false,backupExists=false;
static mosaico_factory_audit::JournalBytes savedJournal;
static mosaico_factory_audit::BackupBytes savedBackup;
static unsigned char heap[16384];
constexpr unsigned MALLOC_CAP_SPIRAM=1,MALLOC_CAP_8BIT=2;
void* heap_caps_malloc(size_t n,unsigned){++allocs;return n<=sizeof(heap)?heap:nullptr;}
struct ScaleStorageLease{bool held=lease;};
struct Power{bool locked,wifiRunning;int phase;};struct Ble{bool advertising,connected;};struct Network{Power powerStats(){return {gate,false,2};}};struct Bluetooth{Ble diagnostics(){return {false,false};}};static Network network;static Bluetooth bluetooth;Network& GetNetworkQuota(){return network;}Bluetooth& GetCodexMicroBle(){return bluetooth;}
namespace MosaicoOta{bool busy(){return !gate;}bool healthPending(){return fault==20;}}
static esp_partition_t part{};const esp_partition_t* esp_ota_get_running_partition(){return &part;}
int esp_ota_get_state_partition(const esp_partition_t*,esp_ota_img_states_t* s){*s=validApp?ESP_OTA_IMG_VALID:ESP_OTA_IMG_PENDING_VERIFY;return 0;}
void gauge_feed(){}void gauge_delay_ms(uint32_t){}
bool gauge_identity(){return fw;}void gauge_engine_locked(Hal::GaugeEngineSnapshot& s,uint8_t,uint8_t){s.validMask=fw?12:0;}
bool gauge_word(uint8_t reg,uint16_t& v){switch(reg){case 0x3a:v=op;break;case 0x3c:v=dc;break;case 0x12:v=fcc;break;case 0x10:v=rm;break;case 0x2c:v=soc;break;case 8:v=mv;break;case 6:v=temp;break;case 0xc:v=uint16_t(current);break;case 0x14:v=uint16_t(average);break;default:return false;}return fault!=21;}
enum class AccessWord{UnsealFirst,UnsealSecond,Full,Seal};
bool gauge_access_word(AccessWord a){switch(a){case AccessWord::UnsealFirst:++unsealWords;return fault!=10;case AccessWord::UnsealSecond:++unsealWords;op=0x24;return fault!=11;case AccessWord::Full:++fullWords;if(fullWords==2)op=0x22;return fault!=12;case AccessWord::Seal:++seals;op=0x26;return fault!=13;}return false;}
bool gauge_wait_security(uint8_t sec){return ((op>>1)&3)==sec;}
int gauge_load_journal(mosaico_gauge::Journal& j,const uint8_t* mac){j={};j.magic=0x47433635;j.version=1;j.deviceType=0x220;j.unitMah=1;j.state=7;j.action=1;j.changedFcc=1;std::memcpy(j.mac,mac,6);j.originalDesign=j.originalFcc=3000;j.targetDesign=j.targetFcc=j.lastDesign=j.lastFcc=65;j.operationBefore=0xa2;mosaico_gauge::seal(j);return fault==9?2:0;}
int gauge_load_access(mosaico_gauge::AccessJournal& j,const uint8_t* mac){j={};j.magic=0x47414331;j.version=1;j.deviceType=0x220;j.unitMah=1;j.state=3;j.priorSecurity=j.lastSecurity=3;j.unsealAttempted=j.unsealVerified=j.fullAttempted=j.fullVerified=1;std::memcpy(j.mac,mac,6);j.baseDesign=j.baseFcc=65;j.operationBefore=0xa6;mosaico_gauge::seal(j);return 0;}
int nvs_get_stats(const char*,nvs_stats_t* s){s->available_entries=unsigned(available);return fault==8?2:0;}
int nvs_open(const char* ns,int mode,unsigned* h){if(!std::strcmp(ns,"gaugecal")&&mode==NVS_READONLY){*h=3;return 0;}if(std::strcmp(ns,"gauge3kaudit")){++legacyWrites;return 2;}*h=mode+1;return fault==22?2:0;}
int nvs_get_blob(unsigned h,const char* key,void* p,size_t* n){++reads;
 if(h==3){if(fault==34)return ESP_ERR_NVS_NOT_FOUND;const uint8_t* bytes=nullptr;size_t length=0;if(!std::strcmp(key,"reload_v1")){bytes=legacyEvidence.reload.data();length=28;}else if(!std::strcmp(key,"fcc_boot_v1")){bytes=legacyEvidence.cold;length=48;}else if(!std::strcmp(key,"fcc_fix_v1")){bytes=legacyEvidence.runtime;length=48;}else{++legacyWrites;return 2;}if(*n<length)return 2;std::memcpy(p,bytes,length);*n=length;if(fault==35)static_cast<uint8_t*>(p)[0]^=1;return 0;}
if(!std::strcmp(key,"f3_aud_v1")){if(!journalExists)return ESP_ERR_NVS_NOT_FOUND;if(*n<64)return 2;std::memcpy(p,savedJournal.data(),64);*n=fault==23?63:64;if(fault==24)static_cast<uint8_t*>(p)[60]^=1;return 0;}if(!std::strncmp(key,"f3b",3)){if(!backupExists)return ESP_ERR_NVS_NOT_FOUND;if(*n<985)return 2;std::memcpy(p,savedBackup.data(),985);*n=fault==25?984:985;if(fault==26)static_cast<uint8_t*>(p)[100]^=1;return 0;}++legacyWrites;return 2;}
int nvs_set_blob(unsigned,const char* key,const void* p,size_t n){++sets;if(fault==27)return 2;if(!std::strcmp(key,"f3_aud_v1")&&n==64){if(static_cast<const uint8_t*>(p)[5]==2&&!backupExists){++legacyWrites;return 2;}std::memcpy(savedJournal.data(),p,n);journalExists=true;return 0;}if(!std::strncmp(key,"f3b",3)&&n==985){if(backupExists){++legacyWrites;return 2;}std::memcpy(savedBackup.data(),p,n);backupExists=true;return fault==28?2:0;}++legacyWrites;return 2;}
int nvs_commit(unsigned){++commits;if(fault==29)return 2;if(fault==30&&commits>=7)available=72;return 0;}void nvs_close(unsigned){}
static bool dm(uint16_t a,uint8_t b[36]){++dmReads;for(unsigned i=0;i<36;++i)b[i]=0;b[0]=uint8_t(a);b[1]=uint8_t(a>>8);b[2]=uint8_t(a>>8);b[3]=uint8_t(a);if(a==0x929d||a==0x929f){b[2]=0x0b;b[3]=0xb8;}if(fault==14&&dmReads==2)b[2]^=1;if(fault==15&&dmReads==4)return false;if(fault==16&&dmReads==4){op=0x422;return false;}if(fault==17&&dmReads==4){op=0x23;return false;}if(fault==18&&dmReads==4){op=0x20;return false;}if(fault==31&&a==0x929d){b[2]=0;b[3]=65;}if(fault==31&&a==0x9180){b[2]=0x80;b[3]=0;}if(fault==32&&dmReads==262)dc=65;if(fault==33&&dmReads==262)op=0x422;b[35]=36;b[34]=mosaico_gauge::macChecksum(b,34);if(fault==19&&dmReads==1)b[34]^=1;return true;}
bool gauge_threshold_read(mosaico_threshold::Selector s,uint8_t b[36]){return dm(uint16_t(s),b);}bool gauge_calibration_read(mosaico_calibration::Selector s,uint8_t b[36]){return dm(uint16_t(s),b);}bool gauge_public_extra_read(mosaico_public_extra::Selector s,uint8_t b[36]){return dm(uint16_t(s),b);}
NATIVE_EVIDENCE_FUNCTIONS
#include "mosaico_factory_audit.inc"
'''
CASES=r'''
static void reset(){factory_workspace=nullptr;fault=0;available=139;commits=sets=reads=unsealWords=fullWords=seals=dmReads=legacyWrites=allocs=0;gate=validApp=lease=gauge=fw=true;op=0x26;dc=fcc=rm=3000;soc=100;mv=4200;temp=3000;current=average=0;journalExists=backupExists=false;savedJournal={};savedBackup={};
 auto mac=hal.getFactoryMac();mosaico_gauge::ReloadJournal reload{};reload.magic=0x47423635;reload.version=1;reload.deviceType=0x220;reload.unitMah=1;reload.state=2;std::memcpy(reload.mac,mac.data(),6);reload.targetDesign=reload.targetFcc=65;mosaico_gauge::seal(reload);std::memcpy(legacyEvidence.reload.data(),&reload,28);
 mosaico_fcc_limit::Journal j{};std::memcpy(j.mac,mac.data(),6);j.state=mosaico_fcc_limit::State::Failed;j.backupValid=j.fullAccessVerified=true;j.intents=1951;j.before={0x102a,3000,3000};j.target={0x112a,65,65};mosaico_fcc_limit::encode(j,legacyEvidence.cold,mosaico_fcc_limit::Mode::ColdFactory);j.state=mosaico_fcc_limit::State::Verified;j.intents=927;j.before={0x102a,65,100};mosaico_fcc_limit::encode(j,legacyEvidence.runtime);
}
static bool has(const char* s,const char* needle){for(size_t i=0;s[i];++i)if(!std::strncmp(s+i,needle,std::strlen(needle)))return true;return false;}
static int codec_case(){using namespace mosaico_factory_audit;if(crc(reinterpret_cast<const uint8_t*>("123456789"),9)!=0xcbf43926U||requiredEntries!=113||capturePeak!=84)return 1;Journal j{};j.generation=42;j.mac=hal.getFactoryMac();j.flags=255;j.count=131;j.before={3000,3000,0x26,3000,100,4200,3000,0,0};j.after=j.before;j.state=State::Completed;BackupBytes b{};initBackup(j,b);for(unsigned i=0;i<131;++i)for(unsigned k=0;k<scale_recovery::layout[i].width;++k)b[67+i*7+k]=uint8_t(i+k);sealBackup(b);j.sourceCrc=sourceCrc(b);if(!validBackup(b,j))return 2;JournalBytes raw{};Journal decoded{};if(!encode(j,raw)||!decode(raw,j.mac,decoded)||decoded.generation!=42||decoded.sourceCrc!=j.sourceCrc)return 3;for(unsigned i=0;i<64;++i){raw[i]^=1;if(decode(raw,j.mac,decoded))return 4;raw[i]^=1;}for(unsigned i=0;i<985;i+=31){b[i]^=1;if(validBackup(b,j))return 5;b[i]^=1;}auto wrong=j.mac;wrong[0]^=1;if(decode(raw,wrong,decoded))return 6;return 0;}
static int run_case(int id){reset();char out[1024];if(id==0&&codec_case())return 20;
 if(id>=1&&id<=7){if(id==1)dc=65;if(id==2)mv=3599;if(id==3)temp=3132;if(id==4)current=131;if(id==5)gate=false;if(id==6)validApp=false;if(id==7)lease=false;}
 if(id>=8&&id<=30)fault=id;
 if(id==31)available=112;if(id==32)fw=false;if(id==33)rm=3001;
 if(id==34||id==35||id==36||id==37){mosaico_factory_audit::Journal j{};j.generation=1;j.mac=hal.getFactoryMac();j.state=id==34?mosaico_factory_audit::State::Pending:id==35?mosaico_factory_audit::State::Failed:mosaico_factory_audit::State::Completed;if(id>=36){j.flags=255;j.count=131;j.before={3000,3000,0x26,3000,100,4200,3000,0,0};j.after=j.before;}mosaico_factory_audit::encode(j,savedJournal);journalExists=true;if(id==36)savedJournal[12]^=1;if(id==37){savedJournal[4]=2;auto c=mosaico_factory_audit::crc(savedJournal.data(),60);mosaico_factory_audit::put32(savedJournal.data()+60,c);}}
  if(id==38){backupExists=true;savedBackup[0]=1;}
 if(id==39)fault=31;if(id==40)fault=32;if(id==41)fault=33;if(id==42){mosaico_factory_audit::Journal j{};j.generation=1;j.mac=hal.getFactoryMac();j.state=mosaico_factory_audit::State::Completed;j.flags=255;j.count=131;j.before={3000,3000,0x26,3000,100,4200,3000,0,0};j.after=j.before;mosaico_factory_audit::encode(j,savedJournal);journalExists=true;}
 if(id==43)fault=34;if(id==44)fault=35;
 if(id==45||id==46||id==47||id==48){mosaico_fcc_limit::Journal j{};auto mac=hal.getFactoryMac();mosaico_fcc_limit::decode(legacyEvidence.cold,mac.data(),j,mosaico_fcc_limit::Mode::ColdFactory);if(id==45)j.state=mosaico_fcc_limit::State::Pending;if(id==46){j.fullAccessVerified=false;j.backupValid=false;j.before={};j.target={};j.intents=mosaico_fcc_limit::Unseal;}if(id==47){j.fullAccessVerified=false;j.backupValid=false;j.before={};j.target={};j.intents=mosaico_fcc_limit::Unseal|mosaico_fcc_limit::Full;}if(id==48)j.intents=1950;mosaico_fcc_limit::encode(j,legacyEvidence.cold,mosaico_fcc_limit::Mode::ColdFactory);}
 if(id==49){mosaico_fcc_limit::Journal j{};auto mac=hal.getFactoryMac();mosaico_fcc_limit::decode(legacyEvidence.runtime,mac.data(),j);j.state=mosaico_fcc_limit::State::Failed;j.fullAccessVerified=false;j.backupValid=false;j.before={};j.target={};j.intents=3;mosaico_fcc_limit::encode(j,legacyEvidence.runtime);}
 auto originalLegacy=legacyEvidence;
 bool ok=hal.gaugeFactoryAuditCapture(out,sizeof(out));
 if(id==0||id==30||id==39){if(!ok||!factory_workspace->complete||!factory_workspace->live||factory_workspace->record.count!=131||unsealWords!=2||fullWords!=2||seals!=1||dmReads!=262)return 1;
  if(!mosaico_factory_audit::validBackup(savedBackup,factory_workspace->record))return 2;
  for(unsigned i=0;i<131;++i)if(!hal.gaugeFactoryAuditField(i,out,sizeof(out)))return 3;
  if(hal.gaugeFactoryAuditField(131,out,sizeof(out)))return 4;
  auto old=savedBackup;if(hal.gaugeFactoryAuditCapture(out,sizeof(out))||savedBackup!=old||unsealWords!=2||fullWords!=2)return 5;
  if(id==30&&(factory_workspace->available!=72||!factory_workspace->headroomValid))return 6;if(id==39&&(factory_observed_word(0x929d)!=65||factory_observed_word(0x9180)!=0x8000))return 13;
  if(!hal.gaugeFactoryAuditStatus(out,sizeof(out)))return 7;if(id==30&&(factory_workspace->available>=73||!has(out,"future_headroom_ready=0")))return 21;
  factory_workspace=nullptr;if(!hal.gaugeFactoryAuditStatus(out,sizeof(out))||factory_workspace->live||factory_workspace->complete||!has(out,"observed_cache_valid=0")||hal.gaugeFactoryAuditField(0,out,sizeof(out)))return 8;
 }else{if(ok||factory_workspace->complete)return 9;if(id<=9||id==20||id==21||(id>=31&&id<=37)||id>=42){if(unsealWords||fullWords||seals||sets)return 10;}
  if(id==16||id==17||id==18||id==41){if(seals)return 11;}if(id==40||id==41){if(factory_workspace->record.count!=131||!hal.gaugeFactoryAuditField(0,out,256)||!hal.gaugeFactoryAuditField(130,out,256)||factory_workspace->complete||backupExists)return 14;}
 }
 if(std::memcmp(&originalLegacy,&legacyEvidence,sizeof(legacyEvidence)))return 22;
 if(unsealWords>2||fullWords>2||seals>1||legacyWrites)return 12;
 return 0;
}
extern "C" {volatile int test_results[50]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<50;++i)test_results[i]=run_case(i);test_done();}
'''
class FactoryTests(unittest.TestCase):
 def test_actual_inc_rv32(self):
  RUN.mkdir(parents=True,exist_ok=True)
  support=base.MOCKS.split('static Hal hal;',1)[0]+base.RV_SUPPORT.split('static esp_partition_t imageParts',1)[0]
  with tempfile.TemporaryDirectory(prefix='.factory-audit-',dir=ROOT) as td:
   td=Path(td);headers={**base.STUBS,**base.HEADERS};headers['nvs.h']=headers['nvs.h'].replace('NVS_READWRITE=1','NVS_READWRITE=1,NVS_READONLY=0');headers['esp_ota_ops.h']+='\nconst esp_partition_t* esp_ota_get_running_partition();\n';headers['esp_ota_ops.h']=headers['esp_ota_ops.h'].replace('{ESP_OTA_IMG_VALID}','{ESP_OTA_IMG_VALID,ESP_OTA_IMG_PENDING_VERIFY}')
   for n,s in headers.items():p=td/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
   native=(ROOT/'main/hal/mosaico/mosaico_native_recovery.inc').read_text();evidence='bool native_read_once('+native.split('bool native_read_once(',1)[1].split('bool native_report(',1)[0]
   src=td/'factory.cpp';src.write_text(FIXTURE.replace('NATIVE_EVIDENCE_FUNCTIONS',evidence)+support+CASES);elf=RUN/'factory.elf'
   args=[str(base.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-Wall','-Wextra','-Werror','-Wno-missing-multilib','-Wno-unused-function','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(td),'-I'+str(ROOT/'main/hal/mosaico'),str(src),'--ld-path='+str(base.CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
   result=subprocess.run(args,capture_output=True,text=True,env={**os.environ,'TEMP':str(td),'TMP':str(td)});(RUN/'compile.log').write_text(result.stdout+result.stderr);self.assertEqual(result.returncode,0,str(RUN/'compile.log'))
  sys.path.insert(0,str(ROOT/'.artifacts/tools/unicorn-2.1.4-private-001/package'))
  from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32
  from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC
  raw=elf.read_bytes();entry=struct.unpack_from('<I',raw,24)[0];phoff=struct.unpack_from('<I',raw,28)[0];phsize,phnum=struct.unpack_from('<HH',raw,42);uc=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);uc.mem_map(0,32*1024*1024)
  for i in range(phnum):
   kind,off,addr,_,n,_,_,_=struct.unpack_from('<8I',raw,phoff+i*phsize)
   if kind==1:uc.mem_write(addr,raw[off:off+n])
  shoff=struct.unpack_from('<I',raw,32)[0];shsize,shnum=struct.unpack_from('<HH',raw,46);symbols={}
  for i in range(shnum):
   sh=struct.unpack_from('<10I',raw,shoff+i*shsize)
   if sh[1]!=2:continue
   strings=struct.unpack_from('<10I',raw,shoff+sh[6]*shsize);text=raw[strings[4]:strings[4]+strings[5]]
   for pos in range(sh[4],sh[4]+sh[5],sh[9]):
    name,value,_,_,_,_=struct.unpack_from('<IIIBBH',raw,pos);symbols[text[name:].split(b'\0',1)[0].decode()]=value
  uc.reg_write(UC_RISCV_REG_SP,0x1fffff0);uc.emu_start(entry,symbols['test_done'],timeout=20000000,count=150000000)
  values=struct.unpack('<50i',bytes(uc.mem_read(symbols['test_results'],200)))
  (RUN/'execution.log').write_text(f'Production factory inc/model; official Unicorn RV32; mocked external transport,NVS,identity,locks,OTA; ELF_SHA256={hashlib.sha256(raw).hexdigest()}\n'+repr(values)+'\n')
  self.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done']);self.assertEqual(values,(0,)*50)
 def test_scope(self):
  src=(ROOT/'main/hal/mosaico/mosaico_factory_audit.inc').read_text()
  for token in ('gauge_control(', 'gauge_write','nvs_erase','gauge_boot_info','scale_runtime->','gauge_threshold_cached','thresh_aud_v1','nvs_open("gaugecal"'):
   self.assertNotIn(token,src)
  self.assertIn('factory::requiredEntries',src)
if __name__=='__main__':unittest.main()
