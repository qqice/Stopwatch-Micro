#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace MosaicoNandDma {
inline constexpr size_t Capacity=2112, Alignment=64;
enum class Error:uint8_t {Unprepared, None, Allocation, Memory, Request, Busy, Ownership};
constexpr const char* errorName(Error e){
    return e==Error::None?"ready":e==Error::Allocation?"allocation":
        e==Error::Memory?"memory":e==Error::Request?"request":
        e==Error::Busy?"busy":e==Error::Ownership?"ownership":"unprepared";
}
// Default atomics are real std::atomic. The injectable template permits a
// constexpr sequential policy fixture without pretending to execute target code.
template<template<class> class Atomic=std::atomic> class Reservation {
    Atomic<bool> attempted{false},ready{false},leased{false},faulted{false};
    Atomic<Error> lastError{Error::Unprepared};
    uint8_t* buffer=nullptr; // Published once by ready.store(release); never freed.
public:
    constexpr bool wasAttempted()const{return attempted.load(std::memory_order_acquire);}
    constexpr bool isReady()const{return ready.load(std::memory_order_acquire)&&!faulted.load(std::memory_order_acquire);}
    constexpr bool isLeased()const{return leased.load(std::memory_order_acquire);}
    constexpr bool isFaulted()const{return faulted.load(std::memory_order_acquire);}
    constexpr Error error()const{return lastError.load(std::memory_order_acquire);}
    static constexpr bool validRequest(size_t size,size_t alignment){
        return size&&size<=Capacity&&alignment&&alignment<=Alignment&&!(alignment&(alignment-1));
    }
    template<class Allocator> constexpr bool prepare(Allocator& allocator){
        if(attempted.exchange(true,std::memory_order_acq_rel))return isReady();
        if(faulted.load(std::memory_order_acquire))return false;
        auto* fresh=allocator.allocate(Alignment,Capacity);
        if(!fresh){
            faulted.store(true,std::memory_order_release);
            lastError.store(Error::Allocation,std::memory_order_release);return false;
        }
        if(!allocator.valid(fresh,Capacity,Alignment)){
            allocator.release(fresh); // Only an invalid, never-published candidate.
            faulted.store(true,std::memory_order_release);
            lastError.store(Error::Memory,std::memory_order_release);return false;
        }
        buffer=fresh;lastError.store(Error::None,std::memory_order_release);
        ready.store(true,std::memory_order_release);return true;
    }
    template<class Allocator> constexpr uint8_t* acquire(size_t size,size_t alignment,Allocator& allocator){
        if(!isReady())return nullptr;
        if(!validRequest(size,alignment)){lastError.store(Error::Request,std::memory_order_release);return nullptr;}
        bool expected=false;
        if(!leased.compare_exchange_strong(expected,true,std::memory_order_acq_rel)){
            lastError.store(Error::Busy,std::memory_order_release);return nullptr;
        }
        if(faulted.load(std::memory_order_acquire)||!allocator.valid(buffer,Capacity,Alignment)){
            faulted.store(true,std::memory_order_release);
            lastError.store(Error::Memory,std::memory_order_release);return nullptr;
        }
        lastError.store(Error::None,std::memory_order_release);return buffer;
    }
    constexpr bool release(void* pointer){
        if(!pointer)return true;
        if(!ready.load(std::memory_order_acquire)||pointer!=buffer){
            faulted.store(true,std::memory_order_release);
            lastError.store(Error::Ownership,std::memory_order_release);return false;
        }
        bool expected=true;
        if(!leased.compare_exchange_strong(expected,false,std::memory_order_acq_rel)){
            faulted.store(true,std::memory_order_release);
            lastError.store(Error::Ownership,std::memory_order_release);return false;
        }
        return true; // Never free, resize, reset, or share the boot reservation.
    }
};
} // namespace MosaicoNandDma
