#include <debug/mosaico_rx_observer.h>
#include "mosaico_ota.h"
#include "mosaico_ota_power.h"
#include "ota_public_key.h"
#include <hal/hal.h>
#include <host/standby_sleep.h>
#include <host/touch_sleep.h>
#include <cJSON.h>
#include <esp_ota_ops.h>
#include <esp_app_desc.h>
#include <esp_heap_caps.h>
#include <esp_memory_utils.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <esp_image_format.h>
#include <nvs.h>
#include <tusb.h>
#include <mbedtls/base64.h>
#include <mbedtls/pk.h>
#include <psa/crypto.h>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace MosaicoOta {
namespace {
constexpr uint32_t MaxImage = 0x3e0000;
std::mutex lock;
std::mutex healthLock;
std::mutex snapshotLock; // Never held over flash, crypto, NVS, or telemetry calls.
UiSnapshot ui{0, 0, 0, UiStage::Idle, "", "-", "", "", "", -1, -1, false, false, false, 0};
std::atomic<bool> approvedRequest{false}, autoInstall{false};
std::atomic<bool> preferenceLoaded{false};
std::atomic<bool> imageReady{false}; // Retained staged image, distinct from historical boot verification.
bool signedVersion = false;
std::atomic<bool> checkQueued{false}, checking{false}, installQueued{false}, rebootQueued{false};
std::atomic<bool> selected{false}, verified{false};
std::atomic<UiStage> statusStage{UiStage::Idle};
int64_t readySince = 0, installSince = 0;
char approvedHash[65]{};
struct Descriptor { bool versionSigned; uint32_t size; uint8_t hash[32]; char sha[65], version[32], fingerprint[17]; };
std::atomic<bool> active{false};
std::atomic<uint32_t> requestedAtMs{0};
std::atomic<bool> pending{false};
bool writing = false, hashLive = false, automaticMode = false, usbBypass = false;
std::atomic<const char*> autoState{"idle"};
bool currentHashChecked = false, currentHashValid = false;
char currentHash[65]{};
esp_ota_handle_t handle = 0;
const esp_partition_t* target = nullptr;
std::atomic<uint32_t> expectedSize{0}, received{0};
std::atomic<const esp_partition_t*> cachedRunning{nullptr};
uint8_t expectedHash[32]{};
char reason[96] = "idle";
psa_hash_operation_t hash = PSA_HASH_OPERATION_INIT;
std::atomic<bool> bootPending{false};
esp_timer_handle_t deadlineTimer = nullptr;
bool healthInitialized = false;
int64_t healthySince = 0, lastLoop = 0, lastCheck = 0;
uint32_t healthyLoops = 0;

bool gaugeSafe()
{
    if (GetHAL().gaugeBootReloadInfo().status == Hal::GaugeBootReloadStatus::Critical) return false;
    const auto b = GetHAL().batteryTelemetry(true);
    return b.valid && ((b.operationStatus >> 1) & 3) == 3 && !(b.operationStatus & 0x0401);
}
bool downloadPowerSafe()
{
    if (GetHAL().gaugeBootReloadInfo().status == Hal::GaugeBootReloadStatus::Critical) return false;
    const auto b = GetHAL().batteryTelemetry(true);
    return b.valid && ((b.operationStatus >> 1) & 3) == 3 && !(b.operationStatus & 0x0401) && b.voltageMv >= 3500;
}
struct InstallPowerEvidence { char text[96]{}; };
void captureInstallPower(InstallPowerEvidence* evidence, const Hal::BatteryTelemetry& b, bool external, bool manual)
{
    if (evidence) std::snprintf(evidence->text, sizeof(evidence->text),
        "mv:%u,soc:%u,external:%d,manual:%d", static_cast<unsigned>(b.voltageMv),
        static_cast<unsigned>(b.reportedSoc), external, manual);
}
bool automaticPowerSafe(InstallPowerEvidence* evidence = nullptr)
{
    if (GetHAL().gaugeBootReloadInfo().status == Hal::GaugeBootReloadStatus::Critical) return false;
    const auto b = GetHAL().batteryTelemetry(true);
    const bool external = tud_mounted() || b.currentMa > 3;
    captureInstallPower(evidence, b, external, false);
    // Conservative charging/enumerated USB policy, NOT a measured VBUS or SOC claim.
    return b.valid && ((b.operationStatus >> 1) & 3) == 3 && !(b.operationStatus & 0x0401) &&
        b.voltageMv >= 3900 && external;
}
bool installPowerSafe(bool manual, InstallPowerEvidence* evidence = nullptr)
{
    if (!manual) return automaticPowerSafe(evidence);
    const bool critical = GetHAL().gaugeBootReloadInfo().status == Hal::GaugeBootReloadStatus::Critical;
    const auto b = GetHAL().batteryTelemetry(true);
    const bool external = tud_mounted();
    captureInstallPower(evidence, b, external || b.currentMa > 3, true);
    return manualInstallPowerSafe(b, external, critical);
}
bool exactSlot(const esp_partition_t* p)
{
    return p && p->type == ESP_PARTITION_TYPE_APP &&
        ((p->subtype == ESP_PARTITION_SUBTYPE_APP_OTA_0 && p->address == 0x20000 && p->size == 0x3e0000) ||
         (p->subtype == ESP_PARTITION_SUBTYPE_APP_OTA_1 && p->address == 0x400000 && p->size == 0x3f0000));
}
int8_t slot(const esp_partition_t* p) { return exactSlot(p) ? static_cast<int8_t>(p->subtype - ESP_PARTITION_SUBTYPE_APP_OTA_0) : -1; }
void publish(UiStage stage, const char* error = "")
{
    std::lock_guard<std::mutex> guard(snapshotLock);
    if (stage == UiStage::Failed || stage == UiStage::WaitingPower) approvedRequest.store(false);
    statusStage.store(stage);
    ui.stage = stage; ui.size = expectedSize.load(); ui.received = received.load();
    ui.automaticInstall = autoInstall.load();
    ui.progressBasisPoints = ui.size ? static_cast<uint16_t>(static_cast<uint64_t>(ui.received) * 10000 / ui.size) : 0;
    std::snprintf(ui.error, sizeof(ui.error), "%s", error); ++ui.revision;
}
bool exactData(uint8_t subtype, uint32_t address, uint32_t size)
{
    const auto* p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, static_cast<esp_partition_subtype_t>(subtype), nullptr);
    return p && p->address == address && p->size == size;
}
bool layoutSafe()
{
    const auto* a = esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_0, nullptr);
    const auto* b = esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_1, nullptr);
    const auto* d = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_OTA, nullptr);
    return exactSlot(a) && exactSlot(b) && d && d->size == 0x2000 && d->address == 0x10000 &&
        exactData(ESP_PARTITION_SUBTYPE_DATA_NVS, 0xa000, 0x6000) &&
        exactData(ESP_PARTITION_SUBTYPE_DATA_PHY, 0x12000, 0x1000) &&
        exactData(0x41, 0x7f0000, 0x320000) && exactData(0x83, 0xb10000, 0x2ee000);
}
bool currentReady()
{
    const auto* running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    return exactSlot(running) && layoutSafe() &&
        esp_ota_get_state_partition(running, &state) == ESP_OK && state == ESP_OTA_IMG_VALID;
}
void failLocked(const char* message)
{
    if (writing) esp_ota_abort(handle);
    writing = false;
    handle = 0;
    if (hashLive) psa_hash_abort(&hash);
    hashLive = false;
    pending = false;
    target = nullptr;
    std::snprintf(reason, sizeof(reason), "%s", message ? message : "failed");
    imageReady = false; verified.store(false);
    { std::lock_guard<std::mutex> uiGuard(snapshotLock); ui.imageVerified = false; }
    checkQueued = checking = installQueued = rebootQueued = false;
    publish(UiStage::Failed, message ? message : "failed");
    active.store(false);
}
bool reject(const char* message) { failLocked(message); return false; }
bool textEquals(const cJSON* object, const char* name, const char* expected)
{
    const auto* v = cJSON_GetObjectItemCaseSensitive(object, name);
    return cJSON_IsString(v) && v->valuestring && !std::strcmp(v->valuestring, expected);
}
bool lowerHex(const char* text)
{
    if (!text || std::strlen(text) != 64) return false;
    for (unsigned i = 0; i < 64; ++i)
        if (!((text[i] >= '0' && text[i] <= '9') || (text[i] >= 'a' && text[i] <= 'f'))) return false;
    return true;
}
void hashHex(const uint8_t* digest, char* text)
{
    const char* digits = "0123456789abcdef";
    for (unsigned i = 0; i < 32; ++i) {
        text[2 * i] = digits[digest[i] >> 4];
        text[2 * i + 1] = digits[digest[i] & 15];
    }
    text[64] = 0;
}
bool currentImageHash()
{
    if (currentHashChecked) return currentHashValid;
    currentHashChecked = true; // Fail closed for this boot; do not retry a failed read/hash.
    const auto* running = esp_ota_get_running_partition();
    esp_image_metadata_t metadata{};
    if (!exactSlot(running) || psa_crypto_init() != PSA_SUCCESS) return false;
    const esp_partition_pos_t pos{running->address, running->size};
    if (esp_image_verify(ESP_IMAGE_VERIFY_SILENT, &pos, &metadata) != ESP_OK ||
        !metadata.image.hash_appended || !metadata.image_len || metadata.image_len > MaxImage) return false;
    // SDK process_appended_hash_and_sig includes the appended 32-byte digest in image_len.
    // Hash the ENTIRE release .bin, not metadata.image_digest (which excludes that suffix).
    auto* buffer = static_cast<uint8_t*>(heap_caps_malloc(4096, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    if (!buffer) return false;
    psa_hash_operation_t operation = PSA_HASH_OPERATION_INIT;
    bool ok = psa_hash_setup(&operation, PSA_ALG_SHA_256) == PSA_SUCCESS;
    for (uint32_t offset = 0; ok && offset < metadata.image_len;) {
        const size_t length = metadata.image_len - offset < 4096 ? metadata.image_len - offset : 4096;
        ok = esp_partition_read(running, offset, buffer, length) == ESP_OK &&
             psa_hash_update(&operation, buffer, length) == PSA_SUCCESS;
        offset += length;
    }
    uint8_t digest[32];
    size_t length = 0;
    if (ok) ok = psa_hash_finish(&operation, digest, sizeof(digest), &length) == PSA_SUCCESS && length == 32;
    psa_hash_abort(&operation);
    heap_caps_free(buffer);
    if (ok) hashHex(digest, currentHash);
    currentHashValid = ok;
    return ok;
}
bool readAttempt(char* attempt)
{
    attempt[0] = 0;
    nvs_handle_t handle;
    esp_err_t rc = nvs_open("mosaico_ota", NVS_READONLY, &handle);
    if (rc == ESP_ERR_NVS_NOT_FOUND) return true; // New installation; namespace absent is not a write.
    if (rc != ESP_OK) return false;
    size_t length = 65;
    rc = nvs_get_str(handle, "attempt", attempt, &length);
    nvs_close(handle);
    if (rc == ESP_ERR_NVS_NOT_FOUND) { attempt[0] = 0; return true; }
    return rc == ESP_OK && length == 65 && lowerHex(attempt);
}
bool recordAttempt(const InstallPowerEvidence& power)
{
    char attempted[65];
    hashHex(expectedHash, attempted);
    nvs_handle_t handle;
    if (nvs_open("mosaico_ota", NVS_READWRITE, &handle) != ESP_OK) return false;
    char candidateVersion[32], fingerprint[17]; int8_t candidateSlot;
    { std::lock_guard<std::mutex> uiGuard(snapshotLock); std::memcpy(candidateVersion, ui.targetVersion, 32); std::memcpy(fingerprint, ui.signatureShort, 17); candidateSlot = ui.targetSlot; }
    const bool ok = nvs_set_str(handle, "attempt", attempted) == ESP_OK &&
        nvs_set_str(handle, "candidate_ver", candidateVersion) == ESP_OK &&
        nvs_set_str(handle, "candidate_sig", fingerprint) == ESP_OK &&
        nvs_set_str(handle, "candidate_hash", attempted) == ESP_OK &&
        nvs_set_i8(handle, "candidate_slot", candidateSlot) == ESP_OK &&
        nvs_set_str(handle, "attempt_power", power.text) == ESP_OK && nvs_commit(handle) == ESP_OK;
    nvs_close(handle);
    return ok;
}
bool requestLocked(bool automatic, bool preserveAge = false)
{
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    if (active.load() || approvedRequest.load() || imageReady || selected.load()) return false;
    if (!currentReady()) return reject("running_or_layout_not_ready");
    if (!downloadPowerSafe()) return reject("gauge_not_valid_sealed_cfg0_cal0");
    automaticMode = automatic;
    received = expectedSize = 0;
    pending = true;
    std::snprintf(reason, sizeof(reason), "requested");
    cachedRunning.store(esp_ota_get_running_partition());
    if (!preserveAge) requestedAtMs.store(static_cast<uint32_t>(esp_timer_get_time() / 1000));
    active.store(true); StandbySleep::otaActivity();
    return true;
}
bool validateDescriptor(const char* json, Descriptor& out, const char*& error)
{
    const auto invalid = [&](const char* why) { error = why; return false; };
    if (psa_crypto_init() != PSA_SUCCESS) return invalid("crypto_init");
    if (!json || std::strlen(json) > 2048) return invalid("manifest_length");
    const char* end = nullptr;
    cJSON* m = cJSON_ParseWithOpts(json, &end, true);
    if (!m) return invalid("manifest_json");
    // Reject duplicate/unknown fields: one interpretation for signed metadata.
    const char* names[] = {"schema", "board", "chip", "project", "layout", "size", "sha256", "signature", "version"};
    bool fields = cJSON_IsObject(m);
    unsigned seen = 0;
    for (const cJSON* v = m->child; v; v = v->next) {
        unsigned bit = 0;
        for (unsigned i = 0; i < 9; ++i) if (v->string && !std::strcmp(v->string, names[i])) bit = 1U << i;
        if (!bit || (seen & bit)) fields = false;
        seen |= bit;
    }
    const auto* schema = cJSON_GetObjectItemCaseSensitive(m, "schema");
    const auto* size = cJSON_GetObjectItemCaseSensitive(m, "size");
    const auto* sha = cJSON_GetObjectItemCaseSensitive(m, "sha256");
    const auto* sig = cJSON_GetObjectItemCaseSensitive(m, "signature");
    const auto* version = cJSON_GetObjectItemCaseSensitive(m, "version");
    const bool v2 = cJSON_IsNumber(schema) && schema->valuedouble == 2;
    out.versionSigned = v2;
    bool versionValid = cJSON_IsString(version) && version->valuestring && std::strlen(version->valuestring) >= 1 && std::strlen(version->valuestring) <= 31;
    if (versionValid) for (const char* c = version->valuestring; *c; ++c) if (*c < 33 || *c > 126) versionValid = false;
    bool valid = fields && seen == (v2 ? 511U : 255U) && cJSON_IsNumber(schema) &&
        (schema->valuedouble == 1 || (v2 && versionValid)) &&
        textEquals(m, "board", "esp-mosaico") && textEquals(m, "chip", "esp32s31") &&
        textEquals(m, "project", "Stopwatch-Mosaico") && textEquals(m, "layout", "mosaico-dual-v1") &&
        cJSON_IsNumber(size) && std::isfinite(size->valuedouble) && size->valuedouble >= 1 &&
        size->valuedouble <= MaxImage && std::floor(size->valuedouble) == size->valuedouble &&
        cJSON_IsString(sha) && sha->valuestring && std::strlen(sha->valuestring) == 64 &&
        cJSON_IsString(sig) && sig->valuestring && std::strlen(sig->valuestring) <= 104;
    if (valid) {
        for (unsigned i = 0; i < 64; ++i) {
            const char ch = sha->valuestring[i];
            if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) { valid = false; break; }
            const uint8_t nibble = ch <= '9' ? ch - '0' : ch - 'a' + 10;
            if (!(i & 1)) out.hash[i / 2] = nibble << 4;
            else out.hash[i / 2] |= nibble;
        }
    }
    uint8_t signature[80]{}, digest[32]{};
    size_t signatureSize = 0, digestSize = 0;
    char canonical[256]{};
    if (valid) {
        out.size = static_cast<uint32_t>(size->valuedouble);
        std::snprintf(out.version, sizeof(out.version), "%s", v2 ? version->valuestring : "-");
        std::snprintf(out.sha, sizeof(out.sha), "%s", sha->valuestring);
        if (v2) std::snprintf(canonical, sizeof(canonical),
            "MOSAICO-OTA-v2\nStopwatch-Mosaico\nesp32s31\nmosaico-dual-v1\n%s\n%lu\n%s\n",
            out.version, static_cast<unsigned long>(out.size), out.sha);
        else std::snprintf(canonical, sizeof(canonical),
            "MOSAICO-OTA-v1\nStopwatch-Mosaico\nesp32s31\nmosaico-dual-v1\n%lu\n%s\n",
            static_cast<unsigned long>(out.size), out.sha);
        valid = mbedtls_base64_decode(signature, sizeof(signature), &signatureSize,
            reinterpret_cast<const unsigned char*>(sig->valuestring), std::strlen(sig->valuestring)) == 0 &&
            signatureSize >= 8 && signatureSize <= 72 &&
            psa_hash_compute(PSA_ALG_SHA_256, reinterpret_cast<const unsigned char*>(canonical), std::strlen(canonical), digest, sizeof(digest), &digestSize) == PSA_SUCCESS;
    }
    cJSON_Delete(m);
    if (!valid) return invalid("manifest_fields_or_encoding");
    mbedtls_pk_context key;
    mbedtls_pk_init(&key);
    int rc = mbedtls_pk_parse_public_key(&key, reinterpret_cast<const unsigned char*>(MosaicoOtaPublicKey),
                                       sizeof(MosaicoOtaPublicKey));
    if (rc == 0 && (!mbedtls_pk_can_do_psa(&key, MBEDTLS_PK_ALG_ECDSA(PSA_ALG_SHA_256), PSA_KEY_USAGE_VERIFY_HASH) || mbedtls_pk_get_bitlen(&key) != 256)) rc = -1;
    if (rc == 0) rc = mbedtls_pk_verify(&key, MBEDTLS_MD_SHA256, digest, sizeof(digest), signature, signatureSize);
    mbedtls_pk_free(&key);
    if (rc != 0) return invalid("signature_invalid");
    if (psa_hash_compute(PSA_ALG_SHA_256, signature, signatureSize, digest, sizeof(digest), &digestSize) != PSA_SUCCESS) return invalid("signature_fingerprint");
    char hex[65]; hashHex(digest, hex); std::memcpy(out.fingerprint, hex, 16); out.fingerprint[16] = 0;
    return true;
}
void healthTimeout(void*)
{
    std::lock_guard<std::mutex> guard(healthLock);
    if (bootPending.exchange(false)) {
        // Bootloader rolls back if normal execution never reaches the health gate.
        if (esp_ota_mark_app_invalid_rollback_and_reboot() != ESP_OK) esp_restart();
    }
}
}

