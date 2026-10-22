"""Execute isolated production GSJ3 runtime on RV32; mocks are not hardware/crypto certification."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,subprocess,tempfile,unittest,os,sys,struct
import test_mosaico_gauge_scale_backend as old
import test_mosaico_gauge_scale_trial as trial
from test_mosaico_factory_scale_model import PRIVATE,EXPECTED
ROOT=old.ROOT
RUN=ROOT/'.artifacts/private/mosaico/gauge95r4-recovery-safety-tests-001/runtime'
SUPPORT=r'''
#include "mosaico_factory_scale_journal.h"
#include "mosaico_factory_audit_model.h"
namespace scale_journal=mosaico_gauge_scale_journal;
namespace scale_recovery=mosaico_gauge_recovery;
namespace factory_scale_journal=mosaico_factory_scale_journal;
namespace factory=mosaico_factory_audit;
namespace native_recovery {struct Evidence{};bool exact(const Evidence&,const uint8_t*){return true;}}
namespace {
bool native_evidence_locked(native_recovery::Evidence&){return true;}
void native_recovery_revoke_locked(){}
scale_journal::Status scale_load_locked(bool){return scale_journal::Status::Missing;}
struct FactoryWorkspace {bool attempted=false,revalidation=false,complete=false,live=false;factory::Journal record{};factory::BackupBytes raw{};};
FactoryWorkspace* factory_workspace=nullptr;
static mosaico_factory_scale_budget::Usage reuseUsageFixture{};
const esp_partition_t* esp_ota_get_running_partition();
bool factory_runtime();bool factory_identity(){return gauge_identity();}
bool factory_physical(unsigned,factory::Standard* =nullptr){return factory_runtime();}
bool factory_read(uint16_t,uint8_t (&)[36]);
}
'''
CASES=r'''
static FactoryScaleRuntime runtimeStorage;
static factory_scale_journal::Record baseline(){factory_scale_journal::Record r{};r.profile=mosaico_factory_scale::Profile::Factory3000RollbackToNominal65Virtual130;r.mac=GetHAL().getFactoryMac();auto&s=r.snapshot;s.identity={0x220,3,4};s.binding=scale_journal::binding(r.mac);s.layoutId=scale_recovery::Layout::Public131V1;s.count=131;s.generation=1;s.epoch=1;s.scale=scale_recovery::Scale::Two;
 for(size_t i=0;i<131;++i){auto&f=s.fields[i];f.address=fixture[i].address;f.width=fixture[i].width;for(unsigned j=0;j<4;++j){f.original[j]=fixture[i].original[j];f.target[j]=fixture[i].target[j];}}
 return r;}
static void reset(bool target=false){reuseUsageFixture={};gauge=true;runtimeStorage=FactoryScaleRuntime{};factory_scale_rt=&runtimeStorage;factory_scale_status=factory_scale_journal::Status::IoError;factory_scale_safety_fault=false;memoryStore=MemoryStore{};
 auto r=baseline();factory_scale_journal::saveInto(memoryStore,r,{true,true,true},runtimeStorage.journal);
 if(target){++r.snapshot.generation;++r.snapshot.epoch;r.snapshot.phase=scale_recovery::Phase::Pending;factory_scale_journal::saveInto(memoryStore,r,{true,true,true},runtimeStorage.journal);++r.snapshot.generation;++r.snapshot.epoch;r.snapshot.phase=scale_recovery::Phase::Applied;factory_scale_journal::saveInto(memoryStore,r,{true,true,true},runtimeStorage.journal);}
 for(size_t i=0;i<131;++i)for(unsigned j=0;j<4;++j)gaugeRaw[i][j]=target?fixture[i].target[j]:fixture[i].original[j];
 operation=0xa6;unknownOp=false;defaultKeyCalls=0;exitAutoSeal=true;commitAckLossOnce=false;availableEntries=95;statsError=false;imageCryptoValid=true;sdkValid=true;uiLocked=true;wifiRunning=bleAdvertising=bleConnected=otaBusy=otaHealth=false;powerPhase=2;nowUs=1000000;failPacketNoCommit=false;failReadAddress=0;readCalls=0;writes=enters=exits=seals=offsetWrites=unsafeSeals=0;freshExists=false;freshWrites=0;intentBeforeKeys=true;durableReject=false;dropPrekey=false;rejectPhaseOnce=-1;factory_workspace=nullptr;leaseDepth=0;leaseViolation=false;revalidationUnderLease=false;revalidationCalls=0;leaseAllowed=true;
 for(unsigned i=0;i<131;++i){forwardPackets[i]=reversePackets[i]=0;}
 factory_scale_load_locked(true);runtimeStorage.verifiedGeneration=r.snapshot.generation;runtimeStorage.verifiedEpoch=r.snapshot.epoch;runtimeStorage.preparedThisBoot=!target;runtimeStorage.preparedUs=nowUs;runtimeStorage.imagesSet=true;
 for(unsigned i=0;i<2;++i){runtimeStorage.images[i].size=512;for(unsigned j=0;j<32;++j)runtimeStorage.images[i].hash[j]=0x42;std::snprintf(runtimeStorage.images[i].version,sizeof(runtimeStorage.images[i].version),i?"0.13.95-r12-mosaico-ota":"0.13.95-r11-mosaico-ota");}}
static mosaico_scale_trial::Result apply(){if(!factory_scale_begin_locked())return mosaico_scale_trial::Result::Blocked;FactoryScaleBackend b;auto r=mosaico_scale_trial::apply(b);runtimeStorage.operation=false;factory_scale_update_protection_locked();return r;}
static mosaico_scale_trial::Result restore(){if(!factory_scale_begin_locked())return mosaico_scale_trial::Result::Blocked;FactoryScaleBackend b;auto r=mosaico_scale_trial::restore(b);runtimeStorage.operation=false;factory_scale_update_protection_locked();return r;}
extern "C" int run_case(int id){
 reset();
 if(id==0){if(factory_scale_context_locked().factor||factory_scale_context_locked().verified||factory_scale_boot_fence_locked()||!factory_scale_present_locked()||factory_scale_protected())return 1;return 0;}
 if(id==1){if(apply()!=mosaico_scale_trial::Result::Applied||writes!=17||!intentBeforeKeys||!runtimeStorage.active)return 2;auto c=factory_scale_context_locked();if(c.factor!=2||!c.verified)return 3;return 0;}
 if(id==2){if(apply()!=mosaico_scale_trial::Result::Applied||restore()!=mosaico_scale_trial::Result::Restored||writes!=34)return 4;auto c=factory_scale_context_locked();return c.factor||c.verified||factory_scale_protected()?5:0;}
 if(id==3){durableReject=true;if(apply()!=mosaico_scale_trial::Result::FailedClosed||defaultKeyCalls||writes)return 6;return 0;}
 if(id==4){nowUs+=120000001;if(apply()!=mosaico_scale_trial::Result::Blocked||defaultKeyCalls||writes)return 7;return 0;}
 if(id==5){runtimeStorage.imagesSet=false;if(apply()!=mosaico_scale_trial::Result::Blocked||defaultKeyCalls||writes)return 8;return 0;}
 if(id==6){reset(true);sdkValid=false;for(int n=0;n<2;++n){nowUs+=1000000;factory_scale_poll_locked();}if(defaultKeyCalls||writes||!factory_scale_protected())return 9;return 0;}
 if(id==7){reset(true);gaugeRaw[fieldIndex(0x91de)][0]=11;if(restore()!=mosaico_scale_trial::Result::FailedClosed||writes||!runtimeStorage.thirdValue)return 10;return 0;}
 if(id==8){reset(true);gaugeRaw[fieldIndex(0x9180)][1]^=1;if(restore()!=mosaico_scale_trial::Result::FailedClosed||writes||!factory_scale_safety_fault)return 11;return 0;}
 if(id==9){reset(true);operation|=0x400;if(restore()!=mosaico_scale_trial::Result::FailedClosed||writes||seals||exits)return 12;return 0;}
 if(id==10){reset(true);operation=0x4a2;if(restore()!=mosaico_scale_trial::Result::Restored||writes!=17||defaultKeyCalls)return 13;return 0;}
 if(id==11){dropPrekey=true;if(apply()!=mosaico_scale_trial::Result::FailedClosed||defaultKeyCalls||writes)return 14;return 0;}
 if(id==12){availableEntries=73;if(apply()!=mosaico_scale_trial::Result::Blocked||defaultKeyCalls||writes)return 15;return 0;}
 if(id==13){reset(true);runtimeStorage.active=true;runtimeStorage.appliedUs=nowUs-300000001;runtimeStorage.appliedVerifiedThisBoot=true;factory_scale_poll_locked();if(writes!=17||runtimeStorage.active||factory_scale_context_locked().factor)return 16;return 0;}
 if(id==14){commitAckLossOnce=true;if(apply()!=mosaico_scale_trial::Result::Restored||writes!=2||!intentBeforeKeys)return 17;return 0;}
 if(id==15){reset(true);operation|=1;if(restore()!=mosaico_scale_trial::Result::FailedClosed||writes||seals||exits)return 18;return 0;}
 if(id==16){reset(true);gaugeRaw[fieldIndex(0x9182)][1]^=1;if(restore()!=mosaico_scale_trial::Result::FailedClosed||writes!=17||!runtimeStorage.untouchedMismatch||factory_scale_context_locked().factor)return 19;return 0;}
 if(id==17){factory_workspace=nullptr;char out[1024];if(!GetHAL().gaugeFactoryScalePrepare(out,sizeof(out))||revalidationCalls!=1||revalidationUnderLease||leaseViolation||leaseDepth||!runtimeStorage.preparedThisBoot)return 20;return 0;}
 if(id==18){factory_workspace=nullptr;char out[1024];if(!GetHAL().gaugeFactoryScalePrepare(out,sizeof(out)))return 21;unsigned calls=revalidationCalls;if(!GetHAL().gaugeFactoryScalePrepare(out,sizeof(out))||revalidationCalls!=calls)return 22;return 0;}
 if(id==19){factory_workspace=nullptr;char out[1024];if(!GetHAL().gaugeFactoryScalePrepare(out,sizeof(out)))return 23;nowUs+=120000001;if(GetHAL().gaugeFactoryScalePrepare(out,sizeof(out))||revalidationCalls!=1)return 24;return 0;}
 if(id==20){factory_workspace=nullptr;char out[1024];if(!GetHAL().gaugeFactoryScalePrepare(out,sizeof(out)))return 25;nowUs+=120000001;if(apply()!=mosaico_scale_trial::Result::Blocked||defaultKeyCalls||writes)return 26;return 0;}
 if(id==21){if(!factory_scale_maintenance_original_cached_locked()||!factory_scale_maintenance_cached_safe_locked())return 27;runtimeStorage.operation=true;if(factory_scale_maintenance_cached_safe_locked())return 28;return 0;}
 if(id==22){reset(true);if(factory_scale_maintenance_original_cached_locked()||factory_scale_maintenance_cached_safe_locked())return 29;return 0;}
 if(id==23){factory_scale_rt=nullptr;if(factory_scale_maintenance_cached_safe_locked())return 30;factory_scale_rt=&runtimeStorage;memoryStore=MemoryStore{};runtimeStorage=FactoryScaleRuntime{};factory_scale_status=factory_scale_journal::Status::IoError;if(factory_scale_maintenance_cached_safe_locked())return 31;factory_scale_load_locked(true);if(!factory_scale_maintenance_cached_safe_locked())return 32;runtimeStorage.everSeen=true;if(factory_scale_maintenance_cached_safe_locked())return 33;return 0;}
 if(id==24){runtimeStorage.untouchedMismatch=true;if(factory_scale_maintenance_cached_safe_locked()||factory_scale_maintenance_original_cached_locked())return 34;return 0;}
 if(id==25){rejectPhaseOnce=int(scale_recovery::Phase::Applied);if(apply()!=mosaico_scale_trial::Result::Restored||writes!=34||factory_scale_context_locked().factor||runtimeStorage.active||runtimeStorage.journal.loaded.records[size_t(runtimeStorage.journal.loaded.selected)].snapshot.phase!=scale_recovery::Phase::Original)return 35;return 0;}
 if(id==26){runtimeStorage.imagesSet=true;char out[1024];if(GetHAL().gaugeFactoryScaleImages("invalid_base64",out,sizeof(out))||runtimeStorage.imagesSet)return 36;if(apply()!=mosaico_scale_trial::Result::Blocked||defaultKeyCalls||writes)return 37;return 0;}
 if(id==27){runtimeStorage.imagesSet=true;imageCryptoValid=false;if(apply()!=mosaico_scale_trial::Result::Blocked||runtimeStorage.imagesSet||defaultKeyCalls||writes)return 38;return 0;}
 if(id>=28&&id<=33){capMode=true;capReject=false;capVerified=0;cJSON signature{};signature.valuestring=const_cast<char*>("cap");auto image=runtimeStorage.images[0];
  std::snprintf(expectedCanonical,sizeof(expectedCanonical),"MOSAICO-FACTORY-SCALE-CAP-v1\ne13f6e771556ad0a507112532f3a2e63139ff6c7433a2ebc1cea022cf8df30fa\n0.13.95-r11-mosaico-ota\n512\n4242424242424242424242424242424242424242424242424242424242424242\n");
  if(id==29)image.version[0]='1';if(id==30)++image.size;if(id==31)image.hash[0]^=1;if(id==32)capReject=true;if(id==33)signature.valuestring=nullptr;
  bool ok=factory_scale_capability(&signature,image);if(id==28?(!ok||capVerified!=1):ok)return 39;return 0;}
 if(id>=34&&id<=38){cJSON object{},image{},signature{},extra{};object.child=&image;image.next=&signature;image.string=const_cast<char*>("image");signature.string=const_cast<char*>("signature");
  if(id==35){signature.next=&extra;extra.string=const_cast<char*>("contract");}if(id==36)signature.string=const_cast<char*>("capability");if(id==37)signature.string=image.string;if(id==38)image.next=nullptr;
  bool ok=factory_scale_two_fields(&object,"image","signature");if(id==34?!ok:ok)return 40;return 0;}
 if(id==39){const char* rejected[]={"0.13.95-r1-mosaico-ota","0.13.95-r2-mosaico-ota","0.13.95-r3-mosaico-ota","0.13.95-r4-mosaico-ota","0.13.95-r5-mosaico-ota","0.13.95-r6-mosaico-ota","0.13.95-r7-mosaico-ota","0.13.95-r8-mosaico-ota","0.13.95-r9-mosaico-ota","0.13.95-r10-mosaico-ota","0.13.95-r20-mosaico-ota","0.13.95-r12-mosaico-ota-extra",""};for(const char* v:rejected)if(factory_scale_rt_version(v))return 41;if(!factory_scale_rt_version("0.13.95-r11-mosaico-ota")||!factory_scale_rt_version("0.13.95-r12-mosaico-ota"))return 42;return 0;}
 if(id==40){reset(true);runtimeStorage.imagesSet=false;imageCryptoValid=false;if(restore()!=mosaico_scale_trial::Result::Restored||writes!=17||factory_scale_context_locked().factor||factory_scale_protected())return 43;return 0;}
 if(id==41){reset(true);runtimeStorage.imagesSet=false;imageCryptoValid=false;factory_scale_poll_locked();if(writes!=17||factory_scale_protected()||!runtimeStorage.bootRestoreAttempted)return 44;return 0;}
 if(id==42){std::snprintf(runtimeStorage.images[0].version,sizeof(runtimeStorage.images[0].version),"0.13.95-r1-mosaico-ota");if(apply()!=mosaico_scale_trial::Result::Blocked||runtimeStorage.imagesSet||writes||defaultKeyCalls)return 45;return 0;}
 if(id==43){if(apply()!=mosaico_scale_trial::Result::Applied)return 46;uiLocked=false;nowUs+=300000000;factory_scale_poll_locked();if(writes!=34||runtimeStorage.active||factory_scale_context_locked().factor)return 47;unsigned keys=defaultKeyCalls;factory_scale_poll_locked();return defaultKeyCalls!=keys?48:0;}
 if(id>=44&&id<=50){if(apply()!=mosaico_scale_trial::Result::Applied)return 49;
  if(id==44)wifiRunning=true;if(id==45)bleAdvertising=true;if(id==46)bleConnected=true;if(id==47)powerPhase=1;if(id==48)sdkValid=false;if(id==49)otaBusy=true;if(id==50)otaHealth=true;
  nowUs+=300000000;unsigned keys=defaultKeyCalls;unsigned reads=readCalls;
  if(factory_scale_context_locked().factor)return 50;factory_scale_poll_locked();
  if(runtimeStorage.active||runtimeStorage.appliedVerifiedThisBoot||!factory_scale_protected()||factory_scale_context_locked().factor||defaultKeyCalls!=keys||writes!=17||readCalls!=reads)return 51;
  if(runtimeStorage.journal.loaded.records[size_t(runtimeStorage.journal.loaded.selected)].snapshot.phase!=scale_recovery::Phase::Applied)return 52;
  wifiRunning=bleAdvertising=bleConnected=otaBusy=otaHealth=false;powerPhase=2;sdkValid=true;uiLocked=false;factory_scale_poll_locked();if(writes!=34||factory_scale_protected())return 53;keys=defaultKeyCalls;nowUs+=1000000;factory_scale_poll_locked();return keys!=defaultKeyCalls?54:0;}
 if(id==51){reset(true);uiLocked=false;char out[1536];return !GetHAL().gaugeFactoryScaleRestore(out,sizeof(out))||writes!=17?55:0;}
 if(id==52){uiLocked=false;if(apply()!=mosaico_scale_trial::Result::Blocked||writes||defaultKeyCalls)return 56;return 0;}
 if(id>=53&&id<=56){if(apply()!=mosaico_scale_trial::Result::Applied)return 57;
  runtimeStorage.nextPollUs=INT64_MAX;
  if(id==53)nowUs=-1;if(id==54)runtimeStorage.appliedUs=0;if(id==55){runtimeStorage.lastPollUs=nowUs+100;nowUs+=50;}if(id==56)nowUs+=300000000;
  if(factory_scale_context_locked().factor)return 58;unsigned keys=defaultKeyCalls;wifiRunning=true;factory_scale_poll_locked();
  if(runtimeStorage.active||runtimeStorage.appliedVerifiedThisBoot||defaultKeyCalls!=keys||!factory_scale_protected())return 59;
  nowUs=500000000;wifiRunning=false;factory_scale_poll_locked();return writes!=34?60:0;}
 if(id==57){reset(true);failPacketNoCommit=true;factory_scale_poll_locked();unsigned keys=defaultKeyCalls,w=writes;nowUs+=2000000;factory_scale_poll_locked();factory_scale_poll_locked();return defaultKeyCalls!=keys||writes!=w?61:0;}
 if(id==58){if(apply()!=mosaico_scale_trial::Result::Applied)return 62;nowUs=INT64_MAX;factory_scale_poll_locked();return runtimeStorage.active||factory_scale_context_locked().factor||writes!=34?63:0;}
 if(id==59){if(apply()!=mosaico_scale_trial::Result::Applied)return 64;nowUs+=300000000;leaseAllowed=false;unsigned keys=defaultKeyCalls;if(factory_scale_context_locked().factor)return 65;factory_scale_poll_locked();if(runtimeStorage.active||runtimeStorage.appliedVerifiedThisBoot||writes!=17||defaultKeyCalls!=keys||!factory_scale_protected())return 66;leaseAllowed=true;nowUs+=1000000;factory_scale_poll_locked();return writes!=34||factory_scale_protected()?67:0;}
 if(id==60){if(apply()!=mosaico_scale_trial::Result::Applied)return 68;nowUs=0;unsigned keys=defaultKeyCalls;if(factory_scale_context_locked().factor)return 69;factory_scale_poll_locked();if(runtimeStorage.active||runtimeStorage.appliedVerifiedThisBoot||writes!=17||keys!=defaultKeyCalls||!factory_scale_protected())return 70;nowUs=500000000;factory_scale_poll_locked();return writes!=34||factory_scale_protected()?71:0;}
 return 999;
}
'''
class FactoryRuntimeTests(unittest.TestCase):
 def test_source_contract(self):
  s=(ROOT/'main/hal/mosaico/mosaico_factory_scale_runtime.inc').read_text()
  for v in ['scale_runtime->','scale_trial->','native_reconcile','native_recovery_revoke_locked(','nvs_erase','esp_partition_write','GaugeCommand::Reset']:self.assertNotIn(v,s)
  self.assertLess(s.index('gaugeFactoryAuditRevalidate(t.details'),s.index('FactoryScaleStorageLease lease;if(!lease.held){t.reason="prepare_post_FA_OTA_lease"'))
  self.assertIn('preKeyStats.available_entries<54',s)
  self.assertIn('factory_scale_time_valid(t,esp_timer_get_time())',s)
  self.assertIn('restore?factory_scale_recovery_runtime():factory_runtime()',s)
  admit=s.split('    bool admit(bool restore){',1)[1].split('    bool intent(',1)[0]
  restoring=admit.split('        if(restore){',1)[1].split('        if(!factory_scale_space_locked(',1)[0]
  self.assertNotIn('images_current',restoring)
  self.assertIn('return !factory_scale_rt->thirdValue',restoring)
  journal=(ROOT/'main/hal/mosaico/mosaico_factory_scale_journal_model.h').read_text()
  record=journal.split('struct Record {',1)[1].split('};',1)[0]
  self.assertNotIn('version',record);self.assertNotIn('images',record)

 def test_actual_runtime_rv32(self):
  self.assertEqual(hashlib.sha256(PRIVATE.read_bytes()).hexdigest(),EXPECTED)
  data=json.loads(PRIVATE.read_bytes())['fields'];changes={0x9184:0x7f73b645,0x9188:0x940aa522,0x91de:10,0x91fb:400,0x9201:200,0x920e:300,0x9210:350,0x9217:20,0x9228:120,0x922a:150,0x922c:80,0x923c:0xec,0x9264:3000,0x926b:400,0x929d:130,0x929f:130,0x92ab:434}
  rows=[]
  for f in data:
   a=int(f['address'],16);w=f['width'];raw=bytes.fromhex(f['rawBE']);target=changes[a].to_bytes(w,'big') if a in changes else raw
   rows.append('{0x%04x,%d,{%s},{%s}}'%(a,w,','.join(map(str,raw.ljust(4,b'\0'))),','.join(map(str,target.ljust(4,b'\0')))))
  capture={};t=trial.TrialTests();t.compile=lambda code,*args:capture.update(code=code);t.test_actual_backend_isolated_compiles_and_stack();code=capture['code']
  code=code[:code.index('#include "mosaico_gauge_scale_runtime.inc"')]
  code=code.replace('bool gauge_access_word(AccessWord){return mockGaugeIdentity();}','bool realAccess(AccessWord);bool gauge_access_word(AccessWord w){return realAccess(w);}')
  code=code.replace('bool gauge_control(GaugeCommand){return mockGaugeIdentity();}','bool enterModel();bool gauge_control(GaugeCommand){return enterModel();}')
  code=code.replace(' bool gaugeThresholdAudit(bool,char*,size_t);',' bool gaugeThresholdAudit(bool,char*,size_t);bool gaugeFactoryAuditRevalidate(char*,size_t);bool gaugeFactoryScaleStatus(char*,size_t);bool gaugeFactoryScalePrepare(char*,size_t);bool gaugeFactoryScaleImages(const char*,char*,size_t);bool gaugeFactoryScaleApply(char*,size_t);bool gaugeFactoryScaleRestore(char*,size_t);bool gaugeFactoryScaleObservedField(size_t,char*,size_t);bool gaugeFactoryScaleObservedPage(size_t,char*,size_t);')
  code+='\n#include <esp_ota_ops.h>\n#include <mbedtls/base64.h>\n#include <psa/crypto.h>\n#include <cJSON.h>\n'+SUPPORT+'\n#include "mosaico_gauge_scale_trial.inc"\n#include "mosaico_factory_scale_runtime.inc"\n'
  # Only portable coordinator from legacy include; no old runtime/backend.
  code=code.replace('#include "mosaico_gauge_scale_trial.inc"','#define MOSAICO_SCALE_TRIAL_MODEL_ONLY\n#include "mosaico_gauge_scale_trial.inc"')
  fixture='struct Fixture{uint16_t address;uint8_t width;uint8_t original[4],target[4];};\nstatic constexpr Fixture fixture[]={'+','.join(rows)+'};\n'
  mocks=old.MOCKS[:old.MOCKS.index('static ScaleRuntime runtimeStorage;')]
  mocks=mocks.replace('scale_trial_owned','factory_scale_rt_owned').replace('scale_journal::Blob','factory_scale_journal::Blob').replace('scale_journal::ReadResult','factory_scale_journal::ReadResult')
  a=mocks.index('namespace mosaico_gauge_scale_journal {Status loadNvs');b=mocks.index('static factory_scale_journal::Blob freshBlob',a)
  mocks=mocks[:a]+'''static unsigned leaseDepth=0,revalidationCalls=0;static bool leaseViolation=false,revalidationUnderLease=false;static bool uiLocked=true,wifiRunning=false,bleAdvertising=false,bleConnected=false,otaBusy=false,otaHealth=false;static int powerPhase=2;static bool sdkValid=true,intentBeforeKeys=true,durableReject=false,dropPrekey=false;static int64_t nowUs=1000000;static int rejectPhaseOnce=-1;
namespace mosaico_factory_scale_journal {mosaico_factory_scale_budget::Usage reuseUsageNvs(const Loaded&){return reuseUsageFixture;}Status loadNvs(const std::array<uint8_t,6>& m,StorageWorkspace& w){return loadInto(memoryStore,m,w);}Status saveNvs(const Record&r,const WriteAdmission&a,StorageWorkspace&w){if(durableReject)return fail(w,Status::CommitFailed);if(rejectPhaseOnce==int(r.snapshot.phase)){rejectPhaseOnce=-1;return fail(w,Status::CommitFailed);}auto s=saveInto(memoryStore,r,a,w);return s;}}
'''+mocks[b:]
  mocks=mocks.replace('a.priorSecurity=3;a.unsealVerified=1;a.fullVerified=1;', 'a.magic=0x47414331U;a.version=1;a.deviceType=0x220;a.unitMah=1;for(unsigned i=0;i<6;++i)a.mac[i]=GetHAL().getFactoryMac()[i];a.priorSecurity=3;a.lastSecurity=3;a.baseDesign=3000;a.baseFcc=3000;a.operationBefore=0xa6;a.unsealAttempted=1;a.fullAttempted=1;a.unsealVerified=1;a.fullVerified=1;').replace('a.state=uint8_t(mosaico_gauge::AccessState::Restored);return 0;', 'a.state=uint8_t(mosaico_gauge::AccessState::Restored);mosaico_gauge::seal(a);return 0;')
  mocks=mocks.replace('int64_t esp_timer_get_time(){return 1000000;}','int64_t esp_timer_get_time(){return nowUs;}')
  mocks=mocks.replace('static unsigned leaseDepth=0,revalidationCalls=0;static bool leaseViolation=false,revalidationUnderLease=false;static bool uiLocked=true,wifiRunning=false,bleAdvertising=false,bleConnected=false,otaBusy=false,otaHealth=false;static int powerPhase=2;static bool sdkValid=true,intentBeforeKeys=true,durableReject=false,dropPrekey=false;static int64_t nowUs=1000000;static int rejectPhaseOnce=-1;','')
  mocks='static bool leaseAllowed=true;static bool capMode=false,capReject=false;static unsigned capVerified=0;static char expectedCanonical[300]{};\nstatic unsigned leaseDepth=0,revalidationCalls=0;static bool leaseViolation=false,revalidationUnderLease=false;static bool uiLocked=true,wifiRunning=false,bleAdvertising=false,bleConnected=false,otaBusy=false,otaHealth=false;static int powerPhase=2;static bool sdkValid=true,intentBeforeKeys=true,durableReject=false,dropPrekey=false;static int64_t nowUs=1000000;static int rejectPhaseOnce=-1;\n'+mocks
  mocks=mocks.replace('if(w!=AccessWord::Seal)++defaultKeyCalls;','if(w!=AccessWord::Seal){++defaultKeyCalls;intentBeforeKeys &= factory_scale_rt->journal.loaded.selected>=0&&factory_scale_rt->journal.loaded.records[size_t(factory_scale_rt->journal.loaded.selected)].snapshot.phase!=scale_recovery::Phase::Original;}')
  mocks=mocks.replace('*s={491,9999,availableEntries,756,3};','unsigned present=unsigned(memoryStore.present[0])+unsigned(memoryStore.present[1]);*s={491,9999,dropPrekey&&present==2?53:availableEntries-(present==2?37:19),756,3};')
  # availableEntries expresses fresh-store95; with Original available76, Pending58.
  mocks=mocks.replace('bool Hal::gaugeThresholdAudit(bool,char*,size_t){return false;}','bool Hal::gaugeThresholdAudit(bool,char*,size_t){return false;}bool Hal::gaugeFactoryAuditRevalidate(char*,size_t){++revalidationCalls;revalidationUnderLease|=leaseDepth!=0;static FactoryWorkspace audit;factory_workspace=&audit;audit={};audit.attempted=audit.revalidation=audit.complete=audit.live=true;audit.record.count=131;audit.record.sourceCrc=mosaico_factory_scale::factorySourceCrc;for(unsigned i=0;i<131;++i)for(unsigned k=0;k<fixture[i].width;++k)audit.raw[67+i*7+k]=fixture[i].original[k];return true;}')
  mocks=mocks.replace('bool tryStorageLease(){return true;}void releaseStorageLease(){}','bool tryStorageLease(){if(!leaseAllowed)return false;if(leaseDepth){leaseViolation=true;return false;}++leaseDepth;return true;}void releaseStorageLease(){if(leaseDepth)--leaseDepth;else leaseViolation=true;}')
  support=old.RV_SUPPORT[:old.RV_SUPPORT.index('extern "C" {volatile int test_results')]
  support=support.replace('0.13.90-mosaico-ota','0.13.95-r11-mosaico-ota').replace('0.13.91-mosaico-ota','0.13.95-r12-mosaico-ota')
  support=support.replace('int mbedtls_base64_decode(unsigned char*,size_t,size_t*,const unsigned char*,size_t){return -1;}', 'int mbedtls_base64_decode(unsigned char* out,size_t cap,size_t* n,const unsigned char* input,size_t size){if(!capMode||size!=3||std::strncmp(reinterpret_cast<const char*>(input),"cap",3)||cap<8)return -1;for(unsigned i=0;i<8;++i)out[i]=0x55;*n=8;return 0;}')
  actual_runtime=(ROOT/'main/hal/mosaico/mosaico_factory_audit.inc').read_text().split('bool factory_runtime(){',1)[1].split('\n',1)[0]
  support+='''\nnamespace {bool factory_runtime(){'''+actual_runtime+'''\nbool factory_read(uint16_t a,uint8_t (&b)[36]){return readModel(a,b);}bool enterModel(){++enters;operation=0x4a2;return true;}}\n'''
  mocks=mocks.replace('Power Network::powerStats(){return {true,false,2};}Ble Bluetooth::diagnostics(){return {false,false};}', 'Power Network::powerStats(){return {uiLocked,wifiRunning,powerPhase};}Ble Bluetooth::diagnostics(){return {bleAdvertising,bleConnected};}')
  mocks=mocks.replace('bool busy(){return false;}bool healthPending(){return false;}', 'bool busy(){return otaBusy;}bool healthPending(){return otaHealth;}')
  support=support.replace('*s=ESP_OTA_IMG_VALID;return imageCryptoValid?0:1;', '*s=sdkValid?ESP_OTA_IMG_VALID:static_cast<esp_ota_img_states_t>(1);return 0;')
  support+='\nnamespace {const esp_partition_t* esp_ota_get_running_partition(){return &imageParts[0];}}\n'
  code+=fixture+mocks+CASES+support
  code+='\nextern "C" int64_t __divdi3(int64_t a,int64_t b){bool n=(a<0)!=(b<0);uint64_t q=__udivdi3(a<0?uint64_t(-a):uint64_t(a),b<0?uint64_t(-b):uint64_t(b));return n?-int64_t(q):int64_t(q);}'
  code+='''\nextern "C" {volatile int test_results[61]={};}extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}extern "C" void _start(){for(int i=0;i<61;++i)test_results[i]=run_case(i);test_done();}\n'''
  headers={**trial.STUBS,**old.HEADERS}
  headers['mbedtls/base64.h']+='\nint mbedtls_base64_encode(unsigned char*,size_t,size_t*,const unsigned char*,size_t);\n'
  headers['cstdint']+='\n#define INT64_MAX 0x7fffffffffffffffLL\n'
  headers['cJSON.h']='''#pragma once
#include <cstddef>
struct cJSON {cJSON* child; cJSON* next;char* string;char* valuestring;};
cJSON* cJSON_ParseWithLength(const char*,size_t);cJSON* cJSON_ParseWithOpts(const char*,const char**,bool);bool cJSON_IsArray(const cJSON*);bool cJSON_IsObject(const cJSON*);bool cJSON_IsString(const cJSON*);int cJSON_GetArraySize(const cJSON*);cJSON* cJSON_GetArrayItem(const cJSON*,int);cJSON* cJSON_GetObjectItemCaseSensitive(const cJSON*,const char*);char* cJSON_PrintUnformatted(const cJSON*);void cJSON_free(void*);void cJSON_Delete(cJSON*);
'''
  headers['mbedtls/pk.h']='''#pragma once
#include <cstddef>
int mockCapabilityVerify(const unsigned char*,size_t,const unsigned char*,size_t);
struct mbedtls_pk_context{};constexpr int MBEDTLS_MD_SHA256=1,PSA_KEY_USAGE_VERIFY_HASH=1;
#define MBEDTLS_PK_ALG_ECDSA(x) (x)
inline void mbedtls_pk_init(mbedtls_pk_context*){}inline void mbedtls_pk_free(mbedtls_pk_context*){}inline int mbedtls_pk_parse_public_key(mbedtls_pk_context*,const unsigned char*,size_t){return 0;}inline bool mbedtls_pk_can_do_psa(mbedtls_pk_context*,unsigned,unsigned){return true;}inline size_t mbedtls_pk_get_bitlen(mbedtls_pk_context*){return 256;}inline int mbedtls_pk_verify(mbedtls_pk_context*,int,const unsigned char* hash,size_t h,const unsigned char* sig,size_t n){return mockCapabilityVerify(hash,h,sig,n);}
'''
  headers['ota/ota_public_key.h']='inline constexpr char MosaicoOtaPublicKey[]="test";'
  headers['psa/crypto.h']+='\npsa_status_t psa_hash_compute(psa_algorithm_t,const unsigned char*,size_t,unsigned char*,size_t,size_t*);\n'
  code+='''
psa_status_t psa_hash_compute(psa_algorithm_t,const unsigned char* text,size_t n,unsigned char* hash,size_t cap,size_t* length){if(!capMode||n!=std::strlen(expectedCanonical)||std::memcmp(text,expectedCanonical,n)||cap<32)return -1;for(unsigned i=0;i<32;++i)hash[i]=0x42;*length=32;return 0;}
int mockCapabilityVerify(const unsigned char* hash,size_t h,const unsigned char* sig,size_t n){++capVerified;if(!capMode||capReject||h!=32||n!=8)return -1;for(unsigned i=0;i<32;++i)if(hash[i]!=0x42)return -1;for(unsigned i=0;i<8;++i)if(sig[i]!=0x55)return -1;return 0;}
cJSON* cJSON_ParseWithOpts(const char*,const char**,bool){return nullptr;}bool cJSON_IsObject(const cJSON* node){return node&&node->child;}bool cJSON_IsString(const cJSON* node){return node&&node->valuestring;}cJSON* cJSON_GetObjectItemCaseSensitive(const cJSON*,const char*){return nullptr;}
'''
  RUN.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.factory-runtime-',dir=ROOT) as td:
   td=Path(td)
   for name,text in headers.items():p=td/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
   src=td/'runtime.cpp';src.write_text(code);private_run=RUN;private_run.mkdir(parents=True,exist_ok=True);elfpath=private_run/'runtime.elf'
   args=[str(old.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-fstack-usage','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(td),'-I'+str(ROOT/'main/hal/mosaico'),str(src),'--ld-path='+str(old.CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elfpath)]
   result=subprocess.run(args,capture_output=True,text=True,env={**os.environ,"TMP":str(td),"TEMP":str(td)});(RUN/'compile.log').write_text(result.stdout+result.stderr);self.assertEqual(result.returncode,0,str(RUN/'compile.log'))
   site=ROOT/'.artifacts/tools/unicorn-2.1.4-private-001/package';sys.path.insert(0,str(site));from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32;from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC
   stack=elfpath.with_suffix('.su').read_text();(RUN/'runtime.su').write_text(stack)
   for line in stack.splitlines():
    if 'mosaico_factory_scale_runtime.inc:' in line:self.assertLessEqual(int(line.split('\t')[1]),512,line)
   elf=elfpath.read_bytes();entry=struct.unpack_from('<I',elf,24)[0];phoff=struct.unpack_from('<I',elf,28)[0];phsize,phnum=struct.unpack_from('<HH',elf,42);uc=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);uc.mem_map(0,32*1024*1024)
   for i in range(phnum):
    kind,off,addr,_,filesize,_,_,_=struct.unpack_from('<8I',elf,phoff+i*phsize)
    if kind==1:uc.mem_write(addr,elf[off:off+filesize])
   shoff=struct.unpack_from('<I',elf,32)[0];shsize,shnum=struct.unpack_from('<HH',elf,46);symbols={}
   for i in range(shnum):
    sh=struct.unpack_from('<10I',elf,shoff+i*shsize)
    if sh[1]!=2:continue
    strings=struct.unpack_from('<10I',elf,shoff+sh[6]*shsize);text=elf[strings[4]:strings[4]+strings[5]]
    for pos in range(sh[4],sh[4]+sh[5],sh[9]):
     name,value,_,_,_,_=struct.unpack_from('<IIIBBH',elf,pos);symbols[text[name:].split(b'\0',1)[0].decode()]=value
   uc.reg_write(UC_RISCV_REG_SP,0x1fffff0);uc.emu_start(entry,symbols['test_done'],timeout=20000000,count=1000000000);values=struct.unpack('<61i',bytes(uc.mem_read(symbols['test_results'],244)))
   (RUN/'execution.log').write_text('Production GSJ3 runtime; RV32 ISA; mock transport/storage/crypto/locks.\n'+f'ELF_SHA256={hashlib.sha256(elf).hexdigest()}\n'+''.join(f'case={i} result={v}\n' for i,v in enumerate(values)))
   self.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done']);self.assertEqual(values,(0,)*61,str(RUN/'execution.log'))
if __name__=='__main__':unittest.main()
