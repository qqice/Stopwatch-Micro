/* SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 * SPDX-License-Identifier: Apache-2.0
 * Board values copied from ESP-Mosaico setup_battery_monitor.c (see PROVENANCE).
 * Previous values: public131 audit plus independently confirmed Near Full=5.
 * Both profiles change only the same 26 official fields, not gain/protection.
 */
#include "mosaico_official_gauge.h"
#include "vendor/bq27220/bq27220.h"
extern "C" void mosaico_bq_reset_attempts(void);
extern "C" unsigned mosaico_bq_get_attempts(void);
#ifndef MOSAICO_GAUGE_USE_PREVIOUS_PROFILE
#define MOSAICO_GAUGE_USE_PREVIOUS_PROFILE 0
#endif
MosaicoOfficialGaugeResult mosaico_official_gauge_apply(i2c_master_dev_handle_t device)
{
#if MOSAICO_GAUGE_USE_PREVIOUS_PROFILE
    static const parameter_cedv_t cedv = {
        .full_charge_cap = 65,
        .design_cap = 65,
        .reserve_cap = 0,
        .near_full = 5,
        .self_discharge_rate = 20,
        .EDV0 = 3031,
        .EDV1 = 3385,
        .EDV2 = 3501,
        .EMF = 3743,
        .C0 = 149,
        .R0 = 867,
        .T0 = 4030,
        .R1 = 316,
        .TC = 9,
        .C1 = 0,
        .DOD0 = 4173,
        .DOD10 = 4043,
        .DOD20 = 3925,
        .DOD30 = 3821,
        .DOD40 = 3725,
        .DOD50 = 3656,
        .DOD60 = 3619,
        .DOD70 = 3582,
        .DOD80 = 3515,
        .DOD90 = 3439,
        .DOD100 = 2713,
    };
    static const gauging_config_t cfg = {.full = 0x102a};
#else
    static const parameter_cedv_t cedv = {
        .full_charge_cap = 65,
        .design_cap = 65,
        .reserve_cap = 0,
        .near_full = 5,
        .self_discharge_rate = 20,
        .EDV0 = 3000,
        .EDV1 = 3410,
        .EDV2 = 3530,
        .EMF = 3670,
        .C0 = 115,
        .R0 = 968,
        .T0 = 4547,
        .R1 = 4764,
        .TC = 11,
        .C1 = 0,
        .DOD0 = 4147,
        .DOD10 = 4002,
        .DOD20 = 3969,
        .DOD30 = 3938,
        .DOD40 = 3880,
        .DOD50 = 3824,
        .DOD60 = 3794,
        .DOD70 = 3753,
        .DOD80 = 3677,
        .DOD90 = 3574,
        .DOD100 = 3490,
    };
    static const gauging_config_t cfg = {
        .CCT = 1,
        .CSYNC = 0,
        .RSVD0 = 0,
        .EDV_CMP = 0,
        .SC = 1,
        .FIXED_EDV0 = 0,
        .RSVD1 = 0,
        .FCC_LIM = 1,
        .RSVD2 = 0,
        .FC_FOR_VDQ = 1,
        .IGNORE_SD = 1,
        .SME0 = 0,
        .RSVD3 = 0,
    };
#endif
    // Non-owning device token, NOT an actual managed bus. Wrapper intercepts all use.
    const bq27220_config_t config = {static_cast<void*>(device), &cfg, &cedv};
    mosaico_bq_reset_attempts();
    bq27220_handle_t handle = bq27220_create(&config);
    const unsigned attempts = mosaico_bq_get_attempts();
    const bool success = handle && bq27220_delete(handle) == ESP_OK;
    return {success, (attempts & 1) != 0, (attempts & 2) != 0};
}
