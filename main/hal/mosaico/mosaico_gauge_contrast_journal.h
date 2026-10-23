#pragma once
#include "mosaico_gauge_contrast_journal_model.h"
namespace mosaico_gauge_contrast_journal {
inline constexpr const char* nvsNamespace="gcontrast";
inline constexpr const char* nvsSlotKeys[2]={"keyc0","keyc1"};
#ifdef ESP_PLATFORM
Status loadNvs(const std::array<uint8_t,6>& expectedMac,Workspace& work);
Status saveNvs(const Record& next,const Admission& admission,Workspace& work);
#endif
} // namespace mosaico_gauge_contrast_journal
