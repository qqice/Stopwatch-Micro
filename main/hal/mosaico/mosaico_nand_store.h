#pragma once
#include "mosaico_nand_store_model.h"
#include <esp_err.h>

namespace MosaicoNandStore {
using Payload=Model::Payload;
using Progress=void(*)(uint32_t completedBlocks,uint32_t totalBlocks,void* context);
enum class Stage { Admission, Allocation, Bus, Pins, Device, Identity, Init, Geometry, Erase, Record, Reopen, Complete };
struct Result {
    esp_err_t error=ESP_OK,cleanupError=ESP_OK;
    Stage stage=Stage::Admission;
    Model::Result record{};
    uint32_t logicalPages=0;
    bool formatted=false;
};
// Explicit caller authorization is required even for loading: official reads may
// refresh ECC in our already-owned store. Root gates OTA/radio/voltage/TWDT task.
// Each call owns and releases SPI3, the Dhara context and all DMA buffers.
Result load(Payload& output,bool authorized);
Result save(const Payload& input,bool authorized);
// Destructive: caller must have received explicit user approval. No implicit call
// from load/save. Official erase skips factory-marked bad blocks. No filesystem.
// Callback runs once/block in this task; caller may feed its legitimately enrolled
// watchdog and throttle UI/serial progress. No automatic watchdog enrollment.
Result formatConfirm(bool authorized,Progress progress=nullptr,void* context=nullptr);
}
