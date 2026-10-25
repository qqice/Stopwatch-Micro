#include "network_quota.h"
#include "mosaico_twt_model.h"
#include "host_bridge.h"
#include "tailscale_transport.h"
#include "token_history.h"
#ifdef MOSAICO_BOARD
#include "quota_monitor.h"
#include "mosaico_ota_buffer_model.h"
#include <debug/mosaico_rx_observer.h>
#include <debug/mosaico_wifi_rx_observer.h>
#include <debug/mosaico_rx_ingress.h>
#include "mosaico_quota_request_policy.h"
#include "mosaico_display_settings.h"
#include "mosaico_session_monitor.h"
#include "wifi_retry_model.h"
#include "first_quota_refresh_policy.h"
#include "system_clock.h"
#include <ota/panic_capture.h>
#include <hal/hal.h>
#include <esp_heap_caps.h>
#include <esp_memory_utils.h>
#include <esp_ota_ops.h>
#include <esp_system.h>
#endif
#include <memory>
#include <algorithm>
#include <new>
#include <cJSON.h>
#include <cmath>
#include <cstring>
#include <cstdio>
#include <esp_crt_bundle.h>
#include <esp_event.h>
#include <esp_http_client.h>
#include <esp_netif.h>
#include <esp_netif_sntp.h>
#include <ctime>
#include <esp_timer.h>
#include <esp_pm.h>
#ifdef MOSAICO_BOARD
#include "standby_sleep.h"
#endif
#include <esp_private/esp_clk.h>
#include <esp_log.h>
#include <hal/ble/codex_micro_ble.h>
#include <esp_wifi.h>
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
#include <esp_private/esp_wifi_he_private.h>
static_assert(sizeof(esp_test_hw_rx_statistics_t) >= 86, "S31 getter writes through byte 85");
#endif
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
#include <esp_wifi_he.h>
#endif
#include <freertos/task.h>
#include <mbedtls/base64.h>
#include <nvs.h>

namespace {
NetworkQuota instance;
#ifdef MOSAICO_BOARD
// Startup is the sole allocator; only the network owner's OTA path acquires.
struct OtaBufferAllocator {
    uint8_t* allocate(MosaicoOtaBuffer::Kind kind,size_t capacity) {
        const uint32_t caps=kind==MosaicoOtaBuffer::Kind::Chunk?
            MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT | MALLOC_CAP_DMA:
            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
        return static_cast<uint8_t*>(heap_caps_malloc(capacity,caps));
    }
    void release(uint8_t* memory){heap_caps_free(memory);}
    bool valid(MosaicoOtaBuffer::Kind kind,const uint8_t* memory,size_t capacity) const {
        const size_t expected=kind==MosaicoOtaBuffer::Kind::Chunk?
            MosaicoOtaBuffer::ChunkCapacity:MosaicoOtaBuffer::BodyCapacity;
        if(!memory||capacity!=expected)return false;
        const uint8_t* end=memory+capacity-1;
        if(kind==MosaicoOtaBuffer::Kind::Chunk)
            return esp_ptr_in_dram(memory)&&esp_ptr_in_dram(end)&&
                esp_ptr_internal(memory)&&esp_ptr_internal(end)&&
                esp_ptr_dma_capable(memory)&&esp_ptr_dma_capable(end);
        return esp_ptr_external_ram(memory)&&esp_ptr_external_ram(end);
    }
};
OtaBufferAllocator otaBufferAllocator;
MosaicoOtaBuffer::Reservation otaBuffers;
void sampleNetworkHeap(const char* stage, MosaicoHeapProbeStage probeStage, unsigned& budget) {
    if (budget >= 2) return;
    ++budget;
    const bool ok = [&]() { MosaicoHeapProbeScope probe(probeStage);
        return heap_caps_check_integrity_all(false); }();
    ESP_LOGI("QuotaHeapDiag", "stage=%s heap_ok=%d stack_hwm=%u sample=%u/2",
        stage, ok, static_cast<unsigned>(uxTaskGetStackHighWaterMark(nullptr)), budget);
}
#endif
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
bool twtOtaBlocked()
{
    return MosaicoOta::blocksTwt(); // UI snapshot try-lock failure is not an OTA request.
}
#endif
bool copyString(cJSON* root, const char* key, char* out, size_t capacity, bool empty = false)
{
    const cJSON* value = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsString(value) || !value->valuestring) return false;
    const size_t length = std::strlen(value->valuestring);
    if (length >= capacity || (!empty && length == 0)) return false;
    for (size_t i = 0; i < length; ++i)
        if (static_cast<unsigned char>(value->valuestring[i]) < 32) return false;
    std::memcpy(out, value->valuestring, length + 1);
    return true;
}
bool number(cJSON* root, const char* key, double low, double high, uint32_t& out)
{
    const cJSON* value = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsNumber(value) || !std::isfinite(value->valuedouble) || value->valuedouble < low ||
        value->valuedouble > high || std::floor(value->valuedouble) != value->valuedouble)
        return false;
    out = static_cast<uint32_t>(value->valuedouble);
    return true;
}
}  // namespace
NetworkQuota& GetNetworkQuota()
{
    return instance;
}
#ifdef MOSAICO_BOARD
bool NetworkQuota::prepareOtaBuffers()
{
    const bool ready=otaBuffers.prepare(otaBufferAllocator);
    ESP_LOGI("NetworkOTA", "boot_buffers_ready=%d error=%s chunk_internal_dma=%u body_psram=%u",
        ready,MosaicoOtaBuffer::errorName(otaBuffers.error()),
        static_cast<unsigned>(MosaicoOtaBuffer::ChunkCapacity),static_cast<unsigned>(MosaicoOtaBuffer::BodyCapacity));
    return ready; // Failure never restarts or prevents ordinary app startup.
}
#endif
void NetworkQuota::setLocked(bool locked)
{
    const bool changed = _locked.exchange(locked) != locked;
#ifdef MOSAICO_BOARD
    // Boost synchronously BEFORE the view starts its full wake redraw, rather
    // than racing the network worker's eventual wake notification.
    if (!locked) { StandbySleep::cancelForActivity(); setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ); }
#endif
    if (changed && _task_handle) xTaskNotifyGive(_task_handle);
#ifndef MOSAICO_BOARD
    GetCodexMicroBle().requestRadioIdle(idleLocked());
#endif
}
void NetworkQuota::setPowerProfile(uint8_t profile)
{
    if (profile > 2) return;
    _power_profile = profile;
#ifndef MOSAICO_BOARD
    GetCodexMicroBle().requestRadioIdle(idleLocked());
#endif
    if (_task_handle) xTaskNotifyGive(_task_handle);
}
void NetworkQuota::refreshWhileLocked()
{
    if (!idleLocked()) return;
    _force_refresh = true;
    if (_task_handle) xTaskNotifyGive(_task_handle);
}
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
bool NetworkQuota::requestLanTcp(const MosaicoLanTcp::Request& request)
{
    if (!_hw_rx_owner_ready.load() || !idleLocked() || MosaicoOta::blocksTwt()) return false;
    std::unique_lock<std::mutex> settingsGuard(_wifi_settings_mutex, std::try_to_lock);
    if (!settingsGuard.owns_lock() || _wifi_settings.pending || _wifi_settings.restartPending) return false;
    const auto t = twtSnapshot();
    if (!t.id || t.cleanupPending || t.cleanupFailed || !t.associated ||
        (t.stage != MosaicoTwt::Stage::Baseline && t.stage != MosaicoTwt::Stage::Active)) return false;
    return _lan_tcp.request(request, t.id, static_cast<uint8_t>(t.stage)); // No notification.
}
void NetworkQuota::serviceLanTcp()
{
    if (!_lan_tcp.busy()) return;
    bool settingsClear=false;
    {
        std::unique_lock<std::mutex> guard(_wifi_settings_mutex, std::try_to_lock);
        settingsClear=guard.owns_lock() && !_wifi_settings.pending && !_wifi_settings.restartPending;
    }
    bool eventsClear;
    portENTER_CRITICAL(&_twt_mux);
    eventsClear = _twt_event_read == _twt_event_write && !_twt_event_overflow;
    portEXIT_CRITICAL(&_twt_mux);
    const bool allowed = settingsClear && eventsClear && idleLocked() && !MosaicoOta::blocksTwt() &&
        _twt_request.load() == -1 && !_twt.state.cleanupPending && !_twt_cleanup_failed &&
        !_twt.state.cleanupFailed && _twt.state.expiryUs > esp_timer_get_time() &&
        (_twt.state.stage == MosaicoTwt::Stage::Baseline || _twt.state.stage == MosaicoTwt::Stage::Active) &&
        _twt.state.associated && _wifi_running.load() && _connected.load();
    _lan_tcp.service(allowed, _twt.state.id, static_cast<uint8_t>(_twt.state.stage));
}
bool NetworkQuota::requestTsfProbe(uint32_t timeoutMs, uint64_t& requestId)
{
    requestId = 0;
    if (timeoutMs < 250 || timeoutMs > 2000) return false;
    bool expected = false;
    if (!_tsf_probe_pending.compare_exchange_strong(expected, true)) return false;
    const bool ready = _hw_rx_owner_ready.load() && !MosaicoOta::blocksTwt();
    portENTER_CRITICAL(&_twt_mux);
    // Once the API has actually been invoked, preserve its evidence forever.
    if (_tsf_probe_consumed) {
        _tsf_probe_pending.store(false);
        portEXIT_CRITICAL(&_twt_mux);
        return false;
    }
    const auto next = _tsf_probe_cache.requestId + 1;
    const auto processed = _tsf_probe_cache.processedId;
    const bool active = _twt_cache.requested == MosaicoTwt::Mode::On &&
        _twt_cache.stage == MosaicoTwt::Stage::Active && _twt_cache.associated;
    _tsf_probe_cache = {};
    _tsf_probe_cache.requestId = next;
    _tsf_probe_cache.processedId = ready && active ? processed : next;
    _tsf_probe_cache.trialId = _twt_cache.id;
    _tsf_probe_cache.timeoutMs = timeoutMs;
    _tsf_probe_cache.sameTrial = active;
    _tsf_probe_cache.queued = ready && active;
    _tsf_probe_cache.apiResult = ESP_ERR_INVALID_STATE;
    requestId = next;
    const bool accepted = _tsf_probe_cache.queued;
    if (!accepted) _tsf_probe_pending.store(false);
    portEXIT_CRITICAL(&_twt_mux);
    return accepted; // No notification: do not change quota/history cadence.
}
MosaicoTsfProbe::Snapshot NetworkQuota::tsfProbeSnapshot() const
{
    portENTER_CRITICAL(&_twt_mux);
    const auto copy = _tsf_probe_cache;
    portEXIT_CRITICAL(&_twt_mux);
    return copy;
}
void NetworkQuota::captureTsfProbeEvent(int status, uint8_t reason, int64_t localUs)
{
    // Fixed first-event slot only: no Wi-Fi API, owner wake, allocation or output.
    portENTER_CRITICAL(&_twt_mux);
    if (_tsf_probe_accept_event && !_tsf_probe_event.seen)
        _tsf_probe_event = {true, status, reason, localUs};
    portEXIT_CRITICAL(&_twt_mux);
}
void NetworkQuota::tsfProbeTimerSentinel(void* arg)
{
    // NetworkQuota is the permanent global instance, never a request-local object.
    // The ESP_TIMER_TASK callback only writes this atomic flag: no SDK calls.
    static_cast<NetworkQuota*>(arg)->_tsf_probe_timer_seen.store(true);
}
void NetworkQuota::serviceTsfProbe()
{
    if (!_tsf_probe_pending.load()) return;
    auto sample = tsfProbeSnapshot();
    if (!sample.queued && !sample.inflight) return; // Admission not published yet.
    auto sameActiveTrial = [&]() {
        portENTER_CRITICAL(&_twt_mux);
        const bool eventsClear = !_twt_event_overflow && _twt_event_read == _twt_event_write;
        portEXIT_CRITICAL(&_twt_mux);
        return eventsClear && idleLocked() && _twt.state.expiryUs > esp_timer_get_time() &&
            _twt_request.load() == -1 && !_twt.state.cleanupPending && !_twt.state.cleanupFailed && !_twt_cleanup_failed &&
            _twt.live() && _twt.state.requested == MosaicoTwt::Mode::On &&
            _twt.state.stage == MosaicoTwt::Stage::Active && _twt.state.associated &&
            _twt.state.id == sample.trialId;
    };
    if (sample.queued) {
        sample.queued = false;
        sample.processedId = sample.requestId;
        sample.sameTrial = sameActiveTrial();
        wifi_ap_record_t ap{};
        const bool allowed = _hw_rx_owner_ready.load() && _wifi_running.load() && _connected.load() &&
            sample.sameTrial && !MosaicoOta::blocksTwt() && esp_wifi_sta_get_ap_info(&ap) == ESP_OK &&
            !MosaicoOta::blocksTwt() && _wifi_running.load() && _connected.load() && sameActiveTrial();
        if (allowed) {
            sample.beforeStartUs = esp_timer_get_time();
            sample.tsfBefore = esp_wifi_get_tsf_time(WIFI_IF_STA);
            sample.beforeEndUs = esp_timer_get_time();
            sample.beforeValid = sample.tsfBefore > 0;
        }
        // Fresh OTA/link/trial gates directly before arming callback ownership.
        if (!allowed || MosaicoOta::blocksTwt() || !_wifi_running.load() || !_connected.load() || !sameActiveTrial()) {
            sample.sameTrial = sameActiveTrial();
            portENTER_CRITICAL(&_twt_mux);
            _tsf_probe_cache = sample;
            _tsf_probe_pending.store(false);
            portEXIT_CRITICAL(&_twt_mux);
            return; // No actual invocation: a later valid admission can retry.
        }
        sample.apiCalled = true;
        sample.consumed = true;
        sample.inflight = true;
        sample.deadlineUs = esp_timer_get_time() + (int64_t(sample.timeoutMs) + 2000) * 1000;
        // Arm BEFORE the public API: its callback may arrive before it returns.
        portENTER_CRITICAL(&_twt_mux);
        // Final event-FIFO gate shares the callback lock with slot arming.
        if (_twt_event_overflow || _twt_event_read != _twt_event_write) {
            sample.apiCalled = sample.consumed = sample.inflight = false;
            sample.sameTrial = false;
            _tsf_probe_cache = sample;
            _tsf_probe_pending.store(false);
            portEXIT_CRITICAL(&_twt_mux);
            return;
        }
        _tsf_probe_consumed = true;
        _tsf_probe_event = {};
        _tsf_probe_accept_event = true;
        _tsf_probe_cache = sample;
        portEXIT_CRITICAL(&_twt_mux);
        sample.apiResult = esp_wifi_sta_itwt_send_probe_req(static_cast<int>(sample.timeoutMs));
        if (sample.apiResult != ESP_OK) {
            sample.inflight = false;
            portENTER_CRITICAL(&_twt_mux);
            _tsf_probe_accept_event = false;
            _tsf_probe_event = {}; // API failure is not an observed completion.
            _tsf_probe_cache = sample;
            _tsf_probe_pending.store(false);
            portEXIT_CRITICAL(&_twt_mux);
            return;
        }
    }
    TsfProbeEvent event;
    portENTER_CRITICAL(&_twt_mux);
    event = _tsf_probe_event;
    portEXIT_CRITICAL(&_twt_mux);
    sample.sameTrial = sameActiveTrial();
    const auto now = esp_timer_get_time();
    sample.deadlineExpired = sample.deadlineExpired ||
        (event.seen ? event.localUs >= sample.deadlineUs : now >= sample.deadlineUs);
    if (event.seen) {
        sample.callbackSeen = true;
        sample.probeStatus = event.status;
        sample.reason = event.reason;
        sample.callbackLocalUs = event.localUs;
        // First PP barrier confirms SDK timer disarm/done; a timer-task sentinel
        // then drains any already-running legacy timer producer. The second PP
        // barrier consumes messages that producer may have posted after barrier 1.
        if (!sample.drainAttempted) {
            sample.drainAttempted = true;
            sample.flowBitmap = -1;
            sample.firstPpResult = esp_wifi_sta_itwt_get_flow_id_status(&sample.flowBitmap);
            sample.drainResult = sample.firstPpResult;
        }
        if (sample.firstPpResult == ESP_OK && !sample.timerCreateAttempted) {
            sample.timerCreateAttempted = true;
            _tsf_probe_timer_seen.store(false);
            esp_timer_create_args_t args{};
            args.callback = &NetworkQuota::tsfProbeTimerSentinel;
            args.arg = this; // Permanent owner lifetime, safe even on a retained fault.
            args.dispatch_method = ESP_TIMER_TASK;
            args.name = "tsf_probe_drain";
            sample.timerError = esp_timer_create(&args, &_tsf_probe_timer);
            sample.timerCreated = sample.timerError == ESP_OK;
            if (sample.timerCreated) {
                sample.timerError = esp_timer_start_once(_tsf_probe_timer, 1);
                sample.timerStarted = sample.timerError == ESP_OK;
            }
        }
        if (sample.timerStarted) sample.timerSeen = _tsf_probe_timer_seen.load();
        if (sample.timerSeen && !sample.secondPpAttempted) {
            sample.secondPpAttempted = true;
            sample.secondPpResult = esp_wifi_sta_itwt_get_flow_id_status(&sample.flowBitmap);
            sample.drainResult = sample.secondPpResult;
        }
        // Delete once after the sentinel ran, or immediately if its start failed.
        // A failed delete is retained as a resource fault, never claimed reclaimed.
        // ESP_OK queues asynchronous timer-task deletion; no claim of synchronous free.
        if (sample.timerCreated && !sample.reclaimAttempted &&
            (sample.timerSeen || (!sample.timerStarted && sample.timerError != ESP_OK))) {
            sample.reclaimAttempted = true;
            sample.deleteResult = esp_timer_delete(_tsf_probe_timer);
            sample.resourceReclaimed = sample.deleteResult == ESP_OK;
            if (sample.resourceReclaimed) _tsf_probe_timer = nullptr;
        }
        sample.driverDrained = sample.drainAttempted && sample.firstPpResult == ESP_OK &&
            sample.timerStarted && sample.timerSeen && sample.secondPpAttempted &&
            sample.secondPpResult == ESP_OK && sample.resourceReclaimed;
        sample.inflight = !sample.driverDrained;
        // A failed barrier is retained for manual investigation; never retry.
        // The after-TSF read belongs to the owner, never the event callback.
        wifi_ap_record_t ap{};
        if (sample.driverDrained && !sample.afterStartUs && _wifi_running.load() && _connected.load() && sample.sameTrial &&
            !MosaicoOta::blocksTwt() && esp_wifi_sta_get_ap_info(&ap) == ESP_OK && !MosaicoOta::blocksTwt()) {
            sample.afterStartUs = esp_timer_get_time();
            sample.tsfAfter = esp_wifi_get_tsf_time(WIFI_IF_STA);
            sample.afterEndUs = esp_timer_get_time();
            sample.afterValid = sample.tsfAfter > 0;
        }
    }
    portENTER_CRITICAL(&_twt_mux);
    _tsf_probe_cache = sample;
    if (sample.driverDrained) {
        _tsf_probe_accept_event = false;
        _tsf_probe_pending.store(false);
    }
    portEXIT_CRITICAL(&_twt_mux);
    // Deadline alone NEVER closes inflight: keep the one late callback observable.
}

