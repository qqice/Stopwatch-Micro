// Independent GCT1 owned3 contrast runtime. UNKNOWN units in every phase.
// Expanded PSRAM snapshots are read/compare caches, never GSJ3 persistence.
#include "mosaico_gauge_contrast_journal.h"
#include "mosaico_gauge_calibration_read_model.h"
#include "mosaico_factory_scale_observed_page.h"
#include <ota/ota_public_key.h>
#include <mbedtls/pk.h>
#include <new>
#include "nvs.h"
#include <algorithm>
namespace {

namespace contrast_runtime_store {
namespace durable=mosaico_gauge_contrast_journal;
using Status=durable::Status;
struct Record {std::array<uint8_t,6> mac{};mosaico_gauge_contrast::Arm arm=mosaico_gauge_contrast::Arm::C;scale_recovery::Snapshot snapshot{};};
struct Loaded {int selected=-1;std::array<Record,2> records{};};
struct StorageWorkspace {durable::Workspace durable{};Loaded loaded{};mosaico_gauge_contrast::Record packed{};};
void pack(const Record& in,mosaico_gauge_contrast::Record& out){
 out={};out.mac=in.mac;out.arm=in.arm;out.identity=in.snapshot.identity;out.generation=in.snapshot.generation;out.epoch=in.snapshot.epoch;out.phase=in.snapshot.phase;
 for(size_t i=0;i<131;++i)for(unsigned j=0;j<scale_recovery::layout[i].width;++j)out.source[mosaico_gauge_contrast::rawOffset(i)+j]=in.snapshot.fields[i].original[j];
}
void expand(const mosaico_gauge_contrast::Record& in,Record& out){
 out.~Record();new(&out) Record{};out.mac=in.mac;out.arm=in.arm;auto& s=out.snapshot;s.identity=in.identity;s.binding=scale_journal::binding(in.mac);s.layoutId=scale_recovery::Layout::Public131V1;s.scale=scale_recovery::Scale::One;s.count=131;s.generation=in.generation;s.epoch=in.epoch;s.phase=in.phase;
 const auto target=mosaico_gauge_contrast::targetRaw(in);
 for(size_t i=0;i<131;++i){auto& f=s.fields[i];f.address=scale_recovery::layout[i].address;f.width=scale_recovery::layout[i].width;for(unsigned j=0;j<f.width;++j){f.original[j]=in.source[mosaico_gauge_contrast::rawOffset(i)+j];f.target[j]=target[mosaico_gauge_contrast::rawOffset(i)+j];}}
}
bool shape(const Record& r){
 mosaico_gauge_contrast::Record p{};pack(r,p);if(!mosaico_gauge_contrast::shape(p)||r.snapshot.count!=131)return false;
 const auto target=mosaico_gauge_contrast::targetRaw(p);
 for(size_t i=0;i<131;++i){const auto& f=r.snapshot.fields[i];if(f.address!=scale_recovery::layout[i].address||f.width!=scale_recovery::layout[i].width)return false;
  for(unsigned j=0;j<4;++j)if(f.target[j]!=(j<f.width?target[mosaico_gauge_contrast::rawOffset(i)+j]:0)|| (j>=f.width&&f.original[j]))return false;}
 return true;
}
bool sameRecoveryImage(const Record& a,const Record& b){
 if(a.mac!=b.mac||a.arm!=b.arm||!shape(a)||!shape(b))return false;
 for(size_t i=0;i<131;++i)for(unsigned j=0;j<4;++j)if(a.snapshot.fields[i].original[j]!=b.snapshot.fields[i].original[j]||a.snapshot.fields[i].target[j]!=b.snapshot.fields[i].target[j])return false;
 return true;
}
Status sync(Status status,StorageWorkspace& w){w.loaded.selected=-1;if(status==Status::Ready){for(unsigned i=0;i<2;++i)expand(w.durable.loaded.records[i],w.loaded.records[i]);w.loaded.selected=w.durable.loaded.selected;}return status;}
Status loadNvs(const std::array<uint8_t,6>& mac,StorageWorkspace& w){return sync(durable::loadNvs(mac,w.durable),w);}
Status saveNvs(const Record& r,const durable::Admission& a,StorageWorkspace& w){pack(r,w.packed);const auto status=durable::saveNvs(w.packed,a,w.durable);return sync(status==Status::Ready?durable::loadNvs(r.mac,w.durable):status,w);}
}

namespace contrast_journal=contrast_runtime_store;
namespace contrast_model=mosaico_gauge_contrast;

// Capability contract UTF-8, no terminating newline:
// GCT1|Factory3000Contrast|source=70a767be|C=91336fc0|B=675dd812|owned3|C112a_DCFCC65|B102a_DCFCC65|fullsource131|never_publish_physical_units|boot_restore_after_SDK_VALID|deadline_seconds=720|host_deadline_seconds=660|no_Failed_waiver
constexpr const char* contrast_contract_digest="da8c79bcf2a17f01a3294394d7ca9b527d1a623a45fa1bc04c730a988cd69bdb";
struct ContrastRuntime {
 contrast_journal::StorageWorkspace journal{};
 contrast_journal::Record record{}; // MUST NOT alias saveNvs scratch/loaded.
 scale_recovery::Observation observed{};
 MosaicoOta::VerifiedImageDescriptor images[2]{};
 esp_app_desc_t descriptions[2]{};
 uint8_t imageElf[2][32]{},buffer[256]{};
 char decodedProof[1537]{},details[1536]{};
 bool loaded=false,everSeen=false,preparedThisBoot=false,appliedVerifiedThisBoot=false;
 uint32_t verifiedGeneration=0;uint64_t verifiedEpoch=0;
 int64_t preparedUs=0,revalidatedUs=0,restoreCompletedUs=0;
 bool imagesSet=false,bootRestoreAttempted=false,active=false,hostPowerAttested=false;
 bool operation=false,accessTouched=false,keyFailed=false,sealAttempted=false;
 uint8_t triedArms=0;
 bool applyAttempted=false,restoreAttempted=false,postExitFull131=false,sealedStandardFallback=false,protectionSealed=false;
 bool preKeySpaceRejected=false,keysStarted=false,thirdValue=false,possibleDirty=false,ownedDirty=false;
 bool noOpRestore=false,inheritedCfgExitRequired=false,untouchedMismatch=false,unsafeCalibration=false,invalidOffset=false;
 size_t budgetBase=56,budgetReuse=0,budgetRequired=56; // Diagnostic only, never a grant.
 size_t unexpectedCount=0;uint16_t firstUnexpected=0;uint8_t firstRaw[4]{},firstOriginal[4]{};
 uint32_t observedGeneration=0;uint64_t observedEpoch=0;
 scale_recovery::Phase observedPhase=scale_recovery::Phase::Original;
 int64_t observedReadMs=0,appliedUs=0,nextPollUs=0,lastPollUs=0;
 const char* observedStage="none";const char* nextReadStage="unknown";const char* reason="not_prepared";
};
ContrastRuntime* contrast_rt=nullptr;
std::atomic<bool> contrast_protection{true};
bool contrast_safety_fault=false;
contrast_journal::Status contrast_status=contrast_journal::Status::IoError;
bool contrast_storage_locked(){
 if(contrast_rt)return true;
 void* p=heap_caps_malloc(sizeof(ContrastRuntime),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
 if(!p){contrast_protection.store(true,std::memory_order_release);return false;}
 contrast_rt=new(p)ContrastRuntime{};return true;
}
bool contrast_rt_fail(const char* why){if(contrast_rt)contrast_rt->reason=why;return false;}
void contrast_update_protection_locked(){
 bool stable=false;
 if(contrast_rt&&contrast_status==contrast_journal::Status::Ready){const auto& l=contrast_rt->journal.loaded;
  stable=l.selected>=0&&l.records[size_t(l.selected)].snapshot.phase==scale_recovery::Phase::Original;}
 contrast_protection.store(contrast_safety_fault||(contrast_rt&&(contrast_rt->operation||contrast_rt->thirdValue||contrast_rt->unsafeCalibration||contrast_rt->untouchedMismatch||contrast_rt->possibleDirty))||!(stable||contrast_status==contrast_journal::Status::Missing),std::memory_order_release);
}
void contrast_invalidate_locked(){if(contrast_rt){contrast_rt->appliedVerifiedThisBoot=false;contrast_rt->preparedThisBoot=false;}}
contrast_journal::Status contrast_load_locked(bool refresh=false){
 if(!contrast_storage_locked())return contrast_journal::Status::IoError;
 auto& t=*contrast_rt;
 if(!t.loaded||refresh){
  contrast_status=contrast_journal::loadNvs(GetHAL().getFactoryMac(),t.journal);t.loaded=true;
  if(contrast_status==contrast_journal::Status::Missing&&t.everSeen)contrast_status=contrast_journal::Status::Corrupt;
  if(contrast_status!=contrast_journal::Status::Missing)t.everSeen=true;
  if(contrast_status==contrast_journal::Status::Ready){const auto& l=t.journal.loaded;
   if(l.selected<0||!contrast_journal::shape(l.records[size_t(l.selected)]))contrast_status=contrast_journal::Status::Rejected;
   else{const auto& s=l.records[size_t(l.selected)].snapshot;
    if(s.generation!=t.verifiedGeneration||s.epoch!=t.verifiedEpoch)contrast_invalidate_locked();}
  }else contrast_invalidate_locked();
 }
 contrast_update_protection_locked();return contrast_status;
}
// RO ownership hooks. No old scale/native functions, keys or control commands.
bool contrast_present_locked(){return contrast_load_locked()!=contrast_journal::Status::Missing;}
bool contrast_boot_fence_locked(){return contrast_load_locked()==contrast_journal::Status::Missing;}
// No I2C/locks: readers must revoke units even if automatic poll is deferred.
bool contrast_time_valid(const ContrastRuntime& t,int64_t now){
 return t.appliedUs>0&&now>=t.appliedUs&&now>=t.lastPollUs&&now-t.appliedUs<720000000;
}
mosaico_gauge_physical::UnitContext contrast_context_locked(){
 mosaico_gauge_physical::UnitContext c{};
 if(contrast_load_locked()!=contrast_journal::Status::Ready)return c;
 const auto& t=*contrast_rt;const auto& s=t.journal.loaded.records[size_t(t.journal.loaded.selected)].snapshot;
 c.generation=s.generation;c.unitEpoch=s.epoch;
 if(s.phase==scale_recovery::Phase::Original)c.phase=mosaico_gauge_physical::Phase::Original; // factor0 UNTRUSTED
 else if(s.phase==scale_recovery::Phase::Pending)c.phase=mosaico_gauge_physical::Phase::Pending;
 else if(s.phase==scale_recovery::Phase::Restoring)c.phase=mosaico_gauge_physical::Phase::Restoring;
 else{c.phase=mosaico_gauge_physical::Phase::Applied;
  // Experimental nominal65 never grants physical units or a consumer waiver.
 }
 return c;
}
bool contrast_audit_allowed_locked(){
 const auto status=contrast_load_locked();
 if(!contrast_rt||contrast_rt->operation||contrast_rt->active)return false;
 return status==contrast_journal::Status::Missing||(status==contrast_journal::Status::Ready&&contrast_rt->journal.loaded.selected>=0&&contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)].snapshot.phase==scale_recovery::Phase::Original);
}
bool contrast_maintenance_original_cached_locked(){
 if(!contrast_rt||!contrast_rt->loaded||contrast_status!=contrast_journal::Status::Ready||(contrast_rt->journal.loaded.selected<0||contrast_rt->journal.loaded.selected>1))return false;
 const auto& t=*contrast_rt;return t.journal.loaded.records[size_t(t.journal.loaded.selected)].snapshot.phase==scale_recovery::Phase::Original&&!t.operation&&!t.active&&t.appliedUs==0&&!t.possibleDirty&&!t.ownedDirty&&!t.keyFailed&&!t.thirdValue&&!t.unsafeCalibration&&!t.untouchedMismatch&&!contrast_safety_fault&&!contrast_protection.load(std::memory_order_acquire);
}
bool contrast_maintenance_cached_safe_locked(){
 if(!contrast_rt||!contrast_rt->loaded||contrast_safety_fault)return false;
 const auto& t=*contrast_rt;
 return (contrast_status==contrast_journal::Status::Missing&&!t.everSeen&&!t.operation&&!t.active&&t.appliedUs==0&&!t.possibleDirty&&!t.ownedDirty&&!t.keyFailed&&!t.thirdValue&&!t.unsafeCalibration&&!t.untouchedMismatch&&!contrast_safety_fault&&!contrast_protection.load(std::memory_order_acquire))||contrast_maintenance_original_cached_locked();
}
bool contrast_protected(){return contrast_protection.load(std::memory_order_acquire);}
bool contrast_legacy_original_locked(){
 const auto status=factory_scale_load_locked(true);
 if(status==factory_scale_journal::Status::Missing)return true;
 return status==factory_scale_journal::Status::Ready&&factory_scale_rt&&factory_scale_rt->journal.loaded.selected>=0&&factory_scale_rt->journal.loaded.records[size_t(factory_scale_rt->journal.loaded.selected)].snapshot.phase==scale_recovery::Phase::Original&&!factory_scale_rt->operation&&!factory_scale_rt->active;
}
bool contrast_space_locked(){
 nvs_stats_t stats{};if(nvs_get_stats(nullptr,&stats)!=ESP_OK)return false;
 // 56 initial = 3*13-entry blobs + namespace1 + margin16. Once Original
 // exists only replacement13+margin16 is required; never claim reuse credit.
 return contrast_status==contrast_journal::Status::Missing?stats.available_entries>=56:
  contrast_status==contrast_journal::Status::Ready&&stats.available_entries>=29;
}
// Reuses only this runtime's completed restore proof. This is cached pre-exit
// authenticated source131 plus later sealed-standard proof, NOT fresh sealed DM.
bool contrast_self_restored_source_locked(int64_t now){
 if(!contrast_rt||contrast_status!=contrast_journal::Status::Ready)return false;
 const auto& t=*contrast_rt;
 if(t.active||t.operation||t.restoreCompletedUs<=0||now<t.restoreCompletedUs||now-t.restoreCompletedUs>120000000||
    !t.protectionSealed||t.thirdValue||t.unsafeCalibration||t.untouchedMismatch||t.unexpectedCount||t.possibleDirty||contrast_safety_fault||
    !t.observed.authenticated||t.observed.count!=131||t.journal.loaded.selected<0||
    t.journal.loaded.records[size_t(t.journal.loaded.selected)].snapshot.phase!=scale_recovery::Phase::Original)return false;
 for(size_t i=0;i<131;++i)for(unsigned j=0;j<scale_recovery::layout[i].width;++j)
  if(t.observed.raw[i][j]!=contrast_model::sourceFixture[contrast_model::rawOffset(i)+j])return false;
 return true;
}
bool contrast_prepared_fresh(){
 if(!contrast_rt||!contrast_rt->preparedThisBoot)return false;
 const auto now=esp_timer_get_time();return now>=contrast_rt->preparedUs&&now-contrast_rt->preparedUs<=120000000;
}
bool contrast_safety_address(uint16_t a){return (a>=0x91b5&&a<=0x91db)||a==0x91b4||a==0x91dd||a==0x91df||a==0x9206||a==0x9180||a==0x9184||a==0x9188;}
// Recovery is allowed after UI unlock, but never before actual SDK VALID or
// while OTA, radios or power phase are unsafe. Forward admission stays strict.
bool contrast_recovery_runtime(){
 const auto p=GetNetworkQuota().powerStats();const auto b=GetCodexMicroBle().diagnostics();
 const auto* part=esp_ota_get_running_partition();esp_ota_img_states_t state{};
 return part&&esp_ota_get_state_partition(part,&state)==ESP_OK&&state==ESP_OTA_IMG_VALID&&
 !MosaicoOta::busy()&&!MosaicoOta::healthPending()&&p.phase==2&&!p.wifiRunning&&!b.advertising&&!b.connected;
}
bool contrast_rt_physical(bool restore){
 if(!restore&&contrast_rt&&contrast_rt->applyAttempted&&contrast_rt->appliedUs>0&&!contrast_time_valid(*contrast_rt,esp_timer_get_time()))return contrast_rt_fail("apply_total_deadline_expired");
 if(!gauge||!(restore?contrast_recovery_runtime():factory_runtime()))return contrast_rt_fail("actual_SDK_VALID_quiet_radios_phase2_required");
 uint16_t op=0,mv=0,temp=0,current=0,average=0;
 if(!gauge_word(0x3a,op)||!gauge_word(8,mv)||!gauge_word(6,temp)||!gauge_word(0x0c,current)||!gauge_word(0x14,average))return contrast_rt_fail("fresh_safety_read");
 const int bound=restore?260:30;
 return (!(op&1)&&(op&0x20)&&mv>=(restore?3500:4000)&&mv<=4350&&temp>=2832&&temp<=3131&&
  int16_t(current)>=-bound&&int16_t(current)<=bound&&int16_t(average)>=-bound&&int16_t(average)<=bound)||contrast_rt_fail("fresh_safety_envelope");
}
bool contrast_rt_exact_identity(){return factory_identity()||contrast_rt_fail("exact_gauge_identity");}
bool contrast_rt_known_access(){
 mosaico_gauge::AccessJournal a{};const auto mac=GetHAL().getFactoryMac();
 return (gauge_load_access(a,mac.data())==ESP_OK&&mosaico_gauge::validAccessJournal(a,mac.data())&&a.priorSecurity==3&&a.unsealVerified&&a.fullVerified&&
 !mosaico_gauge::failedDefaultAttempt(a)&&(a.state==uint8_t(mosaico_gauge::AccessState::Opened)||a.state==uint8_t(mosaico_gauge::AccessState::Restored)))||contrast_rt_fail("known_default_prior3_required");
}
bool contrast_owned_intent(){
 if(contrast_load_locked()!=contrast_journal::Status::Ready)return false;
 const auto p=contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)].snapshot.phase;
 return p==scale_recovery::Phase::Pending||p==scale_recovery::Phase::Restoring;
}
const esp_partition_t* contrast_rt_partition(unsigned slot){return slot<2?esp_partition_find_first(ESP_PARTITION_TYPE_APP,slot?ESP_PARTITION_SUBTYPE_APP_OTA_1:ESP_PARTITION_SUBTYPE_APP_OTA_0,nullptr):nullptr;}
bool contrast_rt_version(const char* v){return !std::strcmp(v,"0.13.95-r13-mosaico-ota")||!std::strcmp(v,"0.13.95-r14-mosaico-ota");}
bool contrast_rt_verify_image(unsigned slot,const MosaicoOta::VerifiedImageDescriptor& image){
    const auto* part=contrast_rt_partition(slot);esp_ota_img_states_t state{};
    if(!part||!image.size||image.size>part->size||!contrast_rt_version(image.version)||
       esp_ota_get_state_partition(part,&state)!=ESP_OK||state!=ESP_OTA_IMG_VALID||
       esp_ota_get_partition_description(part,&contrast_rt->descriptions[slot])!=ESP_OK)return contrast_rt_fail("images_partition_valid_description");
    const auto& desc=contrast_rt->descriptions[slot];
    if(std::strncmp(desc.version,image.version,sizeof(desc.version))||
       std::strncmp(desc.project_name,"Stopwatch-Mosaico",sizeof(desc.project_name)))return contrast_rt_fail("images_exact_project_version");
    bool elfNonzero=false;for(unsigned i=0;i<32;++i){contrast_rt->imageElf[slot][i]=desc.app_elf_sha256[i];elfNonzero|=desc.app_elf_sha256[i]!=0;}
    if(!elfNonzero)return contrast_rt_fail("images_missing_elf_identity");
    if(psa_crypto_init()!=PSA_SUCCESS)return contrast_rt_fail("images_psa_crypto_init");
    psa_hash_operation_t operation=PSA_HASH_OPERATION_INIT;
    bool ok=psa_hash_setup(&operation,PSA_ALG_SHA_256)==PSA_SUCCESS;
    for(size_t offset=0;ok&&offset<image.size;){
        const size_t count=std::min<size_t>(sizeof(contrast_rt->buffer),image.size-offset);gauge_feed();
        ok=esp_partition_read(part,offset,contrast_rt->buffer,count)==ESP_OK&&
           psa_hash_update(&operation,contrast_rt->buffer,count)==PSA_SUCCESS;offset+=count;
    }
    uint8_t digest[32]{};size_t digestLength=0;
    if(ok)ok=psa_hash_finish(&operation,digest,sizeof(digest),&digestLength)==PSA_SUCCESS&&digestLength==32;
    (void)psa_hash_abort(&operation);
    return (ok&&!std::memcmp(digest,image.hash,32))||contrast_rt_fail("images_full_signed_bin_sha256");
}
bool contrast_rt_images_current(){
    if(!contrast_rt||!contrast_rt->imagesSet)return false;
    // Recheck under the operation's OTA storage lease before first mutation.
    for(unsigned i=0;i<2;++i)if(!contrast_rt_verify_image(i,contrast_rt->images[i])){contrast_rt->imagesSet=false;return false;}
    return true;
}
bool contrast_rt_read_field(size_t index,uint8_t (&raw)[4]){
    if(index>=131)return contrast_rt_fail("trial_read_index");
    const auto& f=contrast_rt->record.snapshot.fields[index];
    if(f.address!=scale_recovery::layout[index].address||f.width!=scale_recovery::layout[index].width)return contrast_rt_fail("trial_read_layout");
    uint8_t previous[4]{};
    for(unsigned pass=0;pass<2;++pass){
        uint8_t block[36]{};bool read=false;
        for(const auto& field:mosaico_threshold::fields)if(uint16_t(field.selector)==f.address)read=gauge_threshold_read(field.selector,block);
        for(const auto& field:mosaico_calibration::fields)if(uint16_t(field.selector)==f.address)read=gauge_calibration_read(field.selector,block);
        for(const auto& field:mosaico_public_extra::fields)if(uint16_t(field.selector)==f.address)read=gauge_public_extra_read(field.selector,block);
        if(!read||block[0]!=uint8_t(f.address)||block[1]!=uint8_t(f.address>>8)||block[35]!=36||
           block[34]!=mosaico_gauge::macChecksum(block,34))return contrast_rt_fail("trial_authenticated_field_read");
        for(size_t j=0;j<f.width;++j){if(pass&&previous[j]!=block[2+j])return contrast_rt_fail("trial_unstable_field");previous[j]=block[2+j];}
    }
    for(size_t j=0;j<4;++j)raw[j]=j<f.width?previous[j]:0;
    return true;
}
bool contrast_rt_owned(uint16_t address){return contrast_model::owned(address);}
bool contrast_rt_safety_field(uint16_t address){
    for(const auto& field:mosaico_calibration::fields)
        if(uint16_t(field.selector)==address)return true;
    return contrast_safety_address(address);
}
bool contrast_rt_read_all(){
    const auto& snapshot=contrast_rt->record.snapshot;
    contrast_rt->observedGeneration=snapshot.generation;contrast_rt->observedEpoch=snapshot.epoch;contrast_rt->observedPhase=snapshot.phase;
    contrast_rt->observedReadMs=esp_timer_get_time()/1000;contrast_rt->observedStage=contrast_rt->nextReadStage;
    auto& o=contrast_rt->observed;o.count=0;o.authenticated=false;o.sealed=false;o.cfg0=false;
    for(size_t i=0;i<131;++i){if(!contrast_rt_read_field(i,o.raw[i]))return false;++o.count;gauge_feed();}
    o.authenticated=true;return true;
}
// Preserve complete current authenticated131 in PSRAM, including unexpected
// untouched values. Owned17 third values always prohibit overwrite. Untouched
// unknown values never become writes; safety-calibration changes prohibit ALL
// writes. Only 9180 has an autonomous role; 9182 is NOT generalized into it.
bool contrast_rt_inspect(bool restore,bool requireDesired){
    auto& trial=*contrast_rt;trial.unexpectedCount=0;trial.firstUnexpected=0;
    trial.ownedDirty=false;trial.untouchedMismatch=false;trial.unsafeCalibration=false;trial.invalidOffset=false;
    bool desiredOwned=true,applyExpected=true;
    for(size_t i=0;i<131;++i){const auto& f=trial.record.snapshot.fields[i];const auto* raw=trial.observed.raw[i];
        bool original=true,target=true;
        for(size_t j=0;j<f.width;++j){original&=raw[j]==f.original[j];target&=raw[j]==f.target[j];}
        const bool owned=contrast_rt_owned(f.address);
        if(owned){trial.ownedDirty|=!original;desiredOwned&=restore?original:target;}
        const bool unexpected=owned?(!original&&!target):!original;
        if(unexpected){
            if(!trial.unexpectedCount){trial.firstUnexpected=f.address;
                for(size_t j=0;j<4;++j){trial.firstRaw[j]=raw[j];trial.firstOriginal[j]=f.original[j];}}
            ++trial.unexpectedCount;
            if(owned)trial.thirdValue=true;
            else{trial.untouchedMismatch=true;if(contrast_rt_safety_field(f.address)){trial.unsafeCalibration=true;contrast_safety_fault=true;}}
        }
        if(!restore)applyExpected&=requireDesired?(owned?target:original):original;
    }
    if(!restore&&(trial.invalidOffset||!applyExpected))return contrast_rt_fail("apply_source_or_offset_i2_range_mismatch");
    if(trial.thirdValue)return contrast_rt_fail("trial_unexpected_owned_third_value_no_overwrite");
    if(trial.unsafeCalibration)return contrast_rt_fail("restore_unsafe_calibration_changed_no_writes");
    if(requireDesired&&!desiredOwned)return contrast_rt_fail("trial_owned3_final_mismatch");
    return true;
}

