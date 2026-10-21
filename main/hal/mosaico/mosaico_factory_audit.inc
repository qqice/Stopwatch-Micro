// Explicit factory3000 observation only. No startup caller and no native waiver.
#include "mosaico_factory_audit_model.h"
#include <new>
#include <esp_ota_ops.h>
namespace {
namespace factory=mosaico_factory_audit;
struct FactoryWorkspace {
 factory::Journal record{},decoded{};factory::JournalBytes journal{},journalIo{};
 factory::BackupBytes raw{},io{};
 uint8_t first[36]{},second[36]{};
 bool attempted=false,complete=false,live=false,accessTouched=false,sealAttempted=false,persistedValid=false;
 bool unsealAttempted=false,fullAttempted=false,headroomValid=false;
 unsigned available=0;const char* reason="not_captured";char key[16]{};
};
FactoryWorkspace* factory_workspace=nullptr; // transaction owner, PSRAM only
bool factory_storage(){if(factory_workspace)return true;void* p=heap_caps_malloc(sizeof(FactoryWorkspace),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);if(!p)return false;factory_workspace=new(p)FactoryWorkspace{};return true;}
bool factory_capacity(char* out,size_t n,size_t required){if(out&&n>=required)return true;if(out&&n)std::snprintf(out,n,"error=factory_capacity_require_%u",unsigned(required));return false;}
bool factory_stats(){auto& w=*factory_workspace;nvs_stats_t s{};w.headroomValid=nvs_get_stats(nullptr,&s)==ESP_OK;w.available=w.headroomValid?unsigned(s.available_entries):0;return w.headroomValid;}
// Exact read lengths and canonical decode, twice on the same RO handle.
// Returns 0=absent, 1=valid, -1=invalid/error; no arbitrary namespace/key.
int factory_load(){auto& w=*factory_workspace;w.persistedValid=false;nvs_handle_t h=0;const auto e=nvs_open(factory::nvsNamespace,NVS_READONLY,&h);if(e==ESP_ERR_NVS_NOT_FOUND)return 0;if(e!=ESP_OK)return -1;
 size_t a=w.journal.size();auto first=nvs_get_blob(h,factory::journalKey,w.journal.data(),&a);if(first==ESP_ERR_NVS_NOT_FOUND){nvs_close(h);return 0;}size_t b=w.journalIo.size();auto second=nvs_get_blob(h,factory::journalKey,w.journalIo.data(),&b);nvs_close(h);
 if(first!=ESP_OK||second!=ESP_OK||a!=w.journal.size()||b!=w.journalIo.size()||w.journal!=w.journalIo||!factory::decode(w.journal,GetHAL().getFactoryMac(),w.decoded))return -1;
 w.persistedValid=true;
 return 1;
}
bool factory_save(){auto& w=*factory_workspace;if(!factory::encode(w.record,w.journal))return false;nvs_handle_t h=0;if(nvs_open(factory::nvsNamespace,NVS_READWRITE,&h)!=ESP_OK)return false;gauge_feed();auto e=nvs_set_blob(h,factory::journalKey,w.journal.data(),w.journal.size());if(e==ESP_OK)e=nvs_commit(h);nvs_close(h);gauge_feed();if(e!=ESP_OK)return false;
 // Save readback must not overwrite the caller's expected bytes.
 if(!factory::encode(w.record,w.journalIo))return false;
 const int loaded=factory_load();factory::JournalBytes expected{}; // 64 bytes only
 return loaded==1&&factory::encode(w.record,expected)&&w.journal==expected;
}
bool factory_key(uint32_t generation,char (&key)[16]){return generation&&std::snprintf(key,sizeof(key),"f3b%08lx",static_cast<unsigned long>(generation))==11;}
bool factory_prior_backup(){auto& w=*factory_workspace;if(!factory_key(w.decoded.generation,w.key))return false;nvs_handle_t h=0;if(nvs_open(factory::nvsNamespace,NVS_READONLY,&h)!=ESP_OK)return false;size_t a=w.raw.size(),b=w.io.size();const auto x=nvs_get_blob(h,w.key,w.raw.data(),&a);const auto y=nvs_get_blob(h,w.key,w.io.data(),&b);nvs_close(h);return x==ESP_OK&&y==ESP_OK&&a==w.raw.size()&&b==w.io.size()&&w.raw==w.io&&factory::validBackup(w.raw,w.decoded);}
bool factory_standard(factory::Standard& s){uint16_t current=0,average=0;bool ok=gauge&&gauge_word(0x3c,s.dc)&&gauge_word(0x12,s.fcc)&&gauge_word(0x3a,s.op)&&gauge_word(0x10,s.rm)&&gauge_word(0x2c,s.soc)&&gauge_word(8,s.mv)&&gauge_word(6,s.temp)&&gauge_word(0x0c,current)&&gauge_word(0x14,average);s.current=int16_t(current);s.average=int16_t(average);return ok;}
bool factory_identity(){Hal::GaugeEngineSnapshot s{};gauge_engine_locked(s,mosaico_gauge_status::Explicit,0);return (s.validMask&(mosaico_gauge_engine::Identity|mosaico_gauge_engine::DefinitionMatch))==(mosaico_gauge_engine::Identity|mosaico_gauge_engine::DefinitionMatch)&&gauge_identity();}
bool factory_runtime(){const auto p=GetNetworkQuota().powerStats();const auto b=GetCodexMicroBle().diagnostics();const auto* part=esp_ota_get_running_partition();esp_ota_img_states_t state{};return part&&esp_ota_get_state_partition(part,&state)==ESP_OK&&state==ESP_OTA_IMG_VALID&&!MosaicoOta::busy()&&!MosaicoOta::healthPending()&&p.locked&&p.phase==2&&!p.wifiRunning&&!b.advertising&&!b.connected;}
bool factory_physical(unsigned sec,factory::Standard* out=nullptr){factory::Standard s{};if(!factory_runtime()||!factory_standard(s)||!factory::physical(s,sec))return false;if(out)*out=s;return true;}
// Route only the previously authenticated closed public catalogues. There is
// deliberately no arbitrary selector exposed by an API or console argument.
bool factory_read(uint16_t address,uint8_t (&block)[36]){
 for(const auto& f:mosaico_threshold::fields)if(uint16_t(f.selector)==address)return gauge_threshold_read(f.selector,block);
 for(const auto& f:mosaico_calibration::fields)if(uint16_t(f.selector)==address)return gauge_calibration_read(f.selector,block);
 for(const auto& f:mosaico_public_extra::fields)if(uint16_t(f.selector)==address)return gauge_public_extra_read(f.selector,block);
 return false;
}
const char* factory_type(size_t i){const auto address=scale_recovery::layout[i].address;
 for(const auto& f:mosaico_threshold::fields)if(uint16_t(f.selector)==address){switch(f.type){case mosaico_threshold::Type::U1:return "U1";case mosaico_threshold::Type::H1:return "H1";case mosaico_threshold::Type::I2:return "I2";case mosaico_threshold::Type::U2:return "U2";case mosaico_threshold::Type::H2:return "H2";}}
 for(const auto& f:mosaico_calibration::fields)if(uint16_t(f.selector)==address){switch(f.type){case mosaico_calibration::Type::I2:return "I2";case mosaico_calibration::Type::F4:return "F4_opaque";case mosaico_calibration::Type::I1:return "I1";case mosaico_calibration::Type::U1:return "U1";}}
 for(const auto& f:mosaico_public_extra::fields)if(uint16_t(f.selector)==address){switch(f.type){case mosaico_public_extra::Type::I1:return "I1";case mosaico_public_extra::Type::H1:return "H1";case mosaico_public_extra::Type::U1:return "U1";case mosaico_public_extra::Type::I2:return "I2";case mosaico_public_extra::Type::U2:return "U2";case mosaico_public_extra::Type::H2:return "H2";}}
 return "unknown";
}
struct FactoryBackend {
 FactoryWorkspace& w;explicit FactoryBackend(FactoryWorkspace& work):w(work){}
 bool reject(const char* why){w.reason=why;return false;}
 bool admit(){
  if(w.attempted)return reject("blocked_once_this_boot");
  const int loaded=factory_load();
  if(loaded<0)return reject("blocked_journal_invalid");
  if(loaded==1&&(w.decoded.state!=factory::State::Completed||w.decoded.generation==0xffffffffU))return reject("blocked_persistent_pending_failed_or_generation");
  if(loaded==1&&!factory_prior_backup())return reject("blocked_previous_completed_backup_invalid");
  if(!factory_stats()||w.available<factory::requiredEntries)return reject("blocked_nvs_available_entries");
  if(!factory_runtime())return reject("blocked_actual_valid_phase2_radios_ota");
  native_recovery::Evidence evidence{};const auto evidenceMac=GetHAL().getFactoryMac();
  if(!native_evidence_locked(evidence)||!native_recovery::exact(evidence,evidenceMac.data()))return reject("blocked_exact_authorized_failed_history");
  const auto mac=GetHAL().getFactoryMac();mosaico_gauge::Journal nominal{};mosaico_gauge::AccessJournal access{};
  if(gauge_load_journal(nominal,mac.data())!=ESP_OK||gauge_load_access(access,mac.data())!=ESP_OK||!mosaico_gauge::validJournal(nominal,mac.data())||!mosaico_gauge::validAccessJournal(access,mac.data())||!mosaico_gauge::bootHistoryEligible(nominal,access))return reject("blocked_known_key_history");
  factory::Standard a{},b{};if(!factory_physical(3,&a)||!factory_identity())return reject("blocked_fresh_factory3k_identity");gauge_delay_ms(1000);
  if(!factory_physical(3,&b)||!factory_identity()||a.dc!=b.dc||a.fcc!=b.fcc||a.op!=b.op)return reject("blocked_second_factory3k_sample");
  const uint32_t generation=loaded==1?w.decoded.generation+1:1;w.record={};w.record.generation=generation;w.record.mac=mac;w.record.before=b;
  if(!factory_key(generation,w.key))return reject("blocked_generation_key");
  factory::initBackup(w.record,w.raw);
  w.attempted=true;w.complete=false;w.live=false;w.reason="capturing_factory3k_public131";return true;
 }
 bool intent(uint16_t flags){w.record.flags|=flags;return factory_save()||reject("failed_intent_commit_readback");}
 void mark(uint16_t flags){w.record.flags|=flags;}
 bool unseal(){if(w.unsealAttempted)return reject("failed_unseal_once_cap");gauge_delay_ms(5000);if(!factory_physical(3)||!factory_identity())return reject("failed_unseal_precondition");w.unsealAttempted=true;w.accessTouched=true;
  const bool sent=gauge_access_word(AccessWord::UnsealFirst)&&gauge_access_word(AccessWord::UnsealSecond);const bool observed=gauge_wait_security(2);return (sent&&observed)||reject("failed_unseal_once");}
 bool full(){if(w.fullAttempted)return reject("failed_full_once_cap");gauge_delay_ms(5000);if(!factory_physical(2)||!factory_identity())return reject("failed_full_precondition");w.fullAttempted=true;w.accessTouched=true;
  const bool sent=gauge_access_word(AccessWord::Full)&&gauge_access_word(AccessWord::Full);const bool observed=gauge_wait_security(1);return (sent&&observed)||reject("failed_full_once");}
 bool read131(){
  if(!factory_physical(1)||!factory_identity())return reject("failed_full_access_read_guard");
  for(size_t i=0;i<131;++i){const auto& f=scale_recovery::layout[i];if(!factory_runtime())return reject("failed_runtime_changed_during_read");if(!factory_read(f.address,w.first)||!factory_read(f.address,w.second)||!mosaico_gauge::authenticateDmResponse(w.first,f.address)||!mosaico_gauge::authenticateDmResponse(w.second,f.address))return reject("failed_dm_auth_or_transport");
   for(unsigned k=0;k<f.width;++k){if(w.first[2+k]!=w.second[2+k])return reject("failed_dm_unstable");w.raw[67+i*7+k]=w.second[2+k];}
   // Catalogue profile-1 locations are observations, not an active-profile assertion.
   ++w.record.count;gauge_feed();
  }
  factory::sealBackup(w.raw);w.record.sourceCrc=factory::sourceCrc(w.raw);if(!factory_physical(1)||!factory_identity())return reject("failed_after_read_guard");return factory::validBackup(w.raw,w.record)||reject("failed_full131_shape");
 }
 bool close(){
  // Never send Seal for unknown security, CFG, CAL or uninitialized state.
  uint16_t op=0;if(!gauge_word(0x3a,op)||!factory::safe(op))return reject("failed_cleanup_unknown_cfg_cal");
  if(((op>>1)&3)==3)return true;
  if(!w.accessTouched)return reject("failed_cleanup_unowned_access");
  gauge_delay_ms(5000);
  if(!gauge_word(0x3a,op)||!factory::safe(op))return reject("failed_cleanup_after_grace_unsafe");
  if(((op>>1)&3)==3)return true;
  if(w.sealAttempted)return reject("failed_cleanup_seal_once_cap");
  w.sealAttempted=true;
  const bool sent=gauge_access_word(AccessWord::Seal);const bool observed=gauge_wait_security(3);return (sent&&observed)||reject("failed_cleanup_seal_once");
 }
 bool final(){return (factory_physical(3,&w.record.after)&&factory_identity())||reject("failed_final_factory3k_identity");}
 bool backup(){
  if(!factory::validBackup(w.raw,w.record))return reject("failed_backup_shape");
  nvs_handle_t h=0;
  if(nvs_open(factory::nvsNamespace,NVS_READWRITE,&h)!=ESP_OK)return reject("failed_backup_open");
  size_t n=w.io.size();auto e=nvs_get_blob(h,w.key,w.io.data(),&n);if(e==ESP_ERR_NVS_NOT_FOUND){e=nvs_set_blob(h,w.key,w.raw.data(),w.raw.size());if(e==ESP_OK)e=nvs_commit(h);if(e==ESP_OK){n=w.io.size();e=nvs_get_blob(h,w.key,w.io.data(),&n);}}
  nvs_close(h);return (e==ESP_OK&&n==w.io.size()&&w.io==w.raw&&factory::validBackup(w.io,w.record))||reject("failed_backup_collision_commit_readback");
 }
 bool finish(bool success){w.record.state=success?factory::State::Completed:factory::State::Failed;const bool saved=factory_save();w.complete=success&&saved;w.live=w.complete;if(w.complete)w.reason="factory3k_fresh_full131_durable_sealed_no_parameter_write";else if(!saved)w.reason="failed_final_journal_commit_readback";factory_stats();return w.complete;}
};
uint16_t factory_observed_word(uint16_t address){if(!factory_workspace||!factory_workspace->attempted)return 0;const auto& w=*factory_workspace;for(size_t i=0;i<w.record.count;++i)if(scale_recovery::layout[i].address==address){const auto* p=w.raw.data()+67+i*7;return (uint16_t(p[0])<<8)|p[1];}return 0;}
bool factory_report(char* out,size_t n,bool ok){const auto* w=factory_workspace;const int count=std::snprintf(out,n,"reason=%s namespace=gauge3kaudit schema=1 state=%u attempted_this_boot=%d complete=%d fresh_this_boot=%d generation=%lu source_crc=%08lx fields=%u backup_key=%s available_entries=%u capture_peak_entries=%u final_plus_next_entries=%u required_entries=%u future_gscale_entries=%u future_headroom_ready=%d persisted_record_valid=%d observed_cache_valid=%d observed_profile1_dc=%u observed_profile1_fcc=%u observed_profile1_cfg=%04x profile1_anchor_matches_standard3k=%d calibration_ccoffset_8000=%d profile_active_not_asserted=1 first_FA_field=9180 known_keys_unseal_once=%d full_once=%d seal_once=%d no_CFG=1 no_0091=1 parameter_writes=0 legacy_failed_preserved=1 bootinfo_unchanged=1",w?w->reason:"psram_unavailable",unsigned(w?uint8_t(w->record.state):0),w&&w->attempted,w&&w->complete,w&&w->live,static_cast<unsigned long>(w?w->record.generation:0),static_cast<unsigned long>(w?w->record.sourceCrc:0),unsigned(w?w->record.count:0),w&&w->key[0]?w->key:"none",w?w->available:0,unsigned(factory::capturePeak),unsigned(factory::finalAndNext),unsigned(factory::requiredEntries),unsigned(factory::nextScaleEntries),w&&w->complete&&w->headroomValid&&w->available>=factory::nextScaleEntries,w&&w->persistedValid,w&&w->attempted&&w->record.count>0,factory_observed_word(0x929f),factory_observed_word(0x929d),factory_observed_word(0x929b),w&&w->record.count==131&&factory_observed_word(0x929f)==3000&&factory_observed_word(0x929d)==3000,factory_observed_word(0x9180)==0x8000,w&&w->unsealAttempted,w&&w->fullAttempted,w&&w->sealAttempted);return count>=0&&size_t(count)<n&&ok;}
}
bool Hal::gaugeFactoryAuditCapture(char* out,size_t n){if(!factory_capacity(out,n,1024))return false;ScaleStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);if(!factory_storage())return factory_report(out,n,false);auto& w=*factory_workspace;if(!lease.held){w.reason="blocked_storage_lease";return factory_report(out,n,false);}std::lock_guard<std::mutex> battery(battery_mutex);FactoryBackend backend(w);const bool ok=factory::capture(backend);return factory_report(out,n,ok);}
bool Hal::gaugeFactoryAuditStatus(char* out,size_t n){if(!factory_capacity(out,n,1024))return false;ScaleStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);if(!factory_storage())return factory_report(out,n,false);auto& w=*factory_workspace;if(!lease.held){w.reason="blocked_storage_lease";return factory_report(out,n,false);}bool ok=true;if(!w.attempted){const int loaded=factory_load();ok=loaded>=0;if(loaded==1){w.record=w.decoded;w.reason="readonly_persisted_record_not_current_observation";factory_key(w.record.generation,w.key);}else w.reason=loaded==0?"readonly_missing":"readonly_invalid";}factory_stats();return factory_report(out,n,ok);}
bool Hal::gaugeFactoryAuditField(size_t i,char* out,size_t n){if(!factory_capacity(out,n,256))return false;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);if(i>=131||!factory_workspace||i>=factory_workspace->record.count||!factory_workspace->attempted){std::snprintf(out,n,"error=factory_field_requires_authenticated_current_boot_cache index=%u",unsigned(i));return false;}const auto& w=*factory_workspace;const auto& f=scale_recovery::layout[i];const auto* p=w.raw.data()+67+i*7;const int count=std::snprintf(out,n,"index=%u address=%04x width=%u type=%s raw=%02x%02x%02x%02x generation=%lu source_crc=%08lx fresh_this_boot=%d complete=%d durable=%d closed_verified=%d source_stage=%s no_live_unit_assertion=1",unsigned(i),f.address,unsigned(f.width),factory_type(i),p[0],p[1],p[2],p[3],static_cast<unsigned long>(w.record.generation),static_cast<unsigned long>(w.record.sourceCrc),w.live,w.complete,w.complete&&bool(w.record.flags&factory::BackupVerified),bool(w.record.flags&factory::CloseVerified),w.complete?"complete":"failed_or_partial");return count>=0&&size_t(count)<n;}
