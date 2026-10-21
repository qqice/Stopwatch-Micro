// Bounded, default-deny scale-2 trial. The portable coordinator is also compiled
// with synthetic fault backends: no hardware is required by those tests.
#ifndef MOSAICO_SCALE_TRIAL_COORDINATOR_DEFINED
#define MOSAICO_SCALE_TRIAL_COORDINATOR_DEFINED
namespace mosaico_scale_trial {
enum class Result {Blocked,Applied,Restored,FailedClosed};
template<class Backend> constexpr Result restore(Backend& b){
    b.revoke();
    if(!b.admit(true)||!b.intent(true)){b.protect();return Result::FailedClosed;}
    if(!b.open(true)||!b.observe(true)||!b.enter()||!b.write(true)||!b.verify(true)||
       !b.exit()||!b.seal()||!b.final(true)||!b.complete(true)){
        b.protect();return Result::FailedClosed;
    }
    b.publish(true);return Result::Restored;
}
template<class Backend> constexpr Result apply(Backend& b){
    if(!b.admit(false))return Result::Blocked;
    b.revoke();
    // No key, CFG or parameter operation can occur unless durable intent passed.
    if(!b.intent(false))return Result::FailedClosed;
    if(!b.open(false)||!b.observe(false)||!b.enter()||!b.write(false)||!b.verify(false)||
       !b.exit()||!b.seal()||!b.final(false)||!b.complete(false))return restore(b);
    b.publish(false);return Result::Applied;
}
} // namespace mosaico_scale_trial
#endif

#ifndef MOSAICO_SCALE_TRIAL_MODEL_ONLY
#include <esp_ota_ops.h>
#include <esp_app_desc.h>
#include <esp_partition.h>
#include <mbedtls/base64.h>
#include <psa/crypto.h>
#include <cJSON.h>
#include <cstring>

