/*
 * SPDX-License-Identifier: MIT
 */
#include "view.h"

#include <apps/common/audio/audio.h>
#include <hal/hal.h>
#include <host/host_bridge.h>
#include <host/network_quota.h>
#include <host/token_history.h>
#include <host/token_units_font.h>
#include <system_config.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <new>

#include <esp_log.h>
#include <esp_timer.h>
#include <core/lv_obj_event_private.h>

namespace {

constexpr const char* Tag = "CodexMicro-UI";

constexpr uint32_t Background              = 0x0A0D0B;
constexpr uint32_t Text                    = 0xF7F7F0;
constexpr uint32_t Green                   = 0x59E3A5;
constexpr uint32_t Key                     = 0xF1F0EB;
constexpr uint32_t KeyPressed              = 0xE8ECE8;
constexpr uint32_t KeyBorder               = 0xFFFFFA;
constexpr uint32_t KeyInk                  = 0x171A18;
constexpr uint32_t KeyMuted                = 0x69716D;
constexpr uint32_t CommandFaceTop          = 0xFAFAF6;
constexpr uint32_t CommandFaceBottom       = 0xD7DED9;
constexpr uint32_t CommandPressedTop       = 0xD9DFDB;
constexpr uint32_t CommandPressedBottom    = 0xEBEFEC;
constexpr uint32_t CommandBezel            = 0x343B37;
constexpr uint32_t CodexBlue               = 0x566BF7;
constexpr uint32_t ArcTrack                = 0x686D6A;
constexpr uint32_t ArcThumb                = 0x626763;
constexpr uint32_t ArcThumbBorder          = 0xA9AEAB;
constexpr uint32_t ArcThumbActive          = 0x747A76;
constexpr uint32_t ArcThumbActiveBorder    = 0xD1D5D3;
constexpr uint32_t ArcThumbReturning       = 0x656A67;
constexpr uint32_t ArcThumbReturningBorder = 0xA6ACA8;
constexpr uint32_t Touch                   = 0x1D211F;
constexpr uint32_t TouchBorder             = 0x686F6B;
constexpr uint32_t Fingerprint             = 0xC9CECB;
constexpr uint32_t PairingLine             = 0x777E7A;
constexpr uint32_t PairingCore             = 0xF7F7EF;
constexpr uint32_t AgentOff                = 0xAEB4B0;
constexpr uint32_t StatusCard              = 0x121714;
constexpr uint32_t StatusBorder            = 0x3B4540;
constexpr uint32_t StatusStale             = 0xE4AF57;
constexpr uint32_t BatteryLow              = 0xFF666A;
constexpr uint32_t HistoryMissing          = 0x343A37;
constexpr uint32_t HistoryCorrection       = 0xD7A94C;
constexpr std::size_t HistoryDayCount      = 30;
constexpr std::size_t HistoryHourCount     = 24;
constexpr lv_style_selector_t PressedStyle =
    static_cast<lv_style_selector_t>(LV_PART_MAIN) | static_cast<lv_style_selector_t>(LV_STATE_PRESSED);

constexpr std::array<CodexMicroControl, 6> AgentControls = {
    CodexMicroControl::Agent1, CodexMicroControl::Agent2, CodexMicroControl::Agent3,
    CodexMicroControl::Agent4, CodexMicroControl::Agent5, CodexMicroControl::Agent6,
};
constexpr std::array<uint32_t, 6> CommandAccentColors = {
    0x8B9DFF, 0xB999FF, 0xF4B84B, 0x6F9FFF, Green, BatteryLow,
};
constexpr float FeedbackToneDurationSeconds            = 0.016f;
constexpr float FeedbackToneVolume                     = 0.38f;
constexpr uint16_t FeedbackVibrationDurationMs         = 18;
constexpr uint8_t FeedbackVibrationStrength            = 58;
constexpr float DialRatchetToneDurationSeconds         = 0.010f;
constexpr float DialRatchetToneVolume                  = 0.30f;
constexpr uint16_t DialRatchetVibrationDurationMs      = 10;
constexpr uint8_t DialRatchetVibrationStrength         = 44;
constexpr uint32_t AnimatedLightingRefreshPeriodMs     = 100;
constexpr uint32_t DisplayLockDelayMs                  = 60000;
constexpr uint32_t LockRefreshPeriodMs                 = 60000;
constexpr int LockedBrightness                         = 8;
constexpr uint32_t PixelShiftPeriodMs                  = 60000;
constexpr float AmbientBrightnessScale                 = 0.42f;
constexpr float AmbientSaturationScale                 = 0.72f;
constexpr int AmbientOuterSize                         = 464;
constexpr int AmbientLayerWidth                        = 2;
constexpr int AmbientLayerSizeStep                     = AmbientLayerWidth * 2;
constexpr std::array<lv_opa_t, 14> AmbientLayerOpacity = {
    LV_OPA_COVER,
    static_cast<lv_opa_t>(235),
    static_cast<lv_opa_t>(214),
    static_cast<lv_opa_t>(194),
    static_cast<lv_opa_t>(173),
    static_cast<lv_opa_t>(153),
    static_cast<lv_opa_t>(133),
    static_cast<lv_opa_t>(112),
    static_cast<lv_opa_t>(92),
    static_cast<lv_opa_t>(71),
    static_cast<lv_opa_t>(51),
    static_cast<lv_opa_t>(36),
    static_cast<lv_opa_t>(20),
    static_cast<lv_opa_t>(8),
};
constexpr std::array<std::array<int8_t, 2>, 5> PixelShiftOffsets = {
    std::array<int8_t, 2>{0, 0},  std::array<int8_t, 2>{1, 0},  std::array<int8_t, 2>{0, 1},
    std::array<int8_t, 2>{-1, 0}, std::array<int8_t, 2>{0, -1},
};

constexpr int DisplayCenter             = 233;
constexpr int DialRadius                = 208;
constexpr int DialStartDegrees          = 210;
constexpr int DialEndDegrees            = 330;
constexpr int DialStepCount             = 20;
constexpr int DialCenterStep            = DialStepCount / 2;
constexpr int DialPressRadius           = 44;
constexpr int DialIdlePressRadius       = 23;
constexpr int DialThumbWidth            = 46;
constexpr int DialThumbHeight           = 30;
constexpr uint32_t DialReturnDurationMs = 240;
constexpr uint32_t DialFeedbackPeriodMs = 40;
static_assert(AgentControls.size() == 6, "Codex Micro requires six Agent Keys");
static_assert(CommandAccentColors.size() == 6, "Command accents must match the six Command buttons");

const char* batterySymbol(uint8_t level)
{
    if (level >= 90) {
        return LV_SYMBOL_BATTERY_FULL;
    }
    if (level >= 65) {
        return LV_SYMBOL_BATTERY_3;
    }
    if (level >= 40) {
        return LV_SYMBOL_BATTERY_2;
    }
    if (level >= 15) {
        return LV_SYMBOL_BATTERY_1;
    }
    return LV_SYMBOL_BATTERY_EMPTY;
}

int feedbackMidi(CodexMicroControl control, int8_t agent)
{
    if (agent >= 0) {
        return 78 + agent * 2;
    }

    switch (control) {
        case CodexMicroControl::Fast:
            return 90;
        case CodexMicroControl::Approve:
            return 92;
        case CodexMicroControl::Decline:
            return 84;
        case CodexMicroControl::NewChat:
            return 88;
        case CodexMicroControl::NewTask:
            return 96;
        case CodexMicroControl::Mic:
            return 82;
        case CodexMicroControl::Send:
            return 94;
        case CodexMicroControl::EncoderPress:
            return 86;
        default:
            return 80;
    }
}

void playFeedback(CodexMicroControl control, int8_t agent = -1)
{
    const Hal::ButtonConfig& config = GetHAL().getButtonConfig();
    if (config.sfxEnabled) {
        audio::play_tone_from_midi(feedbackMidi(control, agent), FeedbackToneDurationSeconds, FeedbackToneVolume);
    }
    if (config.vibrateEnabled) {
        GetHAL().vibrate(FeedbackVibrationDurationMs, FeedbackVibrationStrength);
    }
}

void playDialRatchetFeedback(int direction)
{
    const Hal::ButtonConfig& config = GetHAL().getButtonConfig();
    if (config.sfxEnabled) {
        audio::play_tone_from_midi(direction > 0 ? 84 : 80, DialRatchetToneDurationSeconds, DialRatchetToneVolume);
    }
    if (config.vibrateEnabled) {
        GetHAL().vibrate(DialRatchetVibrationDurationMs, DialRatchetVibrationStrength);
    }
}

uint32_t scaledColor(uint32_t color, float brightness)
{
    brightness          = std::clamp(brightness, 0.0f, 1.0f);
    const uint8_t red   = static_cast<uint8_t>(((color >> 16) & 0xFF) * brightness);
    const uint8_t green = static_cast<uint8_t>(((color >> 8) & 0xFF) * brightness);
    const uint8_t blue  = static_cast<uint8_t>((color & 0xFF) * brightness);
    return (static_cast<uint32_t>(red) << 16) | (static_cast<uint32_t>(green) << 8) | blue;
}

uint32_t softenedAmbientColor(uint32_t color, float brightness)
{
    const float red     = static_cast<float>((color >> 16) & 0xFF);
    const float green   = static_cast<float>((color >> 8) & 0xFF);
    const float blue    = static_cast<float>(color & 0xFF);
    const float neutral = (red + green + blue) / 3.0f;
    const auto soften   = [neutral](float channel) { return neutral + (channel - neutral) * AmbientSaturationScale; };
    const uint32_t softened = (static_cast<uint32_t>(std::lround(soften(red))) << 16) |
                              (static_cast<uint32_t>(std::lround(soften(green))) << 8) |
                              static_cast<uint32_t>(std::lround(soften(blue)));
    return scaledColor(softened, brightness * AmbientBrightnessScale);
}

bool lightAssigned(const CodexMicroLight& light)
{
    // Brightness is a user setting and may legitimately be zero. The host uses
    // an off effect with color 0 for an unassigned Agent slot.
    return light.effect != CodexMicroLightEffect::Off && light.color != 0;
}

bool sameLight(const CodexMicroLight& lhs, const CodexMicroLight& rhs)
{
    return lhs.color == rhs.color && lhs.brightness == rhs.brightness && lhs.effect == rhs.effect &&
           lhs.speed == rhs.speed && lhs.magic == rhs.magic;
}

float animatedBrightness(const CodexMicroLight& light, float seconds)
{
    float brightness = light.brightness;
    if (light.effect == CodexMicroLightEffect::Breath || light.effect == CodexMicroLightEffect::ShallowBreath) {
        const float speed = light.speed > 0.05f ? light.speed : 1.0f;
        const float depth = light.effect == CodexMicroLightEffect::ShallowBreath ? 0.22f : 0.45f;
        brightness *= (1.0f - depth) + depth * (std::sin(seconds * 3.8f * speed) * 0.5f + 0.5f);
    }
    return std::clamp(brightness, 0.0f, 1.0f);
}

const char* lightStatus(const CodexMicroLight& light)
{
    if (!lightAssigned(light)) {
        return "Unassigned";
    }

    const int red       = (light.color >> 16) & 0xFF;
    const int green     = (light.color >> 8) & 0xFF;
    const int blue      = light.color & 0xFF;
    const int max_value = std::max({red, green, blue});
    const int min_value = std::min({red, green, blue});
    if (max_value - min_value < 48 && max_value > 150) {
        return "Idle";
    }
    if (blue >= red && blue > green) {
        return "Thinking";
    }
    if (red > 170 && green > 105 && blue < 120) {
        return "Needs input";
    }
    if (red > green && red >= blue) {
        return "Error";
    }
    if (green >= red && green >= blue) {
        return "Complete";
    }
    return "Active";
}

void drawLine(lv_layer_t* layer, int x1, int y1, int x2, int y2, int width, uint32_t color)
{
    lv_draw_line_dsc_t draw;
    lv_draw_line_dsc_init(&draw);
    draw.color       = lv_color_hex(color);
    draw.width       = width;
    draw.round_start = 1;
    draw.round_end   = 1;
    draw.p1          = {static_cast<lv_value_precise_t>(x1), static_cast<lv_value_precise_t>(y1)};
    draw.p2          = {static_cast<lv_value_precise_t>(x2), static_cast<lv_value_precise_t>(y2)};
    lv_draw_line(layer, &draw);
}

void drawArc(lv_layer_t* layer, int x, int y, int radius, int start_angle, int end_angle, int width, uint32_t color,
             bool rounded = true, lv_opa_t opacity = LV_OPA_COVER)
{
    lv_draw_arc_dsc_t draw;
    lv_draw_arc_dsc_init(&draw);
    draw.color       = lv_color_hex(color);
    draw.width       = width;
    draw.rounded     = rounded ? 1 : 0;
    draw.center      = {static_cast<lv_coord_t>(x), static_cast<lv_coord_t>(y)};
    draw.radius      = radius;
    draw.start_angle = start_angle;
    draw.end_angle   = end_angle;
    draw.opa         = opacity;
    lv_draw_arc(layer, &draw);
}

void drawOutline(lv_layer_t* layer, int x1, int y1, int x2, int y2, int radius, int width, uint32_t color)
{
    lv_draw_rect_dsc_t draw;
    lv_draw_rect_dsc_init(&draw);
    draw.bg_opa       = LV_OPA_TRANSP;
    draw.border_color = lv_color_hex(color);
    draw.border_width = width;
    draw.radius       = radius;
    lv_area_t area    = {x1, y1, x2, y2};
    lv_draw_rect(layer, &draw, &area);
}

}  // namespace

