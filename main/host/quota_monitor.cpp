#include "quota_monitor.h"
#include "token_history.h"
#include <cJSON.h>
#include <atomic>
#include <cmath>
#include <cstring>
#include <memory>
#include <new>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

namespace {
SemaphoreHandle_t mutex = nullptr;
QuotaMonitorSnapshot* current = nullptr;
std::atomic<uint32_t> revision{0};
bool number(const cJSON* root, const char* key, double low, double high, uint32_t& out)
{
    const auto* value = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsNumber(value) || !std::isfinite(value->valuedouble) ||
        value->valuedouble < low || value->valuedouble > high ||
        std::floor(value->valuedouble) != value->valuedouble) return false;
    out = static_cast<uint32_t>(value->valuedouble);
    return true;
}
template <std::size_t N>
bool text(const cJSON* root, const char* key, char (&out)[N], bool empty = true)
{
    const auto* value = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsString(value) || !value->valuestring) return false;
    const auto length = std::strlen(value->valuestring);
    if (length >= N || (!empty && !length)) return false;
    // The wire normalizer emits printable ASCII for the bundled device fonts.
    for (std::size_t i = 0; i < length; ++i)
        if (static_cast<unsigned char>(value->valuestring[i]) < 32 ||
            static_cast<unsigned char>(value->valuestring[i]) > 126) return false;
    std::memcpy(out, value->valuestring, length + 1);
    return true;
}
bool parse(const char* json, std::size_t length, QuotaMonitorSnapshot& next)
{
    if (!json || !length || length > 8192 || !JsonNestingValid(json, length)) return false;
    std::unique_ptr<cJSON, decltype(&cJSON_Delete)> root(cJSON_ParseWithLength(json, length), cJSON_Delete);
    uint32_t version = 0, resetCredits = 0;
    const auto* buckets = cJSON_GetObjectItemCaseSensitive(root.get(), "buckets");
    const auto* truncated = cJSON_GetObjectItemCaseSensitive(root.get(), "truncated");
    const auto* credits = cJSON_GetObjectItemCaseSensitive(root.get(), "reset_credits");
    if (!root || !cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root.get(), "available")) ||
        !number(root.get(), "version", 2, 2, version) ||
        !number(root.get(), "captured_epoch", 1, UINT32_MAX, next.capturedEpoch) ||
        !number(root.get(), "age_seconds", 0, 120, next.ageSecondsAtReceipt) ||
        !cJSON_IsArray(buckets) || cJSON_GetArraySize(buckets) < 1 ||
        cJSON_GetArraySize(buckets) > static_cast<int>(QuotaMonitorMaxBuckets) ||
        !cJSON_IsBool(truncated)) return false;
    next.bucketCount = static_cast<uint8_t>(cJSON_GetArraySize(buckets));
    if (!number(root.get(), "total_buckets", next.bucketCount, UINT32_MAX, next.totalBuckets)) return false;
    next.truncated = cJSON_IsTrue(truncated);
    if (next.truncated != (next.totalBuckets > next.bucketCount)) return false;
    if (!cJSON_IsNull(credits)) {
        if (!number(root.get(), "reset_credits", 0, UINT16_MAX, resetCredits)) return false;
        next.resetCreditsKnown = true;
        next.resetCredits = static_cast<uint16_t>(resetCredits);
    }
    for (std::size_t i = 0; i < next.bucketCount; ++i) {
        const auto* bucket = cJSON_GetArrayItem(buckets, static_cast<int>(i));
        auto& destination = next.buckets[i];
        if (!cJSON_IsObject(bucket) || !text(bucket, "id", destination.id, false) ||
            !text(bucket, "name", destination.name, false) || !text(bucket, "plan", destination.plan) ||
            !text(bucket, "reached", destination.reached)) return false;
        for (std::size_t previous = 0; previous < i; ++previous)
            if (std::strcmp(next.buckets[previous].id, destination.id) == 0) return false;
        const auto* windows = cJSON_GetObjectItemCaseSensitive(bucket, "windows");
        if (!cJSON_IsArray(windows) || cJSON_GetArraySize(windows) != 2) return false;
        for (std::size_t w = 0; w < 2; ++w) {
            const auto* source = cJSON_GetArrayItem(windows, static_cast<int>(w));
            if (cJSON_IsNull(source)) continue;
            uint32_t remaining = 0;
            auto& window = destination.windows[w];
            if (!cJSON_IsObject(source) || !number(source, "remaining_bp", 0, 10000, remaining) ||
                !number(source, "duration_minutes", 0, UINT32_MAX, window.durationMinutes) ||
                !number(source, "reset_epoch", 0, UINT32_MAX, window.resetEpoch)) return false;
            window.remainingBasisPoints = static_cast<uint16_t>(remaining);
            window.available = true;
        }
        const auto* credit = cJSON_GetObjectItemCaseSensitive(bucket, "credits");
        if (!cJSON_IsNull(credit)) {
            const auto* unlimited = cJSON_GetObjectItemCaseSensitive(credit, "unlimited");
            const auto* balance = cJSON_GetObjectItemCaseSensitive(credit, "balance");
            if (!cJSON_IsObject(credit) || !cJSON_IsBool(unlimited)) return false;
            destination.creditsKnown = true;
            destination.creditsUnlimited = cJSON_IsTrue(unlimited);
            if (!cJSON_IsNull(balance)) {
                if (!text(credit, "balance", destination.creditBalance, false)) return false;
                bool dot = false, digit = false;
                for (const char* p = destination.creditBalance; *p; ++p) {
                    if (*p == '.' && !dot) dot = true;
                    else if (*p >= '0' && *p <= '9') digit = true;
                    else return false;
                }
                if (!digit) return false;
            }
        }
    }
    next.available = true;
    return true;
}
}

