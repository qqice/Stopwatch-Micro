/* SPDX-License-Identifier: MIT */
#include "view_mosaico.h"
#include "dot_widgets.h"
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
using namespace mosaico_dot;
constexpr uint32_t Green = 0x67E7AE, Gray = 0x343A40, Purple = 0x9868CD, Orange = 0xD18C37;
constexpr uint32_t ResetPurple = 0xB399F7, Gold = 0xE9C46A, Blue = 0x65B6F0, Cyan = 0x68C9D6;
uint32_t levelColor(uint16_t bp) {
    return bp <= 2000 ? 0xE87575 : (bp <= 5000 ? 0xD6B46A : Green);
}
constexpr uint32_t dimCachedColor(uint32_t color) {
    // Keep percentage/alarm hue readable on the dim lock screen. The amber
    // age indicator distinguishes cached data; gray is reserved for unknown.
    return (((color >> 16) & 255U) * 4U / 5U << 16) |
           (((color >> 8) & 255U) * 4U / 5U << 8) |
           ((color & 255U) * 4U / 5U);
}
uint32_t quotaLevelColor(uint16_t bp, bool stale) {
    const uint32_t color = levelColor(bp);
    return stale ? dimCachedColor(color) : color;
}
static_assert(dimCachedColor(Green) == 0x52B88B, "cached green remains readable");
void place(lv_obj_t* obj, int x, int y) { if (obj) lv_obj_set_pos(obj, x, y); }
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
void percent(uint16_t bp, char* out, size_t size) {
    if (bp % 100 == 0) std::snprintf(out, size, "%u%%", bp / 100);
    else if (bp % 10 == 0) std::snprintf(out, size, "%u.%u%%", bp / 100, (bp % 100) / 10);
    else std::snprintf(out, size, "%u.%02u%%", bp / 100, bp % 100);
}
}
namespace view {
CodexMicroView::~CodexMicroView() {
    GetHAL().setTouchIdlePolling(false);
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
    _wifiIcon = createIcon(_root, Icon::WifiOff, 28, Orange); place(_wifiIcon, 20, 22);
    _batteryIcon = createIcon(_root, Icon::Battery, 32); place(_batteryIcon, 296, 20);
    _boltIcon = createIcon(_root, Icon::Bolt, 24, Gold); place(_boltIcon, 266, 24);
    _battery = label(_root, 338, 26, 122, "?", &lv_font_montserrat_20);
    for (size_t i = 0; i < 3; ++i) { _resetIcons[i] = createIcon(_root, Icon::ResetCard, 28, Gray); place(_resetIcons[i], 70 + i * 34, 22); }
    _resetCount = label(_root, 174, 26, 84, "?", &lv_font_montserrat_20);
    _quotaPage = lv_obj_create(_root); panel(_quotaPage, 20, 64, 440, 370, 0);
    lv_obj_add_flag(_quotaPage, LV_OBJ_FLAG_EVENT_BUBBLE); lv_obj_set_scroll_dir(_quotaPage, LV_DIR_VER);
    _quotaStatus = createText(_quotaPage, 440, 78); place(_quotaStatus, 0, 116); setText(_quotaStatus, "--", Gray);
    for (size_t i = 0; i < _cards.size(); ++i) {
        _cards[i] = lv_obj_create(_quotaPage); panel(_cards[i], 0, static_cast<int>(i) * 362, 436, 350, 0);
        lv_obj_add_flag(_cards[i], LV_OBJ_FLAG_EVENT_BUBBLE);
        _cardTitles[i] = label(_cards[i], 14, 0, 408, "", &lv_font_montserrat_20);
        lv_obj_set_height(_cardTitles[i], 30); lv_label_set_long_mode(_cardTitles[i], LV_LABEL_LONG_MODE_DOTS);
        _cardBadges[i] = createText(_cards[i], 408, 40, 5, 0xE9EDF2); place(_cardBadges[i], 14, 0);
        _cardValues[i] = createText(_cards[i], 160, 56, 4); place(_cardValues[i], 52, 48);
        _secondValues[i] = createText(_cards[i], 160, 56, 4); place(_secondValues[i], 264, 48);
        for (size_t j = 0; j < 2; ++j) {
            const int x = 14 + j * 212;
            _windowBars[i][j] = createMeter(_cards[i], 198, 24, 3); place(_windowBars[i][j], x, 110);
            _quotaIcons[i][j] = createIcon(_cards[i], Icon::Quota, 28, Green); place(_quotaIcons[i][j], x, 62);
            _hourglassIcons[i][j] = createIcon(_cards[i], Icon::Hourglass, 28, Cyan); place(_hourglassIcons[i][j], x, 174);
            _resetTimes[i][j] = createText(_cards[i], 160, 56, 4, Cyan); place(_resetTimes[i][j], x + 38, 160);
            _resetBars[i][j] = createMeter(_cards[i], 198, 24, 3); place(_resetBars[i][j], x, 222);
        }
        _creditIcons[i] = createIcon(_cards[i], Icon::Coin, 28, Gold); place(_creditIcons[i], 14, 278);
        _creditValues[i] = label(_cards[i], 52, 280, 370, "", &lv_font_montserrat_20);
        lv_obj_set_style_text_color(_creditValues[i], lv_color_hex(Gold), 0);
        lv_obj_set_height(_creditValues[i], 26); lv_label_set_long_mode(_creditValues[i], LV_LABEL_LONG_MODE_DOTS);
        _cardMeta[i] = label(_cards[i], 14, 314, 408, "", &lv_font_montserrat_20);
        lv_obj_set_height(_cardMeta[i], 30); lv_label_set_long_mode(_cardMeta[i], LV_LABEL_LONG_MODE_DOTS);
        lv_obj_add_flag(_cards[i], LV_OBJ_FLAG_HIDDEN);
    }
    _clockIcon = createIcon(_root, Icon::Clock, 20, Gray); place(_clockIcon, 20, 440);
    _footer = createText(_root, 180, 22, 3); place(_footer, 44, 438);
    _batteryCapacity = label(_root, 220, 440, 240, "", &lv_font_montserrat_14);
    lv_obj_set_height(_batteryCapacity, 18); lv_label_set_long_mode(_batteryCapacity, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_style_text_align(_batteryCapacity, LV_TEXT_ALIGN_RIGHT, 0);
    _bucketCount = label(_root, 290, 440, 170, "", &lv_font_montserrat_14);
    lv_obj_set_style_text_align(_bucketCount, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_add_flag(_bucketCount, LV_OBJ_FLAG_HIDDEN);
    _historyPage = lv_obj_create(_root); panel(_historyPage, 20, 52, 440, 408, 0);
    lv_obj_add_flag(_historyPage, static_cast<lv_obj_flag_t>(LV_OBJ_FLAG_EVENT_BUBBLE | LV_OBJ_FLAG_HIDDEN));
    for (size_t i = 0; i < 2; ++i) {
        auto* button = lv_button_create(_historyPage); panel(button, static_cast<int>(i) * 224, 0, 216, 48);
        auto* text = label(button, 0, 12, 216, i == 0 ? "30d" : "24h", &lv_font_montserrat_20);
        lv_obj_set_style_text_align(text, LV_TEXT_ALIGN_CENTER, 0);
        auto* timezone = label(button, 148, 32, 62, i == 0 ? "TZ?" : "UTC+8", &lv_font_montserrat_12);
        lv_obj_set_style_text_align(timezone, LV_TEXT_ALIGN_RIGHT, 0);
        lv_obj_remove_flag(timezone, LV_OBJ_FLAG_CLICKABLE);
        if (i == 1) {
            // Delta is data notation; the bundled Latin font has no Greek glyph.
            lv_obj_set_width(text, 176);
            static const lv_point_precise_t delta[] = {{8, 0}, {16, 16}, {0, 16}, {8, 0}};
            auto* symbol = lv_line_create(button); lv_line_set_points(symbol, delta, 4);
            lv_obj_set_pos(symbol, 124, 16); lv_obj_set_style_line_width(symbol, 2, 0);
            lv_obj_set_style_line_color(symbol, lv_color_hex(0xE9EDF2), 0);
            lv_obj_remove_flag(symbol, LV_OBJ_FLAG_CLICKABLE);
        }
        _modeButtons[i] = button;
        _modeHits[i] = {this, i}; lv_obj_add_event_cb(button, modeEvent, LV_EVENT_CLICKED, &_modeHits[i]);
        lv_obj_add_flag(button, LV_OBJ_FLAG_EVENT_BUBBLE);
    }
    _range = label(_historyPage, 0, 54, 324, "--", &lv_font_montserrat_16);
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
    _historyClock = createIcon(_historyPage, Icon::Clock, 20, Gray); place(_historyClock, 330, 54);
    _historyAge = createText(_historyPage, 86, 20, 2); place(_historyAge, 354, 54);
    auto* detail = lv_obj_create(_historyPage); panel(detail, 0, 296, 440, 112, 0);
    lv_obj_add_flag(detail, LV_OBJ_FLAG_EVENT_BUBBLE);
    _details = label(detail, 12, 8, 416, "", &lv_font_montserrat_20);
    lv_obj_set_height(_details, 58); lv_obj_set_style_text_line_space(_details, 0, 0);
    lv_label_set_long_mode(_details, LV_LABEL_LONG_MODE_DOTS);
    _qualityIcon = createIcon(detail, Icon::Unknown, 28, Gray); place(_qualityIcon, 12, 76);
    _qualityValue = createText(detail, 370, 36, 4); place(_qualityValue, 52, 68);
    _lockPanel = lv_obj_create(_root); panel(_lockPanel, 0, 0, 480, 480, 0);
    _lockQuota = createText(_lockPanel, 400, 90, 12); place(_lockQuota, 40, 172);
    _lockBatteryIcon = createIcon(_lockPanel, Icon::Battery, 36); place(_lockBatteryIcon, 152, 306);
    _lockBattery = label(_lockPanel, 204, 310, 200, "?", &lv_font_montserrat_20);
    lv_obj_add_flag(_lockPanel, LV_OBJ_FLAG_HIDDEN);
    _overlay = lv_obj_create(_root); panel(_overlay, 0, 0, 480, 480, 0);
    lv_obj_set_style_bg_opa(_overlay, LV_OPA_TRANSP, 0);
    lv_obj_add_flag(_overlay, static_cast<lv_obj_flag_t>(LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_HIDDEN));
    lv_obj_add_event_cb(_overlay, wakeEvent, LV_EVENT_ALL, this);
    bool widgetsReady = _wifiIcon && _batteryIcon && _boltIcon && _resetCount && _quotaStatus && _clockIcon && _footer && _historyClock && _historyAge && _qualityIcon && _qualityValue && _lockQuota && _lockBatteryIcon;
    for (auto* icon : _resetIcons) widgetsReady = widgetsReady && icon;
    for (size_t i = 0; i < _cards.size(); ++i) {
        widgetsReady = widgetsReady && _cardBadges[i] && _cardValues[i] && _secondValues[i] && _creditIcons[i];
        for (size_t j = 0; j < 2; ++j) widgetsReady = widgetsReady && _windowBars[i][j] && _quotaIcons[i][j] && _hourglassIcons[i][j] && _resetTimes[i][j] && _resetBars[i][j];
    }
    if (!widgetsReady) { lv_obj_delete(_root); _root = nullptr; return; }
    _activity = lv_tick_get(); _motionEpoch = _activity; _refresh = _activity; refreshQuota(GetHAL().millis()); refreshHistory();
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
void CodexMicroView::refreshBattery(uint32_t now) {
    const auto telemetry = GetHAL().batteryTelemetry(false);
    _batteryReadTick = now; _batterySeen = true; _batteryValid = telemetry.valid;
    _batteryCharging = telemetry.valid && telemetry.currentMa > 3 && GetHAL().isBatteryCharging();
    _capacityKnown = telemetry.valid && telemetry.capacityValid && telemetry.nominalConfigured;
    char text[80];
    if (telemetry.valid) std::snprintf(text, sizeof(text), "%u%%%s", static_cast<unsigned>(telemetry.reportedSoc), telemetry.nominalConfigured ? "*" : "?");
    else std::snprintf(text, sizeof(text), "?");
    const uint32_t color = telemetry.valid ? levelColor(static_cast<uint16_t>(telemetry.reportedSoc) * 100) : Gray;
    if (_locked) {
        lv_label_set_text(_lockBattery, text);
        lv_obj_set_style_text_color(_lockBattery, lv_color_hex(color), 0);
        setIcon(_lockBatteryIcon, Icon::Battery, color, telemetry.reportedSoc, telemetry.valid);
    } else {
        lv_label_set_text(_battery, text);
        lv_obj_set_style_text_color(_battery, lv_color_hex(color), 0);
        setIcon(_batteryIcon, Icon::Battery, color, telemetry.reportedSoc, telemetry.valid);
        if (_batteryCharging) lv_obj_remove_flag(_boltIcon, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(_boltIcon, LV_OBJ_FLAG_HIDDEN);
        if (_capacityKnown && _page == Page::Command && !_quota->truncated) {
            std::snprintf(text, sizeof(text), "%u/%u mAh", static_cast<unsigned>(telemetry.remainingMah), static_cast<unsigned>(telemetry.fullMah));
            lv_label_set_text(_batteryCapacity, text); lv_obj_remove_flag(_batteryCapacity, LV_OBJ_FLAG_HIDDEN);
        } else lv_obj_add_flag(_batteryCapacity, LV_OBJ_FLAG_HIDDEN);
    }

}
void CodexMicroView::stopAnimations() {
    setMotion(_batteryIcon, 0, false);
    for (auto* icon : _resetIcons) setMotion(icon, 0, false);
    for (size_t i = 0; i < _cards.size(); ++i) {
        setMotion(_creditIcons[i], 0, false);
        for (size_t j = 0; j < 2; ++j) { setMotion(_windowBars[i][j], 0, false); setMotion(_resetBars[i][j], 0, false); }
    }
}
void CodexMicroView::updateAnimations(uint32_t tick) {
    if (_locked) return;
    if (tick - _motionTick < 100) return;
    _motionTick = tick;
    const uint16_t phase = static_cast<uint16_t>(((tick - _motionEpoch) % 12000U) * 360U / 12000U);
    const uint16_t meterPhase = static_cast<uint16_t>(((tick - _motionEpoch) % 8000U) * 360U / 8000U);
    setMotion(_batteryIcon, phase, _batteryValid && _batteryCharging);
    const uint32_t now = GetHAL().millis();
    const uint64_t age = static_cast<uint64_t>(_quota->ageSecondsAtReceipt) + (now - _quota->receivedAtMs) / 1000U;
    const bool quotaActive = _page == Page::Command && _quotaRevision != UINT32_MAX && _quota->available && !_quota->stale && age <= 130 && GetNetworkQuota().connected();
    for (size_t i = 0; i < _resetIcons.size(); ++i) {
        const bool active = quotaActive && _quota->resetCreditsKnown && _quota->resetCredits && !lv_obj_has_flag(_resetIcons[i], LV_OBJ_FLAG_HIDDEN);
        setMotion(_resetIcons[i], phase, active);
    }
    const int scrollY = lv_obj_get_scroll_y(_quotaPage);
    const uint64_t epoch = static_cast<uint64_t>(_quota->capturedEpoch) + age;
    for (size_t i = 0; i < _cards.size(); ++i) {
        const int top = lv_obj_get_y(_cards[i]) - scrollY;
        const bool visible = quotaActive && i < _quota->bucketCount && !lv_obj_has_flag(_cards[i], LV_OBJ_FLAG_HIDDEN) && top < 370 && top + 350 > 0;
        const auto& bucket = _quota->buckets[i];
        setMotion(_creditIcons[i], phase, visible && top + 278 < 370 && top + 306 > 0 && bucket.creditsKnown && !lv_obj_has_flag(_creditIcons[i], LV_OBJ_FLAG_HIDDEN));
        for (size_t j = 0; j < 2; ++j) {
            const auto& window = bucket.windows[j];
            setMotion(_windowBars[i][j], meterPhase, visible && top + 110 < 370 && top + 134 > 0 && window.available && window.remainingBasisPoints > 0);
            const bool knownTime = window.available && window.resetEpoch && _quota->capturedEpoch && window.durationMinutes && window.resetEpoch > epoch;
            setMotion(_resetBars[i][j], meterPhase, visible && top + 222 < 370 && top + 246 > 0 && knownTime);
        }
    }
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
    char buf[128];
    if (!_batterySeen || _locked || now - _batteryReadTick >= 5000) refreshBattery(now);
    const bool online = GetNetworkQuota().connected();
    setIcon(_wifiIcon, online ? Icon::Wifi : Icon::WifiOff, online ? Blue : Orange);
    const unsigned cards = _quota->resetCreditsKnown ? std::min<unsigned>(3, _quota->resetCredits) : 1;
    for (size_t i = 0; i < _resetIcons.size(); ++i) {
        if (i < std::max(1U, cards)) { lv_obj_remove_flag(_resetIcons[i], LV_OBJ_FLAG_HIDDEN); setIcon(_resetIcons[i], Icon::ResetCard, _quota->resetCreditsKnown && _quota->resetCredits ? ResetPurple : Gray); }
        else lv_obj_add_flag(_resetIcons[i], LV_OBJ_FLAG_HIDDEN);
    }
    if (!_quota->resetCreditsKnown) std::snprintf(buf, sizeof(buf), "?");
    else if (!_quota->resetCredits) std::snprintf(buf, sizeof(buf), "0");
    else if (_quota->resetCredits > 3) std::snprintf(buf, sizeof(buf), "+%u", _quota->resetCredits - 3);
    else buf[0] = 0;
    lv_label_set_text(_resetCount, buf);
    if (_quota->available && _quota->bucketCount) lv_obj_add_flag(_quotaStatus, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_remove_flag(_quotaStatus, LV_OBJ_FLAG_HIDDEN);
    if (_quota->available && _quota->bucketCount > 1) lv_obj_add_flag(_quotaPage, LV_OBJ_FLAG_SCROLLABLE);
    else { lv_obj_remove_flag(_quotaPage, LV_OBJ_FLAG_SCROLLABLE); lv_obj_scroll_to_y(_quotaPage, 0, LV_ANIM_OFF); }
    const uint64_t epoch = static_cast<uint64_t>(_quota->capturedEpoch) + _quota->ageSeconds;
    for (size_t i = 0; i < _cards.size(); ++i) {
        if (!_quota->available || i >= _quota->bucketCount) { lv_obj_add_flag(_cards[i], LV_OBJ_FLAG_HIDDEN); continue; }
        lv_obj_remove_flag(_cards[i], LV_OBJ_FLAG_HIDDEN);
        const auto& bucket = _quota->buckets[i];
        std::snprintf(buf, sizeof(buf), "%s%s%s", bucket.name[0] ? bucket.name : bucket.id, bucket.plan[0] ? " / " : "", bucket.plan);
        lv_label_set_text(_cardTitles[i], buf);
        const bool proAlias = std::strcmp(bucket.id, "codex") == 0 && std::strcmp(bucket.plan, "pro") == 0;
        if (proAlias) {
            lv_obj_add_flag(_cardTitles[i], LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_cardBadges[i], LV_OBJ_FLAG_HIDDEN);
            setText(_cardBadges[i], "Pro200", 0xE9EDF2);
        } else { lv_obj_remove_flag(_cardTitles[i], LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_cardBadges[i], LV_OBJ_FLAG_HIDDEN); }
        const size_t validWindows = static_cast<size_t>(bucket.windows[0].available) + static_cast<size_t>(bucket.windows[1].available);
        if (!validWindows) { lv_obj_remove_flag(_cardValues[i], LV_OBJ_FLAG_HIDDEN); place(_cardValues[i], 54, 48); lv_obj_set_width(_cardValues[i], 368); setTextPitch(_cardValues[i], 8); setText(_cardValues[i], "--", Gray); lv_obj_add_flag(_secondValues[i], LV_OBJ_FLAG_HIDDEN); }
        for (size_t j = 0; j < 2; ++j) {
            const auto& w = bucket.windows[j]; auto* value = j ? _secondValues[i] : _cardValues[i];
            if (!w.available) {
                if (validWindows) lv_obj_add_flag(value, LV_OBJ_FLAG_HIDDEN);
                for (auto* obj : {_windowBars[i][j], _quotaIcons[i][j], _hourglassIcons[i][j], _resetTimes[i][j], _resetBars[i][j]}) lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
                continue;
            }
            const int x = validWindows == 1 ? 14 : 14 + static_cast<int>(j) * 212;
            const int width = validWindows == 1 ? 408 : 198;
            for (auto* obj : {value, _windowBars[i][j], _quotaIcons[i][j], _hourglassIcons[i][j], _resetTimes[i][j], _resetBars[i][j]}) lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN);
            const int iconSize = validWindows == 1 ? 32 : 28;
            const int textOffset = validWindows == 1 ? 40 : 38;
            const int textWidth = width - textOffset;
            const int pitch = validWindows == 1 ? 8 : 4;
            place(_quotaIcons[i][j], x, 62); lv_obj_set_size(_quotaIcons[i][j], iconSize, iconSize);
            const uint32_t quotaColor = quotaLevelColor(w.remainingBasisPoints, _quota->stale);
            setIcon(_quotaIcons[i][j], Icon::Quota, quotaColor);
            place(value, x + textOffset, 48); lv_obj_set_width(value, textWidth); setTextPitch(value, pitch);
            percent(w.remainingBasisPoints, buf, sizeof(buf)); setText(value, buf, quotaColor);
            place(_windowBars[i][j], x, 110); lv_obj_set_width(_windowBars[i][j], width); setMeter(_windowBars[i][j], w.remainingBasisPoints, true, quotaColor);
            place(_hourglassIcons[i][j], x, 174); lv_obj_set_size(_hourglassIcons[i][j], iconSize, iconSize);
            place(_resetTimes[i][j], x + textOffset, 160); lv_obj_set_width(_resetTimes[i][j], textWidth); setTextPitch(_resetTimes[i][j], pitch);
            const bool resetKnown = w.resetEpoch && _quota->capturedEpoch;
            const uint64_t seconds = resetKnown && w.resetEpoch > epoch ? w.resetEpoch - epoch : 0;
            if (!resetKnown) std::snprintf(buf, sizeof(buf), "--");
            else { const uint64_t minutes = (seconds + 59) / 60;
                if (minutes >= 1440) std::snprintf(buf, sizeof(buf), "%llud%lluh", static_cast<unsigned long long>(minutes / 1440), static_cast<unsigned long long>((minutes % 1440) / 60));
                else if (minutes >= 60) std::snprintf(buf, sizeof(buf), "%lluh%llum", static_cast<unsigned long long>(minutes / 60), static_cast<unsigned long long>(minutes % 60));
                else std::snprintf(buf, sizeof(buf), "%llum", static_cast<unsigned long long>(minutes));
            }
            setText(_resetTimes[i][j], buf, resetKnown ? Cyan : Gray);
            setIcon(_hourglassIcons[i][j], Icon::Hourglass, resetKnown ? Cyan : Gray);
            place(_resetBars[i][j], x, 222); lv_obj_set_width(_resetBars[i][j], width);
            const uint64_t durationSeconds = static_cast<uint64_t>(w.durationMinutes) * 60;
            const uint16_t timeBp = durationSeconds ? static_cast<uint16_t>(std::min<uint64_t>(10000, seconds * 10000 / durationSeconds)) : 0;
            setMeter(_resetBars[i][j], timeBp, resetKnown && durationSeconds, Cyan);
        }
        if (bucket.creditsKnown) {
            lv_obj_remove_flag(_creditIcons[i], LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_creditValues[i], LV_OBJ_FLAG_HIDDEN);
            std::snprintf(buf, sizeof(buf), "%s Points", bucket.creditsUnlimited ? "inf" : (bucket.creditBalance[0] ? bucket.creditBalance : "--"));
            lv_label_set_text(_creditValues[i], buf);
        } else { lv_obj_add_flag(_creditIcons[i], LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_creditValues[i], LV_OBJ_FLAG_HIDDEN); }
        lv_label_set_text(_cardMeta[i], bucket.reached);
    }
    if (_capacityKnown && _page == Page::Command && !_quota->truncated) lv_obj_remove_flag(_batteryCapacity, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(_batteryCapacity, LV_OBJ_FLAG_HIDDEN);
    const bool haveSnapshot = _quotaRevision != UINT32_MAX;
    setIcon(_clockIcon, Icon::Clock, _quota->stale ? Orange : Gray);
    if (haveSnapshot) std::snprintf(buf, sizeof(buf), "%um", static_cast<unsigned>(_quota->ageSeconds / 60)); else std::snprintf(buf, sizeof(buf), "?");
    setText(_footer, buf, _quota->stale ? Orange : Gray);
    if (_quota->truncated && _page == Page::Command) {
        std::snprintf(buf, sizeof(buf), "%u/%lu", _quota->bucketCount,
                      static_cast<unsigned long>(_quota->totalBuckets));
        lv_label_set_text(_bucketCount, buf);
        lv_obj_remove_flag(_bucketCount, LV_OBJ_FLAG_HIDDEN);
    } else lv_obj_add_flag(_bucketCount, LV_OBJ_FLAG_HIDDEN);
    char lockText[64] = "--";
    if (_quota->available && _quota->bucketCount) {
        const auto& b = _quota->buckets[0]; char a[24] = "", z[24] = "";
        if (b.windows[0].available) percent(b.windows[0].remainingBasisPoints, a, sizeof(a));
        if (b.windows[1].available) percent(b.windows[1].remainingBasisPoints, z, sizeof(z));
        if (a[0] && z[0]) std::snprintf(lockText, sizeof(lockText), "%s %s", a, z);
        else if (a[0] || z[0]) std::snprintf(lockText, sizeof(lockText), "%s", a[0] ? a : z);
    }
    const auto& firstWindow = _quota->buckets[0].windows;
    const uint16_t lockBp = firstWindow[0].available ? firstWindow[0].remainingBasisPoints : firstWindow[1].remainingBasisPoints;
    const bool lockKnown = _quota->available && _quota->bucketCount &&
                           (firstWindow[0].available || firstWindow[1].available);
    setText(_lockQuota, lockText, lockKnown ? quotaLevelColor(lockBp, _quota->stale) : Gray);

}
void CodexMicroView::refreshHistory() {
    if (_historyRevision == TokenHistoryRevision()) { updateHistoryHeader(); return; }
    if (CopyTokenHistory(*_history)) { _historyRevision = _history->revision; renderHistory(); }
}
void CodexMicroView::updateHistoryHeader(bool force) {
    if (!_history->available) { if (force) { lv_label_set_text(_range, "--"); setText(_historyAge, "?", Gray); } return; }
    const uint64_t age = static_cast<uint64_t>(_history->ageSecondsAtReceipt) + (GetHAL().millis() - _history->receivedAtMs) / 1000U;
    const bool stale = age > 600;
    const uint32_t key = static_cast<uint32_t>(std::min<uint64_t>(age / 60, 0x7FFFFFFF)) | (stale ? 0x80000000U : 0U);
    if (!force && key == _historyAgeKey) return;
    _historyAgeKey = key;
    const auto* cells = _hourly ? _history->hours.data() : _history->days.data(); const size_t count = _hourly ? 24 : 30;
    char text[128]; std::snprintf(text, sizeof(text), "%s - %s", cells[0].label, cells[count - 1].label); lv_label_set_text(_range, text);
    std::snprintf(text, sizeof(text), "%llum", static_cast<unsigned long long>(age / 60));
    setText(_historyAge, text, stale ? Orange : Gray); setIcon(_historyClock, Icon::Clock, stale ? Orange : Gray);
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
        char amount[24] = "?"; if (c.valid) compact(c.tokens, amount, sizeof(amount));
        else if (c.quality == TokenHistoryQuality::Gap) std::snprintf(amount, sizeof(amount), "+");
        else if (c.quality == TokenHistoryQuality::Correction) std::snprintf(amount, sizeof(amount), "-");
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
    char buf[160] = ""; const auto* c = selectedCell();
    if (c && c->valid) std::snprintf(buf, sizeof(buf), "%s\n%llu tokens", c->label, static_cast<unsigned long long>(c->tokens));
    else if (c) std::snprintf(buf, sizeof(buf), "%s", c->label);
    lv_label_set_text(_details, buf);
    if (c && (c->quality == TokenHistoryQuality::Gap || c->quality == TokenHistoryQuality::Correction)) {
        const bool gap = c->quality == TokenHistoryQuality::Gap;
        setIcon(_qualityIcon, gap ? Icon::Gap : Icon::Correction, gap ? Purple : Orange);
        std::snprintf(buf, sizeof(buf), "%+lld", static_cast<long long>(gap ? c->gapDelta : c->correctionDelta));
        setText(_qualityValue, buf, gap ? Purple : Orange);
        lv_obj_remove_flag(_qualityIcon, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_qualityValue, LV_OBJ_FLAG_HIDDEN);
    } else if (!c || !c->valid || c->quality == TokenHistoryQuality::Partial || c->quality == TokenHistoryQuality::Local) {
        setIcon(_qualityIcon, Icon::Unknown, c && c->quality == TokenHistoryQuality::Partial ? Purple : Gray);
        lv_obj_remove_flag(_qualityIcon, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_qualityValue, LV_OBJ_FLAG_HIDDEN);
    } else { lv_obj_add_flag(_qualityIcon, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_qualityValue, LV_OBJ_FLAG_HIDDEN); }
    for (size_t i = 0; i < 30; ++i) { lv_obj_set_style_outline_color(_cells[i], lv_color_hex(Green), 0); lv_obj_set_style_outline_width(_cells[i], i == _selected ? 2 : 0, 0); }
}
bool CodexMicroView::selectHistory(size_t index) {
    if (!ready() || !_history || index >= (_hourly ? 24U : 30U)) return false;
    _selected = index; _activity = lv_tick_get(); renderSelection(); return true;
}
bool CodexMicroView::showHistory(bool hourly) {
    if (!ready()) return false;
    _hourly = hourly; if (_selected >= (_hourly ? 24U : 30U)) _selected = SIZE_MAX;
    refreshHistory(); renderHistory(); return setPageForDebug(Page::History);
}
bool CodexMicroView::setPageForDebug(Page page) {
    if (!ready() || page == Page::Agent) return false;
    if (page != Page::Command) { stopAnimations(); lv_obj_add_flag(_batteryCapacity, LV_OBJ_FLAG_HIDDEN); }
    else if (_capacityKnown && !_quota->truncated) lv_obj_remove_flag(_batteryCapacity, LV_OBJ_FLAG_HIDDEN);
    _page = page; _activity = lv_tick_get();
    if (page == Page::Command) { lv_obj_remove_flag(_quotaPage, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_historyPage, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_footer, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_clockIcon, LV_OBJ_FLAG_HIDDEN); }
    else { refreshHistory(); lv_obj_add_flag(_quotaPage, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_historyPage, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_footer, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_clockIcon, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_bucketCount, LV_OBJ_FLAG_HIDDEN); }
    if (page == Page::Command && _quota->truncated) lv_obj_remove_flag(_bucketCount, LV_OBJ_FLAG_HIDDEN);
    return true;
}
void CodexMicroView::togglePage() {
    if (_suppressed || !ready()) return;
    if (_locked) { wakeDisplay(); return; }
    setPageForDebug(_page == Page::Command ? Page::History : Page::Command);
}
void CodexMicroView::wakeDisplay() {
    if (!ready()) return;
    GetNetworkQuota().setLocked(false); _activity = lv_tick_get(); _locked = false;
    GetHAL().setTouchIdlePolling(false);
    if (_overlay) lv_obj_add_flag(_overlay, LV_OBJ_FLAG_HIDDEN);
    if (_lockPanel) lv_obj_add_flag(_lockPanel, LV_OBJ_FLAG_HIDDEN);
    GetHAL().setBackLightBrightness(_brightness, false);
}
void CodexMicroView::lockDisplay() {
    if (_locked || !ready()) return;
    stopAnimations();
    _locked = true; GetNetworkQuota().setLocked(true);
    GetHAL().setTouchIdlePolling(true);
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
    if (!_locked && GetHAL().millis() - _batteryReadTick >= 5000) refreshBattery(GetHAL().millis());
    updateAnimations(tick);
}
}
