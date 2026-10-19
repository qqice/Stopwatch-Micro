#pragma once
#include <cstddef>
#include <cstdint>
namespace MosaicoSystemLog {
enum class ReadStatus : uint8_t { Idle, Pending, Ready, Failed };
struct ChunkMetadata {
 uint64_t seq=0; uint16_t index=0,bytes=0,totalBytes=2048;
 uint32_t pageCrc=0,chunkCrc=0; int32_t error=0; uint32_t logCode=0;
};
// begin is RAM only, safe before HAL.init. service releases worker after HAL.init.
void begin(uint32_t bootId);
void service();
size_t startupInternalReservation(); // Verified resident RTC stack only, <=8192.
bool requestFlush();
void status(char* out,size_t length);
bool requestRead(uint64_t seq);
ReadStatus readStatus();
// Ready cache stays immutable until the next accepted requestRead; no NAND here.
bool readChunk(uint64_t seq,uint16_t index,uint8_t* out,size_t capacity,ChunkMetadata& metadata);
}
