#include "boot_trace.h"
#include <nvs.h>
#include <nvs_flash.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <cstdio>

namespace {
struct Entry { uint32_t boot, reset, stage, elapsedMs; };
struct Trace { uint32_t version, count; Entry entries[4]; } trace{};
esp_err_t error = ESP_OK;
bool ready = false;
void save() {
    nvs_handle_t h;
    error = nvs_open("boot_trace", NVS_READWRITE, &h);
    if (error != ESP_OK) return;
    error = nvs_set_blob(h, "trace", &trace, sizeof(trace));
    if (error == ESP_OK) error = nvs_commit(h);
    nvs_close(h);
}
}
void BootTraceBegin() {
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
void BootTracePrint() {
    for (const auto& e : trace.entries)
        if (e.boot) std::printf("DBG BOOT boot=%lu reset=%lu stage=%lu elapsed_ms=%lu\r\n",
            static_cast<unsigned long>(e.boot), static_cast<unsigned long>(e.reset),
            static_cast<unsigned long>(e.stage), static_cast<unsigned long>(e.elapsedMs));
    std::printf("DBG RESULT command=boot status=%s count=%lu nvs_error=%d uptime_ms=%lld\r\n",
        ready && error == ESP_OK ? "PASS" : "FAIL", static_cast<unsigned long>(trace.count), error,
        static_cast<long long>(esp_timer_get_time()/1000));
}
