// CLOSED factory3000 rollback / nominal65 virtual130 trial. Not a calibration.
// This separate GSJ3 runtime NEVER interprets Original as physical/native65.
#include "mosaico_factory_scale_journal.h"
#include <ota/ota_public_key.h>
#include <mbedtls/pk.h>
#include <new>
#include "nvs.h"
#include <algorithm>
namespace {
namespace factory_scale_journal=mosaico_factory_scale_journal;
namespace factory_scale_model=mosaico_factory_scale;
// Capability contract UTF-8 literal, no terminating newline in contract hash:
// GSJ3|Factory3000RollbackToNominal65Virtual130|source=70a767be|target=b5b0f0d6|owned17|rollback3000=UNKNOWN_UNTRUSTED|applied_units=2|boot_restore_after_SDK_VALID|deadline_seconds=300|no_Failed_waiver
constexpr const char* factory_scale_contract_digest="e13f6e771556ad0a507112532f3a2e63139ff6c7433a2ebc1cea022cf8df30fa";
struct FactoryScaleRuntime {
 factory_scale_journal::StorageWorkspace journal{};
 factory_scale_journal::Record record{}; // MUST NOT alias saveNvs scratch/loaded.
 scale_recovery::Observation observed{};
 MosaicoOta::VerifiedImageDescriptor images[2]{};
 esp_app_desc_t descriptions[2]{};
 uint8_t imageElf[2][32]{},buffer[256]{};
 char decodedProof[1537]{},details[1536]{};
 bool loaded=false,everSeen=false,preparedThisBoot=false,appliedVerifiedThisBoot=false;
 uint32_t verifiedGeneration=0;uint64_t verifiedEpoch=0;
 int64_t preparedUs=0,revalidatedUs=0;
 bool imagesSet=false,bootRestoreAttempted=false,active=false,hostPowerAttested=false;
 bool operation=false,accessTouched=false,keyFailed=false,sealAttempted=false;
 bool applyAttempted=false,restoreAttempted=false,postExitFull131=false,sealedStandardFallback=false,protectionSealed=false;
 bool preKeySpaceRejected=false,keysStarted=false,thirdValue=false,possibleDirty=false,ownedDirty=false;
 bool noOpRestore=false,inheritedCfgExitRequired=false,untouchedMismatch=false,unsafeCalibration=false,invalidOffset=false;
 size_t unexpectedCount=0;uint16_t firstUnexpected=0;uint8_t firstRaw[4]{},firstOriginal[4]{};
 uint32_t observedGeneration=0;uint64_t observedEpoch=0;
 scale_recovery::Phase observedPhase=scale_recovery::Phase::Original;
 int64_t observedReadMs=0,appliedUs=0,nextPollUs=0;
 const char* observedStage="none";const char* nextReadStage="unknown";const char* reason="not_prepared";
};
FactoryScaleRuntime* factory_scale_rt=nullptr;
std::atomic<bool> factory_scale_protection{true};
bool factory_scale_safety_fault=false;
factory_scale_journal::Status factory_scale_status=factory_scale_journal::Status::IoError;
bool factory_scale_storage_locked(){
 if(factory_scale_rt)return true;
 void* p=heap_caps_malloc(sizeof(FactoryScaleRuntime),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
 if(!p){factory_scale_protection.store(true,std::memory_order_release);return false;}
 factory_scale_rt=new(p)FactoryScaleRuntime{};return true;
}
bool factory_scale_rt_fail(const char* why){if(factory_scale_rt)factory_scale_rt->reason=why;return false;}
void factory_scale_update_protection_locked(){
 bool stable=false;
 if(factory_scale_rt&&factory_scale_status==factory_scale_journal::Status::Ready){const auto& l=factory_scale_rt->journal.loaded;
  stable=l.selected>=0&&l.records[size_t(l.selected)].snapshot.phase==scale_recovery::Phase::Original;}
 factory_scale_protection.store((factory_scale_rt&&factory_scale_rt->operation)||!(stable||factory_scale_status==factory_scale_journal::Status::Missing),std::memory_order_release);
}
void factory_scale_invalidate_locked(){if(factory_scale_rt){factory_scale_rt->appliedVerifiedThisBoot=false;factory_scale_rt->preparedThisBoot=false;}}
factory_scale_journal::Status factory_scale_load_locked(bool refresh=false){
 if(!factory_scale_storage_locked())return factory_scale_journal::Status::IoError;
 auto& t=*factory_scale_rt;
 if(!t.loaded||refresh){
  factory_scale_status=factory_scale_journal::loadNvs(GetHAL().getFactoryMac(),t.journal);t.loaded=true;
  if(factory_scale_status==factory_scale_journal::Status::Missing&&t.everSeen)factory_scale_status=factory_scale_journal::Status::Corrupt;
  if(factory_scale_status!=factory_scale_journal::Status::Missing)t.everSeen=true;
  if(factory_scale_status==factory_scale_journal::Status::Ready){const auto& l=t.journal.loaded;
   if(l.selected<0||!factory_scale_journal::shape(l.records[size_t(l.selected)]))factory_scale_status=factory_scale_journal::Status::Rejected;
   else{const auto& s=l.records[size_t(l.selected)].snapshot;
    if(s.generation!=t.verifiedGeneration||s.epoch!=t.verifiedEpoch)factory_scale_invalidate_locked();}
  }else factory_scale_invalidate_locked();
 }
 factory_scale_update_protection_locked();return factory_scale_status;
}
// RO ownership hooks. No old scale/native functions, keys or control commands.
bool factory_scale_present_locked(){return factory_scale_load_locked()!=factory_scale_journal::Status::Missing;}
bool factory_scale_boot_fence_locked(){return factory_scale_load_locked()==factory_scale_journal::Status::Missing;}
mosaico_gauge_physical::UnitContext factory_scale_context_locked(){
 mosaico_gauge_physical::UnitContext c{};
 if(factory_scale_load_locked()!=factory_scale_journal::Status::Ready)return c;
 const auto& t=*factory_scale_rt;const auto& s=t.journal.loaded.records[size_t(t.journal.loaded.selected)].snapshot;
 c.generation=s.generation;c.unitEpoch=s.epoch;
 if(s.phase==scale_recovery::Phase::Original)c.phase=mosaico_gauge_physical::Phase::Original; // factor0 UNTRUSTED
 else if(s.phase==scale_recovery::Phase::Pending)c.phase=mosaico_gauge_physical::Phase::Pending;
 else if(s.phase==scale_recovery::Phase::Restoring)c.phase=mosaico_gauge_physical::Phase::Restoring;
 else{c.phase=mosaico_gauge_physical::Phase::Applied;
  if(t.appliedVerifiedThisBoot&&t.active&&s.generation==t.verifiedGeneration&&s.epoch==t.verifiedEpoch){c.factor=2;c.verified=true;}}
 return c;
}
bool factory_scale_audit_allowed_locked(){
 const auto status=factory_scale_load_locked();
 if(!factory_scale_rt||factory_scale_rt->operation||factory_scale_rt->active)return false;
 return status==factory_scale_journal::Status::Missing||(status==factory_scale_journal::Status::Ready&&factory_scale_rt->journal.loaded.selected>=0&&factory_scale_rt->journal.loaded.records[size_t(factory_scale_rt->journal.loaded.selected)].snapshot.phase==scale_recovery::Phase::Original);
}
bool factory_scale_maintenance_original_cached_locked(){
 if(!factory_scale_rt||!factory_scale_rt->loaded||factory_scale_status!=factory_scale_journal::Status::Ready||(factory_scale_rt->journal.loaded.selected<0||factory_scale_rt->journal.loaded.selected>1))return false;
 const auto& t=*factory_scale_rt;return t.journal.loaded.records[size_t(t.journal.loaded.selected)].snapshot.phase==scale_recovery::Phase::Original&&!t.operation&&!t.active&&!t.thirdValue&&!t.unsafeCalibration&&!t.untouchedMismatch&&!factory_scale_safety_fault&&!factory_scale_protection.load(std::memory_order_acquire);
}
bool factory_scale_maintenance_cached_safe_locked(){
 if(!factory_scale_rt||!factory_scale_rt->loaded||factory_scale_safety_fault)return false;
 const auto& t=*factory_scale_rt;
 return (factory_scale_status==factory_scale_journal::Status::Missing&&!t.everSeen&&!t.operation&&!t.active)||factory_scale_maintenance_original_cached_locked();
}
bool factory_scale_protected(){return factory_scale_protection.load(std::memory_order_acquire);}
bool factory_scale_space_locked(){nvs_stats_t stats{};return nvs_get_stats(nullptr,&stats)==ESP_OK&&stats.available_entries>=factory_scale_journal::nvsRequiredAvailableEntries;}
bool factory_scale_prepared_fresh(){
 if(!factory_scale_rt||!factory_scale_rt->preparedThisBoot)return false;
 const auto now=esp_timer_get_time();return now>=factory_scale_rt->preparedUs&&now-factory_scale_rt->preparedUs<=120000000;
}
bool factory_scale_safety_address(uint16_t a){return (a>=0x91b5&&a<=0x91db)||a==0x91b4||a==0x91dd||a==0x91df||a==0x9206||a==0x9180;}
bool factory_scale_rt_physical(bool restore){
 if(!gauge||!factory_runtime())return factory_scale_rt_fail("actual_SDK_VALID_quiet_radios_phase2_required");
 uint16_t op=0,mv=0,temp=0,current=0,average=0;
 if(!gauge_word(0x3a,op)||!gauge_word(8,mv)||!gauge_word(6,temp)||!gauge_word(0x0c,current)||!gauge_word(0x14,average))return factory_scale_rt_fail("fresh_safety_read");
 const int bound=restore?260:30;
 return (!(op&1)&&(op&0x20)&&mv>=(restore?3500:4000)&&mv<=4350&&temp>=2832&&temp<=3131&&
  int16_t(current)>=-bound&&int16_t(current)<=bound&&int16_t(average)>=-bound&&int16_t(average)<=bound)||factory_scale_rt_fail("fresh_safety_envelope");
}
bool factory_scale_rt_exact_identity(){return factory_identity()||factory_scale_rt_fail("exact_gauge_identity");}
bool factory_scale_rt_known_access(){
 mosaico_gauge::AccessJournal a{};const auto mac=GetHAL().getFactoryMac();
 return (gauge_load_access(a,mac.data())==ESP_OK&&mosaico_gauge::validAccessJournal(a,mac.data())&&a.priorSecurity==3&&a.unsealVerified&&a.fullVerified&&
 !mosaico_gauge::failedDefaultAttempt(a)&&(a.state==uint8_t(mosaico_gauge::AccessState::Opened)||a.state==uint8_t(mosaico_gauge::AccessState::Restored)))||factory_scale_rt_fail("known_default_prior3_required");
}
bool factory_scale_owned_intent(){
 if(factory_scale_load_locked()!=factory_scale_journal::Status::Ready)return false;
 const auto p=factory_scale_rt->journal.loaded.records[size_t(factory_scale_rt->journal.loaded.selected)].snapshot.phase;
 return p==scale_recovery::Phase::Pending||p==scale_recovery::Phase::Restoring;
}
const esp_partition_t* factory_scale_rt_partition(unsigned slot){return slot<2?esp_partition_find_first(ESP_PARTITION_TYPE_APP,slot?ESP_PARTITION_SUBTYPE_APP_OTA_1:ESP_PARTITION_SUBTYPE_APP_OTA_0,nullptr):nullptr;}
bool factory_scale_rt_version(const char* v){return !std::strcmp(v,"0.13.95-r1-mosaico-ota")||!std::strcmp(v,"0.13.95-r2-mosaico-ota");}
bool factory_scale_rt_verify_image(unsigned slot,const MosaicoOta::VerifiedImageDescriptor& image){
    const auto* part=factory_scale_rt_partition(slot);esp_ota_img_states_t state{};
    if(!part||!image.size||image.size>part->size||!factory_scale_rt_version(image.version)||
       esp_ota_get_state_partition(part,&state)!=ESP_OK||state!=ESP_OTA_IMG_VALID||
       esp_ota_get_partition_description(part,&factory_scale_rt->descriptions[slot])!=ESP_OK)return factory_scale_rt_fail("images_partition_valid_description");
    const auto& desc=factory_scale_rt->descriptions[slot];
    if(std::strncmp(desc.version,image.version,sizeof(desc.version))||
       std::strncmp(desc.project_name,"Stopwatch-Mosaico",sizeof(desc.project_name)))return factory_scale_rt_fail("images_exact_project_version");
    bool elfNonzero=false;for(unsigned i=0;i<32;++i){factory_scale_rt->imageElf[slot][i]=desc.app_elf_sha256[i];elfNonzero|=desc.app_elf_sha256[i]!=0;}
    if(!elfNonzero)return factory_scale_rt_fail("images_missing_elf_identity");
    if(psa_crypto_init()!=PSA_SUCCESS)return factory_scale_rt_fail("images_psa_crypto_init");
    psa_hash_operation_t operation=PSA_HASH_OPERATION_INIT;
    bool ok=psa_hash_setup(&operation,PSA_ALG_SHA_256)==PSA_SUCCESS;
    for(size_t offset=0;ok&&offset<image.size;){
        const size_t count=std::min<size_t>(sizeof(factory_scale_rt->buffer),image.size-offset);gauge_feed();
        ok=esp_partition_read(part,offset,factory_scale_rt->buffer,count)==ESP_OK&&
           psa_hash_update(&operation,factory_scale_rt->buffer,count)==PSA_SUCCESS;offset+=count;
    }
    uint8_t digest[32]{};size_t digestLength=0;
    if(ok)ok=psa_hash_finish(&operation,digest,sizeof(digest),&digestLength)==PSA_SUCCESS&&digestLength==32;
    (void)psa_hash_abort(&operation);
    return (ok&&!std::memcmp(digest,image.hash,32))||factory_scale_rt_fail("images_full_signed_bin_sha256");
}
bool factory_scale_rt_images_current(){
    if(!factory_scale_rt||!factory_scale_rt->imagesSet)return false;
    // Recheck under the operation's OTA storage lease before first mutation.
    for(unsigned i=0;i<2;++i)if(!factory_scale_rt_verify_image(i,factory_scale_rt->images[i])){factory_scale_rt->imagesSet=false;return false;}
    return true;
}
bool factory_scale_rt_read_field(size_t index,uint8_t (&raw)[4]){
    if(index>=131)return factory_scale_rt_fail("trial_read_index");
    const auto& f=factory_scale_rt->record.snapshot.fields[index];
    if(f.address!=scale_recovery::layout[index].address||f.width!=scale_recovery::layout[index].width)return factory_scale_rt_fail("trial_read_layout");
    uint8_t previous[4]{};
    for(unsigned pass=0;pass<2;++pass){
        uint8_t block[36]{};bool read=false;
        for(const auto& field:mosaico_threshold::fields)if(uint16_t(field.selector)==f.address)read=gauge_threshold_read(field.selector,block);
        for(const auto& field:mosaico_calibration::fields)if(uint16_t(field.selector)==f.address)read=gauge_calibration_read(field.selector,block);
        for(const auto& field:mosaico_public_extra::fields)if(uint16_t(field.selector)==f.address)read=gauge_public_extra_read(field.selector,block);
        if(!read||block[0]!=uint8_t(f.address)||block[1]!=uint8_t(f.address>>8)||block[35]!=36||
           block[34]!=mosaico_gauge::macChecksum(block,34))return factory_scale_rt_fail("trial_authenticated_field_read");
        for(size_t j=0;j<f.width;++j){if(pass&&previous[j]!=block[2+j])return factory_scale_rt_fail("trial_unstable_field");previous[j]=block[2+j];}
    }
    for(size_t j=0;j<4;++j)raw[j]=j<f.width?previous[j]:0;
    return true;
}
bool factory_scale_rt_owned(uint16_t address){
    switch(address){case 0x9184:case 0x9188:case 0x91de:case 0x91fb:case 0x9201:case 0x920e:case 0x9210:
    case 0x9217:case 0x9228:case 0x922a:case 0x922c:case 0x923c:case 0x9264:case 0x926b:case 0x929d:case 0x929f:case 0x92ab:return true;}
    return false;
}
bool factory_scale_rt_safety_field(uint16_t address){
    return factory_scale_safety_address(address);
}
bool factory_scale_rt_read_all(){
    const auto& snapshot=factory_scale_rt->record.snapshot;
    factory_scale_rt->observedGeneration=snapshot.generation;factory_scale_rt->observedEpoch=snapshot.epoch;factory_scale_rt->observedPhase=snapshot.phase;
    factory_scale_rt->observedReadMs=esp_timer_get_time()/1000;factory_scale_rt->observedStage=factory_scale_rt->nextReadStage;
    auto& o=factory_scale_rt->observed;o.count=0;o.authenticated=false;o.sealed=false;o.cfg0=false;
    for(size_t i=0;i<131;++i){if(!factory_scale_rt_read_field(i,o.raw[i]))return false;++o.count;gauge_feed();}
    o.authenticated=true;return true;
}
// Preserve complete current authenticated131 in PSRAM, including unexpected
// untouched values. Owned17 third values always prohibit overwrite. Untouched
// unknown values never become writes; safety-calibration changes prohibit ALL
// writes. Only 9180 has an autonomous role; 9182 is NOT generalized into it.
bool factory_scale_rt_inspect(bool restore,bool requireDesired){
    auto& trial=*factory_scale_rt;trial.unexpectedCount=0;trial.firstUnexpected=0;
    trial.ownedDirty=false;trial.untouchedMismatch=false;trial.unsafeCalibration=false;trial.invalidOffset=false;
    bool desiredOwned=true,applyExpected=true;
    for(size_t i=0;i<131;++i){const auto& f=trial.record.snapshot.fields[i];const auto* raw=trial.observed.raw[i];
        bool original=true,target=true;
        for(size_t j=0;j<f.width;++j){original&=raw[j]==f.original[j];target&=raw[j]==f.target[j];}
        const bool owned=factory_scale_rt_owned(f.address);
        if(owned){trial.ownedDirty|=!original;desiredOwned&=restore?original:target;}
        const bool unexpected=owned?(!original&&!target):!original;
        if(unexpected){
            if(!trial.unexpectedCount){trial.firstUnexpected=f.address;
                for(size_t j=0;j<4;++j){trial.firstRaw[j]=raw[j];trial.firstOriginal[j]=f.original[j];}}
            ++trial.unexpectedCount;
            if(owned)trial.thirdValue=true;
            else{trial.untouchedMismatch=true;if(factory_scale_rt_safety_field(f.address)){trial.unsafeCalibration=true;factory_scale_safety_fault=true;}}
        }
        if(!restore)applyExpected&=requireDesired?(owned?target:original):original;
    }
    if(!restore&&(trial.invalidOffset||!applyExpected))return factory_scale_rt_fail("apply_source_or_offset_i2_range_mismatch");
    if(trial.thirdValue)return factory_scale_rt_fail("trial_unexpected_owned_third_value_no_overwrite");
    if(trial.unsafeCalibration)return factory_scale_rt_fail("restore_unsafe_calibration_changed_no_writes");
    if(requireDesired&&!desiredOwned)return factory_scale_rt_fail("trial_owned17_final_mismatch");
    return true;
}

bool factory_scale_rt_intent(scale_recovery::Phase phase){
 if(factory_scale_load_locked(true)!=factory_scale_journal::Status::Ready)return factory_scale_rt_fail("intent_no_trusted_GSJ3");
 auto& t=*factory_scale_rt;const auto& current=t.journal.loaded.records[size_t(t.journal.loaded.selected)];
 if(!factory_scale_journal::sameRecoveryImage(t.record,current))return factory_scale_rt_fail("intent_recovery_image_changed");
 if(phase==scale_recovery::Phase::Restoring&&current.snapshot.phase==phase){t.record=current;return true;}
 t.record=current;
 if(t.record.snapshot.generation==UINT32_MAX||t.record.snapshot.epoch==UINT64_MAX)return factory_scale_rt_fail("intent_counter_exhausted");
 ++t.record.snapshot.generation;++t.record.snapshot.epoch;t.record.snapshot.phase=phase;
 factory_scale_protection.store(true,std::memory_order_release);
 if(!factory_scale_rt_physical(phase==scale_recovery::Phase::Restoring||phase==scale_recovery::Phase::Original))return false;
 const auto status=factory_scale_journal::saveNvs(t.record,{true,true,true},t.journal);
 factory_scale_status=status;t.loaded=true;t.everSeen=true;
 if(status!=factory_scale_journal::Status::Ready||t.journal.loaded.selected<0)return factory_scale_rt_fail("intent_commit_readback_failed");
 const auto& saved=t.journal.loaded.records[size_t(t.journal.loaded.selected)];
 return (saved.snapshot.generation==t.record.snapshot.generation&&saved.snapshot.epoch==t.record.snapshot.epoch&&saved.snapshot.phase==phase&&factory_scale_journal::sameRecoveryImage(saved,t.record))||factory_scale_rt_fail("intent_readback_mismatch");
}
struct FactoryScaleBackend {
    bool restoring=false;
    void revoke(){factory_scale_invalidate_locked();}
    bool admit(bool restore){
        restoring=restore;
        if(factory_scale_safety_fault)return factory_scale_rt_fail("trial_known_safety_calibration_fault_latched");
        if(!factory_scale_rt_physical(restore)||!factory_scale_rt_known_access())return false;
        if(restore){
            if(factory_scale_rt->preKeySpaceRejected&&!factory_scale_rt->keysStarted)return factory_scale_rt_fail("apply_prekey_space_rejected_no_automatic_keys");
            if(factory_scale_rt->restoreAttempted)return factory_scale_rt_fail("restore_once_per_boot_no_key_retry");
            return !factory_scale_rt->thirdValue||factory_scale_rt_fail("restore_third_value_no_overwrite");
        }
        if(!factory_scale_space_locked())return factory_scale_rt_fail("apply_nvs_available_entries_headroom");
        if(factory_scale_rt->applyAttempted)return factory_scale_rt_fail("apply_once_per_boot");
        if(!factory_scale_prepared_fresh()||!factory_scale_rt_images_current())return factory_scale_rt_fail("apply_explicit_prepare_and_two_audited_images_required");
        // Method entry is only wired by root to local UART CONFIRM_EXTERNAL_POWER.
        // This is HOST-ATTESTED stable unique V1 5 V, not measured device evidence.
        uint16_t current=0,average=0;
        if(!(gauge_word(0x0c,current)&&gauge_word(0x14,average)&&int16_t(current)>=-30&&int16_t(current)<=30&&
            int16_t(average)>=-30&&int16_t(average)<=30))return factory_scale_rt_fail("apply_quiet_original_current");
        factory_scale_rt->applyAttempted=true;return true;
    }
    bool intent(bool restore){return factory_scale_rt_intent(restore?scale_recovery::Phase::Restoring:scale_recovery::Phase::Pending);}
    bool open(bool restore){
        uint16_t op=0;if(!factory_scale_rt_physical(restore)||!gauge_word(0x3a,op)||!factory_scale_rt_exact_identity())return factory_scale_rt_fail("trial_open_identity_status");
        if(((op>>1)&3)==1){
            if(!factory_scale_owned_intent()||(op&1)||!(op&0x20))return factory_scale_rt_fail("restore_retained_fa_requires_owned_intent_cal0_init1");
            if(!restore)return factory_scale_rt_fail("apply_requires_sealed_open3_path");
            if(factory_scale_rt->restoreAttempted)return factory_scale_rt_fail("restore_open_once_per_boot");
            factory_scale_rt->restoreAttempted=true;factory_scale_rt->bootRestoreAttempted=true;
            factory_scale_rt->accessTouched=true;return true; // journal-owned retained FA during one rollback
        }
        if(factory_scale_rt->keyFailed)return factory_scale_rt_fail("trial_key_attempt_failed_no_retry");
        if(((op>>1)&3)!=3||(op&0x0401))return factory_scale_rt_fail("trial_open_requires_sealed_normal");
        factory_scale_rt->accessTouched=true;factory_scale_rt->sealAttempted=false;
        gauge_delay_ms(5000);
        if(!factory_scale_rt_physical(restore)||!gauge_word(0x3a,op)||((op>>1)&3)!=3||(op&0x0401))return false;
        nvs_stats_t preKeyStats{};
        if(!restore&&(nvs_get_stats(nullptr,&preKeyStats)!=ESP_OK||preKeyStats.available_entries<54)){factory_scale_rt->preKeySpaceRejected=true;return factory_scale_rt_fail("apply_nvs_space_before_first_keys");}
        if(restore){factory_scale_rt->restoreAttempted=true;factory_scale_rt->bootRestoreAttempted=true;}
        factory_scale_rt->keysStarted=true;
        const bool unseal=gauge_access_word(AccessWord::UnsealFirst)&&gauge_access_word(AccessWord::UnsealSecond);
        const bool unsealed=gauge_wait_security(2);
        if(!unseal||!unsealed){factory_scale_rt->keyFailed=true;return factory_scale_rt_fail("trial_unseal_once_ack_or_security");}
        gauge_delay_ms(5000);
        if(!factory_scale_rt_physical(restore))return false;
        const bool full=gauge_access_word(AccessWord::Full)&&gauge_access_word(AccessWord::Full);
        const bool fullAccess=gauge_wait_security(1);
        if(!full||!fullAccess){factory_scale_rt->keyFailed=true;return factory_scale_rt_fail("trial_full_once_ack_or_security");}
        return true;
    }
    bool observe(bool restore){
        factory_scale_rt->nextReadStage=restore?"restore_pre_cfg_fa":"apply_pre_cfg_fa";
        if(!factory_scale_rt_read_all()||!factory_scale_rt_inspect(restore,false))return false;
        factory_scale_rt->noOpRestore=restore&&!factory_scale_rt->ownedDirty;
        return true;
    }
    bool enter(){
        if(restoring&&factory_scale_rt->noOpRestore){
            // Do not enter CFG again. If CFG1 was inherited from a failed
            // forward packet, the already observed owned-original/safe profile
            // still needs ONE necessary exit; CFG0 needs no reinitialization.
            uint16_t inherited=0;
            if(!gauge_word(0x3a,inherited)||(inherited&1)||((inherited>>1)&3)==0||factory_scale_safety_fault)
                return factory_scale_rt_fail("restore_noop_unknown_config_or_safety");
            factory_scale_rt->inheritedCfgExitRequired=bool(inherited&0x0400);
            if(factory_scale_rt->inheritedCfgExitRequired&&((inherited>>1)&3)!=1)return factory_scale_rt_fail("restore_inherited_cfg_requires_fa");
            return true;
        }
        uint16_t op=0;if(!gauge_word(0x3a,op)||((op>>1)&3)!=1||(op&1))return factory_scale_rt_fail("trial_enter_requires_fa_cal0");
        if(op&0x0400)return true; // retained CFG after interrupted forward stage
        const bool sent=gauge_control(GaugeCommand::EnterConfig);const bool observed=gauge_wait_config(true);
        return (sent&&observed)||factory_scale_rt_fail("trial_enter_once_ack_or_cfg");
    }
    bool write(bool restore){
        if(restore&&factory_scale_rt->noOpRestore)return true;
        for(size_t i=0;i<131;++i){const auto& f=factory_scale_rt->record.snapshot.fields[i];
            if(!factory_scale_rt_owned(f.address))continue; // includes autonomous9180: NEVER a write
            bool differs=false;for(size_t j=0;j<f.width;++j)differs|=f.original[j]!=f.target[j];if(!differs)continue;
            uint8_t current[4]{};if(!factory_scale_rt_read_field(i,current))return false;
            bool original=true,target=true;for(size_t j=0;j<f.width;++j){original&=current[j]==f.original[j];target&=current[j]==f.target[j];}
            if(!original&&!target){factory_scale_rt->thirdValue=true;return factory_scale_rt_fail("trial_unexpected_owned_third_value_no_overwrite");}
            const auto* desired=restore?f.original:f.target;bool equal=true;
            for(size_t j=0;j<f.width;++j)equal&=current[j]==desired[j];
            if(equal)continue;
            uint16_t op=0;
            if(!factory_scale_rt_physical(restore)||!gauge_word(0x3a,op)||((op>>1)&3)!=1||!(op&0x0400)||(op&1))return factory_scale_rt_fail("trial_single_write_fa_cfg_cal_gate");
            uint8_t packet[7]={0x3e,uint8_t(f.address),uint8_t(f.address>>8),0,0,0,0};
            for(size_t j=0;j<f.width;++j)packet[3+j]=desired[j];
            factory_scale_rt->possibleDirty=true;gauge_feed();
            // Set dirty BEFORE the one packet attempt: lost ACK is not evidence
            // of no write. Never resend the packet or commit after uncertain ACK.
            if(i2c_master_transmit(gauge,packet,3+f.width,20)!=ESP_OK)return factory_scale_rt_fail("trial_parameter_packet_ack");
            gauge_delay_ms(1);
            const uint8_t commit[]={0x60,mosaico_gauge::macChecksum(packet+1,2+f.width),uint8_t(4+f.width)};
            if(i2c_master_transmit(gauge,commit,sizeof(commit),20)!=ESP_OK)return factory_scale_rt_fail("trial_parameter_commit_ack");
            gauge_delay_ms(20);
            if(!factory_scale_rt_read_field(i,current))return false;
            for(size_t j=0;j<f.width;++j)if(current[j]!=desired[j])return factory_scale_rt_fail("trial_parameter_double_readback_mismatch");
        }
        return true;
    }
    bool verify(bool restore,bool postExit=false){
        factory_scale_rt->nextReadStage=postExit?"post_exit_fa":restore&&factory_scale_rt->noOpRestore?"restore_no_cfg_fa":"pre_exit_cfg_fa";
        return factory_scale_rt_read_all()&&factory_scale_rt_inspect(restore,true);
    }
    bool exit(){
        if(restoring&&factory_scale_rt->noOpRestore&&!factory_scale_rt->inheritedCfgExitRequired)return true;
        return gauge_exit_config()||factory_scale_rt_fail("trial_exit_0091_once_failed");
    }
    bool seal(){
        uint16_t op=0;if(!gauge_word(0x3a,op)||!mosaico_gauge::configExitAccepted(op))return factory_scale_rt_fail("trial_seal_requires_cfg0_init1");
        if(((op>>1)&3)==3){
            // The established 0091 path can already be SEALED, with public DM
            // denied. Preserve the pre-exit authenticated131 proof and require
            // final sealed standard+exact identity; never claim post-exit DM.
            factory_scale_rt->sealedStandardFallback=true;factory_scale_rt->protectionSealed=gauge_wait_security(3);return factory_scale_rt->protectionSealed;
        }
        if(((op>>1)&3)!=1||!verify(restoring,true))return factory_scale_rt_fail("trial_post_exit_accessible_full131_mismatch");
        factory_scale_rt->postExitFull131=true;
        if(factory_scale_rt->sealAttempted)return factory_scale_rt_fail("trial_seal_no_retry");
        factory_scale_rt->sealAttempted=true;const bool sent=gauge_access_word(AccessWord::Seal);const bool observed=gauge_wait_security(3);
        factory_scale_rt->protectionSealed=observed;
        return (sent&&observed)||factory_scale_rt_fail("trial_seal_once_ack_or_security");
    }
    bool final(bool restore){
        if(!factory_scale_rt_physical(restore)||!factory_scale_rt_exact_identity())return false;
        uint16_t op=0,dc=0,fcc=0,rm=0,soc=0;
        const uint16_t expected=restore?3000:130;
        if(!gauge_word(0x3a,op)||!gauge_word(0x3c,dc)||!gauge_word(0x12,fcc)||!gauge_word(0x10,rm)||!gauge_word(0x2c,soc))return factory_scale_rt_fail("trial_final_standard_read");
        // 0091 reinitialization may move SOC; that is NOT evidence of a cure.
        const int difference=int(rm)*100-int(soc)*int(fcc);
        return (mosaico_gauge::configExitAccepted(op)&&((op>>1)&3)==3&&dc==expected&&fcc==expected&&rm<=fcc&&soc<=100&&
            difference>=-int(fcc)*10&&difference<=int(fcc)*10)||factory_scale_rt_fail("trial_final_sealed_dc_fcc_rm_soc");
    }
    bool complete(bool restore){
        if(restore&&factory_scale_rt->untouchedMismatch)return factory_scale_rt_fail("owned17_original_untouched_unexpected_kept_restoring");
        return factory_scale_rt_intent(restore?scale_recovery::Phase::Original:scale_recovery::Phase::Applied);
    }
    void publish(bool restore){
        auto& t=*factory_scale_rt;const auto& s=t.record.snapshot;
        t.appliedVerifiedThisBoot=!restore;t.verifiedGeneration=s.generation;t.verifiedEpoch=s.epoch;
        t.active=!restore;t.appliedUs=restore?0:esp_timer_get_time();
        t.reason=restore?"factory3000_restored_UNTRUSTED_not_native_no_Failed_waiver":"factory130_verified_mapping2_not_capacity_calibration";
        factory_scale_update_protection_locked();
    }
    void protect(){
        factory_scale_protection.store(true,std::memory_order_release);
        factory_scale_rt->protectionSealed=false;
        // Never Seal CFG1/CAL1/unknown, and never issue a cleanup 0091 that could
        // commit/reinitialize an unclassified mixed image. Durable intent stays.
        uint16_t op=0;
        if(!gauge_word(0x3a,op)||(op&0x0401)||!(op&0x0020)||((op>>1)&3)==0){
            factory_scale_rt->reason="protection_config_access_unresolved_no_seal_no_exit";return;
        }
        if(((op>>1)&3)==3){factory_scale_rt->protectionSealed=true;return;} // already protected: no repeated Seal
        if(!factory_scale_rt->accessTouched||factory_scale_rt->sealAttempted)return;
        factory_scale_rt->sealAttempted=true;(void)gauge_access_word(AccessWord::Seal);
        factory_scale_rt->protectionSealed=gauge_wait_security(3);
    }
};

bool factory_scale_begin_locked(){
 if(!factory_scale_storage_locked()||factory_scale_load_locked(true)!=factory_scale_journal::Status::Ready)return false;
 auto& t=*factory_scale_rt;t.record=t.journal.loaded.records[size_t(t.journal.loaded.selected)];
 if(!factory_scale_journal::shape(t.record))return factory_scale_rt_fail("closed_GSJ3_profile_required");
 t.operation=true;t.accessTouched=false;t.sealAttempted=false;t.possibleDirty=false;
 t.noOpRestore=false;t.inheritedCfgExitRequired=false;t.ownedDirty=false;t.untouchedMismatch=false;t.unsafeCalibration=false;
 t.postExitFull131=false;t.sealedStandardFallback=false;t.protectionSealed=false;
 factory_scale_update_protection_locked();return true;
}
bool factory_scale_restore_locked(){
 if(!factory_scale_begin_locked())return false;
 auto& t=*factory_scale_rt;t.active=false;
 std::lock_guard<std::mutex> battery(battery_mutex);FactoryScaleBackend backend;
 const auto result=mosaico_scale_trial::restore(backend);t.operation=false;factory_scale_update_protection_locked();
 return result==mosaico_scale_trial::Result::Restored;
}
struct FactoryScaleStorageLease {bool held=MosaicoOta::tryStorageLease();~FactoryScaleStorageLease(){if(held)MosaicoOta::releaseStorageLease();}};
bool factory_scale_capacity(char* out,size_t n,size_t required=1024){if(out&&n>=required)return true;if(out&&n)std::snprintf(out,n,"error=factory_scale_details_capacity_require_%u",unsigned(required));return false;}
bool factory_scale_report(char* out,size_t n,bool ok){
 const auto* t=factory_scale_rt;const auto* s=t&&factory_scale_status==factory_scale_journal::Status::Ready&&t->journal.loaded.selected>=0?&t->journal.loaded.records[size_t(t->journal.loaded.selected)].snapshot:nullptr;
 const auto c=factory_scale_context_locked();
 const int k=std::snprintf(out,n,"reason=%s success=%d schema=3 namespace=gscale_f3_v1 profile=Factory3000RollbackToNominal65Virtual130 phase=%u generation=%lu epoch=%llu factor=%lld verified=%d capacity_calibrated=0 original3000_UNTRUSTED=1 legacy_Failed_preserved=1 prepared_fresh=%d images_capability_verified=%d protection=%d observed131=%u authenticated=%d third_value=%d unexpected_count=%u first_unexpected=%04x post_exit_full131=%d sealed_standard_fallback=%d deadline_seconds=300 source_crc=70a767be target_crc=b5b0f0d6 contract=%s",
 t?t->reason:"psram_unavailable",ok,unsigned(s?uint8_t(s->phase):255),static_cast<unsigned long>(s?s->generation:0),static_cast<unsigned long long>(s?s->epoch:0),static_cast<long long>(c.factor),c.verified,factory_scale_prepared_fresh(),t&&t->imagesSet,factory_scale_protected(),unsigned(t?t->observed.count:0),t&&t->observed.authenticated,t&&t->thirdValue,unsigned(t?t->unexpectedCount:0),t?t->firstUnexpected:0,t&&t->postExitFull131,t&&t->sealedStandardFallback,factory_scale_contract_digest);
 if(k<0||size_t(k)>=n){std::snprintf(out,n,"error=factory_scale_details_truncated");return false;}return ok;
}
bool factory_scale_two_fields(const cJSON* object,const char* a,const char* b){
 if(!cJSON_IsObject(object)){return false;}
 unsigned seen=0;
 for(const cJSON* v=object->child;v;v=v->next){const unsigned bit=v->string&&!std::strcmp(v->string,a)?1:v->string&&!std::strcmp(v->string,b)?2:0;if(!bit||(seen&bit))return false;seen|=bit;}return seen==3;
}
bool factory_scale_capability(const cJSON* signature,const MosaicoOta::VerifiedImageDescriptor& image){
 // Compact wire proof carries only detached signature. The immutable local
 // contract digest is always included in the signed canonical message.
 if(!cJSON_IsString(signature)||!signature->valuestring||std::strlen(signature->valuestring)>104)return false;
 uint8_t sig[80]{},hash[32]{};size_t sigSize=0,hashSize=0;char sha[65]{};
 for(unsigned i=0;i<32;++i)std::snprintf(sha+2*i,3,"%02x",image.hash[i]);
 const int n=std::snprintf(factory_scale_rt->details,sizeof(factory_scale_rt->details),"MOSAICO-FACTORY-SCALE-CAP-v1\n%s\n%s\n%lu\n%s\n",factory_scale_contract_digest,image.version,static_cast<unsigned long>(image.size),sha);
 if(n<=0||size_t(n)>=sizeof(factory_scale_rt->details)||psa_crypto_init()!=PSA_SUCCESS||
 mbedtls_base64_decode(sig,sizeof(sig),&sigSize,reinterpret_cast<const uint8_t*>(signature->valuestring),std::strlen(signature->valuestring))||sigSize<8||sigSize>72||
 psa_hash_compute(PSA_ALG_SHA_256,reinterpret_cast<const uint8_t*>(factory_scale_rt->details),size_t(n),hash,sizeof(hash),&hashSize)!=PSA_SUCCESS||hashSize!=32)return false;
 mbedtls_pk_context key;mbedtls_pk_init(&key);
 int rc=mbedtls_pk_parse_public_key(&key,reinterpret_cast<const unsigned char*>(MosaicoOtaPublicKey),sizeof(MosaicoOtaPublicKey));
 if(rc==0&&(!mbedtls_pk_can_do_psa(&key,MBEDTLS_PK_ALG_ECDSA(PSA_ALG_SHA_256),PSA_KEY_USAGE_VERIFY_HASH)||mbedtls_pk_get_bitlen(&key)!=256))rc=-1;
 if(rc==0)rc=mbedtls_pk_verify(&key,MBEDTLS_MD_SHA256,hash,sizeof(hash),sig,sigSize);
 mbedtls_pk_free(&key);return rc==0;
}
// Poll is the ONLY automatic recovery dispatch. RO hooks never attempt keys.
bool factory_scale_poll_locked(){
 if(factory_scale_load_locked()!=factory_scale_journal::Status::Ready)return factory_scale_status!=factory_scale_journal::Status::Missing;
 auto& t=*factory_scale_rt;const auto& s=t.journal.loaded.records[size_t(t.journal.loaded.selected)].snapshot;
 if(s.phase==scale_recovery::Phase::Original||t.operation||!factory_runtime())return true;
 const int64_t now=esp_timer_get_time();if(now>=0&&now<t.nextPollUs)return true;t.nextPollUs=now+1000000;
 if(!t.active){
  if(t.bootRestoreAttempted||t.restoreAttempted)return true;
  FactoryScaleStorageLease lease;if(!lease.held)return true;
  (void)factory_scale_restore_locked();return true;
 }
 bool due=false;
 {std::unique_lock<std::mutex> battery(battery_mutex,std::try_to_lock);if(!battery.owns_lock())return true;
  uint16_t mv=0,temp=0,op=0,dc=0,fcc=0;
  const bool read=gauge_word(8,mv)&&gauge_word(6,temp)&&gauge_word(0x3a,op)&&gauge_word(0x3c,dc)&&gauge_word(0x12,fcc);
  due=!read||mv<=3600||mv>4350||temp<2832||temp>3131||!mosaico_gauge::configExitAccepted(op)||((op>>1)&3)!=3||dc!=130||fcc!=130||now<t.appliedUs||now-t.appliedUs>=300000000;
 }
 if(due){FactoryScaleStorageLease lease;if(lease.held){t.active=false;factory_scale_invalidate_locked();(void)factory_scale_restore_locked();}}
 return true;
}
} // anonymous namespace

bool Hal::gaugeFactoryScaleStatus(char* out,size_t n){
 if(!factory_scale_capacity(out,n)){return false;}
std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 const auto s=factory_scale_load_locked(true);return factory_scale_report(out,n,s==factory_scale_journal::Status::Ready||s==factory_scale_journal::Status::Missing);
}
bool Hal::gaugeFactoryScalePrepare(char* out,size_t n){
 if(!factory_scale_capacity(out,n)){return false;}

 std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 if(!factory_scale_storage_locked())return factory_scale_report(out,n,false);
 auto& t=*factory_scale_rt;const auto status=factory_scale_load_locked(true);t.preparedThisBoot=false;
 if((status!=factory_scale_journal::Status::Missing&&status!=factory_scale_journal::Status::Ready)||
 (status==factory_scale_journal::Status::Ready&&t.journal.loaded.records[size_t(t.journal.loaded.selected)].snapshot.phase!=scale_recovery::Phase::Original)){
  t.reason="prepare_requires_missing_or_Original_GSJ3";return factory_scale_report(out,n,false);}
 // Schema2 coexistence is explicitly forbidden, even with historical Failed.
 if(scale_load_locked(true)!=scale_journal::Status::Missing){t.reason="prepare_requires_legacy_gscale_missing";return factory_scale_report(out,n,false);}
 if(!factory_scale_space_locked()||!factory_runtime()){t.reason="prepare_headroom_or_runtime";return factory_scale_report(out,n,false);}
 {std::lock_guard<std::mutex> battery(battery_mutex);native_recovery::Evidence e{};const auto mac=getFactoryMac();
  if(!factory_physical(3)||!factory_identity()||!native_evidence_locked(e)||!native_recovery::exact(e,mac.data())){t.reason="prepare_exact_factory3000_failed_evidence";return factory_scale_report(out,n,false);}}
 const auto now=esp_timer_get_time();
 if(factory_workspace&&factory_workspace->attempted){
  if(!t.revalidatedUs||now<t.revalidatedUs||now-t.revalidatedUs>120000000){t.reason="prepare_prior_FA_attempt_not_owned_fresh_revalidation";return factory_scale_report(out,n,false);}
 }else{
  // NO OTA lease and NO battery_mutex here: revalidation owns both itself.
  if(!gaugeFactoryAuditRevalidate(t.details,sizeof(t.details))){t.reason="prepare_fresh_factory_revalidation_failed";return factory_scale_report(out,n,false);}
  t.revalidatedUs=esp_timer_get_time();
 }
 if(!factory_workspace||!factory_workspace->attempted||!factory_workspace->revalidation||!factory_workspace->complete||!factory_workspace->live||factory_workspace->record.count!=131||factory_workspace->record.sourceCrc!=factory_scale_model::factorySourceCrc){t.reason="prepare_requires_complete_currentboot_exact131";return factory_scale_report(out,n,false);}
 FactoryScaleStorageLease lease;if(!lease.held){t.reason="prepare_post_FA_OTA_lease";return factory_scale_report(out,n,false);}
 std::lock_guard<std::mutex> battery(battery_mutex);
 if(!factory_scale_rt_physical(false)||!factory_physical(3)||!factory_identity()||!factory_scale_space_locked())return factory_scale_report(out,n,false);
 t.record.~Record();new(&t.record) factory_scale_journal::Record{};t.record.profile=factory_scale_model::Profile::Factory3000RollbackToNominal65Virtual130;t.record.mac=getFactoryMac();
 auto& s=t.record.snapshot;s.identity={0x220,3,4};s.binding=scale_journal::binding(t.record.mac);s.layoutId=scale_recovery::Layout::Public131V1;s.scale=scale_recovery::Scale::Two;s.count=131;s.generation=1;s.epoch=1;s.phase=scale_recovery::Phase::Original;
 for(size_t i=0;i<131;++i){auto& f=s.fields[i];f.address=scale_recovery::layout[i].address;f.width=scale_recovery::layout[i].width;
  for(unsigned k=0;k<f.width;++k)f.original[k]=f.target[k]=factory_workspace->raw[67+i*7+k];
  for(const auto& change:factory_scale_model::changes)if(change.address==f.address)for(unsigned k=0;k<f.width;++k)f.target[k]=uint8_t(change.after>>(8*(f.width-1-k)));
 }
 if(!factory_scale_journal::shape(t.record)){t.reason="prepare_full131_closed_source_target_mismatch";return factory_scale_report(out,n,false);}
 if(status==factory_scale_journal::Status::Missing){
  factory_scale_status=factory_scale_journal::saveNvs(t.record,{true,true,true},t.journal);t.loaded=true;t.everSeen=true;
  if(factory_scale_status!=factory_scale_journal::Status::Ready){t.reason="prepare_Original_durable_readback_failed";factory_scale_update_protection_locked();return factory_scale_report(out,n,false);}
 }else{
  const auto& old=t.journal.loaded.records[size_t(t.journal.loaded.selected)];
  if(!factory_scale_journal::sameRecoveryImage(old,t.record)){t.reason="prepare_existing_original_image_mismatch";return factory_scale_report(out,n,false);}t.record=old;
 }
 const auto& saved=t.journal.loaded.records[size_t(t.journal.loaded.selected)];
 if(!factory_scale_journal::sameRecoveryImage(saved,t.record)){t.reason="prepare_original_readback_image_mismatch";return factory_scale_report(out,n,false);}
 t.verifiedGeneration=saved.snapshot.generation;t.verifiedEpoch=saved.snapshot.epoch;t.preparedUs=esp_timer_get_time();t.preparedThisBoot=true;
 t.reason="prepared_fresh131_Original_durable_NO_parameter_writes_UNTRUSTED3000";factory_scale_update_protection_locked();return factory_scale_report(out,n,true);
}
bool Hal::gaugeFactoryScaleImages(const char* proof,char* out,size_t n){
 if(!factory_scale_capacity(out,n)){return false;}
FactoryScaleStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 if(!factory_scale_storage_locked()){return factory_scale_report(out,n,false);}
 auto& t=*factory_scale_rt;t.imagesSet=false;
 if(!lease.held||!factory_runtime()||!proof||std::strlen(proof)>1400){t.reason="images_lease_runtime_length";return factory_scale_report(out,n,false);}
 size_t decoded=0;if(mbedtls_base64_decode(reinterpret_cast<uint8_t*>(t.decodedProof),sizeof(t.decodedProof)-1,&decoded,reinterpret_cast<const uint8_t*>(proof),std::strlen(proof))||!decoded||decoded>=sizeof(t.decodedProof)){t.reason="images_base64_invalid";return factory_scale_report(out,n,false);}for(size_t i=0;i<decoded;++i)if(!t.decodedProof[i]){t.reason="images_embedded_NUL";return factory_scale_report(out,n,false);}t.decodedProof[decoded]=0;
 const char* end=nullptr;cJSON* root=cJSON_ParseWithOpts(t.decodedProof,&end,true);bool ok=root&&cJSON_IsArray(root)&&cJSON_GetArraySize(root)==2;
 for(unsigned i=0;ok&&i<2;++i){const auto* item=cJSON_GetArrayItem(root,int(i));ok=factory_scale_two_fields(item,"image","signature");
  const auto* image=ok?cJSON_GetObjectItemCaseSensitive(item,"image"):nullptr;const auto* capability=ok?cJSON_GetObjectItemCaseSensitive(item,"signature"):nullptr;
  char* json=ok?cJSON_PrintUnformatted(image):nullptr;ok=json&&MosaicoOta::verifySignedImageDescriptor(json,t.images[i]);if(json)cJSON_free(json);
  if(ok)ok=factory_scale_capability(capability,t.images[i])&&factory_scale_rt_verify_image(i,t.images[i]);
 }
 if(root){cJSON_Delete(root);}
 if(ok&&!std::strcmp(t.images[0].version,t.images[1].version))ok=false;
 t.imagesSet=ok;t.reason=ok?"two_actual_SDK_VALID_signed_BINS_and_detached_profile_endorsements_verified":"images_manifest_capability_actual_BINS_rejected";return factory_scale_report(out,n,ok);
}
bool Hal::gaugeFactoryScaleApply(char* out,size_t n){
 if(!factory_scale_capacity(out,n)){return false;}
FactoryScaleStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 if(!lease.held||!factory_scale_begin_locked()){return factory_scale_report(out,n,false);}
 auto& t=*factory_scale_rt;
 t.hostPowerAttested=true; // ONLY root local-console CONFIRM_EXTERNAL_POWER route.
 if(t.record.snapshot.phase!=scale_recovery::Phase::Original){t.reason="apply_requires_Original";t.operation=false;factory_scale_update_protection_locked();return factory_scale_report(out,n,false);}
 std::lock_guard<std::mutex> battery(battery_mutex);FactoryScaleBackend backend;
 const auto result=mosaico_scale_trial::apply(backend);t.operation=false;factory_scale_update_protection_locked();return factory_scale_report(out,n,result==mosaico_scale_trial::Result::Applied);
}
bool Hal::gaugeFactoryScaleRestore(char* out,size_t n){
 if(!factory_scale_capacity(out,n)){return false;}
FactoryScaleStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 if(!lease.held||factory_scale_load_locked(true)!=factory_scale_journal::Status::Ready)return factory_scale_report(out,n,false);
 if(factory_scale_rt->journal.loaded.records[size_t(factory_scale_rt->journal.loaded.selected)].snapshot.phase==scale_recovery::Phase::Original){factory_scale_rt->reason="already_Original_UNTRUSTED_no_keys_no_CFG";return factory_scale_report(out,n,true);}
 return factory_scale_report(out,n,factory_scale_restore_locked());
}
bool Hal::gaugeFactoryScaleObservedField(size_t index,char* out,size_t n){
 if(!factory_scale_capacity(out,n,384)){return false;}
std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 if(!factory_scale_rt||index>=131||index>=factory_scale_rt->observed.count){std::snprintf(out,n,"cached=0 reason=index_not_observed_this_boot");return false;}
 const auto& t=*factory_scale_rt;const auto& f=t.record.snapshot.fields[index];uint32_t raw=0,original=0,target=0;
 for(unsigned j=0;j<f.width;++j){raw=(raw<<8)|t.observed.raw[index][j];original=(original<<8)|f.original[j];target=(target<<8)|f.target[j];}
 const int k=std::snprintf(out,n,"cached=1 index=%u count=%u authenticated=%d address=%04x width=%u rawBE=%08lx originalBE=%08lx targetBE=%08lx generation=%lu epoch=%llu read_ms=%lld stage=%s role=%s",unsigned(index),unsigned(t.observed.count),t.observed.authenticated,f.address,f.width,static_cast<unsigned long>(raw),static_cast<unsigned long>(original),static_cast<unsigned long>(target),static_cast<unsigned long>(t.observedGeneration),static_cast<unsigned long long>(t.observedEpoch),static_cast<long long>(t.observedReadMs),t.observedStage,factory_scale_rt_owned(f.address)?"owned17":"untouched114_neverwrite");return k>=0&&size_t(k)<n;
}