bool NetworkQuota::requestHwRxSnapshot(uint64_t& requestId)
{
    requestId = 0;
    // A single outstanding request, including owner execution. Never overwrite it.
    bool expected = false;
    if (!_hw_rx_pending.compare_exchange_strong(expected, true)) return false;
    const bool accepted = _hw_rx_owner_ready.load() && !MosaicoOta::blocksTwt();
    portENTER_CRITICAL(&_hw_rx_mux);
    const auto next = _hw_rx_cache.requestId + 1;
    const auto samples = _hw_rx_cache.sampleSequence;
    const auto processed = _hw_rx_cache.processedId;
    _hw_rx_cache = {};
    _hw_rx_cache.requestId = next;
    _hw_rx_cache.processedId = accepted ? processed : next;
    _hw_rx_cache.sampleSequence = samples;
    _hw_rx_cache.pending = accepted;
    _hw_rx_cache.error = ESP_ERR_INVALID_STATE;
    requestId = next;
    if (!accepted) _hw_rx_pending.store(false);
    portEXIT_CRITICAL(&_hw_rx_mux);
    // No owner wake: execute at the next naturally scheduled network loop.
    return accepted;
}
MosaicoHwRx::Snapshot NetworkQuota::hwRxSnapshot() const
{
    portENTER_CRITICAL(&_hw_rx_mux);
    const auto copy = _hw_rx_cache;
    portEXIT_CRITICAL(&_hw_rx_mux);
    return copy;
}
void NetworkQuota::serviceHwRxSnapshot()
{
    if (!_hw_rx_pending.load()) return;
    auto sample = hwRxSnapshot();
    // Admission CAS can become visible before cache initialization. Wait for
    // published pending state; a later naturally scheduled loop observes it.
    if (!sample.pending) return;
    sample.pending = false;
    sample.processedId = sample.requestId;
    sample.valid = false;
    sample.error = ESP_ERR_INVALID_STATE;
    wifi_ap_record_t ap{};
    if (_wifi_running.load() && _connected.load() && !MosaicoOta::blocksTwt() &&
        esp_wifi_sta_get_ap_info(&ap) == ESP_OK && !MosaicoOta::blocksTwt()) {
        // Exact installed private type; getter receives the whole SDK structure,
        // but only named numeric whitelist fields escape to the public cache.
        esp_test_hw_rx_statistics_t raw{};
        sample.beforeUs = esp_timer_get_time();
        sample.getterCalled = true;
        sample.error = esp_test_get_hw_rx_statistics(&raw);
        sample.afterUs = esp_timer_get_time();
        if (sample.error == ESP_OK) {
            sample.valid = true;
            ++sample.sampleSequence;
            sample.rx_fcs_err = raw.rx_fcs_err;
            sample.rx_abort = raw.rx_abort;
            sample.rx_abort_fcs_pass = raw.rx_abort_fcs_pass;
            sample.nrx_err_pwrdrop = raw.nrx_err_pwrdrop;
            sample.rx_mpdu = raw.rx_mpdu;
            sample.rx_end_cnt = raw.rx_end_cnt;
            sample.rx_datasuc = raw.rx_datasuc;
            sample.rx_buf_fullcnt = raw.rx_buf_fullcnt;
            sample.rx_fifo_ovfcnt = raw.rx_fifo_ovfcnt;
            sample.rx_tkip_errcnt = raw.rx_tkip_errcnt;
            sample.rx_lastunmatch_err = raw.rx_lastunmatch_err;
            sample.nrx_err_bboff = raw.nrx_err_bboff;
            sample.nrx_err_agcexit = raw.nrx_err_agcexit;
            sample.rxhung_statis = raw.rxhung_statis;
            sample.txhung_statis = raw.txhung_statis;
            sample.rxtxhung = raw.rxtxhung;
            sample.rxtxpanic = raw.rxtxpanic;
        }
    }
    // No SDK API is called while holding this cache lock.
    portENTER_CRITICAL(&_hw_rx_mux);
    _hw_rx_cache = sample;
    _hw_rx_pending.store(false);
    portEXIT_CRITICAL(&_hw_rx_mux);
}
#endif

void NetworkQuota::wait(uint32_t milliseconds)
{
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
    if (_lan_tcp.busy()) milliseconds = std::min<uint32_t>(milliseconds, 100);
#endif
#ifdef MOSAICO_BOARD
    const auto wifiRx = mosaico_wifi_rx_observer::snapshot();
    if (wifiRx.active) {
        const uint32_t now = uint32_t(esp_timer_get_time()/1000);
        const int32_t remaining = int32_t(wifiRx.deadlineMs-now);
        milliseconds=std::min<uint32_t>(milliseconds, remaining>0 ? std::min<uint32_t>(remaining,1000) : 1U);
    } else if (wifiRx.cleanupPending || wifiRx.queued) milliseconds=std::min<uint32_t>(milliseconds,1000U);
    const auto rxObserver = mosaico_rx_observer::snapshot();
    if (rxObserver.active) {
        const uint32_t nowMs = static_cast<uint32_t>(esp_timer_get_time() / 1000);
        const int32_t remaining = static_cast<int32_t>(rxObserver.deadlineMs - nowMs);
        milliseconds = std::min<uint32_t>(milliseconds, remaining > 0 ? std::min<uint32_t>(remaining, 1000) : 1U);
    } else if (rxObserver.reporting || rxObserver.queued) milliseconds = std::min<uint32_t>(milliseconds, 1000U);
    MosaicoDisplay::Snapshot display;
    if (MosaicoDisplay::snapshot(display) && display.pending && milliseconds > 250) milliseconds = 250;
    WifiSettingsSnapshot wifi;
    if (wifiSettingsSnapshot(wifi) && (wifi.pending || wifi.restartPending) && milliseconds > 250) milliseconds=250;
#endif
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
    if (_twt.state.cleanupPending && (_twt.state.cleanupStage == 6 || _twt.state.cleanupStage == 7)) {
        milliseconds = std::min<uint32_t>(milliseconds, 1000); // Probe hold has no cancellation deadline.
    } else if (_twt.live() || (_twt.state.cleanupPending && !_twt_cleanup_failed)) {
        const int64_t now = esp_timer_get_time();
        const int64_t deadline = _twt.state.cleanupPending ? _twt.state.cleanupDeadlineUs :
            (_twt.state.setupDeadlineUs ? std::min(_twt.state.setupDeadlineUs, _twt.state.expiryUs) : _twt.state.expiryUs);
        const uint32_t remaining = static_cast<uint32_t>(std::max<int64_t>(1, (deadline - now) / 1000));
        milliseconds = std::min(milliseconds, std::min<uint32_t>(remaining, 1000));
    }
#endif
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(milliseconds));
}
void NetworkQuota::setCpu(uint32_t mhz)
{
#ifdef MOSAICO_BOARD
    std::lock_guard<std::mutex> clockLock(_cpu_mutex);
    // Repeated corruption is correlated with the 80MHz standby/transition
    // configuration. Keep CPU/SYS/MEM clock domains stable as a conservative
    // guard, NOT a proven display root-cause fix. Radio duty cycling is retained.
    if (idleLocked() && mhz == 80)
        mhz = _diagnostic_override.load() ? _diagnostic_idle_mhz.load() : _idle_cpu_mhz.load();
    else mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ;
#endif
#ifdef MOSAICO_BOARD
    const bool sleep=StandbySleep::allow(mhz,idleLocked(),_wifi_running.load());
    applyCpuConfig(mhz,sleep);
#else
    applyCpuConfig(mhz,false);
#endif
}
void NetworkQuota::applyCpuConfig(uint32_t mhz, bool lightSleep)
{
#ifdef MOSAICO_BOARD
    if(_sleep_pm_fault)lightSleep=false;
    if(_cpu_target==mhz && (_cpu_light_sleep==lightSleep || _sleep_pm_fault))return;
    StandbySleep::beforeConfigure(lightSleep);
#else
    if(_cpu_target==mhz)return;
#endif
    esp_pm_config_t config{};
    config.max_freq_mhz=mhz;config.min_freq_mhz=mhz;config.light_sleep_enable=lightSleep;
    const esp_err_t result=esp_pm_configure(&config);
    _clock_error=result;
#ifdef MOSAICO_BOARD
    if(result!=ESP_OK && (lightSleep || _cpu_light_sleep))_sleep_pm_fault=true;
    StandbySleep::configured(lightSleep,result);
    if(result==ESP_OK)_cpu_light_sleep=lightSleep;
#endif
    if(result==ESP_OK)_cpu_target=mhz;
}
#ifdef MOSAICO_BOARD
bool NetworkQuota::requestRxIngress(bool on,uint32_t src,uint16_t port,uint32_t ttlSeconds)
{
    if (on && MosaicoOta::blocksTwt()) return false;
    if (!mosaico_rx_ingress::request(on,src,port,ttlSeconds)) return false;
    // Close the admission race with an OTA request on another task. OTA also
    // invalidates any earlier ingress generation at its successful admission.
    if (on && MosaicoOta::blocksTwt()) {
        mosaico_rx_ingress::request(false);
        return false;
    }
    if (_task_handle) xTaskNotifyGive(_task_handle);
    return true;
}
bool NetworkQuota::requestWifiRxObservation(bool on, uint32_t ttlSeconds)
{
    if (on && MosaicoOta::blocksTwt()) return false;
    if (!mosaico_wifi_rx_observer::request(on, ttlSeconds)) return false;
    if (_task_handle) xTaskNotifyGive(_task_handle);
    return true;
}
bool NetworkQuota::requestRxObservation(bool on, uint32_t ttlSeconds, bool controlOnly,
                                        uint32_t filterSrcIp, uint16_t filterSrcPort)
{
    if (on && MosaicoOta::busy()) return false;
    if (!mosaico_rx_observer::request(on, ttlSeconds, controlOnly, filterSrcIp, filterSrcPort)) return false;
    if (_task_handle) xTaskNotifyGive(_task_handle);
    return true;
}