bool InitQuotaMonitor()
{
    if (!mutex) mutex = xSemaphoreCreateMutex();
    return mutex != nullptr;
}
uint32_t QuotaMonitorRevision() { return revision.load(); }
bool ApplyQuotaMonitor(const char* json, std::size_t length, uint32_t receivedAtMs)
{
    if (!mutex) return false;
    std::unique_ptr<QuotaMonitorSnapshot> next(new (std::nothrow) QuotaMonitorSnapshot());
    if (!next || !parse(json, length, *next)) return false;
    next->receivedAtMs = receivedAtMs;
    if (xSemaphoreTake(mutex, pdMS_TO_TICKS(50)) != pdTRUE) return false;
    if (current && next->capturedEpoch < current->capturedEpoch) {
        xSemaphoreGive(mutex);
        return true; // Acknowledge without replacing newer data.
    }
    next->receivedAtUs = static_cast<uint64_t>(esp_timer_get_time());
    next->revision = current ? current->revision + 1 : 1;
    auto* old = current;
    current = next.release();
    revision.store(current->revision);
    xSemaphoreGive(mutex);
    delete old;
    return true;
}
bool CopyQuotaMonitor(QuotaMonitorSnapshot& out, uint32_t nowMs)
{
    if (!mutex || xSemaphoreTake(mutex, pdMS_TO_TICKS(10)) != pdTRUE) return false;
    const bool valid = current != nullptr;
    if (valid) out = *current;
    const uint64_t nowUs = static_cast<uint64_t>(esp_timer_get_time());
    xSemaphoreGive(mutex);
    if (!valid) return false;
    (void)nowMs; // Kept for caller compatibility; millis wraps while this cache can outlive a view.
    const uint64_t elapsedUs = nowUs >= out.receivedAtUs ? nowUs - out.receivedAtUs : 0;
    out.ageMilliseconds = static_cast<uint64_t>(out.ageSecondsAtReceipt) * 1000U + elapsedUs / 1000U;
    const uint64_t age = out.ageMilliseconds / 1000U;
    out.ageSeconds = static_cast<uint32_t>(age > UINT32_MAX ? UINT32_MAX : age);
    out.stale = age > 130;
    out.available = age <= 600;
    return true;
}
bool QuotaMonitorRejectionSelfTest()
{
    std::unique_ptr<QuotaMonitorSnapshot> temporary(new (std::nothrow) QuotaMonitorSnapshot());
    if (!temporary) return false;
    constexpr char valid[] = "{\"version\":2,\"available\":true,\"captured_epoch\":100,\"age_seconds\":0,"
        "\"total_buckets\":1,\"truncated\":false,\"reset_credits\":null,\"buckets\":[{\"id\":\"codex\","
        "\"name\":\"Codex\",\"plan\":\"pro\",\"reached\":\"\",\"credits\":null,\"windows\":[null,"
        "{\"remaining_bp\":9700,\"duration_minutes\":10080,\"reset_epoch\":1000}]}]}";
    if (!parse(valid, sizeof(valid) - 1, *temporary) || temporary->resetCreditsKnown ||
        temporary->buckets[0].windows[0].available || temporary->buckets[0].windows[1].remainingBasisPoints != 9700)
        return false;
    constexpr char invalid[] = "{\"version\":2,\"available\":true,\"buckets\":[]}";
    if (parse(invalid, sizeof(invalid) - 1, *temporary) ||
        parse(valid, 8193, *temporary) || parse("[[[[[[[[[]]]]]]]]]", 18, *temporary)) return false;
    for (int test = 0; test < 6; ++test) {
        std::unique_ptr<cJSON, decltype(&cJSON_Delete)> data(cJSON_Parse(valid), cJSON_Delete);
        if (!data) return false;
        auto* buckets = cJSON_GetObjectItemCaseSensitive(data.get(), "buckets");
        auto* bucket = cJSON_GetArrayItem(buckets, 0);
        auto* window = cJSON_GetArrayItem(cJSON_GetObjectItemCaseSensitive(bucket, "windows"), 1);
        switch (test) {
        case 0: cJSON_ReplaceItemInObjectCaseSensitive(window, "remaining_bp", cJSON_CreateNumber(10001)); break;
        case 1: cJSON_ReplaceItemInObjectCaseSensitive(window, "reset_epoch", cJSON_CreateBool(true)); break;
        case 2: cJSON_ReplaceItemInObjectCaseSensitive(window, "duration_minutes", cJSON_CreateNumber(-1)); break;
        case 3: cJSON_ReplaceItemInObjectCaseSensitive(data.get(), "reset_credits", cJSON_CreateNumber(65536)); break;
        case 4: cJSON_ReplaceItemInObjectCaseSensitive(bucket, "name", cJSON_CreateString("bad\nname")); break;
        case 5:
            cJSON_AddItemToArray(buckets, cJSON_Duplicate(bucket, true));
            cJSON_SetNumberValue(cJSON_GetObjectItemCaseSensitive(data.get(), "total_buckets"), 2);
            break;
        }
        std::unique_ptr<char, decltype(&cJSON_free)> serialized(cJSON_PrintUnformatted(data.get()), cJSON_free);
        if (!serialized || parse(serialized.get(), std::strlen(serialized.get()), *temporary)) return false;
    }
    return true;
}