namespace {
struct ScaleTrialStorage {
    scale_journal::Record record{}; // never aliases journal load/decode scratch
    scale_recovery::Observation observed{};
    MosaicoOta::VerifiedImageDescriptor images[2]{};
    esp_app_desc_t descriptions[2]{};
    uint8_t imageElf[2][32]{};
    uint8_t buffer[256]{};
    char decodedProof[1537]{};
    bool imagesSet=false,bootRestoreAttempted=false,active=false,hostPowerAttested=false;
    bool operation=false,accessTouched=false,keyFailed=false,sealAttempted=false;
    bool applyAttempted=false,restoreAttempted=false,postExitFull131=false,sealedStandardFallback=false,protectionSealed=false;
    bool preKeySpaceRejected=false,keysStarted=false;
    bool thirdValue=false,possibleDirty=false,ownedDirty=false,noOpRestore=false,inheritedCfgExitRequired=false,untouchedMismatch=false,unsafeCalibration=false;
    bool offsetSeen=false,invalidOffset=false;
    uint16_t offsetPrimary=0,offsetBefore=0,offsetAfter=0;
    size_t unexpectedCount=0;
    uint16_t firstUnexpected=0;
    uint8_t firstRaw[4]{},firstOriginal[4]{};
    uint32_t observedGeneration=0;uint64_t observedEpoch=0;
    scale_recovery::Phase observedPhase=scale_recovery::Phase::Original;
    int64_t observedReadMs=0;const char* observedStage="none";const char* nextReadStage="unknown";
    int64_t appliedUs=0,nextPollUs=0;
    const char* reason="trial_not_admitted";
};
ScaleTrialStorage* scale_trial=nullptr; // exclusively transaction mutex owned
bool scale_trial_storage_locked(){
    if(scale_trial)return true;
    void* p=heap_caps_malloc(sizeof(ScaleTrialStorage),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!p)return false;
    scale_trial=new(p) ScaleTrialStorage{};return true;
}
bool scale_append_trial_diagnostics(char* out,size_t size){
    int count=-1;
    if(out&&size){
        if(!scale_trial)count=std::snprintf(out,size," observed131=none");
        else{const auto& t=*scale_trial;count=std::snprintf(out,size,
            " observed131=%u authenticated=%d unexpected_count=%u first_unexpected=%04x raw=%02x%02x%02x%02x orig=%02x%02x%02x%02x offset_primary=%04x offset_fresh_before=%04x offset_current_after=%04x volatile_drift_vs_primary=%d trial_offset_changed=%d",
            unsigned(t.observed.count),t.observed.authenticated,unsigned(t.unexpectedCount),t.firstUnexpected,
            t.firstRaw[0],t.firstRaw[1],t.firstRaw[2],t.firstRaw[3],t.firstOriginal[0],t.firstOriginal[1],t.firstOriginal[2],t.firstOriginal[3],
            t.offsetPrimary,t.offsetBefore,t.offsetAfter,t.offsetSeen&&t.offsetAfter!=t.offsetPrimary,t.offsetSeen&&t.offsetAfter!=t.offsetBefore);}
    }
    if(count<0||static_cast<size_t>(count)>=size){if(out&&size)std::snprintf(out,size,"error=scale_details_truncated");return false;}return true;
}
bool scale_trial_fail(const char* why){scale_trial->reason=why;return false;}
// No factory/test partitions and no generic flash range API.
const esp_partition_t* scale_trial_partition(unsigned slot){
    if(slot>1)return nullptr;
    return esp_partition_find_first(ESP_PARTITION_TYPE_APP,
        slot?ESP_PARTITION_SUBTYPE_APP_OTA_1:ESP_PARTITION_SUBTYPE_APP_OTA_0,nullptr);
}
bool scale_trial_version(const char* version){
    return !std::strcmp(version,"0.13.90-mosaico-ota")||!std::strcmp(version,"0.13.91-mosaico-ota");
}
// Exact signed BIN byte range (not esp_partition_get_sha256's image digest).
// app_elf_sha256 is retained for reporting/auditor correlation, not treated as a
// self-hash or a substitute for the signed full-BIN SHA checked below.
bool scale_trial_verify_image(unsigned slot,const MosaicoOta::VerifiedImageDescriptor& image){
    const auto* part=scale_trial_partition(slot);esp_ota_img_states_t state{};
    if(!part||!image.size||image.size>part->size||!scale_trial_version(image.version)||
       esp_ota_get_state_partition(part,&state)!=ESP_OK||state!=ESP_OTA_IMG_VALID||
       esp_ota_get_partition_description(part,&scale_trial->descriptions[slot])!=ESP_OK)return scale_trial_fail("images_partition_valid_description");
    const auto& desc=scale_trial->descriptions[slot];
    if(std::strncmp(desc.version,image.version,sizeof(desc.version))||
       std::strncmp(desc.project_name,"Stopwatch-Mosaico",sizeof(desc.project_name)))return scale_trial_fail("images_exact_project_version");
    bool elfNonzero=false;for(unsigned i=0;i<32;++i){scale_trial->imageElf[slot][i]=desc.app_elf_sha256[i];elfNonzero|=desc.app_elf_sha256[i]!=0;}
    if(!elfNonzero)return scale_trial_fail("images_missing_elf_identity");
    if(psa_crypto_init()!=PSA_SUCCESS)return scale_trial_fail("images_psa_crypto_init");
    psa_hash_operation_t operation=PSA_HASH_OPERATION_INIT;
    bool ok=psa_hash_setup(&operation,PSA_ALG_SHA_256)==PSA_SUCCESS;
    for(size_t offset=0;ok&&offset<image.size;){
        const size_t count=std::min<size_t>(sizeof(scale_trial->buffer),image.size-offset);gauge_feed();
        ok=esp_partition_read(part,offset,scale_trial->buffer,count)==ESP_OK&&
           psa_hash_update(&operation,scale_trial->buffer,count)==PSA_SUCCESS;offset+=count;
    }
    uint8_t digest[32]{};size_t digestLength=0;
    if(ok)ok=psa_hash_finish(&operation,digest,sizeof(digest),&digestLength)==PSA_SUCCESS&&digestLength==32;
    (void)psa_hash_abort(&operation);
    return (ok&&!std::memcmp(digest,image.hash,32))||scale_trial_fail("images_full_signed_bin_sha256");
}
bool scale_trial_images_current(){
    if(!scale_trial||!scale_trial->imagesSet||!scale_both_images_verified)return false;
    // Recheck under the operation's OTA storage lease before first mutation.
    for(unsigned i=0;i<2;++i)if(!scale_trial_verify_image(i,scale_trial->images[i])){scale_both_images_verified=false;return false;}
    return true;
}
// Restore does not require an external-power attestation: otherwise loss of V1
// could make the mandatory battery-safe restore impossible. It does retain all
// independently measured voltage/temperature/current and ownership gates.
bool scale_trial_physical(bool restore){
    if(!gauge||MosaicoOta::busy()||MosaicoOta::healthPending())return scale_trial_fail("trial_ota_or_gauge_gate");
    const auto p=GetNetworkQuota().powerStats();const auto b=GetCodexMicroBle().diagnostics();
    if(p.wifiRunning||b.advertising||b.connected||(!restore&&(!p.locked||p.phase!=2)))return scale_trial_fail("trial_radios_lock_gate");
    uint16_t op=0,mv=0,temp=0,current=0,average=0;
    if(!gauge_word(0x3a,op)||!gauge_word(0x08,mv)||!gauge_word(0x06,temp)||
       !gauge_word(0x0c,current)||!gauge_word(0x14,average))return scale_trial_fail("trial_fresh_physical_read");
    const int bound=restore?260:60;
    return (!(op&1)&&mv>=(restore?3500:4000)&&mv<=4350&&temp>=2832&&temp<=3181&&
        int16_t(current)>=-bound&&int16_t(current)<=bound&&int16_t(average)>=-bound&&int16_t(average)<=bound)||
        scale_trial_fail("trial_voltage_temperature_current");
}
bool scale_trial_exact_identity(){
    Hal::GaugeEngineSnapshot identity{};
    gauge_engine_locked(identity,mosaico_gauge_status::Explicit,0);
    const auto required=mosaico_gauge_engine::Identity|mosaico_gauge_engine::DefinitionMatch;
    return ((identity.validMask&required)==required&&gauge_identity())||scale_trial_fail("trial_exact_bq27220_fw3_build4_identity");
}
bool scale_trial_known_access(){
    mosaico_gauge::AccessJournal a{};const auto mac=GetHAL().getFactoryMac();
    return (gauge_load_access(a,mac.data())==ESP_OK&&a.priorSecurity==3&&a.unsealVerified&&a.fullVerified&&
        !mosaico_gauge::failedDefaultAttempt(a)&&
        (a.state==uint8_t(mosaico_gauge::AccessState::Opened)||a.state==uint8_t(mosaico_gauge::AccessState::Restored)))||
        scale_trial_fail("trial_known_default_prior3_required");
}
// Closed canonical 131-index reader. Typed transports remain independently
// allowlisted; even legacy FF echoes are rejected by this stricter trial layer.
bool scale_trial_read_field(size_t index,uint8_t (&raw)[4]){
    if(index>=131)return scale_trial_fail("trial_read_index");
    const auto& f=scale_trial->record.snapshot.fields[index];
    if(f.address!=scale_recovery::layout[index].address||f.width!=scale_recovery::layout[index].width)return scale_trial_fail("trial_read_layout");
    uint8_t previous[4]{};
    for(unsigned pass=0;pass<2;++pass){
        uint8_t block[36]{};bool read=false;
        for(const auto& field:mosaico_threshold::fields)if(uint16_t(field.selector)==f.address)read=gauge_threshold_read(field.selector,block);
        for(const auto& field:mosaico_calibration::fields)if(uint16_t(field.selector)==f.address)read=gauge_calibration_read(field.selector,block);
        for(const auto& field:mosaico_public_extra::fields)if(uint16_t(field.selector)==f.address)read=gauge_public_extra_read(field.selector,block);
        if(!read||block[0]!=uint8_t(f.address)||block[1]!=uint8_t(f.address>>8)||block[35]!=36||
           block[34]!=mosaico_gauge::macChecksum(block,34))return scale_trial_fail("trial_authenticated_field_read");
        for(size_t j=0;j<f.width;++j){if(pass&&previous[j]!=block[2+j])return scale_trial_fail("trial_unstable_field");previous[j]=block[2+j];}
    }
    for(size_t j=0;j<4;++j)raw[j]=j<f.width?previous[j]:0;
    return true;
}
bool scale_trial_owned(uint16_t address){
    switch(address){case 0x9184:case 0x9188:case 0x91de:case 0x91fb:case 0x9201:case 0x920e:case 0x9210:
    case 0x9217:case 0x9228:case 0x922a:case 0x922c:case 0x923c:case 0x9264:case 0x926b:case 0x929d:case 0x929f:case 0x92ab:return true;}
    return false;
}
bool scale_trial_safety_field(uint16_t address){
    return scale_safety_address(address);
}
bool scale_trial_read_all(){
    const auto& snapshot=scale_trial->record.snapshot;
    scale_trial->observedGeneration=snapshot.generation;scale_trial->observedEpoch=snapshot.epoch;scale_trial->observedPhase=snapshot.phase;
    scale_trial->observedReadMs=esp_timer_get_time()/1000;scale_trial->observedStage=scale_trial->nextReadStage;
    auto& o=scale_trial->observed;o.count=0;o.authenticated=false;o.sealed=false;o.cfg0=false;
    for(size_t i=0;i<131;++i){if(!scale_trial_read_field(i,o.raw[i]))return false;++o.count;gauge_feed();}
    o.authenticated=true;return true;
}
// Preserve complete current authenticated131 in PSRAM, including unexpected
// untouched values. Owned17 third values always prohibit overwrite. Untouched
// unknown values never become writes; safety-calibration changes prohibit ALL
// writes. Only 9180 has an autonomous role; 9182 is NOT generalized into it.
bool scale_trial_inspect(bool restore,bool requireDesired){
    auto& trial=*scale_trial;trial.unexpectedCount=0;trial.firstUnexpected=0;
    trial.ownedDirty=false;trial.untouchedMismatch=false;trial.unsafeCalibration=false;trial.invalidOffset=false;
    bool desiredOwned=true,applyExpected=true;
    for(size_t i=0;i<131;++i){const auto& f=trial.record.snapshot.fields[i];const auto* raw=trial.observed.raw[i];
        bool original=true,target=true;
        for(size_t j=0;j<f.width;++j){original&=raw[j]==f.original[j];target&=raw[j]==f.target[j];}
        if(f.address==0x9180){
            trial.offsetPrimary=uint16_t(f.original[0])<<8|f.original[1];
            trial.offsetAfter=uint16_t(raw[0])<<8|raw[1];
            if(!trial.offsetSeen){trial.offsetBefore=trial.offsetAfter;trial.offsetSeen=true;}
            if(trial.offsetAfter==0x8000){
                trial.invalidOffset=true;trial.untouchedMismatch=true;
                if(!trial.unexpectedCount){trial.firstUnexpected=f.address;for(size_t j=0;j<4;++j){trial.firstRaw[j]=raw[j];trial.firstOriginal[j]=f.original[j];}}
                ++trial.unexpectedCount;
            }
            continue; // Never write or claim that historical drift happened in this trial.
        }
        const bool owned=scale_trial_owned(f.address);
        if(owned){trial.ownedDirty|=!original;desiredOwned&=restore?original:target;}
        const bool unexpected=owned?(!original&&!target):!original;
        if(unexpected){
            if(!trial.unexpectedCount){trial.firstUnexpected=f.address;
                for(size_t j=0;j<4;++j){trial.firstRaw[j]=raw[j];trial.firstOriginal[j]=f.original[j];}}
            ++trial.unexpectedCount;
            if(owned)trial.thirdValue=true;
            else{trial.untouchedMismatch=true;if(scale_trial_safety_field(f.address)){trial.unsafeCalibration=true;scale_known_safety_mismatch=true;}}
        }
        if(!restore)applyExpected&=requireDesired?(owned?target:original):original;
    }
    if(!restore&&(trial.invalidOffset||!applyExpected))return scale_trial_fail("apply_source_or_offset_i2_range_mismatch");
    if(trial.thirdValue)return scale_trial_fail("trial_unexpected_owned_third_value_no_overwrite");
    if(trial.unsafeCalibration)return scale_trial_fail("restore_unsafe_calibration_changed_no_writes");
    if(requireDesired&&!desiredOwned)return scale_trial_fail("trial_owned17_final_mismatch");
    return true;
}
bool scale_trial_intent(scale_recovery::Phase phase){
    if(scale_load_locked(true)!=scale_journal::Status::Ready)return scale_trial_fail("trial_intent_journal_failclosed");
    auto& record=scale_trial->record;
    const auto& current=scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)];
    if(!scale_journal::sameRecoveryImage(record,current)||!scale_reviewed_record(current))return scale_trial_fail("trial_intent_image_mismatch");
    if(phase==scale_recovery::Phase::Restoring&&current.snapshot.phase==phase){record=current;return true;} // existing durable restore intent
    record=current;
    if(record.snapshot.generation==UINT32_MAX||record.snapshot.epoch==UINT64_MAX)return scale_trial_fail("trial_intent_counter_exhausted");
    ++record.snapshot.generation;++record.snapshot.epoch;record.snapshot.phase=phase;
    scale_trial_protection.store(true,std::memory_order_release);
    const auto status=scale_journal::saveNvs(record,{true,true,true},scale_runtime->journal);
    if(status!=scale_journal::Status::Ready){scale_load_locked(true);return scale_trial_fail("trial_intent_commit_readback");}
    if(scale_load_locked(true)!=scale_journal::Status::Ready)return scale_trial_fail("trial_intent_reload");
    const auto& saved=scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)];
    if(saved.snapshot.generation!=record.snapshot.generation||saved.snapshot.epoch!=record.snapshot.epoch||saved.snapshot.phase!=phase||
       !scale_journal::sameRecoveryImage(saved,record))return scale_trial_fail("trial_intent_verified_record_mismatch");
    return true;
}
struct ScaleTrialBackend {
    bool restoring=false;
    void revoke(){scale_invalidate_context_locked();}
    bool admit(bool restore){
        restoring=restore;
        if(scale_known_safety_mismatch)return scale_trial_fail("trial_known_safety_calibration_fault_latched");
        if(!scale_trial_physical(restore)||!scale_trial_known_access())return false;
        if(restore){
            if(scale_trial->preKeySpaceRejected&&!scale_trial->keysStarted)return scale_trial_fail("apply_prekey_space_rejected_no_automatic_keys");
            if(scale_trial->restoreAttempted)return scale_trial_fail("restore_once_per_boot_no_key_retry");
            return !scale_trial->thirdValue||scale_trial_fail("restore_third_value_no_overwrite");
        }
        if(!scale_nvs_space_ready_locked())return scale_trial_fail("apply_nvs_available_entries_headroom");
        if(scale_trial->applyAttempted)return scale_trial_fail("apply_once_per_boot");
        if(!scale_runtime->originalVerifiedThisBoot||!scale_trial_images_current())return scale_trial_fail("apply_explicit_prepare_and_two_audited_images_required");
        // Method entry is only wired by root to local UART CONFIRM_EXTERNAL_POWER.
        // This is HOST-ATTESTED stable unique V1 5 V, not measured device evidence.
        uint16_t current=0,average=0;
        if(!(gauge_word(0x0c,current)&&gauge_word(0x14,average)&&int16_t(current)>=-30&&int16_t(current)<=30&&
            int16_t(average)>=-30&&int16_t(average)<=30))return scale_trial_fail("apply_quiet_original_current");
        scale_trial->applyAttempted=true;return true;
    }
    bool intent(bool restore){return scale_trial_intent(restore?scale_recovery::Phase::Restoring:scale_recovery::Phase::Pending);}
    bool open(bool restore){
        uint16_t op=0;if(!scale_trial_physical(restore)||!gauge_word(0x3a,op)||!scale_trial_exact_identity())return scale_trial_fail("trial_open_identity_status");
        if(((op>>1)&3)==1){
            if(!restore)return scale_trial_fail("apply_requires_sealed_open3_path");
            if(scale_trial->restoreAttempted)return scale_trial_fail("restore_open_once_per_boot");
            scale_trial->restoreAttempted=true;scale_trial->bootRestoreAttempted=true;
            scale_trial->accessTouched=true;return true; // journal-owned retained FA during one rollback
        }
        if(scale_trial->keyFailed)return scale_trial_fail("trial_key_attempt_failed_no_retry");
        if(((op>>1)&3)!=3||(op&0x0401))return scale_trial_fail("trial_open_requires_sealed_normal");
        scale_trial->accessTouched=true;scale_trial->sealAttempted=false;
        gauge_delay_ms(5000);
        if(!scale_trial_physical(restore)||!gauge_word(0x3a,op)||((op>>1)&3)!=3||(op&0x0401))return false;
        if(!restore&&!scale_nvs_space_ready_locked()){scale_trial->preKeySpaceRejected=true;return scale_trial_fail("apply_nvs_space_before_first_keys");}
        if(restore){scale_trial->restoreAttempted=true;scale_trial->bootRestoreAttempted=true;}
        scale_trial->keysStarted=true;
        const bool unseal=gauge_access_word(AccessWord::UnsealFirst)&&gauge_access_word(AccessWord::UnsealSecond);
        const bool unsealed=gauge_wait_security(2);
        if(!unseal||!unsealed){scale_trial->keyFailed=true;return scale_trial_fail("trial_unseal_once_ack_or_security");}
        gauge_delay_ms(5000);
        if(!scale_trial_physical(restore))return false;
        const bool full=gauge_access_word(AccessWord::Full)&&gauge_access_word(AccessWord::Full);
        const bool fullAccess=gauge_wait_security(1);
        if(!full||!fullAccess){scale_trial->keyFailed=true;return scale_trial_fail("trial_full_once_ack_or_security");}
        return true;
    }
    bool observe(bool restore){
        scale_trial->nextReadStage=restore?"restore_pre_cfg_fa":"apply_pre_cfg_fa";
        if(!scale_trial_read_all()||!scale_trial_inspect(restore,false))return false;
        scale_trial->noOpRestore=restore&&!scale_trial->ownedDirty;
        return true;
    }
    bool enter(){
        if(restoring&&scale_trial->noOpRestore){
            // Do not enter CFG again. If CFG1 was inherited from a failed
            // forward packet, the already observed owned-original/safe profile
            // still needs ONE necessary exit; CFG0 needs no reinitialization.
            uint16_t inherited=0;
            if(!gauge_word(0x3a,inherited)||(inherited&1)||((inherited>>1)&3)==0||scale_known_safety_mismatch)
                return scale_trial_fail("restore_noop_unknown_config_or_safety");
            scale_trial->inheritedCfgExitRequired=bool(inherited&0x0400);
            if(scale_trial->inheritedCfgExitRequired&&((inherited>>1)&3)!=1)return scale_trial_fail("restore_inherited_cfg_requires_fa");
            return true;
        }
        uint16_t op=0;if(!gauge_word(0x3a,op)||((op>>1)&3)!=1||(op&1))return scale_trial_fail("trial_enter_requires_fa_cal0");
        if(op&0x0400)return true; // retained CFG after interrupted forward stage
        const bool sent=gauge_control(GaugeCommand::EnterConfig);const bool observed=gauge_wait_config(true);
        return (sent&&observed)||scale_trial_fail("trial_enter_once_ack_or_cfg");
    }
    bool write(bool restore){
        if(restore&&scale_trial->noOpRestore)return true;
        for(size_t i=0;i<131;++i){const auto& f=scale_trial->record.snapshot.fields[i];
            if(!scale_trial_owned(f.address))continue; // includes autonomous9180: NEVER a write
            bool differs=false;for(size_t j=0;j<f.width;++j)differs|=f.original[j]!=f.target[j];if(!differs)continue;
            uint8_t current[4]{};if(!scale_trial_read_field(i,current))return false;
            bool original=true,target=true;for(size_t j=0;j<f.width;++j){original&=current[j]==f.original[j];target&=current[j]==f.target[j];}
            if(!original&&!target){scale_trial->thirdValue=true;return scale_trial_fail("trial_unexpected_owned_third_value_no_overwrite");}
            const auto* desired=restore?f.original:f.target;bool equal=true;
            for(size_t j=0;j<f.width;++j)equal&=current[j]==desired[j];
            if(equal)continue;
            uint16_t op=0;
            if(!scale_trial_physical(restore)||!gauge_word(0x3a,op)||((op>>1)&3)!=1||!(op&0x0400)||(op&1))return scale_trial_fail("trial_single_write_fa_cfg_cal_gate");
            uint8_t packet[7]={0x3e,uint8_t(f.address),uint8_t(f.address>>8),0,0,0,0};
            for(size_t j=0;j<f.width;++j)packet[3+j]=desired[j];
            scale_trial->possibleDirty=true;gauge_feed();
            // Set dirty BEFORE the one packet attempt: lost ACK is not evidence
            // of no write. Never resend the packet or commit after uncertain ACK.
            if(i2c_master_transmit(gauge,packet,3+f.width,20)!=ESP_OK)return scale_trial_fail("trial_parameter_packet_ack");
            gauge_delay_ms(1);
            const uint8_t commit[]={0x60,mosaico_gauge::macChecksum(packet+1,2+f.width),uint8_t(4+f.width)};
            if(i2c_master_transmit(gauge,commit,sizeof(commit),20)!=ESP_OK)return scale_trial_fail("trial_parameter_commit_ack");
            gauge_delay_ms(20);
            if(!scale_trial_read_field(i,current))return false;
            for(size_t j=0;j<f.width;++j)if(current[j]!=desired[j])return scale_trial_fail("trial_parameter_double_readback_mismatch");
        }
        return true;
    }
    bool verify(bool restore,bool postExit=false){
        scale_trial->nextReadStage=postExit?"post_exit_fa":restore&&scale_trial->noOpRestore?"restore_no_cfg_fa":"pre_exit_cfg_fa";
        return scale_trial_read_all()&&scale_trial_inspect(restore,true);
    }
    bool exit(){
        if(restoring&&scale_trial->noOpRestore&&!scale_trial->inheritedCfgExitRequired)return true;
        return gauge_exit_config()||scale_trial_fail("trial_exit_0091_once_failed");
    }
    bool seal(){
        uint16_t op=0;if(!gauge_word(0x3a,op)||!mosaico_gauge::configExitAccepted(op))return scale_trial_fail("trial_seal_requires_cfg0_init1");
        if(((op>>1)&3)==3){
            // The established 0091 path can already be SEALED, with public DM
            // denied. Preserve the pre-exit authenticated131 proof and require
            // final sealed standard+exact identity; never claim post-exit DM.
            scale_trial->sealedStandardFallback=true;scale_trial->protectionSealed=gauge_wait_security(3);return scale_trial->protectionSealed;
        }
        if(((op>>1)&3)!=1||!verify(restoring,true))return scale_trial_fail("trial_post_exit_accessible_full131_mismatch");
        scale_trial->postExitFull131=true;
        if(scale_trial->sealAttempted)return scale_trial_fail("trial_seal_no_retry");
        scale_trial->sealAttempted=true;const bool sent=gauge_access_word(AccessWord::Seal);const bool observed=gauge_wait_security(3);
        scale_trial->protectionSealed=observed;
        return (sent&&observed)||scale_trial_fail("trial_seal_once_ack_or_security");
    }
    bool final(bool restore){
        if(!scale_trial_physical(restore)||!scale_trial_exact_identity())return false;
        uint16_t op=0,dc=0,fcc=0,rm=0,soc=0;
        const uint16_t expected=restore?65:130;
        if(!gauge_word(0x3a,op)||!gauge_word(0x3c,dc)||!gauge_word(0x12,fcc)||!gauge_word(0x10,rm)||!gauge_word(0x2c,soc))return scale_trial_fail("trial_final_standard_read");
        // 0091 reinitialization may move SOC; that is NOT evidence of a cure.
        const int difference=int(rm)*100-int(soc)*int(fcc);
        return (mosaico_gauge::configExitAccepted(op)&&((op>>1)&3)==3&&dc==expected&&fcc==expected&&rm<=fcc&&soc<=100&&
            difference>=-int(fcc)*10&&difference<=int(fcc)*10)||scale_trial_fail("trial_final_sealed_dc_fcc_rm_soc");
    }
    bool complete(bool restore){
        if(restore&&scale_trial->untouchedMismatch)return scale_trial_fail("owned17_original_untouched_unexpected_kept_restoring");
        return scale_trial_intent(restore?scale_recovery::Phase::Original:scale_recovery::Phase::Applied);
    }
    void publish(bool restore){
        const auto& s=scale_trial->record.snapshot;auto& rt=*scale_runtime;
        rt.originalVerifiedThisBoot=restore;rt.appliedVerifiedThisBoot=!restore;rt.verifiedGeneration=s.generation;rt.verifiedEpoch=s.epoch;
        scale_trial->active=!restore;scale_trial->appliedUs=restore?0:esp_timer_get_time();
        if(restore)scale_reconcile_boot_status_locked();
        scale_update_protection_locked();
        scale_trial->reason=restore?"restored_owned17_stable113_offset_preserved_sealed_durable":"applied_full131_sealed_durable_soc_reinit_not_cure";
    }
    void protect(){
        scale_trial_protection.store(true,std::memory_order_release);
        scale_trial->protectionSealed=false;
        // Never Seal CFG1/CAL1/unknown, and never issue a cleanup 0091 that could
        // commit/reinitialize an unclassified mixed image. Durable intent stays.
        uint16_t op=0;
        if(!gauge_word(0x3a,op)||(op&0x0401)||!(op&0x0020)||((op>>1)&3)==0){
            scale_trial->reason="protection_config_access_unresolved_no_seal_no_exit";return;
        }
        if(((op>>1)&3)==3){scale_trial->protectionSealed=true;return;} // already protected: no repeated Seal
        if(!scale_trial->accessTouched||scale_trial->sealAttempted)return;
        scale_trial->sealAttempted=true;(void)gauge_access_word(AccessWord::Seal);
        scale_trial->protectionSealed=gauge_wait_security(3);
    }
};
bool scale_trial_begin_locked(){
    if(!scale_storage_locked()||!scale_trial_storage_locked())return false;
    if(scale_load_locked(true)!=scale_journal::Status::Ready)return scale_trial_fail("trial_no_trusted_recovery_journal");
    const auto& saved=scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)];
    if(!scale_reviewed_record(saved))return scale_trial_fail("trial_not_reviewed_x2_record");
    scale_trial->preKeySpaceRejected=false;scale_trial->keysStarted=false;
    scale_trial->record=saved;scale_trial->operation=true;scale_mutation_in_progress=true;scale_update_protection_locked();scale_trial->accessTouched=false;
    // Failed default-key ACK/security and third-value observations are latched
    // for this boot. A later manual command must not erase them and re-key.
    scale_trial->sealAttempted=false;scale_trial->possibleDirty=false;
    scale_trial->postExitFull131=false;scale_trial->sealedStandardFallback=false;scale_trial->protectionSealed=false;
    scale_trial->offsetSeen=false;scale_trial->unexpectedCount=0;scale_trial->firstUnexpected=0;
    scale_trial->noOpRestore=false;scale_trial->inheritedCfgExitRequired=false;scale_trial->ownedDirty=false;scale_trial->untouchedMismatch=false;scale_trial->unsafeCalibration=false;
    return true;
}
bool scale_trial_restore_locked(){
    if(!scale_trial_begin_locked())return false;
    scale_trial->active=false;
    std::lock_guard<std::mutex> battery(battery_mutex);ScaleTrialBackend backend;
    const auto result=mosaico_scale_trial::restore(backend);scale_trial->operation=false;scale_mutation_in_progress=false;scale_update_protection_locked();
    return result==mosaico_scale_trial::Result::Restored;
}
bool scale_trial_boot_restore_locked(){
    // Startup/physical fence NEVER sends keys. OTA health must first become
    // VALID; main-loop Poll then owns the single admitted recovery attempt.
    return false;
}