bool contrast_rt_intent(scale_recovery::Phase phase){
 if(contrast_load_locked(true)!=contrast_journal::Status::Ready)return contrast_rt_fail("intent_no_trusted_GCT1");
 auto& t=*contrast_rt;const auto& current=t.journal.loaded.records[size_t(t.journal.loaded.selected)];
 if(!contrast_journal::sameRecoveryImage(t.record,current))return contrast_rt_fail("intent_recovery_image_changed");
 if(phase==scale_recovery::Phase::Restoring&&current.snapshot.phase==phase){t.record=current;return true;}
 t.record=current;
 if(t.record.snapshot.generation==UINT32_MAX||t.record.snapshot.epoch==UINT64_MAX)return contrast_rt_fail("intent_counter_exhausted");
 ++t.record.snapshot.generation;++t.record.snapshot.epoch;t.record.snapshot.phase=phase;
 contrast_protection.store(true,std::memory_order_release);
 if(!contrast_rt_physical(phase==scale_recovery::Phase::Restoring||phase==scale_recovery::Phase::Original))return false;
 const auto status=contrast_journal::saveNvs(t.record,{true,true,true},t.journal);
 contrast_status=status;t.loaded=true;t.everSeen=true;
 if(status!=contrast_journal::Status::Ready||t.journal.loaded.selected<0)return contrast_rt_fail("intent_commit_readback_failed");
 const auto& saved=t.journal.loaded.records[size_t(t.journal.loaded.selected)];
 return (saved.snapshot.generation==t.record.snapshot.generation&&saved.snapshot.epoch==t.record.snapshot.epoch&&saved.snapshot.phase==phase&&contrast_journal::sameRecoveryImage(saved,t.record))||contrast_rt_fail("intent_readback_mismatch");
}
struct ContrastBackend {
    
