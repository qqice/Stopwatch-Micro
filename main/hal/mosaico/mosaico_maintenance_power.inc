// Nonblocking raw read only. In particular, do not call scale_boot_fence_locked:
// its recovery branch may perform a gauge transaction, or batteryPolicyTelemetry
// which deliberately rejects an uncalibrated 3000mAh capacity model.
#include "mosaico_maintenance_power_model.h"
#include <atomic>
namespace {
std::atomic<const char*> maintenance_power_reason{"not_sampled"};
bool maintenance_power_reject(const char* reason) {
    maintenance_power_reason.store(reason,std::memory_order_release);
    return false;
}
}
bool Hal::maintenancePowerStatus(char* out,size_t size) const {
    if(!out||!size)return false;
    const int count=std::snprintf(out,size,"reason=%s cached_only=1 no_i2c=1 no_power_proof=1",
        maintenance_power_reason.load(std::memory_order_acquire));
    return count>=0&&size_t(count)<size;
}
bool Hal::tryMaintenancePowerSnapshot(MaintenancePowerSnapshot& out, MaintenancePowerResult* result) {
    if (result) *result = MaintenancePowerResult::Rejected;
    out = {};
    std::unique_lock<std::recursive_mutex> transaction(gauge_transaction_mutex, std::try_to_lock);
    if (!transaction.owns_lock()) {
        if (result) *result = MaintenancePowerResult::Busy;
        return maintenance_power_reject("gauge_transaction_busy");
    }
    std::unique_lock<std::mutex> battery(battery_mutex, std::try_to_lock);
    if (!battery.owns_lock()) {
        if (result) *result = MaintenancePowerResult::Busy;
        return maintenance_power_reject("battery_read_busy");
    }
    if (!gauge) return maintenance_power_reject("no_gauge");
    if (scale_mutation_in_progress || scale_safety_mismatch_locked() || !scale_runtime ||
        !scale_runtime->loaded) return maintenance_power_reject("scale_or_calibration_not_safe");
    // The boot fence already populated this cache. Never initiate/replay a
    // restore, or mutate the ledger, from an OTA admission read.
    if (scale_load_status == scale_journal::Status::Ready) {
        const auto& loaded = scale_runtime->journal.loaded;
        if (loaded.selected < 0 || loaded.selected > 1 ||
            loaded.records[size_t(loaded.selected)].snapshot.phase != scale_recovery::Phase::Original)
            return maintenance_power_reject("scale_recovery_required");
    } else if (scale_load_status != scale_journal::Status::Missing ||
               scale_runtime->journalEverSeen) return maintenance_power_reject("scale_ledger_not_safe");
    if (gauge_boot_info.status == GaugeBootReloadStatus::Deferred)
        return maintenance_power_reject("boot_gauge_not_checked");
    if (gauge_boot_info.status == GaugeBootReloadStatus::Critical) {
        constexpr char allowed[] = "boot_critical_failed_no_retry";
        if (gauge_boot_info.attempted ||
            std::strncmp(gauge_boot_info.reason, allowed, sizeof(allowed) - 1))
            return maintenance_power_reject("critical_not_known_historical_failure");
        // Allow only the already-inspected historical failure, not unknown key
        // failures, pending/corrupt journals or scaling recovery errors.
        native_recovery::Evidence evidence{};
        const auto mac = getFactoryMac();
        if (!native_evidence_locked(evidence) || !native_recovery::exact(evidence, mac.data()))
            return maintenance_power_reject("historical_failure_evidence_mismatch");
    }
    const int64_t started = esp_timer_get_time();
    if (started <= 0) return maintenance_power_reject("clock_not_positive");
    MaintenancePowerSnapshot sample{};
    uint16_t current = 0, average = 0, finalOp = 0, finalDc = 0, finalFcc = 0;
    bool ok = gauge_word(0x3a, sample.operationStatus);
    ok &= gauge_word(0x08, sample.voltageMv);
    ok &= gauge_word(0x06, sample.temperatureDeciK);
    ok &= gauge_word(0x3c, sample.designMah);
    ok &= gauge_word(0x12, sample.fullMah);
    ok &= gauge_word(0x0c, current);
    ok &= gauge_word(0x14, average);
    sample.currentMa = static_cast<int16_t>(current);
    sample.averageCurrentMa = static_cast<int16_t>(average);
    GaugeEngineSnapshot identity{};
    gauge_engine_locked(identity, mosaico_gauge_status::Explicit, 0);
    constexpr auto required = mosaico_gauge_engine::Identity | mosaico_gauge_engine::DefinitionMatch;
    ok &= (identity.validMask & required) == required;
    ok &= gauge_word(0x3a, finalOp);
    ok &= gauge_word(0x3c, finalDc);
    ok &= gauge_word(0x12, finalFcc);
    sample.sampledUs = esp_timer_get_time();
    // Ignore unrelated status bits, but require both actual safety observations
    // and unchanged capacity-pair reads. Do not publish this into UI telemetry.
    if (!ok) return maintenance_power_reject("raw_read_or_identity_failed");
    if (!mosaico_maintenance_power::safe(sample)) {
        if (!mosaico_maintenance_power::safeOperation(sample.operationStatus))
            return maintenance_power_reject("raw_security_cfg_cal_init");
        if (sample.voltageMv<3900||sample.voltageMv>4350)
            return maintenance_power_reject("raw_voltage_range");
        if (sample.temperatureDeciK<2832||sample.temperatureDeciK>3131)
            return maintenance_power_reject("raw_temperature_range");
        if (sample.currentMa < -130||sample.currentMa>130||
            sample.averageCurrentMa < -130||sample.averageCurrentMa>130)
            return maintenance_power_reject("raw_current_range");
        return maintenance_power_reject("raw_capacity_pair");
    }
    if (!mosaico_maintenance_power::safeOperation(finalOp) ||
        finalDc != sample.designMah || finalFcc != sample.fullMah)
        return maintenance_power_reject("raw_safety_or_pair_changed");
    if (sample.sampledUs <= 0 || sample.sampledUs < started || sample.sampledUs-started>750000)
        return maintenance_power_reject("raw_clock_or_read_latency");
    sample.valid = true;
    out = sample;
    if (result) *result = MaintenancePowerResult::Safe;
    maintenance_power_reason.store("raw_verified_not_soc_calibration",std::memory_order_release);
    return true;
}
