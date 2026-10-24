"""RAM-only native recovery: production inc on official Unicorn RV32, no device/build."""
from pathlib import Path
import subprocess,tempfile,unittest,struct,sys,json,hashlib,os
import test_mosaico_gauge_scale_backend as base
ROOT=base.ROOT
RUN=ROOT/'artifacts/native-recovery-tests'
FIXTURE=r'''
#include <mutex>
#include <cstdio>
#include <cstring>
#include "mosaico_gauge_scale_journal_model.h"
#include "mosaico_gauge_engine_model.h"
#include "mosaico_gauge_status_model.h"
#include "mosaico_native_recovery_model.h"
#include "nvs.h"
#include "esp_ota_ops.h"
using namespace mosaico_native_recovery;
namespace scale_journal=mosaico_gauge_scale_journal;namespace scale_recovery=mosaico_gauge_recovery;
struct Hal {enum class GaugeBootReloadStatus {Deferred,Skipped,Applied,Critical};struct GaugeBootReloadInfo {GaugeBootReloadStatus status=GaugeBootReloadStatus::Critical;bool attempted=false;char reason[128]{};};using GaugeEngineSnapshot=mosaico_gauge_engine::Snapshot;std::array<uint8_t,6> getFactoryMac(){return {0x24,1,2,3,4,5};}bool gaugeNativeRecoveryEvidence(char*,size_t);bool gaugeNativeRecoveryResolve(uint32_t,char*,size_t);};
static Hal hal;Hal& GetHAL(){return hal;}
static Hal::GaugeBootReloadInfo gauge_boot_info,historical;
static std::recursive_mutex gauge_transaction_mutex;static std::mutex battery_mutex;
struct ScaleRuntime {
 struct {struct {int selected=0;scale_journal::Record records[1];}loaded;scale_journal::Record scratch;}journal;
 scale_recovery::Observation observed{};bool loaded=false,journalEverSeen=false,appliedVerifiedThisBoot=false,originalVerifiedThisBoot=false,freshBackupDurable=false,nativeResolvedThisBoot=false;
 uint32_t freshSourceCrc=0,verifiedGeneration=0,nativePreparedAuditGeneration=0,nativeEvidenceCrc=0,nativeProofGeneration=0;
 uint64_t verifiedEpoch=0;int64_t nativePreparedUs=0;char nativeOriginalReason[128]{};
};
static ScaleRuntime storage,*scale_runtime=&storage;
static scale_journal::Status scale_load_status=scale_journal::Status::Ready;
static uint32_t gauge_threshold_cached_generation=7,gauge_public_extra_generation=7;
static bool scale_mutation_in_progress=false,busy=false,validApp=true,gate=true,lease=true,fw=true;
static int64_t now=1000000;static int nvsWrites=0,keys=0,paramWrites=0,reads=0,fault=0,wireReads=0;
static uint16_t op=0xa6,dc=65,fcc=65,rm=50,soc=80,mv=4100,temp=3000;static int16_t current=0,average=0;
static Evidence disk;
static mosaico_gauge::Journal normalDisk{};static mosaico_gauge::AccessJournal accessDisk{};
int gauge_load_journal(mosaico_gauge::Journal& j,const uint8_t* mac){j=normalDisk;return mosaico_gauge::validJournal(j,mac)?0:2;}
int gauge_load_access(mosaico_gauge::AccessJournal& j,const uint8_t* mac){j=accessDisk;return mosaico_gauge::validAccessJournal(j,mac)?0:2;}

static mosaico_threshold::Values gauge_threshold_cached;static mosaico_calibration::Values gauge_calibration_cached;static mosaico_public_extra::Values gauge_public_extra_cached;
struct ScaleStorageLease{bool held=lease;};
bool scale_storage_locked(){return true;}bool scale_reviewed_record(const scale_journal::Record&){return true;}
namespace mosaico_gauge_scale_journal {template<class T>Status loadNvs(const std::array<uint8_t,6>&,T&){return scale_load_status;}}
bool scale_runtime_gate(){return gate&&!busy;}int64_t esp_timer_get_time(){return now;}
static bool gauge=true;
bool gauge_word(uint8_t reg,uint16_t& v){++wireReads;switch(reg){case 0x3a:v=op;break;case 8:v=mv;break;case 6:v=temp;break;case 0x3c:v=dc;break;case 0x12:v=fcc;break;case 0x10:v=rm;break;case 0x2c:v=soc;break;case 0x0c:v=uint16_t(current);break;case 0x14:v=uint16_t(average);break;default:return false;}return fault!=30;}
bool gauge_identity(){return fw;}
void gauge_engine_locked(Hal::GaugeEngineSnapshot& s,uint8_t,uint8_t){s.validMask=fw?12:0;}
int nvs_open(const char* ns,int mode,unsigned* h){if(std::strcmp(ns,"gaugecal")||mode!=NVS_READONLY){++keys;return 2;}*h=1;return fault==1?1:0;}
int nvs_get_blob(unsigned,const char* key,void* p,size_t* size){++reads;if(fault==2)return 1;const uint8_t* raw=nullptr;size_t n=0;
 if(!std::strcmp(key,"reload_v1")){raw=disk.reload.data();n=28;}else if(!std::strcmp(key,"fcc_boot_v1")){raw=disk.cold;n=48;}else if(!std::strcmp(key,"fcc_fix_v1")){raw=disk.runtime;n=48;}else{++keys;return 1;}
 std::memcpy(p,raw,n);*size=n-(fault==3);if(fault==4&&reads==4)static_cast<uint8_t*>(p)[0]^=1;return 0;}
void nvs_close(unsigned){}
int nvs_set_blob(unsigned,const char*,const void*,size_t){++nvsWrites;return 0;}int nvs_commit(unsigned){++nvsWrites;return 0;}
static esp_partition_t part{};
const esp_partition_t* esp_ota_get_running_partition(){return &part;}
int esp_ota_get_state_partition(const esp_partition_t*,esp_ota_img_states_t* s){*s=validApp?ESP_OTA_IMG_VALID:ESP_OTA_IMG_PENDING_VERIFY;return 0;}
'''
CASES=r'''
static bool contains(const char* text,const char* word){for(size_t i=0;text[i];++i){size_t j=0;while(word[j]&&text[i+j]&&text[i+j]==word[j])++j;if(!word[j])return true;}return false;}
static void reset(){storage={};scale_runtime=&storage;gauge_boot_info={};std::snprintf(gauge_boot_info.reason,128,"boot_critical_failed_no_retry_preserved");historical=gauge_boot_info;
 scale_load_status=scale_journal::Status::Ready;gauge_threshold_cached_generation=7;gauge_public_extra_generation=7;scale_mutation_in_progress=false;busy=false;validApp=true;gate=true;lease=true;fw=true;now=1000000;nvsWrites=keys=paramWrites=reads=fault=wireReads=0;op=0xa6;dc=fcc=65;rm=50;soc=80;mv=4100;temp=3000;current=average=0;
 gauge_threshold_cached={};gauge_threshold_cached.readable=mosaico_threshold::fieldCount;gauge_threshold_cached.anchors=true;gauge_calibration_cached={};gauge_calibration_cached.readable=mosaico_calibration::fieldCount;gauge_public_extra_cached={};gauge_public_extra_cached.complete=true;gauge_public_extra_cached.readable=mosaico_public_extra::fieldCount;
 auto& r=storage;r.originalVerifiedThisBoot=r.freshBackupDurable=true;r.freshSourceCrc=0x91336fc0;r.verifiedGeneration=1;r.verifiedEpoch=1;r.nativePreparedUs=500000;r.nativePreparedAuditGeneration=7;
 r.observed.count=131;r.observed.authenticated=r.observed.sealed=r.observed.cfg0=true;
 for(unsigned n=0;n<131;++n)r.journal.scratch.snapshot.fields[n]=fixture[n];r.journal.scratch.snapshot.count=131;
 auto& s=r.journal.loaded.records[0].snapshot;s.phase=scale_recovery::Phase::Original;s.generation=1;s.epoch=1;
 auto mac=hal.getFactoryMac();
 normalDisk={};normalDisk.magic=0x47433635;normalDisk.version=1;normalDisk.deviceType=0x220;normalDisk.unitMah=1;normalDisk.state=7;normalDisk.changedFcc=1;normalDisk.action=1;
 std::memcpy(normalDisk.mac,mac.data(),6);normalDisk.originalDesign=normalDisk.originalFcc=3000;normalDisk.targetDesign=normalDisk.targetFcc=normalDisk.lastDesign=normalDisk.lastFcc=65;normalDisk.operationBefore=0xa2;mosaico_gauge::seal(normalDisk);
 accessDisk={};accessDisk.magic=0x47414331;accessDisk.version=1;accessDisk.deviceType=0x220;accessDisk.unitMah=1;accessDisk.state=3;accessDisk.priorSecurity=accessDisk.lastSecurity=3;
 accessDisk.unsealAttempted=accessDisk.unsealVerified=accessDisk.fullAttempted=accessDisk.fullVerified=1;std::memcpy(accessDisk.mac,mac.data(),6);accessDisk.baseDesign=accessDisk.baseFcc=3000;accessDisk.operationBefore=0xa6;mosaico_gauge::seal(accessDisk);
 mosaico_gauge::ReloadJournal reload{};reload.magic=0x47423635;reload.version=1;reload.deviceType=0x220;reload.unitMah=1;reload.state=2;std::memcpy(reload.mac,mac.data(),6);reload.targetDesign=reload.targetFcc=65;mosaico_gauge::seal(reload);std::memcpy(disk.reload.data(),&reload,28);
 mosaico_fcc_limit::Journal j{};std::memcpy(j.mac,mac.data(),6);j.state=mosaico_fcc_limit::State::Failed;j.backupValid=j.fullAccessVerified=true;j.intents=1951;j.before={0x102a,3000,3000};j.target={0x112a,65,65};mosaico_fcc_limit::encode(j,disk.cold,mosaico_fcc_limit::Mode::ColdFactory);
 j.state=mosaico_fcc_limit::State::Verified;j.intents=927;j.before={0x102a,65,100};mosaico_fcc_limit::encode(j,disk.runtime);
}
static int run_case(int id){reset();char output[768];uint32_t crc=digest(disk);
 if(id==1)crc^=1;if(id>=2&&id<=5)fault=id-1;
 if(id==6)disk.runtime[47]^=1;
 if(id==7){disk.cold[12]^=1;auto c=mosaico_fcc_limit::recordCrc(disk.cold,44);for(int i=0;i<4;++i)disk.cold[44+i]=c>>(8*i);}
 if(id==8)fw=false;if(id==9)storage.observed.count=130;if(id==10)storage.freshSourceCrc^=1;
 if(id==11)now=120500001;if(id==12)gauge_public_extra_generation=8;if(id==13)op=0xa2;
 if(id==14)dc=130;if(id==15)fcc=100;if(id==16)rm=66;if(id==17)busy=true;if(id==18)validApp=false;
 if(id==19)gate=false;if(id==20)lease=false;if(id==21)storage.originalVerifiedThisBoot=false;
 if(id==22)gauge_boot_info.attempted=true;if(id==23)storage.journal.loaded.records[0].snapshot.phase=scale_recovery::Phase::Pending;
 if(id==24){disk.reload[9]=1;auto c=mosaico_gauge::crc32(disk.reload.data(),24);std::memcpy(disk.reload.data()+24,&c,4);}
 if(id==25){disk.cold[9]=1;auto c=mosaico_fcc_limit::recordCrc(disk.cold,44);std::memcpy(disk.cold+44,&c,4);}
 if(id==26){disk.cold[26]^=1;auto c=mosaico_fcc_limit::recordCrc(disk.cold,44);std::memcpy(disk.cold+44,&c,4);}
 if(id==27)storage.nativePreparedUs=0;if(id==28)mv=3499;if(id==29)current=131;if(id==30)fault=30;
 if(id==31)gauge_threshold_cached.readable--;if(id==32)gauge_calibration_cached.readable--;if(id==33)gauge_public_extra_cached.complete=false;if(id==34)storage.journal.scratch.snapshot.fields[0].original[0]^=1;if(id==35)storage.journal.loaded.records[0].snapshot.generation++;if(id==36)op|=0x400;if(id==37)op|=1;if(id==38)op&=~0x20;
 Evidence before=disk;auto bootBefore=gauge_boot_info;const auto statusBefore=gauge_boot_info.status;
 bool ok=hal.gaugeNativeRecoveryResolve(crc,output,sizeof(output));
 if(id==0){if(!ok||!storage.nativeResolvedThisBoot||gauge_boot_info.status!=Hal::GaugeBootReloadStatus::Skipped||gauge_boot_info.attempted)return 1;
 if(!hal.gaugeNativeRecoveryResolve(crc,output,sizeof(output)))return 2;
 if(!hal.gaugeNativeRecoveryEvidence(output,sizeof(output)))return 3;
 if(!contains(output,"tx_v1_byteLen=36 tx_v1_raw=")||!contains(output,"access_v1_byteLen=32 access_v1_raw=")||!contains(output,"tx_v1_load=0 access_v1_load=0"))return 42;
 normalDisk.crc^=1;accessDisk.crc^=1;
 if(!hal.gaugeNativeRecoveryEvidence(output,sizeof(output))||contains(output,"tx_v1_raw=")||contains(output,"access_v1_raw=")||!contains(output,"tx_v1_load=2 access_v1_load=2"))return 43;
 normalDisk.crc^=1;accessDisk.crc^=1;
  native_recovery_check_sample_locked(65,65,0xa6,true);if(!storage.nativeResolvedThisBoot)return 9;
 storage.journal.loaded.records[0].snapshot.phase=scale_recovery::Phase::Applied;storage.appliedVerifiedThisBoot=true;scale_load_locked(true);native_recovery_check_sample_locked(130,130,0xa6,true);if(!storage.nativeResolvedThisBoot)return 15;
 storage.journal.loaded.records[0].snapshot.phase=scale_recovery::Phase::Original;storage.originalVerifiedThisBoot=true;storage.appliedVerifiedThisBoot=false;scale_load_locked(true);native_recovery_check_sample_locked(65,65,0xa6,true);if(!storage.nativeResolvedThisBoot)return 16;scale_mutation_in_progress=true;native_recovery_check_sample_locked(130,130,0xa2,false);native_recovery_revoke_locked();if(!storage.nativeResolvedThisBoot)return 4;scale_mutation_in_progress=false;native_recovery_check_sample_locked(65,66,0xa6,true);if(storage.nativeResolvedThisBoot||gauge_boot_info.status!=Hal::GaugeBootReloadStatus::Critical)return 5;
 if(!hal.gaugeNativeRecoveryResolve(crc,output,sizeof(output)))return 10;scale_invalidate_context_locked();if(storage.nativeResolvedThisBoot||gauge_boot_info.status!=Hal::GaugeBootReloadStatus::Critical||storage.originalVerifiedThisBoot)return 11;native_recovery_check_sample_locked(65,65,0xa6,true);if(storage.nativeResolvedThisBoot)return 12;
 for(unsigned faultCase=0;faultCase<5;++faultCase){storage.originalVerifiedThisBoot=true;storage.verifiedGeneration=1;storage.verifiedEpoch=1;storage.nativeResolvedThisBoot=true;gauge_boot_info.status=Hal::GaugeBootReloadStatus::Skipped;scale_load_status=scale_journal::Status::Ready;auto& sn=storage.journal.loaded.records[0].snapshot;sn.phase=scale_recovery::Phase::Original;sn.generation=1;sn.epoch=1;if(faultCase==0)sn.generation=2;if(faultCase==1)sn.phase=scale_recovery::Phase::Pending;if(faultCase==2)scale_load_status=scale_journal::Status::Corrupt;if(faultCase>=3){sn.phase=scale_recovery::Phase::Applied;storage.appliedVerifiedThisBoot=faultCase==4;if(faultCase==4)sn.generation=2;}scale_load_locked(true);if(storage.nativeResolvedThisBoot||gauge_boot_info.status!=Hal::GaugeBootReloadStatus::Critical)return 13;native_recovery_check_sample_locked(65,65,0xa6,true);if(storage.nativeResolvedThisBoot)return 14;}
 ScaleRuntime reboot{};if(reboot.nativeResolvedThisBoot||reboot.nativeEvidenceCrc)return 6;
 }else if(ok||storage.nativeResolvedThisBoot||std::memcmp(&bootBefore,&gauge_boot_info,sizeof(bootBefore))||gauge_boot_info.status!=statusBefore)return 7;
 if(std::memcmp(&before,&disk,sizeof(disk))||std::memcmp(&historical.reason,&bootBefore.reason,sizeof(historical.reason))||nvsWrites||keys||paramWrites)return 8;
 return 0;
}
extern "C" volatile int test_results[39];volatile int test_results[39];
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<39;++i)test_results[i]=run_case(i);test_done();}
'''
class NativeTests(unittest.TestCase):
 def test_actual_inc_rv32_fault_execution(self):
  RUN.mkdir(parents=True,exist_ok=True)
  data=json.loads((ROOT/'.artifacts/private/mosaico/scale2-reviewed-target-001.json').read_text())['all_fields']
  rows=[]
  for f in data:
   pad=lambda key:','.join(str(v) for v in bytes.fromhex(f[key]).ljust(4,b'\0'))
   rows.append('{0x%s,%d,{%s},{%s}}'%(f['address'],f['width'],pad('originalBE'),pad('targetBE')))
  fixture='static constexpr mosaico_gauge_recovery::Field fixture[]={'+','.join(rows)+'};\n'
  runtime=(ROOT/'main/hal/mosaico/mosaico_gauge_scale_runtime.inc').read_text()
  load='scale_journal::Status scale_load_locked(bool refresh=false){'+runtime.split('scale_journal::Status scale_load_locked(bool refresh=false){',1)[1].split('void scale_invalidate_context_locked()',1)[0]
  invalidate='void scale_invalidate_context_locked(){'+runtime.split('void scale_invalidate_context_locked(){',1)[1].split('bool scale_journal_missing_locked()',1)[0]
  standard='bool scale_standard_original_locked(){'+runtime.split('bool scale_standard_original_locked(){',1)[1].split('// Canonical matching',1)[0]
  support=base.MOCKS.split('static Hal hal;',1)[0]+base.RV_SUPPORT.split('static esp_partition_t imageParts',1)[0]
  code=FIXTURE+'namespace {void native_recovery_revoke_locked();} void scale_update_protection_locked(){}'+load+invalidate+standard+'\n#include "mosaico_native_recovery.inc"\n'+support+fixture+CASES
  with tempfile.TemporaryDirectory(prefix='.native-recovery-',dir=ROOT) as td:
   td=Path(td);headers={**base.STUBS,**base.HEADERS};headers['nvs.h']=headers['nvs.h'].replace('NVS_READWRITE=1','NVS_READWRITE=1,NVS_READONLY=0');headers['esp_ota_ops.h']+='\nconst esp_partition_t* esp_ota_get_running_partition();\n';headers['esp_ota_ops.h']=headers['esp_ota_ops.h'].replace('{ESP_OTA_IMG_VALID}','{ESP_OTA_IMG_VALID,ESP_OTA_IMG_PENDING_VERIFY}')
   for n,s in headers.items():p=td/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
   src=td/'native.cpp';src.write_text(code);elf=RUN/'native.elf'
   args=[str(base.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-Wall','-Wextra','-Werror','-Wno-missing-multilib','-Wno-unused-function','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(td),'-I'+str(ROOT/'main/hal/mosaico'),str(src),'--ld-path='+str(base.CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
   env=dict(os.environ,TEMP=str(RUN),TMP=str(RUN))
   result=subprocess.run(args,capture_output=True,text=True,env=env);(RUN/'compile.log').write_text(result.stdout+result.stderr);self.assertEqual(result.returncode,0,str(RUN/'compile.log'))
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
  uc.reg_write(UC_RISCV_REG_SP,0x1fffff0);uc.emu_start(entry,symbols['test_done'],timeout=20000000,count=100000000)
  values=struct.unpack('<39i',bytes(uc.mem_read(symbols['test_results'],156)))
  (RUN/'execution.log').write_text(f'Official Unicorn RV32 production native inc and standard function, mock transport/NVS/locks/OTA; ELF_SHA256={hashlib.sha256(raw).hexdigest()}\n'+repr(values)+'\n')
  self.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done']);self.assertEqual(values,(0,)*39)
 def test_closed_boundaries(self):
  src=(ROOT/'main/hal/mosaico/mosaico_native_recovery.inc').read_text()
  for token in ('nvs_set','nvs_erase','nvs_commit','gauge_access_word','gauge_control(','0091','gaugeThresholdAudit('):self.assertNotIn(token,src)
if __name__=='__main__':unittest.main()