bool scale_trial_report(char* out,size_t size,bool ok){
    int count=-1;if(out&&size)count=std::snprintf(out,size,
        "reason=%s success=%d images_verified=%d trial_active=%d protection=%d host_external_power_attested=%d parameter_scope=reviewed17 full_backup_fields=131 third_value=%d post_exit_full131=%d sealed_standard_fallback=%d sealed_protection_verified=%d unexpected_count=%u first_unexpected=%04x raw=%02x%02x%02x%02x orig=%02x%02x%02x%02x offset_primary=%04x offset_fresh_before=%04x offset_current_after=%04x volatile_drift_vs_primary=%d trial_offset_changed=%d software_restart_not_gauge_ram_loss=1",
        scale_trial?scale_trial->reason:"trial_psram_unavailable",ok,scale_both_images_verified,scale_trial&&scale_trial->active,
        scale_trial_protection.load(std::memory_order_acquire),scale_trial&&scale_trial->hostPowerAttested,scale_trial&&scale_trial->thirdValue,
        scale_trial&&scale_trial->postExitFull131,scale_trial&&scale_trial->sealedStandardFallback,scale_trial&&scale_trial->protectionSealed,
        unsigned(scale_trial?scale_trial->unexpectedCount:0),scale_trial?scale_trial->firstUnexpected:0,
        scale_trial?scale_trial->firstRaw[0]:0,scale_trial?scale_trial->firstRaw[1]:0,scale_trial?scale_trial->firstRaw[2]:0,scale_trial?scale_trial->firstRaw[3]:0,
        scale_trial?scale_trial->firstOriginal[0]:0,scale_trial?scale_trial->firstOriginal[1]:0,scale_trial?scale_trial->firstOriginal[2]:0,scale_trial?scale_trial->firstOriginal[3]:0,
        scale_trial?scale_trial->offsetPrimary:0,scale_trial?scale_trial->offsetBefore:0,scale_trial?scale_trial->offsetAfter:0,
        scale_trial&&scale_trial->offsetSeen&&scale_trial->offsetAfter!=scale_trial->offsetPrimary,
        scale_trial&&scale_trial->offsetSeen&&scale_trial->offsetAfter!=scale_trial->offsetBefore);
    if(count<0||static_cast<size_t>(count)>=size){if(out&&size)std::snprintf(out,size,"error=scale_details_truncated");return false;}
    return ok;
}
} // namespace