namespace view {

CodexMicroView::~CodexMicroView()
{
    GetHAL().setBackLightBrightness(_display_base_brightness, false);
    releaseActiveInputs();
    if (_wake_overlay != nullptr) {
        lv_obj_delete(_wake_overlay);
        _wake_overlay = nullptr;
    }
    if (_root != nullptr) {
        lv_obj_delete(_root);
    }
}

void CodexMicroView::stylePanel(lv_obj_t* object, uint32_t background, uint32_t border, int radius, int border_width)
{
    lv_obj_set_style_bg_color(object, lv_color_hex(background), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(object, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_color(object, lv_color_hex(border), LV_PART_MAIN);
    lv_obj_set_style_border_width(object, border_width, LV_PART_MAIN);
    lv_obj_set_style_radius(object, radius, LV_PART_MAIN);
    lv_obj_set_style_pad_all(object, 0, LV_PART_MAIN);
}

void CodexMicroView::init(lv_obj_t* parent)
{
    _display_base_brightness = std::max(10, GetHAL().getBackLightBrightness());
    _last_activity_tick      = lv_tick_get();
    _display_power           = DisplayPowerState::Active;
    GetHAL().setBackLightBrightness(_display_base_brightness, false);

    lv_obj_remove_flag(parent, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(parent, lv_color_hex(Background), LV_PART_MAIN);

    _root = lv_obj_create(parent);
    lv_obj_set_size(_root, 466, 466);
    lv_obj_align(_root, LV_ALIGN_CENTER, 0, 0);
    lv_obj_remove_flag(_root, LV_OBJ_FLAG_SCROLLABLE);
    stylePanel(_root, Background, Background, 0, 0);

    for (std::size_t index = 0; index < _ambient_layers.size(); ++index) {
        lv_obj_t* layer        = lv_arc_create(_root);
        _ambient_layers[index] = layer;
        const int size         = AmbientOuterSize - static_cast<int>(index) * AmbientLayerSizeStep;
        lv_obj_set_size(layer, size, size);
        lv_obj_align(layer, LV_ALIGN_CENTER, 0, 0);
        lv_obj_remove_flag(layer, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_arc_width(layer, 0, LV_PART_MAIN);
        lv_obj_set_style_arc_width(layer, AmbientLayerWidth, LV_PART_INDICATOR);
        lv_obj_set_style_arc_rounded(layer, false, LV_PART_INDICATOR);
        lv_obj_set_style_arc_opa(layer, LV_OPA_TRANSP, LV_PART_INDICATOR);
        lv_obj_set_style_bg_opa(layer, LV_OPA_TRANSP, LV_PART_KNOB);
        lv_arc_set_angles(layer, 0, 360);
    }

    for (lv_obj_t*& page_root : _page_roots) {
        page_root = createPageRoot();
        lv_obj_add_flag(page_root, LV_OBJ_FLAG_HIDDEN);
    }
    renderCommand(_page_roots[static_cast<std::size_t>(Page::Command)]);
    renderHistory(_page_roots[static_cast<std::size_t>(Page::History)]);
    renderAgent(_page_roots[static_cast<std::size_t>(Page::Agent)]);
    lv_obj_remove_flag(_page_roots[static_cast<std::size_t>(_page)], LV_OBJ_FLAG_HIDDEN);

    _touch_control = lv_button_create(_root);
    lv_obj_set_pos(_touch_control, 156, 414);
    lv_obj_set_size(_touch_control, 154, 68);
    stylePanel(_touch_control, Touch, TouchBorder, 77, 1);
    lv_obj_set_style_bg_color(_touch_control, lv_color_hex(0x2A2F2C), PressedStyle);
    lv_obj_set_style_border_color(_touch_control, lv_color_hex(Green), PressedStyle);
    lv_obj_set_style_shadow_width(_touch_control, 8, LV_PART_MAIN);
    lv_obj_set_style_shadow_opa(_touch_control, LV_OPA_20, LV_PART_MAIN);
    lv_obj_set_style_shadow_color(_touch_control, lv_color_hex(0x000000), LV_PART_MAIN);
    _icon_contexts[7] = {.owner = this, .icon = Icon::Fingerprint};
    lv_obj_add_event_cb(_touch_control, iconEvent, LV_EVENT_DRAW_MAIN, &_icon_contexts[7]);
    lv_obj_add_event_cb(_touch_control, touchEvent, LV_EVENT_PRESSED, this);
    lv_obj_add_event_cb(_touch_control, touchEvent, LV_EVENT_RELEASED, this);
    lv_obj_add_event_cb(_touch_control, touchEvent, LV_EVENT_PRESS_LOST, this);

    lv_obj_move_foreground(_touch_control);

    _mic_screen = lv_obj_create(_root);
    lv_obj_set_pos(_mic_screen, 0, 0);
    lv_obj_set_size(_mic_screen, 466, 466);
    lv_obj_add_flag(_mic_screen, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(_mic_screen, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(_mic_screen, LV_OBJ_FLAG_SCROLLABLE);
    stylePanel(_mic_screen, Background, Background, 0, 0);

    lv_obj_t* mic_icon = lv_obj_create(_mic_screen);
    lv_obj_remove_flag(mic_icon, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(mic_icon, 169, 92);
    lv_obj_set_size(mic_icon, 128, 128);
    stylePanel(mic_icon, Key, KeyBorder, 44, 1);
    lv_obj_set_style_shadow_width(mic_icon, 10, LV_PART_MAIN);
    lv_obj_set_style_shadow_opa(mic_icon, LV_OPA_20, LV_PART_MAIN);
    lv_obj_set_style_shadow_color(mic_icon, lv_color_hex(0x000000), LV_PART_MAIN);
    _icon_contexts[6] = {.owner = this, .icon = Icon::Mic};
    lv_obj_add_event_cb(mic_icon, iconEvent, LV_EVENT_DRAW_MAIN, &_icon_contexts[6]);

    constexpr int MicMeterLeft    = 165;
    constexpr int MicMeterCenterY = 307;
    constexpr int MicBarPitch     = 17;
    for (std::size_t index = 0; index < _mic_bars.size(); ++index) {
        _mic_bars[index] = lv_obj_create(_mic_screen);
        lv_obj_remove_flag(_mic_bars[index], LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_pos(_mic_bars[index], MicMeterLeft + static_cast<int>(index) * MicBarPitch, MicMeterCenterY - 3);
        lv_obj_set_size(_mic_bars[index], 8, 6);
        stylePanel(_mic_bars[index], Text, Text, 4, 0);
        lv_obj_set_style_opa(_mic_bars[index], LV_OPA_30, LV_PART_MAIN);
    }

    lv_obj_t* mic_source = lv_label_create(_mic_screen);
    lv_label_set_text(mic_source, "PTT / COMPUTER MIC");
    lv_obj_set_style_text_font(mic_source, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(mic_source, lv_color_hex(KeyMuted), LV_PART_MAIN);
    lv_obj_align(mic_source, LV_ALIGN_CENTER, 0, 116);

    _pairing_screen = lv_obj_create(_root);
    lv_obj_set_pos(_pairing_screen, 0, 0);
    lv_obj_set_size(_pairing_screen, 466, 466);
    lv_obj_add_flag(_pairing_screen, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(_pairing_screen, LV_OBJ_FLAG_SCROLLABLE);
    stylePanel(_pairing_screen, Background, Background, 0, 0);

    _pairing_pulse = lv_obj_create(_pairing_screen);
    lv_obj_remove_flag(_pairing_pulse, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(_pairing_pulse, 78, 78);
    lv_obj_align(_pairing_pulse, LV_ALIGN_CENTER, 0, -55);
    lv_obj_set_style_bg_opa(_pairing_pulse, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_color(_pairing_pulse, lv_color_hex(PairingLine), LV_PART_MAIN);
    lv_obj_set_style_border_width(_pairing_pulse, 1, LV_PART_MAIN);
    lv_obj_set_style_radius(_pairing_pulse, LV_RADIUS_CIRCLE, LV_PART_MAIN);
    lv_obj_set_style_shadow_width(_pairing_pulse, 14, LV_PART_MAIN);
    lv_obj_set_style_shadow_opa(_pairing_pulse, LV_OPA_10, LV_PART_MAIN);
    lv_obj_set_style_shadow_color(_pairing_pulse, lv_color_hex(PairingCore), LV_PART_MAIN);

    _pairing_core = lv_obj_create(_pairing_pulse);
    lv_obj_remove_flag(_pairing_core, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(_pairing_core, 14, 14);
    lv_obj_align(_pairing_core, LV_ALIGN_CENTER, 0, 0);
    stylePanel(_pairing_core, PairingCore, PairingCore, LV_RADIUS_CIRCLE, 0);

    lv_obj_t* pairing_title = lv_label_create(_pairing_screen);
    lv_label_set_text(pairing_title, "Pairing");
    lv_obj_set_style_text_font(pairing_title, &lv_font_montserrat_28, LV_PART_MAIN);
    lv_obj_set_style_text_color(pairing_title, lv_color_hex(Text), LV_PART_MAIN);
    lv_obj_align(pairing_title, LV_ALIGN_CENTER, 0, 20);

    for (std::size_t index = 0; index < _pairing_dots.size(); ++index) {
        _pairing_dots[index] = lv_obj_create(_pairing_screen);
        lv_obj_remove_flag(_pairing_dots[index], LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_size(_pairing_dots[index], 6, 6);
        lv_obj_set_pos(_pairing_dots[index], 216 + static_cast<int>(index) * 14, 286);
        stylePanel(_pairing_dots[index], PairingCore, PairingCore, LV_RADIUS_CIRCLE, 0);
    }

    _pairing_reset_control = lv_button_create(_pairing_screen);
    lv_obj_set_size(_pairing_reset_control, 210, 54);
    lv_obj_align(_pairing_reset_control, LV_ALIGN_CENTER, 0, 132);
    stylePanel(_pairing_reset_control, Touch, TouchBorder, 27, 1);
    lv_obj_set_style_bg_color(_pairing_reset_control, lv_color_hex(0x2A2F2C), PressedStyle);
    lv_obj_set_style_border_color(_pairing_reset_control, lv_color_hex(Green), PressedStyle);
    lv_obj_add_event_cb(_pairing_reset_control, touchEvent, LV_EVENT_PRESSED, this);
    lv_obj_add_event_cb(_pairing_reset_control, touchEvent, LV_EVENT_RELEASED, this);
    lv_obj_add_event_cb(_pairing_reset_control, touchEvent, LV_EVENT_PRESS_LOST, this);

    lv_obj_t* pairing_reset_label = lv_label_create(_pairing_reset_control);
    lv_label_set_text(pairing_reset_label, "HOLD 3s TO RESET");
    lv_obj_set_style_text_font(pairing_reset_label, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(pairing_reset_label, lv_color_hex(Text), LV_PART_MAIN);
    lv_obj_center(pairing_reset_label);

    lv_obj_move_foreground(_pairing_screen);

    _wake_overlay = lv_obj_create(parent);
    lv_obj_set_pos(_wake_overlay, 0, 0);
    lv_obj_set_size(_wake_overlay, 466, 466);
    lv_obj_add_flag(_wake_overlay, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(_wake_overlay, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(_wake_overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_opa(_wake_overlay, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(_wake_overlay, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(_wake_overlay, 0, LV_PART_MAIN);
    lv_obj_add_event_cb(_wake_overlay, wakeOverlayEvent, LV_EVENT_PRESSED, this);
    lv_obj_add_event_cb(_wake_overlay, wakeOverlayEvent, LV_EVENT_RELEASED, this);
    lv_obj_add_event_cb(_wake_overlay, wakeOverlayEvent, LV_EVENT_PRESS_LOST, this);

    _lock_screen = lv_obj_create(_root);
    lv_obj_set_pos(_lock_screen, 0, 0);
    lv_obj_set_size(_lock_screen, 466, 466);
    lv_obj_add_flag(_lock_screen, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(_lock_screen, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(_lock_screen, LV_OBJ_FLAG_SCROLLABLE);
    stylePanel(_lock_screen, 0x000000, 0x000000, 0, 0);

    _lock_quota_label = lv_label_create(_lock_screen);
    lv_obj_set_width(_lock_quota_label, 400);
    lv_obj_set_style_text_font(_lock_quota_label, &lv_font_montserrat_28, LV_PART_MAIN);
    lv_obj_set_style_text_color(_lock_quota_label, lv_color_hex(Text), LV_PART_MAIN);
    lv_obj_set_style_text_align(_lock_quota_label, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_obj_align(_lock_quota_label, LV_ALIGN_CENTER, 0, -42);

    _lock_battery_label = lv_label_create(_lock_screen);
    lv_obj_set_width(_lock_battery_label, 400);
    lv_obj_set_style_text_font(_lock_battery_label, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_set_style_text_color(_lock_battery_label, lv_color_hex(KeyMuted), LV_PART_MAIN);
    lv_obj_set_style_text_align(_lock_battery_label, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_obj_align(_lock_battery_label, LV_ALIGN_CENTER, 0, 72);

    renderPage();
    ESP_LOGI(Tag, "Stopwatch Micro UI ready: 6 touch command + A mic + B send + 6 agent keys");
}

lv_obj_t* CodexMicroView::createPageRoot()
{
    lv_obj_t* page = lv_obj_create(_root);
    lv_obj_set_pos(page, 0, 0);
    lv_obj_set_size(page, 466, 466);
    lv_obj_remove_flag(page, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_opa(page, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_width(page, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(page, 0, LV_PART_MAIN);
    return page;
}

lv_obj_t* CodexMicroView::createKeyButton(lv_obj_t* parent, std::array<lv_obj_t*, 6>& buttons,
                                          std::array<KeyContext, 6>& contexts, std::size_t slot, int x, int y,
                                          int width, int height, CodexMicroControl control, int8_t agent,
                                          uint32_t background, uint32_t border, int radius)
{
    lv_obj_t* button = lv_button_create(parent);
    lv_obj_set_pos(button, x, y);
    lv_obj_set_size(button, width, height);
    stylePanel(button, background, border, radius, 1);
    lv_obj_set_style_bg_color(button, lv_color_hex(KeyPressed), PressedStyle);
    lv_obj_set_style_border_color(button, lv_color_hex(CodexBlue), PressedStyle);
    lv_obj_set_style_transform_width(button, -4, PressedStyle);
    lv_obj_set_style_transform_height(button, -4, PressedStyle);
    lv_obj_set_style_shadow_width(button, 8, LV_PART_MAIN);
    lv_obj_set_style_shadow_opa(button, LV_OPA_20, LV_PART_MAIN);
    lv_obj_set_style_shadow_color(button, lv_color_hex(0x000000), LV_PART_MAIN);

    buttons[slot]  = button;
    contexts[slot] = {.owner = this, .control = control, .agent = agent, .active = false};
    lv_obj_add_event_cb(button, keyEvent, LV_EVENT_PRESSED, &contexts[slot]);
    lv_obj_add_event_cb(button, keyEvent, LV_EVENT_RELEASED, &contexts[slot]);
    lv_obj_add_event_cb(button, keyEvent, LV_EVENT_PRESS_LOST, &contexts[slot]);
    return button;
}

void CodexMicroView::createCommandButton(lv_obj_t* parent, std::size_t slot, int x, int y, const char* label,
                                         CommandAction action, CodexMicroControl control)
{
    lv_obj_t* button = lv_button_create(parent);
    lv_obj_set_pos(button, x, y);
    lv_obj_set_size(button, 78, 78);
    stylePanel(button, CommandFaceTop, KeyBorder, LV_RADIUS_CIRCLE, 1);
    lv_obj_set_style_bg_grad_color(button, lv_color_hex(CommandFaceBottom), LV_PART_MAIN);
    lv_obj_set_style_bg_grad_dir(button, LV_GRAD_DIR_VER, LV_PART_MAIN);
    lv_obj_set_style_outline_width(button, 2, LV_PART_MAIN);
    lv_obj_set_style_outline_pad(button, 1, LV_PART_MAIN);
    lv_obj_set_style_outline_color(button, lv_color_hex(CommandBezel), LV_PART_MAIN);
    lv_obj_set_style_outline_opa(button, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_shadow_width(button, 8, LV_PART_MAIN);
    lv_obj_set_style_shadow_offset_y(button, 3, LV_PART_MAIN);
    lv_obj_set_style_shadow_spread(button, 0, LV_PART_MAIN);
    lv_obj_set_style_shadow_opa(button, LV_OPA_30, LV_PART_MAIN);
    lv_obj_set_style_shadow_color(button, lv_color_hex(0x000000), LV_PART_MAIN);

    lv_obj_set_style_bg_color(button, lv_color_hex(CommandPressedTop), PressedStyle);
    lv_obj_set_style_bg_grad_color(button, lv_color_hex(CommandPressedBottom), PressedStyle);
    lv_obj_set_style_bg_grad_dir(button, LV_GRAD_DIR_VER, PressedStyle);
    lv_obj_set_style_outline_color(button, lv_color_hex(CommandAccentColors[slot]), PressedStyle);
    lv_obj_set_style_outline_width(button, 3, PressedStyle);
    lv_obj_set_style_shadow_width(button, 3, PressedStyle);
    lv_obj_set_style_shadow_offset_y(button, 1, PressedStyle);
    lv_obj_set_style_shadow_opa(button, LV_OPA_20, PressedStyle);
    lv_obj_set_style_transform_width(button, -4, PressedStyle);
    lv_obj_set_style_transform_height(button, -4, PressedStyle);
    lv_obj_set_style_translate_y(button, 2, PressedStyle);

    _command_buttons[slot]  = button;
    _command_contexts[slot] = {.owner = this, .action = action, .control = control, .slot = slot, .active = false};
    lv_obj_add_event_cb(button, commandEvent, LV_EVENT_PRESSED, &_command_contexts[slot]);
    lv_obj_add_event_cb(button, commandEvent, LV_EVENT_RELEASED, &_command_contexts[slot]);
    lv_obj_add_event_cb(button, commandEvent, LV_EVENT_PRESS_LOST, &_command_contexts[slot]);

    lv_obj_t* highlight = lv_arc_create(button);
    lv_obj_set_size(highlight, 62, 62);
    lv_obj_center(highlight);
    lv_obj_remove_flag(highlight, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(highlight, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_arc_width(highlight, 0, LV_PART_MAIN);
    lv_obj_set_style_arc_width(highlight, 2, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(highlight, lv_color_hex(0xFFFFFF), LV_PART_INDICATOR);
    lv_obj_set_style_arc_opa(highlight, LV_OPA_50, LV_PART_INDICATOR);
    lv_obj_set_style_arc_rounded(highlight, true, LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(highlight, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_arc_set_angles(highlight, 210, 330);

    lv_obj_t* title = lv_label_create(button);
    lv_label_set_text(title, label);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(title, lv_color_hex(KeyInk), LV_PART_MAIN);
    lv_obj_set_style_text_align(title, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_obj_center(title);
}

void setLabelText(lv_obj_t* label, const char* text)
{
    if (label != nullptr && text != nullptr && std::strcmp(lv_label_get_text(label), text) != 0) {
        lv_label_set_text(label, text);
    }
}

void formatResetCountdown(char* output, std::size_t size, uint32_t seconds)
{
    if (seconds >= 86400U) {
        std::snprintf(output, size, "RESET %lud %02luh", static_cast<unsigned long>(seconds / 86400U),
                      static_cast<unsigned long>((seconds % 86400U) / 3600U));
    } else if (seconds >= 3600U) {
        std::snprintf(output, size, "RESET %luh %02lum", static_cast<unsigned long>(seconds / 3600U),
                      static_cast<unsigned long>((seconds % 3600U) / 60U));
    } else {
        std::snprintf(output, size, "RESET %lum %02lus", static_cast<unsigned long>(seconds / 60U),
                      static_cast<unsigned long>(seconds % 60U));
    }
}

void CodexMicroView::renderCommand(lv_obj_t* parent)
{
    lv_obj_t* command_track = lv_obj_create(parent);
    lv_obj_set_pos(command_track, 83, 83);
    lv_obj_set_size(command_track, 300, 300);
    lv_obj_remove_flag(command_track, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(command_track, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_opa(command_track, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_border_color(command_track, lv_color_hex(0x303833), LV_PART_MAIN);
    lv_obj_set_style_border_opa(command_track, LV_OPA_40, LV_PART_MAIN);
    lv_obj_set_style_border_width(command_track, 1, LV_PART_MAIN);
    lv_obj_set_style_radius(command_track, LV_RADIUS_CIRCLE, LV_PART_MAIN);
    lv_obj_set_style_pad_all(command_track, 0, LV_PART_MAIN);

    createCommandButton(parent, 0, 119, 64, "PLAN", CommandAction::Plan, CodexMicroControl::Fast);
    createCommandButton(parent, 1, 269, 64, "NEW\nTASK", CommandAction::Key, CodexMicroControl::NewTask);
    createCommandButton(parent, 2, 44, 194, "FAST", CommandAction::Key, CodexMicroControl::Fast);
    createCommandButton(parent, 3, 344, 194, "FORK", CommandAction::Key, CodexMicroControl::NewChat);
    createCommandButton(parent, 4, 119, 324, "APPROVE", CommandAction::Key, CodexMicroControl::Approve);
    createCommandButton(parent, 5, 269, 324, "DECLINE", CommandAction::Key, CodexMicroControl::Decline);

    renderCenterStatus(parent);
    renderNavigation(parent);
}

void CodexMicroView::renderCenterStatus(lv_obj_t* parent)
{
    _usage_card = lv_obj_create(parent);
    lv_obj_set_pos(_usage_card, 146, 146);
    lv_obj_set_size(_usage_card, 174, 174);
    lv_obj_remove_flag(_usage_card, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(_usage_card, LV_OBJ_FLAG_SCROLLABLE);
    stylePanel(_usage_card, StatusCard, StatusBorder, LV_RADIUS_CIRCLE, 1);

    _usage_status_label = lv_label_create(_usage_card);
    lv_label_set_text(_usage_status_label, "CODEX");
    lv_obj_set_style_text_font(_usage_status_label, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(_usage_status_label, lv_color_hex(KeyMuted), LV_PART_MAIN);
    lv_obj_align(_usage_status_label, LV_ALIGN_TOP_MID, 0, 12);

    _usage_value_label = lv_label_create(_usage_card);
    lv_label_set_text(_usage_value_label, "--");
    lv_obj_set_style_text_font(_usage_value_label, &lv_font_montserrat_28, LV_PART_MAIN);
    lv_obj_set_style_text_color(_usage_value_label, lv_color_hex(Text), LV_PART_MAIN);
    lv_obj_align(_usage_value_label, LV_ALIGN_TOP_MID, 0, 34);

    _usage_bar = lv_bar_create(_usage_card);
    lv_obj_set_size(_usage_bar, 116, 8);
    lv_obj_align(_usage_bar, LV_ALIGN_TOP_MID, 0, 72);
    lv_bar_set_range(_usage_bar, 0, 10000);
    lv_bar_set_value(_usage_bar, 0, LV_ANIM_OFF);
    lv_obj_set_style_bg_color(_usage_bar, lv_color_hex(0x29302C), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(_usage_bar, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(_usage_bar, 4, LV_PART_MAIN);
    lv_obj_set_style_bg_color(_usage_bar, lv_color_hex(Green), LV_PART_INDICATOR);
    lv_obj_set_style_radius(_usage_bar, 4, LV_PART_INDICATOR);

    _usage_reset_label = lv_label_create(_usage_card);
    lv_label_set_text(_usage_reset_label, "RESET --");
    lv_obj_set_style_text_font(_usage_reset_label, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(_usage_reset_label, lv_color_hex(Text), LV_PART_MAIN);
    lv_obj_align(_usage_reset_label, LV_ALIGN_TOP_MID, 0, 92);

    lv_obj_t* divider = lv_obj_create(_usage_card);
    lv_obj_remove_flag(divider, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(divider, 116, 1);
    lv_obj_align(divider, LV_ALIGN_TOP_MID, 0, 119);
    stylePanel(divider, StatusBorder, StatusBorder, 0, 0);

    _battery_label = lv_label_create(_usage_card);
    lv_label_set_text(_battery_label, LV_SYMBOL_BATTERY_EMPTY " --%");
    lv_obj_set_style_text_font(_battery_label, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_set_style_text_color(_battery_label, lv_color_hex(Text), LV_PART_MAIN);
    lv_obj_align(_battery_label, LV_ALIGN_TOP_MID, 0, 132);
}

void CodexMicroView::renderAgent(lv_obj_t* parent)
{
    for (std::size_t index = 0; index < AgentControls.size(); ++index) {
        const int row    = static_cast<int>(index / 2);
        const int col    = static_cast<int>(index % 2);
        const int x      = 48 + col * 191;
        const int y      = 104 + row * 88;
        lv_obj_t* button = createKeyButton(parent, _agent_buttons, _agent_contexts, index, x, y, 179, 78,
                                           AgentControls[index], static_cast<int8_t>(index), Key, KeyBorder, 28);

        lv_obj_t* title = lv_label_create(button);
        lv_label_set_text_fmt(title, "Agent %d", static_cast<int>(index + 1));
        lv_obj_set_style_text_font(title, &lv_font_montserrat_14, LV_PART_MAIN);
        lv_obj_set_style_text_color(title, lv_color_hex(KeyInk), LV_PART_MAIN);
        lv_obj_set_pos(title, 14, 16);

        _agent_labels[index] = lv_label_create(button);
        lv_label_set_text(_agent_labels[index], "Unassigned");
        lv_obj_set_style_text_font(_agent_labels[index], &lv_font_montserrat_14, LV_PART_MAIN);
        lv_obj_set_style_text_color(_agent_labels[index], lv_color_hex(KeyMuted), LV_PART_MAIN);
        lv_obj_set_pos(_agent_labels[index], 14, 43);

        _agent_dots[index] = lv_obj_create(button);
        lv_obj_remove_flag(_agent_dots[index], LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_size(_agent_dots[index], 14, 14);
        stylePanel(_agent_dots[index], AgentOff, 0x979E9A, LV_RADIUS_CIRCLE, 1);
        lv_obj_set_pos(_agent_dots[index], 149, 32);
    }
}

void CodexMicroView::renderHistory(lv_obj_t* parent)
{
    stylePanel(parent, Background, Background, LV_RADIUS_CIRCLE, 0);
    lv_obj_t* title = lv_label_create(parent);
    lv_label_set_text(title, "TOKEN HISTORY");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_28, LV_PART_MAIN);
    lv_obj_set_style_text_color(title, lv_color_hex(Text), LV_PART_MAIN);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 42);

    constexpr std::array<const char*, 2> ModeNames = {"30 DAYS", "24 HOURS"};
    for (std::size_t index = 0; index < _history_mode_buttons.size(); ++index) {
        lv_obj_t* button             = lv_button_create(parent);
        _history_mode_buttons[index] = button;
        lv_obj_set_pos(button, 132 + static_cast<int>(index) * 104, 96);
        lv_obj_set_size(button, 98, 36);
        stylePanel(button, StatusCard, StatusBorder, 18, 1);
        lv_obj_t* label = lv_label_create(button);
        lv_label_set_text(label, ModeNames[index]);
        lv_obj_set_style_text_font(label, &lv_font_montserrat_14, LV_PART_MAIN);
        lv_obj_center(label);
        _history_mode_contexts[index] = {.owner = this, .index = index};
        lv_obj_add_event_cb(button, historyModeEvent, LV_EVENT_CLICKED, &_history_mode_contexts[index]);
    }

    _history_grid = lv_obj_create(parent);
    lv_obj_remove_style_all(_history_grid);
    lv_obj_set_pos(_history_grid, 65, 145);
    lv_obj_set_size(_history_grid, 340, 190);
    lv_obj_add_flag(_history_grid, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(_history_grid, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(_history_grid, historyGridEvent, LV_EVENT_DRAW_MAIN, this);
    lv_obj_add_event_cb(_history_grid, historyGridEvent, LV_EVENT_CLICKED, this);

    _history_footer = lv_label_create(parent);
    lv_obj_set_width(_history_footer, 360);
    lv_obj_set_style_text_font(_history_footer, GetTokenUnitFont(), LV_PART_MAIN);
    lv_obj_set_style_text_color(_history_footer, lv_color_hex(KeyMuted), LV_PART_MAIN);
    lv_obj_set_style_text_align(_history_footer, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_obj_align(_history_footer, LV_ALIGN_TOP_MID, 0, 345);
    setLabelText(_history_footer, "Waiting for token history");
}

const TokenHistoryCell* CodexMicroView::selectedHistoryCell() const
{
    if (_history_snapshot == nullptr || _history_selected == SIZE_MAX) {
        return nullptr;
    }
    if (_history_mode == HistoryMode::Hours) {
        return _history_selected < _history_snapshot->hours.size() ? &_history_snapshot->hours[_history_selected]
                                                                   : nullptr;
    }
    return _history_selected < _history_snapshot->days.size() ? &_history_snapshot->days[_history_selected] : nullptr;
}

void CodexMicroView::updateHistorySelection()
{
    const TokenHistoryCell* cell = selectedHistoryCell();
    char details[192]{};
    if (cell == nullptr) {
        std::snprintf(details, sizeof(details),
                      _history_snapshot != nullptr && _history_snapshot->available
                          ? (_history_mode == HistoryMode::Hours ? "Observed updates | tap for details"
                                                                 : "Official daily tokens | tap a date")
                          : "Waiting for token history");
    } else {
        const char* quality = "missing";
        switch (cell->quality) {
            case TokenHistoryQuality::Official:
                quality = "official";
                break;
            case TokenHistoryQuality::Observed:
                quality = "observed cumulative delta";
                break;
            case TokenHistoryQuality::Partial:
                quality = "observed / partial";
                break;
            case TokenHistoryQuality::Correction:
                quality = "correction";
                break;
            case TokenHistoryQuality::Missing:
                break;
        }
        if (cell->valid) {
            char amount[32]{};
            FormatTokenAmount(cell->tokens, amount, sizeof(amount));
            std::snprintf(details, sizeof(details), "%s\n%s (%llu) | %s", cell->label, amount,
                          static_cast<unsigned long long>(cell->tokens), quality);
        } else {
            std::snprintf(details, sizeof(details), "%s\n%s", cell->label[0] ? cell->label : "Unavailable", quality);
        }
    }
    if (_history_snapshot != nullptr && _history_snapshot->available) {
        const uint32_t elapsed = (GetHAL().millis() - _history_snapshot->receivedAtMs) / 1000U;
        if (static_cast<uint64_t>(_history_snapshot->ageSecondsAtReceipt) + elapsed > 600U) {
            std::strncat(details, "\nCACHED / STALE", sizeof(details) - std::strlen(details) - 1U);
        }
    }
    setLabelText(_history_footer, details);
}

void CodexMicroView::refreshHistory(bool force)
{
    const uint32_t revision = TokenHistoryRevision();
    if (!force && !_history_dirty && revision == _history_revision) {
        return;
    }
    if (_history_snapshot == nullptr) {
        _history_snapshot.reset(new (std::nothrow) TokenHistorySnapshot());
        if (_history_snapshot == nullptr) {
            return;
        }
    }
    if (revision != _history_revision || force) {
        if (CopyTokenHistory(*_history_snapshot)) {
            _history_revision = revision;
        } else if (revision == 0 && !_history_snapshot->available) {
            _history_revision = 0;
        } else {
            return;  // Keep the previous cache on a transient mutex timeout.
        }
    }
    const bool hourly             = _history_mode == HistoryMode::Hours;
    const std::size_t count       = hourly ? _history_snapshot->hours.size() : _history_snapshot->days.size();
    const TokenHistoryCell* cells = hourly ? _history_snapshot->hours.data() : _history_snapshot->days.data();
    uint64_t maximum              = 0;
    for (std::size_t index = 0; index < count; ++index) {
        if (cells[index].valid) maximum = std::max(maximum, cells[index].tokens);
    }
    const double max_log = maximum == 0 ? 1.0 : std::log1p(static_cast<double>(maximum));
    for (std::size_t index = 0; index < _history_colors.size(); ++index) {
        if (index >= count) {
            _history_colors[index]  = Background;
            _history_borders[index] = Background;
            continue;
        }
        const TokenHistoryCell& cell = cells[index];
        uint32_t color               = HistoryMissing;
        if (cell.quality == TokenHistoryQuality::Correction)
            color = HistoryCorrection;
        else if (cell.valid) {
            const float level   = static_cast<float>(std::log1p(static_cast<double>(cell.tokens)) / max_log);
            const uint32_t base = hourly ? CodexBlue : Green;
            color               = scaledColor(base, 0.25f + 0.75f * level);
        }
        _history_colors[index]  = color;
        _history_borders[index] = cell.quality == TokenHistoryQuality::Correction ? HistoryCorrection
                                  : cell.quality == TokenHistoryQuality::Partial  ? 0xA879E6
                                  : cell.valid                                    ? color
                                                                                  : StatusBorder;
    }
    for (std::size_t index = 0; index < _history_mode_buttons.size(); ++index) {
        const bool selected = static_cast<std::size_t>(_history_mode) == index;
        stylePanel(_history_mode_buttons[index], selected ? (index == 0 ? Green : CodexBlue) : StatusCard,
                   selected ? Text : StatusBorder, 18, 1);
    }
    updateHistorySelection();
    _history_dirty = false;
    if (_history_grid != nullptr) lv_obj_invalidate(_history_grid);
}

void CodexMicroView::renderNavigation(lv_obj_t* parent)
{
    lv_obj_add_event_cb(parent, dialTrackEvent, LV_EVENT_DRAW_MAIN, this);

    lv_obj_t* reasoning = lv_label_create(parent);
    lv_label_set_text(reasoning, "REASONING");
    lv_obj_set_style_text_font(reasoning, &lv_font_montserrat_14, LV_PART_MAIN);
    lv_obj_set_style_text_color(reasoning, lv_color_hex(Text), LV_PART_MAIN);
    lv_obj_align(reasoning, LV_ALIGN_TOP_MID, 0, 48);

    lv_obj_t* minus = lv_label_create(parent);
    lv_label_set_text(minus, "-");
    lv_obj_set_style_text_font(minus, &lv_font_montserrat_28, LV_PART_MAIN);
    lv_obj_set_style_text_color(minus, lv_color_hex(KeyMuted), LV_PART_MAIN);
    lv_obj_set_pos(minus, 72, 90);

    lv_obj_t* plus = lv_label_create(parent);
    lv_label_set_text(plus, "+");
    lv_obj_set_style_text_font(plus, &lv_font_montserrat_28, LV_PART_MAIN);
    lv_obj_set_style_text_color(plus, lv_color_hex(KeyMuted), LV_PART_MAIN);
    lv_obj_set_pos(plus, 376, 90);

    _dial = lv_button_create(parent);
    lv_obj_set_pos(_dial, 0, 0);
    lv_obj_set_size(_dial, 466, 466);
    lv_obj_add_flag(_dial, LV_OBJ_FLAG_PRESS_LOCK);
    lv_obj_add_flag(_dial, LV_OBJ_FLAG_ADV_HITTEST);
    stylePanel(_dial, Background, Background, 14, 0);
    lv_obj_set_style_bg_opa(_dial, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(_dial, LV_OPA_TRANSP, PressedStyle);
    lv_obj_set_style_shadow_width(_dial, 0, LV_PART_MAIN);
    lv_obj_add_event_cb(_dial, dialEvent, LV_EVENT_PRESSED, this);
    lv_obj_add_event_cb(_dial, dialEvent, LV_EVENT_PRESSING, this);
    lv_obj_add_event_cb(_dial, dialEvent, LV_EVENT_RELEASED, this);
    lv_obj_add_event_cb(_dial, dialEvent, LV_EVENT_PRESS_LOST, this);
    lv_obj_add_event_cb(_dial, dialHitTestEvent, LV_EVENT_HIT_TEST, this);

    _dial_thumb = lv_obj_create(_dial);
    lv_obj_remove_flag(_dial_thumb, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(_dial_thumb, DialThumbWidth, DialThumbHeight);
    stylePanel(_dial_thumb, ArcThumb, ArcThumbBorder, 9, 2);
    lv_obj_set_style_shadow_width(_dial_thumb, 7, LV_PART_MAIN);
    lv_obj_set_style_shadow_opa(_dial_thumb, LV_OPA_20, LV_PART_MAIN);
    lv_obj_set_style_shadow_color(_dial_thumb, lv_color_hex(ArcThumbBorder), LV_PART_MAIN);
    lv_obj_set_style_transform_pivot_x(_dial_thumb, DialThumbWidth / 2, LV_PART_MAIN);
    lv_obj_set_style_transform_pivot_y(_dial_thumb, DialThumbHeight / 2, LV_PART_MAIN);

    for (int x : {15, 22, 29}) {
        lv_obj_t* grip = lv_obj_create(_dial_thumb);
        lv_obj_remove_flag(grip, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_pos(grip, x, 8);
        lv_obj_set_size(grip, 1, 14);
        stylePanel(grip, 0xEEF1EF, 0xEEF1EF, 1, 0);
        lv_obj_set_style_opa(grip, LV_OPA_60, LV_PART_MAIN);
    }

    resetDial();
    lv_obj_move_foreground(_dial);
}

void CodexMicroView::renderPage()
{
    for (std::size_t index = 0; index < _page_roots.size(); ++index) {
        if (index == static_cast<std::size_t>(_page)) {
            lv_obj_remove_flag(_page_roots[index], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(_page_roots[index], LV_OBJ_FLAG_HIDDEN);
        }
    }
    _page_dirty = true;
    lv_obj_move_foreground(_touch_control);
    if (_mic_screen != nullptr && _mic_active) {
        lv_obj_move_foreground(_mic_screen);
    }
    if (_pairing_screen != nullptr) {
        if (_page == Page::History) {
            lv_obj_add_flag(_pairing_screen, LV_OBJ_FLAG_HIDDEN);
        } else if (!_functional_enabled) {
            lv_obj_remove_flag(_pairing_screen, LV_OBJ_FLAG_HIDDEN);
            lv_obj_move_foreground(_pairing_screen);
        } else {
            lv_obj_add_flag(_pairing_screen, LV_OBJ_FLAG_HIDDEN);
        }
    }
    if (_offline_screen != nullptr && _page == Page::History) {
        lv_obj_add_flag(_offline_screen, LV_OBJ_FLAG_HIDDEN);
    }
    if (_page == Page::History) {
        refreshHistory(false);
    }
    if (_wake_overlay != nullptr && (_wake_overlay_armed || _display_power == DisplayPowerState::Locked)) {
        lv_obj_move_foreground(_wake_overlay);
    }
    ESP_LOGI(Tag, "page=%s", _page == Page::Command ? "command" : _page == Page::History ? "history" : "agent");
}

void CodexMicroView::setPage(Page page)
{
    if (_page == page) {
        return;
    }
    const int64_t started_at = esp_timer_get_time();
    releaseActiveInputs();
    _page = page;
    renderPage();
    ESP_LOGI(Tag, "page switch=%lldus", static_cast<long long>(esp_timer_get_time() - started_at));
}

void CodexMicroView::togglePage()
{
    if (_functional_enabled) {
        setPage(_page == Page::Command ? Page::History : _page == Page::History ? Page::Agent : Page::Command);
    } else {
        setPage(_page == Page::Command ? Page::History : Page::Command);
    }
}

bool CodexMicroView::ready() const
{
    return _root != nullptr && _page_roots[0] != nullptr && _page_roots[1] != nullptr;
}

bool CodexMicroView::functionalEnabled() const
{
    return _functional_enabled;
}

bool CodexMicroView::micActive() const
{
    return _mic_active;
}

bool CodexMicroView::locked() const
{
    return _locked;
}

bool CodexMicroView::lockForDebug()
{
    if (!ready() || interactionActive()) {
        return false;
    }
    lockDisplay();
    return _locked;
}

uint32_t CodexMicroView::lockRefreshCount() const
{
    return _lock_refresh_count;
}

CodexMicroView::Page CodexMicroView::currentPage() const
{
    return _page;
}

bool CodexMicroView::setPageForDebug(Page page)
{
    if (!ready() || (page == Page::Agent && !_functional_enabled)) {
        return false;
    }
    setMicActive(false);
    setPage(page);
    return _page == page;
}

bool CodexMicroView::showHistory(bool hourly)
{
    if (!ready()) {
        return false;
    }
    const HistoryMode requested = hourly ? HistoryMode::Hours : HistoryMode::Days;
    if (_history_mode != requested) {
        _history_mode     = requested;
        _history_selected = SIZE_MAX;
        _history_dirty    = true;
    }
    setMicActive(false);
    setPage(Page::History);
    refreshHistory(false);
    return _page == Page::History;
}

bool CodexMicroView::selectHistory(std::size_t index)
{
    const std::size_t count = _history_mode == HistoryMode::Hours ? HistoryHourCount : HistoryDayCount;
    if (!ready() || index >= count) {
        return false;
    }
    _history_selected = index;
    refreshHistory(false);
    updateHistorySelection();
    if (_history_grid != nullptr) {
        lv_obj_invalidate(_history_grid);
    }
    return selectedHistoryCell() != nullptr;
}

void CodexMicroView::historyDetails(char* out, std::size_t capacity) const
{
    if (out == nullptr || capacity == 0) {
        return;
    }
    const TokenHistoryCell* cell = selectedHistoryCell();
    if (cell == nullptr) {
        std::snprintf(out, capacity, "no history selection");
        return;
    }
    const char* quality = "missing";
    switch (cell->quality) {
        case TokenHistoryQuality::Official:
            quality = "official";
            break;
        case TokenHistoryQuality::Observed:
            quality = "observed";
            break;
        case TokenHistoryQuality::Partial:
            quality = "partial";
            break;
        case TokenHistoryQuality::Correction:
            quality = "correction";
            break;
        case TokenHistoryQuality::Missing:
            break;
    }
    if (cell->valid) {
        std::snprintf(out, capacity, "%s: %llu tokens (%s)", cell->label, static_cast<unsigned long long>(cell->tokens),
                      quality);
    } else {
        std::snprintf(out, capacity, "%s: %s", cell->label[0] ? cell->label : "Unavailable", quality);
    }
}

void CodexMicroView::setInputSuppressed(bool suppressed)
{
    if (_input_suppressed == suppressed) {
        return;
    }
    if (suppressed) {
        releaseActiveInputs();
    }
    _input_suppressed = suppressed;
}

void CodexMicroView::setDisplayPower(DisplayPowerState state)
{
    if (_display_power == state) {
        return;
    }

    _display_power       = state;
    const int brightness = state == DisplayPowerState::Locked ? LockedBrightness : _display_base_brightness;
    GetHAL().setBackLightBrightness(brightness, false);
    ESP_LOGI(Tag, "display power=%s brightness=%d", state == DisplayPowerState::Locked ? "locked" : "active",
             brightness);
}

void CodexMicroView::wakeDisplay()
{
    _last_activity_tick = lv_tick_get();
    if (_locked) {
        _locked = false;
        if (_lock_screen != nullptr) {
            lv_obj_add_flag(_lock_screen, LV_OBJ_FLAG_HIDDEN);
        }
        _page_dirty = true;
    }
    if (_wake_overlay != nullptr && !_wake_overlay_armed) {
        lv_obj_add_flag(_wake_overlay, LV_OBJ_FLAG_HIDDEN);
    }
    setDisplayPower(DisplayPowerState::Active);
}

void CodexMicroView::lockDisplay()
{
    if (_locked || interactionActive()) {
        return;
    }
    releaseActiveInputs();
    setMicActive(false);
    _locked                 = true;
    _lock_last_refresh_tick = 0;
    if (_lock_screen != nullptr) {
        lv_obj_remove_flag(_lock_screen, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(_lock_screen);
    }
    if (_wake_overlay != nullptr) {
        lv_obj_remove_flag(_wake_overlay, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(_wake_overlay);
    }
    setDisplayPower(DisplayPowerState::Locked);
    ESP_LOGI(Tag, "display locked after idle timeout");
}

void CodexMicroView::updateDisplayPower(uint32_t tick)
{
    if (_locked) {
        return;
    }
    if (interactionActive()) {
        _last_activity_tick = tick;
    } else if (tick - _last_activity_tick >= DisplayLockDelayMs) {
        lockDisplay();
    }
    if (_locked || _root == nullptr) {
        return;
    }
    const uint8_t shift = static_cast<uint8_t>((tick / PixelShiftPeriodMs) % PixelShiftOffsets.size());
    if (shift != _pixel_shift_index) {
        _pixel_shift_index = shift;
        lv_obj_align(_root, LV_ALIGN_CENTER, PixelShiftOffsets[shift][0], PixelShiftOffsets[shift][1]);
    }
}

void CodexMicroView::updateLockedScreen(const CodexMicroState& state, uint32_t tick, bool force)
{
    if (!force && _lock_last_refresh_tick != 0 && tick - _lock_last_refresh_tick < LockRefreshPeriodMs) {
        return;
    }
    _lock_last_refresh_tick = tick;
    ++_lock_refresh_count;
    const HostBridgeSnapshot host = GetHostBridge().snapshot(GetHAL().millis());
    char text[64]                 = {};
    if (!host.usageAvailable) {
        setLabelText(_lock_quota_label, "CODEX\n--");
    } else {
        std::snprintf(text, sizeof(text), "CODEX%s\n%u.%02u%%", host.usageStale ? " STALE" : "",
                      static_cast<unsigned>(host.remainingBasisPoints / 100U),
                      static_cast<unsigned>(host.remainingBasisPoints % 100U));
        setLabelText(_lock_quota_label, text);
    }
    std::snprintf(text, sizeof(text), "%s %u%%%s", batterySymbol(state.battery), static_cast<unsigned>(state.battery),
                  state.charging ? " +" : "");
    setLabelText(_lock_battery_label, text);
    if (_lock_screen != nullptr) {
        const uint8_t shift = static_cast<uint8_t>(_lock_refresh_count % PixelShiftOffsets.size());
        lv_obj_set_pos(_lock_screen, PixelShiftOffsets[shift][0], PixelShiftOffsets[shift][1]);
        lv_obj_move_foreground(_lock_screen);
    }
    if (_wake_overlay != nullptr) {
        lv_obj_move_foreground(_wake_overlay);
    }
}

void CodexMicroView::setMicActive(bool active)
{
    active = active && _functional_enabled;
    if (_mic_active == active || _mic_screen == nullptr) {
        return;
    }

    _mic_active = active;
    if (_mic_active) {
        _mic_last_update_tick = 0;
        lv_obj_remove_flag(_mic_screen, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(_mic_screen);
        updateMicMeter();
    } else {
        lv_obj_add_flag(_mic_screen, LV_OBJ_FLAG_HIDDEN);
        constexpr int MicMeterCenterY = 307;
        for (lv_obj_t* bar : _mic_bars) {
            if (bar != nullptr) {
                lv_obj_set_y(bar, MicMeterCenterY - 3);
                lv_obj_set_height(bar, 6);
                lv_obj_set_style_opa(bar, LV_OPA_30, LV_PART_MAIN);
            }
        }
        lv_obj_move_foreground(_touch_control);
    }
}

void CodexMicroView::updateConnection(const CodexMicroState& state)
{
    const bool functional = state.connected && state.protocolReady;
    if (functional != _functional_enabled) {
        if (functional) {
            _functional_enabled = true;
            if (_pairing_screen != nullptr) {
                lv_obj_add_flag(_pairing_screen, LV_OBJ_FLAG_HIDDEN);
            }
            if (_page != Page::History) setPage(Page::Command);
            ESP_LOGI(Tag, "Codex handshake complete; functional pages enabled");
        } else {
            releaseActiveInputs();
            _functional_enabled = false;
            setMicActive(false);
            if (_pairing_screen != nullptr) {
                if (_page != Page::History) {
                    lv_obj_remove_flag(_pairing_screen, LV_OBJ_FLAG_HIDDEN);
                    lv_obj_move_foreground(_pairing_screen);
                } else {
                    lv_obj_add_flag(_pairing_screen, LV_OBJ_FLAG_HIDDEN);
                }
            }
            ESP_LOGW(Tag, "%s; pairing screen active",
                     state.connected ? "waiting for Codex handshake" : "connection lost");
        }
    }

    if (!functional) {
        const std::size_t active_dot = static_cast<std::size_t>((lv_tick_get() / 350U) % _pairing_dots.size());
        for (std::size_t index = 0; index < _pairing_dots.size(); ++index) {
            if (_pairing_dots[index] != nullptr) {
                lv_obj_set_style_opa(_pairing_dots[index], index == active_dot ? LV_OPA_COVER : LV_OPA_30,
                                     LV_PART_MAIN);
            }
        }
        if (_pairing_core != nullptr) {
            lv_obj_set_style_opa(_pairing_core, active_dot == 1 ? LV_OPA_60 : LV_OPA_COVER, LV_PART_MAIN);
        }
        if (_pairing_pulse != nullptr) {
            lv_obj_set_style_shadow_opa(_pairing_pulse, active_dot == 1 ? LV_OPA_20 : LV_OPA_10, LV_PART_MAIN);
        }
    }
}

void CodexMicroView::updateAmbientLighting(const CodexMicroLight& light)
{
    if (light.effect == CodexMicroLightEffect::Off || light.color == 0 || light.brightness <= 0.01f) {
        if (_ambient_visible) {
            for (lv_obj_t* layer : _ambient_layers) {
                lv_obj_set_style_arc_opa(layer, LV_OPA_TRANSP, LV_PART_INDICATOR);
            }
            _ambient_visible = false;
        }
        return;
    }

    const uint8_t base_brightness =
        static_cast<uint8_t>(std::lround(std::clamp(light.brightness, 0.0f, 1.0f) * 255.0f));
    if (!_ambient_visible || light.color != _ambient_base_color || base_brightness != _ambient_base_brightness) {
        const float brightness = static_cast<float>(base_brightness) / 255.0f;
        const uint32_t color   = softenedAmbientColor(light.color, brightness);
        for (std::size_t index = 0; index < _ambient_layers.size(); ++index) {
            lv_obj_set_style_arc_color(_ambient_layers[index], lv_color_hex(color), LV_PART_INDICATOR);
            lv_obj_set_style_arc_opa(_ambient_layers[index], AmbientLayerOpacity[index], LV_PART_INDICATOR);
        }
        _ambient_base_color      = light.color;
        _ambient_base_brightness = base_brightness;
        _ambient_visible         = true;
    }
}

void CodexMicroView::updateCommandLighting(const CodexMicroState& state)
{
    const CodexMicroLight& light = state.keys;
    const bool enabled     = light.effect != CodexMicroLightEffect::Off && light.color != 0 && light.brightness > 0.01f;
    const float seconds    = static_cast<float>(lv_tick_get()) / 1000.0f;
    const float brightness = animatedBrightness(light, seconds);
    const uint32_t color   = enabled ? scaledColor(light.color, brightness) : CommandBezel;
    int snake_slot         = -1;
    if (enabled && light.effect == CodexMicroLightEffect::Snake) {
        const float speed = light.speed > 0.05f ? light.speed : 0.5f;
        snake_slot = static_cast<int>(seconds * (2.0f + 6.0f * speed)) % static_cast<int>(_command_buttons.size());
    }

    for (std::size_t i = 0; i < _command_buttons.size(); ++i) {
        const bool lit = enabled && (snake_slot < 0 || static_cast<int>(i) == snake_slot);
        if (!_page_dirty && _command_lit[i] == lit && _command_light_colors[i] == color) {
            continue;
        }
        _command_lit[i]          = lit;
        _command_light_colors[i] = color;
        if (_command_buttons[i] != nullptr) {
            lv_obj_set_style_outline_color(_command_buttons[i], lv_color_hex(lit ? color : CommandBezel), LV_PART_MAIN);
            lv_obj_set_style_outline_width(_command_buttons[i], lit ? 3 : 2, LV_PART_MAIN);
            lv_obj_set_style_shadow_color(_command_buttons[i], lv_color_hex(lit ? color : 0x000000), LV_PART_MAIN);
            lv_obj_set_style_shadow_opa(_command_buttons[i], lit ? LV_OPA_40 : LV_OPA_30, LV_PART_MAIN);
        }
    }
    _command_last_update_tick = lv_tick_get();
}

void CodexMicroView::updateCenterStatus(const CodexMicroState& state)
{
    const uint32_t now            = GetHAL().millis();
    const HostBridgeSnapshot host = GetHostBridge().snapshot(now);
    if (!_page_dirty && host.revision == _last_host_bridge_revision && state.battery == _last_status_battery &&
        state.charging == _last_status_charging && now - _center_status_last_update_tick < 1000U) {
        return;
    }
    _last_host_bridge_revision      = host.revision;
    _last_status_battery            = state.battery;
    _last_status_charging           = state.charging;
    _center_status_last_update_tick = now;
    char text[48]                   = {};

    if (!host.usageAvailable) {
        setLabelText(_usage_status_label, "CODEX");
        setLabelText(_usage_value_label, "--");
        setLabelText(_usage_reset_label, "RESET --");
        if (_usage_bar != nullptr) {
            lv_bar_set_value(_usage_bar, 0, LV_ANIM_OFF);
        }
        if (_usage_status_label != nullptr) {
            lv_obj_set_style_text_color(_usage_status_label, lv_color_hex(KeyMuted), LV_PART_MAIN);
        }
    } else {
        setLabelText(_usage_status_label, host.usageStale ? "CODEX STALE" : "CODEX");
        if (_usage_status_label != nullptr) {
            lv_obj_set_style_text_color(_usage_status_label, lv_color_hex(host.usageStale ? StatusStale : KeyMuted),
                                        LV_PART_MAIN);
        }
        std::snprintf(text, sizeof(text), "%u.%02u%%", static_cast<unsigned>(host.remainingBasisPoints / 100U),
                      static_cast<unsigned>(host.remainingBasisPoints % 100U));
        setLabelText(_usage_value_label, text);
        if (host.resetAvailable) {
            formatResetCountdown(text, sizeof(text), host.resetSeconds);
            setLabelText(_usage_reset_label, text);
        } else {
            setLabelText(_usage_reset_label, "RESET --");
        }
        if (_usage_bar != nullptr) {
            lv_bar_set_value(_usage_bar, host.remainingBasisPoints, LV_ANIM_OFF);
        }
    }

    if (state.charging) {
        std::snprintf(text, sizeof(text), "%s %u%% %s", batterySymbol(state.battery),
                      static_cast<unsigned>(state.battery), LV_SYMBOL_CHARGE);
    } else {
        std::snprintf(text, sizeof(text), "%s %u%%", batterySymbol(state.battery),
                      static_cast<unsigned>(state.battery));
    }
    setLabelText(_battery_label, text);
    if (_battery_label != nullptr) {
        const uint32_t battery_color = state.charging ? Green : (state.battery < 15 ? BatteryLow : Text);
        lv_obj_set_style_text_color(_battery_label, lv_color_hex(battery_color), LV_PART_MAIN);
    }
}

void CodexMicroView::updateAgentLights(const CodexMicroState& state)
{
    const float seconds = static_cast<float>(lv_tick_get()) / 1000.0f;
    for (std::size_t i = 0; i < state.threads.size(); ++i) {
        const CodexMicroLight& light = state.threads[i];
        const float brightness       = animatedBrightness(light, seconds);
        const bool assigned          = lightAssigned(light);
        const bool visible           = assigned && light.brightness > 0.01f;
        const uint32_t color         = visible ? scaledColor(light.color, brightness) : AgentOff;
        if (_agent_dots[i] != nullptr) {
            stylePanel(_agent_dots[i], color, visible ? color : 0x979E9A, LV_RADIUS_CIRCLE, 1);
            lv_obj_set_style_shadow_width(_agent_dots[i], visible ? 4 : 0, LV_PART_MAIN);
            lv_obj_set_style_shadow_opa(_agent_dots[i], visible ? LV_OPA_20 : LV_OPA_TRANSP, LV_PART_MAIN);
            lv_obj_set_style_shadow_color(_agent_dots[i], lv_color_hex(color), LV_PART_MAIN);
        }
        if (_agent_labels[i] != nullptr) {
            const char* label = lightStatus(light);
            if (std::strcmp(lv_label_get_text(_agent_labels[i]), label) != 0) {
                lv_label_set_text(_agent_labels[i], label);
            }
        }
    }
    _agent_last_update_tick = lv_tick_get();
}

bool CodexMicroView::interactionActive() const
{
    const auto key_active     = [](const KeyContext& context) { return context.active; };
    const auto command_active = [](const CommandContext& context) { return context.active; };
    return GetHAL().btnA.isPressed() || GetHAL().btnB.isPressed() ||
           (GetHAL().lvTouchpad != nullptr && lv_indev_get_state(GetHAL().lvTouchpad) == LV_INDEV_STATE_PRESSED) ||
           _dial_pressed || _touch_pressed || _mic_active || _wake_overlay_armed ||
           std::any_of(_command_contexts.begin(), _command_contexts.end(), command_active) ||
           std::any_of(_agent_contexts.begin(), _agent_contexts.end(), key_active);
}

void CodexMicroView::invalidateCommandButton(std::size_t slot)
{
    if (slot >= _command_buttons.size() || _command_buttons[slot] == nullptr) {
        return;
    }
    lv_obj_invalidate(_command_buttons[slot]);
}

void CodexMicroView::updateMicMeter()
{
    if (!_mic_active) {
        return;
    }
    const uint32_t tick = lv_tick_get();
    if (_mic_last_update_tick != 0 && tick - _mic_last_update_tick < 120U) {
        return;
    }
    _mic_last_update_tick = tick;

    constexpr float Center        = 4.0f;
    constexpr int MicMeterCenterY = 307;
    const float phase             = static_cast<float>(tick % 1440U) / 180.0f;
    for (std::size_t index = 0; index < _mic_bars.size(); ++index) {
        lv_obj_t* bar = _mic_bars[index];
        if (bar == nullptr) {
            continue;
        }
        const float falloff = std::fabs(static_cast<float>(index) - Center) / Center;
        const float pulse   = 0.5f + 0.5f * std::sin(phase - static_cast<float>(index) * 0.68f);
        const float amount  = std::clamp((0.28f + pulse * 0.72f) * (1.0f - falloff * 0.28f), 0.0f, 1.0f);
        const int height    = std::max(6, static_cast<int>(std::lround(54.0f * amount)));
        lv_obj_set_y(bar, MicMeterCenterY - height / 2);
        lv_obj_set_height(bar, height);
        lv_obj_set_style_opa(bar, static_cast<lv_opa_t>(76 + amount * 179.0f), LV_PART_MAIN);
    }
}

void CodexMicroView::update(const CodexMicroState& state)
{
    const uint32_t tick = lv_tick_get();
    updateDisplayPower(tick);
    if (_locked) {
        updateLockedScreen(state, tick, false);
        return;
    }
    if (_page == Page::History) {
        refreshHistory(false);
        if (tick - _history_hint_tick >= 1000) {
            updateHistorySelection();
            _history_hint_tick = tick;
        }
        if (_pairing_screen != nullptr) {
            lv_obj_add_flag(_pairing_screen, LV_OBJ_FLAG_HIDDEN);
        }
    }
    if (GetNetworkQuota().configured() && !(state.connected && state.protocolReady) && _page != Page::History) {
        if (!_offline_screen) {
            _offline_screen = lv_obj_create(_root);
            lv_obj_set_size(_offline_screen, 466, 466);
            lv_obj_center(_offline_screen);
            stylePanel(_offline_screen, Background, Background, 0, 0);
            _offline_label = lv_label_create(_offline_screen);
            lv_obj_set_width(_offline_label, 350);
            lv_obj_set_style_text_font(_offline_label, &lv_font_montserrat_28, 0);
            lv_obj_set_style_text_color(_offline_label, lv_color_hex(Green), 0);
            lv_obj_set_style_text_align(_offline_label, LV_TEXT_ALIGN_CENTER, 0);
            lv_obj_center(_offline_label);
        }
        lv_obj_remove_flag(_offline_screen, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(_offline_screen);
        if (tick - _offline_update_tick >= 1000 || !_offline_update_tick) {
            const auto quota = GetHostBridge().snapshot(GetHAL().millis());
            char text[160]{};
            if (quota.usageAvailable)
                std::snprintf(text, sizeof(text), "CODEX QUOTA\n\n%u%% remaining\n%s\n\nBattery %u%%",
                              quota.remainingBasisPoints / 100, quota.usageStale ? "Stale" : "Wi-Fi",
                              GetHAL().getBatteryLevel());
            else
                std::snprintf(text, sizeof(text), "CODEX QUOTA\n\n%s\n\nBattery %u%%",
                              GetNetworkQuota().connected() ? "Waiting for server" : "Connecting Wi-Fi",
                              GetHAL().getBatteryLevel());
            setLabelText(_offline_label, text);
            _offline_update_tick = tick;
        }
        return;
    }
    if (_offline_screen) lv_obj_add_flag(_offline_screen, LV_OBJ_FLAG_HIDDEN);
    updateDialReturn();
    updateMicMeter();
    const bool state_changed      = state.revision != _last_state_revision;
    const bool host_ready         = state.connected && state.protocolReady;
    const int8_t connection_phase = !host_ready ? static_cast<int8_t>((tick / 350U) % 3U) : 3;
    if (state_changed || connection_phase != _last_connection_phase) {
        updateConnection(state);
        _last_connection_phase = connection_phase;
    }

    const bool input_active = interactionActive();
    if (!sameLight(state.ambient, _ambient_light)) {
        _ambient_light = state.ambient;
        updateAmbientLighting(_ambient_light);
    }

    if (_dial_host_press_active && static_cast<int32_t>(lv_tick_get() - _dial_release_tick) >= 0) {
        GetCodexMicroBle().sendKey(CodexMicroControl::EncoderPress, CodexMicroKeyAction::Release);
        _dial_host_press_active = false;
    }

    if (_page == Page::Command) {
        updateCenterStatus(state);
        const bool keys_animated    = state.keys.effect == CodexMicroLightEffect::Breath ||
                                      state.keys.effect == CodexMicroLightEffect::ShallowBreath ||
                                      state.keys.effect == CodexMicroLightEffect::Snake;
        const bool keys_refresh_due = tick - _command_last_update_tick >= AnimatedLightingRefreshPeriodMs;
        if (state_changed || _page_dirty || (keys_animated && keys_refresh_due && !input_active)) {
            updateCommandLighting(state);
        }
    } else if (_page == Page::Agent) {
        const bool threads_animated =
            std::any_of(state.threads.begin(), state.threads.end(), [](const CodexMicroLight& light) {
                return light.effect == CodexMicroLightEffect::Breath ||
                       light.effect == CodexMicroLightEffect::ShallowBreath;
            });
        const bool threads_refresh_due = tick - _agent_last_update_tick >= AnimatedLightingRefreshPeriodMs;
        if (state_changed || _page_dirty || (threads_animated && threads_refresh_due && !input_active)) {
            updateAgentLights(state);
        }
    }
    _page_dirty          = false;
    _last_state_revision = state.revision;
}

void CodexMicroView::releaseActiveInputs()
{
    for (CommandContext& context : _command_contexts) {
        if (context.active) {
            if (context.action == CommandAction::Plan) {
                GetCodexMicroBle().sendJoystickButton(0.75f, false);
            } else {
                GetCodexMicroBle().sendKey(context.control, CodexMicroKeyAction::Release);
            }
        }
        context.active = false;
    }
    for (KeyContext& context : _agent_contexts) {
        if (context.active) {
            GetCodexMicroBle().sendKey(context.control, CodexMicroKeyAction::Release, context.agent);
        }
        context.active = false;
    }
    if (_page_roots[static_cast<std::size_t>(Page::Command)] != nullptr) {
        lv_obj_invalidate(_page_roots[static_cast<std::size_t>(Page::Command)]);
    }
    if (_dial_host_press_active) {
        GetCodexMicroBle().sendKey(CodexMicroControl::EncoderPress, CodexMicroKeyAction::Release);
        _dial_host_press_active = false;
    }
    _touch_pressed = false;
    resetDial();
}

void CodexMicroView::historyGridEvent(lv_event_t* event)
{
    auto* owner = static_cast<CodexMicroView*>(lv_event_get_user_data(event));
    if (owner == nullptr || owner->_history_grid == nullptr) {
        return;
    }
    if (lv_event_get_code(event) == LV_EVENT_DRAW_MAIN) {
        lv_layer_t* layer = lv_event_get_layer(event);
        if (layer == nullptr) {
            return;
        }
        lv_area_t grid_area;
        lv_obj_get_coords(owner->_history_grid, &grid_area);
        const bool hourly       = owner->_history_mode == HistoryMode::Hours;
        const std::size_t count = hourly ? HistoryHourCount : HistoryDayCount;
        constexpr int Width     = 48;
        constexpr int PitchX    = 56;
        constexpr int PitchY    = 38;
        for (std::size_t index = 0; index < count; ++index) {
            const int row = static_cast<int>(index / 6U);
            const int col = static_cast<int>(index % 6U);
            lv_draw_rect_dsc_t draw;
            lv_draw_rect_dsc_init(&draw);
            draw.bg_color     = lv_color_hex(owner->_history_colors[index]);
            draw.bg_opa       = LV_OPA_COVER;
            draw.border_color = lv_color_hex(index == owner->_history_selected ? Text : owner->_history_borders[index]);
            draw.border_width = index == owner->_history_selected ? 2 : 1;
            draw.radius       = 3;
            const lv_area_t area = {static_cast<lv_coord_t>(grid_area.x1 + col * PitchX),
                                    static_cast<lv_coord_t>(grid_area.y1 + row * PitchY),
                                    static_cast<lv_coord_t>(grid_area.x1 + col * PitchX + Width - 1),
                                    static_cast<lv_coord_t>(grid_area.y1 + row * PitchY + 25)};
            lv_draw_rect(layer, &draw, &area);
            if (owner->_history_snapshot != nullptr) {
                const TokenHistoryCell& cell =
                    hourly ? owner->_history_snapshot->hours[index] : owner->_history_snapshot->days[index];
                const char* label = cell.label;
                if (hourly && std::strlen(label) >= 16) label += 11;
                if (!hourly && std::strlen(label) >= 10) label += 5;
                lv_draw_label_dsc_t text;
                lv_draw_label_dsc_init(&text);
                text.font            = &lv_font_montserrat_14;
                const uint32_t color = owner->_history_colors[index];
                text.color           = lv_color_hex(
                    ((color >> 16U) & 0xFFU) + ((color >> 8U) & 0xFFU) + (color & 0xFFU) > 384U ? KeyInk : Text);
                text.opa             = LV_OPA_COVER;
                text.text            = label;
                text.text_local      = 1;
                text.align           = LV_TEXT_ALIGN_CENTER;
                lv_area_t label_area = area;
                label_area.y1 += 5;
                lv_draw_label(layer, &text, &label_area);
            }
        }
        return;
    }
    if (lv_event_get_code(event) != LV_EVENT_CLICKED || owner->_input_suppressed) {
        return;
    }
    lv_indev_t* indev = lv_event_get_indev(event);
    if (indev == nullptr) {
        return;
    }
    lv_point_t point;
    lv_indev_get_point(indev, &point);
    lv_area_t grid_area;
    lv_obj_get_coords(owner->_history_grid, &grid_area);
    const int x          = point.x - grid_area.x1;
    const int y          = point.y - grid_area.y1;
    const bool hourly    = owner->_history_mode == HistoryMode::Hours;
    constexpr int Width  = 48;
    constexpr int PitchX = 56;
    constexpr int PitchY = 38;
    if (x < 0 || y < 0 || x >= 340 || y >= 190 || x % PitchX >= Width || y % PitchY >= 26) {
        return;
    }
    const int col = x / PitchX;
    const int row = y / PitchY;
    if (col >= 6) return;
    const std::size_t index = static_cast<std::size_t>(row * 6 + col);
    const std::size_t count = hourly ? HistoryHourCount : HistoryDayCount;
    if (index >= count) {
        return;
    }
    owner->wakeDisplay();
    owner->selectHistory(index);
}

void CodexMicroView::historyModeEvent(lv_event_t* event)
{
    auto* context = static_cast<HistoryCellContext*>(lv_event_get_user_data(event));
    if (context == nullptr || context->owner == nullptr || context->owner->_input_suppressed ||
        lv_event_get_code(event) != LV_EVENT_CLICKED) {
        return;
    }
    context->owner->showHistory(context->index == static_cast<std::size_t>(HistoryMode::Hours));
}

void CodexMicroView::keyEvent(lv_event_t* event)
{
    auto* context = static_cast<KeyContext*>(lv_event_get_user_data(event));
    if (context == nullptr || context->owner == nullptr) {
        return;
    }
    if (context->owner->_input_suppressed) {
        return;
    }

    const lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_PRESSED) {
        context->owner->wakeDisplay();
    }
    if (code == LV_EVENT_PRESSED && !context->owner->_functional_enabled) {
        return;
    }
    if (code == LV_EVENT_PRESSED && !context->active) {
        context->active = true;
        playFeedback(context->control, context->agent);
        GetCodexMicroBle().sendKey(context->control, CodexMicroKeyAction::Press, context->agent);
    } else if ((code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) && context->active) {
        GetCodexMicroBle().sendKey(context->control, CodexMicroKeyAction::Release, context->agent);
        context->active = false;
    }
}

void CodexMicroView::commandEvent(lv_event_t* event)
{
    auto* context = static_cast<CommandContext*>(lv_event_get_user_data(event));
    if (context == nullptr || context->owner == nullptr || context->owner->_input_suppressed) {
        return;
    }

    const lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_PRESSED) {
        context->owner->wakeDisplay();
    }
    if (code == LV_EVENT_PRESSED && !context->owner->_functional_enabled) {
        return;
    }
    if (code == LV_EVENT_PRESSED && !context->active) {
        bool sent = false;
        if (context->action == CommandAction::Plan) {
            sent = GetCodexMicroBle().sendJoystickButton(0.75f, true);
            playFeedback(CodexMicroControl::EncoderPress);
        } else {
            sent = GetCodexMicroBle().sendKey(context->control, CodexMicroKeyAction::Press);
            playFeedback(context->control);
        }
        context->active = sent;
    } else if ((code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) && context->active) {
        if (context->action == CommandAction::Plan) {
            GetCodexMicroBle().sendJoystickButton(0.75f, false);
        } else {
            GetCodexMicroBle().sendKey(context->control, CodexMicroKeyAction::Release);
        }
        context->active = false;
    }
    context->owner->invalidateCommandButton(context->slot);
}

void CodexMicroView::iconEvent(lv_event_t* event)
{
    auto* context = static_cast<IconContext*>(lv_event_get_user_data(event));
    if (context == nullptr || context->owner == nullptr || lv_event_get_code(event) != LV_EVENT_DRAW_MAIN) {
        return;
    }

    lv_obj_t* object  = lv_event_get_target_obj(event);
    lv_layer_t* layer = lv_event_get_layer(event);
    lv_area_t coords;
    lv_obj_get_coords(object, &coords);
    const int cx         = (coords.x1 + coords.x2) / 2;
    const int cy         = (coords.y1 + coords.y2) / 2;
    const uint32_t color = context->icon == Icon::Fingerprint
                               ? (lv_obj_has_state(object, LV_STATE_PRESSED) ? Green : Fingerprint)
                               : KeyInk;

    switch (context->icon) {
        case Icon::Fast:
            drawLine(layer, cx + 4, cy - 24, cx - 12, cy - 2, 3, color);
            drawLine(layer, cx - 12, cy - 2, cx - 1, cy - 2, 3, color);
            drawLine(layer, cx - 1, cy - 2, cx - 5, cy + 24, 3, color);
            drawLine(layer, cx - 5, cy + 24, cx + 14, cy - 5, 3, color);
            drawLine(layer, cx + 14, cy - 5, cx + 3, cy - 5, 3, color);
            drawLine(layer, cx + 3, cy - 5, cx + 4, cy - 24, 3, color);
            break;
        case Icon::Approve:
            drawArc(layer, cx, cy, 23, 0, 360, 3, color);
            drawLine(layer, cx - 12, cy, cx - 3, cy + 9, 3, color);
            drawLine(layer, cx - 3, cy + 9, cx + 14, cy - 11, 3, color);
            break;
        case Icon::Decline:
            drawArc(layer, cx, cy, 23, 0, 360, 3, color);
            drawLine(layer, cx - 10, cy - 10, cx + 10, cy + 10, 3, color);
            drawLine(layer, cx + 10, cy - 10, cx - 10, cy + 10, 3, color);
            break;
        case Icon::NewChat:
            drawLine(layer, cx - 23, cy, cx - 12, cy, 3, color);
            drawLine(layer, cx - 12, cy, cx + 8, cy - 15, 3, color);
            drawLine(layer, cx - 12, cy, cx + 8, cy + 15, 3, color);
            drawLine(layer, cx + 8, cy - 15, cx + 20, cy - 15, 3, color);
            drawLine(layer, cx + 8, cy + 15, cx + 20, cy + 15, 3, color);
            drawLine(layer, cx + 20, cy - 15, cx + 14, cy - 21, 3, color);
            drawLine(layer, cx + 20, cy - 15, cx + 14, cy - 9, 3, color);
            drawLine(layer, cx + 20, cy + 15, cx + 14, cy + 9, 3, color);
            drawLine(layer, cx + 20, cy + 15, cx + 14, cy + 21, 3, color);
            break;
        case Icon::Mic:
            drawOutline(layer, cx - 10, cy - 25, cx + 10, cy + 8, 10, 3, color);
            drawArc(layer, cx, cy + 4, 18, 0, 180, 3, color);
            drawLine(layer, cx, cy + 22, cx, cy + 29, 3, color);
            drawLine(layer, cx - 11, cy + 29, cx + 11, cy + 29, 3, color);
            break;
        case Icon::Fingerprint:
            drawArc(layer, cx, cy + 9, 20, 195, 345, 2, color);
            drawArc(layer, cx, cy + 9, 15, 190, 20, 2, color);
            drawArc(layer, cx, cy + 9, 10, 175, 45, 2, color);
            drawArc(layer, cx, cy + 9, 5, 160, 55, 2, color);
            drawLine(layer, cx - 18, cy + 9, cx - 20, cy + 20, 2, color);
            drawLine(layer, cx + 9, cy + 11, cx + 5, cy + 25, 2, color);
            break;
    }
}

void CodexMicroView::dialTrackEvent(lv_event_t* event)
{
    if (lv_event_get_code(event) != LV_EVENT_DRAW_MAIN) {
        return;
    }
    lv_obj_t* object  = lv_event_get_target_obj(event);
    lv_layer_t* layer = lv_event_get_layer(event);
    lv_area_t coords;
    lv_obj_get_coords(object, &coords);
    const int center_x = coords.x1 + DisplayCenter;
    const int center_y = coords.y1 + DisplayCenter;

    drawArc(layer, center_x, center_y, DialRadius, DialStartDegrees, DialEndDegrees, 8, 0x000000);
    drawArc(layer, center_x, center_y, DialRadius, DialStartDegrees, DialEndDegrees, 4, ArcTrack);
}

void CodexMicroView::dialHitTestEvent(lv_event_t* event)
{
    auto* owner              = static_cast<CodexMicroView*>(lv_event_get_user_data(event));
    lv_hit_test_info_t* info = lv_event_get_hit_test_info(event);
    if (owner == nullptr || owner->_dial_thumb == nullptr || info == nullptr || info->point == nullptr) {
        return;
    }
    if (owner->_dial_returning) {
        info->res = false;
        return;
    }
    lv_area_t coords;
    lv_obj_get_coords(owner->_dial_thumb, &coords);
    const int dx     = info->point->x - ((coords.x1 + coords.x2) / 2);
    const int dy     = info->point->y - ((coords.y1 + coords.y2) / 2);
    const int radius = owner->_dial_pressed ? DialPressRadius : DialIdlePressRadius;
    info->res        = dx * dx + dy * dy <= radius * radius;
}

void CodexMicroView::setDialVisualStep(float step)
{
    if (_dial == nullptr || _dial_thumb == nullptr) {
        return;
    }
    _dial_visual_step = std::clamp(step, 0.0f, static_cast<float>(DialStepCount));
    const float degrees =
        static_cast<float>(DialStartDegrees) +
        static_cast<float>(DialEndDegrees - DialStartDegrees) * (_dial_visual_step / static_cast<float>(DialStepCount));
    const float radians = degrees * 0.01745329252f;
    const int center_x  = DisplayCenter + static_cast<int>(std::lround(std::cos(radians) * DialRadius));
    const int center_y  = DisplayCenter + static_cast<int>(std::lround(std::sin(radians) * DialRadius));
    lv_obj_set_pos(_dial_thumb, center_x - DialThumbWidth / 2, center_y - DialThumbHeight / 2);

    float rotation = std::fmod(degrees + 90.0f, 360.0f);
    if (rotation < 0.0f) {
        rotation += 360.0f;
    }
    lv_obj_set_style_transform_rotation(_dial_thumb, static_cast<int>(std::lround(rotation * 10.0f)), LV_PART_MAIN);
}

void CodexMicroView::updateDialFromPoint(const lv_point_t& point)
{
    if (_root == nullptr) {
        return;
    }
    lv_area_t root_coords;
    lv_obj_get_coords(_root, &root_coords);
    const float dx    = static_cast<float>(point.x - (root_coords.x1 + DisplayCenter));
    const float dy    = static_cast<float>(point.y - (root_coords.y1 + DisplayCenter));
    float raw_degrees = std::atan2(dy, dx) * 57.295779513f;
    if (raw_degrees < 0.0f) {
        raw_degrees += 360.0f;
    }

    const float current_degrees =
        static_cast<float>(DialStartDegrees) +
        static_cast<float>(DialEndDegrees - DialStartDegrees) * (_dial_visual_step / static_cast<float>(DialStepCount));
    float unwrapped = raw_degrees;
    for (float candidate : {raw_degrees - 360.0f, raw_degrees, raw_degrees + 360.0f}) {
        if (std::fabs(candidate - current_degrees) < std::fabs(unwrapped - current_degrees)) {
            unwrapped = candidate;
        }
    }
    const float clamped_degrees =
        std::clamp(unwrapped, static_cast<float>(DialStartDegrees), static_cast<float>(DialEndDegrees));
    const int desired_step =
        static_cast<int>(std::lround((clamped_degrees - static_cast<float>(DialStartDegrees)) /
                                     static_cast<float>(DialEndDegrees - DialStartDegrees) * DialStepCount));
    if (desired_step == _dial_step) {
        return;
    }

    _dial_rotating       = true;
    const int step_delta = std::clamp(desired_step - _dial_step, -static_cast<int>(CodexMicroMaxEncoderBatchSteps),
                                      static_cast<int>(CodexMicroMaxEncoderBatchSteps));
    // Moving right increases reasoning effort, but the host's configured
    // reasoning dial maps that direction to ENC_CC. Moving left maps to ENC_CW.
    const int direction     = step_delta > 0 ? -1 : 1;
    const int emitted_steps = std::abs(step_delta);
    if (emitted_steps > 0) {
        // Queue the batch so a fast drag never performs a burst of HID writes
        // inside LVGL's touch callback. The worker still emits every official
        // encoder detent in order.
        const bool queued = GetCodexMicroBle().sendEncoderSteps(direction, static_cast<uint16_t>(emitted_steps));
        if (!queued) {
            return;
        }
        _dial_step += step_delta;
        const uint32_t tick = lv_tick_get();
        if (_dial_last_feedback_tick == 0 || tick - _dial_last_feedback_tick >= DialFeedbackPeriodMs) {
            playDialRatchetFeedback(direction);
            _dial_last_feedback_tick = tick;
        }
        ESP_LOGD(Tag, "arc-slider rotate direction=%d steps=%d desired=%d", direction, emitted_steps, desired_step);
    }
    setDialVisualStep(static_cast<float>(_dial_step));
}

void CodexMicroView::beginDialReturn()
{
    if (std::fabs(_dial_visual_step - static_cast<float>(DialCenterStep)) < 0.01f) {
        resetDial();
        return;
    }
    _dial_returning         = true;
    _dial_return_start_step = _dial_visual_step;
    _dial_return_tick       = lv_tick_get();
    if (_dial_thumb != nullptr) {
        stylePanel(_dial_thumb, ArcThumbReturning, ArcThumbReturningBorder, 9, 2);
    }
}

void CodexMicroView::updateDialReturn()
{
    if (!_dial_returning) {
        return;
    }
    const uint32_t elapsed = lv_tick_get() - _dial_return_tick;
    const float progress   = std::min(1.0f, static_cast<float>(elapsed) / static_cast<float>(DialReturnDurationMs));
    const float remaining  = 1.0f - progress;
    const float eased      = 1.0f - remaining * remaining * remaining;
    const float step = _dial_return_start_step + (static_cast<float>(DialCenterStep) - _dial_return_start_step) * eased;
    setDialVisualStep(step);
    if (progress >= 1.0f) {
        resetDial();
    }
}

void CodexMicroView::resetDial()
{
    _dial_pressed            = false;
    _dial_rotating           = false;
    _dial_returning          = false;
    _dial_step               = DialCenterStep;
    _dial_last_feedback_tick = 0;
    setDialVisualStep(static_cast<float>(DialCenterStep));
    if (_dial_thumb != nullptr) {
        stylePanel(_dial_thumb, ArcThumb, ArcThumbBorder, 9, 2);
    }
}

void CodexMicroView::releaseDialGesture()
{
    if (!_dial_pressed) {
        return;
    }
    const uint32_t held = lv_tick_get() - _dial_press_tick;
    if (!_dial_rotating) {
        if (_dial_host_press_active) {
            GetCodexMicroBle().sendKey(CodexMicroControl::EncoderPress, CodexMicroKeyAction::Release);
            _dial_host_press_active = false;
        }
        if (GetCodexMicroBle().sendKey(CodexMicroControl::EncoderPress, CodexMicroKeyAction::Press)) {
            _dial_host_press_active = true;
            _dial_release_tick      = lv_tick_get() + (held >= 500U ? 520U : 45U);
        }
        ESP_LOGI(Tag, "arc-slider %s gesture=%ums", held >= 500U ? "settings-hold" : "press",
                 static_cast<unsigned>(held));
    }
    _dial_pressed  = false;
    _dial_rotating = false;
    beginDialReturn();
}

void CodexMicroView::dialEvent(lv_event_t* event)
{
    auto* owner = static_cast<CodexMicroView*>(lv_event_get_user_data(event));
    if (owner == nullptr) {
        return;
    }
    if (owner->_input_suppressed) {
        return;
    }
    const lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_PRESSED) {
        owner->wakeDisplay();
    }
    if (!owner->_functional_enabled) {
        return;
    }
    if (code == LV_EVENT_PRESSED) {
        owner->_dial_returning  = false;
        owner->_dial_step       = static_cast<int>(std::lround(owner->_dial_visual_step));
        owner->_dial_pressed    = true;
        owner->_dial_rotating   = false;
        owner->_dial_press_tick = lv_tick_get();
        if (owner->_dial_thumb != nullptr) {
            owner->stylePanel(owner->_dial_thumb, ArcThumbActive, ArcThumbActiveBorder, 9, 2);
        }
        playFeedback(CodexMicroControl::EncoderPress);
    } else if (code == LV_EVENT_PRESSING && owner->_dial_pressed) {
        lv_indev_t* indev = lv_event_get_indev(event);
        if (indev == nullptr) {
            return;
        }
        lv_point_t point;
        lv_indev_get_point(indev, &point);
        owner->updateDialFromPoint(point);
    } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        owner->releaseDialGesture();
    }
}

void CodexMicroView::touchEvent(lv_event_t* event)
{
    auto* owner = static_cast<CodexMicroView*>(lv_event_get_user_data(event));
    if (owner == nullptr) {
        return;
    }
    if (owner->_input_suppressed) {
        return;
    }
    const lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_PRESSED) {
        owner->wakeDisplay();
    }
    const bool pairing_reset_event = lv_event_get_target_obj(event) == owner->_pairing_reset_control;
    if (!owner->_functional_enabled && !pairing_reset_event) {
        return;
    }
    if (code == LV_EVENT_PRESSED) {
        owner->_touch_pressed    = true;
        owner->_touch_press_tick = lv_tick_get();
        playFeedback(CodexMicroControl::EncoderPress);
    } else if ((code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) && owner->_touch_pressed) {
        const uint32_t held   = lv_tick_get() - owner->_touch_press_tick;
        owner->_touch_pressed = false;
        if (held >= 3000U) {
            const bool requested = GetCodexMicroBle().resetPairing();
            ESP_LOGW(Tag, "touch sensor pairing-reset duration=%ums requested=%d", static_cast<unsigned>(held),
                     requested ? 1 : 0);
        } else {
            ESP_LOGI(Tag, "touch sensor tap duration=%ums (single BLE channel)", static_cast<unsigned>(held));
        }
    }
}

void CodexMicroView::wakeOverlayEvent(lv_event_t* event)
{
    auto* owner = static_cast<CodexMicroView*>(lv_event_get_user_data(event));
    if (owner == nullptr) {
        return;
    }

    const lv_event_code_t code = lv_event_get_code(event);
    if (code == LV_EVENT_PRESSED) {
        owner->_wake_overlay_armed = true;
        owner->wakeDisplay();
        ESP_LOGI(Tag, "display woken by touch; gesture consumed");
    } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        owner->_wake_overlay_armed = false;
        if (owner->_wake_overlay != nullptr) {
            lv_obj_add_flag(owner->_wake_overlay, LV_OBJ_FLAG_HIDDEN);
        }
    }
}

}  // namespace view
