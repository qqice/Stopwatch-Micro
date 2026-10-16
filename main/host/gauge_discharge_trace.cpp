/* SPDX-License-Identifier: MIT */
#include "gauge_discharge_trace.h"
#include "gauge_discharge_trace_model.h"
#include "system_clock.h"
#include <hal/hal.h>
#include <ota/mosaico_ota.h>
#include <esp_timer.h>
#include <nvs.h>
#include <cstdio>
#include <mutex>

namespace GaugeDischargeTrace {
namespace {
std::mutex mutex;
Model staging;
Blob ioBlob;
Blob verifyBlob;
std::mutex exportMutex;
Model exportSnapshot;
Model model;
bool ready=false, corrupt=false, storageError=false;
enum class Command { None, Arm, Stop, Clear };
Command pending=Command::None;
uint64_t lastWriteAttempt=0;
bool writeAttempted=false;
// Separate namespace/key: never inspect or modify calibration journals.
constexpr const char* Namespace="gauge_trace";
constexpr const char* Key="trace1";
bool otaBlocked(){return MosaicoOta::busy() || MosaicoOta::healthPending();}
void load() {
    nvs_handle_t h;ioBlob={};size_t size=ioBlob.size();
    esp_err_t err=nvs_open(Namespace,NVS_READONLY,&h);
    if(err==ESP_OK){err=nvs_get_blob(h,Key,ioBlob.data(),&size);nvs_close(h);}
    if(err==ESP_OK){corrupt=size!=ioBlob.size() || !decode(ioBlob,model);}
    else if(err!=ESP_ERR_NVS_NOT_FOUND){corrupt=true;storageError=true;}
    // A session number is only reused when no evidence from that boot was saved.
    if(!corrupt && model.boot!=UINT32_MAX)++model.boot;
    else if(model.boot==UINT32_MAX)corrupt=true;
    ready=true;
}
bool save(const Model& value) {
    if(otaBlocked())return false;
    nvs_handle_t h;
    esp_err_t err=nvs_open(Namespace,NVS_READWRITE,&h);
    if(err==ESP_OK){
        ioBlob=encode(value);
        if(otaBlocked()){nvs_close(h);return false;}
        err=nvs_set_blob(h,Key,ioBlob.data(),ioBlob.size());
        if(err==ESP_OK)err=nvs_commit(h);
        nvs_close(h);
        // Only a successful commit acknowledgement permits a durable-success claim.
        // Reopen read-only and compare the entire canonical blob, including CRC.
        if(err==ESP_OK){
            err=nvs_open(Namespace,NVS_READONLY,&h);
            if(err==ESP_OK){
                verifyBlob={};size_t size=verifyBlob.size();
                err=nvs_get_blob(h,Key,verifyBlob.data(),&size);
                nvs_close(h);
                if(err==ESP_OK && !verifiedReadback(ioBlob,verifyBlob,size))err=ESP_FAIL;
            }
        }
    }
    storageError=err!=ESP_OK;return !storageError;
}
Sample convert(const Hal::BatteryTelemetry& t,uint64_t now) {
    Sample s;s.monotonicMs=now;
    const auto clock=MosaicoClock::snapshot();
    s.flags=(t.valid?Valid:0)|(t.capacityValid?CapacityValid:0)|(t.temperatureValid?TemperatureValid:0)|(clock.valid?EpochValid:0)|(t.nominalConfigured?NominalConfigured:0);
    s.epoch=clock.valid?uint64_t(clock.epoch):0;s.soc=t.reportedSoc;s.voltageMv=t.voltageMv;
    s.currentMa=t.currentMa;s.averageCurrentMa=t.averageCurrentMa;s.remainingMah=t.remainingMah;
    s.fullMah=t.fullMah;s.designMah=t.designMah;s.operationStatus=t.operationStatus;
    s.batteryStatus=t.batteryStatus;s.stateOfHealth=t.stateOfHealth;s.cycleCount=t.cycleCount;
    s.temperatureDeciK=t.temperatureDeciK;return s;
}
bool request(Command c){
    std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);
    if(!lock || !ready || pending!=Command::None)return false;
    if(c==Command::Arm && (corrupt || model.count || model.armed))return false;
    if(c==Command::Stop && !model.armed)return false;
    pending=c;return true;
}
void emitStatus(EmitLine emit,void* ctx,const Model& m,bool isReady,bool bad,bool err,Command cmd,uint64_t writeMs){
    char line[384];std::snprintf(line,sizeof(line),"{\"type\":\"gauge_trace_status\",\"schema\":1,\"ready\":%u,\"armed\":%u,\"pending\":%u,\"corrupt\":%u,\"storage_error\":%u,\"count\":%u,\"capacity\":64,\"boot\":%lu,\"dirty\":%u,\"last_write_attempt_ms\":%llu,\"low_seen\":%u,\"finalized_charge_after_low\":%u}",unsigned(isReady),unsigned(m.armed),unsigned(cmd),unsigned(bad),unsigned(err),unsigned(m.count),static_cast<unsigned long>(m.boot),unsigned(m.dirty),static_cast<unsigned long long>(writeMs),unsigned(m.lowSeen),unsigned(m.finalizedChargeAfterLow));emit(line,ctx);
}
}
bool armConfirm(){return request(Command::Arm);}
bool stop(){return request(Command::Stop);}
bool clearConfirm(){return request(Command::Clear);}
void service(){
    std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);if(!lock || otaBlocked())return;
    if(!ready)load();
    const uint64_t now=uint64_t(esp_timer_get_time()/1000);
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
    std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);
    if(!lock){emit("{\"type\":\"gauge_trace_status\",\"busy\":1}",ctx);return;}
    emitStatus(emit,ctx,model,ready,corrupt,storageError,pending,lastWriteAttempt);
}
void exportRows(EmitLine emit,void* ctx){
    if(!emit)return;
    std::unique_lock<std::mutex> exportLock(exportMutex,std::try_to_lock);
    if(!exportLock){emit("{\"type\":\"gauge_trace_export\",\"busy\":1}",ctx);return;}
    auto& copy=exportSnapshot;
    bool r,b,e;Command cmd;uint64_t writeMs;
    {
        std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);
        if(!lock){emit("{\"type\":\"gauge_trace_export\",\"busy\":1}",ctx);return;}
        copy=model;r=ready;b=corrupt;e=storageError;cmd=pending;writeMs=lastWriteAttempt;
    }
    emitStatus(emit,ctx,copy,r,b,e,cmd,writeMs);
    emit("{\"type\":\"gauge_trace_schema\",\"schema\":1,\"flags\":\"1=valid,2=capacityValid,4=temperatureValid,8=epochValid,16=nominalConfigured\",\"current_sign\":\"positive=charging\",\"temperature_unit\":\"0.1K\",\"order\":\"oldest_first\"}",ctx);
    for(size_t i=0;i<copy.count;++i){
        const auto& s=copy.at(i);char line[640];
        std::snprintf(line,sizeof(line),"{\"type\":\"gauge_trace_row\",\"i\":%u,\"boot\":%lu,\"monotonic_ms\":%llu,\"epoch\":%llu,\"flags\":%u,\"soc\":%u,\"voltage_mv\":%u,\"current_ma\":%d,\"average_current_ma\":%d,\"remaining_mah\":%u,\"full_mah\":%u,\"design_mah\":%u,\"operation_status\":%u,\"battery_status\":%u,\"state_of_health\":%u,\"cycle_count\":%u,\"temperature_deci_k\":%u}",unsigned(i),static_cast<unsigned long>(s.boot),static_cast<unsigned long long>(s.monotonicMs),static_cast<unsigned long long>(s.epoch),unsigned(s.flags),unsigned(s.soc),unsigned(s.voltageMv),int(s.currentMa),int(s.averageCurrentMa),unsigned(s.remainingMah),unsigned(s.fullMah),unsigned(s.designMah),unsigned(s.operationStatus),unsigned(s.batteryStatus),unsigned(s.stateOfHealth),unsigned(s.cycleCount),unsigned(s.temperatureDeciK));emit(line,ctx);
    }
}
}
