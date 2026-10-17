#pragma once
#include <cstdint>
#include <cstddef>
#include <esp_err.h>

namespace MosaicoNandProbe {
enum class Stage : uint8_t { Complete, Lock, Bus, Hold, Wp, Device, Id, RepeatId, Identity, Config, Status, Page, Wait, Ecc, Cache, Bounds };
struct Result {
    esp_err_t error=ESP_OK,cleanupError=ESP_OK;
    Stage stage=Stage::Lock;
    uint8_t id[2]{},repeatId[2]{},protectionA0=0,configB0=0,statusC0=0;
    size_t bytesRead=0;
    bool consistent=false,configRead=false;
    // Identity verification does not authorize writes or filesystem mounting.
    bool exactModelVerified=false;
};
// Explicit debug invocation only. Caller owns OTA/radio/awake admission checks.
// No boot init, feature writes, reset, layer init or filesystem mount.
Result probe();
// Explicit read-only export: output is valid ONLY on error==cleanupError==ESP_OK.
// Maximum128 bytes, column0..2111(data+64 user spare), internal ECC required.
// The final64 ECC-parity bytes are deliberately outside this conservative API.
Result readChunk(uint32_t page,uint16_t column,uint8_t* output,size_t length);
}