bool busy() { return active.load() || approvedRequest.load() || checkQueued.load() || checking.load() || installQueued.load() || rebootQueued.load() || bootPending.load(); }
bool healthPending() { return bootPending.load(); }
bool blocksTwt()
{
    const auto stage = statusStage.load();
    return busy() || (imageReady.load() && stage != UiStage::Complete) || selected.load() ||
        stage == UiStage::ReadyReboot || stage == UiStage::BootChecking;
}
uint32_t requestAgeMs()
{
    return busy() ? static_cast<uint32_t>(esp_timer_get_time() / 1000) - requestedAtMs.load() : 0;
}
bool request()
{
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    std::lock_guard<std::mutex> guard(lock);
    { std::lock_guard<std::mutex> uiGuard(snapshotLock);
      if (busy() || imageReady || selected.load()) return false;
      if (ui.signatureVerified && ui.sha256[0] &&
          (ui.stage == UiStage::Available || ui.stage == UiStage::WaitingPower)) std::memcpy(approvedHash, ui.sha256, 65);
      else {
          approvedHash[0] = 0;
          // A completed boot journal is history, not a staged image for this request.
          ui.signatureVerified = ui.imageVerified = false;
          ui.sha256[0] = ui.signatureShort[0] = 0;
          verified.store(false); ++ui.revision;
      } }
    const bool accepted = requestLocked(true);
    usbBypass = accepted;
    return accepted; // Explicit USB bypass skips screen approvals, never safety gates.
}
bool automaticCheckDue()
{
    // Permanent user opt-out: discovery is manual-only, not a timed trial pause.
    // Do not touch clocks, sleep admission, UI, or OTA state in the idle path.
    return false;
}
bool discoverManifest(const char* json)
{
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    std::lock_guard<std::mutex> guard(lock);
    if (active.load() || approvedRequest.load() || imageReady || selected.load()) return false;
    Descriptor d{}; const char* error = "manifest_invalid";
    if (!validateDescriptor(json, d, error)) { publish(UiStage::Failed, error); return false; }
    if (!currentReady()) return false;
    if (!currentImageHash()) { publish(UiStage::Failed, "current_hash_failed"); return false; }
    char attempted[65];
    if (!readAttempt(attempted)) { publish(UiStage::Failed, "attempt_read_failed"); return false; }
    if (!std::strcmp(d.sha, currentHash) || !std::strcmp(d.sha, attempted)) { publish(UiStage::Idle); return false; }
    expectedSize = d.size; received = 0;
    const int8_t nextSlot = slot(esp_ota_get_next_update_partition(nullptr));
    { std::lock_guard<std::mutex> uiGuard(snapshotLock);
      std::snprintf(ui.targetVersion, sizeof(ui.targetVersion), "%s", d.version);
      std::memcpy(ui.sha256, d.sha, 65); std::memcpy(ui.signatureShort, d.fingerprint, 17);
      ui.signatureVerified = true; ui.imageVerified = false; verified.store(false);
      ui.targetSlot = nextSlot; }
    publish(UiStage::Available);
    checking.store(false);
    if (autoInstall.load()) approveUpdate(d.sha);
    return true;
}
bool requestAutomatic(const char* json) { return discoverManifest(json); }
bool copyUiSnapshot(UiSnapshot& out)
{
    std::unique_lock<std::mutex> guard(snapshotLock, std::try_to_lock);
    if (!guard.owns_lock()) return false;
    out = ui; return true;
}
bool approveUpdate(const char* expectedSha)
{
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    std::unique_lock<std::mutex> guard(snapshotLock, std::try_to_lock);
    if (!guard.owns_lock() || busy() || !lowerHex(expectedSha) ||
        std::strcmp(expectedSha, ui.sha256) || !ui.signatureVerified || ui.imageVerified ||
        (ui.stage != UiStage::Available && ui.stage != UiStage::WaitingPower)) return false;
    // Only a short in-memory copy and atomic queue; network owner performs all work.
    std::memcpy(approvedHash, ui.sha256, 65);
    requestedAtMs.store(static_cast<uint32_t>(esp_timer_get_time() / 1000));
    approvedRequest.store(true); StandbySleep::otaActivity(); return true;
}
void deferUpdate()
{
    std::unique_lock<std::mutex> guard(snapshotLock, std::try_to_lock);
    if (!guard.owns_lock() || busy() || ui.imageVerified || selected.load()) return;
    approvedRequest.store(false); statusStage.store(UiStage::Idle); ui.stage = UiStage::Idle; ++ui.revision;
}
bool automaticInstall() { return autoInstall.load(); }
bool setAutomaticInstall(bool enabled)
{
    std::lock_guard<std::mutex> guard(lock);
    nvs_handle_t h;
    if (nvs_open("mosaico_ota", NVS_READWRITE, &h) != ESP_OK) return false;
    bool ok = nvs_set_u8(h, "autoinstall", enabled ? 1 : 0) == ESP_OK && nvs_commit(h) == ESP_OK;
    nvs_close(h);
    if (ok) { autoInstall.store(enabled); preferenceLoaded = true;
        std::lock_guard<std::mutex> uiGuard(snapshotLock); ui.automaticInstall = enabled; ++ui.revision; }
    return ok;
}
bool takeRequest()
{
    std::lock_guard<std::mutex> guard(lock);
    if(!active.load() && !approvedRequest.load() && !pending.load())return false;
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    if (!active.load() && approvedRequest.load() && requestAgeMs() >= 120000) return reject("request_timeout");
    if (!active.load() && approvedRequest.exchange(false)) {
        if (!currentReady()) { publish(UiStage::Failed, "running_not_ready"); return false; }
        if (!downloadPowerSafe()) { publish(UiStage::WaitingPower, "download_voltage_or_gauge"); return false; }
        usbBypass = false;
        if (!requestLocked(autoInstall.load(), true)) return false;
    }
    const bool result = pending && active.load();
    pending = false;
    return result;
}
void fail(const char* message)
{
    std::lock_guard<std::mutex> guard(lock);
    failLocked(message);
}
bool beginManifest(const char* json)
{
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    std::lock_guard<std::mutex> guard(lock);
    if (!active.load() || pending || writing || imageReady) return reject("manifest_out_of_sequence");
    Descriptor d{}; const char* error = "manifest_invalid";
    if (!validateDescriptor(json, d, error)) return reject(error);
    if (approvedHash[0] && std::strcmp(d.sha, approvedHash)) return reject("approved_manifest_changed");
    expectedSize = d.size; signedVersion = d.versionSigned; std::memcpy(expectedHash, d.hash, 32);
    { std::lock_guard<std::mutex> uiGuard(snapshotLock);
      std::snprintf(ui.targetVersion, sizeof(ui.targetVersion), "%s", d.version);
      std::memcpy(ui.sha256, d.sha, 65); std::memcpy(ui.signatureShort, d.fingerprint, 17);
      ui.signatureVerified = true; ui.imageVerified = false; verified.store(false); }
    if (!currentReady() || !downloadPowerSafe()) return reject("prewrite_safety");
    const auto* running = esp_ota_get_running_partition();
    target = esp_ota_get_next_update_partition(nullptr);
    if (!exactSlot(target) || target->address == running->address || expectedSize > target->size)
        return reject("target_partition");
    hash = psa_hash_operation_t PSA_HASH_OPERATION_INIT;
    hashLive = true;
    if (psa_hash_setup(&hash, PSA_ALG_SHA_256)) return reject("hash_init");
    if (esp_ota_begin(target, expectedSize, &handle) != ESP_OK) return reject("ota_begin");
    writing = true;
    received = 0;
    { std::lock_guard<std::mutex> uiGuard(snapshotLock); ui.targetSlot = slot(target); }
    publish(UiStage::Downloading);
    std::snprintf(reason, sizeof(reason), "writing");
    return true;
}
bool writeChunk(uint32_t offset, const uint8_t* data, size_t length)
{
    std::lock_guard<std::mutex> guard(lock);
    if (!active.load() || !writing || !data || !length || offset != received || received > expectedSize ||
        length > expectedSize - received) return reject("chunk_offset_or_length");
    if (length > 4096 || !esp_ptr_in_dram(data) || !esp_ptr_in_dram(data + length - 1))
        return reject("chunk_not_internal");
    if (offset % 65536U == 0 && !downloadPowerSafe()) return reject("download_power_lost");
    if (esp_ota_write(handle, data, length) != ESP_OK) return reject("ota_write");
    if (psa_hash_update(&hash, data, length)) return reject("hash_update");
    received += length;
    publish(UiStage::Downloading);
    return true;
}
bool finishDownload()
{
    std::lock_guard<std::mutex> guard(lock);
    if (!active.load() || !writing || received != expectedSize) return reject("incomplete_image");
    publish(UiStage::Verifying);
    uint8_t digest[32];
    size_t digestSize = 0;
    if (psa_hash_finish(&hash, digest, sizeof(digest), &digestSize) || std::memcmp(digest, expectedHash, 32)) return reject("image_hash");
    psa_hash_abort(&hash);
    hashLive = false;
    const esp_err_t endResult = esp_ota_end(handle); // SDK consumes handle even on failure.
    writing = false;
    handle = 0;
    if (endResult != ESP_OK) return reject("image_validation");
    esp_app_desc_t downloaded{};
    char verifiedVersion[32];
    { std::lock_guard<std::mutex> uiGuard(snapshotLock); std::memcpy(verifiedVersion, ui.targetVersion, 32); }
    if (esp_ota_get_partition_description(target, &downloaded) != ESP_OK ||
        (signedVersion && std::strcmp(verifiedVersion, downloaded.version))) return reject("image_version_mismatch");
    imageReady = true; verified.store(true); readySince = esp_timer_get_time(); installSince = 0;
    { std::lock_guard<std::mutex> uiGuard(snapshotLock); ui.imageVerified = true; }
    publish(UiStage::ReadyInstall);
    active.store(usbBypass || automaticMode);
    return true;
}
bool installVerified()
{
    std::lock_guard<std::mutex> guard(lock);
    if (!active.load() || !imageReady || selected.load()) return false;
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    if ((usbBypass || automaticMode) && esp_timer_get_time() - readySince < 1500000) return false;
    // The physical bypass is explicitly confirmed by a local human/operator.
    // Use the existing manual battery-floor safety policy even when a full
    // V1-fed cell has zero charging current. Unconfirmed automatic runs retain
    // the charging/enumerated-USB policy.
    const bool manual = useManualInstallPolicy(usbBypass, automaticMode);
    if (!currentReady() || !installPowerSafe(manual)) {
        active.store(false); installSince = 0;
        publish(UiStage::WaitingPower, manual ? "install_power_required" : "install_external_power_required"); return false;
    }
    if (!installSince) {
        installSince = esp_timer_get_time(); publish(UiStage::Installing); return false;
    }
    if (esp_timer_get_time() - installSince < 500000) return false;
    // Fresh power evidence immediately before persistent journal/selector writes.
    InstallPowerEvidence power;
    if (!currentReady() || !installPowerSafe(manual, &power)) {
        active.store(false); installSince = 0;
        publish(UiStage::WaitingPower, manual ? "install_power_required" : "install_power_lost"); return false;
    }
    if (!recordAttempt(power)) return reject("attempt_commit_failed");
    if (esp_ota_set_boot_partition(target) != ESP_OK) return reject("set_boot_partition");
    selected.store(true); publish(UiStage::ReadyReboot);
    std::snprintf(reason, sizeof(reason), "verified_selected");
    active.store(false);
    return true;
}
bool requestCheck()
{
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    std::unique_lock<std::mutex> guard(snapshotLock, std::try_to_lock);
    if (!guard.owns_lock() || busy() || selected.load() ||
        ui.stage == UiStage::ReadyInstall || ui.stage == UiStage::ReadyReboot ||
        (ui.stage == UiStage::WaitingPower && imageReady.load())) return false;
    requestedAtMs.store(static_cast<uint32_t>(esp_timer_get_time() / 1000));
    checkQueued.store(true); StandbySleep::otaActivity(); return true;
}
bool takeCheckRequest()
{
    if(!checkQueued.load())return false;
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    if (!checkQueued.exchange(false)) return false;
    checking.store(true); StandbySleep::otaActivity(); publish(UiStage::Checking); return true;
}
void finishCheck(bool transportOk)
{
    checking.store(false);
    if (!transportOk) publish(UiStage::Failed, "manifest_download");
    else if (statusStage.load() == UiStage::Checking) publish(UiStage::Idle);
}
bool approveInstall(const char* expectedSha)
{
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    std::unique_lock<std::mutex> guard(snapshotLock, std::try_to_lock);
    if (!guard.owns_lock() || busy() || !lowerHex(expectedSha) || std::strcmp(expectedSha, ui.sha256) ||
        !ui.signatureVerified || !ui.imageVerified || !imageReady.load() || selected.load() ||
        (ui.stage != UiStage::ReadyInstall && ui.stage != UiStage::WaitingPower)) return false;
    requestedAtMs.store(static_cast<uint32_t>(esp_timer_get_time() / 1000));
    installQueued.store(true); StandbySleep::otaActivity(); return true;
}
bool requestReboot()
{
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    std::unique_lock<std::mutex> guard(snapshotLock, std::try_to_lock);
    if (!guard.owns_lock() || busy() || ui.stage != UiStage::ReadyReboot || !selected.load()) return false;
    rebootQueued.store(true); StandbySleep::otaActivity(); return true;
}
bool rebootWithFreshPower()
{
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    if (!installPowerSafe(useManualInstallPolicy(usbBypass, automaticMode))) {
        active.store(false);
        // Boot selection is already committed; never erase the retained image
        // or selector just because power changed while waiting for reboot.
        publish(UiStage::ReadyReboot, "reboot_power_required");
        return false;
    }
    active.store(true); StandbySleep::otaActivity(); esp_restart(); return true;
}
void processLocalRequests()
{
    const auto pendingStage=statusStage.load();
    const bool installWork=active.load() && imageReady.load() && !selected.load() &&
        (pendingStage==UiStage::ReadyInstall || pendingStage==UiStage::WaitingPower || pendingStage==UiStage::Installing);
    if(!rebootQueued.load() && !installQueued.load() && !installWork)return;
    mosaico_rx_observer::request(false);
    mosaico_rx_observer::service(false); // Same network owner; stop diagnostics before flash/reboot.
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return;
    if (rebootQueued.exchange(false) && selected.load()) { rebootWithFreshPower(); return; }
    if (installQueued.exchange(false)) {
        automaticMode = usbBypass = false; installSince = 0; active.store(true); StandbySleep::otaActivity();
    }
    // Only bounded install work is polled here, never download or network work.
    const UiStage stage = statusStage.load();
    if (active.load() && imageReady && !selected.load() &&
        (stage == UiStage::ReadyInstall || stage == UiStage::WaitingPower || stage == UiStage::Installing)) {
        const int64_t deadline = esp_timer_get_time() + 6000000;
        while (active.load() && !selected.load() && esp_timer_get_time() < deadline) {
            installVerified(); vTaskDelay(pdMS_TO_TICKS(100));
        }
        if (active.load()) fail("install_stage_timeout");
        if ((usbBypass || automaticMode) && selected.load()) {
            vTaskDelay(pdMS_TO_TICKS(500)); rebootWithFreshPower();
        }
    }
}
bool finish() { return finishDownload(); } // Compatibility: caller must now explicitly installVerified().
void baseStatus(char* out, size_t length)
{
    if (!out || !length) return;
    // Never wait behind sector erase/write, and never read flash while active.
    const auto progress = [&]() {
        const auto* running = cachedRunning.load();
        std::snprintf(out, length, "running=%s address=0x%lx state=%d busy=1 pending=%d received=%lu size=%lu reason=network_update auto_state=%s",
            running ? running->label : "unknown", static_cast<unsigned long>(running ? running->address : 0),
            static_cast<int>(running ? ESP_OTA_IMG_VALID : ESP_OTA_IMG_UNDEFINED), pending.load(),
            static_cast<unsigned long>(received.load()), static_cast<unsigned long>(expectedSize.load()), autoState.load());
    };
    if (busy()) { progress(); return; }
    std::unique_lock<std::mutex> guard(lock, std::try_to_lock);
    if (!guard.owns_lock()) { progress(); return; }
    const auto* running = esp_ota_get_running_partition();
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    if (running) esp_ota_get_state_partition(running, &state);
    std::snprintf(out, length, "running=%s address=0x%lx state=%d busy=%d pending=%d received=%lu size=%lu reason=%s auto_state=%s",
        running ? running->label : "unknown", static_cast<unsigned long>(running ? running->address : 0),
        static_cast<int>(state), busy(), pending.load(), static_cast<unsigned long>(received.load()),
        static_cast<unsigned long>(expectedSize.load()), reason, autoState.load());
}
void status(char* out, size_t length)
{
    if (!out || !length) return;
    baseStatus(out, length);
    const size_t used = std::strlen(out);
    const char* stages[] = {"idle", "checking", "available", "waiting_power", "downloading", "verifying", "ready_install", "installing", "ready_reboot", "boot_checking", "complete", "failed"};
    const unsigned stage = static_cast<unsigned>(statusStage.load());
    std::snprintf(out + used, length - used, " automatic_check=0 stage=%s image_verified=%d selected=%d",
        stage < sizeof(stages)/sizeof(stages[0]) ? stages[stage] : "unknown", verified.load(), selected.load());
    // Diagnostic evidence only: old journals remain valid when this key is absent.
    // Do not read NVS during OTA writes or block behind the network owner.
    if (busy()) return;
    std::unique_lock<std::mutex> guard(lock, std::try_to_lock);
    if (!guard.owns_lock() || busy()) return;
    char power[96] = "unknown";
    nvs_handle_t h;
    if (nvs_open("mosaico_ota", NVS_READONLY, &h) == ESP_OK) {
        size_t size = sizeof(power);
        if (nvs_get_str(h, "attempt_power", power, &size) != ESP_OK)
            std::snprintf(power, sizeof(power), "unknown");
        nvs_close(h);
    }
    const size_t end = std::strlen(out);
    std::snprintf(out + end, length - end, " attempt_power=%s", power);
}
void healthPoll(bool appLoopReady)
{
    std::lock_guard<std::mutex> guard(healthLock);
    const int64_t now = esp_timer_get_time();
    if (!healthInitialized) {
        healthInitialized = true;
        const auto* descriptor = esp_app_get_description();
        const auto* current = esp_ota_get_running_partition();
        { std::lock_guard<std::mutex> uiGuard(snapshotLock);
          std::snprintf(ui.currentVersion, sizeof(ui.currentVersion), "%s", descriptor->version);
          ui.currentSlot = slot(current); ++ui.revision; }
        nvs_handle_t h;
        if (nvs_open("mosaico_ota", NVS_READONLY, &h) == ESP_OK) {
            uint8_t mode = 0;
            if (!preferenceLoaded && nvs_get_u8(h, "autoinstall", &mode) == ESP_OK) autoInstall.store(mode == 1);
            nvs_close(h);
        }
        preferenceLoaded = true;
        { std::lock_guard<std::mutex> uiGuard(snapshotLock); ui.automaticInstall = autoInstall.load(); ++ui.revision; }
        esp_ota_img_states_t state;
        const auto* running = esp_ota_get_running_partition();
        if (!running || esp_ota_get_state_partition(running, &state) != ESP_OK || state != ESP_OTA_IMG_PENDING_VERIFY) return;
        bootPending.store(true); StandbySleep::otaActivity();
        char ver[32] = "-", sha[65]{}, fingerprint[17]{}; int8_t candidate = -1;
        if (nvs_open("mosaico_ota", NVS_READONLY, &h) == ESP_OK) {
            size_t vl = sizeof(ver), sl = sizeof(sha);
            const bool ok = nvs_get_str(h, "candidate_ver", ver, &vl) == ESP_OK &&
                nvs_get_str(h, "candidate_hash", sha, &sl) == ESP_OK && lowerHex(sha) &&
                nvs_get_i8(h, "candidate_slot", &candidate) == ESP_OK && candidate == slot(current) &&
                (!std::strcmp(ver, "-") || !std::strcmp(ver, descriptor->version));
            if (!ok) { std::strcpy(ver, "-"); sha[0] = 0; candidate = -1; }
            size_t fl = sizeof(fingerprint);
            if (ok) nvs_get_str(h, "candidate_sig", fingerprint, &fl);
            nvs_close(h);
        }
        if (!sha[0]) { // Legacy bootstrap has no signed candidate journal: display SDK facts only.
            std::snprintf(ver, sizeof(ver), "%s", descriptor->version); candidate = slot(current);
            fingerprint[0] = 0;
        }
        { std::lock_guard<std::mutex> uiGuard(snapshotLock);
          std::memcpy(ui.signatureShort, fingerprint, sizeof(fingerprint));
          std::memcpy(ui.targetVersion, ver, sizeof(ver)); std::memcpy(ui.sha256, sha, sizeof(sha));
          ui.targetSlot = candidate; ui.signatureVerified = sha[0]; ui.imageVerified = sha[0]; }
        verified.store(sha[0] != 0);
        publish(UiStage::BootChecking);

        esp_timer_create_args_t args{};
        args.callback = healthTimeout;
        args.name = "ota-health";
        if (esp_timer_create(&args, &deadlineTimer) != ESP_OK ||
            esp_timer_start_once(deadlineTimer, 90000000) != ESP_OK) {
            if (esp_ota_mark_app_invalid_rollback_and_reboot() != ESP_OK) esp_restart();
            return;
        }
    }
    if (!bootPending.load()) return;
#ifdef MOSAICO_OTA_HEALTH_TEST_FAIL
    // Inject only after the real app is running: CDC can preserve the proof,
    // but the candidate has NOT completed its 20-second validity gate.
    if (appLoopReady) {
        std::printf("DBG OTA fault_health_test=1 pending_restart=1\r\n");
        std::fflush(stdout);
        vTaskDelay(pdMS_TO_TICKS(200));
        esp_restart(); // Leave PENDING_VERIFY; loader must reject on next boot.
    }
#endif
    if (!appLoopReady || (lastLoop && now - lastLoop > 1500000)) { healthySince = 0; healthyLoops = 0;
        std::lock_guard<std::mutex> uiGuard(snapshotLock); ui.progressBasisPoints = 0; ++ui.revision; }
    if (!appLoopReady) return;
    lastLoop = now;
    ++healthyLoops;
    if (now - lastCheck < 1000000) return;
    lastCheck = now;
    const auto d = GetHAL().diagnostics();
    const bool healthy = d.i2c && d.display && d.buttons && d.touch && GetDisplayFrameCount() > 0 &&
        heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) > 32768 &&
        heap_caps_get_free_size(MALLOC_CAP_SPIRAM) > 0 && heap_caps_check_integrity_all(false);
    if (!healthy) { healthySince = 0; healthyLoops = 0;
        std::lock_guard<std::mutex> uiGuard(snapshotLock); ui.progressBasisPoints = 0; ++ui.revision; return; }
    if (!healthySince) healthySince = now;
    { std::lock_guard<std::mutex> uiGuard(snapshotLock);
      ui.progressBasisPoints = static_cast<uint16_t>((now - healthySince >= 20000000) ? 10000 : (now - healthySince) / 2000); ++ui.revision; }
    if (now - healthySince >= 20000000 && healthyLoops >= 20) {
        if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
            bootPending.store(false);
            esp_timer_stop(deadlineTimer);
            publish(UiStage::Complete);
            { std::lock_guard<std::mutex> uiGuard(snapshotLock); ui.progressBasisPoints = 10000; }
        }
    }
}
bool rollbackTest()
{
    TouchSleep::OtaAdmission touchGuard; if(!touchGuard)return false;
    std::lock_guard<std::mutex> guard(lock);
    if (busy() || imageReady || selected.load() || !gaugeSafe()) return false;
    // SDK checks that another bootable image exists before changing the state.
    return esp_ota_mark_app_invalid_rollback_and_reboot() == ESP_OK;
}
}
