#pragma once
#include <cstddef>
#include <cstdint>

// One boot-lifetime reservation. Startup prepares; the existing network owner
// alone acquires. This is not a general allocator, NAND scratchpad or lease.
namespace MosaicoOtaBuffer {
inline constexpr size_t ChunkCapacity=4096, BodyCapacity=8192;
enum class Kind:uint8_t {Chunk, Body};
enum class Error:uint8_t {Unprepared, None, ChunkAllocation, BodyAllocation, ChunkMemory, BodyMemory};
constexpr const char* errorName(Error error){
    return error==Error::None?"ready":error==Error::ChunkAllocation?"chunk_allocation":
        error==Error::BodyAllocation?"body_allocation":error==Error::ChunkMemory?"chunk_memory":
        error==Error::BodyMemory?"body_memory":"unprepared";
}
struct View {uint8_t* chunk=nullptr;uint8_t* body=nullptr;constexpr explicit operator bool()const{return chunk&&body;}};
class Reservation {
    View buffers{};
    bool attempted=false,ready=false;
    Error lastError=Error::Unprepared;
public:
    constexpr Error error()const{return lastError;}
    // Exactly one attempt, including after failure; never retry under radio load.
    template<class Allocator> constexpr bool prepare(Allocator& allocator){
        if(attempted)return ready;
        attempted=true;
        View fresh{};
        fresh.chunk=allocator.allocate(Kind::Chunk,ChunkCapacity);
        if(!fresh.chunk){lastError=Error::ChunkAllocation;return false;}
        fresh.body=allocator.allocate(Kind::Body,BodyCapacity);
        if(!fresh.body)lastError=Error::BodyAllocation;
        else if(!allocator.valid(Kind::Chunk,fresh.chunk,ChunkCapacity))lastError=Error::ChunkMemory;
        else if(!allocator.valid(Kind::Body,fresh.body,BodyCapacity))lastError=Error::BodyMemory;
        else {buffers=fresh;ready=true;lastError=Error::None;return true;}
        if(fresh.body)allocator.release(fresh.body);
        allocator.release(fresh.chunk);
        return false; // No partially prepared pointer is ever published.
    }
    // Allocation-free; recheck complete ranges before every transfer/erase.
    template<class Allocator> constexpr View acquire(Allocator& allocator){
        if(!ready)return {};
        if(!allocator.valid(Kind::Chunk,buffers.chunk,ChunkCapacity))lastError=Error::ChunkMemory;
        else if(!allocator.valid(Kind::Body,buffers.body,BodyCapacity))lastError=Error::BodyMemory;
        else return buffers;
        ready=false;return {};
    }
};
} // namespace MosaicoOtaBuffer
