// Explicit console-only RAM reconciliation. No startup caller, persistent waiver,
// arbitrary key export, access keys, CFG commands, or parameter writer.
#include "mosaico_native_recovery_model.h"
#include <esp_ota_ops.h>
namespace {
namespace native_recovery=mosaico_native_recovery;
bool native_read_once(native_recovery::Evidence& e){
 nvs_handle_t h=0;if(nvs_open("gaugecal",NVS_READONLY,&h)!=ESP_OK)return false;
 auto read=[&](const char* key,void* p,size_t n){size_t actual=n;return nvs_get_blob(h,key,p,&actual)==ESP_OK&&actual==n;};
 const bool ok=read("reload_v1",e.reload.data(),28)&&read("fcc_boot_v1",e.cold,48)&&read("fcc_fix_v1",e.runtime,48);
 nvs_close(h);return ok;
}
bool native_evidence_locked(native_recovery::Evidence& e){
 native_recovery::Evidence second{};
 const auto mac=GetHAL().getFactoryMac();
 return native_read_once(e)&&native_read_once(second)&&e.reload==second.reload&&
  mosaico_fcc_limit::equalRecord(e.cold,second.cold)&&mosaico_fcc_limit::equalRecord(e.runtime,second.runtime)&&
  native_recovery::valid(e,mac.data());
}
bool native_report(char* out,size_t size,const char* reason,bool ok){
 const auto* r=scale_runtime;
 const int n=std::snprintf(out,size,"reason=%s ram_only=1 resolved_this_boot=%d source_reason=%s evidence_crc=%08lx proof_generation=%lu failed_records_preserved=1 keys=0 parameter_writes=0 nvs_writes=0",reason,r&&r->nativeResolvedThisBoot,r&&r->nativeOriginalReason[0]?r->nativeOriginalReason:"none",static_cast<unsigned long>(r?r->nativeEvidenceCrc:0),static_cast<unsigned long>(r?r->nativeProofGeneration:0));
 return n>=0&&size_t(n)<size&&ok;
}
bool native_capacity(char* out,size_t size){if(out&&size>=768)return true;if(out&&size)std::snprintf(out,size,"error=native_capacity_require_768");return false;}
// Root wires only unexpected standard/security/calibration mismatch here, under
// transaction then battery lock. Expected scale trial mutations are excluded.
void native_recovery_revoke_locked(){
 if(scale_mutation_in_progress||!scale_runtime||!scale_runtime->nativeResolvedThisBoot)return;
 auto& r=*scale_runtime;r.nativeResolvedThisBoot=false;
 gauge_boot_info.status=Hal::GaugeBootReloadStatus::Critical;
 std::snprintf(gauge_boot_info.reason,sizeof(gauge_boot_info.reason),"%s",r.nativeOriginalReason);
}
void native_recovery_check_sample_locked(uint16_t dc,uint16_t fcc,uint16_t op,bool valid){
#ifdef MOSAICO_FACTORY_SCALE_RUNTIME
 if(factory_scale_present_locked()){native_recovery_revoke_locked();return;}
#endif
 if(scale_mutation_in_progress)return;
  uint16_t expected=65;
 if(scale_runtime&&scale_load_status==scale_journal::Status::Ready&&scale_runtime->journal.loaded.selected>=0){
  const auto& r=*scale_runtime;const auto& s=r.journal.loaded.records[size_t(r.journal.loaded.selected)].snapshot;
  if(s.phase==scale_recovery::Phase::Applied&&r.appliedVerifiedThisBoot&&s.generation==r.verifiedGeneration&&s.epoch==r.verifiedEpoch)expected=130;
 }
 if(!valid||dc!=expected||fcc!=expected||((op>>1)&3)!=3||(op&0x0401)||!(op&0x20))native_recovery_revoke_locked();
}
bool native_proof_locked(){
 if(!scale_runtime)return false;
 const auto& r=*scale_runtime;
 if(scale_load_status!=scale_journal::Status::Ready||r.journal.loaded.selected<0)return false;
 const auto& s=r.journal.loaded.records[size_t(r.journal.loaded.selected)].snapshot;
 return native_recovery::fresh({true,s.phase==scale_recovery::Phase::Original,
  r.originalVerifiedThisBoot&&s.generation==r.verifiedGeneration&&s.epoch==r.verifiedEpoch,r.freshBackupDurable,
  gauge_threshold_cached.readable==mosaico_threshold::fieldCount&&gauge_threshold_cached.anchors&&
   gauge_calibration_cached.readable==mosaico_calibration::fieldCount&&gauge_public_extra_cached.complete&&
   gauge_public_extra_cached.readable==mosaico_public_extra::fieldCount&&
   r.observed.count==131&&r.observed.authenticated&&r.observed.sealed&&r.observed.cfg0&&r.journal.scratch.snapshot.count==131&&
   scale_journal::imageDigest(r.journal.scratch,false)==0x91336fc0U,
  r.nativePreparedAuditGeneration==gauge_threshold_cached_generation&&gauge_public_extra_generation==gauge_threshold_cached_generation,
  r.freshSourceCrc,r.nativePreparedAuditGeneration,r.nativePreparedUs,esp_timer_get_time()});
}
}
bool Hal::gaugeNativeRecoveryEvidence(char* out,size_t size){
 if(!native_capacity(out,size))return false;
 ScaleStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 if(!lease.held)return native_report(out,size,"blocked_storage_lease",false);
 native_recovery::Evidence e{};if(!native_evidence_locked(e))return native_report(out,size,"blocked_evidence_missing_length_crc_schema_mac_or_unstable",false);
 size_t used=0;bool ok=true;
 auto hex=[&](const char* key,const uint8_t* p,size_t n){
  int count=std::snprintf(out+used,size-used,"%s_byteLen=%u %s_raw=",key,unsigned(n),key);
  if(count<0||size_t(count)>=size-used){ok=false;return;}used+=size_t(count);
  for(size_t i=0;i<n&&ok;++i){count=std::snprintf(out+used,size-used,"%02x",p[i]);if(count!=2||size-used<=2){ok=false;return;}used+=2;}
  if(used+1>=size){ok=false;return;}out[used++]=' ';out[used]=0;
 };
 hex("reload_v1",e.reload.data(),28);if(ok)hex("fcc_boot_v1",e.cold,48);if(ok)hex("fcc_fix_v1",e.runtime,48);
 if(!ok)return false;
 // Additional read-only backup of the current normal transaction. The three
 // historical blobs and their evidence_crc below remain byte-for-byte scoped
 // exactly as before; malformed/missing current records expose only load codes.
 const auto mac=GetHAL().getFactoryMac();
 mosaico_gauge::Journal nominal{};mosaico_gauge::AccessJournal access{};
 const auto txLoad=gauge_load_journal(nominal,mac.data());
 const auto accessLoad=gauge_load_access(access,mac.data());
 if(txLoad==ESP_OK)hex("tx_v1",reinterpret_cast<const uint8_t*>(&nominal),sizeof(nominal));
 if(accessLoad==ESP_OK)hex("access_v1",reinterpret_cast<const uint8_t*>(&access),sizeof(access));
 if(!ok)return false;
 const int extra=std::snprintf(out+used,size-used,"tx_v1_load=%d access_v1_load=%d ",int(txLoad),int(accessLoad));
 if(extra<0||size_t(extra)>=size-used)return false;used+=size_t(extra);
 const int n=std::snprintf(out+used,size-used,"evidence_crc=%08lx crc_not_auth=1 readonly=1",static_cast<unsigned long>(native_recovery::digest(e)));
 return n>=0&&size_t(n)<size-used;
}
bool Hal::gaugeNativeRecoveryResolve(uint32_t expectedEvidenceCrc,char* out,size_t size){
 if(!native_capacity(out,size))return false;
 ScaleStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
#ifdef MOSAICO_FACTORY_SCALE_RUNTIME
 if(factory_scale_present_locked())return native_report(out,size,"blocked_GSJ3_owned_no_native_waiver",false);
#endif
 if(!lease.held)return native_report(out,size,"blocked_storage_lease",false);
 const auto* running=esp_ota_get_running_partition();esp_ota_img_states_t state{};
 if(!running||esp_ota_get_state_partition(running,&state)!=ESP_OK||state!=ESP_OTA_IMG_VALID||!scale_runtime_gate()||scale_mutation_in_progress)
  return native_report(out,size,"blocked_actual_valid_or_runtime_gate",false);
 // Reloading gscale is read-only; do not mint or refresh a prepare proof here.
 if(scale_load_locked(true)!=scale_journal::Status::Ready||!native_proof_locked())return native_report(out,size,"blocked_fresh_strict131_prepare_proof",false);
 auto& r=*scale_runtime;
 const bool previous=r.nativeResolvedThisBoot;
 const char* source=previous?r.nativeOriginalReason:gauge_boot_info.reason;
 constexpr char prefix[]="boot_critical_failed_no_retry";
 if(gauge_boot_info.attempted||std::strncmp(source,prefix,sizeof(prefix)-1)||
  (!previous&&gauge_boot_info.status!=Hal::GaugeBootReloadStatus::Critical)||
  (previous&&(gauge_boot_info.status!=Hal::GaugeBootReloadStatus::Skipped||r.nativeEvidenceCrc!=expectedEvidenceCrc)))
  return native_report(out,size,"blocked_original_boot_critical",false);
 native_recovery::Evidence e{};const auto mac=GetHAL().getFactoryMac();
 if(!native_evidence_locked(e)||!native_recovery::exact(e,mac.data())||native_recovery::digest(e)!=expectedEvidenceCrc)
  return native_report(out,size,"blocked_expected_failed_evidence",false);
 std::lock_guard<std::mutex> battery(battery_mutex);
 uint16_t rm=0,fcc=0;
 if(!scale_runtime_gate()||!native_proof_locked()||!scale_standard_original_locked()||!gauge_word(0x10,rm)||!gauge_word(0x12,fcc)||fcc!=65||rm>fcc)
  return native_report(out,size,"blocked_fresh_standard_identity_rm",false);
 if(!previous)std::snprintf(r.nativeOriginalReason,sizeof(r.nativeOriginalReason),"%s",gauge_boot_info.reason);
 r.nativeResolvedThisBoot=true;r.nativeEvidenceCrc=expectedEvidenceCrc;r.nativeProofGeneration=r.nativePreparedAuditGeneration;
 gauge_boot_info.status=Hal::GaugeBootReloadStatus::Skipped;
 std::snprintf(gauge_boot_info.reason,sizeof(gauge_boot_info.reason),"native131_observed_this_boot_failed_records_preserved");
 return native_report(out,size,previous?"already_resolved_same_proof_this_boot":"native131_observed_this_boot_failed_records_preserved",true);
}
