/* SPDX-License-Identifier: MIT */
#include "view_mosaico.h"
#include <hal/hal.h>
#include <host/network_quota.h>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <new>

namespace {
constexpr int Margin = 20, Columns = 6, SlotWidth = 73, CellWidth = 66, CellHeight = 44;
static_assert(SlotWidth >= 60 && Margin + Columns * SlotWidth <= 480 - Margin);
static_assert(128 + 5 * CellHeight <= 348 && 380 + 80 <= 480 - Margin);
constexpr uint32_t Green = 0x67E7AE, Gray = 0x343A40, Purple = 0x9868CD, Orange = 0xD18C37;
void panel(lv_obj_t* obj, int x, int y, int w, int h, uint32_t color = 0x15191F) {
    lv_obj_set_pos(obj, x, y); lv_obj_set_size(obj, w, h);
    lv_obj_set_style_bg_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(obj, 0, 0); lv_obj_set_style_radius(obj, 10, 0);
    lv_obj_set_style_pad_all(obj, 0, 0); lv_obj_set_style_shadow_width(obj, 0, 0);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(obj, LV_SCROLLBAR_MODE_OFF);
    lv_obj_set_scroll_dir(obj, LV_DIR_NONE);
}
lv_obj_t* label(lv_obj_t* parent, int x, int y, int w, const char* text, const lv_font_t* font = &lv_font_montserrat_14) {
    auto* obj = lv_label_create(parent); lv_obj_set_pos(obj, x, y); lv_obj_set_width(obj, w);
    lv_obj_set_style_text_font(obj, font, 0); lv_obj_set_style_text_color(obj, lv_color_hex(0xE9EDF2), 0);
    lv_label_set_text(obj, text); lv_obj_add_flag(obj, LV_OBJ_FLAG_EVENT_BUBBLE); return obj;
}
const char* quality(TokenHistoryQuality q) {
    switch (q) {
    case TokenHistoryQuality::Official: return "API day / timezone unknown";
    case TokenHistoryQuality::Observed: return "observed";
    case TokenHistoryQuality::Partial: return "partial";
    case TokenHistoryQuality::Gap: return "sampling gap (unallocated)";
    case TokenHistoryQuality::Correction: return "correction";
    case TokenHistoryQuality::Local: return "local / partial coverage";
    case TokenHistoryQuality::Pending: return "pending (not zero)";
    default: return "missing";
    }
}
void compact(uint64_t value, char* out, size_t size) {
    // Keep even the largest exact API integer inside the tile; details retain
    // the full integer. Ordinary account amounts continue to use K/M.
    if (value >= 1000000000000000ULL) std::snprintf(out, size, "%.1fP", static_cast<double>(value) / 1e15);
    else if (value >= 1000000000000ULL) std::snprintf(out, size, "%.1fT", static_cast<double>(value) / 1e12);
    else if (value >= 1000000000ULL) std::snprintf(out, size, "%.1fB", static_cast<double>(value) / 1e9);
    else if (value >= 1000000) std::snprintf(out, size, "%.1fM", static_cast<double>(value) / 1000000);
    else if (value >= 1000) std::snprintf(out, size, "%.1fK", static_cast<double>(value) / 1000);
    else std::snprintf(out, size, "%llu", static_cast<unsigned long long>(value));
}
void duration(uint32_t mins, char* out, size_t size) {
    if (!mins) std::snprintf(out, size, "window unknown");
    else if (mins % 1440 == 0) std::snprintf(out, size, "%ud", static_cast<unsigned>(mins / 1440));
    else if (mins % 60 == 0) std::snprintf(out, size, "%uh", static_cast<unsigned>(mins / 60));
    else std::snprintf(out, size, "%um", static_cast<unsigned>(mins));
}
void percent(uint16_t bp, char* out, size_t size) {
    if (bp % 100 == 0) std::snprintf(out, size, "%u%%", bp / 100);
    else if (bp % 10 == 0) std::snprintf(out, size, "%u.%u%%", bp / 100, (bp % 100) / 10);
    else std::snprintf(out, size, "%u.%02u%%", bp / 100, bp % 100);
}
}
namespace view {
CodexMicroView::~CodexMicroView() {
    GetNetworkQuota().setLocked(false);
    if (_root) lv_obj_delete(_root);
    GetHAL().setBackLightBrightness(_brightness, false);
}
void CodexMicroView::init(lv_obj_t* parent) {
    _quota.reset(new (std::nothrow) QuotaMonitorSnapshot());
    _history.reset(new (std::nothrow) TokenHistorySnapshot());
    if (!_quota || !_history) { _quota.reset(); _history.reset(); return; }
    lv_obj_remove_flag(parent, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scrollbar_mode(parent, LV_SCROLLBAR_MODE_OFF);
    lv_obj_set_scroll_dir(parent, LV_DIR_NONE);
    _brightness = std::max(10, GetHAL().getBackLightBrightness());
    _root = lv_obj_create(parent); panel(_root, 0, 0, 480, 480, 0x000000);
    lv_obj_add_event_cb(_root, touchEvent, LV_EVENT_PRESSED, this);
    label(_root, 20, 20, 290, "QUOTA MONITOR", &lv_font_montserrat_20);
    _battery = label(_root, 322, 24, 138, "Battery --");
    _quotaPage = lv_obj_create(_root); panel(_quotaPage, 20, 64, 440, 370, 0);
    lv_obj_add_flag(_quotaPage, LV_OBJ_FLAG_EVENT_BUBBLE);
    lv_obj_set_scroll_dir(_quotaPage, LV_DIR_VER);
    _quotaStatus = label(_quotaPage, 0, 0, 440, "Waiting for quota snapshot", &lv_font_montserrat_16);
    lv_obj_set_height(_quotaStatus, 42);
    lv_label_set_long_mode(_quotaStatus, LV_LABEL_LONG_MODE_DOTS);
    for (size_t i = 0; i < _cards.size(); ++i) {
        _cards[i] = lv_obj_create(_quotaPage); panel(_cards[i], 0, 52 + static_cast<int>(i) * 300, 436, 288);
        lv_obj_add_flag(_cards[i], LV_OBJ_FLAG_EVENT_BUBBLE);
        _cardTitles[i] = label(_cards[i], 14, 10, 408, "", &lv_font_montserrat_20);
        lv_obj_set_height(_cardTitles[i], 30);
        lv_label_set_long_mode(_cardTitles[i], LV_LABEL_LONG_MODE_DOTS);
        _cardValues[i] = label(_cards[i], 14, 54, 198, "--", &lv_font_montserrat_48);
        _secondValues[i] = label(_cards[i], 226, 54, 198, "--", &lv_font_montserrat_48);
        for (size_t j = 0; j < 2; ++j) {
            _windowLabels[i][j] = label(_cards[i], 14 + j * 212, 114, 198, "Window unknown", &lv_font_montserrat_20);
            _windowBars[i][j] = lv_bar_create(_cards[i]); panel(_windowBars[i][j], 14 + j * 212, 138, 198, 8, Gray);
            lv_obj_set_style_bg_color(_windowBars[i][j], lv_color_hex(Green), LV_PART_INDICATOR);
            lv_bar_set_range(_windowBars[i][j], 0, 10000);
        }
        _cardMeta[i] = label(_cards[i], 14, 160, 408, "", &lv_font_montserrat_20);
        lv_obj_set_height(_cardMeta[i], 116);
        lv_label_set_long_mode(_cardMeta[i], LV_LABEL_LONG_MODE_DOTS);
        lv_obj_add_flag(_cards[i], LV_OBJ_FLAG_HIDDEN);
    }
    _footer = label(_root, 20, 438, 440, "No cached data", &lv_font_montserrat_16);
    lv_obj_set_height(_footer, 22);
    lv_label_set_long_mode(_footer, LV_LABEL_LONG_MODE_DOTS);
    _historyPage = lv_obj_create(_root); panel(_historyPage, 20, 52, 440, 408, 0);
    lv_obj_add_flag(_historyPage, static_cast<lv_obj_flag_t>(LV_OBJ_FLAG_EVENT_BUBBLE | LV_OBJ_FLAG_HIDDEN));
    for (size_t i = 0; i < 2; ++i) {
        auto* button = lv_button_create(_historyPage); panel(button, static_cast<int>(i) * 224, 0, 216, 48);
        auto* text = label(button, 0, 12, 216, i == 0 ? "30 DAYS" : "24 HOURS", &lv_font_montserrat_20);
        lv_obj_set_style_text_align(text, LV_TEXT_ALIGN_CENTER, 0);
        _modeButtons[i] = button;
        _modeHits[i] = {this, i}; lv_obj_add_event_cb(button, modeEvent, LV_EVENT_CLICKED, &_modeHits[i]);
        lv_obj_add_flag(button, LV_OBJ_FLAG_EVENT_BUBBLE);
    }
    _range = label(_historyPage, 0, 54, 440, "History unavailable", &lv_font_montserrat_16);
    lv_obj_set_height(_range, 20);
    lv_label_set_long_mode(_range, LV_LABEL_LONG_MODE_DOTS);
    for (size_t i = 0; i < 30; ++i) {
        // The whole 73px-wide slot is clickable; its visible tile is inset.
        auto* button = lv_button_create(_historyPage); panel(button, (i % Columns) * SlotWidth, 76 + (i / Columns) * CellHeight, SlotWidth, CellHeight, 0);
        lv_obj_set_style_bg_opa(button, LV_OPA_TRANSP, 0);
        _cells[i] = lv_obj_create(button); panel(_cells[i], 3, 2, CellWidth, CellHeight - 4, Gray);
        lv_obj_remove_flag(_cells[i], LV_OBJ_FLAG_CLICKABLE); lv_obj_add_flag(_cells[i], LV_OBJ_FLAG_EVENT_BUBBLE);
        _cellLabels[i] = label(_cells[i], 0, 2, CellWidth, "--\n--", &lv_font_montserrat_16);
        lv_obj_set_height(_cellLabels[i], 36);
        lv_obj_set_style_text_line_space(_cellLabels[i], 0, 0);
        lv_label_set_long_mode(_cellLabels[i], LV_LABEL_LONG_MODE_DOTS);
        lv_obj_set_style_text_align(_cellLabels[i], LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_remove_flag(_cellLabels[i], LV_OBJ_FLAG_CLICKABLE);
        _hits[i] = {this, i}; lv_obj_add_event_cb(button, cellEvent, LV_EVENT_CLICKED, &_hits[i]);
        lv_obj_add_flag(button, LV_OBJ_FLAG_EVENT_BUBBLE);
    }
    _legend = label(_historyPage, 0, 296, 440, "");
    lv_obj_set_height(_legend, 32);
    lv_label_set_long_mode(_legend, LV_LABEL_LONG_MODE_DOTS);
    auto* detail = lv_obj_create(_historyPage); panel(detail, 0, 328, 440, 80);
    lv_obj_add_flag(detail, LV_OBJ_FLAG_EVENT_BUBBLE);
    _details = label(detail, 12, 8, 416, "Select a time slot", &lv_font_montserrat_16);
    lv_obj_set_height(_details, 64);
    lv_obj_set_style_text_line_space(_details, 0, 0);
    lv_label_set_long_mode(_details, LV_LABEL_LONG_MODE_DOTS);
    _lockPanel = lv_obj_create(_root); panel(_lockPanel, 0, 0, 480, 480, 0);
    _lockQuota = label(_lockPanel, 40, 178, 400, "Quota unknown", &lv_font_montserrat_48);
    _lockBattery = label(_lockPanel, 40, 306, 400, "Battery unknown", &lv_font_montserrat_20);
    lv_obj_add_flag(_lockPanel, LV_OBJ_FLAG_HIDDEN);
    _overlay = lv_obj_create(_root); panel(_overlay, 0, 0, 480, 480, 0);
    lv_obj_set_style_bg_opa(_overlay, LV_OPA_TRANSP, 0);
    lv_obj_add_flag(_overlay, static_cast<lv_obj_flag_t>(LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_HIDDEN));
    lv_obj_add_event_cb(_overlay, wakeEvent, LV_EVENT_ALL, this);
    _activity = lv_tick_get(); _refresh = _activity; refreshQuota(GetHAL().millis()); refreshHistory();
}
void CodexMicroView::touchEvent(lv_event_t* e) {
    auto* self = static_cast<CodexMicroView*>(lv_event_get_user_data(e));
    if (!self->_locked) self->_activity = lv_tick_get();
}
void CodexMicroView::wakeEvent(lv_event_t* e) {
    auto* self = static_cast<CodexMicroView*>(lv_event_get_user_data(e));
    if (lv_event_get_code(e) == LV_EVENT_PRESSED) {
        self->_wakeGesture = true;
        // Hide before changing brightness so HAL can invalidate the full screen.
        self->wakeDisplay();
        // Still target this overlay until release: do not activate underlying cells.
        lv_obj_remove_flag(self->_overlay, LV_OBJ_FLAG_HIDDEN);
    } else if (self->_wakeGesture && (lv_event_get_code(e) == LV_EVENT_RELEASED || lv_event_get_code(e) == LV_EVENT_PRESS_LOST)) {
        self->_wakeGesture = false; lv_obj_add_flag(self->_overlay, LV_OBJ_FLAG_HIDDEN);
    }
}
void CodexMicroView::cellEvent(lv_event_t* e) {
    auto* hit = static_cast<Hit*>(lv_event_get_user_data(e));
    if (!hit->owner->_suppressed && !hit->owner->_locked && !hit->owner->_wakeGesture) hit->owner->selectHistory(hit->index);
}
void CodexMicroView::modeEvent(lv_event_t* e) {
    auto* hit = static_cast<Hit*>(lv_event_get_user_data(e));
    if (!hit->owner->_suppressed && !hit->owner->_locked && !hit->owner->_wakeGesture) hit->owner->showHistory(hit->index == 1);
}
void CodexMicroView::refreshQuota(uint32_t now) {
    if (CopyQuotaMonitor(*_quota, now)) {
        _quotaRevision = _quota->revision;
    } else if (_quotaRevision != UINT32_MAX) {
        // Failed mutex acquisition must not freeze cached freshness indefinitely.
        const uint64_t age = static_cast<uint64_t>(_quota->ageSecondsAtReceipt) + (now - _quota->receivedAtMs) / 1000U;
        _quota->ageSeconds = static_cast<uint32_t>(std::min<uint64_t>(age, UINT32_MAX));
        _quota->stale = age > 130;
        _quota->available = age <= 600;
    }
    char buf[512];
    const auto battery = GetHAL().getBatteryLevel();
    if (GetHAL().isBatteryLevelValid()) std::snprintf(buf, sizeof(buf), "Battery ~%u%%", battery);
    else std::snprintf(buf, sizeof(buf), "Battery unknown");
    lv_label_set_text(_battery, buf);
    lv_label_set_text(_lockBattery, buf);
    std::snprintf(buf, sizeof(buf), "%s%s", !_quota->available ? "Waiting for quota snapshot" : "Remaining quota / cached official report", _quota->stale ? " STALE" : "");
    if (_quota->resetCreditsKnown) {
        char extra[48]; std::snprintf(extra, sizeof(extra), "\nReset credits: %u", _quota->resetCredits);
        std::strncat(buf, extra, sizeof(buf) - std::strlen(buf) - 1);
    }
    lv_label_set_text(_quotaStatus, buf);
    if (_quota->available && _quota->bucketCount > 1) lv_obj_add_flag(_quotaPage, LV_OBJ_FLAG_SCROLLABLE);
    else { lv_obj_remove_flag(_quotaPage, LV_OBJ_FLAG_SCROLLABLE); lv_obj_scroll_to_y(_quotaPage, 0, LV_ANIM_OFF); }
    const uint64_t epoch = static_cast<uint64_t>(_quota->capturedEpoch) + _quota->ageSeconds;
    for (size_t i = 0; i < _cards.size(); ++i) {
        if (!_quota->available || i >= _quota->bucketCount) { lv_obj_add_flag(_cards[i], LV_OBJ_FLAG_HIDDEN); continue; }
        lv_obj_remove_flag(_cards[i], LV_OBJ_FLAG_HIDDEN);
        const auto& bucket = _quota->buckets[i];
        std::snprintf(buf, sizeof(buf), "%s%s%s", bucket.name[0] ? bucket.name : bucket.id, bucket.plan[0] ? " / " : "", bucket.plan);
        lv_label_set_text(_cardTitles[i], buf);
        const size_t validWindows = static_cast<size_t>(bucket.windows[0].available) + static_cast<size_t>(bucket.windows[1].available);
        if (!validWindows) {
            lv_obj_remove_flag(_cardValues[i], LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_pos(_cardValues[i], 14, 54); lv_obj_set_width(_cardValues[i], 408);
            lv_obj_set_style_text_align(_cardValues[i], LV_TEXT_ALIGN_CENTER, 0);
            lv_obj_set_style_text_font(_cardValues[i], &lv_font_montserrat_24, 0);
            lv_label_set_text(_cardValues[i], "Quota unknown");
            lv_obj_add_flag(_secondValues[i], LV_OBJ_FLAG_HIDDEN);
        }
        char resets[220] = {};
        for (size_t j = 0; j < bucket.windows.size(); ++j) {
            const auto& w = bucket.windows[j];
            char windowText[40], windowPercent[24]; duration(w.durationMinutes, windowText, sizeof(windowText));
            percent(w.remainingBasisPoints, windowPercent, sizeof(windowPercent));
            auto* valueLabel = j ? _secondValues[i] : _cardValues[i];
            if (!w.available) {
                if (validWindows) lv_obj_add_flag(valueLabel, LV_OBJ_FLAG_HIDDEN);
                lv_obj_add_flag(_windowLabels[i][j], LV_OBJ_FLAG_HIDDEN);
                lv_obj_add_flag(_windowBars[i][j], LV_OBJ_FLAG_HIDDEN);
                continue;
            }
            const int x = validWindows == 1 ? 14 : 14 + static_cast<int>(j) * 212;
            const int width = validWindows == 1 ? 408 : 198;
            lv_obj_remove_flag(valueLabel, LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_pos(valueLabel, x, 54); lv_obj_set_width(valueLabel, width);
            lv_obj_set_style_text_font(valueLabel, &lv_font_montserrat_48, 0);
            lv_obj_set_style_text_align(valueLabel, LV_TEXT_ALIGN_CENTER, 0);
            lv_label_set_text(valueLabel, windowPercent);
            lv_obj_remove_flag(_windowLabels[i][j], LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_pos(_windowLabels[i][j], x, 114); lv_obj_set_width(_windowLabels[i][j], width);
            lv_obj_set_style_text_align(_windowLabels[i][j], LV_TEXT_ALIGN_CENTER, 0);
            lv_label_set_text(_windowLabels[i][j], windowText);
            lv_obj_remove_flag(_windowBars[i][j], LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_pos(_windowBars[i][j], x, 142); lv_obj_set_width(_windowBars[i][j], width);
            lv_bar_set_value(_windowBars[i][j], w.remainingBasisPoints, LV_ANIM_OFF);
            char dur[32], item[96]; duration(w.durationMinutes, dur, sizeof(dur));
            if (!w.resetEpoch || !_quota->capturedEpoch) std::snprintf(item, sizeof(item), "%s%s reset unknown", resets[0] ? "\n" : "", dur);
            else if (w.resetEpoch <= epoch) std::snprintf(item, sizeof(item), "%s%s reset due / awaiting snapshot", resets[0] ? "\n" : "", dur);
            else { const uint64_t minutes = (w.resetEpoch - epoch + 59) / 60;
                std::snprintf(item, sizeof(item), "%s%s reset in %lluh %llum", resets[0] ? "\n" : "", dur, static_cast<unsigned long long>(minutes / 60), static_cast<unsigned long long>(minutes % 60)); }
            std::strncat(resets, item, sizeof(resets) - std::strlen(resets) - 1);
        }

        std::snprintf(buf, sizeof(buf), "%s%s%s%s%s%s", resets, bucket.creditsKnown ? "\nCredits: " : "", bucket.creditsKnown ? (bucket.creditsUnlimited ? "unlimited" : (bucket.creditBalance[0] ? bucket.creditBalance : "unknown")) : "", bucket.reached[0] ? "\n" : "", bucket.reached, "");
        lv_label_set_text(_cardMeta[i], buf);
    }
    std::snprintf(buf, sizeof(buf), "%s | Age %us%s%s", GetNetworkQuota().connected() ? "Wi-Fi online" : "Wi-Fi offline", static_cast<unsigned>(_quota->ageSeconds), _quota->stale ? " STALE" : "", _locked ? " CACHED" : "");

    if (_quota->truncated) std::strncat(buf, " | first 8 buckets", sizeof(buf) - std::strlen(buf) - 1);
    lv_label_set_text(_footer, buf);
    char lockText[128] = "Quota unknown";
    if (_quota->available && _quota->bucketCount) {
        const auto& b = _quota->buckets[0]; char a[24] = "--", z[24] = "--";
        if (b.windows[0].available) percent(b.windows[0].remainingBasisPoints, a, sizeof(a));
        if (b.windows[1].available) percent(b.windows[1].remainingBasisPoints, z, sizeof(z));
        if (b.windows[0].available && b.windows[1].available)
            std::snprintf(lockText, sizeof(lockText), "%s   %s", a, z);
        else if (b.windows[0].available || b.windows[1].available)
            std::snprintf(lockText, sizeof(lockText), "%s", b.windows[0].available ? a : z);
    }
    lv_label_set_text(_lockQuota, lockText);
}
void CodexMicroView::refreshHistory() {
    if (_historyRevision == TokenHistoryRevision()) { updateHistoryHeader(); return; }
    if (CopyTokenHistory(*_history)) { _historyRevision = _history->revision; renderHistory(); }
}
void CodexMicroView::updateHistoryHeader(bool force) {
    if (!_history->available) {
        if (force) lv_label_set_text(_range, "History unavailable");
        return;
    }
    const uint64_t age = static_cast<uint64_t>(_history->ageSecondsAtReceipt) +
                         (GetHAL().millis() - _history->receivedAtMs) / 1000U;
    const bool stale = age > 600;
    const uint32_t key = static_cast<uint32_t>(std::min<uint64_t>(age / 60, 0x7FFFFFFF)) |
                         (stale ? 0x80000000U : 0U);
    if (!force && key == _historyAgeKey) return;
    _historyAgeKey = key;
    const auto* cells = _hourly ? _history->hours.data() : _history->days.data();
    const size_t count = _hourly ? 24 : 30;
    char text[128];
    std::snprintf(text, sizeof(text), "%s - %s | %s%llum", cells[0].label,
                  cells[count - 1].label, stale ? "STALE " : "cache ",
                  static_cast<unsigned long long>(age / 60));
    lv_label_set_text(_range, text);
}
void CodexMicroView::renderHistory() {
    const size_t count = _hourly ? 24 : 30;
    const auto* cells = _hourly ? _history->hours.data() : _history->days.data();
    char buf[200];
    updateHistoryHeader(true);
    for (size_t i = 0; i < _modeButtons.size(); ++i) {
        const bool selected = i == (_hourly ? 1U : 0U);
        lv_obj_set_style_bg_color(_modeButtons[i], lv_color_hex(selected ? (_hourly ? 0x286999 : 0x287A50) : 0x15191F), 0);
        lv_obj_set_style_border_color(_modeButtons[i], lv_color_hex(selected ? 0xE9EDF2 : Gray), 0);
        lv_obj_set_style_border_width(_modeButtons[i], selected ? 2 : 0, 0);
    }
    lv_label_set_text(_legend, _hourly ? "UTC+8 API delta, may lag; not consumption\nGap:purple Correction:orange Unknown:gray White:new day" : "Daily API timezone unknown\nGap:purple Correction:orange Unknown:gray White:new month");
    uint64_t maximum = 0;
    for (size_t i = 0; i < count; ++i) if (cells[i].valid) maximum = std::max(maximum, cells[i].tokens);
    for (size_t i = 0; i < 30; ++i) {
        auto* button = lv_obj_get_parent(_cells[i]);
        if (i >= count) { lv_obj_add_flag(button, LV_OBJ_FLAG_HIDDEN); continue; }
        lv_obj_remove_flag(button, LV_OBJ_FLAG_HIDDEN);
        const auto& c = cells[i]; uint32_t color = Gray;
        if (c.quality == TokenHistoryQuality::Gap) color = Purple;
        else if (c.quality == TokenHistoryQuality::Correction) color = Orange;
        else if (c.valid) {
            const double fraction = maximum ? std::log1p(static_cast<double>(c.tokens)) / std::log1p(static_cast<double>(maximum)) : 0;
            const uint32_t palettes[2][5] = {{0x20372D, 0x24543E, 0x287A50, 0x3EA76F, 0x67D997}, {0x1D293D, 0x234669, 0x286999, 0x388CCC, 0x65B6F0}};
            const size_t level = c.tokens ? std::max<size_t>(1, std::min<size_t>(4, static_cast<size_t>(std::ceil(fraction * 4)))) : 0;
            color = palettes[_hourly ? 1 : 0][level];
        }
        lv_obj_set_style_bg_color(_cells[i], lv_color_hex(color), 0);
        // Daily labels are ISO dates, hourly labels ISO date + hour range.
        const size_t len = std::strlen(c.label); char time[3] = "--";
        const size_t offset = _hourly ? 11 : 8;
        if (len >= offset + 2) { time[0] = c.label[offset]; time[1] = c.label[offset + 1]; }
        char amount[24] = "--"; if (c.valid) compact(c.tokens, amount, sizeof(amount));
        std::snprintf(buf, sizeof(buf), "%s\n%s", time, amount); lv_label_set_text(_cellLabels[i], buf);
        const bool boundary = i > 0 && std::strncmp(c.label, cells[i - 1].label, _hourly ? 10 : 7) != 0;
        lv_obj_set_style_border_color(_cells[i], lv_color_hex(boundary ? 0xDCE4F2 : (c.quality == TokenHistoryQuality::Partial ? Purple : Gray)), 0);
        lv_obj_set_style_border_width(_cells[i], boundary || c.quality == TokenHistoryQuality::Partial ? 2 : 0, 0);
    }
    renderSelection();
}
const TokenHistoryCell* CodexMicroView::selectedCell() const {
    if (!_history || _selected >= (_hourly ? 24U : 30U)) return nullptr;
    return _hourly ? &_history->hours[_selected] : &_history->days[_selected];
}
void CodexMicroView::historyDetails(char* out, size_t capacity) const {
    if (!out || !capacity) return;
    const auto* c = selectedCell(); if (!c) { std::snprintf(out, capacity, "no history selection"); return; }
    if (c->valid) std::snprintf(out, capacity, "%s: %llu tokens (%s)", c->label, static_cast<unsigned long long>(c->tokens), quality(c->quality));
    else std::snprintf(out, capacity, "%s: %s", c->label[0] ? c->label : "Unavailable", quality(c->quality));
}
void CodexMicroView::renderSelection() {
    char buf[256] = "Select a time slot";
    const auto* c = selectedCell();
    if (c && c->valid) {
        char amount[24]; compact(c->tokens, amount, sizeof(amount));
        std::snprintf(buf, sizeof(buf), "%s\n%llu tokens (%s)\n%s", c->label,
                      static_cast<unsigned long long>(c->tokens), amount,
                      _hourly ? "API reported / may lag; not consumption time" : "API day / timezone unknown");
    } else if (c && (c->quality == TokenHistoryQuality::Gap || c->quality == TokenHistoryQuality::Correction)) {
        std::snprintf(buf, sizeof(buf), "%s\n%s: %+lld\nNot hourly consumption", c->label,
                      c->quality == TokenHistoryQuality::Gap ? "Unallocated gap delta" : "API correction",
                      static_cast<long long>(c->quality == TokenHistoryQuality::Gap ? c->gapDelta : c->correctionDelta));
    } else if (c) std::snprintf(buf, sizeof(buf), "%s\n%s", c->label[0] ? c->label : "Unavailable", quality(c->quality));
    lv_label_set_text(_details, buf);
    for (size_t i = 0; i < 30; ++i) { lv_obj_set_style_outline_color(_cells[i], lv_color_hex(Green), 0); lv_obj_set_style_outline_width(_cells[i], i == _selected ? 2 : 0, 0); }
}
bool CodexMicroView::selectHistory(size_t index) {
    if (!_history || index >= (_hourly ? 24U : 30U)) return false;
    _selected = index; _activity = lv_tick_get(); renderSelection(); return true;
}
bool CodexMicroView::showHistory(bool hourly) {
    if (!ready()) return false;
    _hourly = hourly; if (_selected >= (_hourly ? 24U : 30U)) _selected = SIZE_MAX;
    refreshHistory(); renderHistory(); return setPageForDebug(Page::History);
}
bool CodexMicroView::setPageForDebug(Page page) {
    if (!ready() || page == Page::Agent) return false;
    _page = page; _activity = lv_tick_get();
    if (page == Page::Command) { lv_obj_remove_flag(_quotaPage, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_historyPage, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_footer, LV_OBJ_FLAG_HIDDEN); }
    else { refreshHistory(); lv_obj_add_flag(_quotaPage, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_historyPage, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_footer, LV_OBJ_FLAG_HIDDEN); }
    return true;
}
void CodexMicroView::togglePage() {
    if (_suppressed || !ready()) return;
    if (_locked) { wakeDisplay(); return; }
    setPageForDebug(_page == Page::Command ? Page::History : Page::Command);
}
void CodexMicroView::wakeDisplay() {
    GetNetworkQuota().setLocked(false); _activity = lv_tick_get(); _locked = false;
    if (_overlay) lv_obj_add_flag(_overlay, LV_OBJ_FLAG_HIDDEN);
    if (_lockPanel) lv_obj_add_flag(_lockPanel, LV_OBJ_FLAG_HIDDEN);
    GetHAL().setBackLightBrightness(_brightness, false);
}
void CodexMicroView::lockDisplay() {
    if (_locked || !ready()) return;
    _locked = true; GetNetworkQuota().setLocked(true);
    lv_obj_remove_flag(_lockPanel, LV_OBJ_FLAG_HIDDEN); lv_obj_move_foreground(_lockPanel);
    lv_obj_remove_flag(_overlay, LV_OBJ_FLAG_HIDDEN); lv_obj_move_foreground(_overlay);
    refreshQuota(GetHAL().millis()); ++_lockRefreshCount; _refresh = lv_tick_get();
    GetHAL().setBackLightBrightness(8, false);
}
bool CodexMicroView::lockForDebug() { if (!ready()) return false; lockDisplay(); return _locked; }
void CodexMicroView::update(const CodexMicroState&) {
    if (!ready()) return;
    const uint32_t tick = lv_tick_get();
    bool interacting = lv_obj_is_scrolling(_quotaPage);
    for (auto* input = lv_indev_get_next(nullptr); input; input = lv_indev_get_next(input))
        interacting = interacting || lv_indev_get_state(input) == LV_INDEV_STATE_PRESSED;
    if (!_locked && interacting) _activity = tick;
    if (!_locked && !interacting && tick - _activity >= 60000) lockDisplay();
    // Revision checks are local memory only; no touch or UI path performs HTTP.
    if (tick - _refresh >= 60000) {
        _refresh = tick; refreshQuota(GetHAL().millis());
        if (!_locked) refreshHistory();
        if (_locked) ++_lockRefreshCount;
        static constexpr int offsets[4][2] = {{-1, -1}, {1, -1}, {1, 1}, {-1, 1}};
        const auto& shift = offsets[(tick / 60000) % 4]; lv_obj_set_pos(_root, shift[0], shift[1]);
    } else if (!_locked) {
        if (_quotaRevision != QuotaMonitorRevision()) refreshQuota(GetHAL().millis());
        if (_page == Page::History) refreshHistory();
    }
}
}
