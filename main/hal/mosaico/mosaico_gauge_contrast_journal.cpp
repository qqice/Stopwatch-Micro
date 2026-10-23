#include "mosaico_gauge_contrast_journal.h"
#ifdef ESP_PLATFORM
#include "nvs.h"
namespace mosaico_gauge_contrast_journal {
namespace {
struct Handle {nvs_handle_t value=0;bool writable=false;~Handle(){if(value)nvs_close(value);}};
ReadResult readSlot(void* c,unsigned slot,Blob& out){
    if(slot>1)return ReadResult::Error;
    const auto h=static_cast<Handle*>(c)->value;if(!h)return ReadResult::Missing;
    size_t n=0;const auto e=nvs_get_blob(h,nvsSlotKeys[slot],nullptr,&n);
    if(e==ESP_ERR_NVS_NOT_FOUND)return ReadResult::Missing;
    if(e!=ESP_OK||n!=blobSize)return ReadResult::Error;
    n=out.size();return nvs_get_blob(h,nvsSlotKeys[slot],out.data(),&n)==ESP_OK&&n==blobSize?ReadResult::Present:ReadResult::Error;
}
bool writeSlot(void* c,unsigned slot,const Blob& b){
    if(slot>1)return false;
    auto& h=*static_cast<Handle*>(c);
    // Lazily allocate/open writable namespace only AFTER saveInto capacity and
    // transition admission. Namespace cost is included in the 56 entry gate.
    if(!h.writable){if(h.value)nvs_close(h.value);h.value=0;
        if(nvs_open(nvsNamespace,NVS_READWRITE,&h.value)!=ESP_OK)return false;
        h.writable=true;}
    return nvs_set_blob(h.value,nvsSlotKeys[slot],b.data(),b.size())==ESP_OK;
}
bool commit(void* c){return nvs_commit(static_cast<Handle*>(c)->value)==ESP_OK;}
bool capacity(void*,size_t& available){nvs_stats_t stats{};if(nvs_get_stats(nullptr,&stats)!=ESP_OK)return false;available=stats.available_entries;return true;}
Store store(Handle& h){return {&h,readSlot,writeSlot,commit,capacity};}
}
Status loadNvs(const std::array<uint8_t,6>& mac,Workspace& w){
    Handle h{};const auto e=nvs_open(nvsNamespace,NVS_READONLY,&h.value);
    if(e!=ESP_OK){w.loaded={};return w.loaded.status=e==ESP_ERR_NVS_NOT_FOUND?Status::Missing:Status::IoError;}
    auto b=store(h);return loadInto(b,mac,w);
}
Status saveNvs(const Record& next,const Admission& admission,Workspace& w){
    if(&next==&w.loaded.records[0]||&next==&w.loaded.records[1]||!admission.transactionHeld||!admission.otaLeaseHeld||!admission.stableVoltage||!shape(next))return Status::Rejected;
    Handle h{};const auto e=nvs_open(nvsNamespace,NVS_READONLY,&h.value);
    if(e!=ESP_OK&&e!=ESP_ERR_NVS_NOT_FOUND)return Status::IoError;
    auto b=store(h);return saveInto(b,next,admission,w);
}
} // namespace mosaico_gauge_contrast_journal
#endif