void NetworkQuota::serviceStandbySleep()
{
    // Same PM serialization as the radio owner; never change its frequency,
    // wake deadlines, Wi-Fi/BLE configuration or normal polling cadence.
    std::lock_guard<std::mutex> clockLock(_cpu_mutex);
    if(!_cpu_target)return;
    const bool sleep=StandbySleep::allow(_cpu_target,idleLocked(),_wifi_running.load());
    applyCpuConfig(_cpu_target,sleep);
}
#endif

#ifdef MOSAICO_BOARD
bool NetworkQuota::wifiSettingsSnapshot(WifiSettingsSnapshot& out)
{
    std::unique_lock<std::mutex> guard(_wifi_settings_mutex, std::try_to_lock);
    if (!guard.owns_lock()) return false;
    out = _wifi_settings; return true;
}
void NetworkQuota::scanEvent(void* arg,const char*,int32_t,void* data)
{
    if(!data) return;
    auto& owner=*static_cast<NetworkQuota*>(arg);
    const auto& event=*static_cast<const wifi_event_sta_scan_done_t*>(data);
    // A canceled/completed generation is drained before another scan is allowed.
    // No override/new scan or repeated stop before that DONE is consumed.
    if(!owner._wifi_scan_started.load()) return;
    owner._wifi_scan_id.store(event.scan_id); // Diagnostic only: async IDs need not be monotonic.
    owner._wifi_scan_status.store(event.status);
    owner._wifi_scan_done.store(true);
}
void NetworkQuota::cancelWifiScan()
{
    if(!_wifi_scan_started || _wifi_scan_cancelled) return;
    _wifi_scan_cancelled=true;
    _wifi_scan_deadline=esp_timer_get_time()+2000000;
    if(_wifi_scan_done.load()) { drainWifiScan(); return; }
    // Even a stop error is fail-closed: no new scan until DONE is acknowledged.
    esp_wifi_scan_stop();
}
void NetworkQuota::drainWifiScan()
{
    if(!_wifi_scan_started || !_wifi_scan_cancelled) return;
    if(!_wifi_scan_done.load()) {
        if(!_wifi_scan_fault && esp_timer_get_time()>=_wifi_scan_deadline) {
            _wifi_scan_fault=true;
            std::lock_guard<std::mutex> guard(_wifi_settings_mutex);
            _wifi_settings.scanError=ESP_ERR_TIMEOUT;
        }
        return;
    }
    esp_wifi_clear_ap_list();
    _wifi_scan_started=false; _wifi_scan_cancelled=false; _wifi_scan_done=false;
    _wifi_candidates={}; _wifi_scan_complete=true;
}
bool NetworkQuota::selectWifiCandidate(bool, int64_t now)
{
    // Called only by quota owner and outside TWT live state. Async scan has an
    // explicit wall deadline, bounded AP buffer and unconditional result release.
    drainWifiScan();
    if(_wifi_scan_fault) return false;
    if(_wifi_scan_started && _wifi_scan_cancelled) return false;
    if(!_wifi_profiles.count) { _wifi_candidates={}; _wifi_scan_complete=true; return false; }
    if(!_wifi_scan_complete) {
        if(!_wifi_scan_started) {
            _wifi_candidates={}; _wifi_scan_done=false;
            wifi_scan_config_t scan{};
            scan.show_hidden=false; scan.scan_type=WIFI_SCAN_TYPE_ACTIVE;
            scan.scan_time.active.min=30; scan.scan_time.active.max=60;
            _wifi_scan_started=true;
            if(!_wifi_scan_handler || esp_wifi_scan_start(&scan,false)!=ESP_OK) {
                _wifi_scan_started=false; esp_wifi_clear_ap_list(); _wifi_scan_complete=true; return false;
            }
            _wifi_scan_deadline=now+4000000;
            return false;
        }
        if(!_wifi_scan_done) {
            if(now>=_wifi_scan_deadline) cancelWifiScan();
            return false;
        }
        wifi_ap_record_t records[24]{}; uint16_t count=24;
        if(_wifi_scan_status.load()==0 && esp_wifi_scan_get_ap_records(&count,records)==ESP_OK) {
            MosaicoWifiProfiles::Visible visible[24]{};
            for(unsigned i=0;i<count && i<24;++i) {
                std::memcpy(visible[i].ssid,records[i].ssid,32);
                visible[i].rssi=records[i].rssi;
            }
            _wifi_candidates=MosaicoWifiProfiles::rank(_wifi_profiles,visible,std::min<unsigned>(count,24));
        }
        esp_wifi_clear_ap_list(); _wifi_scan_started=false; _wifi_scan_complete=true;
    }
    if(_wifi_candidates.next>=_wifi_candidates.count) return false;
    const auto& profile=_wifi_profiles.entries[_wifi_candidates.indices[_wifi_candidates.next++]];
    wifi_config_t config{};
    config.sta.sae_pwe_h2e=WPA3_SAE_PWE_BOTH; config.sta.pmf_cfg.capable=true;
    std::memcpy(config.sta.ssid,profile.ssid,32); std::memcpy(config.sta.password,profile.password,64);
    // Abort any preceding unsuccessful association before changing profile.
    esp_wifi_disconnect();
    const esp_err_t error=esp_wifi_set_config(WIFI_IF_STA,&config);
    MosaicoWifi::scrub(&config,sizeof(config));
    if(error!=ESP_OK) return false;
    std::memcpy(_ssid,profile.ssid,33); std::memcpy(_password,profile.password,65);
    { std::lock_guard<std::mutex> guard(_wifi_settings_mutex);
      std::memcpy(_wifi_settings.ssid,_ssid,sizeof(_ssid)); publishWifiProfiles(); }
    return true;
}
bool NetworkQuota::requestWifiCredentials(const char* ssid, const char* password)
{
    std::unique_lock<std::mutex> guard(_wifi_settings_mutex, std::try_to_lock);
    if (!guard.owns_lock()) return false;
    if (!_wifi_settings.available) { _wifi_settings.error=ESP_ERR_INVALID_STATE; return false; }
    if (_wifi_settings.pending || _wifi_settings.restartPending) return false;
    if (!MosaicoWifi::validCredentials(ssid,password)) { _wifi_settings.error=ESP_ERR_INVALID_ARG; return false; }
    bool exists=false;
    for(unsigned i=0;i<_wifi_settings.count;++i) if(!std::strcmp(ssid,_wifi_settings.names[i])) exists=true;
    if(!exists && _wifi_settings.count==6) { _wifi_settings.error=ESP_ERR_NO_MEM; return false; }
    _pending_forget=false;
    std::strcpy(_pending_ssid,ssid); std::strcpy(_pending_password,password);
    _wifi_settings.pending=true; _wifi_settings.error=0;
#if CONFIG_IDF_TARGET_ESP32S31
    ++_wifi_settings_generation;
#endif
    xTaskNotifyGive(_task_handle); return true;
}
bool NetworkQuota::requestWifiForget(const char* ssid)
{
    std::unique_lock<std::mutex> guard(_wifi_settings_mutex,std::try_to_lock);
    if(!guard.owns_lock()) return false;
    if(!_wifi_settings.available || _wifi_settings.pending || _wifi_settings.restartPending) return false;
    bool exists=false;
    if(MosaicoWifi::validCredentials(ssid,"")) for(unsigned i=0;i<_wifi_settings.count;++i)
        if(!std::strcmp(ssid,_wifi_settings.names[i])) exists=true;
    if(!exists || _wifi_settings.count<=1) { _wifi_settings.error=ESP_ERR_INVALID_ARG; return false; }
    std::strcpy(_pending_ssid,ssid); MosaicoWifi::scrub(_pending_password,sizeof(_pending_password));
    _pending_forget=true; _wifi_settings.pending=true; _wifi_settings.error=0;
#if CONFIG_IDF_TARGET_ESP32S31
    ++_wifi_settings_generation;
#endif
    xTaskNotifyGive(_task_handle); return true;
}
void NetworkQuota::publishWifiProfiles()
{
    // Caller holds snapshot mutex; only the network owner changes private profiles.
    _wifi_settings.count=_wifi_profiles.count;
    _wifi_settings.currentIndex=255;
    std::memset(_wifi_settings.names,0,sizeof(_wifi_settings.names));
    for(unsigned i=0;i<_wifi_profiles.count;++i) {
        std::memcpy(_wifi_settings.names[i],_wifi_profiles.entries[i].ssid,33);
        if(!std::strcmp(_ssid,_wifi_profiles.entries[i].ssid)) _wifi_settings.currentIndex=i;
    }
}
bool NetworkQuota::requestWifiRestart()
{
    std::unique_lock<std::mutex> guard(_wifi_settings_mutex, std::try_to_lock);
    if (!guard.owns_lock()) return false;
    if (!_wifi_settings.available || _wifi_settings.pending || _wifi_settings.error || (!_wifi_settings.rebootRequired && !_wifi_settings.scanError)) return false;
#if CONFIG_IDF_TARGET_ESP32S31
    ++_wifi_settings_generation;
#endif
    _wifi_settings.restartPending=true; _wifi_settings.restartError=0; xTaskNotifyGive(_task_handle); return true;
}
void NetworkQuota::serviceWifiSettings()
{
    // Retain queued settings until the bounded gauge trial has safely restored;
    // a deferred reconnect/restart must not race its radio-off safety gate.
    if(GetHAL().gaugeScaleTrialActive())return;
    // Same sole-owner safety gate as display persistence. Never hold the GUI
    // cache mutex over NVS operations or restart, never rebuild live netifs.
    if (MosaicoOta::busy() || MosaicoOta::healthPending()) return;
    MosaicoOta::UiSnapshot ota{};
    if (!MosaicoOta::copyUiSnapshot(ota) ||
        (ota.imageVerified && ota.stage != MosaicoOta::UiStage::Complete) ||
        ota.stage==MosaicoOta::UiStage::ReadyReboot || ota.stage==MosaicoOta::UiStage::BootChecking) return;
    const esp_partition_t* running=esp_ota_get_running_partition();
    esp_ota_img_states_t state=ESP_OTA_IMG_UNDEFINED;
    if (!running || esp_ota_get_state_partition(running,&state)!=ESP_OK || state!=ESP_OTA_IMG_VALID) return;
    char ssid[33]{}, password[65]{};
    bool restart=false, forget=false;
#if CONFIG_IDF_TARGET_ESP32S31
    uint64_t generation=0;
    {
        std::unique_lock<std::mutex> guard(_wifi_settings_mutex,std::try_to_lock);
        if (!guard.owns_lock()) return; // No snapshot-derived fail-open admission.
        restart=_wifi_settings.restartPending && !_wifi_settings.pending && !_wifi_settings.error;
        if (!restart && !_wifi_settings.pending) return;
        generation=_wifi_settings_generation;
    }
    // SDK/socket operations never run under the settings mutex. A failed close
    // retains debt; neither NVS nor restart may pass this gate.
    if (!_lan_tcp.quiesce()) return;
#endif
    {
        std::unique_lock<std::mutex> guard(_wifi_settings_mutex,std::try_to_lock);
        if (!guard.owns_lock()) return;
#if CONFIG_IDF_TARGET_ESP32S31
        if (generation!=_wifi_settings_generation) return; // Queue changed while closing.
#endif
        restart=_wifi_settings.restartPending && !_wifi_settings.pending && !_wifi_settings.error;
        if (!restart && !_wifi_settings.pending) return;
        if (!restart) { forget=_pending_forget; std::memcpy(ssid,_pending_ssid,sizeof(ssid)); std::memcpy(password,_pending_password,sizeof(password)); }
    }
    if (restart) {
        MosaicoDisplay::Snapshot display;
        if (!MosaicoDisplay::snapshot(display)) return;
        if (display.error) {
            std::lock_guard<std::mutex> guard(_wifi_settings_mutex);
            _wifi_settings.restartPending=false;
            _wifi_settings.restartError=display.error;
            return;
        }
        if (!MosaicoDisplay::rebootSaveReady(display)) return;
        esp_restart(); return;
    }
    auto candidate=_wifi_profiles;
    const bool valid=forget ? candidate.forget(ssid) : candidate.upsert(ssid,password);
    auto blob=MosaicoWifiProfiles::encode(candidate);
    auto previous=MosaicoWifiProfiles::encode(_wifi_profiles);
    const bool changed=blob!=previous;
    nvs_handle_t h=0;
    esp_err_t error=valid ? ESP_OK : ESP_ERR_INVALID_ARG;
    if(valid && (changed || !_wifi_profiles_persisted)) {
        error=nvs_open("quota_net",NVS_READWRITE,&h);
        if(error==ESP_OK) {
            error=nvs_set_blob(h,"wifi_profiles",blob.data(),blob.size());
            if(error==ESP_OK) error=nvs_commit(h);
            nvs_close(h);
        }
    }
    MosaicoWifi::scrub(blob.data(),blob.size());
    MosaicoWifi::scrub(previous.data(),previous.size());
    MosaicoWifi::scrub(password,sizeof(password));
    std::lock_guard<std::mutex> guard(_wifi_settings_mutex);
    MosaicoWifi::scrub(_pending_password,sizeof(_pending_password));
    MosaicoWifi::scrub(_pending_ssid,sizeof(_pending_ssid));
    _wifi_settings.pending=false; _wifi_settings.error=error;
    if(!error) { cancelWifiScan(); _wifi_candidates={}; _wifi_scan_complete=true; _wifi_profiles_persisted=true; _wifi_profiles=candidate; publishWifiProfiles(); if(changed) _wifi_settings.rebootRequired=true; }
    MosaicoWifi::scrub(&candidate,sizeof(candidate));
}
#if SOC_WIFI_HE_SUPPORT
bool NetworkQuota::requestTwtKeepalive(bool enabled)
{
    if (!_task_handle || !idleLocked() || twtOtaBlocked()) return false;
    std::unique_lock<std::mutex> settingsGuard(_wifi_settings_mutex, std::try_to_lock);
    if (!settingsGuard.owns_lock() || _wifi_settings.pending || _wifi_settings.restartPending) return false;
    portENTER_CRITICAL(&_twt_mux);
    const bool allowed = !_twt_keepalive_busy && _twt_request.load() == -1 &&
        idleLocked() && !twtOtaBlocked();
    if (allowed) {
        _twt_keepalive.requested = enabled ? 1 : 0;
        _twt_keepalive_cache = _twt_keepalive;
    }
    portEXIT_CRITICAL(&_twt_mux);
    return allowed;
}
MosaicoTwtKeepalive::State NetworkQuota::twtKeepaliveSnapshot() const
{
    portENTER_CRITICAL(&_twt_mux);
    auto out = _twt_keepalive_cache;
    out.pending = _twt_keepalive_busy || _twt_request.load() != -1;
    portEXIT_CRITICAL(&_twt_mux);
    return out;
}
bool NetworkQuota::requestTwtTrial(MosaicoTwt::Mode mode, uint32_t leaseSeconds, MosaicoTwt::Profile profile)
{
    const int request = MosaicoTwt::encodeRequest(mode, leaseSeconds, profile);
    if (!_task_handle || !MosaicoTwt::validLease(leaseSeconds) || request < 0 ||
        (mode == MosaicoTwt::Mode::Off && profile != MosaicoTwt::Profile::Default)) return false;
    portENTER_CRITICAL(&_twt_mux);
    _twt_keepalive_busy = true;
    // Off always wins over a queued command; no Wi-Fi API on serial/GUI threads.
    if (mode == MosaicoTwt::Mode::Off) _twt_request.store(0);
    else { int empty = -1; if (!_twt_request.compare_exchange_strong(empty, request)) { portEXIT_CRITICAL(&_twt_mux); return false; } }
    portEXIT_CRITICAL(&_twt_mux);
    xTaskNotifyGive(_task_handle);
    return true;
}
void NetworkQuota::requestTwtObserve(bool enabled) { _twt_observe.store(enabled); }
bool NetworkQuota::popTwtCycle(MosaicoTwt::Cycle& cycle)
{
    portENTER_CRITICAL(&_twt_mux);
    const bool available = _twt_cycles.pop(cycle);
    portEXIT_CRITICAL(&_twt_mux);
    return available;
}
void NetworkQuota::reconcileTwtCycleIdentity()
{
    // Never carry an evidence window into another lease (including observe ID 0).
    const uint16_t currentId = _twt.live() ? _twt.state.id : 0;
    if (_twt_cycle_open && _twt_cycle_trial_id != currentId)
        recordTwtCycle(MosaicoTwt::CyclePhase::End, MosaicoTwt::CycleReason::Cancel);
}
void NetworkQuota::recordTwtCycle(MosaicoTwt::CyclePhase phase, MosaicoTwt::CycleReason reason, uint8_t flags)
{
    using namespace MosaicoTwt;
    if (phase == CyclePhase::Start) {
        if (_twt.state.stage == Stage::Armed) return; // Bootstrap is not a measured refresh cycle.
        if (!_twt_observe.load() && !_twt.live()) return;
        _twt_cycle_open = true; ++_twt_cycle_seq;
        _twt_cycle_trial_id = _twt.live() ? _twt.state.id : 0;
    } else if (!_twt_cycle_open) return;
    wifi_ap_record_t ap{};
    Cycle cycle{};
    cycle.phase = phase; cycle.reason = reason; cycle.deviceUs = esp_timer_get_time();
    cycle.trialId = _twt_cycle_trial_id; cycle.cycleSeq = _twt_cycle_seq;
    cycle.associated = _wifi_running && esp_wifi_sta_get_ap_info(&ap) == ESP_OK;
    cycle.fetchFlags = flags;
    portENTER_CRITICAL(&_twt_mux);
    _twt_cycles.push(cycle);
    portEXIT_CRITICAL(&_twt_mux);
    if (phase == CyclePhase::End) _twt_cycle_open = false;
}
MosaicoTwt::Snapshot NetworkQuota::twtSnapshot() const
{
    portENTER_CRITICAL(&_twt_mux);
    auto out = _twt_cache;
    portEXIT_CRITICAL(&_twt_mux);
    return out;
}
void NetworkQuota::publishTwt()
{
    portENTER_CRITICAL(&_twt_mux);
    _twt_cache = _twt.state;
    _twt_keepalive_busy = _twt_keepalive_owner_processing || _twt.live() || _twt.state.cleanupPending || _twt_cleanup_failed || _twt_request.load() != -1;
    _twt_keepalive_cache = _twt_keepalive;
    portEXIT_CRITICAL(&_twt_mux);
}
void NetworkQuota::twtEvent(void* arg, const char*, int32_t event, void* data)
{
    auto& owner = *static_cast<NetworkQuota*>(arg);
#if CONFIG_IDF_TARGET_ESP32S31
    if (event == WIFI_EVENT_ITWT_PROBE) {
        if (data) {
            const auto& e = *static_cast<const wifi_event_sta_itwt_probe_t*>(data);
            owner.captureTsfProbeEvent(static_cast<int>(e.status), e.reason, esp_timer_get_time());
        }
        return; // Do not wake the network loop or call any Wi-Fi API here.
    }
#endif
    TwtEvent copied{};
    if (event == WIFI_EVENT_ITWT_SETUP && data) {
        const auto& e = *static_cast<const wifi_event_sta_itwt_setup_t*>(data);
        copied.kind = 1;
        copied.setup.id = e.config.twt_id; copied.setup.flow = e.config.flow_id;
        copied.setup.mantissa = e.config.wake_invl_mant; copied.setup.exponent = e.config.wake_invl_expn;
        copied.setup.duration = e.config.min_wake_dura; copied.setup.unit = e.config.wake_duration_unit;
        copied.setup.trigger = e.config.trigger; copied.setup.flowType = e.config.flow_type;
        copied.setup.status = e.status; copied.setup.reason = e.reason; copied.setup.targetWakeUs = e.target_wake_time;
    } else if (event == WIFI_EVENT_STA_DISCONNECTED) {
        copied.kind = 2;
        copied.eventUs = esp_timer_get_time();
        if (data) {
            const auto& e = *static_cast<const wifi_event_sta_disconnected_t*>(data);
            copied.staReason = e.reason;
            copied.staReasonValid = true;
        }
    } else if (event == WIFI_EVENT_ITWT_TEARDOWN && data) {
        const auto& e = *static_cast<const wifi_event_sta_itwt_teardown_t*>(data);
        copied.kind = 3; copied.flow = e.flow_id; copied.setup.status = e.status;
    } else return; // No per-wake logging, APIs, allocations, NVS or HAL calls.
    portENTER_CRITICAL(&owner._twt_mux);
    const uint8_t next = (owner._twt_event_write + 1) % 8;
    if (next == owner._twt_event_read) owner._twt_event_overflow = true;
    else { owner._twt_events[owner._twt_event_write] = copied; owner._twt_event_write = next; }
    portEXIT_CRITICAL(&owner._twt_mux);
    if (owner._task_handle) xTaskNotifyGive(owner._task_handle);
}
void NetworkQuota::cancelTwtTrial(MosaicoTwt::Stop reason)
{
    _twt.end(reason);
    cleanupTwtTrial();
    publishTwt();
}
void NetworkQuota::cleanupTwtTrial()
{
    // S31 incident: teardown ESP_OK/bitmap0 did not mean TX callback drained.
    // SUCCESS is posted before he_twt_teardown_txcb's final accesses. In this
    // installed SDK, post-ack get_flow_id_status is a synchronous PP-task ioctl;
    // it runs after that same PP-task TX callback returns. Require BOTH proofs.
    // Never disconnect/stop a STA with a pending setup/teardown callback.
#if CONFIG_IDF_TARGET_ESP32S31
    if (!_lan_tcp.quiesce()) {
        _twt.state.cleanupPending = true;
        _twt.state.cleanupStage = 7; // Socket worker owns close; no driver teardown before it returns.
        _twt.state.cleanupDeadlineUs = 0;
        return;
    }
    if (_twt.state.cleanupStage == 7) {
        _twt.state.cleanupPending = false;
        _twt.state.cleanupStage = 0;
        _twt.state.cleanupDeadlineUs = 0;
    }
    const auto probe = tsfProbeSnapshot();
    if (probe.apiCalled && probe.apiResult == ESP_OK && !probe.driverDrained) {
        _twt.state.cleanupPending = true;
        _twt.state.cleanupStage = 6; // Explicit probe hold, not driver failure or cancellation.
        _twt.state.cleanupDeadlineUs = 0; // Cleanup countdown starts only after PP drain.
        return; // No teardown, PS restore, stop or speculative barrier before callback.
    }
    if (_twt.state.cleanupStage == 6) {
        _twt.state.cleanupPending = false;
        _twt.state.cleanupStage = 0;
        _twt.state.cleanupDeadlineUs = 0;
    }
#endif
    if (_twt_cleanup_failed) return;
    if (!_twt_submitted && !_twt_ps_saved && !_twt_cleanup_barrier_needed && !_twt_keepalive.restoreOwed) return;
    _twt.state.cleanupPending = true;
    const int64_t now = esp_timer_get_time();
    if (!_twt.state.cleanupDeadlineUs) _twt.state.cleanupDeadlineUs = now + 10LL * 1000000;
    if (now >= _twt.state.cleanupDeadlineUs) {
        _twt_cleanup_failed = _twt.state.cleanupFailed = true;
        _twt.state.cleanupStage = 5;
        return; // Keep radio and restoration debt; reboot/manual diagnosis only.
    }
    if (_twt_negotiation_pending) {
        _twt.state.cleanupStage = 1;
        return; // Await the original twt_id result; no teardown while setup TX pending.
    }
    if (_twt_submitted && !_twt_teardown_sent) {
        _twt.state.cleanupStage = 2;
        _twt_teardown_sent = true; // One submission per cleanup, even if API fails.
        _twt_teardown_ack = false;
        _twt.state.teardownError = esp_wifi_sta_itwt_teardown(FLOW_ID_ALL);
        return;
    }
    if (_twt_submitted && !_twt_teardown_ack) {
        _twt.state.cleanupStage = 2;
        return; // Bitmap0 alone can precede TX completion; no driver query yet.
    }
    if (_twt_submitted || _twt_cleanup_barrier_needed) {
        _twt.state.cleanupStage = 3;
        int bitmap = -1;
        const esp_err_t queried = esp_wifi_sta_itwt_get_flow_id_status(&bitmap);
        if (queried != ESP_OK || bitmap != 0) {
            _twt.state.teardownError = queried != ESP_OK ? queried : ESP_ERR_INVALID_STATE;
            _twt_cleanup_failed = _twt.state.cleanupFailed = true;
            _twt.state.cleanupStage = 5;
            return; // No stop fallback and no repeated ioctl/teardown attempts.
        }
        _twt_submitted = _twt_cleanup_barrier_needed = false;
        _twt_teardown_sent = _twt_teardown_ack = false;
    }
    if (_twt_keepalive.restoreOwed) {
        // A known public baseline, NOT a claim about the unknown SDK boot defaults.
        wifi_twt_config_t baseline{};
        baseline.post_wakeup_event = false;
        baseline.twt_enable_keep_alive = false;
        _twt_keepalive.restored(esp_wifi_sta_twt_config(&baseline));
        if (_twt_keepalive.restoreResult != ESP_OK) {
            _twt_cleanup_failed = _twt.state.cleanupFailed = true;
            _twt.state.cleanupStage = 8; // Config restoration debt: no retries/radio teardown.
            return;
        }
    }
    if (_twt_ps_saved) {
        _twt.state.cleanupStage = 4;
        ++_twt_cleanup_attempts;
        _twt.state.restoreError = esp_wifi_set_ps(static_cast<wifi_ps_type_t>(_twt_saved_ps));
        if (_twt.state.restoreError == ESP_OK) _twt_ps_saved = false;
        else if (_twt_cleanup_attempts >= 2) {
            _twt_cleanup_failed = _twt.state.cleanupFailed = true;
            _twt.state.cleanupStage = 5;
            return;
        }
    }
    _twt.state.cleanupPending = _twt_submitted || _twt_ps_saved || _twt_cleanup_barrier_needed || _twt_keepalive.restoreOwed;
    if (!_twt.state.cleanupPending) {
        _twt_cleanup_attempts = 0;
        _twt.state.cleanupDeadlineUs = 0;
        _twt.state.cleanupStage = 0;
    }
}
bool NetworkQuota::serviceTwtKeepaliveConfig()
{
    using namespace MosaicoTwt;
    if (_twt_keepalive.shouldApply()) {
        std::unique_lock<std::mutex> guard(_wifi_settings_mutex, std::try_to_lock);
        const bool allowed = guard.owns_lock() && !_wifi_settings.pending && !_wifi_settings.restartPending &&
            idleLocked() && !twtOtaBlocked() && _twt_request.load() == -1 &&
            !_twt.state.cleanupPending && !_twt_cleanup_failed &&
            _twt.state.stage == Stage::Armed && _twt.check(esp_timer_get_time(), twtOtaBlocked(), idleLocked());
        if (!allowed) {
            cancelTwtTrial(twtOtaBlocked() ? Stop::Ota : Stop::Explicit);
            return false;
        }
        wifi_twt_config_t diagnostic{};
        diagnostic.post_wakeup_event = false;
        diagnostic.twt_enable_keep_alive = _twt_keepalive.trialRequested != 0;
        _twt_keepalive.configured(esp_wifi_sta_twt_config(&diagnostic));
        if (_twt_keepalive.configResult != ESP_OK || twtOtaBlocked() || !idleLocked() || _twt_request.load() != -1) {
            _twt.state.error = _twt_keepalive.configResult;
            cancelTwtTrial(twtOtaBlocked() ? Stop::Ota : Stop::Driver);
            return false;
        }
    }
    return true;
}
void NetworkQuota::serviceTwtTrial(int64_t now, bool locked)
{
    using namespace MosaicoTwt;
    struct KeepaliveOwnerGuard {
        NetworkQuota& owner;
        explicit KeepaliveOwnerGuard(NetworkQuota& value) : owner(value) {
            portENTER_CRITICAL(&owner._twt_mux);
            owner._twt_keepalive_owner_processing = owner._twt_keepalive_busy = true;
            portEXIT_CRITICAL(&owner._twt_mux);
        }
        ~KeepaliveOwnerGuard() {
            portENTER_CRITICAL(&owner._twt_mux);
            owner._twt_keepalive_owner_processing = false;
            portEXIT_CRITICAL(&owner._twt_mux);
            owner.publishTwt(); // Release admission and publish final metadata on every return.
        }
    } keepaliveOwnerGuard(*this);
    reconcileTwtCycleIdentity();
    // Expiry/OTA/unlock wins over an already queued setup success.
    if (_twt.live() && !_twt.check(now, twtOtaBlocked(), locked))
        cancelTwtTrial(_twt.state.stop);
    bool overflow = false;
    portENTER_CRITICAL(&_twt_mux);
    overflow = _twt_event_overflow; _twt_event_overflow = false;
    portEXIT_CRITICAL(&_twt_mux);
    if (overflow) {
        // Lost callback identity cannot be reconstructed from a bitmap. Freeze
        // the owner without sending another frame or stopping the driver.
        _twt_cleanup_failed = _twt.state.cleanupFailed = true;
        _twt.state.cleanupPending = true; _twt.state.cleanupStage = 5;
        _twt.end(Stop::Lost);
    }
    while (true) {
        TwtEvent e{};
        portENTER_CRITICAL(&_twt_mux);
        const bool available = _twt_event_read != _twt_event_write;
        if (available) { e = _twt_events[_twt_event_read]; _twt_event_read = (_twt_event_read + 1) % 8; }
        portEXIT_CRITICAL(&_twt_mux);
        if (!available) break;
        if (e.kind == 1) {
            const bool current = _twt.state.stage == Stage::Negotiating && e.setup.id == _twt.state.id;
            const bool draining = _twt_negotiation_pending && e.setup.id == _twt.state.id;
            if (draining) {
                _twt_negotiation_pending = false;
                if (!current) {
                    _twt_cleanup_barrier_needed = true;
                    _twt_submitted = e.setup.status == 1;
                    _twt.state.actual = e.setup;
                }
            }
            const bool accepted = _twt.accept(e.setup);
            if (!accepted && current) {
                _twt_cleanup_barrier_needed = true;
                _twt_submitted = e.setup.status == 1;
                cancelTwtTrial(_twt.state.stop);
            }
            if (!current && e.setup.status == 1) {
                // SDK carries no request generation except echoed twt_id. Never stamp current generation.
                // A late accepted flow colliding with a live trial invalidates that trial too.
                _twt_submitted = true;
                cancelTwtTrial(Stop::Lost);
            }
        } else if (e.kind == 2) {
            // Latch even when check/cleanup already ended this trial. No callback model reads.
            _twt.recordStaDisconnect(e.staReason, e.eventUs, e.staReasonValid);
            if (_twt.live()) { ++_twt.state.losses; cancelTwtTrial(Stop::Lost); }
        } else if (e.kind == 3) {
            if (_twt_teardown_sent && e.flow == FLOW_ID_ALL) {
                if (e.setup.status == ITWT_TEARDOWN_SUCCESS) _twt_teardown_ack = true;
                else {
                    _twt_cleanup_failed = _twt.state.cleanupFailed = true;
                    _twt.state.cleanupPending = true; _twt.state.cleanupStage = 5;
                }
            } else if (e.setup.status == ITWT_TEARDOWN_SUCCESS && _twt.state.stage == Stage::Active &&
                       e.flow == _twt.state.actual.flow) {
                // AP initiated per-flow teardown: event + ioctl drain barrier,
                // no additional transmitted teardown that could race a stop.
                ++_twt.state.losses;
                _twt_submitted = false; _twt_cleanup_barrier_needed = true;
                cancelTwtTrial(Stop::Lost);
            }
        }
    }
    if (_twt.state.cleanupPending) cleanupTwtTrial();
    portENTER_CRITICAL(&_twt_mux);
    const int request = _twt_request.exchange(-1);
    if (request >= 0) _twt_keepalive_busy = true;
    portEXIT_CRITICAL(&_twt_mux);
    if (request >= 0) {
        cancelTwtTrial(Stop::Explicit);
        _twt_bootstrap_id = 0;
        _twt_bootstrap_attempted = _twt_bootstrap_ok = false;
        const auto mode = static_cast<Mode>(request & 3);
        const uint32_t leaseSeconds = request & 4 ? 1800 : 600;
        const auto profile = (request & 24) == 24 ? static_cast<Profile>(255) :
            request & 16 ? Profile::WideServiceWindow : request & 8 ? Profile::AnnouncedTrigger : Profile::Default;
        if (_twt.state.cleanupPending || _twt_cleanup_failed) {
            _twt.state.error = ESP_ERR_INVALID_STATE;
            _twt.end(Stop::Driver);
        } else if (encodeRequest(mode, leaseSeconds, profile) != request || !_twt.start(mode, now, leaseSeconds, profile)) { _twt.state.error = ESP_ERR_INVALID_STATE; _twt.end(Stop::Driver); }
        if (_twt.live()) _twt_keepalive.beginTrial(mode == Mode::On);
        if (_twt.live() && !locked) cancelTwtTrial(Stop::Awake);
        if (mode != Mode::Off && (!_twt_handler_ready || twtOtaBlocked()))
            cancelTwtTrial(_twt_handler_ready ? Stop::Ota : Stop::Driver);
    }
    if (_twt.live() && _wifi_running) {
        wifi_ap_record_t ap{};
        const bool associated = esp_wifi_sta_get_ap_info(&ap) == ESP_OK;
        _twt.state.associated = associated;
        const bool linkReady = associated && _connected;
        const bool controlReady = !GetTailnetQuota().enabled() || GetTailnetQuota().ready();
        _twt.state.controlReady = controlReady;
        // Control readiness gates bootstrap/HTTP, not ongoing Wi-Fi association.
        if (!linkReady && (_twt.state.stage != Stage::Armed ||
                           (_twt_bootstrap_attempted && _twt_bootstrap_id == _twt.state.id))) {
            ++_twt.state.losses; cancelTwtTrial(Stop::Lost);
        } else if (linkReady && controlReady && locked && _twt.state.stage == Stage::Armed &&
                   _twt_bootstrap_ok && _twt_bootstrap_id == _twt.state.id) {
            wifi_phy_mode_t phy{};
            _twt.state.apAx = ap.phy_11ax;
            const esp_err_t phyError = esp_wifi_sta_get_negotiated_phymode(&phy);
            _twt.state.phy = phyError == ESP_OK ? static_cast<int>(phy) : -1;
            if (_twt.state.requested == Mode::On && (phyError != ESP_OK || !ap.phy_11ax || phy != WIFI_PHY_MODE_HE20)) {
                _twt.state.error = phyError; cancelTwtTrial(Stop::Unsupported);
            } else {
                wifi_ps_type_t ps{};
                _twt.state.error = esp_wifi_get_ps(&ps);
                if (_twt.state.error == ESP_OK) {
                    _twt_saved_ps = static_cast<int>(ps); _twt_ps_saved = true;
                    _twt.state.error = esp_wifi_set_ps(WIFI_PS_MIN_MODEM);
                }
                if (_twt.state.error != ESP_OK) cancelTwtTrial(Stop::Driver);
                else if (_twt.state.requested == Mode::Baseline) {
                    if (!_twt.bootstrapReady(esp_timer_get_time())) cancelTwtTrial(_twt.state.stop);
                } else {
                    wifi_itwt_setup_config_t config{};
                    config.setup_cmd = TWT_SUGGEST;
                    const bool announced = _twt.state.profile == Profile::AnnouncedTrigger;
                    config.trigger = announced ? 1 : 0; config.flow_type = announced ? 0 : 1; config.flow_id = 0;
                    config.wake_invl_expn = 11; config.wake_invl_mant = 512;
                    // RAM-only discriminator: same interval/unit and unannounced non-trigger flow.
                    config.min_wake_dura = _twt.state.profile == Profile::WideServiceWindow ? 255 : 64;
                    config.wake_duration_unit = 0;
                    config.twt_id = _twt.state.id; config.timeout_time_ms = 5000;
                    if (!serviceTwtKeepaliveConfig()) { publishTwt(); return; }
                    if (!_twt.bootstrapReady(esp_timer_get_time())) {
                        cancelTwtTrial(_twt.state.stop);
                        publishTwt(); return;
                    }
                    if (_twt_keepalive.trialRequested >= 0 &&
                        (twtOtaBlocked() || !idleLocked() || _twt_request.load() != -1)) {
                        cancelTwtTrial(twtOtaBlocked() ? Stop::Ota : Stop::Explicit);
                        publishTwt(); return;
                    }
                    _twt_submitted = true;
                    _twt_negotiation_pending = true;
                    _twt.state.error = esp_wifi_sta_itwt_setup(&config);
                    if (_twt.state.error != ESP_OK) {
                        _twt_negotiation_pending = false;
                        _twt_submitted = false;
                        _twt_cleanup_barrier_needed = true;
                        cancelTwtTrial(Stop::Driver);
                    }
                }
            }
        }
    }
    reconcileTwtCycleIdentity();
    publishTwt();
}
bool NetworkQuota::serviceTwtBootstrap()
{
    using namespace MosaicoTwt;
    // Candidate causal fix: control-map readiness alone never proved the
    // DERP/WireGuard HTTP data path. Both arms prime it before changing PS.
    serviceTwtTrial(esp_timer_get_time(), idleLocked());
    if (_twt.state.stage != Stage::Armed || _twt.state.cleanupPending || _twt_cleanup_failed ||
        !_wifi_running || !_connected || !_twt.state.associated || !_twt.state.controlReady ||
        !idleLocked() || twtOtaBlocked() || _twt_bootstrap_attempted) return false;
    const uint16_t id = _twt.state.id;
    _twt_bootstrap_id = id;
    _twt_bootstrap_attempted = true; // Latch before HTTP; never retry this trial.
    const bool ok = fetch(); // Quota only, existing timeout; no history/cycle counters.
    ESP_LOGI("TwtBootstrap", "id=%u attempt=1 ok=%u", static_cast<unsigned>(id), static_cast<unsigned>(ok));
    // Consume Off/new request/disconnect, and recheck OTA/unlock/deadlines before
    // accepting success. This service cannot dispatch while bootstrap_ok=false.
    serviceTwtTrial(esp_timer_get_time(), idleLocked());
    if (_twt.state.id != id || _twt.state.stage != Stage::Armed ||
        _twt_bootstrap_id != id || !_twt_bootstrap_attempted) return true;
    if (!ok) cancelTwtTrial(Stop::Driver);
    else if (!_wifi_running || !_connected || !_twt.state.associated || !_twt.state.controlReady)
        cancelTwtTrial(Stop::Lost);
    else {
        _twt_bootstrap_ok = true;
        serviceTwtTrial(esp_timer_get_time(), idleLocked()); // Fresh cancellation checks before PS/setup.
    }
    return true; // Return to the owner loop even on cancellation; no periodic/history fetch here.
}
#endif
void NetworkQuota::setLowClockDiagnostic(bool enabled)
{
    if (enabled) _diagnostic_idle_mhz = 80; // legacy on/off means the original 80MHz experiment.
    _diagnostic_low_clock = enabled;
    _diagnostic_override = enabled;
    ESP_LOGW("DisplayClock", "diagnostic_low_clock=%d configured_idle_mhz=%lu", enabled,
             static_cast<unsigned long>(_idle_cpu_mhz.load()));
    if (_task_handle) xTaskNotifyGive(_task_handle);
}
void NetworkQuota::setDiagnosticIdleFrequency(uint32_t mhz)
{
    if (mhz != 80 && mhz != 160 && mhz != CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ) return;
    _diagnostic_idle_mhz = mhz;
    _diagnostic_low_clock = mhz != CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ;
    _diagnostic_override = true; // includes an explicit temporary 320MHz override.
    ESP_LOGW("DisplayClock", "diagnostic_idle_override_mhz=%lu", static_cast<unsigned long>(mhz));
    if (_task_handle) xTaskNotifyGive(_task_handle);
}
bool NetworkQuota::setIdleCpuFrequency(uint32_t mhz)
{
    if (mhz != 160 && mhz != 320) return false; // never persist the suspect 80MHz path.
    nvs_handle_t handle = 0;
    if (nvs_open("display_pw", NVS_READWRITE, &handle) != ESP_OK) return false;
    esp_err_t result = nvs_set_u32(handle, "idle_mhz", mhz);
    if (result == ESP_OK) result = nvs_commit(handle);
    nvs_close(handle);
    if (result != ESP_OK || nvs_open("display_pw", NVS_READONLY, &handle) != ESP_OK) return false;
    uint32_t observed = 0;
    result = nvs_get_u32(handle, "idle_mhz", &observed);
    nvs_close(handle);
    if (result != ESP_OK || observed != mhz) return false;
    _idle_cpu_mhz = mhz;
    _diagnostic_low_clock = false;
    _diagnostic_override = false;
    if (_task_handle) xTaskNotifyGive(_task_handle);
    return true;
}
#endif
void NetworkQuota::recordWifiRunning(bool running)
{
    const uint64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&_power_mux);
    if (!running && _wifi_running) _off_since_us = now;
    if (running && !_wifi_running && _off_since_us) {
        _off_completed_us += now - _off_since_us;
        _off_since_us = 0;
    }
    _wifi_running = running;
    portEXIT_CRITICAL(&_power_mux);
}
IdlePowerStats NetworkQuota::powerStats() const
{
    IdlePowerStats stats;
    stats.locked     = _locked;
    stats.profile    = _power_profile;
    stats.phase      = _power_phase;
    stats.cpuMHz     = esp_clk_cpu_freq() / 1000000;
    stats.cycles     = _power_cycles;
    stats.clockError = _clock_error;
    portENTER_CRITICAL(&_power_mux);
    stats.wifiRunning = _wifi_running;
    stats.offMs       = (_off_completed_us + (_off_since_us ? esp_timer_get_time() - _off_since_us : 0)) / 1000;
    portEXIT_CRITICAL(&_power_mux);
    return stats;
}
void NetworkQuota::setPhase(uint8_t phase)
{
    if (_power_phase.exchange(phase) != phase) {
        const auto stats = powerStats();
        ESP_LOGI("IdlePower", "phase=%u cpu_mhz=%lu wifi_running=%d cycles=%lu", phase,
                 static_cast<unsigned long>(stats.cpuMHz), stats.wifiRunning, static_cast<unsigned long>(stats.cycles));
    }
}

