#include "token_history.h"
#include <atomic>
#include <algorithm>
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
        else if (!std::strcmp(quality->valuestring, "pending"))
            cell.quality = TokenHistoryQuality::Pending;
        else if (daily && !std::strcmp(quality->valuestring, "official"))
            cell.quality = TokenHistoryQuality::Official;
        else if (!daily && !std::strcmp(quality->valuestring, "observed"))
            cell.quality = TokenHistoryQuality::Observed;
        else if (!daily && !std::strcmp(quality->valuestring, "partial"))
            cell.quality = TokenHistoryQuality::Partial;
        else if (!daily && !std::strcmp(quality->valuestring, "gap"))
            cell.quality = TokenHistoryQuality::Gap;
        else if (!daily && !std::strcmp(quality->valuestring, "correction"))
            cell.quality = TokenHistoryQuality::Correction;
        else
            return false;
        if (cJSON_IsNull(count)) {
            if (cell.quality != TokenHistoryQuality::Missing && cell.quality != TokenHistoryQuality::Correction &&
                cell.quality != TokenHistoryQuality::Pending && cell.quality != TokenHistoryQuality::Gap)
                return false;
        } else {
            double value = 0;
            if (cell.quality == TokenHistoryQuality::Missing || cell.quality == TokenHistoryQuality::Correction ||
                cell.quality == TokenHistoryQuality::Pending || cell.quality == TokenHistoryQuality::Gap ||
                !integer(entry, "tokens", 0, 9007199254740991.0, value))
                return false;
            cell.tokens = static_cast<uint64_t>(value);
            cell.valid  = true;
        }
        for (const char* field : {"correction_delta", "gap_delta"}) {
            if (!cJSON_GetObjectItemCaseSensitive(entry, field)) continue;
            double value = 0;
            if (!integer(entry, field, -9007199254740991.0, 9007199254740991.0, value)) return false;
            if (std::strcmp(field, "correction_delta") == 0) {
                if (value > 0) return false;
                cell.correctionDelta = static_cast<int64_t>(value);
            } else cell.gapDelta = static_cast<int64_t>(value);
        }
    }
    return true;
}
template <size_t N>
bool trendPoints(cJSON* root, const char* name, std::array<QuotaTrendPoint, N>& out, uint32_t start, uint32_t end)
{
    const auto* values = cJSON_GetObjectItemCaseSensitive(root, name);
    if (!cJSON_IsArray(values) || static_cast<size_t>(cJSON_GetArraySize(values)) != N) return false;
    uint32_t previous = 0;
    for (size_t i = 0; i < N; ++i) {
        auto* item = cJSON_GetArrayItem(values, i);
        const auto* remaining = cJSON_GetObjectItemCaseSensitive(item, "remaining_bp");
        const auto* gap = cJSON_GetObjectItemCaseSensitive(item, "break_before");
        const auto* reset = cJSON_GetObjectItemCaseSensitive(item, "reset_before");
        double epoch = 0, bp = 0;
        if (!cJSON_IsObject(item) || !integer(item, "epoch", start, end, epoch) ||
            (i && epoch <= previous) || !cJSON_IsBool(gap) || !cJSON_IsBool(reset) ||
            (!cJSON_IsNull(remaining) && !integer(item, "remaining_bp", 0, 10000, bp))) return false;
        auto& point = out[i];
        point.epoch = static_cast<uint32_t>(epoch); previous = point.epoch;
        point.valid = !cJSON_IsNull(remaining); point.remainingBasisPoints = static_cast<uint16_t>(bp);
        point.breakBefore = cJSON_IsTrue(gap); point.resetBefore = cJSON_IsTrue(reset);
        if (point.resetBefore && !point.breakBefore) return false;
    }
    return true;
}
bool trend(cJSON* root, QuotaTrendSnapshot& out)
{
    const auto* available = cJSON_GetObjectItemCaseSensitive(root, "available");
    const auto* id = cJSON_GetObjectItemCaseSensitive(root, "limit_id");
    double version=0, captured=0, age=0, duration=0, offset=0, start24=0, start7=0, end=0;
    if (!cJSON_IsObject(root) || !cJSON_IsBool(available) || !cJSON_IsString(id) ||
        std::strlen(id->valuestring) >= sizeof(out.limitId) || !integer(root,"version",1,1,version) ||
        !integer(root,"captured_epoch",0,UINT32_MAX,captured) || !integer(root,"age_seconds",0,UINT32_MAX,age) ||
        !integer(root,"duration_minutes",10080,10080,duration) || !integer(root,"timezone_offset_minutes",480,480,offset) ||
        !integer(root,"start_24h_epoch",1,UINT32_MAX,start24) || !integer(root,"start_7d_epoch",1,UINT32_MAX,start7) ||
        !integer(root,"end_epoch",1,UINT32_MAX,end) || end-start24 != 86400 || end-start7 != 604800) return false;
    for (const unsigned char* p=reinterpret_cast<const unsigned char*>(id->valuestring); *p; ++p)
        if (*p < 32 || *p > 126) return false;
    out.available = cJSON_IsTrue(available);
    if (out.available && (!captured || captured > end || std::strcmp(id->valuestring,"codex"))) return false;
    if (captured > end || (captured && std::strcmp(id->valuestring,"codex")) ||
        (!captured && (id->valuestring[0] || age)) || (!out.available && captured && captured >= start7)) return false;
    out.capturedEpoch=captured; out.ageSecondsAtReceipt=age; out.durationMinutes=duration;
    out.timezoneOffsetMinutes=offset; out.start24hEpoch=start24; out.start7dEpoch=start7; out.endEpoch=end;
    std::strcpy(out.limitId,id->valuestring);
    if (!trendPoints(root,"hours",out.hours,out.start24hEpoch,out.endEpoch) ||
        !trendPoints(root,"days",out.days,out.start7dEpoch,out.endEpoch)) return false;
    uint32_t latest = 0;
    for (const auto& p:out.hours) if (p.valid) latest = std::max(latest,p.epoch);
    for (const auto& p:out.days) if (p.valid) latest = std::max(latest,p.epoch);
    if (out.available != (latest != 0) || (latest && latest != out.capturedEpoch)) return false;
    // Unavailable retained metadata may honestly predate the rolling seven-day range.
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
    bool ok = root && cJSON_IsBool(available) && integer(root, "version", 1, 2, version) &&
              integer(root, "captured_epoch", 0, UINT32_MAX, captured) &&
              integer(root, "age_seconds", 0, UINT32_MAX, age) &&
              integer(root, "timezone_offset_minutes", -720, 840, offset) && cells(root, "days", next->days, true) &&
              cells(root, "hours", next->hours, false);
    bool hasTrend = false;
    if (ok && version == 2) {
        const auto* tokenAvailable = cJSON_GetObjectItemCaseSensitive(root, "token_available");
        ok = cJSON_IsBool(tokenAvailable) && integer(root,"token_captured_epoch",0,UINT32_MAX,captured) &&
            integer(root,"token_age_seconds",0,UINT32_MAX,age);
        next->tokenAvailable = cJSON_IsTrue(tokenAvailable);
        auto* quotaTrend = cJSON_GetObjectItemCaseSensitive(root,"quota_trend");
        hasTrend = quotaTrend != nullptr;
        if (ok && hasTrend) ok = trend(quotaTrend,next->quotaTrend);
        if (ok && hasTrend && cJSON_IsTrue(available) != (next->tokenAvailable || next->quotaTrend.available)) ok = false;
    } else if (ok) next->tokenAvailable = cJSON_IsTrue(available);
    if (ok) {
        next->available           = next->tokenAvailable;
        next->capturedEpoch       = captured;
        next->ageSecondsAtReceipt = age;
        if (next->tokenAvailable && captured == 0) ok = false;
        next->timezoneOffsetMinutes = offset;
        next->receivedAtMs          = esp_timer_get_time() / 1000;
        next->quotaTrend.receivedAtMs = next->receivedAtMs;
    }
    cJSON_Delete(root);
    if (!ok) return false;
    if (xSemaphoreTake(mutex, pdMS_TO_TICKS(50)) != pdTRUE) return false;
    // Token freshness must not reject a fresh independently captured quota trend.
    if (current && current->tokenAvailable && (!next->tokenAvailable || next->capturedEpoch < current->capturedEpoch)) {
        next->days=current->days; next->hours=current->hours;
        next->available=current->available; next->tokenAvailable=current->tokenAvailable;
        next->capturedEpoch=current->capturedEpoch; next->ageSecondsAtReceipt=current->ageSecondsAtReceipt;
        next->receivedAtMs=current->receivedAtMs; next->timezoneOffsetMinutes=current->timezoneOffsetMinutes;
    }
    if (current && (!hasTrend || next->quotaTrend.endEpoch < current->quotaTrend.endEpoch))
        next->quotaTrend=current->quotaTrend;
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
