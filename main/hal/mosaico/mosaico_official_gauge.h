#pragma once
#include "driver/i2c_master.h"
struct MosaicoOfficialGaugeResult {
    bool success;
    bool exitAttempted;
    bool sealAttempted;
};
// Caller owns device and holds transaction/battery locks. Attempt evidence is
// RAM-only and set BEFORE transport, so unknown ACK never authorizes a retry.
MosaicoOfficialGaugeResult mosaico_official_gauge_apply(i2c_master_dev_handle_t device, uint16_t capacity = 65);
