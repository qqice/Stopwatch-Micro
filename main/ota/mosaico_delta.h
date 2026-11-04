#pragma once
#include "mosaico_ota.h"
namespace MosaicoDelta {
constexpr uint32_t MaxPatchSize=2U*1024U*1024U;
struct Plan { uint32_t size=0; uint8_t hash[32]{}; char sha256[65]{}; };
// Invalid, full, unknown, disabled and unsupported responses all select full OTA.
bool parsePlan(const char* json,size_t length,const MosaicoOta::VerifiedImageDescriptor& base,
               const MosaicoOta::VerifiedImageDescriptor& target,Plan& out);
bool decodeChunk(const char* json,size_t length,uint32_t offset,uint32_t total,
                 uint8_t* destination,size_t capacity,size_t& count);
void hashHex(const uint8_t hash[32],char out[65]);
bool patchHashMatches(const uint8_t* patch,const Plan& plan);
// Uses official esp_delta_ota; output is copied into the boot-reserved INTERNAL
// chunk before writeChunk. Caller has already called beginManifest on signed target.
bool reconstruct(const uint8_t* patch,const Plan& plan,
                 const MosaicoOta::VerifiedImageDescriptor& base,
                 const MosaicoOta::VerifiedImageDescriptor& target,
                 uint8_t* internalChunk,size_t capacity,int64_t deadlineUs);
}
