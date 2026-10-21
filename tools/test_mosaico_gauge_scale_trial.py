"""Bounded trial model faults + isolated wire-backend compile, never hardware."""
from pathlib import Path
from datetime import datetime,timezone
import os
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
INC=ROOT/'main/hal/mosaico/mosaico_gauge_scale_trial.inc'
RUN=ROOT/'artifacts/gauge-scale-trial-tests'/datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S%fZ')
CXX=os.environ.get('CXX','C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
MODEL=r'''
#define MOSAICO_SCALE_TRIAL_MODEL_ONLY
#include "mosaico_gauge_scale_trial.inc"
using namespace mosaico_scale_trial;
enum class Stage {None,Admit,Intent,Open,Observe,Enter,Write,Verify,Exit,Seal,Final,Complete};
struct FaultBackend {
 Stage failForward=Stage::None,failRestore=Stage::None;
 bool restoring=false,live=false,originalLive=false,protection=false,third=false;
 bool forwardFault=false,restoreFault=false,unauthorized=false,unknownOverwritten=false;
 bool partial=false,pending=false,restoreIntent=false;
 unsigned keys[2]{},writes[2][17]{},exits[2]{},seals[2]{},phase=0;
 unsigned char image[17]{},backup[524]{};
 constexpr FaultBackend(){for(unsigned i=0;i<524;++i)backup[i]=static_cast<unsigned char>(i);}
 constexpr bool intact(){for(unsigned i=0;i<524;++i)if(backup[i]!=static_cast<unsigned char>(i))return false;return true;}
 constexpr bool step(Stage s){auto& fired=restoring?restoreFault:forwardFault;
  if((restoring?failRestore:failForward)==s&&!fired){fired=true;return false;}return true;}
 constexpr void revoke(){live=false;originalLive=false;protection=true;}
 constexpr bool admit(bool restore){restoring=restore;if(restore&&third)return false;return step(Stage::Admit);}
 constexpr bool intent(bool restore){restoring=restore;if(!step(Stage::Intent))return false;
  if(restore){restoreIntent=true;phase=3;}else{pending=true;phase=1;}return true;}
 constexpr bool open(bool restore){restoring=restore;
  if(!(restore?restoreIntent:pending)){unauthorized=true;return false;}++keys[restore?1:0];return step(Stage::Open);}
 constexpr bool observe(bool restore){restoring=restore;
  for(auto value:image)if(value>1){third=true;return false;}
  return step(Stage::Observe);}
 constexpr bool enter(){return step(Stage::Enter);}
 constexpr bool write(bool restore){restoring=restore;
  if(!(restore?restoreIntent:pending)){unauthorized=true;return false;}
  for(unsigned i=0;i<17;++i){if(image[i]>1){unknownOverwritten=true;return false;}
   const auto desired=restore?0:1;if(image[i]==desired)continue;++writes[restore?1:0][i];
   if(!step(Stage::Write)){image[i]=partial?2:desired;third=partial;return false;}image[i]=desired;
  }return true;}
 constexpr bool verify(bool restore){restoring=restore;
  for(auto value:image)if(value!=(restore?0:1))return false;
  return step(Stage::Verify);}
 constexpr bool exit(){++exits[restoring?1:0];return step(Stage::Exit);}
 constexpr bool seal(){++seals[restoring?1:0];return step(Stage::Seal);}
 constexpr bool final(bool restore){restoring=restore;return step(Stage::Final);}
 constexpr bool complete(bool restore){restoring=restore;if(!step(Stage::Complete))return false;phase=restore?0:2;return true;}
 constexpr void publish(bool restore){live=!restore;originalLive=restore;protection=!restore;}
 constexpr void protect(){if(!seals[restoring?1:0])++seals[restoring?1:0];live=false;originalLive=false;protection=true;}
 constexpr bool safe(){if(!intact()||unauthorized||unknownOverwritten)return false;
  for(unsigned d=0;d<2;++d){if(keys[d]>1||exits[d]>1||seals[d]>1)return false;
   for(auto n:writes[d])if(n>1)return false;}return true;}
};
'''
STUBS={
'nvs.h':r'''#pragma once
#include <cstddef>
using nvs_handle_t=unsigned;using esp_err_t=int;constexpr int ESP_OK=0,ESP_ERR_NVS_NOT_FOUND=1,NVS_READWRITE=1;
struct nvs_stats_t {size_t used_entries,free_entries,available_entries,total_entries,namespace_count;};
int nvs_get_stats(const char*,nvs_stats_t*);
int nvs_open(const char*,int,nvs_handle_t*);int nvs_get_blob(nvs_handle_t,const char*,void*,size_t*);int nvs_set_blob(nvs_handle_t,const char*,const void*,size_t);int nvs_commit(nvs_handle_t);void nvs_close(nvs_handle_t);
''',
'esp_app_desc.h':r'''#pragma once
#include <cstdint>
struct esp_app_desc_t {char version[32];char project_name[32];uint8_t app_elf_sha256[32];};
''',
'esp_partition.h':r'''#pragma once
#include <cstddef>
#include <cstdint>
struct esp_partition_t {size_t size;};
constexpr int ESP_PARTITION_TYPE_APP=0,ESP_PARTITION_SUBTYPE_APP_OTA_0=16,ESP_PARTITION_SUBTYPE_APP_OTA_1=17;
const esp_partition_t* esp_partition_find_first(int,int,const char*);
int esp_partition_read(const esp_partition_t*,size_t,void*,size_t);
''',
'esp_ota_ops.h':r'''#pragma once
#include "esp_partition.h"
#include "esp_app_desc.h"
enum esp_ota_img_states_t {ESP_OTA_IMG_VALID};
int esp_ota_get_state_partition(const esp_partition_t*,esp_ota_img_states_t*);
int esp_ota_get_partition_description(const esp_partition_t*,esp_app_desc_t*);
''',
'cJSON.h':r'''#pragma once
#include <cstddef>
struct cJSON{};
cJSON* cJSON_ParseWithLength(const char*,size_t);
bool cJSON_IsArray(const cJSON*);int cJSON_GetArraySize(const cJSON*);
cJSON* cJSON_GetArrayItem(const cJSON*,int);char* cJSON_PrintUnformatted(const cJSON*);
void cJSON_free(void*);void cJSON_Delete(cJSON*);
''',
'mbedtls/base64.h':r'''#pragma once
#include <cstddef>
int mbedtls_base64_decode(unsigned char*,size_t,size_t*,const unsigned char*,size_t);
''',
'psa/crypto.h':r'''#pragma once
#include <cstddef>
using psa_status_t=int;using psa_algorithm_t=unsigned;
constexpr psa_status_t PSA_SUCCESS=0;
constexpr psa_algorithm_t PSA_ALG_SHA_256=0x02000009;
struct psa_hash_operation_t {unsigned char opaque[112];};
#define PSA_HASH_OPERATION_INIT {}
psa_status_t psa_crypto_init();
psa_status_t psa_hash_setup(psa_hash_operation_t*,psa_algorithm_t);
psa_status_t psa_hash_update(psa_hash_operation_t*,const unsigned char*,size_t);
psa_status_t psa_hash_finish(psa_hash_operation_t*,unsigned char*,size_t,size_t*);
psa_status_t psa_hash_abort(psa_hash_operation_t*);
'''}
class TrialTests(unittest.TestCase):
 def compile(self,code,name,stubs=False):
  RUN.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='.gauge-trial-',dir=ROOT) as td:
   td=Path(td);source=td/'test.cpp';source.write_text(code)
   for path,text in STUBS.items():
    p=td/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
   args=[CXX,'-std=c++20','-Wall','-Wextra','-Werror','-Wno-unused-function','-fconstexpr-ops-limit=100000000','-I'+str(td),'-I'+str(INC.parent)]
   if stubs:args+=['-Os','-fstack-usage','-c',str(source),'-o',str(RUN/(name+'.o'))]
   else:args+=['-fsyntax-only',str(source)]
   result=subprocess.run(args,capture_output=True,text=True)
   log=RUN/(name+'.log');log.write_text(result.stdout+result.stderr)
   self.assertEqual(result.returncode,0,str(log))
   if stubs:
    for line in (RUN/(name+'.su')).read_text().splitlines():
     self.assertLessEqual(int(line.split('\t')[1]),512,line)
 def test_success_default_denial_and_exact_backup(self):
  self.compile(MODEL+r'''
constexpr bool test(){FaultBackend b{};
 if(apply(b)!=Result::Applied||!b.live||!b.pending||b.phase!=2||!b.safe())return false;
 if(restore(b)!=Result::Restored||b.live||!b.originalLive||b.phase!=0||!b.safe())return false;
 FaultBackend denied{};denied.failForward=Stage::Admit;
 if(apply(denied)!=Result::Blocked||denied.pending||denied.keys[0]||denied.writes[0][0]||!denied.intact())return false;
 FaultBackend pending{};pending.failForward=Stage::Intent;
 return apply(pending)==Result::FailedClosed&&!pending.keys[0]&&!pending.keys[1]&&pending.protection&&pending.safe();}
static_assert(test());
''','success')
 def test_every_forward_and_restore_stage_failure(self):
  self.compile(MODEL+r'''
constexpr bool test(){
 for(unsigned s=unsigned(Stage::Open);s<=unsigned(Stage::Complete);++s){FaultBackend b{};b.failForward=Stage(s);
  const auto result=apply(b);if(result==Result::Applied||!b.safe()||b.live)return false;
  if(result==Result::Restored&&!b.originalLive)return false;
 }
 for(unsigned s=unsigned(Stage::Admit);s<=unsigned(Stage::Complete);++s){FaultBackend b{};
  b.failForward=Stage::Write;b.failRestore=Stage(s);const auto result=apply(b);
  if(result!=Result::FailedClosed||b.live||b.originalLive||!b.protection||!b.safe())return false;
 }
 return true;}
static_assert(test());
''','all-stage-faults')
 def test_half_write_third_value_never_overwritten(self):
  self.compile(MODEL+r'''
constexpr bool test(){FaultBackend b{};b.failForward=Stage::Write;b.partial=true;
 const auto r=apply(b);if(r!=Result::FailedClosed||!b.third||b.live||!b.protection||!b.safe())return false;
 for(auto n:b.writes[1])if(n)return false;
 return b.image[0]==2&&b.intact()&&b.keys[1]==0;}
static_assert(test());
''','half-write-third-value')
 def test_actual_backend_isolated_compiles_and_stack(self):
  from test_mosaico_gauge_scale_runtime import FIXTURE
  fixture=FIXTURE.replace('#define MOSAICO_SCALE_TRIAL_MODEL_ONLY\n','')
  fixture=fixture.replace('namespace {bool scale_trial_boot_restore_locked(){return false;}bool scale_append_trial_diagnostics(char*,size_t){return true;}}','')
  fixture=fixture.replace('#include <cstdio>','#include <cstdio>\n#include <algorithm>\n#include "mosaico_gauge_model.h"')
  fixture=fixture.replace('namespace std {struct mutex{};struct recursive_mutex{};template<class T>struct lock_guard{explicit lock_guard(T&){};};}',r'''namespace std {struct mutex{};struct recursive_mutex{};
struct try_to_lock_t{};inline constexpr try_to_lock_t try_to_lock{};
template<class T>struct lock_guard{explicit lock_guard(T&){};};
template<class T>struct unique_lock{unique_lock(T&,try_to_lock_t){};bool owns_lock()const;};}''')
  fixture=fixture.replace(' bool gaugeThresholdAudit(bool,char*,size_t);',r''' bool gaugeThresholdAudit(bool,char*,size_t);
 bool gaugeScaleImages(const char*,char*,size_t);bool gaugeScaleApply(char*,size_t);
 bool gaugeScaleRestore(char*,size_t);bool gaugeScaleObservedField(size_t,char*,size_t);bool gaugeScaleTrialActive()const;void gaugeScalePoll();''')
  fixture=fixture.replace('namespace MosaicoOta {bool busy();',r'''namespace MosaicoOta {
struct VerifiedImageDescriptor {uint32_t size;uint8_t hash[32];char version[32];};
bool verifySignedImageDescriptor(const char*,VerifiedImageDescriptor&);
bool busy();''')
  extra=r'''
int64_t esp_timer_get_time();void gauge_feed();void gauge_delay_ms(uint32_t);
int i2c_master_transmit(bool,const uint8_t*,size_t,int);
int mockLoadAccess(mosaico_gauge::AccessJournal&,const uint8_t*);
bool mockWaitSecurity(uint8_t);bool mockWaitConfig(bool);bool mockExit();
bool mockThreshold(mosaico_threshold::Selector,uint8_t*);
bool mockCalibration(mosaico_calibration::Selector,uint8_t*);
bool mockExtra(mosaico_public_extra::Selector,uint8_t*);
namespace {
enum class AccessWord:uint16_t {UnsealFirst,UnsealSecond,Full,Seal};
enum class GaugeCommand:uint16_t {EnterConfig};
bool gauge_access_word(AccessWord){return mockGaugeIdentity();}
bool gauge_control(GaugeCommand){return mockGaugeIdentity();}
bool gauge_wait_security(uint8_t n){return mockWaitSecurity(n);}
bool gauge_wait_config(bool b){return mockWaitConfig(b);}
bool gauge_exit_config(){return mockExit();}
int gauge_load_access(mosaico_gauge::AccessJournal& a,const uint8_t* m){return mockLoadAccess(a,m);}
bool gauge_threshold_read(mosaico_threshold::Selector s,uint8_t* b){return mockThreshold(s,b);}
bool gauge_calibration_read(mosaico_calibration::Selector s,uint8_t* b){return mockCalibration(s,b);}
bool gauge_public_extra_read(mosaico_public_extra::Selector s,uint8_t* b){return mockExtra(s,b);}
}
'''
  fixture=fixture.replace('#include "mosaico_gauge_scale_runtime.inc"',extra+'\n#include "mosaico_gauge_scale_runtime.inc"')
  self.compile(fixture,'actual-backend',True)
 def test_closed_wire_and_signed_actual_images_source_contract(self):
  text=INC.read_text()
  for forbidden in ['mbedtls/sha256.h','mbedtls_sha256_','scale_trial->keyFailed=false','scale_trial->thirdValue=false','GaugeCommand::Reset','GaugeCommand::Ocv','nvs_erase','esp_partition_write','esp_ota_set_boot_partition']:
   self.assertNotIn(forbidden,text)
  for required in ['verifySignedImageDescriptor','ESP_OTA_IMG_VALID','esp_partition_read','psa_crypto_init','psa_hash_setup','PSA_ALG_SHA_256','psa_hash_update','psa_hash_finish','digestLength==32','psa_hash_abort',
      '0.13.90-mosaico-ota','0.13.91-mosaico-ota','apply_explicit_prepare_and_two_audited_images_required',
      'trial_key_attempt_failed_no_retry','trial_unexpected_owned_third_value_no_overwrite','scale_trial->possibleDirty=true',
      '300000000','mv<=3600','scale_journal::saveNvs(record,{true,true,true}',
      'uint8_t(4+f.width)','mosaico_gauge::macChecksum(packet+1,2+f.width)']:
   self.assertIn(required,text)
if __name__=='__main__':unittest.main()
