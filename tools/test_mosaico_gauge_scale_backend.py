"""Execute the REAL ScaleTrialBackend with official Unicorn RV32 ISA emulation.
No IDF build/device, crypto correctness, scheduler or physical I2C claim.
"""
from pathlib import Path
from datetime import datetime,timezone
import json,subprocess,tempfile,unittest,hashlib
import test_mosaico_gauge_scale_trial as trial_module
STUBS=trial_module.STUBS
ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'artifacts/gauge-scale-backend-tests'/datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S%fZ')
CLANG=Path('C:/Espressif/tools/esp-clang/esp-21.1.3_20260408/esp-clang/bin/clang++.exe')
HEADERS={
'cstdint':'''#pragma once
typedef unsigned char uint8_t;typedef signed char int8_t;typedef unsigned short uint16_t;typedef short int16_t;typedef unsigned int uint32_t;typedef int int32_t;typedef unsigned long long uint64_t;typedef long long int64_t;
#define UINT32_MAX 0xffffffffU
#define UINT64_MAX 0xffffffffffffffffULL
namespace std {using ::uint8_t;using ::uint16_t;using ::uint32_t;using ::uint64_t;using ::int8_t;using ::int16_t;using ::int32_t;using ::int64_t;}
''',
'cstddef':'''#pragma once
typedef __SIZE_TYPE__ size_t;
#define offsetof(T,M) __builtin_offsetof(T,M)
namespace std {using ::size_t;}
''',
'array':'''#pragma once
#include <cstddef>
namespace std {template<class T,size_t N>struct array{T values[N];constexpr T& operator[](size_t i){return values[i];}constexpr const T& operator[](size_t i)const{return values[i];}constexpr T* data(){return values;}constexpr const T* data()const{return values;}constexpr size_t size()const{return N;}constexpr T* begin(){return values;}constexpr T* end(){return values+N;}constexpr const T* begin()const{return values;}constexpr const T* end()const{return values+N;}constexpr bool operator==(const array& b)const{for(size_t i=0;i<N;++i)if(values[i]!=b.values[i])return false;return true;}constexpr bool operator!=(const array& b)const{return !(*this==b);}};}
''',
'atomic':'''#pragma once
namespace std {enum memory_order {memory_order_acquire,memory_order_release};template<class T>struct atomic {T value;constexpr atomic(T v):value(v){};void store(T v,memory_order){value=v;}T load(memory_order)const{return value;}};}
''',
'mutex':'''#pragma once
namespace std {struct mutex{void lock(){}void unlock(){}bool try_lock(){return true;}};using recursive_mutex=mutex;struct try_to_lock_t{};inline constexpr try_to_lock_t try_to_lock{};template<class T>struct lock_guard{explicit lock_guard(T&){};};template<class T>struct unique_lock{unique_lock(T&,try_to_lock_t){}bool owns_lock()const{return true;}};}
''',
'new':'''#pragma once
#include <cstddef>
inline void* operator new(size_t,void* p)noexcept{return p;}
''',
'algorithm':'''#pragma once
namespace std {template<class T>constexpr T min(T a,T b){return a<b?a:b;}}
''',
'cstdio':'''#pragma once
#include <cstddef>
namespace std {int snprintf(char*,size_t,const char*,...);}
''',
'cstring':'''#pragma once
#include <cstddef>
extern "C" void* memcpy(void*,const void*,size_t);extern "C" void* memset(void*,int,size_t);extern "C" int memcmp(const void*,const void*,size_t);
namespace std {using ::memcpy;using ::memset;using ::memcmp;size_t strlen(const char*);int strcmp(const char*,const char*);int strncmp(const char*,const char*,size_t);}
''',
 'type_traits':'''#pragma once
namespace std {template<class T>struct is_trivial {static constexpr bool value=__is_trivial(T);};template<class T>struct is_standard_layout {static constexpr bool value=__is_standard_layout(T);};}
''',
'nvs.h':'''#pragma once
#include <cstddef>
using nvs_handle_t=unsigned;
using esp_err_t=int;
constexpr int ESP_OK=0,ESP_ERR_NVS_NOT_FOUND=1,NVS_READWRITE=1;
struct nvs_stats_t {size_t used_entries,free_entries,available_entries,total_entries,namespace_count;};
int nvs_get_stats(const char*,nvs_stats_t*);
int nvs_open(const char*,int,nvs_handle_t*);int nvs_get_blob(nvs_handle_t,const char*,void*,size_t*);int nvs_set_blob(nvs_handle_t,const char*,const void*,size_t);int nvs_commit(nvs_handle_t);void nvs_close(nvs_handle_t);
'''}
MOCKS=r'''
extern "C" void* memcpy(void* d,const void* s,size_t n){for(size_t i=0;i<n;++i)static_cast<uint8_t*>(d)[i]=static_cast<const uint8_t*>(s)[i];return d;}
extern "C" void* memset(void* d,int v,size_t n){for(size_t i=0;i<n;++i)static_cast<uint8_t*>(d)[i]=uint8_t(v);return d;}
extern "C" int memcmp(const void* a,const void* b,size_t n){for(size_t i=0;i<n;++i){int d=static_cast<const uint8_t*>(a)[i]-static_cast<const uint8_t*>(b)[i];if(d)return d;}return 0;}
namespace std {
size_t strlen(const char* s){size_t n=0;while(s[n])++n;return n;}
int strcmp(const char*a,const char*b){while(*a&&*a==*b){++a;++b;}return uint8_t(*a)-uint8_t(*b);}
int strncmp(const char*a,const char*b,size_t n){for(size_t i=0;i<n;++i){int d=uint8_t(a[i])-uint8_t(b[i]);if(d||!a[i])return d;}return 0;}
int snprintf(char*out,size_t cap,const char* f,...){
 __builtin_va_list ap;__builtin_va_start(ap,f);size_t count=0;
 auto emit=[&](char c){if(count+1<cap)out[count]=c;++count;};
 while(*f){if(*f!='%'){emit(*f++);continue;}++f;bool zero=false;int width=0,length=0;
  if(*f=='0'){zero=true;++f;}if(*f=='*'){width=__builtin_va_arg(ap,int);++f;}else while(*f>='0'&&*f<='9'){width=width*10+*f++-'0';}
  while(*f=='l'){++length;++f;}char type=*f++;
  if(type=='s'){const char* s=__builtin_va_arg(ap,const char*);while(*s)emit(*s++);continue;}
  bool neg=false;uint64_t value=0;
  if(type=='d'){int64_t v=length>1?__builtin_va_arg(ap,long long):length?__builtin_va_arg(ap,long):__builtin_va_arg(ap,int);neg=v<0;value=neg?uint64_t(-v):uint64_t(v);}
  else value=length>1?__builtin_va_arg(ap,unsigned long long):length?__builtin_va_arg(ap,unsigned long):__builtin_va_arg(ap,unsigned);
  char buf[32];unsigned n=0,base=type=='x'?16:10;do{unsigned v=value%base;buf[n++]=v<10?'0'+v:'a'+v-10;value/=base;}while(value);
  if(neg)emit('-');while(int(n)<width-- )emit(zero?'0':' ');while(n)emit(buf[--n]);
 }
 __builtin_va_end(ap);if(cap)out[count<cap?count:cap-1]=0;return int(count);
}
}
static Hal hal;Hal& GetHAL(){return hal;}std::array<uint8_t,6> Hal::getFactoryMac(){return {0x24,1,2,3,4,5};}
static Network network;static Bluetooth ble;Network& GetNetworkQuota(){return network;}Bluetooth& GetCodexMicroBle(){return ble;}
Power Network::powerStats(){return {true,false,2};}Ble Bluetooth::diagnostics(){return {false,false};}
namespace MosaicoOta {bool busy(){return false;}bool healthPending(){return false;}bool tryStorageLease(){return true;}void releaseStorageLease(){}bool verifySignedImageDescriptor(const char*,VerifiedImageDescriptor&){return false;}}
bool Hal::gaugeThresholdAudit(bool,char*,size_t){return false;}
void* heap_caps_malloc(size_t,unsigned){return nullptr;}
static uint8_t gaugeRaw[131][4];static uint16_t operation=0xa6;static bool unknownOp=false,failPacketNoCommit=false;static uint16_t failReadAddress=0;static unsigned readCalls=0,defaultKeyCalls=0;static bool exitAutoSeal=true,commitAckLossOnce=false;static unsigned forwardPackets[131]{},reversePackets[131]{};static unsigned writes=0,enters=0,exits=0,seals=0,offsetWrites=0,unsafeSeals=0;static uint16_t packetAddress=0;static uint8_t packetRaw[4];static unsigned packetWidth=0;
static size_t fieldIndex(uint16_t a){for(size_t i=0;i<131;++i)if(scale_recovery::layout[i].address==a)return i;return 131;}
bool mockGaugeIdentity(){return true;}
void mockEngine(Hal::GaugeEngineSnapshot& s,uint8_t,uint8_t){s={};s.validMask=mosaico_gauge_engine::Identity|mosaico_gauge_engine::DefinitionMatch;}
bool mockGaugeWord(uint8_t r,uint16_t& out){if(r==0x3a){if(unknownOp)return false;out=operation;return true;}
 if(r==0x08){out=4100;return true;}if(r==0x06){out=3000;return true;}if(r==0x0c||r==0x14){out=0;return true;}
 if(r==0x3c||r==0x12){size_t i=fieldIndex(r==0x3c?0x929f:0x929d);out=uint16_t(gaugeRaw[i][0])<<8|gaugeRaw[i][1];return true;}
 if(r==0x10){uint16_t f=0;mockGaugeWord(0x12,f);out=f*80/100;return true;}if(r==0x2c){out=80;return true;}return false;}
void gauge_feed(){}void gauge_delay_ms(uint32_t){}int64_t esp_timer_get_time(){return 1000000;}
static bool readModel(uint16_t a,uint8_t* b){++readCalls;if(a==failReadAddress)return false;size_t i=fieldIndex(a);if(i==131)return false;for(unsigned j=0;j<36;++j)b[j]=0;b[0]=uint8_t(a);b[1]=uint8_t(a>>8);for(unsigned j=0;j<scale_recovery::layout[i].width;++j)b[j+2]=gaugeRaw[i][j];b[35]=36;b[34]=mosaico_gauge::macChecksum(b,34);return true;}
bool mockThreshold(mosaico_threshold::Selector a,uint8_t*b){return readModel(uint16_t(a),b);}bool mockCalibration(mosaico_calibration::Selector a,uint8_t*b){return readModel(uint16_t(a),b);}bool mockExtra(mosaico_public_extra::Selector a,uint8_t*b){return readModel(uint16_t(a),b);}
bool mockWaitSecurity(uint8_t sec){return ((operation>>1)&3)==sec;}bool mockWaitConfig(bool cfg){return bool(operation&0x400)==cfg;}
bool mockExit(){++exits;operation=exitAutoSeal?0xa6:0xa2;return true;}
int mockLoadAccess(mosaico_gauge::AccessJournal& a,const uint8_t*){a={};a.priorSecurity=3;a.unsealVerified=1;a.fullVerified=1;a.state=uint8_t(mosaico_gauge::AccessState::Restored);return 0;}
namespace {
bool realAccess(AccessWord w){if(w!=AccessWord::Seal)++defaultKeyCalls;if(w==AccessWord::UnsealSecond)operation=0xa4;if(w==AccessWord::Full)operation=0xa2;if(w==AccessWord::Seal){++seals;if(operation&0x401)++unsafeSeals;operation=0xa6;}return true;}
}
int i2c_master_transmit(bool,const uint8_t* p,size_t n,int){
 if(p[0]==0x3e){if(failPacketNoCommit)return 9;packetAddress=uint16_t(p[1])|uint16_t(p[2])<<8;packetWidth=unsigned(n-3);for(unsigned j=0;j<packetWidth;++j)packetRaw[j]=p[j+3];size_t idx=fieldIndex(packetAddress);if(idx<131){bool target=true;for(unsigned j=0;j<packetWidth;++j)target&=packetRaw[j]==fixture[idx].target[j];if(target)++forwardPackets[idx];else ++reversePackets[idx];}return 0;}
 if(p[0]==0x60){size_t i=fieldIndex(packetAddress);if(i==131||!scale_trial_owned(packetAddress))return 9;++writes;if(packetAddress==0x9180)++offsetWrites;for(unsigned j=0;j<packetWidth;++j)gaugeRaw[i][j]=packetRaw[j];if(commitAckLossOnce){commitAckLossOnce=false;return 9;}return 0;}return 9;}
struct MemoryStore {scale_journal::Blob blobs[2]{};bool present[2]{};
 scale_journal::ReadResult read(unsigned i,scale_journal::Blob& b){if(!present[i])return scale_journal::ReadResult::Missing;b=blobs[i];return scale_journal::ReadResult::Present;}
 bool write(unsigned i,const scale_journal::Blob& b){present[i]=true;blobs[i]=b;return true;}bool commit(){return true;}} memoryStore;
namespace mosaico_gauge_scale_journal {Status loadNvs(const std::array<uint8_t,6>& m,Workspace& w){return loadInto(memoryStore,m,w);}Status saveNvs(const Record&r,const WriteAdmission&a,Workspace&w){return saveInto(memoryStore,r,a,w);}}
static scale_journal::Blob freshBlob;static bool freshExists=false;static unsigned freshWrites=0;static size_t availableEntries=139;static bool statsError=false,imageCryptoValid=false;
int nvs_get_stats(const char*,nvs_stats_t* s){if(statsError)return 9;*s={491,9999,availableEntries,756,3};return 0;}
int nvs_open(const char*,int,nvs_handle_t*h){*h=1;return 0;}int nvs_get_blob(nvs_handle_t,const char*,void*p,size_t*n){if(!freshExists)return ESP_ERR_NVS_NOT_FOUND;if(*n<freshBlob.size())return 8;memcpy(p,freshBlob.data(),freshBlob.size());*n=freshBlob.size();return 0;}
int nvs_set_blob(nvs_handle_t,const char*,const void*p,size_t n){if(n!=freshBlob.size())return 8;memcpy(freshBlob.data(),p,n);freshExists=true;++freshWrites;return 0;}int nvs_commit(nvs_handle_t){return 0;}void nvs_close(nvs_handle_t){}
static ScaleRuntime runtimeStorage;static ScaleTrialStorage trialStorage;
static scale_journal::Record baseline(){scale_journal::Record r{};r.mac=GetHAL().getFactoryMac();auto&s=r.snapshot;s.identity={0x220,3,4};s.binding=scale_journal::binding(r.mac);s.layoutId=scale_recovery::Layout::Public131V1;s.count=131;s.generation=1;s.epoch=1;s.scale=scale_recovery::Scale::Two;
 for(size_t i=0;i<131;++i){auto&f=s.fields[i];f.address=fixture[i].address;f.width=fixture[i].width;for(unsigned j=0;j<4;++j){f.original[j]=fixture[i].original[j];f.target[j]=fixture[i].target[j];}}
 return r;}
static void reset(bool target=true){gauge=true;scale_known_safety_mismatch=false;runtimeStorage=ScaleRuntime{};trialStorage=ScaleTrialStorage{};scale_runtime=&runtimeStorage;scale_trial=&trialStorage;scale_mutation_in_progress=false;scale_load_status=scale_journal::Status::IoError;gauge_boot_info=Hal::GaugeBootReloadInfo{};memoryStore=MemoryStore{};
 auto r=baseline();scale_journal::saveInto(memoryStore,r,{true,true,true},runtimeStorage.journal);++r.snapshot.generation;++r.snapshot.epoch;r.snapshot.phase=scale_recovery::Phase::Pending;scale_journal::saveInto(memoryStore,r,{true,true,true},runtimeStorage.journal);++r.snapshot.generation;++r.snapshot.epoch;r.snapshot.phase=scale_recovery::Phase::Applied;scale_journal::saveInto(memoryStore,r,{true,true,true},runtimeStorage.journal);
 for(size_t i=0;i<131;++i)for(unsigned j=0;j<4;++j)gaugeRaw[i][j]=target?fixture[i].target[j]:fixture[i].original[j];operation=0xa6;unknownOp=false;defaultKeyCalls=0;exitAutoSeal=true;commitAckLossOnce=false;availableEntries=139;statsError=false;imageCryptoValid=false;for(unsigned i=0;i<131;++i){forwardPackets[i]=0;reversePackets[i]=0;}failPacketNoCommit=false;failReadAddress=0;readCalls=0;writes=enters=exits=seals=offsetWrites=unsafeSeals=0;freshExists=false;freshWrites=0;}
static bool ownedOriginal(){for(size_t i=0;i<131;++i)if(scale_trial_owned(fixture[i].address))for(unsigned j=0;j<fixture[i].width;++j)if(gaugeRaw[i][j]!=fixture[i].original[j])return false;return true;}
static bool immutable(){auto r=baseline();scale_load_locked(true);return scale_journal::sameRecoveryImage(r,runtimeStorage.journal.loaded.records[size_t(runtimeStorage.journal.loaded.selected)]);}
static mosaico_scale_trial::Result actualRestore(){if(!scale_trial_begin_locked())return mosaico_scale_trial::Result::Blocked;ScaleTrialBackend b;return mosaico_scale_trial::restore(b);}
static void prepareApplyModel(){reset(false);memoryStore=MemoryStore{};runtimeStorage=ScaleRuntime{};auto original=baseline();scale_journal::saveInto(memoryStore,original,{true,true,true},runtimeStorage.journal);scale_load_locked(true);
 runtimeStorage.originalVerifiedThisBoot=true;runtimeStorage.verifiedGeneration=1;runtimeStorage.verifiedEpoch=1;trialStorage.imagesSet=true;scale_both_images_verified=true;imageCryptoValid=true;
 for(unsigned i=0;i<2;++i){trialStorage.images[i].size=512;for(unsigned j=0;j<32;++j)trialStorage.images[i].hash[j]=0x42;std::snprintf(trialStorage.images[i].version,sizeof(trialStorage.images[i].version),i?"0.13.91-mosaico-ota":"0.13.90-mosaico-ota");}}
extern "C" int run_case(int id){
 if(id==0){reset();size_t o=fieldIndex(0x9180);gaugeRaw[o][0]=4;gaugeRaw[o][1]=0x84;
  if(actualRestore()!=mosaico_scale_trial::Result::Restored)return 1;
  if(!ownedOriginal()||writes!=17||offsetWrites||gaugeRaw[o][0]!=4||gaugeRaw[o][1]!=0x84||!immutable())return 2;
  if(!trialStorage.offsetSeen||trialStorage.offsetBefore!=0x484||trialStorage.offsetAfter!=0x484||trialStorage.offsetPrimary!=0xfff0)return 3;return 0;}
 if(id==1){reset();gaugeRaw[fieldIndex(0x9182)][1]^=1;
  if(actualRestore()!=mosaico_scale_trial::Result::FailedClosed||!ownedOriginal()||writes!=17||!trialStorage.untouchedMismatch||trialStorage.observed.count!=131||!immutable())return 11;
  if(runtimeStorage.journal.loaded.records[size_t(runtimeStorage.journal.loaded.selected)].snapshot.phase!=scale_recovery::Phase::Restoring)return 12;
  const unsigned oldEnter=enters,oldExit=exits,oldWrites=writes;trialStorage=ScaleTrialStorage{};runtimeStorage.loaded=false;
  if(actualRestore()!=mosaico_scale_trial::Result::FailedClosed||enters!=oldEnter||exits!=oldExit||writes!=oldWrites||!trialStorage.noOpRestore)return 13;return 0;}
 if(id==2){for(size_t i=0;i<131;++i)if(scale_trial_safety_field(fixture[i].address)){reset();gaugeRaw[i][0]^=1;
   if(actualRestore()!=mosaico_scale_trial::Result::FailedClosed||writes||enters||exits||!trialStorage.unsafeCalibration||!immutable())return int(100+i);}return 0;}
 if(id==3){reset();gaugeRaw[fieldIndex(0x9184)][0]^=0x40;
  if(actualRestore()!=mosaico_scale_trial::Result::FailedClosed||writes||enters||exits||!trialStorage.thirdValue||trialStorage.observed.count!=131||!immutable())return 31;return 0;}
 if(id==4){const uint16_t states[]={0x4a2,0xa2,0xa6,0xa3,0x82,0xa0,0xa4};for(unsigned i=0;i<7;++i){reset();operation=states[i];trialStorage.accessTouched=true;ScaleTrialBackend b;b.protect();b.protect();
   if(seals!=((i==1||i==6)?1U:0U)||exits||unsafeSeals)return 40+int(i);}
  reset();unknownOp=true;trialStorage.accessTouched=true;ScaleTrialBackend b;b.protect();return seals||exits?49:0;}
 if(id==5){reset();auto old=baseline();auto fresh=old;fresh.snapshot.fields[0].original[0]=4;fresh.snapshot.fields[0].original[1]=0x84;fresh.snapshot.fields[0].target[0]=4;fresh.snapshot.fields[0].target[1]=0x84;
  const auto originalCrc=scale_journal::imageDigest(old,false);if(!scale_reviewed_record(fresh)||scale_journal::imageDigest(fresh,false)==originalCrc)return 51;
  if(!scale_fresh_backup_locked(fresh,old)||freshWrites!=1||!runtimeStorage.freshBackupDurable||scale_journal::imageDigest(old,false)!=originalCrc)return 52;
  fresh.snapshot.generation=99;fresh.snapshot.epoch=99;if(!scale_fresh_backup_locked(fresh,old)||freshWrites!=1)return 53;
  auto collision=fresh;collision.snapshot.fields[1].original[0]^=1;scale_journal::encode(collision,freshBlob);
  if(scale_fresh_backup_locked(fresh,old)||freshWrites!=1)return 54;return 0;}
 if(id==6){reset(false);trialStorage.record=baseline();ScaleTrialBackend b;gaugeRaw[fieldIndex(0x9180)][0]=4;
  if(!b.observe(false))return 61;for(size_t i=0;i<131;++i)if(scale_trial_owned(fixture[i].address))for(unsigned j=0;j<4;++j)gaugeRaw[i][j]=fixture[i].target[j];
  if(!b.verify(false))return 62;gaugeRaw[fieldIndex(0x9182)][0]^=1;if(b.verify(false))return 63;return 0;}
 if(id==7){reset();char small[8];if(GetHAL().gaugeScaleRestore(small,sizeof(small))||writes||enters||exits)return 71;return 0;}
 if(id==8){for(unsigned mode=0;mode<2;++mode){reset();auto bad=baseline();if(mode==0)bad.snapshot.fields[0].target[0]^=1;else bad.snapshot.fields[fieldIndex(0x9182)].target[0]^=1;
   if(scale_reviewed_record(bad))return 81+int(mode);
   memoryStore=MemoryStore{};runtimeStorage=ScaleRuntime{};scale_journal::saveInto(memoryStore,bad,{true,true,true},runtimeStorage.journal);++bad.snapshot.generation;++bad.snapshot.epoch;bad.snapshot.phase=scale_recovery::Phase::Pending;scale_journal::saveInto(memoryStore,bad,{true,true,true},runtimeStorage.journal);
   if(actualRestore()!=mosaico_scale_trial::Result::Blocked||writes||enters||exits)return 83+int(mode);
  }return 0;}
 if(id==9){reset();gaugeRaw[fieldIndex(0x91b5)][0]^=1;if(actualRestore()!=mosaico_scale_trial::Result::FailedClosed||!scale_safety_mismatch_locked())return 91;
  scale_trial_begin_locked();if(!scale_safety_mismatch_locked()||writes)return 92;return 0;}
 if(id==10){reset();gauge_boot_info.status=Hal::GaugeBootReloadStatus::Critical;std::snprintf(gauge_boot_info.reason,sizeof(gauge_boot_info.reason),"boot_critical_scale_recovery_required");
  if(actualRestore()!=mosaico_scale_trial::Result::Restored||gauge_boot_info.status!=Hal::GaugeBootReloadStatus::Applied||!runtimeStorage.scaleBootCriticalRecovered||std::strcmp(runtimeStorage.recoveredCriticalSource,"boot_critical_scale_recovery_required"))return 101;
  reset();gauge_boot_info.status=Hal::GaugeBootReloadStatus::Critical;std::snprintf(gauge_boot_info.reason,sizeof(gauge_boot_info.reason),"other_critical");
  if(actualRestore()!=mosaico_scale_trial::Result::Restored||gauge_boot_info.status!=Hal::GaugeBootReloadStatus::Critical||runtimeStorage.scaleBootCriticalRecovered)return 102;return 0;}
 if(id==11){reset(false);memoryStore=MemoryStore{};runtimeStorage=ScaleRuntime{};auto r=baseline();scale_journal::saveInto(memoryStore,r,{true,true,true},runtimeStorage.journal);++r.snapshot.generation;++r.snapshot.epoch;r.snapshot.phase=scale_recovery::Phase::Pending;scale_journal::saveInto(memoryStore,r,{true,true,true},runtimeStorage.journal);
  if(!scale_trial_begin_locked())return 111;ScaleTrialBackend b;if(!b.open(false)||!b.observe(false)||!b.enter())return 112;failPacketNoCommit=true;
  if(b.write(false)||writes||!(operation&0x400))return 113;
  if(mosaico_scale_trial::restore(b)!=mosaico_scale_trial::Result::Restored||enters!=1||exits!=1||writes||!ownedOriginal()||!immutable()||unsafeSeals)return 114;return 0;}
 if(id==12){reset();failReadAddress=fixture[50].address;if(actualRestore()!=mosaico_scale_trial::Result::FailedClosed||trialStorage.observed.count!=50)return 121;
  const auto reads=readCalls;char out[320];if(!GetHAL().gaugeScaleObservedField(49,out,sizeof(out))||readCalls!=reads)return 122;
  if(GetHAL().gaugeScaleObservedField(50,out,sizeof(out))||readCalls!=reads)return 123;return 0;}
 if(id==13){reset();if(actualRestore()!=mosaico_scale_trial::Result::Restored)return 131;
  if(trialStorage.observedGeneration>=trialStorage.record.snapshot.generation||trialStorage.observedPhase!=scale_recovery::Phase::Restoring||std::strcmp(trialStorage.observedStage,"pre_exit_cfg_fa"))return 132;
  char out[320];const auto reads=readCalls;if(!GetHAL().gaugeScaleObservedField(0,out,sizeof(out))||reads!=readCalls)return 133;return 0;}
 if(id==14){reset();if(actualRestore()!=mosaico_scale_trial::Result::Restored)return 141;char out[1024];if(!GetHAL().gaugeScaleStatus(out,sizeof(out)))return 142;return 0;}
 if(id==15){reset(false);scale_load_locked(true);gaugeRaw[fieldIndex(0x91b5)][0]^=1;
  gauge_threshold_cached.readable=mosaico_threshold::fieldCount;gauge_threshold_cached.anchors=true;gauge_threshold_cached_generation=7;
  for(size_t i=0;i<mosaico_threshold::fieldCount;++i){auto a=uint16_t(mosaico_threshold::fields[i].selector);auto k=fieldIndex(a);gauge_threshold_cached.raw[i]=uint16_t(gaugeRaw[k][0])<<8|gaugeRaw[k][1];}
  gauge_calibration_cached.readable=mosaico_calibration::fieldCount;
  for(size_t i=0;i<mosaico_calibration::fieldCount;++i){auto k=fieldIndex(uint16_t(mosaico_calibration::fields[i].selector));for(unsigned j=0;j<4;++j)gauge_calibration_cached.raw[i][j]=gaugeRaw[k][j];}
  gauge_public_extra_cached.readable=mosaico_public_extra::fieldCount;gauge_public_extra_cached.complete=true;gauge_public_extra_generation=7;
  for(size_t i=0;i<mosaico_public_extra::fieldCount;++i){auto k=fieldIndex(uint16_t(mosaico_public_extra::fields[i].selector));gauge_public_extra_cached.raw[i]=mosaico_public_extra::fields[i].width==1?gaugeRaw[k][0]:uint16_t(gaugeRaw[k][0])<<8|gaugeRaw[k][1];}
  if(scale_cache_original_locked(runtimeStorage.journal.scratch)||!scale_safety_mismatch_locked()||writes)return 151;return 0;}
 if(id==16){reset(false);auto fresh=baseline();fresh.snapshot.fields[0].original[0]=4;fresh.snapshot.fields[0].original[1]=0x84;fresh.snapshot.fields[0].target[0]=4;fresh.snapshot.fields[0].target[1]=0x84;
  const auto actual=scale_journal::imageDigest(fresh,false);memoryStore=MemoryStore{};runtimeStorage=ScaleRuntime{};
  if(scale_journal::saveInto(memoryStore,fresh,{true,true,true},runtimeStorage.journal)!=scale_journal::Status::Ready)return 161;
  ++fresh.snapshot.generation;++fresh.snapshot.epoch;fresh.snapshot.phase=scale_recovery::Phase::Pending;
  if(scale_journal::saveInto(memoryStore,fresh,{true,true,true},runtimeStorage.journal)!=scale_journal::Status::Ready||scale_load_locked(true)!=scale_journal::Status::Ready)return 162;
  for(unsigned slot=0;slot<2;++slot){scale_journal::Record decoded{};if(!scale_journal::decode(memoryStore.blobs[slot],fresh.mac,decoded)||scale_journal::imageDigest(decoded,false)!=actual||decoded.snapshot.fields[0].original[0]!=4||decoded.snapshot.fields[0].original[1]!=0x84)return 163;}
  return actual==0x91336fc0?164:0;}
 if(id==17){for(unsigned mode=0;mode<2;++mode){prepareApplyModel();exitAutoSeal=mode==0;
   if(!scale_trial_begin_locked())return 171;ScaleTrialBackend b;if(mosaico_scale_trial::apply(b)!=mosaico_scale_trial::Result::Applied||writes!=17||!runtimeStorage.appliedVerifiedThisBoot)return 172;
   if(mode==0?!trialStorage.sealedStandardFallback:!trialStorage.postExitFull131)return 173;
   if(actualRestore()!=mosaico_scale_trial::Result::Restored||writes!=34||!ownedOriginal()||!immutable())return 174;
   for(unsigned i=0;i<131;++i)if(scale_trial_owned(fixture[i].address)&&(forwardPackets[i]!=1||reversePackets[i]!=1))return 175;
  }return 0;}
 if(id==18){prepareApplyModel();commitAckLossOnce=true;if(!scale_trial_begin_locked())return 181;ScaleTrialBackend b;
  if(mosaico_scale_trial::apply(b)!=mosaico_scale_trial::Result::Restored||writes!=2||!ownedOriginal()||!immutable())return 182;
  for(unsigned i=0;i<131;++i)if(forwardPackets[i]>1||reversePackets[i]>1)return 183;return 0;}
 if(id==19){for(unsigned mode=0;mode<4;++mode){prepareApplyModel();availableEntries=mode==0?139:mode==1?85:mode==2?72:0;statsError=mode==3;char out[1024];
   const bool applied=GetHAL().gaugeScaleApply(out,sizeof(out));if(mode<2){if(!applied||writes!=17)return 191;}
   else if(applied||writes||enters||exits||defaultKeyCalls||trialStorage.applyAttempted)return 192;
  }return 0;}
 if(id==20){reset();auto old=baseline();auto fresh=old;fresh.snapshot.fields[0].original[0]=4;fresh.snapshot.fields[0].original[1]=0x84;fresh.snapshot.fields[0].target[0]=4;fresh.snapshot.fields[0].target[1]=0x84;
  availableEntries=72;if(scale_fresh_backup_locked(fresh,old)||freshWrites||defaultKeyCalls||writes)return 201;
  availableEntries=85;if(!scale_fresh_backup_locked(fresh,old)||freshWrites!=1)return 202;
  availableEntries=0;if(!scale_fresh_backup_locked(fresh,old)||freshWrites!=1)return 203;return 0;}
 if(id==21){reset();gaugeRaw[fieldIndex(0x9180)][0]=0x80;gaugeRaw[fieldIndex(0x9180)][1]=0;
  if(actualRestore()!=mosaico_scale_trial::Result::FailedClosed||!ownedOriginal()||writes!=17||offsetWrites||!trialStorage.invalidOffset||runtimeStorage.originalVerifiedThisBoot)return 211;
  auto invalid=baseline();invalid.snapshot.fields[0].original[0]=0x80;invalid.snapshot.fields[0].original[1]=0;invalid.snapshot.fields[0].target[0]=0x80;invalid.snapshot.fields[0].target[1]=0;
  if(scale_reviewed_record(invalid))return 212;return 0;}
 return 999;
}
'''
RV_SUPPORT=r'''
extern "C" uint64_t __udivdi3(uint64_t a,uint64_t b){uint64_t r=0,q=0;for(int i=63;i>=0;--i){r=(r<<1)|((a>>i)&1);if(r>=b){r-=b;q|=uint64_t(1)<<i;}}return q;}
extern "C" uint64_t __umoddi3(uint64_t a,uint64_t b){uint64_t r=0;for(int i=63;i>=0;--i){r=(r<<1)|((a>>i)&1);if(r>=b)r-=b;}return r;}
static esp_partition_t imageParts[2]={{1024},{1024}};
const esp_partition_t* esp_partition_find_first(int,int subtype,const char*){return imageCryptoValid?&imageParts[subtype==ESP_PARTITION_SUBTYPE_APP_OTA_1?1:0]:nullptr;}
int esp_partition_read(const esp_partition_t*,size_t,void* p,size_t n){memset(p,0,n);return imageCryptoValid?0:1;}
int esp_ota_get_state_partition(const esp_partition_t*,esp_ota_img_states_t* s){*s=ESP_OTA_IMG_VALID;return imageCryptoValid?0:1;}
int esp_ota_get_partition_description(const esp_partition_t* p,esp_app_desc_t* d){*d={};std::snprintf(d->project_name,sizeof(d->project_name),"Stopwatch-Mosaico");std::snprintf(d->version,sizeof(d->version),p==&imageParts[0]?"0.13.90-mosaico-ota":"0.13.91-mosaico-ota");d->app_elf_sha256[0]=1;return imageCryptoValid?0:1;}
psa_status_t psa_crypto_init(){return imageCryptoValid?PSA_SUCCESS:-1;}
psa_status_t psa_hash_setup(psa_hash_operation_t*,psa_algorithm_t algorithm){return imageCryptoValid&&algorithm==PSA_ALG_SHA_256?PSA_SUCCESS:-1;}
psa_status_t psa_hash_update(psa_hash_operation_t*,const unsigned char*,size_t){return imageCryptoValid?PSA_SUCCESS:-1;}
psa_status_t psa_hash_finish(psa_hash_operation_t*,unsigned char* out,size_t capacity,size_t* length){if(capacity<32)return -1;for(unsigned i=0;i<32;++i)out[i]=0x42;*length=32;return imageCryptoValid?PSA_SUCCESS:-1;}
psa_status_t psa_hash_abort(psa_hash_operation_t*){return PSA_SUCCESS;}
int mbedtls_base64_decode(unsigned char*,size_t,size_t*,const unsigned char*,size_t){return -1;}
cJSON* cJSON_ParseWithLength(const char*,size_t){return nullptr;}bool cJSON_IsArray(const cJSON*){return false;}int cJSON_GetArraySize(const cJSON*){return 0;}cJSON* cJSON_GetArrayItem(const cJSON*,int){return nullptr;}char* cJSON_PrintUnformatted(const cJSON*){return nullptr;}void cJSON_free(void*){}void cJSON_Delete(cJSON*){}
extern "C" {volatile int test_results[22]={-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999,-999};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<22;++i)test_results[i]=run_case(i);test_done();}

'''
class ActualBackendTests(unittest.TestCase):
 def test_actual_backend_rv32_execution(self):
  private=ROOT/'.artifacts/private/mosaico/scale2-reviewed-target-001.json'
  if not private.is_file():self.skipTest('private reviewed131 fixture unavailable')
  data=json.loads(private.read_text())['all_fields'];rows=[]
  for f in data:
   pad=lambda key:','.join(str(v) for v in bytes.fromhex(f[key]).ljust(4,b'\0'))
   rows.append('{0x%s,%d,{%s},{%s}}'%(f['address'],f['width'],pad('originalBE'),pad('targetBE')))
  capture={};test=trial_module.TrialTests();test.compile=lambda code,*args: capture.update(code=code);test.test_actual_backend_isolated_compiles_and_stack()
  code=capture['code']
  # Execute the production backend, replacing only external transport bindings.
  code=code.replace('bool gauge_access_word(AccessWord){return mockGaugeIdentity();}','bool realAccess(AccessWord);bool gauge_access_word(AccessWord w){return realAccess(w);}')
  code=code.replace('bool gauge_control(GaugeCommand){return mockGaugeIdentity();}','bool enterModel();bool gauge_control(GaugeCommand){return enterModel();}')
  # The captured trial fixture already removed the isolated runtime stubs.
  self.assertIn('namespace {void native_recovery_revoke_locked(){}}',code)
  self.assertNotIn('bool scale_trial_boot_restore_locked(){return false;}',code)
  fixture='struct Fixture{uint16_t address;uint8_t width;uint8_t original[4],target[4];};\nstatic constexpr Fixture fixture[]={'+','.join(rows)+'};\n'
  code+='\n'+fixture+MOCKS+'\nnamespace {bool enterModel(){++enters;operation=0x4a2;return true;}}\n'+RV_SUPPORT
  RUN.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.actual-scale-backend-',dir=ROOT) as td:
   td=Path(td)
   for name,text in {**STUBS,**HEADERS}.items():p=td/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
   src=td/'actual.cpp';src.write_text(code)
   private_run=ROOT/'.artifacts/private/mosaico/scale-backend-tests'/RUN.name;private_run.mkdir(parents=True,exist_ok=True);wasm=private_run/'actual-backend.elf'
   args=[str(CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(td),'-I'+str(ROOT/'main/hal/mosaico'),str(src),'--ld-path='+str(CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(wasm)]
   result=subprocess.run(args,capture_output=True,text=True);(RUN/'compile.log').write_text(result.stdout+result.stderr)
   self.assertEqual(result.returncode,0,str(RUN/'compile.log'))
   import os,sys,struct
   site=Path(os.environ.get('UNICORN_PYTHONPATH',str(ROOT/'.artifacts/tools/unicorn-2.1.4-private-001/package')))
   if site.is_dir():sys.path.insert(0,str(site))
   try:
    from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32
    from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC
   except ImportError:
    (RUN/'execution.log').write_text('NOT EXECUTED: mature Unicorn RV32 simulator unavailable; ELF compilation only.\n')
    self.skipTest('ELF compiled; actual execution awaits root-provided Unicorn')
   elf=wasm.read_bytes();entry=struct.unpack_from('<I',elf,24)[0]
   phoff=struct.unpack_from('<I',elf,28)[0];phsize,phnum=struct.unpack_from('<HH',elf,42)
   uc=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);uc.mem_map(0,32*1024*1024)
   for i in range(phnum):
    kind,off,addr,_,filesize,_,_,_=struct.unpack_from('<8I',elf,phoff+i*phsize)
    if kind==1:uc.mem_write(addr,elf[off:off+filesize])
   shoff=struct.unpack_from('<I',elf,32)[0];shsize,shnum=struct.unpack_from('<HH',elf,46);symbols={}
   for i in range(shnum):
    sh=struct.unpack_from('<10I',elf,shoff+i*shsize)
    if sh[1]!=2:continue
    strings=struct.unpack_from('<10I',elf,shoff+sh[6]*shsize);text=elf[strings[4]:strings[4]+strings[5]]
    for pos in range(sh[4],sh[4]+sh[5],sh[9]):
     name,value,_,_,_,_=struct.unpack_from('<IIIBBH',elf,pos);label=text[name:].split(b'\0',1)[0].decode()
     symbols[label]=value
   uc.reg_write(UC_RISCV_REG_SP,0x1fffff0)
   uc.emu_start(entry,symbols['test_done'],timeout=20000000,count=1000000000)
   values=struct.unpack('<22i',bytes(uc.mem_read(symbols['test_results'],88)))
   (RUN/'execution.log').write_text('Actual production backend; official Unicorn RV32; mocked transport/NVS/crypto/locks.\n'+f'ELF_PRIVATE={wasm}\nELF_SHA256={hashlib.sha256(elf).hexdigest()}\ncompletion_pc={uc.reg_read(UC_RISCV_REG_PC):x} expected_done={symbols["test_done"]:x} timeout_us=20000000 instruction_cap=1000000000\n'+''.join(f'case={i} result={value}\n' for i,value in enumerate(values)))
   self.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done'],'Execution budget exhausted before test_done; see execution.log')
   self.assertEqual(values,(0,)*22,str(RUN/'execution.log'))
if __name__=='__main__':unittest.main()