bool NetworkQuota::configure(const char* encoded)
{
    // USB-only provisioning: never print the input or credentials.
    unsigned char decoded[1024]{};
    size_t length = 0;
    if (!encoded || mbedtls_base64_decode(decoded, sizeof(decoded) - 1, &length,
                                          reinterpret_cast<const unsigned char*>(encoded), std::strlen(encoded)) != 0)
        return false;
    cJSON* root = cJSON_ParseWithLength(reinterpret_cast<char*>(decoded), length);
    char ssid[33]{}, password[65]{}, url[257]{}, token[129]{};
    const bool valid = root && copyString(root, "ssid", ssid, sizeof(ssid)) &&
                       copyString(root, "password", password, sizeof(password), true) &&
                       copyString(root, "url", url, sizeof(url)) && copyString(root, "token", token, sizeof(token)) &&
                       (std::strncmp(url, "http://", 7) == 0 || std::strncmp(url, "https://", 8) == 0);
    cJSON_Delete(root);
    std::memset(decoded, 0, sizeof(decoded));
#ifdef MOSAICO_BOARD
    if(!valid || !MosaicoWifi::validCredentials(ssid,password)) {
        MosaicoWifi::scrub(password,sizeof(password)); MosaicoWifi::scrub(token,sizeof(token)); return false;
    }
#else
    if (!valid) return false;
#endif
    nvs_handle_t handle;
    if (nvs_open("quota_net", NVS_READWRITE, &handle) != ESP_OK) {
        std::memset(password,0,sizeof(password)); std::memset(token,0,sizeof(token)); return false;
    }
#ifdef MOSAICO_BOARD
    MosaicoWifiProfiles::Model profiles{};
    MosaicoWifiProfiles::Blob profileBlob{}; size_t profileSize=profileBlob.size();
    const esp_err_t profileRead=nvs_get_blob(handle,"wifi_profiles",profileBlob.data(),&profileSize);
    if(profileRead==ESP_OK && profileSize==profileBlob.size())
        MosaicoWifiProfiles::decode(profileBlob,profiles);
    if(profileRead==ESP_ERR_NVS_NOT_FOUND) {
        char oldSsid[33]{},oldPassword[65]{};
        MosaicoWifi::Blob oldBlob{}; size_t oldSize=oldBlob.size();
        bool oldValid=nvs_get_blob(handle,"wifi_ui",oldBlob.data(),&oldSize)==ESP_OK && oldSize==oldBlob.size() && MosaicoWifi::decode(oldBlob,oldSsid,oldPassword);
        if(!oldValid) { size_t x=33,y=65;
            oldValid=nvs_get_str(handle,"ssid",oldSsid,&x)==ESP_OK && nvs_get_str(handle,"password",oldPassword,&y)==ESP_OK; }
        if(oldValid) profiles.upsert(oldSsid,oldPassword);
        MosaicoWifi::scrub(oldPassword,sizeof(oldPassword)); MosaicoWifi::scrub(oldBlob.data(),oldBlob.size());
    }
    const bool profileValid=profiles.upsert(ssid,password);
    if(!profileValid) {
        nvs_close(handle); MosaicoWifi::scrub(&profiles,sizeof(profiles)); MosaicoWifi::scrub(profileBlob.data(),profileBlob.size());
        MosaicoWifi::scrub(password,sizeof(password)); MosaicoWifi::scrub(token,sizeof(token)); return false;
    }
    profileBlob=MosaicoWifiProfiles::encode(profiles);
#endif
    bool ok = nvs_set_str(handle, "ssid", ssid) == ESP_OK && nvs_set_str(handle, "password", password) == ESP_OK &&
              nvs_set_str(handle, "url", url) == ESP_OK && nvs_set_str(handle, "token", token) == ESP_OK;
#ifdef MOSAICO_BOARD
    if(ok) ok=nvs_set_blob(handle,"wifi_profiles",profileBlob.data(),profileBlob.size())==ESP_OK;
    MosaicoWifi::scrub(profileBlob.data(),profileBlob.size()); MosaicoWifi::scrub(&profiles,sizeof(profiles));
    if (ok) {
        auto blob=MosaicoWifi::encode(ssid,password);
        ok=nvs_set_blob(handle,"wifi_ui",blob.data(),blob.size())==ESP_OK;
        MosaicoWifi::scrub(blob.data(),blob.size());
    }
#endif
    if (ok) ok=nvs_commit(handle)==ESP_OK;
    nvs_close(handle);
    std::memset(password, 0, sizeof(password));
    std::memset(token, 0, sizeof(token));
    return ok;
}

