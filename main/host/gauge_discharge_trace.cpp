/* SPDX-License-Identifier: MIT */
#include "gauge_discharge_trace.h"
#include "gauge_discharge_trace_model.h"
#include "gauge_trace_storage_codec.h"
#include "system_clock.h"
#include <hal/hal.h>
#include <ota/mosaico_ota.h>
#include <esp_timer.h>
#include <nvs.h>
#include <esp_heap_caps.h>
#include <new>
#include <cstdio>
#include <mutex>

namespace GaugeDischargeTrace {
namespace {
std::mutex mutex;
std::mutex exportMutex;
struct Storage {
    Model model, staging, exportSnapshot;
    Blob ioBlob{}, verifyBlob{};
    StorageCodec::Packed packedIo{}, packedVerify{};
    char traceLine[640]{}; // Protected by exportMutex through emit callbacks.
};
Storage* storage=nullptr;
bool allocationError=false, allocationAttempted=false;
uint64_t lastAllocationAttempt=0;
bool ready=false, corrupt=false, storageError=false;
enum class Command { None, Arm, Stop, Clear, Checkpoint };
// RAM-only diagnostics: a later success never hides the last failure this boot.
// Length/CRC/byte mismatches have no SDK error; use ESP_FAIL plus their stage.
enum class FailureStage : uint8_t { None, ReadOpen, Load, WriteOpen, Set, Commit, Reopen, Read, Length, Crc, Compare };
esp_err_t lastNvsError=ESP_OK;
FailureStage failureStage=FailureStage::None;
size_t encodedBytes=0;
void failure(esp_err_t error,FailureStage stage){lastNvsError=error;failureStage=stage;}
Command pending=Command::None;
uint64_t lastWriteAttempt=0;
bool writeAttempted=false;
// Separate namespace/key: never inspect or modify calibration journals.
constexpr const char* Namespace="gauge_trace";
constexpr const char* Key="trace1";
bool otaBlocked(){return MosaicoOta::busy() || MosaicoOta::healthPending();}
bool ensureStorage(uint64_t now) {
    if(storage)return true;
    if(allocationAttempted && now-lastAllocationAttempt<60000)return false;
    allocationAttempted=true;lastAllocationAttempt=now;
    void* memory=heap_caps_calloc(1,sizeof(Storage),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!memory){allocationError=true;storageError=true;return false;}
    storage=new(memory) Storage;
    allocationError=false;storageError=false;return true;
}
void load() {
    auto& model=storage->model;auto& ioBlob=storage->ioBlob;auto& packed=storage->packedIo;
    nvs_handle_t h;ioBlob={};size_t size=0;
    esp_err_t err=nvs_open(Namespace,NVS_READONLY,&h);
    FailureStage stage=FailureStage::ReadOpen;
    if(err==ESP_OK){
        stage=FailureStage::Load;err=nvs_get_blob(h,Key,nullptr,&size);
        if(err==ESP_OK){
            if(size<StorageCodec::HeaderSize || size>BlobSize){stage=FailureStage::Length;err=ESP_FAIL;}
            else err=nvs_get_blob(h,Key,packed.bytes.data(),&size);
        }
        nvs_close(h);
    }
    if(err==ESP_OK){
        packed.size=size;encodedBytes=size;
        const auto result=StorageCodec::unpack(packed,ioBlob);
        if(result!=StorageCodec::Error::None){
            corrupt=true;failure(ESP_FAIL,result==StorageCodec::Error::Length?FailureStage::Length:result==StorageCodec::Error::Crc?FailureStage::Crc:FailureStage::Load);
        }else if(!decode(ioBlob,model)){corrupt=true;failure(ESP_FAIL,FailureStage::Load);}
    }
    else if(err!=ESP_ERR_NVS_NOT_FOUND){corrupt=true;storageError=true;failure(err,stage);}
    // A session number is only reused when no evidence from that boot was saved.
    if(!corrupt && model.boot!=UINT32_MAX)++model.boot;
    else if(model.boot==UINT32_MAX)corrupt=true;
    ready=true;
}
bool save(const Model& value) {
    auto& ioBlob=storage->ioBlob;auto& verifyBlob=storage->verifyBlob;
    auto& packed=storage->packedIo;auto& readback=storage->packedVerify;
    if(otaBlocked())return false;
    nvs_handle_t h;
    FailureStage stage=FailureStage::WriteOpen;
    esp_err_t err=nvs_open(Namespace,NVS_READWRITE,&h);
    if(err==ESP_OK){
        ioBlob=encode(value);StorageCodec::pack(ioBlob,packed);encodedBytes=packed.size;
        if(otaBlocked()){nvs_close(h);return false;}
        stage=FailureStage::Set;err=nvs_set_blob(h,Key,packed.bytes.data(),packed.size);
        if(err==ESP_OK){stage=FailureStage::Commit;err=nvs_commit(h);}
        nvs_close(h);
        // Only a successful commit acknowledgement permits a durable-success claim.
        // Reopen read-only and compare the entire canonical blob, including CRC.
        if(err==ESP_OK){
            stage=FailureStage::Reopen;err=nvs_open(Namespace,NVS_READONLY,&h);
            if(err==ESP_OK){
                verifyBlob={};size_t size=readback.bytes.size();
                stage=FailureStage::Read;err=nvs_get_blob(h,Key,readback.bytes.data(),&size);
                nvs_close(h);
                if(err==ESP_OK){
                    readback.size=size;
                    const auto result=StorageCodec::unpack(readback,verifyBlob);
                    if(result!=StorageCodec::Error::None){
                        stage=result==StorageCodec::Error::Length?FailureStage::Length:result==StorageCodec::Error::Crc?FailureStage::Crc:FailureStage::Compare;err=ESP_FAIL;
                    }else if(!verifiedReadback(ioBlob,verifyBlob,BlobSize)){stage=FailureStage::Compare;err=ESP_FAIL;}
                }
            }
        }
    }
    if(err!=ESP_OK)failure(err,stage);
    storageError=err!=ESP_OK;return !storageError;
}
Sample convert(const Hal::BatteryTelemetry& t,uint64_t now) {
    Sample s;s.monotonicMs=now;
    const auto clock=MosaicoClock::snapshot();
    s.flags=(t.valid?Valid:0)|(t.capacityValid?CapacityValid:0)|(t.temperatureValid?TemperatureValid:0)|(clock.valid?EpochValid:0)|(t.nominalConfigured?NominalConfigured:0)|(t.rawCoulombValid?RawCoulombValid:0);
    s.epoch=clock.valid?uint64_t(clock.epoch):0;s.soc=t.reportedSoc;s.voltageMv=t.voltageMv;
    s.currentMa=t.currentMa;s.averageCurrentMa=t.averageCurrentMa;s.remainingMah=t.remainingMah;
    s.fullMah=t.fullMah;s.designMah=t.designMah;s.operationStatus=t.operationStatus;
    s.batteryStatus=t.batteryStatus;s.stateOfHealth=t.stateOfHealth;s.cycleCount=t.cycleCount;
    s.temperatureDeciK=t.temperatureDeciK;s.rawCoulombMah=t.rawCoulombMah;return s;
}
bool request(Command c){
    std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);
    if(!lock || !ready || pending!=Command::None)return false;
    auto& model=storage->model;
    if(c==Command::Arm && (corrupt || model.count || model.armed))return false;
    if(c==Command::Stop && !model.armed)return false;
    if(c==Command::Checkpoint && (corrupt || model.armed || !model.count))return false;
    pending=c;return true;
}
void emitStatus(EmitLine emit,void* ctx,const Model& m,bool isReady,bool bad,bool err,Command cmd,uint64_t writeMs){
    auto& traceLine=storage->traceLine;
    std::snprintf(traceLine,sizeof(traceLine),"{\"type\":\"gauge_trace_status\",\"schema\":2,\"ready\":%u,\"armed\":%u,\"pending\":%u,\"corrupt\":%u,\"storage_error\":%u,\"count\":%u,\"capacity\":64,\"boot\":%lu,\"dirty\":%u,\"last_write_attempt_ms\":%llu,\"low_seen\":%u,\"finalized_charge_after_low\":%u,\"storage_external\":1,\"storage_bytes\":%u,\"allocation_error\":%u,\"internal_free\":%u,\"internal_largest\":%u}",unsigned(isReady),unsigned(m.armed),unsigned(cmd),unsigned(bad),unsigned(err),unsigned(m.count),static_cast<unsigned long>(m.boot),unsigned(m.dirty),static_cast<unsigned long long>(writeMs),unsigned(m.lowSeen),unsigned(m.finalizedChargeAfterLow),unsigned(sizeof(Storage)),unsigned(allocationError),unsigned(heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)),unsigned(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)));emit(traceLine,ctx);
}
void emitNvsStatus(EmitLine emit,void* ctx,esp_err_t error,FailureStage stage,size_t encoded){
    auto& traceLine=storage->traceLine;
    nvs_stats_t stats{};
    const esp_err_t statsError=nvs_get_stats(nullptr,&stats);
    if(statsError!=ESP_OK)stats={}; // Never present partial counts as valid.
    std::snprintf(traceLine,sizeof(traceLine),"{\"type\":\"gauge_trace_nvs\",\"last_nvs_error\":%ld,\"failure_stage\":%u,\"stats_error\":%ld,\"total\":%u,\"used\":%u,\"free\":%u,\"available\":%u,\"namespaces\":%u,\"encoded_bytes\":%u}",static_cast<long>(error),unsigned(stage),static_cast<long>(statsError),unsigned(stats.total_entries),unsigned(stats.used_entries),unsigned(stats.free_entries),unsigned(stats.available_entries),unsigned(stats.namespace_count),unsigned(encoded));
    emit(traceLine,ctx);
}
void emitUnavailable(EmitLine emit,void* ctx,bool error,bool allocationFailed){
    // Allocation-failure-only small stack fallback; normal lines live in PSRAM.
    char line[288];
    std::snprintf(line,sizeof(line),"{\"type\":\"gauge_trace_status\",\"schema\":2,\"ready\":0,\"armed\":0,\"pending\":0,\"storage_error\":%u,\"storage_external\":0,\"storage_bytes\":%u,\"allocation_error\":%u,\"internal_free\":%u,\"internal_largest\":%u}",unsigned(error),unsigned(sizeof(Storage)),unsigned(allocationFailed),unsigned(heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)),unsigned(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)));
    emit(line,ctx);
}

}
bool armConfirm(){return request(Command::Arm);}
bool stop(){return request(Command::Stop);}
bool clearConfirm(){return request(Command::Clear);}
bool checkpointConfirm(){return request(Command::Checkpoint);}
void service(){
    std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);if(!lock || otaBlocked())return;
    const uint64_t now=uint64_t(esp_timer_get_time()/1000);
    if(!ensureStorage(now))return;
    auto& model=storage->model;auto& staging=storage->staging;
    if(!ready)load();
    const bool command=pending!=Command::None;
    if(corrupt && !command)return;
    const bool canAttempt=!writeAttempted || now-lastWriteAttempt>=60000;
    if(!model.due(now) && !((command || (model.finalizedChargeAfterLow && model.dirty)) && canAttempt))return;
    Hal::BatteryTelemetry t;
    // Admission failure is not a gauge read failure and must not produce a false invalid row.
    if(!GetHAL().gaugeTraceTrySample(t))return;
    const Sample s=convert(t,now);
    if(model.due(now) && !corrupt)model.append(s);
    if(!writable(s) || otaBlocked())return;
    if(command && canAttempt){
        staging=model;auto& next=staging;
        if(pending==Command::Arm)next.armed=true;
        if(pending==Command::Stop)next.armed=false;
        if(pending==Command::Clear){next=Model{};next.boot=model.boot;}
        lastWriteAttempt=now;writeAttempted=true;
        if(save(next)){model=next;model.dirty=false;pending=Command::None;corrupt=false;}
        return;
    }
    if((model.checkpointDue(now) || (model.finalizedChargeAfterLow && model.dirty)) && canAttempt){
        lastWriteAttempt=now;writeAttempted=true;model.attempted(now);
        if(save(model))model.dirty=false;
    }
}
void status(EmitLine emit,void* ctx){
    if(!emit)return;
    std::unique_lock<std::mutex> exportLock(exportMutex,std::try_to_lock);
    if(!exportLock){emit("{\"type\":\"gauge_trace_status\",\"busy\":1}",ctx);return;}
    bool r,b,e,allocated,allocationFailed;Command cmd;uint64_t writeMs;
    esp_err_t nvsError;FailureStage stage;size_t encoded;
    {
        std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);
        if(!lock){emit("{\"type\":\"gauge_trace_status\",\"busy\":1}",ctx);return;}
        allocated=storage!=nullptr;allocationFailed=allocationError;
        if(allocated)storage->exportSnapshot=storage->model;
        r=ready;b=corrupt;e=storageError;cmd=pending;writeMs=lastWriteAttempt;
        nvsError=lastNvsError;stage=failureStage;encoded=encodedBytes;
    }
    if(!allocated){emitUnavailable(emit,ctx,e,allocationFailed);return;}
    emitStatus(emit,ctx,storage->exportSnapshot,r,b,e,cmd,writeMs);
    emitNvsStatus(emit,ctx,nvsError,stage,encoded);
}
void exportRows(EmitLine emit,void* ctx){
    if(!emit)return;
    std::unique_lock<std::mutex> exportLock(exportMutex,std::try_to_lock);
    if(!exportLock){emit("{\"type\":\"gauge_trace_export\",\"busy\":1}",ctx);return;}
    bool r,b,e,allocated,allocationFailed;Command cmd;uint64_t writeMs;
    esp_err_t nvsError;FailureStage stage;size_t encoded;
    {
        std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);
        if(!lock){emit("{\"type\":\"gauge_trace_export\",\"busy\":1}",ctx);return;}
        allocated=storage!=nullptr;allocationFailed=allocationError;
        if(allocated)storage->exportSnapshot=storage->model;
        r=ready;b=corrupt;e=storageError;cmd=pending;writeMs=lastWriteAttempt;
        nvsError=lastNvsError;stage=failureStage;encoded=encodedBytes;
    }
    if(!allocated){emitUnavailable(emit,ctx,e,allocationFailed);return;}
    auto& copy=storage->exportSnapshot;auto& traceLine=storage->traceLine;
    emitStatus(emit,ctx,copy,r,b,e,cmd,writeMs);
    emitNvsStatus(emit,ctx,nvsError,stage,encoded);
    emit("{\"type\":\"gauge_trace_schema\",\"schema\":2,\"flags\":\"1=valid,2=capacityValid,4=temperatureValid,8=epochValid,16=nominalConfigured,32=rawCoulombValid\",\"current_sign\":\"positive=charging\",\"temperature_unit\":\"0.1K\",\"order\":\"oldest_first\"}",ctx);
    for(size_t i=0;i<copy.count;++i){
        const auto& s=copy.at(i);
        std::snprintf(traceLine,sizeof(traceLine),"{\"type\":\"gauge_trace_row\",\"i\":%u,\"boot\":%lu,\"monotonic_ms\":%llu,\"epoch\":%llu,\"flags\":%u,\"soc\":%u,\"voltage_mv\":%u,\"current_ma\":%d,\"average_current_ma\":%d,\"remaining_mah\":%u,\"full_mah\":%u,\"design_mah\":%u,\"operation_status\":%u,\"battery_status\":%u,\"state_of_health\":%u,\"cycle_count\":%u,\"temperature_deci_k\":%u,\"raw_coulomb_mah\":%u}",unsigned(i),static_cast<unsigned long>(s.boot),static_cast<unsigned long long>(s.monotonicMs),static_cast<unsigned long long>(s.epoch),unsigned(s.flags),unsigned(s.soc),unsigned(s.voltageMv),int(s.currentMa),int(s.averageCurrentMa),unsigned(s.remainingMah),unsigned(s.fullMah),unsigned(s.designMah),unsigned(s.operationStatus),unsigned(s.batteryStatus),unsigned(s.stateOfHealth),unsigned(s.cycleCount),unsigned(s.temperatureDeciK),unsigned(s.rawCoulombMah));emit(traceLine,ctx);
    }
}
}
