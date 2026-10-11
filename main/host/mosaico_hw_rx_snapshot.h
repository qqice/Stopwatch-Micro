// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
namespace MosaicoHwRx {
// Whitelisted raw register counts only. No payload, opaque SDK bytes or writes.
// Container widths are not hardware counter widths: power-drop wraps at 10 bits,
// FCS at 16 bits. Host deltas must account for wrap; a read is not atomic hardware capture.
struct Snapshot {
    uint64_t requestId = 0, processedId = 0, sampleSequence = 0;
    int64_t beforeUs = 0, afterUs = 0;
    int error = 0;
    bool pending = false, valid = false, getterCalled = false;
    uint16_t rx_fcs_err = 0;
    uint16_t rx_abort = 0;
    uint16_t rx_abort_fcs_pass = 0;
    uint16_t nrx_err_pwrdrop = 0;
    uint16_t rx_mpdu = 0;
    uint16_t rx_end_cnt = 0;
    uint16_t rx_datasuc = 0;
    uint16_t rx_buf_fullcnt = 0;
    uint16_t rx_fifo_ovfcnt = 0;
    uint16_t rx_tkip_errcnt = 0;
    uint16_t rx_lastunmatch_err = 0;
    uint16_t nrx_err_bboff = 0;
    uint16_t nrx_err_agcexit = 0;
    uint16_t rxhung_statis = 0;
    uint16_t txhung_statis = 0;
    uint32_t rxtxhung = 0;
    uint32_t rxtxpanic = 0;
};
}
