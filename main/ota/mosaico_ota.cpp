#include "mosaico_ota.h"
#include "ota_public_key.h"
#include <hal/hal.h>
#include <cJSON.h>
#include <esp_ota_ops.h>
#include <esp_heap_caps.h>
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
#ifdef MOSAICO_OTA_HEALTH_TEST_FAIL
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#endif

namespace MosaicoOta {
namespace {
constexpr uint32_t MaxImage = 0x3e0000;
std::mutex lock;
std::mutex healthLock;
std::atomic<bool> active{false};
std::atomic<uint32_t> requestedAtMs{0};
std::atomic<bool> pending{false};
bool writing = false, hashLive = false, automaticMode = false;
int64_t nextAutomaticCheck = 0;
std::atomic<const char*> autoState{"idle"};
bool currentHashChecked = false, currentHashValid = false;
char currentHash[65]{}, automaticHash[65]{};
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
bool automaticPowerSafe()
{
    if (GetHAL().gaugeBootReloadInfo().status == Hal::GaugeBootReloadStatus::Critical) return false;
    const auto b = GetHAL().batteryTelemetry(true);
    // Conservative charging/enumerated USB policy, NOT a measured VBUS or SOC claim.
    return b.valid && ((b.operationStatus >> 1) & 3) == 3 && !(b.operationStatus & 0x0401) &&
        b.voltageMv >= 3900 && (tud_mounted() || b.currentMa > 3);
}
bool exactSlot(const esp_partition_t* p)
{
    return p && p->type == ESP_PARTITION_TYPE_APP &&
        ((p->subtype == ESP_PARTITION_SUBTYPE_APP_OTA_0 && p->address == 0x20000 && p->size == 0x3e0000) ||
         (p->subtype == ESP_PARTITION_SUBTYPE_APP_OTA_1 && p->address == 0x400000 && p->size == 0x3f0000));
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
bool recordAttempt()
{
    char attempted[65];
    hashHex(expectedHash, attempted);
    nvs_handle_t handle;
    if (nvs_open("mosaico_ota", NVS_READWRITE, &handle) != ESP_OK) return false;
    const bool ok = nvs_set_str(handle, "attempt", attempted) == ESP_OK && nvs_commit(handle) == ESP_OK;
    nvs_close(handle);
    return ok;
}
bool requestLocked(bool automatic)
{
    if (active.load()) return false;
    if (!currentReady()) return reject("running_or_layout_not_ready");
    if (!(automatic ? automaticPowerSafe() : gaugeSafe())) return reject("gauge_not_valid_sealed_cfg0_cal0");
    automaticMode = automatic;
    received = expectedSize = 0;
    pending = true;
    std::snprintf(reason, sizeof(reason), "requested");
    cachedRunning.store(esp_ota_get_running_partition());
    requestedAtMs.store(static_cast<uint32_t>(esp_timer_get_time() / 1000));
    active.store(true);
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

bool busy() { return active.load(); }
bool healthPending() { return bootPending.load(); }
uint32_t requestAgeMs()
{
    return active.load() ? static_cast<uint32_t>(esp_timer_get_time() / 1000) - requestedAtMs.load() : 0;
}
bool request()
{
    std::lock_guard<std::mutex> guard(lock);
    return requestLocked(false); // Explicit manual confirmation may retry an attempted hash.
}
bool automaticCheckDue()
{
    std::lock_guard<std::mutex> guard(lock);
    const int64_t now = esp_timer_get_time();
    if (active.load() || now < nextAutomaticCheck) return false;
    if (!currentReady()) { autoState.store("not_valid"); return false; }
    if (!automaticPowerSafe()) { autoState.store("power_deferred"); return false; }
    nextAutomaticCheck = now + 3600000000LL;
    autoState.store("checking");
    return true;
}
bool requestAutomatic(const char* json)
{
    std::lock_guard<std::mutex> guard(lock);
    if (active.load()) return false;
    if (!json || std::strlen(json) > 2048) { autoState.store("manifest_invalid"); return false; }
    cJSON* m = cJSON_ParseWithOpts(json, nullptr, true);
    const auto* value = m ? cJSON_GetObjectItemCaseSensitive(m, "sha256") : nullptr;
    unsigned count = 0;
    if (m) for (const cJSON* v = m->child; v; v = v->next)
        if (v->string && !std::strcmp(v->string, "sha256")) ++count;
    const bool valid = cJSON_IsObject(m) && count == 1 && cJSON_IsString(value) && lowerHex(value->valuestring);
    if (valid) std::memcpy(automaticHash, value->valuestring, sizeof(automaticHash));
    cJSON_Delete(m);
    if (!valid) { autoState.store("manifest_invalid"); return false; }
    if (!currentReady() || !automaticPowerSafe()) { autoState.store("power_or_state_deferred"); return false; }
    if (!currentImageHash()) { autoState.store("current_hash_failed"); return false; }
    if (!std::strcmp(automaticHash, currentHash)) { autoState.store("same_image"); return false; }
    char attempted[65];
    if (!readAttempt(attempted)) { autoState.store("attempt_read_failed"); return false; }
    if (!std::strcmp(automaticHash, attempted)) { autoState.store("already_attempted"); return false; }
    const bool ok = requestLocked(true); // Full signature verification remains in beginManifest.
    autoState.store(ok ? "requested" : "request_deferred");
    return ok;
}
bool takeRequest()
{
    std::lock_guard<std::mutex> guard(lock);
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
    std::lock_guard<std::mutex> guard(lock);
    if (psa_crypto_init() != PSA_SUCCESS) return reject("crypto_init");
    if (!active.load() || pending || writing) return reject("manifest_out_of_sequence");
    if (!json || std::strlen(json) > 2048) return reject("manifest_length");
    const char* end = nullptr;
    cJSON* m = cJSON_ParseWithOpts(json, &end, true);
    if (!m) return reject("manifest_json");
    // Reject duplicate/unknown fields: one interpretation for signed metadata.
    const char* names[] = {"schema", "board", "chip", "project", "layout", "size", "sha256", "signature"};
    bool fields = cJSON_IsObject(m);
    unsigned seen = 0;
    for (const cJSON* v = m->child; v; v = v->next) {
        unsigned bit = 0;
        for (unsigned i = 0; i < 8; ++i) if (v->string && !std::strcmp(v->string, names[i])) bit = 1U << i;
        if (!bit || (seen & bit)) fields = false;
        seen |= bit;
    }
    const auto* schema = cJSON_GetObjectItemCaseSensitive(m, "schema");
    const auto* size = cJSON_GetObjectItemCaseSensitive(m, "size");
    const auto* sha = cJSON_GetObjectItemCaseSensitive(m, "sha256");
    const auto* sig = cJSON_GetObjectItemCaseSensitive(m, "signature");
    bool valid = fields && seen == 255 && cJSON_IsNumber(schema) && schema->valuedouble == 1 &&
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
            if (!(i & 1)) expectedHash[i / 2] = nibble << 4;
            else expectedHash[i / 2] |= nibble;
        }
    }
    uint8_t signature[80]{}, digest[32]{};
    size_t signatureSize = 0, digestSize = 0;
    char canonical[192]{};
    if (valid) {
        expectedSize = static_cast<uint32_t>(size->valuedouble);
        std::snprintf(canonical, sizeof(canonical),
            "MOSAICO-OTA-v1\nStopwatch-Mosaico\nesp32s31\nmosaico-dual-v1\n%lu\n%s\n",
            static_cast<unsigned long>(expectedSize), sha->valuestring);
        valid = mbedtls_base64_decode(signature, sizeof(signature), &signatureSize,
            reinterpret_cast<const unsigned char*>(sig->valuestring), std::strlen(sig->valuestring)) == 0 &&
            signatureSize >= 8 && signatureSize <= 72 &&
            psa_hash_compute(PSA_ALG_SHA_256, reinterpret_cast<const unsigned char*>(canonical), std::strlen(canonical), digest, sizeof(digest), &digestSize) == PSA_SUCCESS;
    }
    cJSON_Delete(m);
    if (!valid) return reject("manifest_fields_or_encoding");
    mbedtls_pk_context key;
    mbedtls_pk_init(&key);
    int rc = mbedtls_pk_parse_public_key(&key, reinterpret_cast<const unsigned char*>(MosaicoOtaPublicKey),
                                       sizeof(MosaicoOtaPublicKey));
    if (rc == 0 && (!mbedtls_pk_can_do_psa(&key, MBEDTLS_PK_ALG_ECDSA(PSA_ALG_SHA_256), PSA_KEY_USAGE_VERIFY_HASH) || mbedtls_pk_get_bitlen(&key) != 256)) rc = -1;
    if (rc == 0) rc = mbedtls_pk_verify(&key, MBEDTLS_MD_SHA256, digest, sizeof(digest), signature, signatureSize);
    mbedtls_pk_free(&key);
    if (rc != 0) return reject("signature_invalid");
    if (automaticMode) {
        char verified[65];
        hashHex(expectedHash, verified);
        if (std::strcmp(verified, automaticHash)) return reject("automatic_manifest_changed");
    }
    if (!currentReady() || !(automaticMode ? automaticPowerSafe() : gaugeSafe())) return reject("prewrite_safety");
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
    std::snprintf(reason, sizeof(reason), "writing");
    return true;
}
bool writeChunk(uint32_t offset, const uint8_t* data, size_t length)
{
    std::lock_guard<std::mutex> guard(lock);
    if (!active.load() || !writing || !data || !length || offset != received || received > expectedSize ||
        length > expectedSize - received) return reject("chunk_offset_or_length");
    if (esp_ota_write(handle, data, length) != ESP_OK) return reject("ota_write");
    if (psa_hash_update(&hash, data, length)) return reject("hash_update");
    received += length;
    return true;
}
bool finish()
{
    std::lock_guard<std::mutex> guard(lock);
    if (!active.load() || !writing || received != expectedSize) return reject("incomplete_image");
    uint8_t digest[32];
    size_t digestSize = 0;
    if (psa_hash_finish(&hash, digest, sizeof(digest), &digestSize) || std::memcmp(digest, expectedHash, 32)) return reject("image_hash");
    psa_hash_abort(&hash);
    hashLive = false;
    const esp_err_t endResult = esp_ota_end(handle); // SDK consumes handle even on failure.
    writing = false;
    handle = 0;
    if (endResult != ESP_OK) return reject("image_validation");
    if (!currentReady() || !(automaticMode ? automaticPowerSafe() : gaugeSafe())) return reject("prereboot_safety");
    if (!recordAttempt()) return reject("attempt_commit_failed");
    if (esp_ota_set_boot_partition(target) != ESP_OK) return reject("set_boot_partition");
    std::snprintf(reason, sizeof(reason), "verified_restarting");
    // Keep active true until the normal restart: no idle/sleep window.
    esp_restart();
    return true;
}
void status(char* out, size_t length)
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
    if (active.load()) { progress(); return; }
    std::unique_lock<std::mutex> guard(lock, std::try_to_lock);
    if (!guard.owns_lock()) { progress(); return; }
    const auto* running = esp_ota_get_running_partition();
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    if (running) esp_ota_get_state_partition(running, &state);
    std::snprintf(out, length, "running=%s address=0x%lx state=%d busy=%d pending=%d received=%lu size=%lu reason=%s auto_state=%s",
        running ? running->label : "unknown", static_cast<unsigned long>(running ? running->address : 0),
        static_cast<int>(state), active.load(), pending.load(), static_cast<unsigned long>(received.load()),
        static_cast<unsigned long>(expectedSize.load()), reason, autoState.load());
}
void healthPoll(bool appLoopReady)
{
    std::lock_guard<std::mutex> guard(healthLock);
    const int64_t now = esp_timer_get_time();
    if (!healthInitialized) {
        healthInitialized = true;
        esp_ota_img_states_t state;
        const auto* running = esp_ota_get_running_partition();
        if (!running || esp_ota_get_state_partition(running, &state) != ESP_OK || state != ESP_OTA_IMG_PENDING_VERIFY) return;
        bootPending.store(true);

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
    if (!appLoopReady || (lastLoop && now - lastLoop > 1500000)) { healthySince = 0; healthyLoops = 0; }
    if (!appLoopReady) return;
    lastLoop = now;
    ++healthyLoops;
    if (now - lastCheck < 1000000) return;
    lastCheck = now;
    const auto d = GetHAL().diagnostics();
    const bool healthy = d.i2c && d.display && d.buttons && d.touch && GetDisplayFrameCount() > 0 &&
        heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) > 32768 &&
        heap_caps_get_free_size(MALLOC_CAP_SPIRAM) > 0 && heap_caps_check_integrity_all(false);
    if (!healthy) { healthySince = 0; healthyLoops = 0; return; }
    if (!healthySince) healthySince = now;
    if (now - healthySince >= 20000000 && healthyLoops >= 20) {
        if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
            bootPending.store(false);
            esp_timer_stop(deadlineTimer);
        }
    }
}
bool rollbackTest()
{
    std::lock_guard<std::mutex> guard(lock);
    if (active.load() || !gaugeSafe()) return false;
    // SDK checks that another bootable image exists before changing the state.
    return esp_ota_mark_app_invalid_rollback_and_reboot() == ESP_OK;
}
}