bool Hal::gaugeScaleImages(const char* proof,char* details,size_t size){
    if(!scale_details_preflight(details,size))return false;
    ScaleStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    scale_both_images_verified=false;
    if(!lease.held||MosaicoOta::busy()||MosaicoOta::healthPending()||!scale_trial_storage_locked())return scale_trial_report(details,size,false);
    auto& trial=*scale_trial;trial.imagesSet=false;
    if(!proof||std::strlen(proof)>1536){trial.reason="images_proof_length";return scale_trial_report(details,size,false);}
    size_t decoded=0;
    if(mbedtls_base64_decode(reinterpret_cast<uint8_t*>(trial.decodedProof),sizeof(trial.decodedProof)-1,&decoded,
        reinterpret_cast<const uint8_t*>(proof),std::strlen(proof))!=0||!decoded||decoded>=sizeof(trial.decodedProof)){
        trial.reason="images_proof_base64";return scale_trial_report(details,size,false);}
    trial.decodedProof[decoded]=0;
    cJSON* root=cJSON_ParseWithLength(trial.decodedProof,decoded+1);bool ok=root&&cJSON_IsArray(root)&&cJSON_GetArraySize(root)==2;
    for(unsigned i=0;ok&&i<2;++i){
        char* json=cJSON_PrintUnformatted(cJSON_GetArrayItem(root,int(i)));
        ok=json&&MosaicoOta::verifySignedImageDescriptor(json,trial.images[i]);if(json)cJSON_free(json);
        if(ok)ok=scale_trial_verify_image(i,trial.images[i]);
    }
    if(root)cJSON_Delete(root);
    if(ok&&(!std::strcmp(trial.images[0].version,trial.images[1].version)))ok=scale_trial_fail("images_two_distinct_closed_versions_required");
    trial.imagesSet=ok;scale_both_images_verified=ok;
    trial.reason=ok?"two_signed_audited_bins_actual_valid_slots_verified":"images_proof_or_actual_slot_rejected";
    return scale_trial_report(details,size,ok);
}
bool Hal::gaugeScaleApply(char* details,size_t size){
    if(!scale_details_preflight(details,size))return false;
    // Root's ONLY parser route requires CONFIRM_EXTERNAL_POWER after actual V1
    // 5V/500mA host checks and single-source topology. This API does not infer V1.
    ScaleStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    if(!lease.held||!scale_trial_begin_locked())return scale_trial_report(details,size,false);
    scale_trial->hostPowerAttested=true; // root's confirmed local-console call, never an inferred V1 read
    if(scale_trial->record.snapshot.phase!=scale_recovery::Phase::Original){scale_trial->reason="apply_requires_original_phase";scale_trial->operation=false;scale_mutation_in_progress=false;scale_update_protection_locked();return scale_trial_report(details,size,false);}
    std::lock_guard<std::mutex> battery(battery_mutex);ScaleTrialBackend backend;
    const auto result=mosaico_scale_trial::apply(backend);scale_trial->operation=false;scale_mutation_in_progress=false;scale_update_protection_locked();
    return scale_trial_report(details,size,result==mosaico_scale_trial::Result::Applied);
}
bool Hal::gaugeScaleRestore(char* details,size_t size){
    if(!scale_details_preflight(details,size))return false;
    ScaleStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    if(!lease.held||!scale_trial_storage_locked())return scale_trial_report(details,size,false);
    if(scale_load_locked(true)!=scale_journal::Status::Ready)return scale_trial_report(details,size,false);
    const auto& saved=scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)];
    if(saved.snapshot.phase==scale_recovery::Phase::Original){
        scale_trial->reason=scale_runtime->originalVerifiedThisBoot?"already_original_verified_no_cfg_or_keys":"original_requires_explicit_prepare_not_restore_reinit";
        return scale_trial_report(details,size,scale_runtime->originalVerifiedThisBoot);
    }
    return scale_trial_report(details,size,scale_trial_restore_locked());
}
bool Hal::gaugeScaleObservedField(size_t index,char* out,size_t size){
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    if(!out||!size)return false;
    if(!scale_trial||index>=131||index>=scale_trial->observed.count){
        std::snprintf(out,size,"cached=0 reason=index_not_in_current_observation");return false;
    }
    const auto& f=scale_trial->record.snapshot.fields[index];uint32_t raw=0,original=0,target=0;
    for(size_t j=0;j<f.width;++j){raw=(raw<<8)|scale_trial->observed.raw[index][j];original=(original<<8)|f.original[j];target=(target<<8)|f.target[j];}
    const int count=std::snprintf(out,size,
        "cached=1 index=%u count=%u full131=%d address=%04x width=%u rawBE=%0*lx expectedOriginalBE=%0*lx targetBE=%0*lx observed_generation=%lu observed_epoch=%llu observed_phase=%s read_ms=%lld source_stage=%s role=%s",
        unsigned(index),unsigned(scale_trial->observed.count),scale_trial->observed.count==131&&scale_trial->observed.authenticated,f.address,f.width,f.width*2,static_cast<unsigned long>(raw),f.width*2,static_cast<unsigned long>(original),f.width*2,static_cast<unsigned long>(target),
        static_cast<unsigned long>(scale_trial->observedGeneration),static_cast<unsigned long long>(scale_trial->observedEpoch),scale_phase_name(scale_trial->observedPhase),
        static_cast<long long>(scale_trial->observedReadMs),scale_trial->observedStage,
        f.address==0x9180?"autonomous9180_neverwrite":scale_trial_owned(f.address)?"owned17":scale_trial_safety_field(f.address)?"untouched_safety":"untouched_stable");
    if(count<0||static_cast<size_t>(count)>=size){std::snprintf(out,size,"error=scale_observed_details_truncated");return false;}return true;
}
bool Hal::gaugeScaleTrialActive() const {return scale_trial_protection.load(std::memory_order_acquire);}
void Hal::gaugeScalePoll(){
    std::unique_lock<std::recursive_mutex> transaction(gauge_transaction_mutex,std::try_to_lock);
    if(!transaction.owns_lock()||!scale_runtime||scale_load_locked()!=scale_journal::Status::Ready||
       MosaicoOta::busy()||MosaicoOta::healthPending())return;
    const auto& current=scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)].snapshot;
    if(current.phase==scale_recovery::Phase::Original)return;
    if(!scale_trial_storage_locked()||scale_trial->operation)return;
    const int64_t now=esp_timer_get_time();
    if(now>=0&&now<scale_trial->nextPollUs)return;
    scale_trial->nextPollUs=now+1000000; // bounded read-only defer checks, no key retry loop
    if(!scale_trial->active){
        if(scale_runtime->appliedVerifiedThisBoot||scale_trial->bootRestoreAttempted||scale_trial->restoreAttempted)return;
        ScaleStorageLease lease;if(!lease.held)return;
        (void)scale_trial_restore_locked(); // pre-key admission failure consumes no key budget
        return;
    }
    bool due=false;
    {std::unique_lock<std::mutex> battery(battery_mutex,std::try_to_lock);if(!battery.owns_lock())return;
        uint16_t mv=0,temp=0,op=0,dc=0,fcc=0;
        const bool read=gauge_word(0x08,mv)&&gauge_word(0x06,temp)&&gauge_word(0x3a,op)&&gauge_word(0x3c,dc)&&gauge_word(0x12,fcc);
        due=!read||mv<=3600||mv>4350||temp<2832||temp>3181||!mosaico_gauge::configExitAccepted(op)||((op>>1)&3)!=3||dc!=130||fcc!=130||
            now<scale_trial->appliedUs||now-scale_trial->appliedUs>=300000000;
    }
    if(!due)return;
    ScaleStorageLease lease;if(!lease.held)return;
    scale_trial->active=false;
    (void)scale_trial_restore_locked();
}

#endif // !MOSAICO_SCALE_TRIAL_MODEL_ONLY