void NetworkQuota::begin()
{
    InitTokenHistory();
#ifdef MOSAICO_BOARD
    InitQuotaMonitor();
    // Loading this setting cannot enable an unsupported frequency. Diagnostics
    // are still RAM-only; choosing 160 requires the separate explicit command.
    nvs_handle_t displayHandle = 0;
    uint32_t savedIdle = 320;
    if (nvs_open("display_pw", NVS_READONLY, &displayHandle) == ESP_OK) {
        if (nvs_get_u32(displayHandle, "idle_mhz", &savedIdle) != ESP_OK ||
            (savedIdle != 160 && savedIdle != 320)) savedIdle = 320;
        nvs_close(displayHandle);
    }
    _idle_cpu_mhz = savedIdle;
#endif
    GetTailnetQuota().load();
    nvs_handle_t handle;
    if (nvs_open("quota_net", NVS_READONLY, &handle) != ESP_OK) {
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
    size_t a=sizeof(_ssid), b=sizeof(_password), c=sizeof(_url), d=sizeof(_token);
    const bool endpointOk=nvs_get_str(handle,"url",_url,&c)==ESP_OK && nvs_get_str(handle,"token",_token,&d)==ESP_OK;
    bool ok=nvs_get_str(handle,"ssid",_ssid,&a)==ESP_OK && nvs_get_str(handle,"password",_password,&b)==ESP_OK;
#ifdef MOSAICO_BOARD
    MosaicoWifiProfiles::Blob profilesBlob{}; size_t profilesSize=profilesBlob.size();
    const esp_err_t profilesRead=nvs_get_blob(handle,"wifi_profiles",profilesBlob.data(),&profilesSize);
    bool loaded=profilesRead==ESP_OK &&
        profilesSize==profilesBlob.size() && MosaicoWifiProfiles::decode(profilesBlob,_wifi_profiles);
    MosaicoWifi::scrub(profilesBlob.data(),profilesBlob.size());
    _wifi_profiles_persisted=loaded;
    if(profilesRead==ESP_ERR_NVS_NOT_FOUND) {
        MosaicoWifi::Blob wifiBlob{}; size_t wifiSize=wifiBlob.size();
        if(nvs_get_blob(handle,"wifi_ui",wifiBlob.data(),&wifiSize)==ESP_OK && wifiSize==wifiBlob.size())
            ok=MosaicoWifi::decode(wifiBlob,_ssid,_password) || ok;
        MosaicoWifi::scrub(wifiBlob.data(),wifiBlob.size());
        if(ok) loaded=_wifi_profiles.upsert(_ssid,_password); // Read-only migration; save new blob only on edit.
    }
    ok=loaded;
    if(!loaded) { MosaicoWifi::scrub(_ssid,sizeof(_ssid)); MosaicoWifi::scrub(_password,sizeof(_password)); }
    if(ok) { const unsigned i=_wifi_profiles.lastSuccess<_wifi_profiles.count ? _wifi_profiles.lastSuccess : 0;
        std::memcpy(_ssid,_wifi_profiles.entries[i].ssid,33); std::memcpy(_password,_wifi_profiles.entries[i].password,65); }
#endif
#ifdef MOSAICO_BOARD
    ok=endpointOk; // Corrupt/missing Wi-Fi remains repairable by the live owner; no auto-connect.
#else
    ok=ok && endpointOk;
#endif
    nvs_close(handle);
#ifdef MOSAICO_BOARD
    { std::lock_guard<std::mutex> guard(_wifi_settings_mutex); std::memcpy(_wifi_settings.ssid,_ssid,sizeof(_ssid)); publishWifiProfiles(); }
#endif
    if (!ok || !_url[0] || !_token[0]
#ifndef MOSAICO_BOARD
        || !_ssid[0]
#endif
        ) {
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
    _configured = true;
    if (xTaskCreate(task, "quota_wifi", 8192, this, 2, &_task_handle) != pdPASS) {
        _configured = false;
        ++_failures;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
    }
}
void NetworkQuota::task(void* arg)
{
    static_cast<NetworkQuota*>(arg)->run();
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
    static_cast<NetworkQuota*>(arg)->_hw_rx_owner_ready.store(false);
#endif
    vTaskDelete(nullptr);
}
void NetworkQuota::run()
{
#ifdef MOSAICO_BOARD
    // app_main sampled SDK pending before begin(). Only a pending boot is held;
    // its existing health loop/timer must run independently of network startup.
    if (MosaicoOta::healthPending()) {
        for (;;) {
            MosaicoOta::HealthDiagnostic health;
            MosaicoOta::copyHealthDiagnostic(health);
            if (health.markSucceeded) break;
            // Timeout clears bootPending before rollback; that is NOT VALID.
            ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(20));
        }
    }
#endif
    setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
    if (esp_netif_init() != ESP_OK) {
        ++_failures;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
    esp_err_t err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ++_failures;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
    if (!esp_netif_create_default_wifi_sta()) {
        ++_failures;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
#ifdef MOSAICO_BOARD
    // Credentials/configuration are owned by the application's NVS namespace.
    // WIFI_STORAGE_RAM is set only after init and cannot prevent init-time
    // wifi_nvs_load. Avoid that unused persistent Wi-Fi path entirely.
    init.nvs_enable = 0;
#endif
    // Quota traffic is small and infrequent; reserve less scarce DMA SRAM.
    init.static_tx_buf_num = 4;
    init.cache_tx_buf_num  = 8;
    wifi_config_t config{};
#ifdef MOSAICO_BOARD
    config.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    config.sta.pmf_cfg.capable = true;
#endif
    std::memcpy(config.sta.ssid, _ssid, std::strlen(_ssid));
    std::memcpy(config.sta.password, _password, std::strlen(_password));
    if (esp_wifi_init(&init) != ESP_OK || esp_wifi_set_storage(WIFI_STORAGE_RAM) != ESP_OK ||
        esp_wifi_set_mode(WIFI_MODE_STA) != ESP_OK ||
#ifdef MOSAICO_BOARD
        (_wifi_profiles.count && esp_wifi_set_config(WIFI_IF_STA, &config) != ESP_OK) ||
#else
        esp_wifi_set_config(WIFI_IF_STA, &config) != ESP_OK ||
#endif
#ifdef MOSAICO_BOARD
        (!GetHAL().gaugeScaleTrialActive() && esp_wifi_start() != ESP_OK)) {
#else
        esp_wifi_start() != ESP_OK) {
#endif
        ++_failures;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::startFallbackOwner();
#endif
        return;
    }
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
    _twt_handler_ready = esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &NetworkQuota::twtEvent, this) == ESP_OK;
#endif
#ifdef MOSAICO_BOARD
    _wifi_scan_handler=esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_SCAN_DONE,&NetworkQuota::scanEvent,this)==ESP_OK;
    MosaicoWifi::scrub(&config,sizeof(config));
#endif
#ifdef MOSAICO_BOARD
    recordWifiRunning(!GetHAL().gaugeScaleTrialActive());
#else
    recordWifiRunning(true);
#endif
    esp_sntp_config_t timeConfig = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
#ifdef MOSAICO_BOARD
    timeConfig.sync_cb = MosaicoClock::onSntpTime;
#endif
    const esp_err_t timeInit = esp_netif_sntp_init(&timeConfig);
    if (timeInit != ESP_OK) ESP_LOGW("SystemClock", "SNTP init failed: %s", esp_err_to_name(timeInit));
    bool hadConnection = false;
    bool wasLocked = false, updateWindow = false;
    auto closeWindow = [&](MosaicoTwt::CycleReason reason) {
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
        if (updateWindow) recordTwtCycle(MosaicoTwt::CyclePhase::End, reason);
#else
        (void)reason;
#endif
        updateWindow = false;
    };
    int64_t nextRefresh = 0, windowDeadline = 0;
    constexpr int64_t RefreshIntervalUs = 300LL * 1000000;
    constexpr int64_t UpdateWindowUs    = 90LL * 1000000;
#ifdef MOSAICO_BOARD
    const bool ownsDisplaySettings = MosaicoDisplay::claimNetworkOwner();
    { std::lock_guard<std::mutex> guard(_wifi_settings_mutex);
      std::memcpy(_wifi_settings.ssid,_ssid,sizeof(_ssid)); publishWifiProfiles(); _wifi_settings.available=true; }
    uint32_t refreshIntervalMs=300000;
    int64_t refreshBaseUs=0;
    int64_t nextGaugeCheckUs = 0;
    MosaicoWifiRetry::Policy wifiRetry;
    MosaicoFirstQuotaRefresh::Policy firstQuotaRefresh;
    unsigned scanHeapBefore = 0, scanHeapAfter = 0, tailnetHeapBefore = 0, tailnetHeapAfter = 0;

#endif
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
    _hw_rx_owner_ready.store(true); // All startup gates have completed.
#endif
    while (true) {
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
        serviceHwRxSnapshot();
#endif
#ifdef MOSAICO_BOARD
        if (MosaicoOta::busy())
            mosaico_rx_observer::request(false);
        mosaico_rx_observer::service(!MosaicoOta::busy());
        mosaico_wifi_rx_observer::service(!MosaicoOta::blocksTwt());
        mosaico_rx_ingress::service(!MosaicoOta::blocksTwt());
        drainWifiScan();
#if SOC_WIFI_HE_SUPPORT
        if(_wifi_scan_started && (_twt.live() || _twt_request.load()!=-1)) {
            cancelWifiScan(); drainWifiScan();
        }
        if(_wifi_scan_fault && _twt_request.exchange(-1)!=-1) {
            _twt.state.error=ESP_ERR_TIMEOUT; _twt.end(MosaicoTwt::Stop::Timeout); publishTwt();
        }
        if(!_wifi_scan_started && !_wifi_scan_fault) serviceTwtTrial(esp_timer_get_time(), idleLocked());
        // Scan-fault paths can change identity without servicing the trial.
        reconcileTwtCycleIdentity();
#if CONFIG_IDF_TARGET_ESP32S31
        serviceLanTcp();
        serviceTsfProbe(); // Cancellation/events reconcile before probe, even during cleanup hold.
#endif
        if (_twt.state.cleanupPending || _twt_cleanup_failed) {
            if (MosaicoOta::busy()) MosaicoOta::fail("twt_cleanup_not_ready");
            setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
            setPhase(4);
            wait((_twt_cleanup_failed || (_twt.state.cleanupStage == 6 || _twt.state.cleanupStage == 7)) ? 1000 : 50);
            continue; // No OTA, radio stop, restart or new network request while TX is uncertain.
        }
#endif
        if (ownsDisplaySettings) MosaicoDisplay::service();
        serviceWifiSettings();
#if SOC_WIFI_HE_SUPPORT
        if (twtOtaBlocked() && _twt.live()) cancelTwtTrial(MosaicoTwt::Stop::Ota);
        if (_twt.state.cleanupPending || _twt_cleanup_failed) {
            if (MosaicoOta::busy()) MosaicoOta::fail("twt_cleanup_not_ready");
            wait(1000); continue; // A late OTA cancellation must not fall into radio stop.
        } else
#endif
            MosaicoOta::processLocalRequests(); // INSTALL/REBOOT do not require network readiness.
        if (MosaicoOta::busy() && MosaicoOta::requestAgeMs() > 120000)
            MosaicoOta::fail("network_ready_timeout");
#endif
        const bool gaugeHold =
#ifdef MOSAICO_BOARD
            GetHAL().gaugeScaleTrialActive();
#else
            false;
#endif
        const bool locked = idleLocked() || gaugeHold;
        const int64_t now = esp_timer_get_time();
        int64_t refreshIntervalUs=RefreshIntervalUs;
        int64_t updateWindowUs=UpdateWindowUs;
#ifdef MOSAICO_BOARD
        MosaicoDisplay::Snapshot display;
        if (MosaicoDisplay::snapshot(display)) {
            const uint32_t interval=MosaicoDisplay::lockIntervalMs(display.effectiveConfig.lockWifiMinutes,5);
            if (interval!=refreshIntervalMs) { refreshIntervalMs=interval; nextRefresh=refreshBaseUs+int64_t(interval)*1000; }
        }
        refreshIntervalUs=int64_t(refreshIntervalMs)*1000;
        updateWindowUs=std::min<int64_t>(UpdateWindowUs, refreshIntervalUs*3/4);
        if (updateWindow) windowDeadline=std::min(windowDeadline,refreshBaseUs+updateWindowUs);
        // Network/OTA can close the lease, never reopen a stale GUI request.
        // Main-loop Sessions service is the sole advertising-enable owner.
        if (!MosaicoSessions::enabled() || MosaicoOta::busy() || MosaicoOta::healthPending() || gaugeHold)
            GetCodexMicroBle().requestRadioIdle(true);
#else
        GetCodexMicroBle().requestRadioIdle(locked);
#endif
#ifdef MOSAICO_BOARD
        bool firstQuotaBootstrap = false;
#endif
        if (locked && !wasLocked) {
#ifdef MOSAICO_BOARD
            // Consume once per owner boot, including already accepted or live-TWT cases.
            firstQuotaBootstrap = firstQuotaRefresh.takeOnLock(_accepted != 0);
#if SOC_WIFI_HE_SUPPORT
            firstQuotaBootstrap = firstQuotaBootstrap && !_twt.live();
#endif
            nextRefresh = firstQuotaBootstrap ? now : now + refreshIntervalUs;
#else
            nextRefresh  = now + refreshIntervalUs;
#endif
#ifdef MOSAICO_BOARD
            refreshBaseUs=now;
#endif
            closeWindow(MosaicoTwt::CycleReason::Cancel);
        }
#ifdef MOSAICO_BOARD
        if (!locked && wasLocked) { wifiRetry.reset(); _wifi_scan_complete=false; } // Explicit wake gets a fresh attempt.
#endif
        wasLocked = locked;
        if(gaugeHold)closeWindow(MosaicoTwt::CycleReason::Cancel);
        if (locked && !gaugeHold && !updateWindow && (_force_refresh.exchange(false) || now >= nextRefresh)) {
            updateWindow   = true;
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
            recordTwtCycle(MosaicoTwt::CyclePhase::Start);
#endif
#ifdef MOSAICO_BOARD
            wifiRetry.reset(); _wifi_scan_complete=false; // New configured refresh window, not a task notification.
#endif
            windowDeadline = now + updateWindowUs;
#ifdef MOSAICO_BOARD
            if (firstQuotaBootstrap)
                ESP_LOGI("QuotaStartup", "reason=first_quota_pending budget_ms=%u",
                    static_cast<unsigned>(updateWindowUs / 1000));
#endif
            nextRefresh    = now + refreshIntervalUs;
#ifdef MOSAICO_BOARD
            refreshBaseUs=now;
#endif
        }
        if (locked && updateWindow && now >= windowDeadline) closeWindow(MosaicoTwt::CycleReason::Deadline);
        if (locked && !updateWindow) {
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
            // Hold association/tailnet, but never fetch outside the existing refresh window.
            // Armed may bootstrap via the normal owner path, bounded by 60s.
            const bool trialHold = _twt.live();
            if (trialHold && _twt.state.stage != MosaicoTwt::Stage::Armed &&
                _wifi_running && _twt.state.associated && _connected &&
                (!GetTailnetQuota().enabled() || GetTailnetQuota().ready())) {
                setCpu(_power_profile == 2 ? 80 : CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
                setPhase(2);
                wait(static_cast<uint32_t>(std::max<int64_t>(1, (nextRefresh - esp_timer_get_time()) / 1000)));
                continue;
            }
            if (!trialHold) {
#endif
            if (_wifi_running) {
                setPhase(1);
                if (!GetTailnetQuota().pause()) {
                    setPhase(4);
                    wait(1000);
                    continue;
                }
                if (!idleLocked()&&!gaugeHold) continue;
#ifdef MOSAICO_BOARD
                cancelWifiScan(); drainWifiScan();
#endif
#ifdef MOSAICO_BOARD
                mosaico_rx_ingress::request(false);
                mosaico_rx_ingress::service(false);
                mosaico_wifi_rx_observer::request(false);
                mosaico_wifi_rx_observer::service(false);
                const auto wifiRxStop=mosaico_wifi_rx_observer::snapshot();
                // Filter rollback can retry after stop; only an unverified radio disable holds normal idle.
                if (!mosaico_wifi_rx_observer::detail::canStopRadio(wifiRxStop)) { wait(1000); continue; }
#endif
#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31
                if (!_lan_tcp.quiesce()) { wait(100); continue; }
#endif
                esp_wifi_disconnect();
                if (esp_wifi_stop() != ESP_OK) {
                    setPhase(4);
                    wait(1000);
                    continue;
                }
                recordWifiRunning(false);
                _connected    = false;
                hadConnection = false;
            }
            if (!idleLocked()&&!gaugeHold) continue;
            const auto ble = GetCodexMicroBle().diagnostics();
            if (GetCodexMicroBle().connected() || ble.advertising) {
                setPhase(1);
                wait(100);
                continue;
            }
            setCpu(_power_profile == 2 ? 80 : CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
            setPhase(2);
#ifdef MOSAICO_BOARD
            // Deferred preconditions get read-only checks, at most once/minute,
            // while radios are off. A real attempt is latched by the HAL; failed
            // access/configuration must never become an automatic retry loop.
            if (GetHAL().gaugeBootReloadInfo().status == Hal::GaugeBootReloadStatus::Deferred &&
                esp_timer_get_time() >= nextGaugeCheckUs) {
                char reason[128]{};
                const auto status = GetHAL().gaugeBootReload(reason, sizeof(reason));
                ESP_LOGI("GaugeBoot", "idle status=%u reason=%s", static_cast<unsigned>(status), reason);
                nextGaugeCheckUs = esp_timer_get_time() + 60LL * 1000000;
            }
#endif
            const int64_t remaining = (nextRefresh - esp_timer_get_time()) / 1000;
            uint32_t waitMs = static_cast<uint32_t>(remaining > 0 ? remaining : 1);
            if(gaugeHold)waitMs=1000; // No busy loop when a refresh deadline passes during the bounded trial.
#ifdef MOSAICO_BOARD
            if (GetHAL().gaugeBootReloadInfo().status == Hal::GaugeBootReloadStatus::Deferred)
                waitMs = std::min<uint32_t>(waitMs, 60000);
#endif
            wait(waitMs);
            continue;
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
            }
#endif
        }
        setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
        if (!_wifi_running) {
#ifdef MOSAICO_BOARD
            // Same mutex as gauge mutation, but queued OTA must be able to
            // start its own network. Real gauge Pending remains checked below.
            struct RadioStartLease {bool held=MosaicoOta::tryNetworkStartLease();~RadioStartLease(){if(held)MosaicoOta::releaseStorageLease();}} radioLease;
            if(!radioLease.held||GetHAL().gaugeScaleTrialActive()){wait(100);continue;}
#endif
            if (esp_wifi_start() != ESP_OK) {
                setPhase(4);
                wait(1000);
                continue;
            }
            recordWifiRunning(true);
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
            hadConnection = false; // Cleanup may have stopped Wi-Fi before the usual pause path.
#endif
        }
        setPhase(locked ? 3 : 0);
        if (!locked) closeWindow(MosaicoTwt::CycleReason::Awake);
        wifi_ap_record_t ap{};
        esp_netif_ip_info_t ip{};
        esp_netif_t* netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
        _connected = esp_wifi_sta_get_ap_info(&ap) == ESP_OK && netif && esp_netif_get_ip_info(netif, &ip) == ESP_OK &&
                     ip.ip.addr != 0;
        if (!_connected) {
            hadConnection = false;
#ifdef MOSAICO_BOARD
            bool useRetryPolicy = true;
#if SOC_WIFI_HE_SUPPORT
            useRetryPolicy = !_twt.live(); // Do not inherit retry backoff into TWT's finite bootstrap owner.
#endif
            if (useRetryPolicy) {
            const int64_t retryNow = esp_timer_get_time();
            if (wifiRetry.exhausted(locked) && !wifiRetry.remainingMs(retryNow)) {
                ++_wifi_budget_closures;
                closeWindow(MosaicoTwt::CycleReason::RetryBudget);
                continue; // Existing pause/disconnect/stop path; keep nextRefresh unchanged.
            }
            const uint32_t remaining = wifiRetry.remainingMs(retryNow);
            if (remaining) { wait(std::min<uint32_t>(remaining, 5000)); continue; }
            const bool scanReady = _wifi_scan_started && !_wifi_scan_cancelled && _wifi_scan_done && !_wifi_scan_status;
            if (scanReady) sampleNetworkHeap("scan_ready_before", MosaicoHeapProbeStage::ScanBefore, scanHeapBefore);
            const bool selectedCandidate = selectWifiCandidate(locked,retryNow);
            if (scanReady) sampleNetworkHeap("scan_ready_after", MosaicoHeapProbeStage::ScanAfter, scanHeapAfter);
            if(!selectedCandidate) {
                if(_wifi_scan_complete) {
                    if(locked) closeWindow(MosaicoTwt::CycleReason::NoCandidate);
                    else { wifiRetry.attempted(retryNow,false); _wifi_scan_complete=false; }
                }
                wait(100); continue;
            }
            ++_wifi_connect_attempts;
            wifiRetry.attempted(retryNow, locked);
            }
#endif
            esp_wifi_connect();
            wait(5000);
            continue;
        }
#ifdef MOSAICO_BOARD
        if(!hadConnection) {
            const int i=_wifi_profiles.find(_ssid); if(i>=0) _wifi_profiles.lastSuccess=i;
            std::lock_guard<std::mutex> guard(_wifi_settings_mutex);
            std::memcpy(_wifi_settings.ssid,_ssid,sizeof(_ssid)); publishWifiProfiles();
        }
        if(!locked) wifiRetry.reset(); // Awake recovery clears backoff; locked total attempts remain window-scoped.
#endif
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
        // Armed must allow DHCP/clock/tailnet bootstrap. Only dispatched
        // negotiation forbids blocking work while its response is pending.
        if (_twt.state.stage == MosaicoTwt::Stage::Negotiating) {
            wait(50); continue;
        }
#endif
        if (GetTailnetQuota().enabled()) {
            // A real clock is required before certificate verification.
            if (std::time(nullptr) < 1735689600) {
                wait(5000);
                continue;
            }
#ifdef MOSAICO_BOARD
            sampleNetworkHeap("tailnet_start_before", MosaicoHeapProbeStage::TailnetBefore, tailnetHeapBefore);
#endif
            GetTailnetQuota().start();
#ifdef MOSAICO_BOARD
            sampleNetworkHeap("tailnet_start_after", MosaicoHeapProbeStage::TailnetAfter, tailnetHeapAfter);
#endif
            // start() resumes a paused context; rebind is only for live link loss.
            if (!hadConnection && GetTailnetQuota().ready()) GetTailnetQuota().rebind();
        }
        hadConnection = true;
        if (GetTailnetQuota().enabled() && !GetTailnetQuota().ready()) {
            wait(5000);
            continue;
        }
#ifdef MOSAICO_BOARD
        // Reuse an already-online quota window. Do not wake radios just for OTA.
        if (MosaicoOta::takeCheckRequest() || MosaicoOta::automaticCheckDue()) {
            mosaico_rx_observer::request(false);
            mosaico_rx_observer::service(false);
            mosaico_rx_ingress::request(false);
            mosaico_rx_ingress::service(false);
            mosaico_wifi_rx_observer::request(false);
            mosaico_wifi_rx_observer::service(false);
#if SOC_WIFI_HE_SUPPORT
            cancelTwtTrial(MosaicoTwt::Stop::Ota);
            if (_twt.state.cleanupPending || _twt_cleanup_failed || !_wifi_running || !_connected) {
                MosaicoOta::finishCheck(false);
                wait(1000); continue;
            }
#endif
            std::unique_ptr<char[]> manifest(new (std::nothrow) char[2048]);
            int manifestSize = 0;
            const bool downloaded = manifest && requestJson("/v1/ota/manifest", manifest.get(), 2048, manifestSize);
            if (downloaded) MosaicoOta::discoverManifest(manifest.get());
            MosaicoOta::finishCheck(downloaded);
        }
        if (MosaicoOta::takeRequest()) {
            mosaico_rx_observer::request(false);
            mosaico_rx_observer::service(false);
            mosaico_rx_ingress::request(false);
            mosaico_rx_ingress::service(false);
            mosaico_wifi_rx_observer::request(false);
            mosaico_wifi_rx_observer::service(false);
            updateFirmware();

            continue;
        }
#endif
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
        // Dispatch only after normal bootstrap has completed, before HTTP.
        serviceTwtTrial(esp_timer_get_time(), idleLocked());
        if (_twt.state.stage == MosaicoTwt::Stage::Armed) {
            closeWindow(MosaicoTwt::CycleReason::Cancel); // No bootstrap HTTP in an energy window.
            if (serviceTwtBootstrap()) continue;
        }
        if (_twt.state.cleanupPending || _twt_cleanup_failed ||
            _twt.state.stage == MosaicoTwt::Stage::Armed || _twt.state.stage == MosaicoTwt::Stage::Negotiating) {
            wait(50); continue;
        }
#endif
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
        // A late Off/unlock/expiry/timeout may end the trial above. The
        // locked refresh-window boundary must not depend on trial liveness;
        // return to the owner loop for normal idle/stop or awake recovery.
        if (locked && !updateWindow) continue;
        const int64_t twtFetchStart = esp_timer_get_time();
        const bool twtMeasure = _twt.live();
        const uint16_t twtFetchId = _twt.state.id;
#endif
#ifdef MOSAICO_BOARD
        const MosaicoQuota::RequestPolicy requestPolicy{displayLocked()};
#endif
        const bool quotaOk = fetch();
        if (quotaOk) {
#ifdef MOSAICO_BOARD
            if (_accepted == 0)
                ESP_LOGI("QuotaStartup", "reason=first_quota_accepted boot_elapsed_ms=%llu",
                    static_cast<unsigned long long>(esp_timer_get_time() / 1000));
#endif
            ++_accepted;
        } else
            ++_failures;
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
        // Process queued off/unlock/OTA/expiry between bounded requests, not
        // only after both quota and history transport timeouts have elapsed.
        serviceTwtTrial(esp_timer_get_time(), idleLocked());
        const bool twtHistoryAllowed = _wifi_running && !_twt.state.cleanupPending && !_twt_cleanup_failed &&
            _twt.state.stage != MosaicoTwt::Stage::Armed && _twt.state.stage != MosaicoTwt::Stage::Negotiating &&
            (!twtMeasure || (_twt.live() && _twt.state.id == twtFetchId));
#endif
        // Re-read the authoritative UI lock after quota and trial servicing:
        // a newly locked view must not start the larger history transfer.
#ifdef MOSAICO_BOARD
        const bool historyRequired = requestPolicy.historyRequired(displayLocked());
#else
        const bool historyRequired = true;
#endif
        bool historyOk = false, historyAttempted = false;
        if (historyRequired && (!GetTailnetQuota().enabled() || GetTailnetQuota().ready())
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
            && twtHistoryAllowed
#endif
            ) {
            historyAttempted = true;
            historyOk = fetchHistory();
            if (historyOk)
                ++_history_accepted;
            else
                ++_history_failures;
        }
#ifdef MOSAICO_BOARD
        const bool requestsOk = MosaicoQuota::requestsSucceeded(quotaOk, historyRequired, historyOk);
#else
        const bool requestsOk = quotaOk && historyOk;
#endif
#if defined(MOSAICO_BOARD) && SOC_WIFI_HE_SUPPORT
        recordTwtCycle(MosaicoTwt::CyclePhase::Fetch, MosaicoTwt::CycleReason::None,
            1 | (quotaOk ? 2 : 0) | (historyAttempted ? 4 : 0) | (historyOk ? 8 : 0));
        serviceTwtTrial(esp_timer_get_time(), idleLocked()); // Also before the next wait/idle transition.
        if (twtMeasure && _twt.state.id == twtFetchId && _twt.live()) {
            ++_twt.state.fetchAttempts;
            if (requestsOk) ++_twt.state.fetchOk;
            _twt.state.fetchUs += static_cast<uint64_t>(esp_timer_get_time() - twtFetchStart);
            publishTwt();
        }
#endif
        if (locked && requestsOk
#ifdef MOSAICO_BOARD
            && updateWindow && idleLocked()
#endif
            ) {
            ++_power_cycles;
            closeWindow(MosaicoTwt::CycleReason::Success);
            continue;
        }
        wait(locked ? 5000 : 60000);
    }
}
bool NetworkQuota::requestJson(const char* path, char* body, size_t capacity, int& used)
{
    used = 0;
    if (!body || capacity < 2) return false;
    body[0] = 0;
    bool ok = false;
    if (GetTailnetQuota().enabled()) {
        // Fail closed: an enabled tailnet never falls back to LAN cleartext.
        ok = GetTailnetQuota().fetch(_token, body, capacity, used, path);
    } else {
        char requestUrl[288]{};
        std::strcpy(requestUrl, _url);
        if (path) {
            const char* scheme = std::strstr(_url, "://");
            if (!scheme) return false;
            const char* slash   = std::strchr(scheme + 3, '/');
            const size_t origin = slash ? static_cast<size_t>(slash - _url) : std::strlen(_url);
            if (origin + std::strlen(path) >= sizeof(requestUrl)) return false;
            requestUrl[origin] = 0;
            std::strcat(requestUrl, path);
        }
        esp_http_client_config_t config{};
        config.url                      = requestUrl;
        config.timeout_ms               = 7000;
        config.crt_bundle_attach        = esp_crt_bundle_attach;
        config.disable_auto_redirect    = true;
        esp_http_client_handle_t client = esp_http_client_init(&config);
        if (!client) return false;
        char authorization[136] = "Bearer ";
        std::strcat(authorization, _token);
        esp_http_client_set_header(client, "Authorization", authorization);
        esp_http_client_set_header(client, "Accept", "application/json");
        ok = esp_http_client_open(client, 0) == ESP_OK;
        if (ok) {
            esp_http_client_fetch_headers(client);
            ok = esp_http_client_get_status_code(client) == 200;
        }
        while (ok && used < static_cast<int>(capacity) - 1) {
            int count = esp_http_client_read(client, body + used, capacity - 1 - used);
            if (count < 0) {
                ok = false;
                break;
            }
            if (!count) break;
            used += count;
        }
        ok = ok && esp_http_client_is_complete_data_received(client);
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        std::memset(authorization, 0, sizeof(authorization));
    }
    body[used] = 0;
    return ok;
}
bool NetworkQuota::fetchHistory()
{
    std::unique_ptr<char[]> body(new (std::nothrow) char[32769]);
    int used = 0;
#ifdef MOSAICO_BOARD
    const char* path = "/v2/history";
#else
    const char* path = "/v1/history";
#endif
    return body && requestJson(path, body.get(), 32769, used) && ApplyTokenHistory(body.get(), used);
}
bool NetworkQuota::fetch()
{
#ifdef MOSAICO_BOARD
    std::unique_ptr<char[]> body(new (std::nothrow) char[8193]);
    int used = 0;
    const uint32_t now = static_cast<uint32_t>(esp_timer_get_time() / 1000);
    if (!body || !requestJson("/v2/status", body.get(), 8193, used) ||
        !ApplyQuotaMonitor(body.get(), used, now)) return false;
    // Keep legacy diagnostics populated from the canonical bucket, without
    // collapsing the independently stored monitor's buckets/windows.
    std::unique_ptr<QuotaMonitorSnapshot> snapshot(new (std::nothrow) QuotaMonitorSnapshot());
    if (!snapshot || !CopyQuotaMonitor(*snapshot, now)) return false;
    for (std::size_t i = 0; i < snapshot->bucketCount; ++i) {
        const auto& bucket = snapshot->buckets[i];
        if (std::strcmp(bucket.id, "codex")) continue;
        const QuotaMonitorWindow* selected = nullptr;
        for (const auto& window : bucket.windows)
            if (window.available && (!selected || window.remainingBasisPoints < selected->remainingBasisPoints ||
                (window.remainingBasisPoints == selected->remainingBasisPoints && window.durationMinutes > selected->durationMinutes)))
                selected = &window;
        if (selected) GetHostBridge().applyNetworkUsage(selected->remainingBasisPoints, selected->resetEpoch,
            snapshot->capturedEpoch, static_cast<uint8_t>(std::min<uint16_t>(snapshot->resetCredits, 99)),
            now - snapshot->ageSecondsAtReceipt * 1000U);
        break;
    }
    return true;
#else
    char body[2048]{};
    int used = 0;
    if (!requestJson(nullptr, body, sizeof(body), used) || !JsonNestingValid(body, used)) return false;
    cJSON* root      = cJSON_ParseWithLength(body, used);
    uint32_t version = 0, remaining = 0, reset = 0, captured = 0, credits = 0, age = 0;
    bool ok = root && cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root, "available")) &&
              number(root, "version", 1, 1, version) && number(root, "remaining_bp", 0, 10000, remaining) &&
              number(root, "reset_epoch", 0, UINT32_MAX, reset) &&
              number(root, "captured_epoch", 1, UINT32_MAX, captured) &&
              number(root, "reset_credits", 0, 99, credits) && number(root, "age_seconds", 0, 120, age);
    cJSON_Delete(root);
    if (!ok) return false;
    return GetHostBridge().applyNetworkUsage(remaining, reset, captured, credits,
                                             static_cast<uint32_t>(esp_timer_get_time() / 1000) - age * 1000);
#endif
}

#ifdef MOSAICO_BOARD
void NetworkQuota::updateFirmware()
{
    mosaico_rx_ingress::request(false);
    mosaico_rx_ingress::service(false);
    mosaico_wifi_rx_observer::request(false);
    mosaico_wifi_rx_observer::service(false);
    if (mosaico_wifi_rx_observer::snapshot().cleanupPending) {
        MosaicoOta::fail("wifi_rx_cleanup_not_ready"); return;
    }
#if SOC_WIFI_HE_SUPPORT
    cancelTwtTrial(MosaicoTwt::Stop::Ota); // Before any manifest/chunk transfer.
    if (_twt.state.cleanupPending || _twt_cleanup_failed || !_wifi_running || !_connected) {
        MosaicoOta::fail("twt_cleanup_not_ready"); return;
    }
#endif
    struct TraceScope { ~TraceScope() { MosaicoOtaBreadcrumb(0, 0); } } traceScope;
    MosaicoOtaBreadcrumb(1, 0); // reserved-buffer check and initial heap probe
    // Probe only during OTA, not normal/locked operation or every GUI frame.
    if (![&]() { MosaicoHeapProbeScope probe(MosaicoHeapProbeStage::DownloadBefore); return heap_caps_check_integrity_all(true); }()) { MosaicoOta::fail("heap_before_ota"); return; }
    // Only the network owner runs this path: no parallel quota HTTP, tailnet
    // pause, idle downclock or deferred gauge reload can race the transfer.
    constexpr size_t BodyCapacity=MosaicoOtaBuffer::BodyCapacity, ChunkCapacity=MosaicoOtaBuffer::ChunkCapacity;
    const auto buffers=otaBuffers.acquire(otaBufferAllocator); // No allocation here.
    if(!buffers){
        ESP_LOGW("NetworkOTA", "boot_buffers_unavailable error=%s",MosaicoOtaBuffer::errorName(otaBuffers.error()));
        MosaicoOta::fail("ota_boot_buffers_unavailable");return;
    }
    char* const body=reinterpret_cast<char*>(buffers.body);
    uint8_t* const chunk=buffers.chunk;
    uint8_t* const chunkEnd=chunk+ChunkCapacity-1;
    ESP_LOGI("NetworkOTA", "flash source start=%p end=%p dram=%d/%d internal=%d/%d capacity=%u",
        static_cast<void*>(chunk),static_cast<void*>(chunkEnd),esp_ptr_in_dram(chunk),esp_ptr_in_dram(chunkEnd),
        esp_ptr_internal(chunk),esp_ptr_internal(chunkEnd),static_cast<unsigned>(ChunkCapacity));
    // Preserve the NOR source's runtime guards even after acquire's validation.
    if(!esp_ptr_in_dram(chunk)||!esp_ptr_in_dram(chunkEnd)||
       !esp_ptr_internal(chunk)||!esp_ptr_internal(chunkEnd)){
        MosaicoOta::fail("ota_buffer_not_internal");return;
    }
    // Persistent PSRAM JSON storage is never a NOR write source. Clear the full
    // body at both transfer boundaries; requestJson also resets byte 0 and used.
    struct BodyScope {char* p;~BodyScope(){std::memset(p,0,BodyCapacity);}} bodyScope{body};
    std::memset(body,0,BodyCapacity);
    int used = 0;
    MosaicoOtaBreadcrumb(2, 0); // manifest HTTP/parse
    if (!requestJson("/v1/ota/manifest", body, BodyCapacity, used)) {
        MosaicoOta::fail("manifest_download"); return;
    }
    cJSON* manifest = cJSON_ParseWithLength(body, static_cast<size_t>(used));
    const cJSON* sizeValue = manifest ? cJSON_GetObjectItemCaseSensitive(manifest, "size") : nullptr;
    char imageHash[65]{};
    const uint32_t imageCapacity = MosaicoOta::imageCapacity();
    const bool fieldsOk = imageCapacity > 0 && manifest && cJSON_IsNumber(sizeValue) &&
        sizeValue->valuedouble > 0 && sizeValue->valuedouble <= imageCapacity &&
        std::floor(sizeValue->valuedouble) == sizeValue->valuedouble &&
        copyString(manifest, "sha256", imageHash, sizeof(imageHash));
    const uint32_t size = fieldsOk ? static_cast<uint32_t>(sizeValue->valuedouble) : 0;
    cJSON_Delete(manifest);
    if (!fieldsOk) { MosaicoOta::fail("manifest_metadata"); return; }
    MosaicoOtaBreadcrumb(3, 0); // validation + inactive-slot erase
    if (!MosaicoOta::beginManifest(body)) return;
    const int64_t deadline = esp_timer_get_time() + 600LL * 1000000;
    for (uint32_t offset = 0; offset < size;) {
        MosaicoOtaBreadcrumb(4, offset); // probe before allocating HTTP/JSON
        if ((offset % 65536U) == 0 && ![&]() { MosaicoHeapProbeScope probe(MosaicoHeapProbeStage::DownloadChunkBoundary); return heap_caps_check_integrity_all(true); }()) {
            MosaicoOta::fail("heap_during_ota"); return;
        }
        if (esp_timer_get_time() >= deadline) { MosaicoOta::fail("download_timeout"); return; }
        char path[160]{};
        const int n = std::snprintf(path, sizeof(path), "/v1/ota/chunk?sha256=%s&offset=%lu",
                                  imageHash, static_cast<unsigned long>(offset));
        if (n <= 0 || n >= static_cast<int>(sizeof(path))) {
            MosaicoOta::fail("chunk_path"); return;
        }
        bool downloaded = false;
        MosaicoOtaBreadcrumb(5, offset); // chunk HTTP
        // A failed HTTP read never advances offset or writes bytes. Retrying
        // this hash-bound chunk cannot mix releases or duplicate flash writes.
        for (unsigned attempt = 0; attempt < 3 && esp_timer_get_time() < deadline; ++attempt) {
            if (requestJson(path, body, BodyCapacity, used)) { downloaded = true; break; }
            if (attempt < 2) vTaskDelay(pdMS_TO_TICKS(300U << attempt));
        }
        if (!downloaded) { MosaicoOta::fail("chunk_download"); return; }
        MosaicoOtaBreadcrumb(6, offset); // JSON allocation / decode / free
        cJSON* root = cJSON_ParseWithLength(body, static_cast<size_t>(used));
        const cJSON* position = root ? cJSON_GetObjectItemCaseSensitive(root, "offset") : nullptr;
        const cJSON* data = root ? cJSON_GetObjectItemCaseSensitive(root, "data") : nullptr;
        size_t count = 0;
        const bool ok = cJSON_IsNumber(position) && position->valuedouble == offset &&
            cJSON_IsString(data) && data->valuestring &&
            mbedtls_base64_decode(chunk, ChunkCapacity, &count,
                reinterpret_cast<const unsigned char*>(data->valuestring),
                std::strlen(data->valuestring)) == 0 &&
            count == std::min<uint32_t>(ChunkCapacity, size - offset);
        cJSON_Delete(root);
        if (!ok) { MosaicoOta::fail("chunk_format"); return; }
        MosaicoOtaBreadcrumb(7, offset); // SDK write + incremental hash
        if (!MosaicoOta::writeChunk(offset, chunk, count)) return;
        offset += static_cast<uint32_t>(count);
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    MosaicoOtaBreadcrumb(8, size); // final hash/SDK verification
    if (![&]() { MosaicoHeapProbeScope probe(MosaicoHeapProbeStage::DownloadComplete); return heap_caps_check_integrity_all(true); }()) { MosaicoOta::fail("heap_after_download"); return; }
    if (!MosaicoOta::finishDownload()) return;
    MosaicoOta::processLocalRequests(); // Full pipeline only when explicitly enabled/bypassed.

}
#endif
#ifdef MOSAICO_BOARD
void NetworkQuota::wakeForFirmwareUpdate()
{
    StandbySleep::cancelForActivity();
    setCpu(CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
    if (_task_handle) xTaskNotifyGive(_task_handle);
}
#endif
