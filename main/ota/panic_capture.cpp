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
constexpr uint32_t CaptureMagic = 0x504e4331;
struct PanicCapture {
    uint32_t magic;
    int32_t core;
    uint32_t cause, addr, pc, ra, sp, mcause, mtval;
};
// No initializer: the linker/startup must leave this RTC NOLOAD record intact.
RTC_NOINIT_ATTR volatile PanicCapture capture;
}

extern "C" void __real_esp_panic_handler(panic_info_t* info);
extern "C" void IRAM_ATTR __wrap_esp_panic_handler(panic_info_t* info)
{
    capture.magic = 0;
    const auto* frame = info ? static_cast<const RvExcFrame*>(info->frame) : nullptr;
    capture.core = info ? info->core : -1;
    capture.cause = info ? static_cast<uint32_t>(info->exception) : UINT32_MAX;
    capture.addr = info ? reinterpret_cast<uintptr_t>(info->addr) : 0;
    capture.pc = frame ? frame->mepc : 0;
    capture.ra = frame ? frame->ra : 0;
    capture.sp = frame ? frame->sp : 0;
    capture.mcause = frame ? frame->mcause : 0;
    capture.mtval = frame ? frame->mtval : 0;
    __asm__ volatile("fence w,w" ::: "memory");
    capture.magic = CaptureMagic;
    __real_esp_panic_handler(info); // Preserve the SDK's complete panic handling.
}

bool MosaicoPanicStatus(char* out, size_t length)
{
    if (!out || !length) return false;
    if (capture.magic != CaptureMagic) {
        std::snprintf(out, length, "reason=no_saved_panic");
        return false;
    }
    std::snprintf(out, length, "valid=1 arch=riscv current_reset=%d core=%ld cause=%lu addr=0x%08lx pc=0x%08lx ra=0x%08lx sp=0x%08lx mcause=0x%08lx mtval=0x%08lx",
        static_cast<int>(esp_reset_reason()), static_cast<long>(capture.core),
        static_cast<unsigned long>(capture.cause), static_cast<unsigned long>(capture.addr),
        static_cast<unsigned long>(capture.pc), static_cast<unsigned long>(capture.ra),
        static_cast<unsigned long>(capture.sp), static_cast<unsigned long>(capture.mcause),
        static_cast<unsigned long>(capture.mtval));
    return true;
}

void MosaicoPanicReport()
{
    if (esp_reset_reason() == ESP_RST_PANIC) {
        char details[256];
        if (MosaicoPanicStatus(details, sizeof(details))) {
            std::printf("DBG PANIC_RTC %s\r\n", details);
            std::fflush(stdout);
        }
    }
    // Retain the record for a later USB query, including across non-panic resets.
}
