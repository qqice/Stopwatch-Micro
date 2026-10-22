// SPDX-License-Identifier: MIT
// Quarantine only the current ELF's instruction alignment gap. This does not
// establish a root cause for the observed PSRAM read exception.
#include "mosaico_psram_heap_policy.h"
#include <sdkconfig.h>
#include <esp_attr.h>
#include <esp_heap_caps.h>
#include <esp_heap_caps_init.h>
#include <heap_memory_layout.h>
#include <soc/soc_caps.h>
#if !defined(MOSAICO_BOARD) || !CONFIG_IDF_TARGET_ESP32S31 || !CONFIG_IDF_TARGET_ARCH_RISCV || !SOC_MMU_DI_VADDR_SHARED || !CONFIG_SPIRAM_FETCH_INSTRUCTIONS || !CONFIG_SPIRAM_RODATA || !CONFIG_SPIRAM_XIP_FROM_PSRAM || !CONFIG_SPIRAM_PRE_CONFIGURE_MEMORY_PROTECTION
#error "Instruction-gap quarantine requires the reviewed Mosaico S31 shared-D/I XIP configuration"
#endif
static_assert(CONFIG_MMU_PAGE_SIZE == 65536 && SOC_MEMORY_TYPE_NO_PRIOS == 3);
static_assert(CONFIG_SPIRAM_SPEED == 200);
static_assert(sizeof(intptr_t) == 4 && __atomic_always_lock_free(4, nullptr));
extern "C" uint8_t _instruction_reserved_end;
extern "C" uint8_t _rodata_reserved_end;
extern "C" esp_err_t __real_heap_caps_add_region_with_caps(const uint32_t[], intptr_t, intptr_t);
namespace {
enum : uint32_t { Geometry=1, Caps=2, Duplicate=4, Forward=8, Overflow=16, Contention=32 };
// Monotonic atomics, never reset. No spin lock (queries cannot block startup).
DRAM_ATTR uint32_t skipped, forwarded, failed, faults, recordCount, completed;
struct Record { MosaicoPsramHeapPolicyRecord value; uint32_t ready; };
DRAM_ATTR Record records[MosaicoPsramHeapPolicyRecordCapacity];
uint32_t IRAM_ATTR load(const uint32_t* p) { return __atomic_load_n(p, __ATOMIC_SEQ_CST); }
void IRAM_ATTR fault(uint32_t value) { __atomic_fetch_or(&faults,value,__ATOMIC_SEQ_CST); }
// Counts describe recorded registrations only. A racing final reservation
// may exceed Capacity, but that is sticky-faulted and never indexes records.
void IRAM_ATTR count(uint32_t* p) {
    if(load(p)>=MosaicoPsramHeapPolicyRecordCapacity) {fault(Overflow);return;}
    if(__atomic_fetch_add(p,1,__ATOMIC_SEQ_CST)>=MosaicoPsramHeapPolicyRecordCapacity)fault(Overflow);
}
bool IRAM_ATTR reserve(uint32_t& index) {
    if(load(&recordCount)>=MosaicoPsramHeapPolicyRecordCapacity) {fault(Overflow);return false;}
    index=__atomic_fetch_add(&recordCount,1,__ATOMIC_SEQ_CST);
    if(index>=MosaicoPsramHeapPolicyRecordCapacity) {fault(Overflow);return false;}
    return true;
}
void IRAM_ATTR record(uint32_t index,const uint32_t* caps,intptr_t start,intptr_t end,uint32_t action,esp_err_t result) {
    auto& r=records[index];
    r.value.start=static_cast<uint32_t>(start);r.value.end=static_cast<uint32_t>(end);
    for(unsigned i=0;i<3;++i)r.value.caps[i]=caps?caps[i]:0;
    r.value.action=action;r.value.result=result;
    __atomic_store_n(&r.ready,1,__ATOMIC_RELEASE);
    count(&completed);
}
bool IRAM_ATTR geometry(uint32_t& start,uint32_t& end) {
    const uint32_t address=static_cast<uint32_t>(reinterpret_cast<uintptr_t>(&_instruction_reserved_end));
    if (!address || address>UINT32_MAX-65535u) {start=end=0;return false;}
    start=(address+3u)&~3u;end=(address+65535u)&~65535u;
    return start<end;
}
}
extern "C" esp_err_t IRAM_ATTR __wrap_heap_caps_add_region_with_caps(const uint32_t caps[],intptr_t start,intptr_t end) {
    uint32_t index;
    const bool reserved=reserve(index);
    uint32_t wantedStart,wantedEnd;
    if (!geometry(wantedStart,wantedEnd)) {fault(Geometry);if(reserved)record(index,caps,start,end,2,ESP_ERR_INVALID_STATE);return ESP_ERR_INVALID_STATE;}
    if (static_cast<uint32_t>(start)==wantedStart && static_cast<uint32_t>(end)==wantedEnd) {
        if(!reserved)return ESP_ERR_INVALID_STATE;
        if (!caps || caps[0]!=(MALLOC_CAP_SPIRAM|MALLOC_CAP_DEFAULT) || caps[1]!=0 ||
            caps[2]!=(MALLOC_CAP_8BIT|MALLOC_CAP_32BIT|MALLOC_CAP_SIMD)) {
            fault(Caps);record(index,caps,start,end,2,ESP_ERR_INVALID_ARG);return ESP_ERR_INVALID_ARG;
        }
        // Single AMOSWAP: duplicate detection needs no LR/SC retry loop.
        if (__atomic_exchange_n(&skipped,1,__ATOMIC_SEQ_CST)!=0) {
            fault(Duplicate);record(index,caps,start,end,2,ESP_ERR_INVALID_STATE);return ESP_ERR_INVALID_STATE;
        }
        record(index,caps,start,end,1,ESP_OK);return ESP_OK;
    }
    if(reserved)count(&forwarded);
    const esp_err_t result=__real_heap_caps_add_region_with_caps(caps,start,end);
    if (result!=ESP_OK) {if(reserved)count(&failed);fault(Forward);}
    if(reserved)record(index,caps,start,end,0,result);
    return result;
}
void MosaicoPsramHeapPolicySnapshot(MosaicoPsramHeapPolicyDiagnostic& out) {
    // Double collect of monotonic saturating counters: an equal pair is a
    // coherent snapshot. No lock, target-memory read, allocator or SDK call.
    uint32_t a,b,c,d,e,f;
    out.coherent=false;
    for(unsigned attempt=0;attempt<8;++attempt) {
        a=load(&skipped);b=load(&forwarded);c=load(&failed);d=load(&faults);e=load(&recordCount);f=load(&completed);
        out.skipped=load(&skipped);out.forwarded=load(&forwarded);
        out.forwardFailures=load(&failed);out.faults=load(&faults);
        out.reserved=load(&recordCount);out.completed=load(&completed);
        if(a==out.skipped&&b==out.forwarded&&c==out.forwardFailures&&d==out.faults&&e==out.reserved&&f==out.completed) {out.coherent=true;break;}
    }
    const bool valid=geometry(out.start,out.end);
    out.bytes=valid?out.end-out.start:0;
    const uint32_t rodata=static_cast<uint32_t>(reinterpret_cast<uintptr_t>(&_rodata_reserved_end));
    out.rodataStart=out.rodataEnd=0;
    if(rodata && rodata<=UINT32_MAX-65535u) {out.rodataStart=(rodata+3u)&~3u;out.rodataEnd=(rodata+65535u)&~65535u;}
    out.healthy=out.coherent && valid && out.skipped==1 && !out.faults && !out.forwardFailures && out.reserved==out.completed && out.reserved<=MosaicoPsramHeapPolicyRecordCapacity;
}
bool MosaicoPsramHeapPolicyHealthy() {
    MosaicoPsramHeapPolicyDiagnostic snapshot;
    MosaicoPsramHeapPolicySnapshot(snapshot);
    return snapshot.healthy;
}
bool MosaicoPsramHeapPolicyReadRecord(uint32_t index,MosaicoPsramHeapPolicyRecord& out) {
    if(index>=MosaicoPsramHeapPolicyRecordCapacity || !__atomic_load_n(&records[index].ready,__ATOMIC_ACQUIRE))return false;
    out=records[index].value;return true;
}
