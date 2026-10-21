"""Maintenance power actual-provider RV32 execution; no SDK/device/build mutations."""
from pathlib import Path
import hashlib,os,struct,subprocess,sys,tempfile,unittest
import test_mosaico_gauge_scale_backend as base
ROOT=base.ROOT
RUN=ROOT/'.artifacts/private/mosaico/maintenance-power-tests'
INC=ROOT/'main/hal/mosaico/mosaico_maintenance_power.inc'
FIXTURE=r'''
#include <mutex>
#include <cstdio>
#include <cstring>
#include "mosaico_gauge_scale_journal_model.h"
#include "mosaico_gauge_engine_model.h"
#include "mosaico_gauge_status_model.h"
#include "mosaico_native_recovery_model.h"
#include "nvs.h"
namespace scale_journal=mosaico_gauge_scale_journal;namespace scale_recovery=mosaico_gauge_recovery;namespace native_recovery=mosaico_native_recovery;
struct Hal {
 enum class GaugeBootReloadStatus{Deferred,Skipped,Applied,Critical};
 struct BootInfo{GaugeBootReloadStatus status=GaugeBootReloadStatus::Critical;bool attempted=false;char reason[128]{};};
 using GaugeEngineSnapshot=mosaico_gauge_engine::Snapshot;
 SNAPSHOT_DECL
 std::array<uint8_t,6> getFactoryMac(){return {0x24,1,2,3,4,5};}
 bool tryMaintenancePowerSnapshot(MaintenancePowerSnapshot&);
 bool maintenancePowerStatus(char*,size_t) const;
};
static Hal hal;Hal& GetHAL(){return hal;}
static Hal::BootInfo gauge_boot_info,historical;
static std::recursive_mutex gauge_transaction_mutex;static std::mutex battery_mutex;
struct ScaleRuntime{bool loaded=true,journalEverSeen=false;struct{struct{int selected=0;struct{struct{scale_recovery::Phase phase=scale_recovery::Phase::Original;}snapshot;}records[2];}loaded;}journal;};
static ScaleRuntime storage,*scale_runtime=&storage;static scale_journal::Status scale_load_status=scale_journal::Status::Ready;
static bool scale_mutation_in_progress=false,calFault=false,gauge=true;
bool scale_safety_mismatch_locked(){return calFault;}
static native_recovery::Evidence evidenceDisk;
static unsigned wordCalls=0,identityCalls=0,nvsReads=0,nvsOpens=0;
static int readFault=0,evidenceFault=0,identityFault=0;
static uint16_t op=0x26,dc=3000,fcc=3000,mv=4200,temp=3000,finalOp=0x26,finalDc=3000,finalFcc=3000;
static int16_t current=0,average=0;static int64_t startedUs=1000000,endedUs=1000100;static unsigned timerCalls=0;
static unsigned uiSentinel=0x31415926,batteryTelemetrySentinel=0x16180339;
int64_t esp_timer_get_time(){return timerCalls++?endedUs:startedUs;}
bool gauge_word(uint8_t reg,uint16_t& v){++wordCalls;if(int(wordCalls)==readFault)return false;
 switch(wordCalls){case 1:if(reg!=0x3a)return false;v=op;break;case 2:if(reg!=8)return false;v=mv;break;case 3:if(reg!=6)return false;v=temp;break;case 4:if(reg!=0x3c)return false;v=dc;break;case 5:if(reg!=0x12)return false;v=fcc;break;case 6:if(reg!=0xc)return false;v=uint16_t(current);break;case 7:if(reg!=0x14)return false;v=uint16_t(average);break;case 8:if(reg!=0x3a)return false;v=finalOp;break;case 9:if(reg!=0x3c)return false;v=finalDc;break;case 10:if(reg!=0x12)return false;v=finalFcc;break;default:return false;}return true;}
void gauge_engine_locked(Hal::GaugeEngineSnapshot& s,uint8_t,uint8_t){++identityCalls;s={};s.validMask=identityFault?mosaico_gauge_engine::Identity:mosaico_gauge_engine::Identity|mosaico_gauge_engine::DefinitionMatch;s.rawIdentity[0]=2;s.rawIdentity[1]=0x20;s.rawIdentity[3]=3;s.rawIdentity[5]=4;if(identityFault==1)s.rawIdentity[1]^=1;if(identityFault==2)s.rawIdentity[3]^=1;if(identityFault==3)s.rawIdentity[5]^=1;}
int nvs_open(const char* ns,int access,unsigned* h){++nvsOpens;if(std::strcmp(ns,"gaugecal")||access!=NVS_READONLY)return 2;*h=1;return evidenceFault==1?1:0;}
int nvs_get_blob(unsigned,const char* key,void* p,size_t* n){++nvsReads;if(evidenceFault==2)return 1;const uint8_t* bytes=nullptr;size_t length=0;
 if(!std::strcmp(key,"reload_v1")){bytes=evidenceDisk.reload.data();length=28;}else if(!std::strcmp(key,"fcc_boot_v1")){bytes=evidenceDisk.cold;length=48;}else if(!std::strcmp(key,"fcc_fix_v1")){bytes=evidenceDisk.runtime;length=48;}else return 2;
 if(*n<length)return 2;std::memcpy(p,bytes,length);*n=length-(evidenceFault==3);if(evidenceFault==4||(evidenceFault==5&&nvsReads==4))static_cast<uint8_t*>(p)[0]^=1;return 0;}
void nvs_close(unsigned){}
NATIVE_FUNCTIONS
#include "mosaico_maintenance_power.inc"
'''
RESET=r'''
static void reset(){maintenance_power_reason.store("not_sampled",std::memory_order_release);storage={};scale_runtime=&storage;scale_load_status=scale_journal::Status::Ready;scale_mutation_in_progress=calFault=false;gauge=true;gauge_transaction_mutex.available=battery_mutex.available=true;
 wordCalls=identityCalls=nvsReads=nvsOpens=timerCalls=0;readFault=evidenceFault=identityFault=0;op=finalOp=0x26;dc=fcc=finalDc=finalFcc=3000;mv=4200;temp=3000;current=average=0;startedUs=1000000;endedUs=1000100;gauge_boot_info={};std::snprintf(gauge_boot_info.reason,128,"boot_critical_failed_no_retry observed_sec=3 cfg=0 cal=0");historical=gauge_boot_info;
 auto mac=hal.getFactoryMac();mosaico_gauge::ReloadJournal reload{};reload.magic=0x47423635;reload.version=1;reload.deviceType=0x220;reload.unitMah=1;reload.state=2;std::memcpy(reload.mac,mac.data(),6);reload.targetDesign=reload.targetFcc=65;mosaico_gauge::seal(reload);std::memcpy(evidenceDisk.reload.data(),&reload,28);
 mosaico_fcc_limit::Journal j{};std::memcpy(j.mac,mac.data(),6);j.state=mosaico_fcc_limit::State::Failed;j.backupValid=j.fullAccessVerified=true;j.intents=1951;j.before={0x102a,3000,3000};j.target={0x112a,65,65};mosaico_fcc_limit::encode(j,evidenceDisk.cold,mosaico_fcc_limit::Mode::ColdFactory);j.state=mosaico_fcc_limit::State::Verified;j.intents=927;j.before={0x102a,65,100};mosaico_fcc_limit::encode(j,evidenceDisk.runtime);
}
static void alterCold(int mode){auto mac=hal.getFactoryMac();mosaico_fcc_limit::Journal j{};mosaico_fcc_limit::decode(evidenceDisk.cold,mac.data(),j,mosaico_fcc_limit::Mode::ColdFactory);if(mode==0)j.state=mosaico_fcc_limit::State::Pending;if(mode==1){j.backupValid=j.fullAccessVerified=false;j.before={};j.target={};j.intents=3;}if(mode==2)j.intents=1950;mosaico_fcc_limit::encode(j,evidenceDisk.cold,mosaico_fcc_limit::Mode::ColdFactory);}
static void alterReload(){mosaico_gauge::ReloadJournal j{};std::memcpy(&j,evidenceDisk.reload.data(),28);j.state=1;mosaico_gauge::seal(j);std::memcpy(evidenceDisk.reload.data(),&j,28);}
'''
# label, setup, expected success, must issue no BQ I/O
CASES=[('critical_exact_3k','',True,False),('native65','dc=fcc=finalDc=finalFcc=65;op=finalOp=0xa6;',True,False),('op00a6_3k','op=finalOp=0xa6;',True,False),('tx_busy','gauge_transaction_mutex.available=false;',False,True),('battery_busy','battery_mutex.available=false;',False,True),('no_gauge','gauge=false;',False,True),('scale_mutation','scale_mutation_in_progress=true;',False,True),('calibration_fault','calFault=true;',False,True),('no_runtime','scale_runtime=nullptr;',False,True),('runtime_unloaded','storage.loaded=false;',False,True)]
for name,status in [('io','IoError'),('corrupt','Corrupt'),('ambiguous','Ambiguous'),('rejected','Rejected')]:CASES.append((name,f'scale_load_status=scale_journal::Status::{status};',False,True))
CASES += [('missing_seen','scale_load_status=scale_journal::Status::Missing;storage.journalEverSeen=true;',False,True),('missing_valid','scale_load_status=scale_journal::Status::Missing;',True,False)]
for phase in ['Pending','Applied','Restoring']:CASES.append(('phase_'+phase,f'storage.journal.loaded.records[0].snapshot.phase=scale_recovery::Phase::{phase};',False,True))
for selected in [-1,2]:CASES.append(('selected_'+str(selected),f'storage.journal.loaded.selected={selected};',False,True))
CASES += [('original_slot1','storage.journal.loaded.selected=1;',True,False),('boot_deferred','gauge_boot_info.status=Hal::GaugeBootReloadStatus::Deferred;',False,True),('critical_attempted','gauge_boot_info.attempted=true;',False,True),('critical_wrong_reason','std::snprintf(gauge_boot_info.reason,128,"boot_critical_scale_recovery_required");',False,True),('normal_skipped','gauge_boot_info.status=Hal::GaugeBootReloadStatus::Skipped;',True,False),('normal_applied','gauge_boot_info.status=Hal::GaugeBootReloadStatus::Applied;',True,False)]
for i in range(1,6):CASES.append(('evidence_fault_'+str(i),f'evidenceFault={i};',False,True))
for i in range(3):CASES.append(('cold_invalid_'+str(i),f'alterCold({i});',False,True))
CASES.append(('new_reload_pending','alterReload();',False,True))
CASES += [('reload_completed','evidenceDisk.reload[9]=3;mosaico_gauge::ReloadJournal r{};std::memcpy(&r,evidenceDisk.reload.data(),28);mosaico_gauge::seal(r);std::memcpy(evidenceDisk.reload.data(),&r,28);',False,True),('wrong_mac','evidenceDisk.cold[12]^=1;mosaico_fcc_limit::put16(evidenceDisk.cold+18,1951);uint32_t c=mosaico_fcc_limit::recordCrc(evidenceDisk.cold,44);for(unsigned k=0;k<4;++k)evidenceDisk.cold[44+k]=uint8_t(c>>(8*k));',False,True),('runtime_pending','auto mac=hal.getFactoryMac();mosaico_fcc_limit::Journal j{};mosaico_fcc_limit::decode(evidenceDisk.runtime,mac.data(),j);j.state=mosaico_fcc_limit::State::Pending;mosaico_fcc_limit::encode(j,evidenceDisk.runtime);',False,True)]
for i in range(1,11):CASES.append(('word_read_failure_'+str(i),f'readFault={i};',False,False))
for i in range(1,4):CASES.append(('identity_bad_'+str(i),f'identityFault={i};',False,False))
for name,setup in [('op_noinit_0006','op=6;'),('op_noinit_0086','op=0x86;'),('sec2','op=0x24;'),('sec1','op=0x22;'),('cfg','op|=0x400;'),('cal','op|=1;'),('mix_dc','dc=65;'),('mix_fcc','fcc=65;'),('scale130','dc=fcc=finalDc=finalFcc=130;'),('v_low','mv=3899;'),('v_high','mv=4351;'),('temp_low','temp=2831;'),('temp_high','temp=3132;'),('current_low','current=-131;'),('current_high','current=131;'),('average_low','average=-131;'),('average_high','average=131;'),('final_cfg','finalOp|=0x400;'),('final_cal','finalOp|=1;'),('final_noinit','finalOp=0x86;'),('final_dc','finalDc=65;'),('final_fcc','finalFcc=65;'),('time_backward','endedUs=999999;'),('time_too_long','endedUs=1750001;'),('negative_start','startedUs=-1;endedUs=100;'),('negative_both','startedUs=-100;endedUs=-50;'),('zero_start','startedUs=0;endedUs=100;'),('zero_end','startedUs=100;endedUs=0;')]:CASES.append((name,setup,False,False))
for name,setup in [('v_min','mv=3900;'),('v_max','mv=4350;'),('temp_min','temp=2832;'),('temp_max','temp=3131;'),('current_min','current=-130;'),('current_max','current=130;'),('average_min','average=-130;'),('average_max','average=130;'),('time_max','endedUs=1750000;'),('unrelated_op_bit7','op=0xa6;finalOp=0x26;')]:CASES.append((name,setup,True,False))
MODEL=r'''
static bool contains(const char* text,const char* token){for(size_t i=0;text[i];++i)if(!std::strncmp(text+i,token,std::strlen(token)))return true;return false;}
constexpr bool modelCases(){using namespace mosaico_maintenance_power;Hal::MaintenancePowerSnapshot s{};s.operationStatus=0x26;s.designMah=s.fullMah=3000;s.voltageMv=3900;s.temperatureDeciK=2832;s.currentMa=-130;s.averageCurrentMa=130;if(!safe(s)||!safeOperation(0xa6)||safeOperation(0x86)||safeOperation(6)||safeOperation(0x426)||safeOperation(0x27))return false;s.designMah=s.fullMah=65;if(!safe(s))return false;s.designMah=s.fullMah=130;if(safe(s))return false;s.designMah=65;s.fullMah=3000;return !safe(s);}
static_assert(modelCases(),"INITCOMP is bit5, never bit7; paired raw capacity only");
'''
class MaintenancePowerTests(unittest.TestCase):
 def test_actual_provider_rv32(self):
  RUN.mkdir(parents=True,exist_ok=True)
  hal=(ROOT/'main/hal/hal.h').read_text();snapshot='struct MaintenancePowerSnapshot {'+hal.split('struct MaintenancePowerSnapshot {',1)[1].split('\n    };',1)[0]+'\n};'
  native=(ROOT/'main/hal/mosaico/mosaico_native_recovery.inc').read_text();evidence='bool native_read_once('+native.split('bool native_read_once(',1)[1].split('bool native_report(',1)[0]
  cases=[]
  for i,(label,setup,expected,noio) in enumerate(CASES):cases.append(f'case {i}:{{{setup}expected={str(expected).lower()};noio={str(noio).lower()};break;}}')
  runner=RESET+MODEL+'''\nstatic int run_case(int id){reset();bool expected=false,noio=false;switch(id){'''+''.join(cases)+'''}auto oldBoot=gauge_boot_info,oldHistorical=historical;auto oldEvidence=evidenceDisk;auto oldStorage=storage;
 char initial[192]{};if(!hal.maintenancePowerStatus(initial,sizeof(initial))||!contains(initial,"reason=not_sampled")||wordCalls||identityCalls||nvsReads||nvsOpens)return 6;
 Hal::MaintenancePowerSnapshot out{};out.valid=true;out.voltageMv=1;bool ok=hal.tryMaintenancePowerSnapshot(out);
 const unsigned oldWords=wordCalls,oldIdentity=identityCalls,oldNvs=nvsReads,oldOpens=nvsOpens,oldTimer=timerCalls;
 const char* cached=maintenance_power_reason.load(std::memory_order_acquire);char diagnostic[192]{},small[4]{};
 if(!hal.maintenancePowerStatus(diagnostic,sizeof(diagnostic))||!contains(diagnostic,"cached_only=1")||!contains(diagnostic,"no_i2c=1")||!contains(diagnostic,"no_power_proof=1"))return 7;
 if(expected&&!contains(diagnostic,"reason=raw_verified_not_soc_calibration"))return 8;
 if(id==3&&!contains(diagnostic,"reason=gauge_transaction_busy"))return 9;
 if(id==4&&!contains(diagnostic,"reason=battery_read_busy"))return 10;
 if(hal.maintenancePowerStatus(nullptr,192)||hal.maintenancePowerStatus(diagnostic,0)||hal.maintenancePowerStatus(small,sizeof(small)))return 11;
 if(wordCalls!=oldWords||identityCalls!=oldIdentity||nvsReads!=oldNvs||nvsOpens!=oldOpens||timerCalls!=oldTimer||maintenance_power_reason.load(std::memory_order_acquire)!=cached)return 12;
 if(ok!=expected)return 1;if(out.valid!=expected)return 2;if(noio&&(wordCalls||identityCalls))return 3;
 if(std::memcmp(&oldBoot,&gauge_boot_info,sizeof(oldBoot))||std::memcmp(&oldHistorical,&historical,sizeof(historical))||std::memcmp(&oldEvidence,&evidenceDisk,sizeof(evidenceDisk))||std::memcmp(&oldStorage,&storage,sizeof(storage))||uiSentinel!=0x31415926||batteryTelemetrySentinel!=0x16180339)return 4;
 if(expected&&(wordCalls!=10||identityCalls!=1||out.designMah!=dc||out.fullMah!=fcc||out.sampledUs!=endedUs))return 5;return 0;}
 extern "C" {volatile int test_results[COUNT]={};}
 extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
 extern "C" void _start(){for(int i=0;i<COUNT;++i)test_results[i]=run_case(i);test_done();}
 '''.replace('COUNT',str(len(CASES)))
  support=base.MOCKS.split('static Hal hal;',1)[0]+base.RV_SUPPORT.split('static esp_partition_t imageParts',1)[0]
  with tempfile.TemporaryDirectory(prefix='.maintenance-power-',dir=ROOT) as td:
   td=Path(td);headers=dict(base.HEADERS);headers['nvs.h']=headers['nvs.h'].replace('NVS_READWRITE=1','NVS_READWRITE=1,NVS_READONLY=0');headers['mutex']='''#pragma once
namespace std{struct mutex{bool available=true;};struct recursive_mutex{bool available=true;};struct try_to_lock_t{};inline constexpr try_to_lock_t try_to_lock{};template<class T>struct unique_lock{bool held;unique_lock(T& m,try_to_lock_t):held(m.available){}bool owns_lock()const{return held;}};}
'''
   for name,s in headers.items():(td/name).write_text(s)
   src=td/'power.cpp';src.write_text(FIXTURE.replace('SNAPSHOT_DECL',snapshot).replace('NATIVE_FUNCTIONS',evidence)+support+runner);elf=RUN/'power.elf'
   args=[str(base.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-Wall','-Wextra','-Werror','-Wno-missing-multilib','-Wno-unused-function','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-fstack-usage','-nostdinc++','-nostdlib','-I'+str(td),'-I'+str(INC.parent),str(src),'--ld-path='+str(base.CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
   result=subprocess.run(args,capture_output=True,text=True,env={**os.environ,'TEMP':str(td),'TMP':str(td)});(RUN/'compile.log').write_text(result.stdout+result.stderr);self.assertEqual(result.returncode,0,str(RUN/'compile.log'))
   stack=elf.with_suffix('.su').read_text();(RUN/'stack.log').write_text(stack)
   provider=[line for line in stack.splitlines() if 'tryMaintenancePowerSnapshot' in line]
   self.assertTrue(provider,'provider stack usage missing')
   for line in provider:self.assertLessEqual(int(line.split('\t')[1]),512,line)
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
  values=struct.unpack('<'+'i'*len(CASES),bytes(uc.mem_read(symbols['test_results'],4*len(CASES))))
  (RUN/'execution.log').write_text(f'Actual production provider/model+actual native evidence model/double-read functions; mocked locks/BQ transport/engine and NVS read-only. ELF_SHA256={hashlib.sha256(raw).hexdigest()}\n'+''.join(f'{i} {CASES[i][0]} result={v}\n' for i,v in enumerate(values)))
  self.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done']);self.assertEqual(values,(0,)*len(CASES),str(RUN/'execution.log'))
 def test_source_boundary(self):
  src=INC.read_text();body=src[src.index('bool Hal::'):]
  for token in ['nvs_set','nvs_commit','nvs_erase','gauge_access_word','gauge_control(','scale_boot_fence_locked(','scale_load_locked(','batteryPolicyTelemetry(','batteryTelemetry(','MosaicoOta::busy(','gaugeNativeRecoveryResolve(','gaugeFactoryAuditCapture(']:self.assertNotIn(token,body)
  self.assertIn('std::try_to_lock',body);self.assertIn('native_recovery::exact',body)
if __name__=='__main__':unittest.main()
