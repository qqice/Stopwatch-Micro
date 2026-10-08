#include "panic_capture.h"
#include <sdkconfig.h>
#if !defined(MOSAICO_BOARD) || !CONFIG_IDF_TARGET_ESP32S31 || !CONFIG_IDF_TARGET_ARCH_RISCV
#error "Mosaico panic capture requires the ESP32-S31 main RISC-V app"
#endif
#include <esp_attr.h>
#include <esp_system.h>
#include <esp_private/panic_internal.h>
#include <riscv/rvruntime-frames.h>
#include <cstdint>
#include <cstdio>

namespace {
constexpr uint32_t LegacyCaptureMagic = 0x504e4332;
constexpr uint32_t CaptureMagic = 0x504e4333;
struct PanicCapture {
    uint32_t magic;
    int32_t core;
    uint32_t cause, addr, pc, ra, sp, mcause, mtval;
    uint32_t otaPhase, otaOffset;
    // Append-only extension; no stack or payload contents are copied.
    uint32_t registerMagic, a0, a1, a2, a3, a4, a5, a6, a7, framePointer;
};
constexpr uint32_t RegisterMagic = 0x52474331;
// No initializer: the linker/startup must leave this RTC NOLOAD record intact.
RTC_NOINIT_ATTR volatile PanicCapture capture;
DRAM_ATTR volatile uint32_t otaPhase = 0, otaOffset = 0;
}

void IRAM_ATTR MosaicoOtaBreadcrumb(uint32_t phase, uint32_t offset)
{
    otaPhase = 0;
    otaOffset = offset;
    __asm__ volatile("fence w,w" ::: "memory");
    otaPhase = phase;
}

extern "C" void __real_esp_panic_handler(panic_info_t* info);
extern "C" void IRAM_ATTR __wrap_esp_panic_handler(panic_info_t* info)
{
    capture.magic = 0;
    capture.registerMagic = 0;
    const auto* frame = info ? static_cast<const RvExcFrame*>(info->frame) : nullptr;
    capture.core = info ? info->core : -1;
    capture.cause = info ? static_cast<uint32_t>(info->exception) : UINT32_MAX;
    capture.addr = info ? reinterpret_cast<uintptr_t>(info->addr) : 0;
    capture.pc = frame ? frame->mepc : 0;
    capture.ra = frame ? frame->ra : 0;
    capture.sp = frame ? frame->sp : 0;
    capture.mcause = frame ? frame->mcause : 0;
    capture.mtval = frame ? frame->mtval : 0;
    capture.otaPhase = otaPhase;
    capture.otaOffset = otaOffset;
    capture.a0 = frame ? frame->a0 : 0;
    capture.a1 = frame ? frame->a1 : 0;
    capture.a2 = frame ? frame->a2 : 0;
    capture.a3 = frame ? frame->a3 : 0;
    capture.a4 = frame ? frame->a4 : 0;
    capture.a5 = frame ? frame->a5 : 0;
    capture.a6 = frame ? frame->a6 : 0;
    capture.a7 = frame ? frame->a7 : 0;
    capture.framePointer = frame ? frame->s0 : 0;
    capture.registerMagic = RegisterMagic;
    __asm__ volatile("fence w,w" ::: "memory");
    capture.magic = CaptureMagic;
    __real_esp_panic_handler(info); // Preserve the SDK's complete panic handling.
}

bool MosaicoPanicStatus(char* out, size_t length)
{
    if (!out || !length) return false;
    if (capture.magic != CaptureMagic && capture.magic != LegacyCaptureMagic) {
        std::snprintf(out, length, "reason=no_saved_panic");
        return false;
    }
    const int used = std::snprintf(out, length, "valid=1 arch=riscv current_reset=%d core=%ld cause=%lu addr=0x%08lx pc=0x%08lx ra=0x%08lx sp=0x%08lx mcause=0x%08lx mtval=0x%08lx ota_phase=%lu ota_offset=%lu",
        static_cast<int>(esp_reset_reason()), static_cast<long>(capture.core),
        static_cast<unsigned long>(capture.cause), static_cast<unsigned long>(capture.addr),
        static_cast<unsigned long>(capture.pc), static_cast<unsigned long>(capture.ra),
        static_cast<unsigned long>(capture.sp), static_cast<unsigned long>(capture.mcause),
        static_cast<unsigned long>(capture.mtval), static_cast<unsigned long>(capture.otaPhase),
        static_cast<unsigned long>(capture.otaOffset));
    if (used > 0 && static_cast<size_t>(used) < length && capture.magic == CaptureMagic && capture.registerMagic == RegisterMagic)
        std::snprintf(out + used, length - used, " regs_valid=1 a0=%08lx a1=%08lx a2=%08lx a3=%08lx a4=%08lx a5=%08lx a6=%08lx a7=%08lx fp=%08lx stack_copy=0",
            static_cast<unsigned long>(capture.a0), static_cast<unsigned long>(capture.a1),
            static_cast<unsigned long>(capture.a2), static_cast<unsigned long>(capture.a3),
            static_cast<unsigned long>(capture.a4), static_cast<unsigned long>(capture.a5),
            static_cast<unsigned long>(capture.a6), static_cast<unsigned long>(capture.a7),
            static_cast<unsigned long>(capture.framePointer));
    else if (used > 0 && static_cast<size_t>(used) < length)
        std::snprintf(out + used, length - used, " regs_valid=0 legacy_record=1");
    return true;
}

void MosaicoPanicReport()
{
    if (esp_reset_reason() == ESP_RST_PANIC) {
        char details[768];
        if (MosaicoPanicStatus(details, sizeof(details))) {
            std::printf("DBG PANIC_RTC %s\r\n", details);
            std::fflush(stdout);
        }
    }
    // Retain the record for a later USB query, including across non-panic resets.
}
