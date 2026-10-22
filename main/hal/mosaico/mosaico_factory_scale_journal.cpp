#include "mosaico_factory_scale_journal.h"
#ifdef ESP_PLATFORM
#include "nvs.h"
#include <cstring>

namespace mosaico_factory_scale_journal {
namespace {
constexpr size_t textLength(const char* p){size_t n=0;while(p[n])++n;return n;}
struct Handle {nvs_handle_t value=0;~Handle(){if(value)nvs_close(value);}};
ReadResult readSlot(void* context,unsigned slot,Blob& out){
    if(slot>1)return ReadResult::Error;
    const auto handle=static_cast<Handle*>(context)->value;
    size_t size=0;const auto probe=nvs_get_blob(handle,nvsSlotKeys[slot],nullptr,&size);
    if(probe==ESP_ERR_NVS_NOT_FOUND)return ReadResult::Missing;
    if(probe!=ESP_OK||size!=blobSize)return ReadResult::Error;
    size=out.size();
    return nvs_get_blob(handle,nvsSlotKeys[slot],out.data(),&size)==ESP_OK&&size==blobSize?ReadResult::Present:ReadResult::Error;
}
bool writeSlot(void* context,unsigned slot,const Blob& blob){
    return slot<2&&nvs_set_blob(static_cast<Handle*>(context)->value,nvsSlotKeys[slot],blob.data(),blob.size())==ESP_OK;
}
bool commit(void* context){return nvs_commit(static_cast<Handle*>(context)->value)==ESP_OK;}
Store store(Handle& h){return {&h,readSlot,writeSlot,commit};}
}
Status loadNvs(const std::array<uint8_t,6>& mac,StorageWorkspace& work){
    Handle h{};const auto error=nvs_open(nvsNamespace,NVS_READONLY,&h.value);
    if(error==ESP_ERR_NVS_NOT_FOUND){work.loaded.status=Status::Missing;work.loaded.valid={false,false};work.loaded.selected=-1;return Status::Missing;}
    if(error!=ESP_OK){work.loaded.status=Status::IoError;work.loaded.valid={false,false};work.loaded.selected=-1;return Status::IoError;}
    auto backend=store(h);return loadInto(backend,mac,work);
}
mosaico_factory_scale_budget::Usage reuseUsageNvs(const Loaded& loaded){
    namespace budget=mosaico_factory_scale_budget;
    budget::Usage result{};if(!budget::pair(loaded))return result;
    static_assert(blobSize==512&&textLength(nvsNamespace)<16&&textLength(nvsSlotKeys[0])<16&&textLength(nvsSlotKeys[1])<16);
    Handle h{};if(nvs_open(nvsNamespace,NVS_READONLY,&h.value)!=ESP_OK)return result;
    size_t before=0,after=0;
    if(nvs_get_used_entry_count(h.value,&before)!=ESP_OK||before<budget::persistedCreditMaximum)return result;
    nvs_iterator_t iterator=nullptr;auto error=nvs_entry_find_in_handle(h.value,NVS_TYPE_ANY,&iterator);
    unsigned seen=0,count=0;bool ok=true;
    while(error==ESP_OK&&count<3){
        nvs_entry_info_t info{};
        if(nvs_entry_info(iterator,&info)!=ESP_OK||std::strncmp(info.namespace_name,nvsNamespace,sizeof(info.namespace_name))||info.type!=NVS_TYPE_BLOB){ok=false;break;}
        unsigned slot=2;for(unsigned i=0;i<2;++i)if(!std::strncmp(info.key,nvsSlotKeys[i],sizeof(info.key)))slot=i;
        if(slot>1||(seen&(1u<<slot))){ok=false;break;}
        size_t length=0;if(nvs_get_blob(h.value,nvsSlotKeys[slot],nullptr,&length)!=ESP_OK||length!=blobSize){ok=false;break;}
        seen|=1u<<slot;++count;error=nvs_entry_next(&iterator);
    }
    nvs_release_iterator(iterator);
    if(!ok||error!=ESP_ERR_NVS_NOT_FOUND||count!=2||seen!=3||
       nvs_get_used_entry_count(h.value,&after)!=ESP_OK||after!=before)return result;
    result.entries=before;result.exactNamespace=result.exactKeys=result.exactLengths=result.stableStats=true;return result;
}
Status saveNvs(const Record& next,const WriteAdmission& admission,StorageWorkspace& work){
    if(!admission.gaugeTransactionHeld||!admission.otaLeaseHeld||!admission.stableVoltage||
       &next==&work.scratch||&next==&work.loaded.records[0]||&next==&work.loaded.records[1]||!shape(next))return fail(work,Status::Rejected);
    // Initial creation only: preserve completion/restoration after Pending
    // consumes headroom. There is no caller-supplied threshold bypass.
    if(initialRecord(next)){
        nvs_stats_t stats{};
        if(nvs_get_stats(nullptr,&stats)!=ESP_OK)return fail(work,Status::IoError);
        if(stats.available_entries<nvsRequiredAvailableEntries)return fail(work,Status::Rejected);
    }
    Handle h{};if(nvs_open(nvsNamespace,NVS_READWRITE,&h.value)!=ESP_OK)return fail(work,Status::IoError);
    auto backend=store(h);return saveInto(backend,next,admission,work);
}
} // namespace mosaico_factory_scale_journal
#endif
