#pragma once
#include <sdkconfig.h>
#include <cstddef>
#include <cstdint>
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31 && CONFIG_IDF_TARGET_ARCH_RISCV
// Normal context only; the RTC record survives queries and is replaced on panic.
void MosaicoPanicReport();
bool MosaicoPanicStatus(char* out, size_t length);
// Numeric phase/offset only, without content or credentials.
void MosaicoOtaBreadcrumb(uint32_t phase, uint32_t offset);
#endif