    bool restoring=false;
    void revoke(){contrast_invalidate_locked();}
    bool admit(bool restore){
        restoring=restore;
        if(contrast_safety_fault)return contrast_rt_fail("trial_known_safety_calibration_fault_latched");
        if(scale_load_locked(true)!=scale_journal::Status::Missing||!contrast_legacy_original_locked())return contrast_rt_fail("contrast_requires_GSJ2_missing_GSJ3_missing_or_Original");
        if(!contrast_rt_physical(restore)||!contrast_rt_known_access())return false;
        if(restore){
            if(contrast_rt->preKeySpaceRejected&&!contrast_rt->keysStarted)return contrast_rt_fail("apply_prekey_space_rejected_no_automatic_keys");
            if(contrast_rt->restoreAttempted)return contrast_rt_fail("restore_once_per_boot_no_key_retry");
            return !contrast_rt->thirdValue||contrast_rt_fail("restore_third_value_no_overwrite");
        }
        if(!contrast_space_locked())return contrast_rt_fail("apply_nvs_available_entries_headroom");
        const uint8_t armBit=contrast_rt->record.arm==contrast_model::Arm::C?1:2;
        if(contrast_rt->triedArms&armBit)return contrast_rt_fail("apply_once_per_arm_per_boot_no_renewal");
        if(!contrast_prepared_fresh()||!contrast_rt_images_current())return contrast_rt_fail("apply_explicit_prepare_and_two_audited_images_required");
        // Method entry is only wired by root to local UART CONFIRM_EXTERNAL_POWER.
        // This is HOST-ATTESTED stable unique V1 5 V, not measured device evidence.
        uint16_t current=0,average=0;
        if(!(gauge_word(0x0c,current)&&gauge_word(0x14,average)&&int16_t(current)>=-30&&int16_t(current)<=30&&
            int16_t(average)>=-30&&int16_t(average)<=30))return contrast_rt_fail("apply_quiet_original_current");
        contrast_rt->restoreCompletedUs=0;contrast_rt->applyAttempted=true;contrast_rt->triedArms|=armBit;contrast_rt->appliedUs=esp_timer_get_time();return true;
    }
    bool intent(bool restore){return contrast_rt_intent(restore?scale_recovery::Phase::Restoring:scale_recovery::Phase::Pending);}
    bool open(bool restore){
        uint16_t op=0;if(!contrast_rt_physical(restore)||!gauge_word(0x3a,op)||!contrast_rt_exact_identity())return contrast_rt_fail("trial_open_identity_status");
        if(((op>>1)&3)==1){
            if(!contrast_owned_intent()||(op&1)||!(op&0x20))return contrast_rt_fail("restore_retained_fa_requires_owned_intent_cal0_init1");
            if(!restore)return contrast_rt_fail("apply_requires_sealed_open3_path");
            if(contrast_rt->restoreAttempted)return contrast_rt_fail("restore_open_once_per_boot");
            contrast_rt->restoreAttempted=true;contrast_rt->bootRestoreAttempted=true;
            contrast_rt->accessTouched=true;return true; // journal-owned retained FA during one rollback
        }
        if(contrast_rt->keyFailed)return contrast_rt_fail("trial_key_attempt_failed_no_retry");
        if(((op>>1)&3)!=3||(op&0x0401))return contrast_rt_fail("trial_open_requires_sealed_normal");
        contrast_rt->accessTouched=true;contrast_rt->sealAttempted=false;
        gauge_delay_ms(5000);
        if(!contrast_rt_physical(restore)||!gauge_word(0x3a,op)||((op>>1)&3)!=3||(op&0x0401))return false;
        nvs_stats_t preKeyStats{};
        if(!restore&&(nvs_get_stats(nullptr,&preKeyStats)!=ESP_OK||preKeyStats.available_entries<29)){contrast_rt->preKeySpaceRejected=true;return contrast_rt_fail("apply_nvs_space_before_first_keys");}
        if(restore){contrast_rt->restoreAttempted=true;contrast_rt->bootRestoreAttempted=true;}
        contrast_rt->keysStarted=true;
        const bool unseal=gauge_access_word(AccessWord::UnsealFirst)&&gauge_access_word(AccessWord::UnsealSecond);
        const bool unsealed=gauge_wait_security(2);
        if(!unseal||!unsealed){contrast_rt->keyFailed=true;return contrast_rt_fail("trial_unseal_once_ack_or_security");}
        gauge_delay_ms(5000);
        if(!contrast_rt_physical(restore))return false;
        const bool full=gauge_access_word(AccessWord::Full)&&gauge_access_word(AccessWord::Full);
        const bool fullAccess=gauge_wait_security(1);
        if(!full||!fullAccess){contrast_rt->keyFailed=true;return contrast_rt_fail("trial_full_once_ack_or_security");}
        return true;
    }
    bool observe(bool restore){
        contrast_rt->nextReadStage=restore?"restore_pre_cfg_fa":"apply_pre_cfg_fa";
        if(!contrast_rt_read_all()||!contrast_rt_inspect(restore,false))return false;
        contrast_rt->noOpRestore=restore&&!contrast_rt->ownedDirty;
        return true;
    }
    bool enter(){
        if(restoring&&contrast_rt->noOpRestore){
            // Do not enter CFG again. If CFG1 was inherited from a failed
            // forward packet, the already observed owned-original/safe profile
            // still needs ONE necessary exit; CFG0 needs no reinitialization.
            uint16_t inherited=0;
            if(!gauge_word(0x3a,inherited)||(inherited&1)||((inherited>>1)&3)==0||contrast_safety_fault)
                return contrast_rt_fail("restore_noop_unknown_config_or_safety");
            contrast_rt->inheritedCfgExitRequired=bool(inherited&0x0400);
            if(contrast_rt->inheritedCfgExitRequired&&((inherited>>1)&3)!=1)return contrast_rt_fail("restore_inherited_cfg_requires_fa");
            return true;
        }
        uint16_t op=0;if(!gauge_word(0x3a,op)||((op>>1)&3)!=1||(op&1))return contrast_rt_fail("trial_enter_requires_fa_cal0");
        if(op&0x0400)return true; // retained CFG after interrupted forward stage
        const bool sent=gauge_control(GaugeCommand::EnterConfig);const bool observed=gauge_wait_config(true);
        return (sent&&observed)||contrast_rt_fail("trial_enter_once_ack_or_cfg");
    }
    bool write(bool restore){
        if(restore&&contrast_rt->noOpRestore)return true;
        for(size_t i=0;i<131;++i){const auto& f=contrast_rt->record.snapshot.fields[i];
            if(!contrast_rt_owned(f.address))continue; // includes autonomous9180: NEVER a write
            bool differs=false;for(size_t j=0;j<f.width;++j)differs|=f.original[j]!=f.target[j];if(!differs)continue;
            uint8_t current[4]{};if(!contrast_rt_read_field(i,current))return false;
            bool original=true,target=true;for(size_t j=0;j<f.width;++j){original&=current[j]==f.original[j];target&=current[j]==f.target[j];}
            if(!original&&!target){contrast_rt->thirdValue=true;return contrast_rt_fail("trial_unexpected_owned_third_value_no_overwrite");}
            const auto* desired=restore?f.original:f.target;bool equal=true;
            for(size_t j=0;j<f.width;++j)equal&=current[j]==desired[j];
            if(equal)continue;
            uint16_t op=0;
            if(!contrast_rt_physical(restore)||!gauge_word(0x3a,op)||((op>>1)&3)!=1||!(op&0x0400)||(op&1))return contrast_rt_fail("trial_single_write_fa_cfg_cal_gate");
            uint8_t packet[7]={0x3e,uint8_t(f.address),uint8_t(f.address>>8),0,0,0,0};
            for(size_t j=0;j<f.width;++j)packet[3+j]=desired[j];
            contrast_rt->possibleDirty=true;gauge_feed();
            // Set dirty BEFORE the one packet attempt: lost ACK is not evidence
            // of no write. Never resend the packet or commit after uncertain ACK.
            if(i2c_master_transmit(gauge,packet,3+f.width,20)!=ESP_OK)return contrast_rt_fail("trial_parameter_packet_ack");
            gauge_delay_ms(1);
            const uint8_t commit[]={0x60,mosaico_gauge::macChecksum(packet+1,2+f.width),uint8_t(4+f.width)};
            if(i2c_master_transmit(gauge,commit,sizeof(commit),20)!=ESP_OK)return contrast_rt_fail("trial_parameter_commit_ack");
            gauge_delay_ms(20);
            if(!contrast_rt_read_field(i,current))return false;
            for(size_t j=0;j<f.width;++j)if(current[j]!=desired[j])return contrast_rt_fail("trial_parameter_double_readback_mismatch");
        }
        return true;
    }
    bool verify(bool restore,bool postExit=false){
        contrast_rt->nextReadStage=postExit?"post_exit_fa":restore&&contrast_rt->noOpRestore?"restore_no_cfg_fa":"pre_exit_cfg_fa";
        return contrast_rt_read_all()&&contrast_rt_inspect(restore,true);
    }
    bool exit(){
        if(restoring&&contrast_rt->noOpRestore&&!contrast_rt->inheritedCfgExitRequired)return true;
        return gauge_exit_config()||contrast_rt_fail("trial_exit_0091_once_failed");
    }
    bool seal(){
        uint16_t op=0;if(!gauge_word(0x3a,op)||!mosaico_gauge::configExitAccepted(op))return contrast_rt_fail("trial_seal_requires_cfg0_init1");
        if(((op>>1)&3)==3){
            // The established 0091 path can already be SEALED, with public DM
            // denied. Preserve the pre-exit authenticated131 proof and require
            // final sealed standard+exact identity; never claim post-exit DM.
            contrast_rt->sealedStandardFallback=true;contrast_rt->protectionSealed=gauge_wait_security(3);return contrast_rt->protectionSealed;
        }
        if(((op>>1)&3)!=1||!verify(restoring,true))return contrast_rt_fail("trial_post_exit_accessible_full131_mismatch");
        contrast_rt->postExitFull131=true;
        if(contrast_rt->sealAttempted)return contrast_rt_fail("trial_seal_no_retry");
        contrast_rt->sealAttempted=true;const bool sent=gauge_access_word(AccessWord::Seal);const bool observed=gauge_wait_security(3);
        contrast_rt->protectionSealed=observed;
        return (sent&&observed)||contrast_rt_fail("trial_seal_once_ack_or_security");
    }
    bool final(bool restore){
        if(!contrast_rt_physical(restore)||!contrast_rt_exact_identity())return false;
        uint16_t op=0,dc=0,fcc=0,rm=0,soc=0;
        const uint16_t expected=restore?3000:65;
        if(!gauge_word(0x3a,op)||!gauge_word(0x3c,dc)||!gauge_word(0x12,fcc)||!gauge_word(0x10,rm)||!gauge_word(0x2c,soc))return contrast_rt_fail("trial_final_standard_read");
        // 0091 reinitialization may move SOC; that is NOT evidence of a cure.
        const int difference=int(rm)*100-int(soc)*int(fcc);
        return (mosaico_gauge::configExitAccepted(op)&&((op>>1)&3)==3&&dc==expected&&fcc==expected&&rm<=fcc&&soc<=100&&
            difference>=-int(fcc)*10&&difference<=int(fcc)*10)||contrast_rt_fail("trial_final_sealed_dc_fcc_rm_soc");
    }
    bool complete(bool restore){
        if(restore&&contrast_rt->untouchedMismatch)return contrast_rt_fail("owned3_original_untouched_unexpected_kept_restoring");
        if(!contrast_rt_intent(restore?scale_recovery::Phase::Original:scale_recovery::Phase::Applied))return false;
        publish(restore);return true;
    }
    void publish(bool restore){
        auto& t=*contrast_rt;const auto& s=t.record.snapshot;
        t.appliedVerifiedThisBoot=!restore;t.verifiedGeneration=s.generation;t.verifiedEpoch=s.epoch;
        t.active=!restore;if(restore){t.appliedUs=0;t.possibleDirty=false;t.ownedDirty=false;t.restoreCompletedUs=esp_timer_get_time();}
        t.reason=restore?"factory3000_restored_UNTRUSTED_not_native_no_Failed_waiver":"contrast65_verified_UNKNOWN_not_capacity_calibration";
        contrast_update_protection_locked();
    }
    void protect(){
        contrast_protection.store(true,std::memory_order_release);
        contrast_rt->protectionSealed=false;
        // Never Seal CFG1/CAL1/unknown, and never issue a cleanup 0091 that could
        // commit/reinitialize an unclassified mixed image. Durable intent stays.
        uint16_t op=0;
        if(!gauge_word(0x3a,op)||(op&0x0401)||!(op&0x0020)||((op>>1)&3)==0){
            contrast_rt->reason="protection_config_access_unresolved_no_seal_no_exit";return;
        }
        if(((op>>1)&3)==3){contrast_rt->protectionSealed=true;return;} // already protected: no repeated Seal
        if(!contrast_rt->accessTouched||contrast_rt->sealAttempted)return;
        contrast_rt->sealAttempted=true;(void)gauge_access_word(AccessWord::Seal);
        contrast_rt->protectionSealed=gauge_wait_security(3);
    }
};

