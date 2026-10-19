#pragma once
#include <cstdint>
void BootTraceBegin();
uint32_t BootTraceCount(); // RAM-only current session identity, after Begin.
void BootTraceStage(uint32_t stage);
using BootTraceWriter = int (*)(const char*, ...);
void BootTracePrint(BootTraceWriter writer = nullptr);

// Current-boot RAM timings only; no extra flash writes.
enum class BootTiming : uint8_t { Nvs, I2c, Pmic, Display, FirstFrame, GaugeStart, GaugeEnd, UiReady, Count };
void BootTimingMark(BootTiming point);
