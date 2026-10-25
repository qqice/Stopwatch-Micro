// Nonblocking raw read only. In particular, do not call scale_boot_fence_locked:
// its recovery branch may perform a gauge transaction, or batteryPolicyTelemetry
// which deliberately rejects an uncalibrated 3000mAh capacity model.
#include "mosaico_maintenance_power_model.h"
namespace {
struct MaintenancePowerDiagnostic {
    const char* reason = "not_sampled";
    uint16_t standardFailed = 0;
    MaintenanceGaugeTransportDiagnostic transport{};
    uint16_t identityMask = 0, echo02 = 0, device = 0, firmware = 0, build = 0;
    int64_t elapsedUs = 0;
    bool sampled = false;
};
portMUX_TYPE maintenance_power_cache_lock = portMUX_INITIALIZER_UNLOCKED;
MaintenancePowerDiagnostic maintenance_power_cache{};
uint32_t maintenance_power_bus_lock_busy = 0; // Monotonic RAM diagnostic, never power proof.
void maintenance_power_publish(const char* reason, const MaintenancePowerDiagnostic& diagnostic) {
    portENTER_CRITICAL(&maintenance_power_cache_lock);
    maintenance_power_cache = diagnostic;
    maintenance_power_cache.reason = reason;
    portEXIT_CRITICAL(&maintenance_power_cache_lock);
}
bool maintenance_power_reject(const char* reason, const MaintenancePowerDiagnostic& diagnostic = {}) {
    maintenance_power_publish(reason, diagnostic);
    return false;
}
}
bool Hal::maintenancePowerStatus(char* out,size_t size) const {
    if(!out||!size)return false;
    MaintenancePowerDiagnostic diagnostic;
    uint32_t busLockBusy = 0;
    portENTER_CRITICAL(&maintenance_power_cache_lock);
    diagnostic = maintenance_power_cache;
    busLockBusy = maintenance_power_bus_lock_busy;
    portEXIT_CRITICAL(&maintenance_power_cache_lock);
    const int count=std::snprintf(out,size,
        "reason=%s cached_only=1 no_i2c=1 no_power_proof=1 bus_lock_busy=%lu diag_valid=%u std_fail=0x%03x first_err=%d first_reg=0x%02x first_stage=%u identity_mask=0x%04x echo02=0x%04x device=0x%04x fw=0x%04x build=0x%04x elapsed_us=%lld",
        diagnostic.reason,static_cast<unsigned long>(busLockBusy),unsigned(diagnostic.sampled),unsigned(diagnostic.standardFailed),int(diagnostic.transport.firstError),
        unsigned(diagnostic.transport.firstReg),unsigned(diagnostic.transport.firstStage),
        unsigned(diagnostic.identityMask),unsigned(diagnostic.echo02),unsigned(diagnostic.device),
        unsigned(diagnostic.firmware),unsigned(diagnostic.build),static_cast<long long>(diagnostic.elapsedUs));
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
#ifdef MOSAICO_FACTORY_SCALE_RUNTIME
    // Cached only: even an unchanged historical reason cannot admit a live
    // Pending/Applied/Restoring/corrupt factory transaction to maintenance OTA.
#ifdef MOSAICO_GAUGE_CONTRAST_RUNTIME
    if (!contrast_maintenance_cached_safe_locked())
        return maintenance_power_reject("contrast_not_cached_stable_original_or_missing");
#endif
    if (!factory_scale_maintenance_cached_safe_locked())
        return maintenance_power_reject("factory_scale_not_cached_stable_original_or_missing");
#endif
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
    bool historicalCritical = false;
    uint32_t historicalFailedCrc = 0;
    if (gauge_boot_info.status == GaugeBootReloadStatus::Critical) {
        // Boot reason/attempted are retained history, not present power proof.
        // A completed recovery may leave Critical latched until MCU restart.
        // Require exact native evidence here and fresh raw safety reads below;
        // the explicit maintenance-restart caller still checks its expected CRC.
        native_recovery::Evidence evidence{};
        const auto mac = getFactoryMac();
        if (!native_evidence_locked(evidence) || !native_recovery::exact(evidence, mac.data()))
            return maintenance_power_reject("historical_failure_evidence_mismatch");
        historicalCritical = true;
        historicalFailedCrc = native_recovery::digest(evidence);
    }
    const int64_t started = esp_timer_get_time();
    if (started <= 0) return maintenance_power_reject("clock_not_positive");
    MaintenancePowerSnapshot sample{};
    MaintenancePowerDiagnostic diagnostic{};
    // Bit ordinal: op, voltage, temperature, design, FCC, current, average,
    // final op, final design, final FCC. Except for a proven first bus-lock busy,
    // never short-circuit the existing reads.
    auto word = [&](uint8_t reg, uint16_t& value, unsigned ordinal) {
        const bool read = gauge_word_diagnostic(reg,value,&diagnostic.transport,ordinal<7?1:4);
        if (!read) diagnostic.standardFailed |= uint16_t(1u<<ordinal);
        return read;
    };
    uint16_t current = 0, average = 0, finalOp = 0, finalDc = 0, finalFcc = 0;
    bool ok = word(0x3a, sample.operationStatus,0);
    // Installed synchronous IDF 6.1 transport: ESP_ERR_TIMEOUT here uniquely
    // means bus_lock acquisition failed before this call issued a transaction.
    // Transfer timeout/NACK is INVALID_RESPONSE, not a retryable power sample.
    // Return through both lock destructors; the existing OTA contention loop
    // may yield and request a complete fresh sample, never cached proof.
    if (!ok && mosaico_maintenance_power::firstReadBusLockBusy(
            diagnostic.transport.firstError == ESP_ERR_TIMEOUT,
            diagnostic.transport.firstReg, diagnostic.transport.firstStage,
            diagnostic.standardFailed)) {
        const int64_t ended = esp_timer_get_time();
        diagnostic.elapsedUs = ended > 0 ? ended - started : -1;
        diagnostic.sampled = true;
        portENTER_CRITICAL(&maintenance_power_cache_lock);
        if (maintenance_power_bus_lock_busy != UINT32_MAX) ++maintenance_power_bus_lock_busy;
        portEXIT_CRITICAL(&maintenance_power_cache_lock);
        if (result) *result = MaintenancePowerResult::Busy;
        return maintenance_power_reject("raw_bus_lock_busy",diagnostic);
    }
    ok &= word(0x08, sample.voltageMv,1);
    ok &= word(0x06, sample.temperatureDeciK,2);
    ok &= word(0x3c, sample.designMah,3);
    ok &= word(0x12, sample.fullMah,4);
    ok &= word(0x0c, current,5);
    ok &= word(0x14, average,6);
    sample.currentMa = static_cast<int16_t>(current);
    sample.averageCurrentMa = static_cast<int16_t>(average);
    GaugeEngineSnapshot identity{};
    gauge_engine_locked(identity, mosaico_gauge_status::Explicit, 0,&diagnostic.transport);
    constexpr auto required = mosaico_gauge_engine::Identity | mosaico_gauge_engine::DefinitionMatch;
    ok &= (identity.validMask & required) == required;
    diagnostic.identityMask=identity.validMask;
    diagnostic.echo02=identity.echo02;
    diagnostic.device=mosaico_gauge_engine::beWord(identity.rawIdentity,0);
    diagnostic.firmware=mosaico_gauge_engine::beWord(identity.rawIdentity,2);
    diagnostic.build=mosaico_gauge_engine::beWord(identity.rawIdentity,4);
    ok &= word(0x3a, finalOp,7);
    ok &= word(0x3c, finalDc,8);
    ok &= word(0x12, finalFcc,9);
    sample.sampledUs = esp_timer_get_time();
    diagnostic.elapsedUs=sample.sampledUs>0?sample.sampledUs-started:-1;
    diagnostic.sampled=true;
    // Ignore unrelated status bits, but require both actual safety observations
    // and unchanged capacity-pair reads. Do not publish this into UI telemetry.
    if (!ok) return maintenance_power_reject("raw_read_or_identity_failed",diagnostic);
    if (!mosaico_maintenance_power::safe(sample)) {
        if (!mosaico_maintenance_power::safeOperation(sample.operationStatus))
            return maintenance_power_reject("raw_security_cfg_cal_init",diagnostic);
        if (sample.voltageMv<3900||sample.voltageMv>4350)
            return maintenance_power_reject("raw_voltage_range",diagnostic);
        if (sample.temperatureDeciK<2832||sample.temperatureDeciK>3131)
            return maintenance_power_reject("raw_temperature_range",diagnostic);
        if (sample.currentMa < -130||sample.currentMa>130||
            sample.averageCurrentMa < -130||sample.averageCurrentMa>130)
            return maintenance_power_reject("raw_current_range",diagnostic);
        return maintenance_power_reject("raw_capacity_pair",diagnostic);
    }
    if (!mosaico_maintenance_power::safeOperation(finalOp) ||
        finalDc != sample.designMah || finalFcc != sample.fullMah)
        return maintenance_power_reject("raw_safety_or_pair_changed",diagnostic);
    if (sample.sampledUs <= 0 || sample.sampledUs < started || sample.sampledUs-started>750000)
        return maintenance_power_reject("raw_clock_or_read_latency",diagnostic);
    sample.historicalCritical = historicalCritical;
    sample.historicalFailedCrc = historicalFailedCrc;
    sample.valid = true;
    out = sample;
    if (result) *result = MaintenancePowerResult::Safe;
    maintenance_power_publish("raw_verified_not_soc_calibration",diagnostic);
    return true;
}
