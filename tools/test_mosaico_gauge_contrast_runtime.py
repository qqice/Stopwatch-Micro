"""Execute isolated production GCT1 runtime on RV32; mocks are not hardware/crypto certification."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,subprocess,tempfile,unittest,os,sys,struct
import test_mosaico_gauge_scale_backend as old
import test_mosaico_gauge_scale_trial as trial
from test_mosaico_factory_scale_model import PRIVATE,EXPECTED
ROOT=old.ROOT
RUN=ROOT/'.artifacts/contrast-runtime-tests'
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
 operation=0xa6;unknownOp=false;defaultKeyCalls=0;exitAutoSeal=true;commitAckLossOnce=false;availableEntries=95;statsError=false;imageCryptoValid=true;sdkValid=true;invalidSdkSlot=-1;partitionReads[0]=partitionReads[1]=0;uiLocked=true;wifiRunning=bleAdvertising=bleConnected=otaBusy=otaHealth=false;powerPhase=2;nowUs=1000000;failPacketNoCommit=false;failReadAddress=0;readCalls=0;writes=enters=exits=seals=offsetWrites=unsafeSeals=0;freshExists=false;freshWrites=0;intentBeforeKeys=true;durableReject=false;dropPrekey=false;rejectPhaseOnce=-1;factory_workspace=nullptr;leaseDepth=0;leaseViolation=false;revalidationUnderLease=false;revalidationCalls=0;leaseAllowed=true;
 for(unsigned i=0;i<131;++i){forwardPackets[i]=reversePackets[i]=0;}
 factory_scale_load_locked(true);runtimeStorage.verifiedGeneration=r.snapshot.generation;runtimeStorage.verifiedEpoch=r.snapshot.epoch;runtimeStorage.preparedThisBoot=!target;runtimeStorage.preparedUs=nowUs;runtimeStorage.imagesSet=true;
 for(unsigned i=0;i<2;++i){runtimeStorage.images[i].size=512;for(unsigned j=0;j<32;++j)runtimeStorage.images[i].hash[j]=0x42;std::snprintf(runtimeStorage.images[i].version,sizeof(runtimeStorage.images[i].version),i?"0.13.95-r12-mosaico-ota":"0.13.95-r11-mosaico-ota");}}
static mosaico_scale_trial::Result apply(){if(!factory_scale_begin_locked())return mosaico_scale_trial::Result::Blocked;FactoryScaleBackend b;auto r=mosaico_scale_trial::apply(b);runtimeStorage.operation=false;factory_scale_update_protection_locked();return r;}
static mosaico_scale_trial::Result restore(){if(!factory_scale_begin_locked())return mosaico_scale_trial::Result::Blocked;FactoryScaleBackend b;auto r=mosaico_scale_trial::restore(b);runtimeStorage.operation=false;factory_scale_update_protection_locked();return r;}

static ContrastRuntime contrastStorage;
struct ContrastMemory {mosaico_gauge_contrast_journal::Blob blobs[2]{};bool present[2]{};unsigned writes=0,reads=0;
 mosaico_gauge_contrast_journal::ReadResult read(unsigned i,mosaico_gauge_contrast_journal::Blob& b){++reads;if(!present[i])return mosaico_gauge_contrast_journal::ReadResult::Missing;b=blobs[i];return mosaico_gauge_contrast_journal::ReadResult::Present;}
 bool write(unsigned i,const mosaico_gauge_contrast_journal::Blob& b){++writes;blobs[i]=b;present[i]=true;return true;}bool available(size_t& n){n=95;return true;}bool commit(){return !durableReject;}} contrastMemory;
namespace mosaico_gauge_contrast_journal {
Status loadNvs(const std::array<uint8_t,6>& mac,Workspace& w){return loadInto(contrastMemory,mac,w);}
Status saveNvs(const Record& r,const Admission& a,Workspace& w){return saveInto(contrastMemory,r,a,w);}}
static void resetContrast(bool b=false){reset();contrastStorage=ContrastRuntime{};contrast_rt=&contrastStorage;contrast_status=contrast_journal::Status::IoError;contrast_safety_fault=false;contrastMemory={};
 auto& r=contrastStorage.record;contrast_model::Record compact{};compact.mac=GetHAL().getFactoryMac();compact.source=contrast_model::sourceFixture;compact.generation=1;compact.epoch=1;compact.arm=b?contrast_model::Arm::B:contrast_model::Arm::C;contrast_runtime_store::expand(compact,r);
 contrast_journal::saveNvs(r,{true,true,true},contrastStorage.journal);contrastStorage.loaded=false;contrast_load_locked(true);
 contrastStorage.preparedThisBoot=true;contrastStorage.preparedUs=nowUs;contrastStorage.imagesSet=true;contrastStorage.verifiedGeneration=1;contrastStorage.verifiedEpoch=1;
 for(unsigned i=0;i<2;++i){contrastStorage.images[i]=runtimeStorage.images[i];std::snprintf(contrastStorage.images[i].version,sizeof(contrastStorage.images[i].version),i?"0.13.95-r14-mosaico-ota":"0.13.95-r13-mosaico-ota");}
}
static auto contrastApply(){contrast_begin_locked();ContrastBackend b;auto result=mosaico_gauge_contrast_trial::apply(b);contrastStorage.operation=false;contrast_update_protection_locked();return result;}
static auto contrastRestore(){contrast_begin_locked();ContrastBackend b;auto result=mosaico_gauge_contrast_trial::restore(b);contrastStorage.operation=false;contrast_update_protection_locked();return result;}
extern "C" int run_case(int id){resetContrast(id==2);
 if(id==0){auto c=contrast_context_locked();return c.factor||c.verified||contrast_protected()?1:0;}
 if(id==1||id==2){if(contrastApply()!=mosaico_gauge_contrast_trial::Result::Applied)return 2;if(writes!=(id==2?2:3)||contrast_context_locked().factor||contrast_context_locked().verified)return 3;if(contrastRestore()!=mosaico_gauge_contrast_trial::Result::Restored)return 4;return writes!=(id==2?4:6)?5:0;}
 if(id==3){durableReject=true;contrastApply();return writes||defaultKeyCalls?6:0;}
 if(id==4){contrastStorage.imagesSet=false;contrastApply();return writes||defaultKeyCalls?7:0;}
 if(id==5){nowUs+=120000001;contrastApply();return writes||defaultKeyCalls?8:0;}
 if(id==6){gaugeRaw[0][0]^=1;contrastApply();return writes?9:0;}
 if(id==7){if(contrastApply()!=mosaico_gauge_contrast_trial::Result::Applied)return 10;nowUs+=720000000;contrast_poll_locked();return writes!=6||contrast_context_locked().factor?11:0;}
 if(id==8){if(contrast_rt_version("0.13.95-r12-mosaico-ota")||!contrast_rt_version("0.13.95-r13-mosaico-ota"))return 12;return 0;}
 if(id==9){contrastMemory.blobs[0][0]^=1;contrast_load_locked(true);return !contrast_protected()||contrast_audit_allowed_locked()?13:0;}
 if(id==10){contrastApply();if(contrastRestore()!=mosaico_gauge_contrast_trial::Result::Restored)return 14;contrastStorage.preparedThisBoot=true;contrastApply();return writes!=6?15:0;}
 if(id==11){contrastStorage.imagesSet=true;char out[1536];GetHAL().gaugeContrastImages("bad",out,sizeof(out));return contrastStorage.imagesSet?16:0;}
 if(id>=12&&id<=16){char out[2048];GetHAL().gaugeFactoryAuditRevalidate(out,sizeof(out));contrastStorage.revalidatedUs=nowUs;
  if(contrastApply()!=mosaico_gauge_contrast_trial::Result::Applied)return 17;
  nowUs+=480000000;
  if(id==14){return GetHAL().gaugeContrastPrepare("B",out,sizeof(out))?18:0;}
  if(contrastRestore()!=mosaico_gauge_contrast_trial::Result::Restored)return 19;
  if(id==13)nowUs+=120000001;
  if(id==15)contrastStorage.observed.raw[0][0]^=1;
  if(id==16)imageCryptoValid=false;
  bool ok=GetHAL().gaugeContrastPrepare("B",out,sizeof(out));
  if(id!=12)return ok?20:0;
  if(!ok||contrastStorage.record.arm!=contrast_model::Arm::B||revalidationCalls!=1)return 21;
  if(contrastApply()!=mosaico_gauge_contrast_trial::Result::Applied||writes!=8)return 22;
  return contrastRestore()!=mosaico_gauge_contrast_trial::Result::Restored||writes!=10?23:0;
 }
 if(id==17){contrastApply();nowUs+=720000000;wifiRunning=true;unsigned before=defaultKeyCalls;contrast_poll_locked();return defaultKeyCalls!=before||contrastStorage.active||!contrast_protected()?24:0;}
 // Cached maintenance getter is exercised directly, never a mocked provider.
 if(id==18){contrastMemory={};contrastStorage=ContrastRuntime{};contrast_load_locked(true);unsigned reads=readCalls,nr=contrastMemory.reads,nw=contrastMemory.writes;
  bool safe=contrast_maintenance_cached_safe_locked();return !safe||readCalls!=reads||contrastMemory.reads!=nr||contrastMemory.writes!=nw||defaultKeyCalls?25:0;}
 if(id==19){return !contrast_maintenance_cached_safe_locked()?26:0;}
 if(id==20){contrastStorage.record.snapshot.phase=scale_recovery::Phase::Pending;++contrastStorage.record.snapshot.generation;++contrastStorage.record.snapshot.epoch;contrast_journal::saveNvs(contrastStorage.record,{true,true,true},contrastStorage.journal);contrast_load_locked(true);return contrast_maintenance_cached_safe_locked()?27:0;}
 if(id==21){contrastMemory.blobs[0][0]^=1;contrast_load_locked(true);return contrast_maintenance_cached_safe_locked()?28:0;}
 if(id==22){contrastStorage.appliedUs=nowUs;return contrast_maintenance_cached_safe_locked()?29:0;}
 if(id==23){auto old=baseline();old.snapshot.phase=scale_recovery::Phase::Pending;++old.snapshot.generation;++old.snapshot.epoch;factory_scale_journal::saveInto(memoryStore,old,{true,true,true},runtimeStorage.journal);
  ContrastBackend backend;unsigned reads=readCalls;bool ok=backend.admit(false);return ok||defaultKeyCalls||writes||readCalls!=reads?30:0;}
 // The crypto mock accepts only the exact independent canonical byte string.
 // These test canonical binding, not cryptographic implementation authenticity.
 if(id>=24&&id<=31){capMode=true;capReject=false;capVerified=0;cJSON signature{};signature.valuestring=const_cast<char*>("cap");auto image=contrastStorage.images[0];
  std::snprintf(expectedCanonical,sizeof(expectedCanonical),"MOSAICO-GAUGE-CONTRAST-CAP-v1\nda8c79bcf2a17f01a3294394d7ca9b527d1a623a45fa1bc04c730a988cd69bdb\n0.13.95-r13-mosaico-ota\n512\n4242424242424242424242424242424242424242424242424242424242424242\n");
  if(id==25)image.version[0]='1';if(id==26)++image.size;if(id==27)image.hash[0]^=1;
  if(id==28)expectedCanonical[0]='X';if(id==29)expectedCanonical[40]^=1;if(id==30)capReject=true;if(id==31)signature.valuestring=nullptr;
  bool ok=contrast_capability(&signature,image);return id==24?(!ok||capVerified!=1?31:0):(ok?32:0);}

 if(id>=38&&id<=40){if(contrastApply()!=mosaico_gauge_contrast_trial::Result::Applied)return 35;
  const uint16_t address=id==38?0x9184:id==39?0x9188:0x91de;const auto index=fieldIndex(address);gaugeRaw[index][0]^=1;
  const uint8_t drift=gaugeRaw[index][0];const unsigned beforeWrites=writes,beforeEnter=enters;
  const auto result=contrastRestore();
  if(result!=mosaico_gauge_contrast_trial::Result::FailedClosed||writes!=beforeWrites||enters!=beforeEnter||gaugeRaw[index][0]!=drift)return 36;
  if(!contrastStorage.unsafeCalibration||!contrast_protected()||contrast_status!=contrast_journal::Status::Ready||contrastStorage.journal.loaded.selected<0||contrastStorage.journal.loaded.records[size_t(contrastStorage.journal.loaded.selected)].snapshot.phase!=scale_recovery::Phase::Restoring)return 37;
  return 0;
 }
 // imagesSet cannot bypass rehash/description/SDK_VALID checks at admission.
 if(id>=32&&id<=37){
  if(id==32)contrastStorage.images[0].hash[0]^=1;
  if(id==33)contrastStorage.images[1].hash[0]^=1;
  if(id==34)invalidSdkSlot=0;
  if(id==35)invalidSdkSlot=1;
  if(id==36)contrastStorage.images[1].size=1025;
  ContrastBackend backend;bool ok=id==34||id==35?contrast_rt_images_current():backend.admit(false);
  if(id==37)return !ok||!partitionReads[0]||!partitionReads[1]?33:0;
  return ok||defaultKeyCalls||writes||contrastStorage.imagesSet?34:0;
 }


 return 0;
}
'''
class ContrastRuntimeTests(unittest.TestCase):
 def test_source_contract(self):
  text=(ROOT/'main/hal/mosaico/mosaico_gauge_contrast_runtime.inc').read_text()
  self.assertIn('never_publish_physical_units',text)
  self.assertNotIn('c.factor=2',text)
  self.assertIn('available_entries>=56',text)
  self.assertIn('0.13.95-r13-mosaico-ota',text)
  self.assertIn('0.13.95-r14-mosaico-ota',text)
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
  code=code.replace(' bool gaugeThresholdAudit(bool,char*,size_t);',' bool gaugeThresholdAudit(bool,char*,size_t);bool gaugeContrastStatus(char*,size_t);bool gaugeContrastPrepare(const char*,char*,size_t);bool gaugeContrastImages(const char*,char*,size_t);bool gaugeContrastApply(char*,size_t);bool gaugeContrastRestore(char*,size_t);bool gaugeContrastObservedPage(size_t,char*,size_t);bool gaugeFactoryAuditRevalidate(char*,size_t);bool gaugeFactoryScaleStatus(char*,size_t);bool gaugeFactoryScalePrepare(char*,size_t);bool gaugeFactoryScaleImages(const char*,char*,size_t);bool gaugeFactoryScaleApply(char*,size_t);bool gaugeFactoryScaleRestore(char*,size_t);bool gaugeFactoryScaleObservedField(size_t,char*,size_t);bool gaugeFactoryScaleObservedPage(size_t,char*,size_t);')
  code+='\n#include <esp_ota_ops.h>\n#include <mbedtls/base64.h>\n#include <psa/crypto.h>\n#include <cJSON.h>\n'+SUPPORT+'\n#include "mosaico_gauge_scale_trial.inc"\n#include "mosaico_factory_scale_runtime.inc"\n#include "mosaico_gauge_contrast_runtime.inc"\n'
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
  mocks='static int invalidSdkSlot=-1;static unsigned partitionReads[2]{};static bool leaseAllowed=true;static bool capMode=false,capReject=false;static unsigned capVerified=0;static char expectedCanonical[300]{};\nstatic unsigned leaseDepth=0,revalidationCalls=0;static bool leaseViolation=false,revalidationUnderLease=false;static bool uiLocked=true,wifiRunning=false,bleAdvertising=false,bleConnected=false,otaBusy=false,otaHealth=false;static int powerPhase=2;static bool sdkValid=true,intentBeforeKeys=true,durableReject=false,dropPrekey=false;static int64_t nowUs=1000000;static int rejectPhaseOnce=-1;\n'+mocks
  mocks=mocks.replace('if(w!=AccessWord::Seal)++defaultKeyCalls;','if(w!=AccessWord::Seal){++defaultKeyCalls;intentBeforeKeys &= factory_scale_rt->journal.loaded.selected>=0&&factory_scale_rt->journal.loaded.records[size_t(factory_scale_rt->journal.loaded.selected)].snapshot.phase!=scale_recovery::Phase::Original;}')
  mocks=mocks.replace('*s={491,9999,availableEntries,756,3};','unsigned present=unsigned(memoryStore.present[0])+unsigned(memoryStore.present[1]);*s={491,9999,dropPrekey&&present==2?53:availableEntries-(present==2?37:19),756,3};')
  # availableEntries expresses fresh-store95; with Original available76, Pending58.
  mocks=mocks.replace('bool Hal::gaugeThresholdAudit(bool,char*,size_t){return false;}','bool Hal::gaugeThresholdAudit(bool,char*,size_t){return false;}bool Hal::gaugeFactoryAuditRevalidate(char*,size_t){++revalidationCalls;revalidationUnderLease|=leaseDepth!=0;static FactoryWorkspace audit;factory_workspace=&audit;audit={};audit.attempted=audit.revalidation=audit.complete=audit.live=true;audit.record.count=131;audit.record.sourceCrc=mosaico_factory_scale::factorySourceCrc;for(unsigned i=0;i<131;++i)for(unsigned k=0;k<fixture[i].width;++k)audit.raw[67+i*7+k]=fixture[i].original[k];return true;}')
  mocks=mocks.replace('bool tryStorageLease(){return true;}void releaseStorageLease(){}','bool tryStorageLease(){if(!leaseAllowed)return false;if(leaseDepth){leaseViolation=true;return false;}++leaseDepth;return true;}void releaseStorageLease(){if(leaseDepth)--leaseDepth;else leaseViolation=true;}')
  support=old.RV_SUPPORT[:old.RV_SUPPORT.index('extern "C" {volatile int test_results')]
  support=support.replace('0.13.90-mosaico-ota','0.13.95-r13-mosaico-ota').replace('0.13.91-mosaico-ota','0.13.95-r14-mosaico-ota')
  support=support.replace('int mbedtls_base64_decode(unsigned char*,size_t,size_t*,const unsigned char*,size_t){return -1;}', 'int mbedtls_base64_decode(unsigned char* out,size_t cap,size_t* n,const unsigned char* input,size_t size){if(!capMode||size!=3||std::strncmp(reinterpret_cast<const char*>(input),"cap",3)||cap<8)return -1;for(unsigned i=0;i<8;++i)out[i]=0x55;*n=8;return 0;}')
  actual_runtime=(ROOT/'main/hal/mosaico/mosaico_factory_audit.inc').read_text().split('bool factory_runtime(){',1)[1].split('\n',1)[0]
  support+='''\nnamespace {bool factory_runtime(){'''+actual_runtime+'''\nbool factory_read(uint16_t a,uint8_t (&b)[36]){return readModel(a,b);}bool enterModel(){++enters;operation=0x4a2;return true;}}\n'''
  mocks=mocks.replace('Power Network::powerStats(){return {true,false,2};}Ble Bluetooth::diagnostics(){return {false,false};}', 'Power Network::powerStats(){return {uiLocked,wifiRunning,powerPhase};}Ble Bluetooth::diagnostics(){return {bleAdvertising,bleConnected};}')
  mocks=mocks.replace('bool busy(){return false;}bool healthPending(){return false;}', 'bool busy(){return otaBusy;}bool healthPending(){return otaHealth;}')
  support=support.replace('int esp_ota_get_state_partition(const esp_partition_t*,', 'int esp_ota_get_state_partition(const esp_partition_t* p,').replace('*s=ESP_OTA_IMG_VALID;return imageCryptoValid?0:1;', '*s=sdkValid&&invalidSdkSlot!=(p==&imageParts[0]?0:1)?ESP_OTA_IMG_VALID:static_cast<esp_ota_img_states_t>(1);return 0;')
  support=support.replace('int esp_partition_read(const esp_partition_t*,size_t,void* p,size_t n){', 'int esp_partition_read(const esp_partition_t* part,size_t,void* p,size_t n){++partitionReads[part==&imageParts[0]?0:1];')
  support+='\nnamespace {const esp_partition_t* esp_ota_get_running_partition(){return &imageParts[0];}}\n'
  mocks=mocks.replace("factory_scale_rt_owned", "contrast_rt_owned")
  code+=fixture+mocks+CASES+support
  code+='\nextern "C" int64_t __divdi3(int64_t a,int64_t b){bool n=(a<0)!=(b<0);uint64_t q=__udivdi3(a<0?uint64_t(-a):uint64_t(a),b<0?uint64_t(-b):uint64_t(b));return n?-int64_t(q):int64_t(q);}'
  code+='''\nextern "C" {volatile int test_results[41]={};}extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}extern "C" void _start(){for(int i=0;i<41;++i)test_results[i]=run_case(i);test_done();}\n'''
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
  headers['ota/ota_public_key.h']='#pragma once\ninline constexpr char MosaicoOtaPublicKey[]="test";'
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
    if 'mosaico_gauge_contrast_runtime.inc:' in line:self.assertLessEqual(int(line.split('\t')[1]),512,line)
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
   uc.reg_write(UC_RISCV_REG_SP,0x1fffff0);uc.emu_start(entry,symbols['test_done'],timeout=20000000,count=1000000000);values=struct.unpack('<41i',bytes(uc.mem_read(symbols['test_results'],164)))
   (RUN/'execution.log').write_text('Production GCT1 runtime; RV32 ISA; mock transport/storage/crypto/locks.\n'+f'ELF_SHA256={hashlib.sha256(elf).hexdigest()}\n'+''.join(f'case={i} result={v}\n' for i,v in enumerate(values)))
   self.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done']);self.assertEqual(values,(0,)*41,str(RUN/'execution.log'))
if __name__=='__main__':unittest.main()