bool contrast_begin_locked(){
 if(!contrast_storage_locked()||contrast_load_locked(true)!=contrast_journal::Status::Ready)return false;
 auto& t=*contrast_rt;
 t.record=t.journal.loaded.records[size_t(t.journal.loaded.selected)];
 if(!contrast_journal::shape(t.record))return contrast_rt_fail("closed_GCT1_profile_required");
 t.operation=true;t.accessTouched=false;t.sealAttempted=false;t.possibleDirty=false;
 t.noOpRestore=false;t.inheritedCfgExitRequired=false;t.ownedDirty=false;t.untouchedMismatch=false;t.unsafeCalibration=false;
 t.postExitFull131=false;t.sealedStandardFallback=false;t.protectionSealed=false;
 contrast_update_protection_locked();return true;
}
bool contrast_restore_locked(){
 if(!contrast_begin_locked())return false;
 auto& t=*contrast_rt;t.active=false;
 std::lock_guard<std::mutex> battery(battery_mutex);ContrastBackend backend;
 const auto result=mosaico_gauge_contrast_trial::restore(backend);t.operation=false;contrast_update_protection_locked();
 return result==mosaico_gauge_contrast_trial::Result::Restored;
}
bool contrast_poll_locked();
bool contrast_startup_restore_locked(){return contrast_poll_locked();}
struct ContrastStorageLease {bool held=MosaicoOta::tryStorageLease();~ContrastStorageLease(){if(held)MosaicoOta::releaseStorageLease();}};
bool contrast_capacity(char* out,size_t n,size_t required=1024){if(out&&n>=required)return true;if(out&&n)std::snprintf(out,n,"error=contrast_details_capacity_require_%u",unsigned(required));return false;}
bool contrast_report(char* out,size_t n,bool ok){
 const auto* t=contrast_rt;const auto* s=t&&contrast_status==contrast_journal::Status::Ready&&t->journal.loaded.selected>=0?&t->journal.loaded.records[size_t(t->journal.loaded.selected)].snapshot:nullptr;
 const auto c=contrast_context_locked();
 const int k=std::snprintf(out,n,"reason=%s success=%d schema=1 namespace=gcontrast profile=Factory3000Contrast runtime_ready=1 phase=%u generation=%lu epoch=%llu arm=%s factor=%lld verified=%d capacity_calibrated=0 original3000_UNTRUSTED=1 legacy_Failed_preserved=1 prepared_fresh=%d images_capability_verified=%d protection=%d observed131=%u authenticated=%d third_value=%d unexpected_count=%u first_unexpected=%04x post_exit_full131=%d sealed_standard_fallback=%d deadline_seconds=720 source_crc=70a767be C_crc=91336fc0 B_crc=675dd812 never_publish_physical_units=1 host_deadline_seconds=660 contract=%s base_required=%u reuse_credit=%u dynamic_required=%u",
 t?t->reason:"psram_unavailable",ok,unsigned(s?uint8_t(s->phase):255),static_cast<unsigned long>(s?s->generation:0),static_cast<unsigned long long>(s?s->epoch:0),t&&t->record.arm==contrast_model::Arm::B?"B":"C",static_cast<long long>(c.factor),c.verified,contrast_prepared_fresh(),t&&t->imagesSet,contrast_protected(),unsigned(t?t->observed.count:0),t&&t->observed.authenticated,t&&t->thirdValue,unsigned(t?t->unexpectedCount:0),t?t->firstUnexpected:0,t&&t->postExitFull131,t&&t->sealedStandardFallback,contrast_contract_digest,unsigned(t?t->budgetBase:56),unsigned(t?t->budgetReuse:0),unsigned(t?t->budgetRequired:56));
 if(k<0||size_t(k)>=n){std::snprintf(out,n,"error=contrast_details_truncated");return false;}return ok;
}
bool contrast_two_fields(const cJSON* object,const char* a,const char* b){
 if(!cJSON_IsObject(object)){return false;}
 unsigned seen=0;
 for(const cJSON* v=object->child;v;v=v->next){const unsigned bit=v->string&&!std::strcmp(v->string,a)?1:v->string&&!std::strcmp(v->string,b)?2:0;if(!bit||(seen&bit))return false;seen|=bit;}return seen==3;
}
bool contrast_capability(const cJSON* signature,const MosaicoOta::VerifiedImageDescriptor& image){
 // Compact wire proof carries only detached signature. The immutable local
 // contract digest is always included in the signed canonical message.
 if(!cJSON_IsString(signature)||!signature->valuestring||std::strlen(signature->valuestring)>104)return false;
 uint8_t sig[80]{},hash[32]{};size_t sigSize=0,hashSize=0;char sha[65]{};
 for(unsigned i=0;i<32;++i)std::snprintf(sha+2*i,3,"%02x",image.hash[i]);
 const int n=std::snprintf(contrast_rt->details,sizeof(contrast_rt->details),"MOSAICO-GAUGE-CONTRAST-CAP-v1\n%s\n%s\n%lu\n%s\n",contrast_contract_digest,image.version,static_cast<unsigned long>(image.size),sha);
 if(n<=0||size_t(n)>=sizeof(contrast_rt->details)||psa_crypto_init()!=PSA_SUCCESS||
 mbedtls_base64_decode(sig,sizeof(sig),&sigSize,reinterpret_cast<const uint8_t*>(signature->valuestring),std::strlen(signature->valuestring))||sigSize<8||sigSize>72||
 psa_hash_compute(PSA_ALG_SHA_256,reinterpret_cast<const uint8_t*>(contrast_rt->details),size_t(n),hash,sizeof(hash),&hashSize)!=PSA_SUCCESS||hashSize!=32)return false;
 mbedtls_pk_context key;mbedtls_pk_init(&key);
 int rc=mbedtls_pk_parse_public_key(&key,reinterpret_cast<const unsigned char*>(MosaicoOtaPublicKey),sizeof(MosaicoOtaPublicKey));
 if(rc==0&&(!mbedtls_pk_can_do_psa(&key,MBEDTLS_PK_ALG_ECDSA(PSA_ALG_SHA_256),PSA_KEY_USAGE_VERIFY_HASH)||mbedtls_pk_get_bitlen(&key)!=256))rc=-1;
 if(rc==0)rc=mbedtls_pk_verify(&key,MBEDTLS_MD_SHA256,hash,sizeof(hash),sig,sigSize);
 mbedtls_pk_free(&key);return rc==0;
}
// Poll is the ONLY automatic recovery dispatch. RO hooks never attempt keys.
bool contrast_poll_locked(){
 if(contrast_load_locked()!=contrast_journal::Status::Ready)return contrast_status!=contrast_journal::Status::Missing;
 auto& t=*contrast_rt;const auto& s=t.journal.loaded.records[size_t(t.journal.loaded.selected)].snapshot;
 if(s.phase==scale_recovery::Phase::Original||t.operation)return true;
 const int64_t now=esp_timer_get_time();
 // Revoke volatile units BEFORE eligibility, throttle, storage lease or I2C.
 // Durable Applied remains protected until safe authenticated restoration.
 const bool revoked=t.active&&!contrast_time_valid(t,now);
 if(revoked){t.active=false;t.nextPollUs=0;contrast_invalidate_locked();}
 if(now<=0)return true; // Invalid clock: no automatic keys; retry on valid time.
 t.lastPollUs=now;
 if(!contrast_recovery_runtime())return true;
 if(!revoked&&now<t.nextPollUs)return true;
 t.nextPollUs=now<=INT64_MAX-1000000?now+1000000:INT64_MAX;
 if(!t.active){
  if(t.bootRestoreAttempted||t.restoreAttempted)return true;
  ContrastStorageLease lease;if(!lease.held)return true;
  (void)contrast_restore_locked();return true;
 }
 bool due=false;
 {std::unique_lock<std::mutex> battery(battery_mutex,std::try_to_lock);if(!battery.owns_lock())return true;
  uint16_t mv=0,temp=0,op=0,dc=0,fcc=0;
  const bool read=gauge_word(8,mv)&&gauge_word(6,temp)&&gauge_word(0x3a,op)&&gauge_word(0x3c,dc)&&gauge_word(0x12,fcc);
  due=!read||mv<=3900||mv>4350||temp<2832||temp>3131||!mosaico_gauge::configExitAccepted(op)||((op>>1)&3)!=3||dc!=65||fcc!=65;
 }
 if(due){t.active=false;contrast_invalidate_locked();ContrastStorageLease lease;if(lease.held)(void)contrast_restore_locked();}
 return true;
}
} // anonymous namespace

