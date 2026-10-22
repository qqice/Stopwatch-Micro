// Included ONCE at the end of hal_mosaico.cpp. Preparation is read-only;
// the separately reviewed trial implementation below owns all bounded writes.
#include "mosaico_gauge_scale_journal.h"
#include "mosaico_gauge_physical_model.h"
#include <new>
#include "nvs.h"

namespace {
namespace scale_journal=mosaico_gauge_scale_journal;
namespace scale_recovery=mosaico_gauge_recovery;
constexpr uint32_t scale_original_crc=0x91336fc0U,scale_target_crc=0x43de4704U;
// Deliberately unavailable: a version number is NOT a two-partition capability
// proof. A later writer must verify actual appdesc hashes against both audited
// ELF/manifest identities before replacing this default-deny admission.
bool scale_both_images_verified=false;
bool scale_mutation_in_progress=false;
bool scale_known_safety_mismatch=false;
bool scale_safety_mismatch_locked(){return scale_known_safety_mismatch;}
bool scale_safety_address(uint16_t address){
    return (address>=0x91b5&&address<=0x91db)||address==0x91b4||address==0x91dd||address==0x91df||address==0x9206;
}
std::atomic<bool> scale_trial_protection{true}; // no I/O in OTA admission getter
bool scale_trial_boot_restore_locked();
void native_recovery_revoke_locked();
bool scale_append_trial_diagnostics(char*,size_t);
struct ScaleRuntime {
    scale_journal::Workspace journal{};
    scale_recovery::Observation observed{};
    scale_journal::Record freshDecoded{};
    bool loaded=false,originalVerifiedThisBoot=false,appliedVerifiedThisBoot=false,journalEverSeen=false;
    uint32_t verifiedGeneration=0;
    uint64_t verifiedEpoch=0;
    uint32_t freshSourceCrc=0,freshTargetCrc=0;
    char freshKey[10]{};
    bool freshBackupDurable=false,scaleBootCriticalRecovered=false;
    char recoveredCriticalSource[128]{};
    int64_t nativePreparedUs=0;uint32_t nativePreparedAuditGeneration=0;
    bool nativeResolvedThisBoot=false;uint32_t nativeEvidenceCrc=0,nativeProofGeneration=0;
    char nativeOriginalReason[128]{};
    size_t nvsAvailable=0;bool nvsStatsValid=false;
};
ScaleRuntime* scale_runtime=nullptr; // exclusively gauge_transaction_mutex owned
scale_journal::Status scale_load_status=scale_journal::Status::IoError;

const char* scale_status_name(scale_journal::Status s){
    using S=scale_journal::Status;
    switch(s){case S::Missing:return "missing";case S::Ready:return "ready";
    case S::Corrupt:return "corrupt";case S::IoError:return "io_error";
    case S::Ambiguous:return "ambiguous";case S::Rejected:return "rejected";
    case S::WriteFailed:return "write_failed";case S::CommitFailed:return "commit_failed";
    case S::ReadbackFailed:return "readback_failed";}
    return "unknown";
}
const char* scale_phase_name(scale_recovery::Phase p){
    switch(p){case scale_recovery::Phase::Original:return "original";
    case scale_recovery::Phase::Pending:return "pending";case scale_recovery::Phase::Applied:return "applied";
    case scale_recovery::Phase::Restoring:return "restoring";}
    return "unknown";
}
bool scale_storage_locked(){
    if(scale_runtime)return true;
    void* p=heap_caps_malloc(sizeof(ScaleRuntime),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!p){scale_load_status=scale_journal::Status::IoError;return false;}
    scale_runtime=new(p) ScaleRuntime{};
    return true;
}
// Review normalization is ONLY a comparison projection. Never encode/save it.
// 9180 is the single reviewed autonomous CCOffset; original and target must
// retain the SAME actual source bytes. All other 130 fields remain CRC-closed.
uint32_t scale_review_digest(const scale_journal::Record& r,bool target){
    uint32_t crc=0xffffffffU;
    for(const auto& f:r.snapshot.fields)for(size_t j=0;j<7;++j){
        uint8_t v=j<2?uint8_t(f.address>>(8*j)):j==2?f.width:(target?f.target[j-3]:f.original[j-3]);
        if(f.address==0x9180&&j>=3)v=j==3?0xff:j==4?0xf0:0;
        crc^=v;for(unsigned bit=0;bit<8;++bit)crc=(crc>>1)^((crc&1)?0xedb88320U:0);
    }
    return ~crc;
}
bool scale_same_review_images(const scale_journal::Record& a,const scale_journal::Record& b){
    if(a.mac!=b.mac||!scale_recovery::sameIdentity(a.snapshot.identity,b.snapshot.identity)||a.snapshot.scale!=b.snapshot.scale)return false;
    for(size_t i=0;i<131;++i){const auto& x=a.snapshot.fields[i];const auto& y=b.snapshot.fields[i];
        if(x.address!=y.address||x.width!=y.width)return false;
        if(x.address==0x9180)continue;
        for(size_t j=0;j<4;++j)if(x.original[j]!=y.original[j]||x.target[j]!=y.target[j])return false;
    }
    return true;
}
bool scale_reviewed_record(const scale_journal::Record& r){
    if(!scale_journal::shape(r)||r.snapshot.scale!=scale_recovery::Scale::Two)return false;
    const auto& offset=r.snapshot.fields[0];
    if(offset.address!=0x9180||offset.width!=2||(offset.original[0]==0x80&&offset.original[1]==0))return false;
    for(size_t j=0;j<4;++j)if(offset.original[j]!=offset.target[j])return false;
    return scale_review_digest(r,false)==scale_original_crc&&scale_review_digest(r,true)==scale_target_crc;
}
// IDF 6.1 nvs_page.cpp writeItem uses 32-byte entries; a 512-byte
// blob needs 16 data entries + a BLOB_DATA header + BLOB_IDX. Allow a
// second chunk header if split at a page tail. Reserve THREE such transient
// copies plus 16 entries, using available_entries (which excludes a GC page),
// NOT free_entries. This is admission headroom, not a flash-space reservation.
constexpr size_t scale_nvs_entries_per_blob=(scale_journal::blobSize+31)/32+3;
constexpr size_t scale_nvs_required_entries=3*scale_nvs_entries_per_blob+16;
static_assert(scale_nvs_required_entries==73,"schema2 conservative headroom");
bool scale_nvs_space_ready_locked(){
    if(!scale_runtime)return false;
    nvs_stats_t stats{};auto& rt=*scale_runtime;
    rt.nvsStatsValid=nvs_get_stats(nullptr,&stats)==ESP_OK;rt.nvsAvailable=rt.nvsStatsValid?stats.available_entries:0;
    return rt.nvsStatsValid&&rt.nvsAvailable>=scale_nvs_required_entries;
}
// Complete immutable fresh131 evidence only; never a ledger/epoch substitute.
// Caller holds the real storage lease + transaction + fresh safe-voltage guard.
bool scale_fresh_backup_locked(const scale_journal::Record& fresh,const scale_journal::Record& primary){
    auto& rt=*scale_runtime;rt.freshBackupDurable=false;
    rt.freshSourceCrc=scale_journal::imageDigest(fresh,false);rt.freshTargetCrc=scale_journal::imageDigest(fresh,true);
    if(scale_journal::sameRecoveryImage(fresh,primary)){
        std::snprintf(rt.freshKey,sizeof(rt.freshKey),"primary");rt.freshBackupDurable=true;return true;
    }
    if(!scale_same_review_images(fresh,primary)||!scale_reviewed_record(fresh))return false;
    const int printed=std::snprintf(rt.freshKey,sizeof(rt.freshKey),"f%08lx",static_cast<unsigned long>(rt.freshSourceCrc));
    if(printed!=9)return false;
    auto& expected=rt.journal.encoded;auto& actual=rt.journal.io;
    if(!scale_journal::encode(fresh,expected))return false;
    nvs_handle_t h=0;if(nvs_open(scale_journal::nvsNamespace,NVS_READWRITE,&h)!=ESP_OK)return false;
    bool created=false;size_t size=actual.size();esp_err_t status=nvs_get_blob(h,rt.freshKey,actual.data(),&size);
    if(status==ESP_ERR_NVS_NOT_FOUND){
        if(!scale_nvs_space_ready_locked()){nvs_close(h);return false;}
        created=true;gauge_feed();status=nvs_set_blob(h,rt.freshKey,expected.data(),expected.size());
        if(status==ESP_OK)status=nvs_commit(h);
        if(status==ESP_OK){size=actual.size();status=nvs_get_blob(h,rt.freshKey,actual.data(),&size);}gauge_feed();
    }
    nvs_close(h);
    // Existing CRC-key collisions/different records are never overwritten.
    // Fresh evidence may have an older generation/epoch; image identity is exact.
    if(status!=ESP_OK||size!=actual.size()||(created&&actual!=expected)||!scale_journal::decode(actual,fresh.mac,rt.freshDecoded)||
       !scale_journal::sameRecoveryImage(fresh,rt.freshDecoded))return false;
    rt.freshBackupDurable=true;return true;
}
// OTA/radio protection is NOT a physical-unit proof or apply permission.
void scale_update_protection_locked(){
    const bool missing=scale_load_status==scale_journal::Status::Missing;
    bool original=false;
    if(scale_runtime&&scale_load_status==scale_journal::Status::Ready){
        const auto& loaded=scale_runtime->journal.loaded;
        original=loaded.selected>=0&&loaded.records[size_t(loaded.selected)].snapshot.phase==scale_recovery::Phase::Original;
    }
    scale_trial_protection.store(scale_mutation_in_progress||!(missing||original),std::memory_order_release);
}
// Requires transaction, never battery_mutex; no gauge/control/key traffic.
scale_journal::Status scale_load_locked(bool refresh=false){
    if(!scale_storage_locked())return scale_load_status;
    auto& rt=*scale_runtime;
    if(!rt.loaded||refresh){
        scale_load_status=scale_journal::loadNvs(GetHAL().getFactoryMac(),rt.journal);rt.loaded=true;
        // A record that disappears during this boot is NOT initial Missing.
        if(scale_load_status==scale_journal::Status::Missing&&rt.journalEverSeen)scale_load_status=scale_journal::Status::Corrupt;
        if(scale_load_status==scale_journal::Status::Ready||scale_load_status==scale_journal::Status::Corrupt||scale_load_status==scale_journal::Status::Ambiguous)rt.journalEverSeen=true;
        if(scale_load_status==scale_journal::Status::Ready){
            const auto& r=rt.journal.loaded.records[size_t(rt.journal.loaded.selected)];
            if(!scale_reviewed_record(r)){scale_load_status=scale_journal::Status::Rejected;}
            if(r.snapshot.generation!=rt.verifiedGeneration||r.snapshot.epoch!=rt.verifiedEpoch){native_recovery_revoke_locked();rt.originalVerifiedThisBoot=false;rt.appliedVerifiedThisBoot=false;}
                        if(r.snapshot.phase!=scale_recovery::Phase::Original){
                // A verified stable Applied context is the explicit trial, not
                // an implicit native waiver. Unexpected phases still revoke.
                if(r.snapshot.phase!=scale_recovery::Phase::Applied||!rt.appliedVerifiedThisBoot)native_recovery_revoke_locked();
                rt.originalVerifiedThisBoot=false;
            }
            if(r.snapshot.phase!=scale_recovery::Phase::Applied)rt.appliedVerifiedThisBoot=false;
        }
        if(scale_load_status!=scale_journal::Status::Ready){native_recovery_revoke_locked();rt.originalVerifiedThisBoot=false;rt.appliedVerifiedThisBoot=false;}
    }
    scale_update_protection_locked();
    return scale_load_status;
}
void scale_invalidate_context_locked(){
#ifdef MOSAICO_FACTORY_SCALE_RUNTIME
    factory_scale_invalidate_locked();
#endif
    native_recovery_revoke_locked();
    if(scale_runtime){scale_runtime->originalVerifiedThisBoot=false;scale_runtime->appliedVerifiedThisBoot=false;scale_runtime->verifiedGeneration=0;scale_runtime->verifiedEpoch=0;}
    scale_update_protection_locked();
}
bool scale_journal_missing_locked(){
#ifdef MOSAICO_FACTORY_SCALE_RUNTIME
    if(factory_scale_present_locked())return false;
#endif
    return scale_load_locked()==scale_journal::Status::Missing;
}
bool scale_boot_fence_locked(){
    // Populate the old ledger's READ-ONLY cache even when GSJ3 owns the gauge;
    // maintenance OTA must never initialize/recover it from its admission path.
    const auto s=scale_load_locked();
#ifdef MOSAICO_FACTORY_SCALE_RUNTIME
    if(!factory_scale_boot_fence_locked())return false;
#endif
    if(s==scale_journal::Status::Missing)return true; // permits legacy, NOT physical scale1
    if(s!=scale_journal::Status::Ready)return false;
    const auto& r=scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)];
    // A persisted Original record allows the existing legacy reload path only.
    // It does not prove this boot's RAM profile; no implicit FA/key operation.
    if(r.snapshot.phase==scale_recovery::Phase::Original)return true;
    if(scale_runtime->appliedVerifiedThisBoot)return false; // live bounded trial, never legacy
    return scale_trial_boot_restore_locked();
}
mosaico_gauge_physical::UnitContext scale_unit_context_locked(){
#ifdef MOSAICO_FACTORY_SCALE_RUNTIME
    if(factory_scale_present_locked())return factory_scale_context_locked();
#endif
    mosaico_gauge_physical::UnitContext c{};
    if(scale_load_locked()!=scale_journal::Status::Ready)return c;
    const auto& rt=*scale_runtime;
    const auto& s=rt.journal.loaded.records[size_t(rt.journal.loaded.selected)].snapshot;
    c.unitEpoch=s.epoch;c.generation=s.generation;
    if(s.phase==scale_recovery::Phase::Original){c.phase=mosaico_gauge_physical::Phase::Original;
        if(rt.originalVerifiedThisBoot&&s.generation==rt.verifiedGeneration&&s.epoch==rt.verifiedEpoch){c.factor=1;c.verified=true;}}
    else if(s.phase==scale_recovery::Phase::Pending)c.phase=mosaico_gauge_physical::Phase::Pending;
    else if(s.phase==scale_recovery::Phase::Applied){c.phase=mosaico_gauge_physical::Phase::Applied;
        if(rt.appliedVerifiedThisBoot&&s.generation==rt.verifiedGeneration&&s.epoch==rt.verifiedEpoch){c.factor=2;c.verified=true;}}
    else c.phase=mosaico_gauge_physical::Phase::Restoring;
    return c;
}
bool scale_runtime_gate(){
    const auto p=GetNetworkQuota().powerStats();const auto b=GetCodexMicroBle().diagnostics();
    return p.locked&&p.phase==2&&!p.wifiRunning&&!b.advertising&&!b.connected&&!MosaicoOta::busy()&&!MosaicoOta::healthPending();
}
// Fresh standard reads, caller holds battery_mutex. Never changes access/CFG.
bool scale_standard_original_locked(){
    uint16_t op=0,mv=0,temp=0,dc=0,fcc=0,soc=0,current=0,average=0;
    if(!gauge||!gauge_word(0x3a,op)||!gauge_word(0x08,mv)||!gauge_word(0x06,temp)||
       !gauge_word(0x3c,dc)||!gauge_word(0x12,fcc)||!gauge_word(0x2c,soc)||
       !gauge_word(0x0c,current)||!gauge_word(0x14,average))return false;
    Hal::GaugeEngineSnapshot identity{};
    gauge_engine_locked(identity,mosaico_gauge_status::Explicit,0);
    const bool exactFirmware=(identity.validMask&(mosaico_gauge_engine::Identity|mosaico_gauge_engine::DefinitionMatch))==
        (mosaico_gauge_engine::Identity|mosaico_gauge_engine::DefinitionMatch);
    return exactFirmware&&((op>>1)&3)==3&&!(op&0x0401)&&(op&0x20)&&dc==65&&fcc==65&&soc<=100&&
        mv>=3500&&mv<=4350&&temp>=2832&&temp<=3181&&
        int16_t(current)>=-130&&int16_t(current)<=130&&int16_t(average)>=-130&&int16_t(average)<=130&&gauge_identity();
}
// Canonical matching by key (never relies on cache order matching sorted order).
// Raw caches are authenticated double-read bytes from the closed FA audit.
bool scale_cache_original_locked(scale_journal::Record& r){
    if(gauge_threshold_cached.readable!=mosaico_threshold::fieldCount||!gauge_threshold_cached.anchors||
       !gauge_threshold_cached_generation||gauge_calibration_cached.readable!=mosaico_calibration::fieldCount||
       !gauge_public_extra_cached.complete||gauge_public_extra_cached.readable!=mosaico_public_extra::fieldCount||
       gauge_public_extra_generation!=gauge_threshold_cached_generation)return false;
    r.mac=GetHAL().getFactoryMac();auto& s=r.snapshot;
    s.identity={0x0220,3,4};s.binding=scale_journal::binding(r.mac);s.layoutId=scale_recovery::Layout::Public131V1;
    s.generation=1;s.epoch=1;s.phase=scale_recovery::Phase::Original;s.scale=scale_recovery::Scale::Two;s.count=131;
    auto& observation=scale_runtime->observed;observation.count=131;observation.authenticated=true;observation.sealed=true;observation.cfg0=true;
    for(size_t n=0;n<131;++n){auto& f=s.fields[n];f={};f.address=scale_recovery::layout[n].address;f.width=scale_recovery::layout[n].width;bool found=false;
        for(size_t i=0;i<mosaico_threshold::fieldCount;++i)if(uint16_t(mosaico_threshold::fields[i].selector)==f.address){
            const uint16_t v=gauge_threshold_cached.raw[i];f.original[0]=uint8_t(v>>8);if(f.width==2)f.original[1]=uint8_t(v);found=true;}
        for(size_t i=0;i<mosaico_calibration::fieldCount;++i)if(uint16_t(mosaico_calibration::fields[i].selector)==f.address){
            for(size_t j=0;j<f.width;++j)f.original[j]=gauge_calibration_cached.raw[i][j];
            found=true;}
        for(size_t i=0;i<mosaico_public_extra::fieldCount;++i)if(uint16_t(mosaico_public_extra::fields[i].selector)==f.address){
            const uint16_t v=gauge_public_extra_cached.raw[i];f.original[0]=uint8_t(f.width==1?v:v>>8);if(f.width==2)f.original[1]=uint8_t(v);found=true;}
        if(!found)return false;
        for(size_t j=0;j<4;++j){f.target[j]=f.original[j];observation.raw[n][j]=f.original[j];}
    }
    scale_runtime->freshSourceCrc=scale_journal::imageDigest(r,false);
    scale_runtime->freshTargetCrc=0; // no reviewed candidate until all owned17 checks pass
    scale_runtime->freshBackupDurable=false;scale_runtime->freshKey[0]=0;
    // Fresh explicit cache proof also exposes known safety-calibration changes
    // against an existing immutable primary, even if review CRC then rejects it.
    if(scale_load_status==scale_journal::Status::Ready){
        const auto& primary=scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)];
        for(size_t i=0;i<131;++i)if(scale_safety_address(s.fields[i].address))
            for(size_t j=0;j<s.fields[i].width;++j)if(s.fields[i].original[j]!=primary.snapshot.fields[i].original[j])scale_known_safety_mismatch=true;
    }
    // Exactly the independently reviewed x2 candidate. No generic scaling,
    // address writer, CFG/control commands, hidden fields or key material.
    struct Change {uint16_t address;uint8_t width;uint32_t original,target;};
    static constexpr Change changes[]={
        {0x9184,4,0x7e73b645,0x7f73b645},{0x9188,4,0x930aa522,0x940aa522},{0x91de,1,5,10},
        {0x91fb,2,200,400},{0x9201,2,100,200},{0x920e,2,150,300},{0x9210,2,175,350},
        {0x9217,2,10,20},{0x9228,2,60,120},{0x922a,2,75,150},{0x922c,2,40,80},
        {0x923c,1,0xf6,0xec},{0x9264,2,1500,3000},{0x926b,2,200,400},
        {0x929d,2,65,130},{0x929f,2,65,130},{0x92ab,2,867,434}};
    size_t changed=0;
    for(const auto& change:changes){bool found=false;
        for(auto& f:s.fields)if(f.address==change.address){
            if(f.width!=change.width)return false;
            uint32_t original=0;for(size_t j=0;j<f.width;++j)original=(original<<8)|f.original[j];
            if(original!=change.original)return false;
            for(size_t j=0;j<f.width;++j)f.target[j]=uint8_t(change.target>>(8*(f.width-1-j)));
            ++changed;found=true;}
        if(!found)return false;
    }
    scale_runtime->freshTargetCrc=scale_journal::imageDigest(r,true);
    return changed==17&&scale_reviewed_record(r);
}
// Clear ONLY the current scale-fence critical after verified durable restore.
// Preserve its source reason in PSRAM; never modify historical boot snapshots.
void scale_reconcile_boot_status_locked(){
    constexpr const char* source="boot_critical_scale_recovery_required";
    if(gauge_boot_info.status!=Hal::GaugeBootReloadStatus::Critical||std::strcmp(gauge_boot_info.reason,source))return;
    auto& rt=*scale_runtime;std::snprintf(rt.recoveredCriticalSource,sizeof(rt.recoveredCriticalSource),"%s",gauge_boot_info.reason);
    rt.scaleBootCriticalRecovered=true;gauge_boot_info.status=Hal::GaugeBootReloadStatus::Applied;
    std::snprintf(gauge_boot_info.reason,sizeof(gauge_boot_info.reason),"boot_scale_original_restored_from_scale_fence_critical");
}
bool scale_details_preflight(char* out,size_t size){
    if(!out||size<1024){if(out&&size)std::snprintf(out,size,"error=scale_details_capacity_require_1024");return false;}
    return true;
}
bool scale_report(char* out,size_t size,const char* reason,bool success){
    const auto context=scale_unit_context_locked();const auto status=scale_load_status;
    const bool ready=status==scale_journal::Status::Ready;
    const auto* s=ready?&scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)].snapshot:nullptr;
    int count=-1;if(out&&size)count=std::snprintf(out,size,
        "reason=%s journal=%s phase=%s factor=%lld epoch=%llu generation=%lu physical_verified=%d primary_source_crc=%08lx primary_target_crc=%08lx fresh_source_crc=%08lx fresh_target_crc=%08lx fresh_key=%s fresh_durable=%d recovered_scale_boot_critical=%d source_critical=%s backup_fields=%u nvs_available_entries=%u nvs_required_entries=%u nvs_stats_valid=%d hardware_apply_gated=1 images_verified=%d FA_reads_only=1 no_CFG=1 parameter_writes=0",
        reason,scale_status_name(status),s?scale_phase_name(s->phase):"unknown",static_cast<long long>(context.factor),
        static_cast<unsigned long long>(s?s->epoch:0),static_cast<unsigned long>(s?s->generation:0),context.verified,
        static_cast<unsigned long>(s?scale_journal::imageDigest(scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)],false):0),
        static_cast<unsigned long>(s?scale_journal::imageDigest(scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)],true):0),
        static_cast<unsigned long>(scale_runtime?scale_runtime->freshSourceCrc:0),static_cast<unsigned long>(scale_runtime?scale_runtime->freshTargetCrc:0),
        scale_runtime&&scale_runtime->freshKey[0]?scale_runtime->freshKey:"none",scale_runtime&&scale_runtime->freshBackupDurable,scale_runtime&&scale_runtime->scaleBootCriticalRecovered,
        scale_runtime&&scale_runtime->recoveredCriticalSource[0]?scale_runtime->recoveredCriticalSource:"none",s?131U:0U,unsigned(scale_runtime?scale_runtime->nvsAvailable:0),unsigned(scale_nvs_required_entries),scale_runtime&&scale_runtime->nvsStatsValid,scale_both_images_verified);
    if(count<0||static_cast<size_t>(count)>=size){if(out&&size)std::snprintf(out,size,"error=scale_details_truncated");return false;}
    if(!scale_append_trial_diagnostics(out+count,size-static_cast<size_t>(count)))return false;
    return success;
}
struct ScaleStorageLease {bool held=MosaicoOta::tryStorageLease();~ScaleStorageLease(){if(held)MosaicoOta::releaseStorageLease();}};
} // namespace

