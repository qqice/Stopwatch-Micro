/* SPDX-License-Identifier: MIT */
#include "view_mosaico.h"
#include "dot_widgets.h"
#include "reset_countdown.h"
#include "quota_trend_geometry.h"
#include "session_status_model.h"
#include "lock_session_geometry.h"
#include <hal/hal.h>
#include <host/network_quota.h>
#include <host/tailscale_transport.h>
#include <host/system_clock.h>
#include <ota/mosaico_ota_power.h>
#include <tusb.h>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <new>
#include <ctime>

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
void formatCountdown(mosaico_time::Countdown time, char* out, size_t size, bool minutePrecision = true) {
    if (!time.known) std::snprintf(out, size, "--");
    else if (time.minutes >= 1440) {
        if (minutePrecision) std::snprintf(out, size, "%llud%lluh%llum",
            static_cast<unsigned long long>(time.minutes / 1440), static_cast<unsigned long long>((time.minutes % 1440) / 60),
            static_cast<unsigned long long>(time.minutes % 60));
        else std::snprintf(out, size, "%llud%lluh",
            static_cast<unsigned long long>(time.minutes / 1440), static_cast<unsigned long long>((time.minutes % 1440) / 60));
    }
    else if (time.minutes >= 60) std::snprintf(out, size, "%lluh%llum",
        static_cast<unsigned long long>(time.minutes / 60), static_cast<unsigned long long>(time.minutes % 60));
    else std::snprintf(out, size, "%llum", static_cast<unsigned long long>(time.minutes));
}
}
namespace view {
CodexMicroView::~CodexMicroView() {
    MosaicoSessions::setEnabled(false);
    cancelOrientation();
    GetHAL().setMotionIdle(true);
    cancelPageSlide();
    GetHAL().setTouchIdlePolling(false);
    GetNetworkQuota().setLocked(false);
    if (_root) lv_obj_delete(_root);
    if (_rotationCurtain) lv_obj_delete(_rotationCurtain);
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
    lv_obj_add_event_cb(_root, touchEvent, LV_EVENT_ALL, this);
    _wifiIcon = createIcon(_root, Icon::WifiOff, 28, Orange); place(_wifiIcon, 20, 22);
    _batteryIcon = createIcon(_root, Icon::Battery, 32); place(_batteryIcon, 296, 20);
    _boltIcon = createIcon(_root, Icon::Bolt, 24, Gold); place(_boltIcon, 266, 24);
    _battery = label(_root, 338, 26, 122, "?", &lv_font_montserrat_20);
    for (size_t i = 0; i < 3; ++i) { _resetIcons[i] = createIcon(_root, Icon::ResetCard, 28, Gray); place(_resetIcons[i], 70 + i * 34, 22); }
    _resetCount = label(_root, 174, 26, 84, "?", &lv_font_montserrat_20);
    _quotaPage = lv_obj_create(_root); panel(_quotaPage, 20, 64, 440, 360, 0);
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
    _clockIcon = createIcon(_root, Icon::Clock, 26, Purple); place(_clockIcon, 20, 430);
    _footer = createText(_root, 180, 48, 6, Purple); place(_footer, 48, 426);
    _clockDate = createText(_root, 180, 48, 6, Purple); place(_clockDate, 270, 426);
    lv_obj_add_flag(_clockDate, LV_OBJ_FLAG_HIDDEN);
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
    _historyChart = lv_obj_create(_historyPage); lv_obj_remove_style_all(_historyChart);
    lv_obj_set_pos(_historyChart, 0, 300); lv_obj_set_size(_historyChart, 440, 80);
    lv_obj_remove_flag(_historyChart, static_cast<lv_obj_flag_t>(LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE));
    lv_obj_add_flag(_historyChart, LV_OBJ_FLAG_EVENT_BUBBLE);
    lv_obj_add_event_cb(_historyChart, historyChartEvent, LV_EVENT_DRAW_MAIN, this);
    label(_historyChart, 0, 0, 32, "100%", &lv_font_montserrat_12);
    label(_historyChart, 8, 46, 24, "0%", &lv_font_montserrat_12);
    _trendHint = label(_historyChart, 34, 62, 290, "7d --", &lv_font_montserrat_12);
    auto* now = label(_historyChart, 392, 62, 48, "NOW", &lv_font_montserrat_12);
    lv_obj_remove_flag(now, LV_OBJ_FLAG_CLICKABLE);
    auto* detail = lv_obj_create(_historyPage); panel(detail, 0, 380, 440, 28, 0);
    lv_obj_add_flag(detail, LV_OBJ_FLAG_EVENT_BUBBLE);
    _details = label(detail, 12, 6, 416, "", &lv_font_montserrat_12);
    lv_obj_set_height(_details, 18); lv_label_set_long_mode(_details, LV_LABEL_LONG_MODE_DOTS);
    initOta();
    initSettings();
    initSessions();
    _lockPanel = lv_obj_create(_root); panel(_lockPanel, 0, 0, 480, 480, 0);
    _lockClock = createText(_lockPanel, 240, 64, 8, Orange); place(_lockClock, 120, 64);
    setText(_lockClock, "--:--", Orange);
    _lockQuota = createText(_lockPanel, 400, 90, 12); place(_lockQuota, 40, 148);
    _lockResetIcon = createIcon(_lockPanel, Icon::Hourglass, 28, Cyan);
    _lockResetTime = createText(_lockPanel, 340, 48, 6, Cyan);
    _lockBatteryIcon = createIcon(_lockPanel, Icon::Battery, 36); place(_lockBatteryIcon, 152, 334);
    _lockBattery = label(_lockPanel, 0, 334, 1, "?", &lv_font_montserrat_20);
    lv_obj_set_size(_lockBattery, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    initLockSessions();
    lv_obj_add_flag(_lockPanel, LV_OBJ_FLAG_HIDDEN);
    _overlay = lv_obj_create(_root); panel(_overlay, 0, 0, 480, 480, 0);
    lv_obj_set_style_bg_opa(_overlay, LV_OPA_TRANSP, 0);
    lv_obj_add_flag(_overlay, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(_overlay, LV_OBJ_FLAG_CLICKABLE); // Function-only wake; no touch handler.
    // Opaque-background only: unlike parent opacity this needs no full-screen
    // intermediate layer. A sibling covers the root's one-pixel burn-in shift.
    _rotationCurtain = lv_obj_create(parent); panel(_rotationCurtain, 0, 0, 480, 480, 0);
    lv_obj_set_style_radius(_rotationCurtain, 0, 0);
    lv_obj_set_style_bg_opa(_rotationCurtain, LV_OPA_TRANSP, 0);
    lv_obj_add_flag(_rotationCurtain, LV_OBJ_FLAG_HIDDEN);
    bool widgetsReady = _lockSessionNumbers[5] && _sessionsCounts && _sessionsLink && _sessionNumbers[5] && _settingsStatus && _lockClock && _clockDate && _rotationCurtain && _otaButton && _otaButtonLabel && _otaStageIcon && _otaTitle && _otaPercent && _otaMeter && _otaArrow && _otaImageIcon && _otaSignatureIcon && _otaSlotNumbers[0] && _otaSlotNumbers[1] && _otaChips[0] && _otaChips[1] && _wifiIcon && _batteryIcon && _boltIcon && _resetCount && _quotaStatus && _clockIcon && _footer && _historyClock && _historyAge && _historyChart && _trendHint && _lockQuota && _lockBatteryIcon && _lockResetIcon && _lockResetTime;
    for (auto* number : _lockSessionNumbers) widgetsReady = widgetsReady && number;
    for (auto* icon : _resetIcons) widgetsReady = widgetsReady && icon;
    for (size_t i = 0; i < _cards.size(); ++i) {
        widgetsReady = widgetsReady && _cardBadges[i] && _cardValues[i] && _secondValues[i] && _creditIcons[i];
        for (size_t j = 0; j < 2; ++j) widgetsReady = widgetsReady && _windowBars[i][j] && _quotaIcons[i][j] && _hourglassIcons[i][j] && _resetTimes[i][j] && _resetBars[i][j];
    }
    if (!widgetsReady) { lv_obj_delete(_root); _root = nullptr; if (_rotationCurtain) lv_obj_delete(_rotationCurtain); _rotationCurtain = nullptr; return; }
    _activity = lv_tick_get(); _motionEpoch = _activity; _refresh = _activity; refreshQuota(GetHAL().millis()); refreshHistory();
    refreshClock(_activity, true);
    refreshDisplaySettings();
    if (!GetHAL().isDisplayOrientationHealthy()) { _rotationFault = true; cancelOrientation(); }
    GetHAL().setMotionIdle(_rotationFault);
}
// Settings reuse LVGL's input/render loop; callbacks only enqueue RAM changes.
void CodexMicroView::initSettings() {
    _settingsPage = lv_obj_create(_root); panel(_settingsPage, 20, 64, 440, 402, 0);
    lv_obj_add_flag(_settingsPage, static_cast<lv_obj_flag_t>(LV_OBJ_FLAG_HIDDEN | LV_OBJ_FLAG_EVENT_BUBBLE));
    static const char* names[] = {"CHG SEC", "BAT SEC", "CHG %", "BAT %", "LOCK %", "SHIFT"};
    for (size_t row = 0; row < 6; ++row) {
        const int y = static_cast<int>(row) * 62;
        auto* name = label(_settingsPage, 4, y + 16, 164, names[row], &lv_font_montserrat_16);
        lv_obj_set_height(name, 24);
        _settingsValues[row] = label(_settingsPage, 174, y + 14, 110, "", &lv_font_montserrat_20);
        lv_obj_set_height(_settingsValues[row], 28);
        lv_obj_set_style_text_align(_settingsValues[row], LV_TEXT_ALIGN_CENTER, 0);
        for (size_t side = 0; side < 2; ++side) {
            auto* button = lv_button_create(_settingsPage);
            panel(button, 300 + static_cast<int>(side) * 70, y, 60, 54);
            auto* text = label(button, 0, 15, 60, row == 5 ? (side ? "ON" : "OFF") : (side ? "+" : "-"), &lv_font_montserrat_20);
            lv_obj_set_style_text_align(text, LV_TEXT_ALIGN_CENTER, 0);
            lv_obj_remove_flag(text, LV_OBJ_FLAG_CLICKABLE);
            auto& hit = _settingsHits[row * 2 + side]; hit = {this, row * 2 + side};
            lv_obj_add_event_cb(button, settingsEvent, LV_EVENT_CLICKED, &hit);
            lv_obj_add_flag(button, LV_OBJ_FLAG_EVENT_BUBBLE);
        }
    }
    _settingsStatus = createIcon(_settingsPage, Icon::Check, 24, Green); place(_settingsStatus, 208, 374);
    renderSettings();
}
void CodexMicroView::renderSettings() {
    const auto& c = _displaySettings.config;
    const uint32_t values[] = {c.chargeTimeoutSeconds, c.batteryTimeoutSeconds, c.chargeBrightness, c.batteryBrightness, c.lockBrightness};
    for (size_t row = 0; row < 5; ++row) {
        char text[16];
        if (row == 0 && !values[row]) std::snprintf(text, sizeof(text), "NEVER");
        else std::snprintf(text, sizeof(text), "%lu", static_cast<unsigned long>(values[row]));
        lv_label_set_text(_settingsValues[row], text);
    }
    lv_label_set_text(_settingsValues[5], c.burnIn ? "ON" : "OFF");
    const bool error = _settingsRequestFailed || _displaySettings.error;
    const bool pending = _displaySettings.pending;
    setIcon(_settingsStatus, error ? Icon::Unknown : pending ? Icon::Hourglass : Icon::Check,
            error ? Orange : pending ? Gold : _displaySettings.savedRevision == _displaySettings.revision ? Green : Gray);
}
void CodexMicroView::settingsEvent(lv_event_t* event) {
    auto* hit = static_cast<Hit*>(lv_event_get_user_data(event));
    if (!hit || !hit->owner) return;
    auto* self = hit->owner;
    if (self->_rotationFault || self->_locked || self->_suppressed || self->_swipeConsumed || self->_slideTo ||
        self->_rotationPhase != RotationPhase::Idle || self->otaBusy() || self->_page != Page::Settings) return;
    auto config = self->_displaySettings.config;
    const size_t row = hit->index / 2;
    const bool up = hit->index % 2;
    if (row < 2) {
        static constexpr uint32_t charge[] = {15, 30, 60, 120, 300, 600, 0};
        static constexpr uint32_t battery[] = {15, 30, 45, 60};
        auto& value = row ? config.batteryTimeoutSeconds : config.chargeTimeoutSeconds;
        const auto* options = row ? battery : charge;
        const size_t count = row ? 4 : 7;
        size_t index = 0; while (index + 1 < count && options[index] != value) ++index;
        value = options[(index + (up ? 1 : count - 1)) % count];
    } else if (row < 5) {
        auto& value = row == 2 ? config.chargeBrightness : row == 3 ? config.batteryBrightness : config.lockBrightness;
        const int low = row == 4 ? 0 : 10;
        const int next = up ? (value / 5 + 1) * 5 : value ? ((value - 1) / 5) * 5 : 0;
        value = static_cast<uint8_t>(std::clamp(next, low, 100));
    } else config.burnIn = up;
    self->_settingsRequestFailed = !MosaicoDisplay::request(config);
    self->_activity = lv_tick_get();
    self->refreshDisplaySettings(); // Accepted RAM config applies immediately, persistence is worker-owned.
    self->renderSettings();
}
void CodexMicroView::refreshDisplaySettings() {
    MosaicoDisplay::Snapshot next{};
    if (MosaicoDisplay::snapshot(next)) {
        next.config = MosaicoDisplay::sanitize(next.config);
        if (next.config.burnIn != _displaySettings.config.burnIn) _shiftPending = true;
        const bool statusChanged = next.revision != _displaySettings.revision || next.savedRevision != _displaySettings.savedRevision ||
            next.pending != _displaySettings.pending || next.error != _displaySettings.error;
        _displaySettings = next;
        if (statusChanged && _settingsPage) renderSettings();
    }
    const bool chargeProfile = _chargeSupply.external;
    if (!_profileSeen || chargeProfile != _chargeProfile) {
        _profileSeen = true; _chargeProfile = chargeProfile; _activity = lv_tick_get();
    }
    const auto& c = _displaySettings.config;
    _brightness = _chargeProfile ? c.chargeBrightness : c.batteryBrightness;
    const int target = _locked ? c.lockBrightness : _brightness;
    if (!_rotationFault && target != _appliedBrightness) {
        GetHAL().setBackLightBrightness(target, false); _appliedBrightness = target;
    }
}
void CodexMicroView::applyBurnInShift(bool touching) {
    if (!_shiftPending || touching || _touchTracking || _slideTo || _rotationPhase != RotationPhase::Idle ||
        _rotationFault || _suppressed || otaBusy()) return;
    // Bounded two-pixel scan, including the centre; no new timer or touch offsets.
    static constexpr int offsets[9][2] = {{0, 0}, {2, 0}, {2, 2}, {0, 2}, {-2, 2}, {-2, 0}, {-2, -2}, {0, -2}, {2, -2}};
    if (_displaySettings.config.burnIn) {
        _shiftIndex = (_shiftIndex + 1) % 9;
        lv_obj_set_pos(_root, offsets[_shiftIndex][0], offsets[_shiftIndex][1]);
    } else { _shiftIndex = 0; lv_obj_set_pos(_root, 0, 0); }
    _shiftPending = false;
}
void CodexMicroView::initLockSessions() {
    for (size_t i=0;i<6;++i) {
        const auto box=mosaico_lock_sessions::card(i);
        auto* obj=lv_obj_create(_lockPanel); lv_obj_remove_style_all(obj);
        lv_obj_set_pos(obj,box.x,box.y); lv_obj_set_size(obj,box.w,box.h);
        lv_obj_remove_flag(obj,static_cast<lv_obj_flag_t>(LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE));
        _lockSessionCards[i]=obj; _lockSessionHits[i]={this,i}; _lockSessionColors[i]=Gray;
        lv_obj_add_event_cb(obj,lockSessionBorderEvent,LV_EVENT_DRAW_MAIN,&_lockSessionHits[i]);
        _lockSessionNumbers[i]=createText(obj,29,35,5,Gray); place(_lockSessionNumbers[i],12,6);
        char number[2]={static_cast<char>('1'+i),0}; setText(_lockSessionNumbers[i],number,Gray);
    }

}
void CodexMicroView::lockSessionBorderEvent(lv_event_t* event) {
    auto* hit=static_cast<Hit*>(lv_event_get_user_data(event));
    if (!hit || !hit->owner || hit->index>=6) return;
    auto* obj=lv_event_get_current_target_obj(event);lv_area_t area;lv_obj_get_coords(obj,&area);
    auto* layer=lv_event_get_layer(event);
    const auto color=lv_color_hex(hit->owner->_lockSessionColors[hit->index]);
    lv_draw_line_dsc_t line;lv_draw_line_dsc_init(&line);line.color=color;line.width=2;line.dash_width=5;line.dash_gap=3;
    const auto edge=[&](int x1,int y1,int x2,int y2) {
        line.p1={static_cast<lv_value_precise_t>(area.x1+x1),static_cast<lv_value_precise_t>(area.y1+y1)};
        line.p2={static_cast<lv_value_precise_t>(area.x1+x2),static_cast<lv_value_precise_t>(area.y1+y2)};lv_draw_line(layer,&line);
    };
    edge(9,1,44,1);edge(52,9,52,38);edge(44,46,9,46);edge(1,38,1,9);
    lv_draw_arc_dsc_t arc;lv_draw_arc_dsc_init(&arc);arc.color=color;arc.width=2;arc.radius=8;arc.rounded=1;
    // True native quarter-circle dash segments, not square corners or a canvas.
    for (unsigned i=0;i<4;++i) {
        const auto corner=mosaico_lock_sessions::corner(i);arc.center={area.x1+corner.x,area.y1+corner.y};
        for (int angle=0;angle<90;angle+=45) {
            arc.start_angle=corner.angle+angle;arc.end_angle=corner.angle+angle+22;lv_draw_arc(layer,&arc);
        }
    }
}
void CodexMicroView::requestLockedSessions() {
    if (_locked && !_suppressed && !_rotationFault && !otaKeepAwake() && ready()) MosaicoSessions::requestLockedRefresh();
}
void CodexMicroView::renderLockSessions() {
    const auto& cache=_sessionBackend.lockedState;
    const uint32_t now=GetHAL().millis();
    const bool valid=_sessionBackend.lockedCacheValid && !_sessionBackend.failed && cache.ready && cache.protocolReady && cache.connectionGeneration;
    for (size_t i=0;i<6;++i) {
        const bool known=valid && (cache.knownMask & (1U<<i));
        const bool fresh=_sessionBackend.freshnessKnownMask & (1U<<i);
        const auto status=known ? mosaico_sessions_ui::classify(cache.threads[i]) : mosaico_sessions_ui::Status::Unknown;
        const uint32_t color=mosaico_lock_sessions::color(status,known,fresh,now,_sessionBackend.lockedCapturedMs);
        if (color!=_lockSessionColors[i]) { _lockSessionColors[i]=color;lv_obj_invalidate(_lockSessionCards[i]); }
        char number[2]={static_cast<char>('1'+i),0};setText(_lockSessionNumbers[i],number,color);
    }
    _lockSessionsRevision=_sessionBackend.lockedRefreshRevision;
}
void CodexMicroView::initSessions() {
    _sessionsPage = lv_obj_create(_root); panel(_sessionsPage, 20, 64, 440, 402, 0);
    lv_obj_add_flag(_sessionsPage, static_cast<lv_obj_flag_t>(LV_OBJ_FLAG_HIDDEN | LV_OBJ_FLAG_EVENT_BUBBLE));
    _sessionsCounts = label(_sessionsPage, 0, 2, 440, "6 SLOTS / RUN -- WAIT -- ERR --", &lv_font_montserrat_14);
    lv_obj_set_height(_sessionsCounts, 22);
    _sessionsLink = label(_sessionsPage, 0, 30, 440, "--", &lv_font_montserrat_16);
    lv_obj_set_height(_sessionsLink, 24);
    for (size_t i=0; i<6; ++i) {
        const int x=static_cast<int>(i%2)*224, y=70+static_cast<int>(i/2)*108;
        _sessionCards[i]=lv_obj_create(_sessionsPage); panel(_sessionCards[i], x, y, 216, 98);
        lv_obj_remove_flag(_sessionCards[i], LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_flag(_sessionCards[i], LV_OBJ_FLAG_EVENT_BUBBLE);
        _sessionNumbers[i]=createText(_sessionCards[i], 52, 50, 7, Gray); place(_sessionNumbers[i], 10, 12);
        char number[2]={static_cast<char>('1'+i),0}; setText(_sessionNumbers[i],number,Gray);
        _sessionNames[i]=label(_sessionCards[i],72,18,134,"UNKNOWN",&lv_font_montserrat_16);
        lv_obj_set_height(_sessionNames[i],24);
        _sessionAges[i]=label(_sessionCards[i],72,52,134,"--",&lv_font_montserrat_12);
        lv_obj_set_height(_sessionAges[i],20);
    }
    renderSessions();
}
void CodexMicroView::refreshSessionsLease() {
    MosaicoSessions::setEnabled(_page == Page::Sessions && !_locked && !_suppressed && !_rotationFault &&
        !otaKeepAwake() && ready());
}
void CodexMicroView::refreshSessions(const CodexMicroState& state) {
    const bool previousReady=_sessionBackend.ready, previousStarting=_sessionBackend.starting, previousFailed=_sessionBackend.failed;
    MosaicoSessions::Snapshot next{};
    if (MosaicoSessions::snapshot(next)) _sessionBackend=next;
    // The application already passed one coherent native BLE snapshot; do not
    // combine its lighting/known mask with the module's previous-loop state.
    const bool linkChanged=state.ready!=_sessionState.ready || state.connected!=_sessionState.connected || state.protocolReady!=_sessionState.protocolReady;
    _sessionState=state;
    if (_locked && (_lockSessionsRevision!=_sessionBackend.lockedRefreshRevision || previousFailed!=_sessionBackend.failed)) renderLockSessions();
    _sessionsHadStatus=_sessionsHadStatus || (state.knownMask!=0 && state.connected && state.protocolReady);
    const uint32_t second=GetHAL().millis()/1000U;
    if (_page == Page::Sessions && !_locked && (state.revision!=_sessionsRevision || state.connectionGeneration!=_sessionsGeneration ||
        state.knownMask!=_sessionsKnownMask || linkChanged || second!=_sessionsAgeSecond || previousReady!=_sessionBackend.ready ||
        previousStarting!=_sessionBackend.starting || previousFailed!=_sessionBackend.failed)) {
        _sessionsRevision=state.revision; _sessionsGeneration=state.connectionGeneration;
        _sessionsKnownMask=state.knownMask; _sessionsAgeSecond=second; renderSessions();
    }
}
void CodexMicroView::renderSessions() {
    using S=mosaico_sessions_ui::Status;
    const auto summary=mosaico_sessions_ui::counts(_sessionState,_sessionBackend.ready && !_sessionBackend.failed && !_sessionBackend.starting);
    const uint32_t now=GetHAL().millis();
    char text[96];
    if (summary.live && summary.known) {
        const auto running=mosaico_sessions_ui::counterLabel(summary.running,summary.known);
        const auto waiting=mosaico_sessions_ui::counterLabel(summary.waiting,summary.known);
        const auto errors=mosaico_sessions_ui::counterLabel(summary.errors,summary.known);
        std::snprintf(text,sizeof(text),"KNOWN %u/6  RUN %s WAIT %s ERR %s",summary.known,running.data(),waiting.data(),errors.data());
    }
    else std::snprintf(text,sizeof(text),"6 SLOTS / RUN -- WAIT -- ERR --");
    lv_label_set_text(_sessionsCounts,text);
    lv_obj_set_style_text_color(_sessionsCounts,lv_color_hex(summary.live && summary.known ? 0xE9EDF2 : Gray),0);
    uint32_t youngest=UINT32_MAX;
    for (size_t i=0;i<6;++i) {
        const bool known=summary.live && (_sessionState.knownMask & (1U<<i));
        const S status=known ? mosaico_sessions_ui::classify(_sessionState.threads[i]) : S::Unknown;
        const char* name=status==S::Idle ? "IDLE" : status==S::Thinking ? "THINKING" : status==S::Complete ? "UNREAD" :
            status==S::Wait ? "NEEDS INPUT" : status==S::Error ? "ERROR" : status==S::Unassigned ? "OFF" : "UNKNOWN";
        const uint32_t color=status==S::Idle ? 0xE9EDF2 : status==S::Thinking ? Blue : status==S::Complete ? Green :
            status==S::Wait ? Orange : status==S::Error ? 0xE87575 : Gray;
        char number[2]={static_cast<char>('1'+i),0}; setText(_sessionNumbers[i],number,color);
        lv_label_set_text(_sessionNames[i],name); lv_obj_set_style_text_color(_sessionNames[i],lv_color_hex(color),0);
        lv_obj_set_style_border_width(_sessionCards[i],2,0); lv_obj_set_style_border_color(_sessionCards[i],lv_color_hex(color),0);
        if (known) {
            const uint32_t age=mosaico_sessions_ui::ageSeconds(now,_sessionState.lastThreadStatusMs[i]); youngest=std::min(youngest,age);
            std::snprintf(text,sizeof(text),"LAST %lus",static_cast<unsigned long>(age));
        } else std::snprintf(text,sizeof(text),"--");
        lv_label_set_text(_sessionAges[i],text); lv_obj_set_style_text_color(_sessionAges[i],lv_color_hex(Gray),0);
    }
    if (_sessionBackend.failed) std::snprintf(text,sizeof(text),"BT ERROR");
    else if (_sessionBackend.starting || !_sessionBackend.ready) std::snprintf(text,sizeof(text),"BT START");
    else if (!_sessionState.connected) std::snprintf(text,sizeof(text),"BT OFF%s",_sessionsHadStatus ? " / STALE" : " / --");
    else if (!summary.live || !summary.known) std::snprintf(text,sizeof(text),"BT LINK / --");
    else std::snprintf(text,sizeof(text),"BT LINK / LAST %lus",static_cast<unsigned long>(youngest));
    lv_label_set_text(_sessionsLink,text);
    lv_obj_set_style_text_color(_sessionsLink,lv_color_hex(summary.live && summary.known ? Blue : Gray),0);
}
// All OTA widgets are children of this 440x402 panel; the shared status bar survives.
void CodexMicroView::initOta() {
    _otaPage = lv_obj_create(_root); panel(_otaPage, 20, 64, 440, 402, 0);
    lv_obj_add_flag(_otaPage, static_cast<lv_obj_flag_t>(LV_OBJ_FLAG_HIDDEN | LV_OBJ_FLAG_EVENT_BUBBLE));
    _otaTitle = createText(_otaPage, 170, 56, 8, Cyan); place(_otaTitle, 0, 0); setText(_otaTitle, "OTA", Cyan);
    _otaPhase = label(_otaPage, 184, 18, 256, "", &lv_font_montserrat_20);
    _otaCurrent = label(_otaPage, 0, 58, 440, "", &lv_font_montserrat_20);
    _otaTarget = label(_otaPage, 0, 110, 440, "", &lv_font_montserrat_20);
    for (auto* text : {_otaCurrent, _otaTarget}) {
        lv_obj_set_height(text, 24); lv_label_set_long_mode(text, LV_LABEL_LONG_MODE_DOTS);
        lv_obj_set_style_text_font(text, &lv_font_montserrat_16, 0);
    }
    lv_obj_set_style_text_color(_otaCurrent, lv_color_hex(Cyan), 0);
    lv_obj_set_style_text_color(_otaTarget, lv_color_hex(Gold), 0);
    _otaStageIcon = createIcon(_otaPage, Icon::Refresh, 88, Gold); place(_otaStageIcon, 176, 106);
    lv_obj_add_flag(_otaStageIcon, LV_OBJ_FLAG_HIDDEN);
    _otaPercent = createText(_otaPage, 440, 52, 7); place(_otaPercent, 0, 106);
    _otaMeter = createMeter(_otaPage, 440, 28, 3); place(_otaMeter, 0, 194);
    _otaBytes = label(_otaPage, 0, 224, 440, "", &lv_font_montserrat_20);
    lv_obj_set_height(_otaBytes, 24); lv_label_set_long_mode(_otaBytes, LV_LABEL_LONG_MODE_CLIP);
    _otaHash = label(_otaPage, 36, 146, 404, "", &lv_font_montserrat_20);
    _otaSignature = label(_otaPage, 36, 178, 404, "", &lv_font_montserrat_20);
    _otaImageIcon = createIcon(_otaPage, Icon::Chip, 28, Gold); place(_otaImageIcon, 0, 144);
    _otaSignatureIcon = createIcon(_otaPage, Icon::Check, 28, Gold); place(_otaSignatureIcon, 0, 176);
    _otaArrow = createIcon(_otaPage, Icon::Arrow, 36, Gold); place(_otaArrow, 202, 248);
    for (size_t i = 0; i < 2; ++i) {
        const int x = i ? 254 : 0;
        _otaSlots[i] = lv_obj_create(_otaPage); panel(_otaSlots[i], x, 216, 186, 104);
        lv_obj_remove_flag(_otaSlots[i], LV_OBJ_FLAG_CLICKABLE);
        auto* name = label(_otaSlots[i], 8, 6, 170, i ? "NEW" : "CURRENT");
        _otaSlotNames[i] = name;
        lv_obj_set_style_text_color(name, lv_color_hex(i ? Gold : Cyan), 0);
        _otaSlotNumbers[i] = createText(_otaSlots[i], 90, 70, 9, i ? Gold : Cyan); place(_otaSlotNumbers[i], 72, 24);
        _otaChips[i] = createIcon(_otaSlots[i], Icon::Chip, 36, i ? Gold : Cyan); place(_otaChips[i], 16, 42);
    }
    _otaButton = lv_button_create(_otaPage); panel(_otaButton, 0, 334, 440, 64, 0x11251F);
    lv_obj_set_style_bg_opa(_otaButton, LV_OPA_TRANSP, LV_STATE_DEFAULT);
    lv_obj_set_style_bg_opa(_otaButton, LV_OPA_TRANSP, LV_STATE_DISABLED);
    lv_obj_set_style_bg_opa(_otaButton, LV_OPA_TRANSP, LV_STATE_PRESSED);
    lv_obj_set_style_border_width(_otaButton, 0, 0);
    _otaButtonLabel = createText(_otaButton, 440, 40, 4, Orange); place(_otaButtonLabel, 0, 12);
    setText(_otaButtonLabel, "CHECK", Orange);
    lv_obj_remove_flag(_otaButtonLabel, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(_otaButton, otaBorderEvent, LV_EVENT_DRAW_MAIN, this);
    lv_obj_add_event_cb(_otaButton, otaEvent, LV_EVENT_CLICKED, this);
    lv_obj_add_flag(_otaButton, LV_OBJ_FLAG_EVENT_BUBBLE);
}
bool CodexMicroView::otaBusy() const {
    using S = MosaicoOta::UiStage;
    return _otaApproved || _ota.stage == S::Checking || _ota.stage == S::Downloading ||
        _ota.stage == S::Verifying ||
        _ota.stage == S::Installing || _ota.stage == S::BootChecking;
}
bool CodexMicroView::otaKeepAwake() const {
    return _page == Page::OTA || otaBusy() || MosaicoOta::busy();
}
bool CodexMicroView::otaHasDownloadOffer() const {
    return _ota.signatureVerified && _ota.sha256[0] && _ota.sha256[0] != '-';
}
bool CodexMicroView::otaActionEnabled() const {
    if (_otaApproved) return false;
    using S = MosaicoOta::UiStage;
    switch (_ota.stage) {
    case S::Idle: case S::Complete: case S::Failed: case S::Available: return _otaNetworkReady;
    case S::ReadyInstall: return _ota.imageVerified && _externalPowerReady;
    case S::WaitingPower: return _ota.imageVerified ? _externalPowerReady : _otaNetworkReady;
    case S::ReadyReboot: return _externalPowerReady;
    default: return false;
    }
}
void CodexMicroView::renderOtaAction() {
    using S = MosaicoOta::UiStage;
    const char* action = "CHECK";
    switch (_ota.stage) {
    case S::Available: case S::Downloading: case S::Verifying: action = "DOWNLOAD"; break;
    case S::ReadyInstall: case S::Installing: action = "UPGRADE"; break;
    case S::WaitingPower: action = _ota.imageVerified ? "UPGRADE" : otaHasDownloadOffer() ? "DOWNLOAD" : "CHECK"; break;
    case S::ReadyReboot: case S::BootChecking: action = "REBOOT"; break;
    default: break;
    }
    const bool enabled = otaActionEnabled();
    const bool wasDisabled = lv_obj_has_state(_otaButton, LV_STATE_DISABLED);
    if (enabled) {
        lv_obj_remove_state(_otaButton, LV_STATE_DISABLED);
        lv_obj_add_flag(_otaButton, LV_OBJ_FLAG_CLICKABLE);
    } else {
        lv_obj_add_state(_otaButton, LV_STATE_DISABLED);
        lv_obj_remove_flag(_otaButton, LV_OBJ_FLAG_CLICKABLE);
    }
    setText(_otaButtonLabel, action, enabled ? Orange : 0x68451C);
    if (wasDisabled == enabled) lv_obj_invalidate(_otaButton);
}
void CodexMicroView::otaBorderEvent(lv_event_t* e) {
    auto* obj = lv_event_get_current_target_obj(e);
    lv_area_t a; lv_obj_get_coords(obj, &a);
    auto* layer = lv_event_get_layer(e);
    lv_draw_line_dsc_t d; lv_draw_line_dsc_init(&d);
    d.color = lv_color_hex(lv_obj_has_state(obj, LV_STATE_DISABLED) ? 0x68451C : Orange);
    d.width = 2; d.dash_width = 6; d.dash_gap = 4;
    const lv_value_precise_t x1 = a.x1 + 1, x2 = a.x2 - 1, y1 = a.y1 + 1, y2 = a.y2 - 1;
    d.p1 = {x1, y1}; d.p2 = {x2, y1}; lv_draw_line(layer, &d);
    d.p1 = {x2, y1}; d.p2 = {x2, y2}; lv_draw_line(layer, &d);
    d.p1 = {x2, y2}; d.p2 = {x1, y2}; lv_draw_line(layer, &d);
    d.p1 = {x1, y2}; d.p2 = {x1, y1}; lv_draw_line(layer, &d);
}
void CodexMicroView::otaEvent(lv_event_t* e) {
    auto* self = static_cast<CodexMicroView*>(lv_event_get_user_data(e));
    if (self->_rotationFault || self->_suppressed || self->_locked || self->_swipeConsumed || self->_slideTo || self->_rotationPhase != RotationPhase::Idle || self->_page != Page::OTA || self->otaBusy()) return;
    if (!self->otaActionEnabled()) return;
    using S = MosaicoOta::UiStage;
    bool accepted = false;
    switch (self->_ota.stage) {
    case S::Idle: case S::Complete: case S::Failed: accepted = MosaicoOta::requestCheck(); break;
    case S::Available: accepted = MosaicoOta::approveUpdate(self->_ota.sha256); break;
    case S::ReadyInstall: accepted = MosaicoOta::approveInstall(self->_ota.sha256); break;
    case S::WaitingPower:
        accepted = self->_ota.imageVerified ? MosaicoOta::approveInstall(self->_ota.sha256) :
            self->otaHasDownloadOffer() ? MosaicoOta::approveUpdate(self->_ota.sha256) : MosaicoOta::requestCheck();
        break;
    case S::ReadyReboot: accepted = MosaicoOta::requestReboot(); break;
    default: break;
    }
    if (accepted) {
        GetNetworkQuota().wakeForFirmwareUpdate();
        self->_otaApproved = true; self->_activity = lv_tick_get();
        self->renderOtaAction();
    }
}
void CodexMicroView::refreshOta(uint32_t tick) {
    MosaicoOta::UiSnapshot next{};
    if (MosaicoOta::copyUiSnapshot(next) && (!_otaSeen || next.revision != _ota.revision)) {
        using S = MosaicoOta::UiStage;
        const bool offer = next.stage == S::Available && (!_otaSeen || _ota.stage != S::Available ||
            std::strcmp(next.targetVersion, _ota.targetVersion) != 0 || std::strcmp(next.sha256, _ota.sha256) != 0);
        _ota = next; _otaSeen = true;
        // A backend response releases the local request latch, including power rejection and timeout.
        _otaApproved = false;
        if (offer) { _otaPending = true; if (_page != Page::OTA) _otaReturn = _page; }
        if (!_locked) renderOta();
    }
    if (otaBusy()) {
        cancelOrientation(); GetHAL().setMotionIdle(true);
        cancelPageSlide(); _touchTracking = false; _swipeConsumed = true;
        _activity = tick;
        // Only an actual accepted operation can wake the display, never discovery polling.
        if (_locked) wakeDisplay();
        if (_page != Page::OTA) { _otaReturn = _page; setPageForDebug(Page::OTA); }
    } else if (_otaPending && !_locked) {
        _otaPending = false; setPageForDebug(Page::OTA);
    }
}
void CodexMicroView::renderOta() {
    using S = MosaicoOta::UiStage;
    const bool idle = _ota.stage == S::Idle || _ota.stage == S::Checking ||
        (_ota.stage == S::WaitingPower && !_ota.imageVerified && !otaHasDownloadOffer());
    const bool offer = _ota.stage == S::Available || (_ota.stage == S::WaitingPower && !_ota.imageVerified && otaHasDownloadOffer());
    const bool terminal = _ota.stage == S::Complete || _ota.stage == S::Failed || _ota.stage == S::Idle;
    const bool discovery = offer || terminal || idle;
    const char* phase = "CURRENT";
    switch (_ota.stage) {
    case S::Checking: phase = "CHECKING"; break;
    case S::Available: phase = "UPDATE"; break;
    case S::WaitingPower: phase = _ota.imageVerified ? "USB POWER" : otaHasDownloadOffer() ? "DOWNLOAD BLOCKED" : "CHECK REQUIRED"; break;
    case S::Downloading: phase = "DOWNLOADING"; break;
    case S::Verifying: phase = "VERIFYING"; break;
    case S::ReadyInstall: phase = "READY"; break;
    case S::Installing: phase = "INSTALLING"; break;
    case S::ReadyReboot: phase = "READY TO REBOOT"; break;
    case S::BootChecking: phase = "BOOT CHECK"; break;
    case S::Complete: phase = "VERIFIED 100%"; break;
    case S::Failed: phase = "FAILED"; break;
    case S::Idle: phase = _otaSeen ? "NO UPDATE" : "CHECKING"; break;
    default: break;
    }
    lv_label_set_text(_otaPhase, phase);
    char text[96];
    lv_label_set_text(_otaCurrent, _ota.currentVersion[0] ? _ota.currentVersion : "READING VERSION");
    lv_label_set_text(_otaTarget, _ota.stage == S::Complete ? _ota.currentVersion : _ota.targetVersion);
    place(_otaTarget, 0, _ota.stage == S::Failed ? 110 : discovery && _ota.stage != S::Complete ? 86 : 58);
    if (discovery && _ota.stage != S::Complete) lv_obj_remove_flag(_otaCurrent, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(_otaCurrent, LV_OBJ_FLAG_HIDDEN);
    // Idle describes only the running image, never a cached or unknown candidate.
    if (idle) lv_obj_add_flag(_otaTarget, LV_OBJ_FLAG_HIDDEN); else lv_obj_remove_flag(_otaTarget, LV_OBJ_FLAG_HIDDEN);
    const bool download = _ota.stage == S::Downloading || _ota.stage == S::Verifying;
    const uint16_t bp = download ? (_ota.size ? static_cast<uint16_t>(std::min<uint64_t>(10000, static_cast<uint64_t>(_ota.received) * 10000 / _ota.size)) : 0)
        : std::min<uint16_t>(10000, _ota.progressBasisPoints);
    // Byte/health progress are the only meaningful percentages; installation is an activity icon.
    const bool numericProgress = download || _ota.stage == S::BootChecking;
    const bool stageIcon = _ota.stage == S::ReadyInstall || _ota.stage == S::Installing || _ota.stage == S::ReadyReboot || (_ota.stage == S::WaitingPower && _ota.imageVerified);
    if (stageIcon) {
        lv_obj_remove_flag(_otaStageIcon, LV_OBJ_FLAG_HIDDEN);
        setIcon(_otaStageIcon, _ota.stage == S::Installing ? Icon::Refresh : Icon::Check,
                _ota.stage == S::ReadyInstall && _ota.imageVerified ? Green : Gold);
    } else lv_obj_add_flag(_otaStageIcon, LV_OBJ_FLAG_HIDDEN);
    if (!discovery && numericProgress) {
        lv_obj_remove_flag(_otaPercent, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_otaMeter, LV_OBJ_FLAG_HIDDEN);
        percent(bp, text, sizeof(text)); setText(_otaPercent, text, download ? Green : Cyan);
        setMeter(_otaMeter, bp, download ? _ota.size != 0 : _ota.stage == S::BootChecking, download ? Green : Cyan);
    } else for (auto* obj : {_otaPercent, _otaMeter}) lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
    place(_otaBytes, 0, stageIcon ? 194 : 162);
    if (!discovery && (download || _ota.size)) {
        const bool mega = _ota.size >= 1000000;
        const double divisor = mega ? 1000000.0 : 1000.0;
        if (download) std::snprintf(text, sizeof(text), "%.2f / %.2f %sB", _ota.received / divisor, _ota.size / divisor, mega ? "M" : "K");
        else std::snprintf(text, sizeof(text), "%.2f %sB", _ota.size / divisor, mega ? "M" : "K");
        lv_label_set_text(_otaBytes, text); lv_obj_remove_flag(_otaBytes, LV_OBJ_FLAG_HIDDEN);
    } else lv_obj_add_flag(_otaBytes, LV_OBJ_FLAG_HIDDEN);
    std::snprintf(text, sizeof(text), "SHA %.12s", _ota.sha256[0] ? _ota.sha256 : "--"); lv_label_set_text(_otaHash, text);
    std::snprintf(text, sizeof(text), "SIG %.8s", _ota.signatureShort[0] ? _ota.signatureShort : "--"); lv_label_set_text(_otaSignature, text);
    setIcon(_otaImageIcon, _ota.imageVerified ? Icon::Check : (_ota.stage == S::Downloading ? Icon::Download : _ota.stage == S::Verifying ? Icon::Refresh : Icon::Chip), _ota.imageVerified ? Green : Gold);
    setIcon(_otaSignatureIcon, _ota.signatureVerified ? Icon::Check : Icon::Unknown, _ota.signatureVerified ? Green : Gold);
    // Fixed one-line metadata and version boxes: no line can grow into the cards/action.
    for (auto* obj : {_otaHash, _otaSignature}) {
        lv_obj_set_style_text_font(obj, &lv_font_montserrat_14, 0);
        lv_obj_set_height(obj, 24); lv_label_set_long_mode(obj, LV_LABEL_LONG_MODE_CLIP);
    }
    lv_obj_set_width(_otaHash, 184); lv_obj_set_width(_otaSignature, 168);
    place(_otaHash, 36, 222); place(_otaImageIcon, 0, 220);
    place(_otaSignature, 272, 222); place(_otaSignatureIcon, 236, 220);
    const bool hashKnown = !idle && _ota.sha256[0] && _ota.sha256[0] != '-';
    const bool signatureKnown = !idle && _ota.signatureShort[0] && _ota.signatureShort[0] != '-';
    for (auto* obj : {_otaHash, _otaImageIcon}) {
        if (hashKnown) lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
    }
    for (auto* obj : {_otaSignature, _otaSignatureIcon}) {
        if (signatureKnown) lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
    }
    const bool sameSlot = _ota.currentSlot >= 0 && _ota.currentSlot == _ota.targetSlot;
    const bool slotTransition = !idle && _ota.currentSlot >= 0 && _ota.targetSlot >= 0 && !sameSlot;
    for (size_t i = 0; i < 2; ++i) {
        lv_label_set_text(_otaSlotNames[i], !idle && sameSlot ? (i ? "VERIFY" : "ACTIVE") : (i ? "NEW" : "CURRENT"));
        place(_otaSlots[i], idle ? 127 : (i ? 254 : 0), 252);
        if (idle && (i || !_otaSeen || _ota.currentSlot < 0)) lv_obj_add_flag(_otaSlots[i], LV_OBJ_FLAG_HIDDEN);
        else lv_obj_remove_flag(_otaSlots[i], LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_height(_otaSlots[i], 72);
        place(_otaSlotNumbers[i], 72, 16);
        lv_obj_set_height(_otaSlotNumbers[i], 52);
        setTextPitch(_otaSlotNumbers[i], 7);
        place(_otaChips[i], 16, 24);
        const int slot = i ? _ota.targetSlot : _ota.currentSlot;
        std::snprintf(text, sizeof(text), "%s", slot == 0 ? "0" : slot == 1 ? "1" : "?"); setText(_otaSlotNumbers[i], text, i ? Gold : Cyan);
        lv_obj_set_style_border_width(_otaSlots[i], i && _ota.imageVerified ? 3 : 1, 0);
        lv_obj_set_style_border_color(_otaSlots[i], lv_color_hex(i ? Gold : Cyan), 0);
    }
    place(_otaArrow, 202, 270);
    if (slotTransition) lv_obj_remove_flag(_otaArrow, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(_otaArrow, LV_OBJ_FLAG_HIDDEN);
    renderOtaAction();
    if (_ota.stage == S::Failed) {
        // The backend supplies a short ID; never expose arbitrary diagnostic text.
        char safe[25]{}; size_t n = 0;
        for (; n < sizeof(safe) - 1 && _ota.error[n]; ++n) {
            const char c = _ota.error[n]; safe[n] = ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-') ? c : '?';
        }
        lv_label_set_text(_otaTarget, safe[0] ? safe : "OTA_ERROR");
    }
}
void CodexMicroView::touchEvent(lv_event_t* e) {
    const auto code = lv_event_get_code(e);
    if (code != LV_EVENT_PRESSED && code != LV_EVENT_PRESSING && code != LV_EVENT_RELEASED && code != LV_EVENT_PRESS_LOST) return;
    auto* self = static_cast<CodexMicroView*>(lv_event_get_user_data(e));
    if (self->_rotationFault || self->_suppressed || self->_locked || self->otaBusy() || self->_slideTo || self->_rotationPhase != RotationPhase::Idle) {
        self->_touchTracking = false; self->_swipeConsumed = true;
        return;
    }
    auto* input = lv_event_get_indev(e);
    if (!input || lv_indev_get_type(input) != LV_INDEV_TYPE_POINTER) return;
    lv_point_t point{}; lv_indev_get_point(input, &point);
    if (code == LV_EVENT_PRESSED) {
        self->_activity = lv_tick_get(); self->_touchStart = point;
        self->_touchTracking = true; self->_swipeConsumed = false;
    } else if (self->_touchTracking && (code == LV_EVENT_PRESSING || code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST)) {
        const int dx = point.x - self->_touchStart.x;
        const int dy = point.y - self->_touchStart.y;
        // Large horizontal motion consumes this contact even if it later drifts vertically.
        if (std::abs(dx) >= 96) self->_swipeConsumed = true;
        // Recognize while the contact is still valid: release/press-lost is not
        // guaranteed after a drag. setPageForDebug clears tracking, so this
        // contact cannot navigate again, even after the 200ms slide completes.
        if (code != LV_EVENT_PRESS_LOST && std::abs(dx) >= 96 && std::abs(dy) <= 48 && std::abs(dx) >= 2 * std::abs(dy)) {
            self->_touchTracking = false;
            self->navigatePage(dx < 0 ? 1 : -1);
        } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
            self->_touchTracking = false;
        }
    }
}
void CodexMicroView::cellEvent(lv_event_t* e) {
    auto* hit = static_cast<Hit*>(lv_event_get_user_data(e));
    if (!hit->owner->_rotationFault && !hit->owner->_suppressed && !hit->owner->_locked && !hit->owner->_swipeConsumed && !hit->owner->_slideTo && hit->owner->_rotationPhase == RotationPhase::Idle && !hit->owner->otaBusy()) hit->owner->selectHistory(hit->index);
}
void CodexMicroView::modeEvent(lv_event_t* e) {
    auto* hit = static_cast<Hit*>(lv_event_get_user_data(e));
    if (!hit->owner->_rotationFault && !hit->owner->_suppressed && !hit->owner->_locked && !hit->owner->_swipeConsumed && !hit->owner->_slideTo && hit->owner->_rotationPhase == RotationPhase::Idle && !hit->owner->otaBusy()) hit->owner->showHistory(hit->index == 1);
}
void CodexMicroView::refreshBattery(uint32_t now) {
    const auto telemetry = GetHAL().batteryTelemetry(false);
    _batteryReadTick = now; _batterySeen = true; _batteryValid = telemetry.valid;
    _chargeSupply.update(telemetry.valid, telemetry.currentMa, telemetry.reportedSoc);
    _batteryCharging = telemetry.valid && telemetry.currentMa > 3;
    const bool critical = GetHAL().gaugeBootReloadInfo().status == Hal::GaugeBootReloadStatus::Critical;
    _externalPowerReady = MosaicoOta::manualInstallPowerSafe(telemetry, tud_mounted(), critical);
    if (!_locked) renderOtaAction();
    char text[80];
    if (telemetry.valid) std::snprintf(text, sizeof(text), "%u%%%s", static_cast<unsigned>(telemetry.reportedSoc), telemetry.nominalConfigured ? "*" : "?");
    else std::snprintf(text, sizeof(text), "?");
    const uint32_t color = telemetry.valid ? levelColor(static_cast<uint16_t>(telemetry.reportedSoc) * 100) : Gray;
    if (_locked) {
        lv_label_set_text(_lockBattery, text);
        lv_point_t size;lv_text_get_size(&size,text,&lv_font_montserrat_20,0,0,480,LV_TEXT_FLAG_NONE);
        const auto row=mosaico_lock_sessions::batteryRow(size.x,size.y);
        lv_obj_set_size(_lockBattery,size.x,size.y);place(_lockBatteryIcon,row.iconX,334);place(_lockBattery,row.textX,row.textY);
        lv_obj_set_style_text_color(_lockBattery, lv_color_hex(color), 0);
        setIcon(_lockBatteryIcon, Icon::Battery, color, telemetry.reportedSoc, telemetry.valid);
    } else {
        lv_label_set_text(_battery, text);
        lv_obj_set_style_text_color(_battery, lv_color_hex(color), 0);
        setIcon(_batteryIcon, Icon::Battery, color, telemetry.reportedSoc, telemetry.valid);
        if (_chargeSupply.external) lv_obj_remove_flag(_boltIcon, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(_boltIcon, LV_OBJ_FLAG_HIDDEN);
    }

}
void CodexMicroView::refreshClock(uint32_t tick, bool force) {
    if (!force && tick - _clockReadTick < (_locked ? 60000U : 1000U)) return;
    _clockReadTick = tick;
    // System time only. A cold boot without network calibration remains unknown;
    // never use persisted epochs or quota capture timestamps as a running RTC.
    const auto systemClock = MosaicoClock::snapshot();
    const int64_t minute = systemClock.valid ? systemClock.epoch / 60 : -1;
    if (!force && minute == _clockMinute) return;
    _clockMinute = minute;
    char clock[6] = "--:--", date[6]{};
    tm local{};
    if (MosaicoClock::shanghaiTime(systemClock.epoch, local)) {
        std::strftime(clock, sizeof(clock), "%H:%M", &local);
        std::strftime(date, sizeof(date), "%m-%d", &local);
    } else _clockMinute = -1;
    setText(_footer, clock, Purple);
    setText(_lockClock, clock, Orange);
    setIcon(_clockIcon, Icon::Clock, Purple);
    setText(_clockDate, date, Purple);
    if (_clockMinute >= 0 && _page == Page::Command && !_quota->truncated) lv_obj_remove_flag(_clockDate, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(_clockDate, LV_OBJ_FLAG_HIDDEN);
}
void CodexMicroView::cancelOrientation() {
    if (_rotationFault) {
        lv_anim_delete(this, orientationExec);
        if (_rotationCurtain) {
            lv_obj_set_style_bg_opa(_rotationCurtain, LV_OPA_COVER, 0);
            lv_obj_remove_flag(_rotationCurtain, LV_OBJ_FLAG_HIDDEN);
            lv_obj_move_foreground(_rotationCurtain);
        }
        _rotationPhase = RotationPhase::Idle;
        _touchTracking = false; _swipeConsumed = true;
        return;
    }
    if (_rotationPhase == RotationPhase::Idle) return;
    lv_anim_delete(this, orientationExec);
    if (_rotationCurtain) {
        lv_obj_add_flag(_rotationCurtain, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_bg_opa(_rotationCurtain, LV_OPA_TRANSP, 0);
    }
    if (_rotationPhase != RotationPhase::Idle) { _touchTracking = false; _swipeConsumed = true; }
    _rotationPhase = RotationPhase::Idle;
}
void CodexMicroView::orientationExec(void* owner, int32_t opacity) {
    auto* self = static_cast<CodexMicroView*>(owner);
    if (self->_rotationFault) return;
    if (self->_rotationCurtain) lv_obj_set_style_bg_opa(self->_rotationCurtain, static_cast<lv_opa_t>(opacity), 0);
}
void CodexMicroView::orientationCompleted(lv_anim_t* anim) {
    auto* self = static_cast<CodexMicroView*>(anim->var);
    if (self->_rotationFault) { self->cancelOrientation(); return; }
    if (self->_rotationPhase == RotationPhase::FadeOut) {
        self->_rotationPhase = RotationPhase::WaitBlack;
        self->_rotationFrame = GetDisplayFrameCount();
        // Wait for a full invalidation at opacity 255, not merely an animation
        // completion callback. HAL drains this black color queue before MADCTL.
        lv_obj_invalidate(lv_obj_get_parent(self->_root));
    } else if (self->_rotationPhase == RotationPhase::FadeIn) {
        lv_obj_add_flag(self->_rotationCurtain, LV_OBJ_FLAG_HIDDEN);
        self->_rotationPhase = RotationPhase::Idle;
        self->_touchTracking = false; self->_swipeConsumed = true;
    }
}
void CodexMicroView::startOrientationFade(bool fadeIn) {
    if (_rotationFault) return;
    _rotationPhase = fadeIn ? RotationPhase::FadeIn : RotationPhase::FadeOut;
    lv_anim_t anim; lv_anim_init(&anim); lv_anim_set_var(&anim, this);
    lv_anim_set_exec_cb(&anim, orientationExec);
    lv_anim_set_values(&anim, fadeIn ? 255 : 0, fadeIn ? 0 : 255);
    lv_anim_set_duration(&anim, fadeIn ? 160 : 120);
    lv_anim_set_completed_cb(&anim, orientationCompleted); lv_anim_start(&anim);
}
void CodexMicroView::updateOrientation(bool touching) {
    if (_rotationFault || !GetHAL().isDisplayOrientationHealthy()) {
        _rotationFault = true; cancelOrientation(); GetHAL().setMotionIdle(true); return;
    }
    const bool idle = _locked || _suppressed || otaBusy();
    GetHAL().setMotionIdle(idle);
    if (idle) { cancelOrientation(); return; }
    if (_rotationPhase != RotationPhase::Idle) {
        // A newly pressed finger cancels before any hardware direction change.
        if (touching || _slideTo) { cancelOrientation(); return; }
        if (_rotationPhase == RotationPhase::WaitBlack && GetDisplayFrameCount() != _rotationFrame) {
            _motionGeneration = _rotationGeneration; // One HAL attempt per stable candidate.
            if (!GetHAL().setDisplayOrientation(_rotationTarget)) {
                _rotationFault = !GetHAL().isDisplayOrientationHealthy();
                cancelOrientation(); return;
            }
            _touchTracking = false; _swipeConsumed = true;
            _rotationPhase = RotationPhase::WaitRotated;
            _rotationFrame = GetDisplayFrameCount();
            lv_obj_invalidate(lv_obj_get_parent(_root));
        } else if (_rotationPhase == RotationPhase::WaitRotated && GetDisplayFrameCount() != _rotationFrame) {
            startOrientationFade(true);
        }
        return;
    }
    if (touching || _touchTracking || _slideTo) return;
    const auto orientation = GetHAL().motionOrientation(); // Cache only.
    if (!orientation.available || orientation.idle || !orientation.valid || orientation.generation == _motionGeneration) return;
    if (orientation.degrees == GetHAL().getDisplayOrientation()) { _motionGeneration = orientation.generation; return; }
    _rotationGeneration = orientation.generation;
    _rotationTarget = orientation.degrees;
    _touchTracking = false; _swipeConsumed = true;
    lv_obj_remove_flag(_rotationCurtain, LV_OBJ_FLAG_HIDDEN); lv_obj_move_foreground(_rotationCurtain);
    startOrientationFade(false);
}
void CodexMicroView::stopAnimations() {
    for (auto* obj : {_otaStageIcon, _otaMeter, _otaArrow, _otaChips[0], _otaChips[1]}) setMotion(obj, 0, false);
    setMotion(_batteryIcon, 0, false);
    for (auto* icon : _resetIcons) setMotion(icon, 0, false);
    for (size_t i = 0; i < _cards.size(); ++i) {
        setMotion(_creditIcons[i], 0, false);
        for (size_t j = 0; j < 2; ++j) { setMotion(_windowBars[i][j], 0, false); setMotion(_resetBars[i][j], 0, false); }
    }
}
void CodexMicroView::updateAnimations(uint32_t tick) {
    if (_locked || _suppressed || _slideTo) return;
    if (_rotationFault || _rotationPhase != RotationPhase::Idle) return;
    if (tick - _motionTick < 100) return;
    _motionTick = tick;
    const uint16_t phase = static_cast<uint16_t>(((tick - _motionEpoch) % 12000U) * 360U / 12000U);
    const uint16_t meterPhase = static_cast<uint16_t>(((tick - _motionEpoch) % 6000U) * 360U / 6000U);
    using S = MosaicoOta::UiStage;
    const bool otaMotion = _page == Page::OTA && (_ota.stage == S::Downloading || _ota.stage == S::Verifying || _ota.stage == S::Installing || _ota.stage == S::BootChecking);
    setMotion(_otaStageIcon, meterPhase, _page == Page::OTA && _ota.stage == S::Installing);
    // Reverse only OTA phase: quota/reset meters retain their right-to-left charging sweep.
    const uint16_t otaMeterPhase = static_cast<uint16_t>((360U - meterPhase) % 360U);
    setMotion(_otaMeter, otaMeterPhase, otaMotion && !lv_obj_has_flag(_otaMeter, LV_OBJ_FLAG_HIDDEN));
    setMotion(_otaArrow, meterPhase, otaMotion && !lv_obj_has_flag(_otaArrow, LV_OBJ_FLAG_HIDDEN));
    for (auto* obj : {_otaChips[0], _otaChips[1]}) setMotion(obj, meterPhase, otaMotion);
    setMotion(_batteryIcon, phase, _batteryValid && _batteryCharging);
    const uint32_t now = GetHAL().millis();
    const uint64_t age = static_cast<uint64_t>(_quota->ageSecondsAtReceipt) + (now - _quota->receivedAtMs) / 1000U;
    const bool quotaActive = _page == Page::Command && _quotaRevision != UINT32_MAX && _quota->available && !_quota->stale && age <= 130 && GetNetworkQuota().connected();
    for (size_t i = 0; i < _resetIcons.size(); ++i) {
        const bool active = quotaActive && _quota->resetCreditsKnown && _quota->resetCredits && !lv_obj_has_flag(_resetIcons[i], LV_OBJ_FLAG_HIDDEN);
        setMotion(_resetIcons[i], phase, active);
    }
    const int scrollY = lv_obj_get_scroll_y(_quotaPage);
    const int viewportHeight = lv_obj_get_height(_quotaPage);
    const uint64_t epoch = static_cast<uint64_t>(_quota->capturedEpoch) + age;
    for (size_t i = 0; i < _cards.size(); ++i) {
        const int top = lv_obj_get_y(_cards[i]) - scrollY;
        const bool visible = quotaActive && i < _quota->bucketCount && !lv_obj_has_flag(_cards[i], LV_OBJ_FLAG_HIDDEN) && top < viewportHeight && top + 350 > 0;
        const auto& bucket = _quota->buckets[i];
        setMotion(_creditIcons[i], phase, visible && top + 278 < viewportHeight && top + 306 > 0 && bucket.creditsKnown && !lv_obj_has_flag(_creditIcons[i], LV_OBJ_FLAG_HIDDEN));
        for (size_t j = 0; j < 2; ++j) {
            const auto& window = bucket.windows[j];
            setMotion(_windowBars[i][j], meterPhase, visible && top + 110 < viewportHeight && top + 134 > 0 && window.available && window.remainingBasisPoints > 0);
            const bool knownTime = window.available && window.resetEpoch && _quota->capturedEpoch && window.durationMinutes && window.resetEpoch > epoch;
            setMotion(_resetBars[i][j], meterPhase, visible && top + 222 < viewportHeight && top + 246 > 0 && knownTime);
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
            formatCountdown(mosaico_time::countdown(w.available, _quota->capturedEpoch, w.resetEpoch, epoch), buf, sizeof(buf));
            setText(_resetTimes[i][j], buf, resetKnown ? Cyan : Gray);
            setIcon(_hourglassIcons[i][j], Icon::Hourglass, resetKnown ? Cyan : Gray);
            place(_resetBars[i][j], x, 222); lv_obj_set_width(_resetBars[i][j], width);
            const uint64_t durationSeconds = static_cast<uint64_t>(w.durationMinutes) * 60;
            const uint16_t timeBp = durationSeconds ? static_cast<uint16_t>(std::min<uint64_t>(10000, seconds * 10000 / durationSeconds)) : 0;
            setMeter(_resetBars[i][j], timeBp, resetKnown && durationSeconds, Cyan);
        }
        if (bucket.creditsKnown) {
            lv_obj_remove_flag(_creditIcons[i], LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_creditValues[i], LV_OBJ_FLAG_HIDDEN);
            std::snprintf(buf, sizeof(buf), "%s Credits", bucket.creditsUnlimited ? "inf" : (bucket.creditBalance[0] ? bucket.creditBalance : "--"));
            lv_label_set_text(_creditValues[i], buf);
        } else { lv_obj_add_flag(_creditIcons[i], LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_creditValues[i], LV_OBJ_FLAG_HIDDEN); }
        lv_label_set_text(_cardMeta[i], bucket.reached);
    }
    refreshClock(lv_tick_get());
    if (_quota->truncated && _page == Page::Command) {
        std::snprintf(buf, sizeof(buf), "%u/%lu", _quota->bucketCount,
                      static_cast<unsigned long>(_quota->totalBuckets));
        lv_label_set_text(_bucketCount, buf);
        lv_obj_remove_flag(_bucketCount, LV_OBJ_FLAG_HIDDEN);
    } else lv_obj_add_flag(_bucketCount, LV_OBJ_FLAG_HIDDEN);
    if (_page == Page::Command && _clockMinute >= 0 && !_quota->truncated) lv_obj_remove_flag(_clockDate, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(_clockDate, LV_OBJ_FLAG_HIDDEN);
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
    char lockReset[64] = "--", leftReset[32]{}, rightReset[32]{};
    const auto leftTime = mosaico_time::countdown(lockKnown && firstWindow[0].available,
        _quota->capturedEpoch, firstWindow[0].resetEpoch, epoch);
    const auto rightTime = mosaico_time::countdown(lockKnown && firstWindow[1].available,
        _quota->capturedEpoch, firstWindow[1].resetEpoch, epoch);
    if (lockKnown && firstWindow[0].available) formatCountdown(leftTime, leftReset, sizeof(leftReset), true);
    if (lockKnown && firstWindow[1].available) formatCountdown(rightTime, rightReset, sizeof(rightReset), true);
    if (leftReset[0] && rightReset[0]) std::snprintf(lockReset, sizeof(lockReset), "%s %s", leftReset, rightReset);
    else if (leftReset[0] || rightReset[0]) std::snprintf(lockReset, sizeof(lockReset), "%s", leftReset[0] ? leftReset : rightReset);
    const int glyphs = static_cast<int>(std::min<size_t>(std::strlen(lockReset), 32));
    const int columns = std::max(1, glyphs * 6 - 1);
    const int textWidth = columns * std::max(1, std::min(6, 360 / columns));
    const int rowLeft = (480 - (28 + 12 + textWidth)) / 2;
    place(_lockResetIcon, rowLeft, 262); place(_lockResetTime, rowLeft + 40, 252);
    lv_obj_set_width(_lockResetTime, textWidth);
    const bool lockResetKnown = leftTime.known || rightTime.known;
    const uint32_t timeColor = lockResetKnown ? (_quota->stale ? dimCachedColor(Cyan) : Cyan) : Gray;
    setIcon(_lockResetIcon, Icon::Hourglass, timeColor); setText(_lockResetTime, lockReset, timeColor);
}
void CodexMicroView::historyChartEvent(lv_event_t* event) {
    auto* self = static_cast<CodexMicroView*>(lv_event_get_user_data(event));
    if (!self || !self->_history) return;
    lv_area_t area; lv_obj_get_content_coords(self->_historyChart, &area);
    auto* layer = lv_event_get_layer(event);
    lv_draw_line_dsc_t line; lv_draw_line_dsc_init(&line); line.width = 2;
    const auto drawLine = [&](int x1, int y1, int x2, int y2, uint32_t color) {
        line.color = lv_color_hex(color); line.p1.x = area.x1 + x1; line.p1.y = area.y1 + y1;
        line.p2.x = area.x1 + x2; line.p2.y = area.y1 + y2; lv_draw_line(layer, &line);
    };
    drawLine(32, 6, 32, 54, Gray); drawLine(32, 54, 428, 54, Gray);
    const auto& trend = self->_history->quotaTrend;
    if (!trend.available) return; // Empty cold history is a gap, never a fabricated zero line.
    const uint32_t start = self->_hourly ? trend.start24hEpoch : trend.start7dEpoch;
    const auto* points = self->_hourly ? trend.hours.data() : trend.days.data();
    const size_t count = self->_hourly ? trend.hours.size() : trend.days.size();
    const uint64_t age = static_cast<uint64_t>(trend.ageSecondsAtReceipt) +
        (GetHAL().millis() - trend.receivedAtMs) / 1000U;
    const uint32_t color = age > 600 ? dimCachedColor(Cyan) : Cyan;
    lv_draw_rect_dsc_t dot; lv_draw_rect_dsc_init(&dot); dot.bg_opa = LV_OPA_COVER; dot.radius = LV_RADIUS_CIRCLE;
    for (size_t i = 0; i < count; ++i) {
        const auto p = mosaico_trend::position(points[i], start, trend.endEpoch);
        if (!p.valid) continue;
        if (i && mosaico_trend::connects(points[i-1], points[i], start, trend.endEpoch)) {
            const auto previous = mosaico_trend::position(points[i-1], start, trend.endEpoch);
            drawLine(previous.x, previous.y, p.x, p.y, color);
        }
        dot.bg_color = lv_color_hex(points[i].resetBefore ? Gold : color);
        const lv_area_t spot = {area.x1+p.x-2, area.y1+p.y-2, area.x1+p.x+1, area.y1+p.y+1};
        lv_draw_rect(layer, &dot, &spot);
    }
}
void CodexMicroView::refreshHistory() {
    if (_historyRevision == TokenHistoryRevision()) { updateHistoryHeader(); return; }
    if (CopyTokenHistory(*_history)) { _historyRevision = _history->revision; renderHistory(); }
}
void CodexMicroView::updateHistoryHeader(bool force) {
    const auto& trend = _history->quotaTrend;
    const uint64_t trendAge = static_cast<uint64_t>(trend.ageSecondsAtReceipt) + (GetHAL().millis() - trend.receivedAtMs) / 1000U;
    const uint32_t trendKey = (trend.available ? 1U : 0U) | (trendAge > 600 ? 2U : 0U) | (_hourly ? 4U : 0U);
    if (force || trendKey != _trendAgeKey) {
        _trendAgeKey = trendKey;
        char value[24] = "--", hint[48];
        const auto* latest = _hourly ? mosaico_trend::latest(trend.hours) : mosaico_trend::latest(trend.days);
        if (trend.available && latest) percent(latest->remainingBasisPoints, value, sizeof(value));
        std::snprintf(hint, sizeof(hint), "%s %s%s", _hourly ? "24h" : "7d", value,
            trend.capturedEpoch && trendAge > 600 ? " STALE" : "");
        lv_label_set_text(_trendHint, hint); lv_obj_invalidate(_historyChart);
    }
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
    lv_obj_invalidate(_historyChart);
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
    if (c && c->valid) {
        char amount[24]; compact(c->tokens, amount, sizeof(amount));
        std::snprintf(buf, sizeof(buf), "%s %s tokens / %s", c->label, amount, quality(c->quality));
    } else if (c && (c->quality == TokenHistoryQuality::Gap || c->quality == TokenHistoryQuality::Correction)) {
        const bool gap = c->quality == TokenHistoryQuality::Gap;
        std::snprintf(buf, sizeof(buf), "%s %s %+lld", c->label, gap ? "gap" : "correction",
            static_cast<long long>(gap ? c->gapDelta : c->correctionDelta));
    } else if (c) std::snprintf(buf, sizeof(buf), "%s / %s", c->label, quality(c->quality));
    lv_label_set_text(_details, buf);
    for (size_t i = 0; i < 30; ++i) { lv_obj_set_style_outline_color(_cells[i], lv_color_hex(Green), 0); lv_obj_set_style_outline_width(_cells[i], i == _selected ? 2 : 0, 0); }
}
bool CodexMicroView::selectHistory(size_t index) {
    if (_rotationFault) return false;
    if (!ready() || !_history || index >= (_hourly ? 24U : 30U)) return false;
    _selected = index; _activity = lv_tick_get(); renderSelection(); return true;
}
bool CodexMicroView::showHistory(bool hourly) {
    if (_rotationFault) return false;
    if (!ready()) return false;
    _hourly = hourly; if (_selected >= (_hourly ? 24U : 30U)) _selected = SIZE_MAX;
    refreshHistory(); renderHistory(); return setPageForDebug(Page::History);
}
bool CodexMicroView::setPageForDebug(Page page) {
    if (_rotationFault) return false;
    if (!ready() || page == Page::Agent || (page != Page::Command && page != Page::History && page != Page::OTA && page != Page::Settings && page != Page::Sessions)) return false;
    if (otaBusy() && page != Page::OTA) return false;
    cancelOrientation();
    cancelPageSlide();
    if (_touchTracking) _swipeConsumed = true;
    _touchTracking = false;
    if (page == Page::OTA) {
        if (_page != Page::OTA) _otaReturn = _page;
        renderOta();
    }
    if (page == Page::OTA) lv_obj_remove_flag(_otaPage, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(_otaPage, LV_OBJ_FLAG_HIDDEN);
    if (page == Page::Settings) { renderSettings(); lv_obj_remove_flag(_settingsPage, LV_OBJ_FLAG_HIDDEN); }
    else lv_obj_add_flag(_settingsPage, LV_OBJ_FLAG_HIDDEN);
    if (page == Page::Sessions) { renderSessions(); lv_obj_remove_flag(_sessionsPage, LV_OBJ_FLAG_HIDDEN); }
    else lv_obj_add_flag(_sessionsPage, LV_OBJ_FLAG_HIDDEN);
    if (page != Page::Command) stopAnimations();
    _page = page; _activity = lv_tick_get();
    refreshSessionsLease();
    if (page == Page::Command) { lv_obj_remove_flag(_quotaPage, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_historyPage, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_footer, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_clockIcon, LV_OBJ_FLAG_HIDDEN); }
    else if (page == Page::History) { refreshHistory(); lv_obj_add_flag(_quotaPage, LV_OBJ_FLAG_HIDDEN); lv_obj_remove_flag(_historyPage, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_footer, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_clockIcon, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_bucketCount, LV_OBJ_FLAG_HIDDEN); }
    if (page == Page::OTA || page == Page::Settings || page == Page::Sessions) { lv_obj_add_flag(_quotaPage, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_historyPage, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_footer, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_clockIcon, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(_bucketCount, LV_OBJ_FLAG_HIDDEN); }
    if (page == Page::Command && _quota->truncated) lv_obj_remove_flag(_bucketCount, LV_OBJ_FLAG_HIDDEN);
    if (page == Page::Command && _clockMinute >= 0 && !_quota->truncated) lv_obj_remove_flag(_clockDate, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(_clockDate, LV_OBJ_FLAG_HIDDEN);
    if (page == Page::OTA && _locked) wakeDisplay();
    return true;
}
lv_obj_t* CodexMicroView::pagePanel(Page page) const {
    return page == Page::Command ? _quotaPage : page == Page::History ? _historyPage : page == Page::Settings ? _settingsPage : page == Page::Sessions ? _sessionsPage : _otaPage;
}
void CodexMicroView::cancelPageSlide() {
    lv_anim_delete(this, slideExec);
    for (auto* panel : {_slideFrom, _slideTo}) if (panel) {
        lv_obj_set_x(panel, 20);
        if (panel != pagePanel(_page)) lv_obj_add_flag(panel, LV_OBJ_FLAG_HIDDEN);
    }
    _slideFrom = _slideTo = nullptr;
}
void CodexMicroView::slideExec(void* owner, int32_t offset) {
    auto* self = static_cast<CodexMicroView*>(owner);
    if (!self->_slideTo) return;
    lv_obj_set_x(self->_slideFrom, 20 - self->_slideDirection * offset);
    lv_obj_set_x(self->_slideTo, 20 + self->_slideDirection * (480 - offset));
}
void CodexMicroView::slideCompleted(lv_anim_t* anim) {
    auto* self = static_cast<CodexMicroView*>(anim->var);
    if (self->_slideFrom) {
        lv_obj_add_flag(self->_slideFrom, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_x(self->_slideFrom, 20);
    }
    if (self->_slideTo) lv_obj_set_x(self->_slideTo, 20);
    self->_slideFrom = self->_slideTo = nullptr;
}
void CodexMicroView::navigatePage(int direction) {
    if (_rotationFault || _suppressed || _locked || !ready() || otaBusy() || _slideTo || _rotationPhase != RotationPhase::Idle) return;
    const Page pages[] = {Page::Command, Page::History, Page::OTA, Page::Settings, Page::Sessions};
    const int index = _page == Page::Command ? 0 : _page == Page::History ? 1 : _page == Page::OTA ? 2 : _page == Page::Settings ? 3 : 4;
    auto* from = pagePanel(_page);
    if (!setPageForDebug(pages[(index + (direction > 0 ? 1 : 4)) % 5])) return;
    _slideFrom = from; _slideTo = pagePanel(_page); _slideDirection = direction > 0 ? 1 : -1;
    lv_obj_remove_flag(_slideFrom, LV_OBJ_FLAG_HIDDEN);
    // Executed by LVGL's existing timer under its single port mutex; no screen/snapshot allocations.
    slideExec(this, 0);
    lv_anim_t anim; lv_anim_init(&anim); lv_anim_set_var(&anim, this);
    lv_anim_set_exec_cb(&anim, slideExec); lv_anim_set_values(&anim, 0, 480);
    lv_anim_set_duration(&anim, 200); lv_anim_set_path_cb(&anim, lv_anim_path_ease_out);
    lv_anim_set_completed_cb(&anim, slideCompleted); lv_anim_start(&anim);
}
void CodexMicroView::setInputSuppressed(bool suppressed) {
    _suppressed = suppressed;
    refreshSessionsLease();
    if (suppressed) { _touchTracking = false; _swipeConsumed = true; cancelOrientation(); GetHAL().setMotionIdle(true); cancelPageSlide(); stopAnimations(); }
}
void CodexMicroView::togglePage() {
    if (_rotationFault) return;
    if (_suppressed || !ready()) return;
    if (_locked) { wakeDisplay(); return; }
    if (otaBusy()) return;
    navigatePage(1);
}
void CodexMicroView::wakeDisplay() {
    if (_rotationFault) return;
    if (!ready()) return;
    GetNetworkQuota().setLocked(false); _activity = lv_tick_get(); _locked = false;
    GetHAL().setTouchIdlePolling(false);
    GetHAL().setMotionIdle(_suppressed || otaBusy()); refreshClock(lv_tick_get(), true);
    if (_overlay) lv_obj_add_flag(_overlay, LV_OBJ_FLAG_HIDDEN);
    if (_lockPanel) lv_obj_add_flag(_lockPanel, LV_OBJ_FLAG_HIDDEN);
    refreshQuota(GetHAL().millis());
    if (_page == Page::History) refreshHistory();
    refreshDisplaySettings();
    GetHAL().setBackLightBrightness(_brightness, false); _appliedBrightness = _brightness;
    lv_obj_invalidate(_root);
    if (_page == Page::OTA) renderOta(); // Apply snapshots cached while locked without changing pages.
    if (_otaPending) { _otaPending = false; setPageForDebug(Page::OTA); }
    refreshSessionsLease();
}
void CodexMicroView::lockDisplay() {
    if (_rotationFault) return;
    if (_locked || !ready() || otaKeepAwake()) return;
    cancelOrientation(); GetHAL().setMotionIdle(true);
    cancelPageSlide(); _touchTracking = false; _swipeConsumed = true;
    stopAnimations();
    _locked = true; GetNetworkQuota().setLocked(true);
    refreshSessionsLease();
    GetHAL().setTouchIdlePolling(true);
    lv_obj_remove_flag(_lockPanel, LV_OBJ_FLAG_HIDDEN); lv_obj_move_foreground(_lockPanel);
    // Function-only wake needs no overlay above custom DRAW_MAIN clock dots.
    lv_obj_add_flag(_overlay, LV_OBJ_FLAG_HIDDEN);
    refreshClock(lv_tick_get(), true);
    lv_obj_invalidate(_lockPanel); // Also redraw an unchanged minute on lock entry.
    refreshQuota(GetHAL().millis()); ++_lockRefreshCount; _refresh = lv_tick_get();
    requestLockedSessions();renderLockSessions();
    GetHAL().setBackLightBrightness(_displaySettings.config.lockBrightness, false);
    _appliedBrightness = _displaySettings.config.lockBrightness;
}
bool CodexMicroView::lockForDebug() { if (!ready()) return false; lockDisplay(); return _locked; }
void CodexMicroView::update(const CodexMicroState& state) {
    if (!ready()) return;
    uint32_t tick = lv_tick_get();
    const bool networkReady = GetNetworkQuota().connected() && (!GetTailnetQuota().enabled() || GetTailnetQuota().ready());
    if (networkReady != _otaNetworkReady) { _otaNetworkReady = networkReady; if (!_locked) renderOtaAction(); }
    refreshOta(tick);
    refreshSessionsLease();
    refreshSessions(state);
    bool interacting = lv_obj_is_scrolling(_quotaPage), touching = false;
    for (auto* input = lv_indev_get_next(nullptr); input; input = lv_indev_get_next(input))
        touching = touching || lv_indev_get_state(input) == LV_INDEV_STATE_PRESSED;
    interacting = interacting || touching || _slideTo || _rotationPhase != RotationPhase::Idle;
    updateOrientation(touching);
    refreshDisplaySettings();
    if (!_locked) refreshClock(tick);
    // Rendering/page callbacks can record activity after the entry tick.
    tick = lv_tick_get();
    const bool keepAwake = otaKeepAwake();
    if (!_locked && (interacting || keepAwake)) _activity = tick;
    const uint32_t idleElapsed = tick - _activity;
    const uint32_t timeoutSeconds = _chargeProfile ? _displaySettings.config.chargeTimeoutSeconds :
        std::min<uint32_t>(60, _displaySettings.config.batteryTimeoutSeconds);
    if (!_locked && !interacting && !keepAwake && timeoutSeconds && idleElapsed >= timeoutSeconds * 1000U && idleElapsed < 0x80000000U) lockDisplay();
    // Revision checks are local memory only; no touch or UI path performs HTTP.
    // lockDisplay records a fresh refresh timestamp; never compare it to an older tick.
    tick = lv_tick_get();
    const uint32_t refreshElapsed = tick - _refresh;
    if (refreshElapsed >= 60000U && refreshElapsed < 0x80000000U) {
        _refresh = tick; refreshQuota(GetHAL().millis());
        if (!_locked) refreshHistory();
        if (_locked) { ++_lockRefreshCount;requestLockedSessions();renderLockSessions(); }
        _shiftPending = true;
    } else if (!_locked) {
        if (_quotaRevision != QuotaMonitorRevision()) refreshQuota(GetHAL().millis());
        if (_page == Page::History) refreshHistory();
    }
    if (!_locked && GetHAL().millis() - _batteryReadTick >= 5000) refreshBattery(GetHAL().millis());
    applyBurnInShift(touching);
    updateAnimations(tick);
}
}