bool Hal::gaugeContrastStatus(char* out,size_t n){
 if(!contrast_capacity(out,n)){return false;}
std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 const auto s=contrast_load_locked(true);return contrast_report(out,n,s==contrast_journal::Status::Ready||s==contrast_journal::Status::Missing);
}
bool Hal::gaugeContrastPrepare(const char* arm,char* out,size_t n){
 if(!contrast_capacity(out,n)){return false;}

 std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 if(!contrast_storage_locked())return contrast_report(out,n,false);
 auto& t=*contrast_rt;const auto status=contrast_load_locked(true);t.preparedThisBoot=false;
 if(!arm||(std::strcmp(arm,"C")&&std::strcmp(arm,"B"))){t.reason="prepare_arm_C_or_B_required";return contrast_report(out,n,false);}
 const auto requestedArm=!std::strcmp(arm,"C")?contrast_model::Arm::C:contrast_model::Arm::B;
 if((status!=contrast_journal::Status::Missing&&status!=contrast_journal::Status::Ready)||
 (status==contrast_journal::Status::Ready&&t.journal.loaded.records[size_t(t.journal.loaded.selected)].snapshot.phase!=scale_recovery::Phase::Original)){
  t.reason="prepare_requires_missing_or_Original_GCT1";return contrast_report(out,n,false);}
 // Schema2 coexistence is explicitly forbidden, even with historical Failed.
 if(!contrast_legacy_original_locked()||scale_load_locked(true)!=scale_journal::Status::Missing){t.reason="prepare_requires_legacy_gscale_missing";return contrast_report(out,n,false);}
 if(!contrast_space_locked()||!factory_runtime()){t.reason="prepare_headroom_or_runtime";return contrast_report(out,n,false);}
 {std::lock_guard<std::mutex> battery(battery_mutex);native_recovery::Evidence e{};const auto mac=getFactoryMac();
  if(!factory_physical(3)||!factory_identity()||!native_evidence_locked(e)||!native_recovery::exact(e,mac.data())){t.reason="prepare_exact_factory3000_failed_evidence";return contrast_report(out,n,false);}}
 const auto now=esp_timer_get_time();
 const bool selfRestored=contrast_self_restored_source_locked(now);
 if(factory_workspace&&factory_workspace->attempted){
  if(!selfRestored&&(!t.revalidatedUs||now<t.revalidatedUs||now-t.revalidatedUs>120000000)){t.reason="prepare_prior_FA_attempt_not_owned_fresh_revalidation";return contrast_report(out,n,false);}
 }else{
  // NO OTA lease and NO battery_mutex here: revalidation owns both itself.
  if(!gaugeFactoryAuditRevalidate(t.details,sizeof(t.details))){t.reason="prepare_fresh_factory_revalidation_failed";return contrast_report(out,n,false);}
  t.revalidatedUs=esp_timer_get_time();
 }
 if(!selfRestored&&(!factory_workspace||!factory_workspace->attempted||!factory_workspace->revalidation||!factory_workspace->complete||!factory_workspace->live||factory_workspace->record.count!=131||factory_workspace->record.sourceCrc!=contrast_model::canonicalSourceCrc)){t.reason="prepare_requires_complete_currentboot_exact131";return contrast_report(out,n,false);}
 ContrastStorageLease lease;if(!lease.held){t.reason="prepare_post_FA_OTA_lease";return contrast_report(out,n,false);}
 std::lock_guard<std::mutex> battery(battery_mutex);
 if(!contrast_rt_physical(false)||!factory_physical(3)||!factory_identity()||!contrast_space_locked()||(selfRestored&&!contrast_rt_images_current()))return contrast_report(out,n,false);
 t.record.~Record();new(&t.record) contrast_journal::Record{};t.record.mac=getFactoryMac();t.record.arm=requestedArm;
 t.restoreAttempted=false;t.bootRestoreAttempted=false;t.applyAttempted=false;t.preKeySpaceRejected=false;t.keysStarted=false;
 auto& s=t.record.snapshot;s.identity={0x220,3,4};s.binding=scale_journal::binding(t.record.mac);s.layoutId=scale_recovery::Layout::Public131V1;s.scale=scale_recovery::Scale::One;s.count=131;s.generation=1;s.epoch=1;s.phase=scale_recovery::Phase::Original;
 for(size_t i=0;i<131;++i){auto& f=s.fields[i];f.address=scale_recovery::layout[i].address;f.width=scale_recovery::layout[i].width;
  for(unsigned k=0;k<f.width;++k)f.original[k]=f.target[k]=selfRestored?t.observed.raw[i][k]:factory_workspace->raw[67+i*7+k];
  if(contrast_rt_owned(f.address)){const uint16_t v=f.address==0x929b?(requestedArm==contrast_model::Arm::C?0x112a:0x102a):65;f.target[0]=uint8_t(v>>8);f.target[1]=uint8_t(v);}
 }
 if(!contrast_journal::shape(t.record)){t.reason="prepare_full131_closed_source_target_mismatch";return contrast_report(out,n,false);}
 if(status==contrast_journal::Status::Missing){
  contrast_status=contrast_journal::saveNvs(t.record,{true,true,true},t.journal);t.loaded=true;t.everSeen=true;
  if(contrast_status!=contrast_journal::Status::Ready){t.reason="prepare_Original_durable_readback_failed";contrast_update_protection_locked();return contrast_report(out,n,false);}
 }else{
  const auto& old=t.journal.loaded.records[size_t(t.journal.loaded.selected)];
  if(old.arm!=requestedArm){
   if(old.snapshot.generation==UINT32_MAX||old.snapshot.epoch==UINT64_MAX){t.reason="prepare_counter_exhausted";return contrast_report(out,n,false);}
   t.record.snapshot.generation=old.snapshot.generation+1;t.record.snapshot.epoch=old.snapshot.epoch+1;
   contrast_status=contrast_journal::saveNvs(t.record,{true,true,true},t.journal);
   if(contrast_status!=contrast_journal::Status::Ready){contrast_update_protection_locked();t.reason="prepare_arm_commit_failed";return contrast_report(out,n,false);}
  }else{if(!contrast_journal::sameRecoveryImage(old,t.record)){t.reason="prepare_existing_original_image_mismatch";return contrast_report(out,n,false);}t.record=old;}
 }
 const auto& saved=t.journal.loaded.records[size_t(t.journal.loaded.selected)];
 if(!contrast_journal::sameRecoveryImage(saved,t.record)){t.reason="prepare_original_readback_image_mismatch";return contrast_report(out,n,false);}
 t.verifiedGeneration=saved.snapshot.generation;t.verifiedEpoch=saved.snapshot.epoch;t.preparedUs=esp_timer_get_time();t.preparedThisBoot=true;
 t.reason=selfRestored?"prepared_self_restore_cached_source131_plus_fresh_sealed_standard_NOT_fresh_sealed_DM":"prepared_fresh131_Original_durable_NO_parameter_writes_UNTRUSTED3000";contrast_update_protection_locked();return contrast_report(out,n,true);
}
bool Hal::gaugeContrastImages(const char* proof,char* out,size_t n){
 if(!contrast_capacity(out,n)){return false;}
ContrastStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 if(!contrast_storage_locked()){return contrast_report(out,n,false);}
 auto& t=*contrast_rt;t.imagesSet=false;
 if(!lease.held||!factory_runtime()||!proof||std::strlen(proof)>1400){t.reason="images_lease_runtime_length";return contrast_report(out,n,false);}
 size_t decoded=0;if(mbedtls_base64_decode(reinterpret_cast<uint8_t*>(t.decodedProof),sizeof(t.decodedProof)-1,&decoded,reinterpret_cast<const uint8_t*>(proof),std::strlen(proof))||!decoded||decoded>=sizeof(t.decodedProof)){t.reason="images_base64_invalid";return contrast_report(out,n,false);}for(size_t i=0;i<decoded;++i)if(!t.decodedProof[i]){t.reason="images_embedded_NUL";return contrast_report(out,n,false);}t.decodedProof[decoded]=0;
 const char* end=nullptr;cJSON* root=cJSON_ParseWithOpts(t.decodedProof,&end,true);bool ok=root&&cJSON_IsArray(root)&&cJSON_GetArraySize(root)==2;
 for(unsigned i=0;ok&&i<2;++i){const auto* item=cJSON_GetArrayItem(root,int(i));ok=contrast_two_fields(item,"image","signature");
  const auto* image=ok?cJSON_GetObjectItemCaseSensitive(item,"image"):nullptr;const auto* capability=ok?cJSON_GetObjectItemCaseSensitive(item,"signature"):nullptr;
  char* json=ok?cJSON_PrintUnformatted(image):nullptr;ok=json&&MosaicoOta::verifySignedImageDescriptor(json,t.images[i]);if(json)cJSON_free(json);
  if(ok)ok=contrast_capability(capability,t.images[i])&&contrast_rt_verify_image(i,t.images[i]);
 }
 if(root){cJSON_Delete(root);}
 if(ok&&!std::strcmp(t.images[0].version,t.images[1].version))ok=false;
 t.imagesSet=ok;t.reason=ok?"two_actual_SDK_VALID_signed_BINS_and_detached_profile_endorsements_verified":"images_manifest_capability_actual_BINS_rejected";return contrast_report(out,n,ok);
}
bool Hal::gaugeContrastApply(char* out,size_t n){
 if(!contrast_capacity(out,n)){return false;}
ContrastStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 if(!lease.held){return contrast_report(out,n,false);}
 if(!contrast_begin_locked()){return contrast_report(out,n,false);}
 auto& t=*contrast_rt;
 t.hostPowerAttested=true; // ONLY root local-console CONFIRM_EXTERNAL_POWER route.
 if(t.record.snapshot.phase!=scale_recovery::Phase::Original){t.reason="apply_requires_Original";t.operation=false;contrast_update_protection_locked();return contrast_report(out,n,false);}
 std::lock_guard<std::mutex> battery(battery_mutex);ContrastBackend backend;
 const auto result=mosaico_gauge_contrast_trial::apply(backend);t.operation=false;contrast_update_protection_locked();return contrast_report(out,n,result==mosaico_gauge_contrast_trial::Result::Applied);
}
bool Hal::gaugeContrastRestore(char* out,size_t n){
 if(!contrast_capacity(out,n)){return false;}
ContrastStorageLease lease;std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 if(!lease.held||contrast_load_locked(true)!=contrast_journal::Status::Ready)return contrast_report(out,n,false);
 if(contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)].snapshot.phase==scale_recovery::Phase::Original){contrast_rt->reason="already_Original_UNTRUSTED_no_keys_no_CFG";return contrast_report(out,n,true);}
 return contrast_report(out,n,contrast_restore_locked());
}
bool Hal::gaugeContrastObservedPage(size_t index,char* out,size_t n){
 // Fixed cached observation only: no loadNvs, keys, FA, I2C or allocation.
 if(!contrast_capacity(out,n,768))return false;
 std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
 mosaico_factory_observed_page::Page page{};
 if(!contrast_rt||!contrast_rt->observedGeneration||!contrast_rt->observedEpoch||contrast_rt->observedReadMs<=0||
    !scale_recovery::validPhase(contrast_rt->observedPhase)||!contrast_rt->observedStage||std::strlen(contrast_rt->observedStage)>80||
    !mosaico_factory_observed_page::encode(contrast_rt->record.snapshot,contrast_rt->observed,index,page)){
  std::snprintf(out,n,"cached=0 reason=page_not_observed_this_boot");return false;
 }
 const auto& t=*contrast_rt;
 const int used=std::snprintf(out,n,"version=1 cached=1 cached_only=1 no_i2c=1 no_nvs=1 page=%u first=%u count=%u total=131 authenticated=1 generation=%lu epoch=%llu read_ms=%lld cache_phase=%u stage=%s observed_crc=%08lx page_crc=%08lx encoding=le_addr2_width1_raw4_be_pad0_v1 data=",
  unsigned(index),unsigned(page.first),unsigned(page.count),static_cast<unsigned long>(t.observedGeneration),static_cast<unsigned long long>(t.observedEpoch),static_cast<long long>(t.observedReadMs),unsigned(t.observedPhase),t.observedStage,static_cast<unsigned long>(page.observedCrc),static_cast<unsigned long>(page.pageCrc));
 size_t encoded=0;
 if(used<0||size_t(used)>=n||mbedtls_base64_encode(reinterpret_cast<unsigned char*>(out+used),n-size_t(used),&encoded,page.bytes.data(),page.size)||encoded>300||size_t(used)+encoded>=n){
  std::snprintf(out,n,"cached=0 reason=page_output_capacity");return false;
 }
 out[size_t(used)+encoded]=0;return true;
}
