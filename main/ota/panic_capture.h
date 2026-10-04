#pragma once
#include <sdkconfig.h>
#include <cstddef>
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31 && CONFIG_IDF_TARGET_ARCH_RISCV
// Normal context only; the RTC record survives queries and is replaced on panic.
void MosaicoPanicReport();
bool MosaicoPanicStatus(char* out, size_t length);
#endif