#pragma once
#include "mosaico_nand_store_model.h"
#include "mosaico_nand_log_model.h"
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
// Destructive: caller must have received explicit user approval. No implicit call
// from load/save. Official erase skips factory-marked bad blocks. No filesystem.
// Callback runs once/block in this task; caller may feed its legitimately enrolled
// watchdog and throttle UI/serial progress. No automatic watchdog enrollment.
Result formatConfirm(bool authorized,Progress progress=nullptr,void* context=nullptr);
}