bool Hal::gaugeScalePrepare(char* details,size_t detailsSize){
    if(!scale_details_preflight(details,detailsSize))return false;
    ScaleStorageLease lease;
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
#ifdef MOSAICO_FACTORY_SCALE_RUNTIME
    if(factory_scale_present_locked()){
        std::snprintf(details,detailsSize,"reason=GSJ3_owned_use_gauge_factory_scale no_changes=1");return false;
    }
#endif
    if(!lease.held)return scale_report(details,detailsSize,"blocked_ota_storage_lease",false);
    if(!scale_storage_locked())return scale_report(details,detailsSize,"blocked_psram_workspace",false);
    const auto initial=scale_load_locked(true);
    if(!scale_nvs_space_ready_locked())return scale_report(details,detailsSize,"blocked_nvs_available_entries",false);
    if(initial!=scale_journal::Status::Missing&&initial!=scale_journal::Status::Ready)return scale_report(details,detailsSize,"blocked_journal_failclosed",false);
    if(initial==scale_journal::Status::Ready&&scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)].snapshot.phase!=scale_recovery::Phase::Original)
        return scale_report(details,detailsSize,"blocked_recovery_required_use_restore",false);
    if(!scale_runtime_gate())return scale_report(details,detailsSize,"blocked_locked_phase2_radios_off",false);
    {std::lock_guard<std::mutex> battery(battery_mutex);
        if(!scale_standard_original_locked()){scale_invalidate_context_locked();return scale_report(details,detailsSize,"blocked_fresh_standard_identity",false);}}
    if(scale_runtime->originalVerifiedThisBoot)
        return scale_report(details,detailsSize,"already_prepared_this_boot_no_new_security_attempt",true);
    // This public coordinator owns its own battery lock and cleanup. It is an
    // explicit known-key FA-read audit; NEVER call while holding battery_mutex.
    if(!gaugeThresholdAudit(true,nullptr,0))return scale_report(details,detailsSize,"blocked_explicit_FA_audit",false);
    if(!scale_runtime_gate())return scale_report(details,detailsSize,"blocked_gate_changed_after_audit",false);
    {std::lock_guard<std::mutex> battery(battery_mutex);
        if(!scale_standard_original_locked()||!scale_cache_original_locked(scale_runtime->journal.scratch)){
            scale_invalidate_context_locked();return scale_report(details,detailsSize,"blocked_full131_crc_or_closed_standard",false);}
}
    auto& rt=*scale_runtime;
    if(initial==scale_journal::Status::Missing){
        // Recheck voltage/temperature/locks immediately before the only new NVS
        // write. This preparation call has no gauge parameter writer.
        std::lock_guard<std::mutex> battery(battery_mutex);
        if(!scale_runtime_gate()||!scale_standard_original_locked())return scale_report(details,detailsSize,"blocked_before_journal_commit",false);
        if(!scale_nvs_space_ready_locked())return scale_report(details,detailsSize,"blocked_nvs_available_entries",false);
        const auto saved=scale_journal::saveNvs(rt.journal.scratch,{true,true,true},rt.journal);
        if(saved!=scale_journal::Status::Ready){scale_load_locked(true);return scale_report(details,detailsSize,"blocked_original_journal_commit_readback",false);}
    }
    if(scale_load_locked(true)!=scale_journal::Status::Ready)return scale_report(details,detailsSize,"blocked_journal_reread",false);
    const auto& saved=rt.journal.loaded.records[size_t(rt.journal.loaded.selected)];
    if(!scale_same_review_images(saved,rt.journal.scratch)||saved.snapshot.phase!=scale_recovery::Phase::Original)
        return scale_report(details,detailsSize,"blocked_saved_original_target_mismatch",false);
    {std::lock_guard<std::mutex> battery(battery_mutex);
        if(!scale_runtime_gate()||!scale_standard_original_locked()||!scale_fresh_backup_locked(rt.journal.scratch,saved))
            return scale_report(details,detailsSize,"blocked_fresh131_immutable_backup",false);
    }
    rt.nativePreparedUs=esp_timer_get_time();rt.nativePreparedAuditGeneration=gauge_threshold_cached_generation;
    rt.originalVerifiedThisBoot=true;rt.appliedVerifiedThisBoot=false;rt.verifiedGeneration=saved.snapshot.generation;rt.verifiedEpoch=saved.snapshot.epoch;
    scale_trial_protection.store(false,std::memory_order_release);
    const bool spaceReady=scale_nvs_space_ready_locked();
    return scale_report(details,detailsSize,spaceReady?"prepared_actual131_durable_no_parameter_write":"prepared_backup_no_apply_headroom",spaceReady);
}
bool Hal::gaugeScaleStatus(char* details,size_t detailsSize){
    if(!scale_details_preflight(details,detailsSize))return false;
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    const auto status=scale_load_locked(true);
    (void)scale_nvs_space_ready_locked(); // readonly current SDK available_entries, not cached free-space guess
    return scale_report(details,detailsSize,status==scale_journal::Status::Ready?"readonly_persisted_context_not_live131_assertion":"readonly_no_trusted_unit_context",
        status==scale_journal::Status::Ready||status==scale_journal::Status::Missing);
}
bool Hal::gaugeScaleRestorePlan(char* details,size_t detailsSize){
    if(!scale_details_preflight(details,detailsSize))return false;
    std::lock_guard<std::recursive_mutex> transaction(gauge_transaction_mutex);
    const auto status=scale_load_locked(true);
    if(status!=scale_journal::Status::Ready)return scale_report(details,detailsSize,"restore_plan_unavailable_failclosed",false);
    const auto& saved=scale_runtime->journal.loaded.records[size_t(scale_runtime->journal.loaded.selected)];
    return scale_report(details,detailsSize,saved.snapshot.phase==scale_recovery::Phase::Original?
        "original_saved_fresh131_observation_required_before_any_restore":"restore_owned17_preserve9180_complete_only_if_stable113_match",true);
}

#include "mosaico_gauge_scale_trial.inc"
