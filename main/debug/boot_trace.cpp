#include "boot_trace.h"
#include <nvs.h>
#include <nvs_flash.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <cstdio>
#include <array>
#include <atomic>

namespace {
struct Entry { uint32_t boot, reset, stage, elapsedMs; };
struct Trace { uint32_t version, count; Entry entries[4]; } trace{};
esp_err_t error = ESP_OK;
bool ready = false;
std::array<std::atomic<uint32_t>, static_cast<unsigned>(BootTiming::Count)> timing{};
constexpr const char* timingNames[]={"nvs","i2c","pmic","display","first_frame","gauge_start","gauge_end","ui_ready"};
void save() {
    nvs_handle_t h;
    error = nvs_open("boot_trace", NVS_READWRITE, &h);
    if (error != ESP_OK) return;
    error = nvs_set_blob(h, "trace", &trace, sizeof(trace));
    if (error == ESP_OK) error = nvs_commit(h);
    nvs_close(h);
}
}
void BootTimingMark(BootTiming point) {
    const auto index=static_cast<unsigned>(point);
    if(index<timing.size())timing[index].store(static_cast<uint32_t>(esp_timer_get_time()/1000)+1,std::memory_order_release);
}
void BootTraceBegin() {
    for(auto& value:timing)value.store(0,std::memory_order_relaxed);
    error = nvs_flash_init();
    if (error != ESP_OK) return; // Never erase user settings for diagnostics.
    nvs_handle_t h;
    if (nvs_open("boot_trace", NVS_READONLY, &h) == ESP_OK) {
        size_t size = sizeof(trace);
        if (nvs_get_blob(h, "trace", &trace, &size) != ESP_OK || size != sizeof(trace) || trace.version != 1)
            trace = {};
        nvs_close(h);
    }
    trace.version = 1;
    ++trace.count;
    ready = true;
    trace.entries[(trace.count - 1) % 4] = {trace.count, static_cast<uint32_t>(esp_reset_reason()), 1, 0};
    BootTraceStage(1);
}
void BootTraceStage(uint32_t stage) {
    if (!ready) return;
    auto& e = trace.entries[(trace.count - 1) % 4];
    e.stage = stage;
    e.elapsedMs = static_cast<uint32_t>(esp_timer_get_time() / 1000);
    save();
}
uint32_t BootTraceCount() { return trace.count; }
void BootTracePrint(BootTraceWriter writer) {
    if (!writer) writer = std::printf;
    for(unsigned i=0;i<timing.size();++i) {
        const auto ms=timing[i].load(std::memory_order_acquire);
        if(ms)writer("DBG BOOT_TIMING point=%s elapsed_ms=%lu current_boot_ram_only=1\r\n",timingNames[i],static_cast<unsigned long>(ms-1));
    }
    for (const auto& e : trace.entries)
        if (e.boot) writer("DBG BOOT boot=%lu reset=%lu stage=%lu elapsed_ms=%lu\r\n",
            static_cast<unsigned long>(e.boot), static_cast<unsigned long>(e.reset),
            static_cast<unsigned long>(e.stage), static_cast<unsigned long>(e.elapsedMs));
    writer("DBG RESULT command=boot status=%s count=%lu nvs_error=%d uptime_ms=%lld\r\n",
        ready && error == ESP_OK ? "PASS" : "FAIL", static_cast<unsigned long>(trace.count), error,
        static_cast<long long>(esp_timer_get_time()/1000));
}
