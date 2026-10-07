#pragma once
#include <cstdint>
void BootTraceBegin();
void BootTraceStage(uint32_t stage);
using BootTraceWriter = int (*)(const char*, ...);
void BootTracePrint(BootTraceWriter writer = nullptr);
