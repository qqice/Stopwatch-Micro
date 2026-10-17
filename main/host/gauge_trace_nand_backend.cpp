/* SPDX-License-Identifier: MIT */
#include "gauge_trace_nand_backend.h"
#include <cstdio>
#ifdef MOSAICO_NAND_STORE
#include <hal/mosaico/mosaico_nand_store.h>
#include <hal/hal.h>
#include <hal/ble/codex_micro_ble.h>
#include <host/network_quota.h>
#include <ota/mosaico_ota.h>
#include <esp_ota_ops.h>
#include <esp_heap_caps.h>
#include <esp_task_wdt.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/idf_additions.h>
#include <nvs.h>
#include <atomic>
#include <mutex>
#include <new>
#include <type_traits>

namespace GaugeDischargeTrace::NandBackend {
namespace {
static_assert(std::is_same_v<Blob,MosaicoNandStore::Payload>);
constexpr const char* Namespace="gauge_trace";
constexpr const char* PolicyKey="nand_v1";
enum class Policy:int { Unknown=-1, Nvs=0, Pending=1, Nand=2, Fault=3 };
enum class Phase:int { Idle, Admission, Queued, Pending, Format, Migrate, Activate, Ready, Failed };
std::mutex selectionMutex;
std::mutex statusMutex;
std::atomic<Policy> policy{Policy::Unknown};
std::atomic<Phase> phase{Phase::Idle};
std::atomic_bool working{false},formatAttempted{false};
std::atomic_bool retryAdmission{false};
std::atomic<int> nvsError{ESP_OK},nandError{ESP_OK},cleanupError{ESP_OK},nandStage{0},recordCode{0},recordStage{0};
std::atomic<int> nvsStage{0}; // 1=open-read,2=read,3=open-write,4=set,5=commit,6=reopen,7=verify.
std::atomic<uint32_t> completed{0},total{0};
std::atomic<uint64_t> generation{0};

bool crcValid(const Blob& b){return get(b,BlobSize-4,4)==crc(b);}
bool readPolicy(){
    if(policy.load()!=Policy::Unknown)return policy.load()==Policy::Nvs||policy.load()==Policy::Nand;
    nvs_handle_t h=0;uint8_t value=0;
    nvsStage.store(1);esp_err_t err=nvs_open(Namespace,NVS_READONLY,&h);
    if(err==ESP_OK){nvsStage.store(2);err=nvs_get_u8(h,PolicyKey,&value);nvs_close(h);}
    if(err==ESP_ERR_NVS_NOT_FOUND){policy.store(Policy::Nvs);return true;}
    nvsError.store(err);
    if(err!=ESP_OK){policy.store(Policy::Fault);phase.store(Phase::Failed);return false;}
    if(value==1){policy.store(Policy::Pending);phase.store(Phase::Failed);return false;}
    if(value==2){policy.store(Policy::Nand);return true;}
    nvsError.store(ESP_ERR_INVALID_STATE);policy.store(Policy::Fault);phase.store(Phase::Failed);return false;
}
bool writePolicy(uint8_t value){
    nvs_handle_t h=0;nvsStage.store(3);esp_err_t err=nvs_open(Namespace,NVS_READWRITE,&h);
    if(err==ESP_OK){
        nvsStage.store(4);err=nvs_set_u8(h,PolicyKey,value);
        if(err==ESP_OK){nvsStage.store(5);err=nvs_commit(h);}
        nvs_close(h);
    }
    if(err==ESP_OK){
        nvsStage.store(6);err=nvs_open(Namespace,NVS_READONLY,&h);
        if(err==ESP_OK){uint8_t actual=0;nvsStage.store(7);err=nvs_get_u8(h,PolicyKey,&actual);nvs_close(h);
            if(err==ESP_OK&&actual!=value)err=ESP_ERR_INVALID_STATE;}
    }
    nvsError.store(err);
    if(err!=ESP_OK){policy.store(Policy::Fault);return false;}
    policy.store(value==1?Policy::Pending:Policy::Nand);return true;
}
bool admission(bool format){
    if(MosaicoOta::busy()||MosaicoOta::healthPending())return false;
    const auto* running=esp_ota_get_running_partition();esp_ota_img_states_t state=ESP_OTA_IMG_UNDEFINED;
    if(!running||esp_ota_get_state_partition(running,&state)!=ESP_OK||state!=ESP_OTA_IMG_VALID)return false;
    if(!format){
        const auto t=GetHAL().batteryTelemetry(false);
        return t.valid&&t.voltageMv>=3300&&t.temperatureValid&&t.temperatureDeciK>=2832&&t.temperatureDeciK<=3182&&
            !MosaicoOta::busy()&&!MosaicoOta::healthPending();
    }
    const auto power=GetNetworkQuota().powerStats();const auto ble=GetCodexMicroBle().diagnostics();
    if(!power.locked||power.phase!=2||power.wifiRunning||ble.advertising||ble.connected)return false;
    const auto t=GetHAL().batteryTelemetry(true);
    return t.valid&&t.capacityValid&&t.temperatureValid&&t.nominalConfigured&&t.voltageMv>=(format?3900:3300)&&
        t.temperatureDeciK>=2832&&t.temperatureDeciK<=3182&&t.designMah==65&&t.fullMah==65&&
        (t.operationStatus&0x0401)==0&&(t.operationStatus&0x20)&&((t.operationStatus>>1)&3)==3&&
        !MosaicoOta::busy()&&!MosaicoOta::healthPending();
}
bool retainResult(const MosaicoNandStore::Result& r){
    nandError.store(r.error);cleanupError.store(r.cleanupError);nandStage.store(int(r.stage));
    recordCode.store(int(r.record.code));recordStage.store(int(r.record.stage));generation.store(r.record.generation);
    return r.error==ESP_OK&&r.cleanupError==ESP_OK;
}
void progress(uint32_t done,uint32_t blocks,void* context){
    completed.store(done);total.store(blocks);
    if(*static_cast<bool*>(context))(void)esp_task_wdt_reset();
}
void formatWorker(void* memory){
    auto* snapshot=static_cast<Blob*>(memory);
    const bool joined=esp_task_wdt_add(nullptr)==ESP_OK;
    // Independent fresh admission snapshot, never an immediate forced reread.
    vTaskDelay(pdMS_TO_TICKS(1000));
    bool ok=false;
    {
        std::lock_guard<std::mutex> lock(selectionMutex);
        phase.store(Phase::Admission);
        if(admission(true)&&policy.load()==Policy::Nvs){
            phase.store(Phase::Pending);
            if(writePolicy(1)){
                phase.store(Phase::Format);
                bool enrolled=joined;
                if(retainResult(MosaicoNandStore::formatConfirm(true,progress,&enrolled))){
                    if(joined)(void)esp_task_wdt_reset();
                    phase.store(Phase::Migrate);
                    if(admission(false)&&retainResult(MosaicoNandStore::save(*snapshot,true))){
                        if(joined)(void)esp_task_wdt_reset();
                        phase.store(Phase::Activate);ok=writePolicy(2);
                    }
                }
            }
        }
        if(!ok){if(nandError.load()==ESP_OK)nandError.store(ESP_ERR_INVALID_STATE);phase.store(Phase::Failed);}
        else phase.store(Phase::Ready);
    }
    snapshot->~Blob();heap_caps_free(memory);
    if(joined)(void)esp_task_wdt_delete(nullptr);
    working.store(false);
    vTaskDeleteWithCaps(nullptr);
}
}

bool busy(){return working.load();}
bool retryable(){return retryAdmission.load();}
bool load(Blob& canonical,bool& selected){
    selected=true;retryAdmission.store(false);
    std::unique_lock<std::mutex> lock(selectionMutex,std::try_to_lock);
    if(!lock||busy()){retryAdmission.store(true);return false;}
    if(!readPolicy())return false;
    if(policy.load()==Policy::Nvs){selected=false;return false;}
    if(policy.load()!=Policy::Nand)return false;
    if(!admission(false)){retryAdmission.store(true);return false;}
    void* memory=heap_caps_calloc(1,sizeof(Blob),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!memory){nandError.store(ESP_ERR_NO_MEM);retryAdmission.store(true);return false;}
    auto* scratch=new(memory) Blob;
    const auto result=MosaicoNandStore::load(*scratch,true);
    const bool ok=retainResult(result)&&crcValid(*scratch);
    // Do not mistake these clean temporary PSRAM/SPI3/driver-DMA resource
    // refusals for damaged evidence. Admission failures can
    // instead be the store's latched cleanup fence, and must remain terminal.
    using StoreStage=MosaicoNandStore::Stage;
    if(result.cleanupError==ESP_OK&&
       (((result.stage==StoreStage::Allocation||result.stage==StoreStage::Init)&&result.error==ESP_ERR_NO_MEM)||
        (result.stage==StoreStage::Bus&&(result.error==ESP_ERR_INVALID_STATE||result.error==ESP_ERR_NO_MEM))))
        retryAdmission.store(true);
    if(ok)canonical=*scratch;else if(nandError.load()==ESP_OK)nandError.store(ESP_ERR_INVALID_CRC);
    scratch->~Blob();heap_caps_free(memory);return ok;
}
bool save(const Blob& canonical,bool& selected){
    selected=true;retryAdmission.store(false);
    std::unique_lock<std::mutex> lock(selectionMutex,std::try_to_lock);
    if(!lock||busy()){retryAdmission.store(true);return false;}
    if(!readPolicy())return false;
    if(policy.load()==Policy::Nvs){selected=false;return false;}
    if(policy.load()!=Policy::Nand||!crcValid(canonical))return false;
    if(!admission(false)){retryAdmission.store(true);return false;}
    return retainResult(MosaicoNandStore::save(canonical,true));
}
bool beginFormat(const Blob& snapshot){
    std::unique_lock<std::mutex> lock(selectionMutex,std::try_to_lock);
    if(!lock||busy()||formatAttempted.load()||!readPolicy()||policy.load()!=Policy::Nvs)return false;
    if(!crcValid(snapshot)||get(snapshot,8,2)==0||get(snapshot,8,2)>Capacity||snapshot[10]||!admission(true))return false;
    formatAttempted.store(true);phase.store(Phase::Admission);
    void* memory=heap_caps_calloc(1,sizeof(Blob),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!memory){nandError.store(ESP_ERR_NO_MEM);phase.store(Phase::Failed);return false;}
    auto* copy=new(memory) Blob(snapshot);
    working.store(true);phase.store(Phase::Queued);
    if(xTaskCreateWithCaps(formatWorker,"trace_nand",8192,copy,1,nullptr,MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)!=pdPASS){
        working.store(false);phase.store(Phase::Failed);nandError.store(ESP_ERR_NO_MEM);copy->~Blob();heap_caps_free(memory);return false;
    }
    return true;
}
void status(EmitLine emit,void* context){
    if(!emit)return;
    // Atomic-only: UART status remains usable during the asynchronous erase.
    std::unique_lock<std::mutex> lock(statusMutex,std::try_to_lock);
    if(!lock){emit("{\"type\":\"gauge_trace_nand\",\"status_busy\":1}",context);return;}
    static char line[384];
    std::snprintf(line,sizeof(line),"{\"type\":\"gauge_trace_nand\",\"policy\":%d,\"busy\":%u,\"phase\":%d,\"completed\":%lu,\"total\":%lu,\"nvs_error\":%d,\"nvs_stage\":%d,\"nand_error\":%d,\"cleanup_error\":%d,\"nand_stage\":%d,\"record_code\":%d,\"record_stage\":%d,\"generation\":%llu}",
        int(policy.load()),unsigned(busy()),int(phase.load()),static_cast<unsigned long>(completed.load()),static_cast<unsigned long>(total.load()),nvsError.load(),nvsStage.load(),nandError.load(),cleanupError.load(),nandStage.load(),recordCode.load(),recordStage.load(),static_cast<unsigned long long>(generation.load()));
    emit(line,context);
}
}
#else
namespace GaugeDischargeTrace::NandBackend {
bool busy(){return false;}
bool retryable(){return false;}
bool load(Blob&,bool& selected){selected=false;return false;}
bool save(const Blob&,bool& selected){selected=false;return false;}
bool beginFormat(const Blob&){return false;}
void status(EmitLine emit,void* context){if(emit)emit("{\"type\":\"gauge_trace_nand\",\"supported\":0}",context);}
}
#endif
