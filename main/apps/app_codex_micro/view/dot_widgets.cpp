#include "dot_widgets.h"
#include "dot_patterns.h"
#include <cstring>
#include <new>

namespace mosaico_dot {
namespace {
enum class Kind { Text, Meter, Icon };
struct State {
    Kind kind;
    uint32_t color = 0x67E7AE;
    char text[detail::patternsMaxTextLength + 1]{};
    int pitch = 10, rows = 3;
    uint16_t bp = 0;
    bool known = false, valid = true;
    Icon icon = Icon::Unknown;
    uint8_t level = 100;
    uint16_t phase = 0;
    bool motion = false;
};
// Hand-authored nine-column masks, leftmost bit = 0x100.
constexpr uint16_t masks[][9] = {
    {0,124,130,257,56,68,0,16,0}, // Wifi
    {256,124,194,289,40,68,2,17,0}, // WifiOff: diagonal slash
    {0,0,254,258,258,258,254,0,0}, // Battery (fill handled below)
    {8,24,48,124,12,24,48,32,0}, // Bolt
    {254,68,40,16,16,40,68,254,0}, // Hourglass
    {254,130,154,166,190,162,130,254,0}, // Rectangular reset card with return arrow
    {56,68,128,128,128,128,128,68,56}, // Credit C, not a currency symbol
    {56,68,146,145,145,129,130,68,56}, // Clock
    {56,68,4,8,16,0,16,0,0}, // Unknown
    {0,0,0,0,455,0,0,0,0}, // Gap
    {4,12,28,60,124,60,28,12,4}, // Correction
    {56,68,146,257,273,281,130,254,0}, // Quota gauge: ticks, needle, flat baseline
    {56,68,130,313,273,313,130,68,56} // Coin: round rim and short internal stripes
};
constexpr int maskCount = static_cast<int>(sizeof(masks) / sizeof(masks[0]));
constexpr bool iconPatternsTest() {
    if (maskCount != static_cast<int>(Icon::Coin) + 1 || sizeof(masks[0]) / sizeof(masks[0][0]) != 9)
        return false;
    for (const auto& mask : masks) for (auto row : mask) if (row > 511) return false;
    bool distinct = false;
    for (int row = 0; row < 9; ++row)
        if (masks[static_cast<int>(Icon::Quota)][row] != masks[static_cast<int>(Icon::Coin)][row])
            distinct = true;
    return distinct;
}
static_assert(iconPatternsTest(), "dot icon dimensions and bit bounds");
struct Motion { int scale = 1000, wavePhase = -1, pulse = 0; };
Motion motion(const State& s, int w, int h, uint16_t phase, bool enabled) {
    Motion m;
    if (!enabled) return m;
    if (s.kind == Kind::Meter) {
        if (detail::highlightColumn(detail::meterLayout(w, h, s.rows), s.bp, s.known, phase, true) >= 0)
            m.wavePhase = phase % 360;
    }
    else if (s.kind == Kind::Icon && detail::min(w / 9, h / 9) >= 2) {
        if (s.icon == Icon::Coin || s.icon == Icon::ResetCard) {
            m.scale = detail::flipScale(phase);
            // Both faces of the symmetric coin are visually identical.
            if (s.icon == Icon::Coin && m.scale < 0) m.scale = -m.scale;
        } else if (s.icon == Icon::Battery && s.valid)
            m.pulse = detail::pulse(phase);
    }
    return m;
}
void dot(lv_layer_t* layer, lv_draw_rect_dsc_t& dsc, int x, int y, int d) {
    const lv_area_t area = {x, y, x + d - 1, y + d - 1};
    lv_draw_rect(layer, &dsc, &area);
}
void event(lv_event_t* e) {
    auto* obj = lv_event_get_current_target_obj(e);
    auto* s = static_cast<State*>(lv_obj_get_user_data(obj));
    if (!s) return;
    if (lv_event_get_code(e) == LV_EVENT_DELETE) {
        lv_obj_set_user_data(obj, nullptr);
        delete s;
        return;
    }
    if (lv_event_get_code(e) != LV_EVENT_DRAW_MAIN) return;
    lv_area_t a;
    lv_obj_get_content_coords(obj, &a);
    const int w = lv_area_get_width(&a), h = lv_area_get_height(&a);
    const auto m = motion(*s, w, h, s->phase, s->motion);
    auto* layer = lv_event_get_layer(e);
    lv_draw_rect_dsc_t dsc;
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_color = lv_color_hex(s->color);
    dsc.bg_opa = LV_OPA_COVER;
    dsc.radius = LV_RADIUS_CIRCLE;
    if (s->kind == Kind::Text) {
        int count = static_cast<int>(std::strlen(s->text));
        auto l = detail::textLayout(w, h, count, s->pitch);
        const bool cannotFit = count && !l.diameter;
        if (cannotFit) {
            count = 1;
            l = detail::textLayout(w, h, count, s->pitch);
        }
        if (!l.diameter) return;
        for (int i = 0; i < count; ++i) {
            const auto& glyph = detail::glyph(cannotFit ? '?' : s->text[i]);
            for (int y = 0; y < 7; ++y) for (int x = 0; x < 5; ++x)
                if (glyph.rows[y] & (1 << (4 - x)))
                    dot(layer, dsc, a.x1 + l.x + (i * 6 + x) * l.pitch,
                        a.y1 + l.y + y * l.pitch, l.diameter);
        }
    } else if (s->kind == Kind::Meter) {
        const auto g = detail::meterLayout(w, h, s->rows);
        for (int x = 0; x < g.columns; ++x) {
            const int mix = detail::waveMix(g, s->bp, s->known, static_cast<uint16_t>(detail::max(0, m.wavePhase)), m.wavePhase >= 0, x);
            const auto foreground = mix ? lv_color_mix(lv_color_hex(0xFFFFFF), lv_color_hex(s->color), static_cast<uint8_t>(mix))
                                        : lv_color_hex(s->known ? s->color : 0x69716D);
            for (int y = 0; y < g.rows; ++y) {
                const bool lit = detail::meterLit(x, y, g, s->bp, s->known);
                dsc.bg_color = lit ? foreground : lv_color_hex(0x283642);
                dot(layer, dsc, a.x1 + g.x + x * g.pitch, a.y1 + g.y + y * g.pitch, g.diameter);
            }
        }
    } else {
        const int p = detail::min(w / 9, h / 9);
        if (p < 2) return;
        const int d = detail::max(1, p * 7 / 10);
        const int ox = a.x1 + (w - (8 * p + d)) / 2;
        const int oy = a.y1 + (h - (8 * p + d)) / 2;
        const int index = static_cast<int>(s->icon);
        const auto* mask = masks[index >= 0 && index < maskCount ? index : static_cast<int>(Icon::Unknown)];
        for (int y = 0; y < 9; ++y) for (int x = 0; x < 9; ++x) {
            bool lit = (mask[y] & (1 << (8 - x))) != 0;
            if (s->icon == Icon::Battery && !s->valid) {
                // An explicit full-height question mark, never an empty battery.
                lit = (masks[8][y] & (1 << (8 - x))) != 0;
            } else if (s->icon == Icon::Battery) {
                // Seven-column body, separate terminal, five fill columns.
                lit = (y == 2 || y == 6) ? x >= 1 && x <= 7
                      : (y >= 3 && y <= 5 && (x == 1 || x == 7 || (x == 8 && y == 4)));
                if (x >= 2 && x <= 6 && y >= 3 && y <= 5) {
                    lit = x - 2 < detail::filledDots(5, s->level * 100);
                }
            }
            dsc.bg_color = lv_color_hex(s->color);
            // Only existing mask/fill dots pulse; zero capacity still has an
            // honest outline that can indicate actual charging.
            if (lit && s->icon == Icon::Battery && s->valid && m.pulse)
                dsc.bg_color = lv_color_mix(lv_color_hex(0xFFFFFF), dsc.bg_color, static_cast<uint8_t>(m.pulse));
            if (lit) dot(layer, dsc, ox + detail::projectedX(x, p, m.scale), oy + y * p, d);
        }
    }
}
lv_obj_t* create(lv_obj_t* parent, int w, int h, Kind kind) {
    auto* s = new (std::nothrow) State{};
    if (!s) return nullptr;
    auto* obj = lv_obj_create(parent);
    if (!obj) { delete s; return nullptr; }
    s->kind = kind;
    lv_obj_remove_style_all(obj);
    lv_obj_set_size(obj, detail::max(1, w), detail::max(1, h));
    lv_obj_remove_flag(obj, static_cast<lv_obj_flag_t>(LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE));
    lv_obj_set_user_data(obj, s);
    lv_obj_add_event_cb(obj, event, LV_EVENT_ALL, nullptr);
    return obj;
}
State* state(lv_obj_t* obj, Kind kind) {
    auto* s = obj ? static_cast<State*>(lv_obj_get_user_data(obj)) : nullptr;
    return s && s->kind == kind ? s : nullptr;
}
}
bool selfTest() { return detail::algorithmTest() && detail::motionTest() && iconPatternsTest(); }
lv_obj_t* createText(lv_obj_t* parent, int w, int h, int pitch, uint32_t color) {
    auto* obj = create(parent, w, h, Kind::Text);
    if (auto* s = state(obj, Kind::Text)) { s->pitch = detail::max(2, pitch); s->color = color; }
    return obj;
}
void setTextPitch(lv_obj_t* obj, int pitch) {
    auto* s = state(obj, Kind::Text);
    if (!s) return;
    pitch = detail::max(2, pitch);
    if (s->pitch == pitch) return;
    s->pitch = pitch;
    lv_obj_invalidate(obj);
}
void setMotion(lv_obj_t* obj, uint16_t phase, bool enabled) {
    auto* s = obj ? static_cast<State*>(lv_obj_get_user_data(obj)) : nullptr;
    if (!s) return;
    phase %= 360;
    if (s->phase == phase && s->motion == enabled) return;
    const int w = lv_obj_get_content_width(obj), h = lv_obj_get_content_height(obj);
    const auto old = motion(*s, w, h, s->phase, s->motion);
    const auto next = motion(*s, w, h, phase, enabled);
    s->phase = phase; s->motion = enabled;
    // Stopping for a hidden/locked/stale widget never causes a wake-up redraw.
    // The next otherwise-required draw will use its static appearance.
    if (!enabled) return;
    const auto base = lv_color_hex(s->color), white = lv_color_hex(0xFFFFFF);
    bool changed = false;
    if (old.wavePhase != next.wavePhase) {
        const auto g = detail::meterLayout(w, h, s->rows);
        const int filled = s->known ? detail::filledDots(g.columns * g.rows, s->bp) : 0;
        for (int x = 0; x * g.rows < filled; ++x) {
            const int a = detail::waveMix(g, s->bp, s->known, static_cast<uint16_t>(detail::max(0, old.wavePhase)), old.wavePhase >= 0, x);
            const int b = detail::waveMix(g, s->bp, s->known, static_cast<uint16_t>(detail::max(0, next.wavePhase)), next.wavePhase >= 0, x);
            if (!lv_color_eq(lv_color_mix(white, base, static_cast<uint8_t>(a)), lv_color_mix(white, base, static_cast<uint8_t>(b)))) {
                changed = true;
                break;
            }
        }
    }
    if (old.pulse != next.pulse && !lv_color_eq(lv_color_mix(white, base, static_cast<uint8_t>(old.pulse)),
                                              lv_color_mix(white, base, static_cast<uint8_t>(next.pulse)))) changed = true;
    const int p = detail::min(w / 9, h / 9);
    if (old.scale != next.scale) {
        uint16_t occupied = 0;
        for (auto row : masks[static_cast<int>(s->icon)]) occupied |= row;
        for (int x = 0; x < 9; ++x)
            if ((occupied & (1 << (8 - x))) &&
                detail::projectedX(x, p, old.scale) != detail::projectedX(x, p, next.scale)) changed = true;
    }
    if (changed) lv_obj_invalidate(obj);
}
void setText(lv_obj_t* obj, const char* text, uint32_t color) {
    auto* s = state(obj, Kind::Text);
    if (!s) return;
    char value[detail::patternsMaxTextLength + 1]{};
    int i = 0;
    if (text) {
        for (; i < detail::patternsMaxTextLength && text[i]; ++i) value[i] = detail::glyph(text[i]).key;
        if (i == detail::patternsMaxTextLength && text[i]) value[i - 1] = '?';
    }
    if (s->color == color && std::strcmp(s->text, value) == 0) return;
    std::memcpy(s->text, value, sizeof value);
    s->color = color;
    lv_obj_invalidate(obj);
}
lv_obj_t* createMeter(lv_obj_t* parent, int w, int h, int rows) {
    auto* obj = create(parent, w, h, Kind::Meter);
    if (auto* s = state(obj, Kind::Meter)) s->rows = detail::min(4, detail::max(1, rows));
    return obj;
}
void setMeter(lv_obj_t* obj, uint16_t bp, bool known, uint32_t color) {
    auto* s = state(obj, Kind::Meter);
    if (!s) return;
    bp = static_cast<uint16_t>(known ? detail::min(10000, bp) : 0);
    if (s->bp == bp && s->known == known && s->color == color) return;
    s->bp = bp; s->known = known; s->color = color;
    lv_obj_invalidate(obj);
}
lv_obj_t* createIcon(lv_obj_t* parent, Icon icon, int size, uint32_t color) {
    auto* obj = create(parent, size, size, Kind::Icon);
    setIcon(obj, icon, color);
    return obj;
}
void setIcon(lv_obj_t* obj, Icon icon, uint32_t color, uint8_t level, bool valid) {
    auto* s = state(obj, Kind::Icon);
    if (!s) return;
    level = icon == Icon::Battery && valid ? static_cast<uint8_t>(detail::min(100, level)) : 100;
    valid = icon == Icon::Battery ? valid : true;
    if (s->icon == icon && s->color == color && s->level == level && s->valid == valid) return;
    s->icon = icon; s->color = color; s->level = level; s->valid = valid;
    lv_obj_invalidate(obj);
}
}
