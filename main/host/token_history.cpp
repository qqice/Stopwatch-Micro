#include "token_history.h"
#include <atomic>
#include <cmath>
#include <cstring>
#include <memory>
#include <new>
#include <cJSON.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

namespace {
SemaphoreHandle_t mutex       = nullptr;
TokenHistorySnapshot* current = nullptr;
std::atomic<uint32_t> revision{0};
bool integer(cJSON* root, const char* name, double low, double high, double& out)
{
    const auto* v = cJSON_GetObjectItemCaseSensitive(root, name);
    if (!cJSON_IsNumber(v) || !std::isfinite(v->valuedouble) || v->valuedouble < low || v->valuedouble > high ||
        std::floor(v->valuedouble) != v->valuedouble)
        return false;
    out = v->valuedouble;
    return true;
}
template <size_t N>
bool cells(cJSON* root, const char* name, std::array<TokenHistoryCell, N>& destination, bool daily)
{
    cJSON* values = cJSON_GetObjectItemCaseSensitive(root, name);
    if (!cJSON_IsArray(values) || static_cast<size_t>(cJSON_GetArraySize(values)) != N) return false;
    for (size_t i = 0; i < N; ++i) {
        auto* entry   = cJSON_GetArrayItem(values, i);
        auto* label   = cJSON_GetObjectItemCaseSensitive(entry, "label");
        auto* quality = cJSON_GetObjectItemCaseSensitive(entry, "quality");
        auto* count   = cJSON_GetObjectItemCaseSensitive(entry, "tokens");
        if (!cJSON_IsString(label) || !cJSON_IsString(quality) || !label->valuestring[0] ||
            std::strlen(label->valuestring) >= sizeof(destination[i].label))
            return false;
        for (const unsigned char* p = reinterpret_cast<const unsigned char*>(label->valuestring); *p; ++p)
            if (*p < 32 || *p > 126) return false;
        auto& cell = destination[i];
        std::strcpy(cell.label, label->valuestring);
        if (!std::strcmp(quality->valuestring, "missing"))
            cell.quality = TokenHistoryQuality::Missing;
        else if (!std::strcmp(quality->valuestring, "local"))
            cell.quality = TokenHistoryQuality::Local;
        else if (!daily && !std::strcmp(quality->valuestring, "pending"))
            cell.quality = TokenHistoryQuality::Pending;
        else if (daily && !std::strcmp(quality->valuestring, "official"))
            cell.quality = TokenHistoryQuality::Official;
        else if (!daily && !std::strcmp(quality->valuestring, "observed"))
            cell.quality = TokenHistoryQuality::Observed;
        else if (!daily && !std::strcmp(quality->valuestring, "partial"))
            cell.quality = TokenHistoryQuality::Partial;
        else if (!daily && !std::strcmp(quality->valuestring, "correction"))
            cell.quality = TokenHistoryQuality::Correction;
        else
            return false;
        if (cJSON_IsNull(count)) {
            if (cell.quality != TokenHistoryQuality::Missing && cell.quality != TokenHistoryQuality::Correction &&
                cell.quality != TokenHistoryQuality::Pending)
                return false;
        } else {
            double value = 0;
            if (cell.quality == TokenHistoryQuality::Missing || cell.quality == TokenHistoryQuality::Correction ||
                cell.quality == TokenHistoryQuality::Pending ||
                !integer(entry, "tokens", 0, 9007199254740991.0, value))
                return false;
            cell.tokens = static_cast<uint64_t>(value);
            cell.valid  = true;
        }
    }
    return true;
}
}  // namespace
bool JsonNestingValid(const char* data, size_t length)
{
    if (!data) return false;
    int depth   = 0;
    bool quoted = false, escaped = false;
    for (size_t i = 0; i < length; ++i) {
        const char ch = data[i];
        if (quoted) {
            if (escaped)
                escaped = false;
            else if (ch == '\\')
                escaped = true;
            else if (ch == '"')
                quoted = false;
        } else if (ch == '"')
            quoted = true;
        else if (ch == '{' || ch == '[') {
            if (++depth > 8) return false;
        } else if (ch == '}' || ch == ']') {
            if (--depth < 0) return false;
        }
    }
    return depth == 0 && !quoted;
}
bool InitTokenHistory()
{
    if (!mutex) mutex = xSemaphoreCreateMutex();
    return mutex != nullptr;
}
uint32_t TokenHistoryRevision()
{
    return revision.load();
}
bool CopyTokenHistory(TokenHistorySnapshot& out)
{
    if (!mutex || xSemaphoreTake(mutex, pdMS_TO_TICKS(10)) != pdTRUE) return false;
    bool valid = current != nullptr;
    if (valid) out = *current;
    xSemaphoreGive(mutex);
    return valid;
}
bool ApplyTokenHistory(const char* json, size_t length)
{
    if (!mutex || !json || length == 0 || length > 32768 || !JsonNestingValid(json, length)) return false;
    std::unique_ptr<TokenHistorySnapshot> next(new (std::nothrow) TokenHistorySnapshot());
    if (!next) return false;
    cJSON* root    = cJSON_ParseWithLength(json, length);
    double version = 0, captured = 0, age = 0, offset = 0;
    const auto* available = cJSON_GetObjectItemCaseSensitive(root, "available");
    bool ok = root && cJSON_IsBool(available) && integer(root, "version", 1, 1, version) &&
              integer(root, "captured_epoch", 0, UINT32_MAX, captured) &&
              integer(root, "age_seconds", 0, UINT32_MAX, age) &&
              integer(root, "timezone_offset_minutes", -720, 840, offset) && cells(root, "days", next->days, true) &&
              cells(root, "hours", next->hours, false);
    if (ok) {
        next->available           = cJSON_IsTrue(available);
        next->capturedEpoch       = captured;
        next->ageSecondsAtReceipt = age;
        if (next->available && captured == 0) ok = false;
        next->timezoneOffsetMinutes = offset;
        next->receivedAtMs          = esp_timer_get_time() / 1000;
    }
    cJSON_Delete(root);
    if (!ok) return false;
    if (xSemaphoreTake(mutex, pdMS_TO_TICKS(50)) != pdTRUE) return false;
    if (current && current->available && (!next->available || next->capturedEpoch < current->capturedEpoch)) {
        xSemaphoreGive(mutex);
        return true;
    }
    TokenHistorySnapshot* old = current;
    next->revision            = revision.load() + 1;
    current                   = next.release();
    revision.store(current->revision);
    xSemaphoreGive(mutex);
    delete old;
    return true;
}

bool TokenHistoryRejectionSelfTest()
{
    const uint32_t before = TokenHistoryRevision();
    constexpr char malformed[] =
        "{\"version\":1,\"available\":true,\"captured_epoch\":1,\"age_seconds\":0,\"timezone_offset_minutes\":480,"
        "\"days\":[],\"hours\":[]}";
    std::unique_ptr<char[]> oversized(new (std::nothrow) char[32769]);
    if (!oversized) return false;
    std::memset(oversized.get(), '[', 32769);
    return !ApplyTokenHistory(malformed, sizeof(malformed) - 1) && !ApplyTokenHistory(oversized.get(), 32768) &&
           !ApplyTokenHistory(oversized.get(), 32769) && before == TokenHistoryRevision();
}
