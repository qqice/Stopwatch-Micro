#pragma once
#include "mosaico_nand_store_model.h"
#include "mosaico_nand_log_model.h"
#include "mosaico_gauge_cold_store_model.h"
#include <esp_err.h>
#include <cstddef>

namespace MosaicoNandStore {
using Payload=Model::Payload;
using LogPage=LogModel::Page;
using LogState=LogModel::State;
using Progress=void(*)(uint32_t completedBlocks,uint32_t totalBlocks,void* context);
// Once at boot after HAL ready, before BLE/radio allocations. No late retry.
bool prepareDmaBuffer();
// Read-only boot residency credit; zero unless the independent DMA allocation
// was validated and remains ready/non-faulted. No allocation or NAND I/O.
size_t startupInternalReservation();
void dmaStatus(char* output,size_t length); // RAM-only; never initializes NAND.
enum class Stage { Admission, Allocation, Bus, Pins, Device, Identity, Init, Geometry, Erase, Record, Reopen, Complete };
struct Result {
    esp_err_t error=ESP_OK,cleanupError=ESP_OK;
    Stage stage=Stage::Admission;
    Model::Result record{};
    uint32_t logicalPages=0;
    bool formatted=false;
    LogModel::Code logCode=LogModel::Code::Empty;
    mosaico_gauge_cold_store::Code coldCode=mosaico_gauge_cold_store::Code::Empty;
};
// Explicit caller authorization is required even for loading: official reads may
// refresh ECC in our already-owned store. Root gates OTA/radio/voltage/TWDT task.
// Each call releases SPI3, Dhara/PSRAM workspaces and its DMA-buffer lease.
// The independent boot DMA reservation remains allocated for this boot.
Result load(Payload& output,bool authorized);
Result save(const Payload& input,bool authorized);
// Raw page: sessionBoot LE32 at 16, count LE16 at 20, 64-byte records at 60.
// Header/unused records/CRC finalized by append; state refreshed from media.
// Load/read never publish metadata. A complete orphan is exposed in RAM only.
Result logLoad(LogState& output,bool authorized);
Result logAppend(LogPage& page,LogState& output,bool authorized);
Result logRead(uint64_t sequence,LogPage& output,bool authorized);
// Fixed logical pairs only. No raw address, implicit format or fallback page.
enum class ColdArea : uint8_t { Journal=1, Proof=2 };
using ColdRecord=mosaico_gauge_cold_store::Record;
struct ColdPolicy {
    void* context=nullptr;
    bool (*validFn)(void*,const ColdRecord&)=nullptr;
    bool (*initialFn)(void*,const ColdRecord&)=nullptr;
    bool (*transitionFn)(void*,const ColdRecord&,const ColdRecord&)=nullptr;
    constexpr bool ready()const{return validFn&&initialFn&&transitionFn;}
    constexpr bool valid(const ColdRecord& r)const{return validFn&&validFn(context,r);}
    constexpr bool initial(const ColdRecord& r)const{return initialFn&&initialFn(context,r);}
    constexpr bool transition(const ColdRecord& a,const ColdRecord& b)const{return transitionFn&&transitionFn(context,a,b);}
};
// Callbacks MUST be pure: no I/O, locks or gauge/battery/OTA callbacks. Caller
// must not hold battery_mutex. Inherited lease requires exact current-task
// ownership, checked internally; a bool alone never establishes ownership.
// Output is published only after all media/cleanup checks succeed. Empty is
// ESP_OK with coldCode=Empty and leaves output unchanged. Save additionally
// closes/resumes Dhara and re-loads/compares the COMPLETE selected Record.
// A durable grant requires error==ESP_OK AND cleanupError==ESP_OK AND
// stage==Complete AND coldCode==Ready, plus the caller's exact payload digest.
// coldCode may retain Ready if a later reopen/cleanup step fails: never use it
// alone as durability evidence or an IC-write authorization.
Result coldLoad(ColdArea area,ColdRecord& output,const ColdPolicy& policy,bool authorized,bool inheritStorageLease=false);
Result coldSave(ColdArea area,const ColdRecord& input,const ColdPolicy& policy,bool authorized,bool inheritStorageLease=false);
// Destructive: caller must have received explicit user approval. No implicit call
// from load/save. Official erase skips factory-marked bad blocks. No filesystem.
// Callback runs once/block in this task; caller may feed its legitimately enrolled
// watchdog and throttle UI/serial progress. No automatic watchdog enrollment.
Result formatConfirm(bool authorized,Progress progress=nullptr,void* context=nullptr);
}
