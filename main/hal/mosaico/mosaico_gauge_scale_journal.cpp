#include "mosaico_gauge_scale_journal.h"
#ifdef ESP_PLATFORM
#include "nvs.h"

namespace mosaico_gauge_scale_journal {
namespace {
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
Status loadNvs(const std::array<uint8_t,6>& mac,Workspace& work){
    Handle h{};const auto error=nvs_open(nvsNamespace,NVS_READONLY,&h.value);
    if(error==ESP_ERR_NVS_NOT_FOUND){work.loaded.status=Status::Missing;work.loaded.valid={false,false};work.loaded.selected=-1;return Status::Missing;}
    if(error!=ESP_OK){work.loaded.status=Status::IoError;work.loaded.valid={false,false};work.loaded.selected=-1;return Status::IoError;}
    auto backend=store(h);return loadInto(backend,mac,work);
}
Status saveNvs(const Record& next,const WriteAdmission& admission,Workspace& work){
    if(!admission.gaugeTransactionHeld||!admission.otaLeaseHeld||!admission.stableVoltage||!shape(next))return Status::Rejected;
    Handle h{};if(nvs_open(nvsNamespace,NVS_READWRITE,&h.value)!=ESP_OK)return Status::IoError;
    auto backend=store(h);return saveInto(backend,next,admission,work);
}
} // namespace mosaico_gauge_scale_journal
#endif
