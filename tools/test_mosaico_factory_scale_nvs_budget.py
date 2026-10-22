"""Bounded offline budget/real NVS-adapter tests; no physical reservation claim."""
from pathlib import Path
import unittest
import inspect,re
from unittest.mock import patch
import test_mosaico_factory_scale_journal as codec
import test_mosaico_factory_scale_storage as store
import test_mosaico_factory_scale_runtime as runtime
import test_mosaico_factory_revalidate as revalidate
import test_mosaico_factory_audit as audit
ROOT=codec.ROOT
OUT=ROOT/'.artifacts/private/mosaico/factory-scale-nvs-budget-tests'

class BudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):codec.FactoryScaleJournalTests.setUpClass();cls.fixture=codec.FactoryScaleJournalTests.fixture
    def compile(self,code):codec.FactoryScaleJournalTests.compile(self,code)
    def test_pure_pair_identity_transition_namespace_and_no_margin_credit(self):
        self.compile('#pragma GCC diagnostic push\n#pragma GCC diagnostic ignored "-Wmisleading-indentation"\n'+store.MODEL+'\n#pragma GCC diagnostic pop\n'+r'''
#include "mosaico_factory_scale_nvs_budget_model.h"
namespace b=mosaico_factory_scale_budget;
constexpr b::Usage usage{37,true,true,true,true};
constexpr bool test(){Memory m{};j::StorageWorkspace w{};auto a=sample();
 if(b::credit(w.loaded,usage,true))return false;
 if(j::saveInto(m,a,admitted,w)!=j::Status::Ready||b::credit(w.loaded,usage,true))return false;
 const r::Phase phases[]={r::Phase::Pending,r::Phase::Applied,r::Phase::Restoring,r::Phase::Original};for(auto phase:phases){a=next(a,phase);if(j::saveInto(m,a,admitted,w)!=j::Status::Ready)return false;}
 if(b::budget(74,w.loaded,usage,true).required!=38||b::budget(92,w.loaded,usage,true).required!=56)return false;
 auto owned=a;auto proof=b::proof(w.loaded,usage,owned,true);
 if(!b::matches(proof,w.loaded,usage,true)||b::matches(proof,w.loaded,usage,false))return false;
 for(unsigned mode=0;mode<11;++mode){auto l=w.loaded;auto u=usage;
  switch(mode){case 0:l.valid[0]=false;break;case 1:l.status=j::Status::Corrupt;break;case 2:l.records[size_t(l.selected)].snapshot.phase=r::Phase::Pending;break;
  case 3:l.records[0].snapshot.binding.bytes[0]^=1;break;case 4:l.records[0].snapshot.generation=1;break;
  case 5:l.records[0].snapshot.fields[10].target[0]^=1;break;case 6:u.exactNamespace=false;break;
  case 7:u.exactKeys=false;break;case 8:u.exactLengths=false;break;case 9:u.stableStats=false;break;default:u.entries=35;}
  if(b::credit(l,u,true)||b::budget(74,l,u,true).required!=74||b::matches(proof,l,u,true))return false;
 }
 auto changedUsage=usage;++changedUsage.entries;if(b::matches(proof,w.loaded,changedUsage,true))return false;
 auto l=w.loaded;++l.records[0].snapshot.epoch;if(b::matches(proof,l,usage,true))return false;
 // Same-address owned record overwrite cannot turn image matching into a
 // self-comparison: MAC and canonical source/target CRC were frozen by value.
 auto moved=w.loaded;owned.mac[5]^=1;owned.snapshot.binding=old::binding(owned.mac);
 for(unsigned i=0;i<2;++i){moved.records[i].mac=owned.mac;moved.records[i].snapshot.binding=old::binding(owned.mac);}
 if(!b::pair(moved)||b::matches(proof,moved,usage,true))return false;
 owned=a;owned.snapshot.phase=r::Phase::Pending;if(b::matches(proof,w.loaded,usage,true))return false;
 owned=a;auto swapped=w.loaded;auto temp=swapped.records[0];swapped.records[0]=swapped.records[1];swapped.records[1]=temp;swapped.selected=1-swapped.selected;
 if(!b::pair(swapped)||b::matches(proof,swapped,usage,true))return false;
 auto heavy=usage;heavy.entries=1000;
 return b::credit(w.loaded,heavy,true)==36&&b::preKeyBase==54&&
  b::prepareBase-36>=19+16+1&&b::revalidateBase-36-(b::prepareBase-36)==18;
}
static_assert(test());
''')
    def test_actual_RO_NVS_evidence_and_failed_native_write_preserves_old_record(self):
        extras=r'''
static int usageFault,usageCalls,iteratorIndex;
esp_err_t nvs_get_used_entry_count(nvs_handle_t,size_t* out){++usageCalls;*out=usageFault==1?35:usageFault==2&&usageCalls==2?38:37;return usageFault==14||(usageFault==15&&usageCalls==2)?2:0;}
esp_err_t nvs_entry_find_in_handle(nvs_handle_t,nvs_type_t,nvs_iterator_t* out){iteratorIndex=0;*out=reinterpret_cast<void*>(1);return usageFault==3?2:ESP_OK;}
esp_err_t nvs_entry_info(nvs_iterator_t,nvs_entry_info_t* info){if(usageFault==5)return 2;
 const char* ns=usageFault==6?"wrong":"gscale_f3_v1";const char* key=iteratorIndex?"recover1":"recover0";
 if(usageFault==7||iteratorIndex==2)key="other";if(usageFault==8)key="recover0";
 std::memset(info,0,sizeof(*info));std::memcpy(info->namespace_name,ns,std::strlen(ns));std::memcpy(info->key,key,std::strlen(key));
 if(usageFault==11)for(unsigned i=0;i<16;++i)info->key[i]='x';
 info->type=usageFault==10?NVS_TYPE_ANY:NVS_TYPE_BLOB;return ESP_OK;}
esp_err_t nvs_entry_next(nvs_iterator_t* it){if(usageFault==4)return 2;++iteratorIndex;if(iteratorIndex<(usageFault==9?3:2))return ESP_OK;*it=nullptr;return ESP_ERR_NVS_NOT_FOUND;}
void nvs_release_iterator(nvs_iterator_t){}
'''
        prefix=store.HARNESS.split('int main(){',1)[0]
        body=r'''
int main(){j::StorageWorkspace w{};nv={};auto a=sample();
 assert(j::saveNvs(a,admitted,w)==j::Status::Ready);
 const r::Phase phases[]={r::Phase::Pending,r::Phase::Applied,r::Phase::Restoring,r::Phase::Original};for(auto phase:phases){a=next(a,phase);assert(j::saveNvs(a,admitted,w)==j::Status::Ready);}
 const auto stable=nv;const auto original=nv.slots[size_t(w.loaded.selected)];const auto selected=w.loaded.selected;
 for(unsigned f=0;f<16;++f){nv=stable;usageFault=int(f);usageCalls=0;nv.probeSize=f==12?511:512;nv.probeError=f==13;
  auto u=j::reuseUsageNvs(w.loaded);const auto credit=mosaico_factory_scale_budget::credit(w.loaded,u,true);
  assert(credit==(f==0?36:0)&&nv.sets==stable.sets&&nv.commits==stable.commits);}
 nv=stable;usageFault=0;usageCalls=0;auto mac=a.mac;mac[5]^=1;
 assert(j::loadNvs(mac,w)!=j::Status::Ready&&!mosaico_factory_scale_budget::credit(w.loaded,j::reuseUsageNvs(w.loaded),true));
 for(unsigned fault=0;fault<7;++fault){nv=stable;assert(j::loadNvs(a.mac,w)==j::Status::Ready);auto pending=next(a,r::Phase::Pending);
  switch(fault){case 0:nv.openError=true;break;case 1:nv.setError=true;break;case 2:nv.commitError=true;break;
   case 3:nv.torn=true;break;case 4:nv.readbackError=true;break;case 5:nv.probeError=true;break;default:nv.dataError=true;}
  assert(j::saveNvs(pending,admitted,w)!=j::Status::Ready);
  // Power loss may leave the new slot missing/pending/torn. The previous valid
  // Original bytes must remain; never claim that the new write was committed.
  assert(nv.slots[size_t(selected)]==original);j::Record oldRecord{};assert(j::decode(original,a.mac,oldRecord,w.codec)&&oldRecord.snapshot.phase==r::Phase::Original);
 }
 return 0;}
'''
        with patch.object(store,'HARNESS',prefix+extras+body),patch.object(store,'OUT',OUT/'adapter'):
            store._test_actual_nvs_rv32(self)
    def test_actual_runtime_local_Apply_proof_and_54_gate(self):
        cases=runtime.CASES.split('extern "C" int run_case(',1)[0]+r'''
extern "C" int run_case(int id){reset();
 auto a=runtimeStorage.journal.loaded.records[size_t(runtimeStorage.journal.loaded.selected)];
 const scale_recovery::Phase phases[]={scale_recovery::Phase::Pending,scale_recovery::Phase::Applied,scale_recovery::Phase::Restoring,scale_recovery::Phase::Original};for(auto phase:phases){
  ++a.snapshot.generation;++a.snapshot.epoch;a.snapshot.phase=phase;if(factory_scale_journal::saveInto(memoryStore,a,{true,true,true},runtimeStorage.journal)!=factory_scale_journal::Status::Ready)return 1;}
 factory_scale_load_locked(true);runtimeStorage.record=a;runtimeStorage.verifiedGeneration=a.snapshot.generation;runtimeStorage.verifiedEpoch=a.snapshot.epoch;runtimeStorage.preparedThisBoot=true;runtimeStorage.preparedUs=nowUs;
 reuseUsageFixture={37,true,true,true,true};char out[1024];
 const int mode=id%9;
 if(mode==1)reuseUsageFixture.exactKeys=false;if(mode==2)runtimeStorage.thirdValue=true;if(mode==3)runtimeStorage.unsafeCalibration=true;
 if(mode==4)availableEntries=74;if(mode==5)dropPrekey=true;if(mode==6)reuseUsageFixture.stableStats=false;
 if(mode==7){auto p=factory_scale_budget_proof_locked();runtimeStorage.operation=true;auto u=reuseUsageFixture;++u.entries;if(factory_scale_budget::matches(p,runtimeStorage.journal.loaded,u,true))return 2;return 0;}
 if(mode==8){if(!factory_scale_begin_locked())return 3;FactoryScaleBackend plain;if(plain.admit(false)||defaultKeyCalls)return 4;return 0;}
 const bool ok=GetHAL().gaugeFactoryScaleApply(out,sizeof(out));
 if(mode==0)return ok&&writes==17&&runtimeStorage.budgetReuse==36&&runtimeStorage.budgetRequired==38?0:5;
 if(ok||writes||defaultKeyCalls)return 6;
 if(mode==5&&!runtimeStorage.preKeySpaceRejected)return 7;
 return 0;}
'''
        method=inspect.getsource(runtime.FactoryRuntimeTests.test_actual_runtime_rv32).lstrip()
        method=re.sub(r'\b61\b','9',method).replace('244','36').replace('<61i','<9i')
        namespace=dict(runtime.__dict__);namespace.update(CASES=cases,RUN=OUT/'runtime')
        exec(method,namespace);namespace['test_actual_runtime_rv32'](self)
    def test_actual_FA_receipt_gate_56_or_fallback92_no_receipt_credit(self):
        fixture=revalidate.FIXTURE.replace('#include "mosaico_factory_audit.inc"',r'''
#define MOSAICO_FACTORY_SCALE_RUNTIME 1
namespace {static size_t creditFixture=0;static bool reuseAllowed=true;
size_t factory_scale_reuse_credit_locked(){return creditFixture;}
bool factory_scale_audit_allowed_locked(){return reuseAllowed;}}
#include "mosaico_factory_audit.inc"
''')
        cases=audit.CASES.split('static int run_case(',1)[0]+r'''
static int run_case(int id){reset();creditFixture=0;reuseAllowed=true;char out[1024];
 if(!hal.gaugeFactoryAuditCapture(out,sizeof(out)))return 1;
 const auto backup=savedBackup;const auto journal=savedJournal;
 factory_workspace=nullptr;sets=commits=reads=unsealWords=fullWords=seals=dmReads=0;
 creditFixture=id<2||id==4?36:0;available=id==0?56:id==1?55:id==2?91:92;
 if(id==4)reuseAllowed=false;
 const bool ok=hal.gaugeFactoryAuditRevalidate(out,sizeof(out));
 if(id==0||id==3){if(!ok||factory_workspace->reuseCredit!=creditFixture||factory_workspace->dynamicRequired!=(id==0?56:92)||dmReads!=262||unsealWords!=2||fullWords!=2)return 2;}
 else if(ok||unsealWords||fullWords||seals||sets)return 3;
 return savedBackup==backup&&savedJournal==journal?0:4;}
extern "C" {volatile int test_results[5]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<5;++i)test_results[i]=run_case(i);test_done();}
'''
        self.fixture,self.cases,self.case_count,self.run_directory=fixture,cases,5,OUT/'FA-gate'
        try:audit.FactoryTests.test_actual_inc_rv32(self)
        finally:self.fixture=codec.FactoryScaleJournalTests.fixture

if __name__=='__main__':unittest.main()
