#pragma once
#include <cstdint>
#include <lvgl.h>

namespace mosaico_dot {
enum class Icon { Wifi, WifiOff, Battery, Bolt, Hourglass, ResetCard, Credit, Clock, Unknown, Gap, Correction, Quota, Coin };
// Allocation failure returns nullptr; setters accept nullptr. LVGL-thread only.
// Text retains up to 32 glyphs. Longer input gets a trailing '?'; an area too
// small to fit the retained text shows '?' instead of clipping exact values.
lv_obj_t* createText(lv_obj_t* parent, int width, int height, int pitch = 10, uint32_t color = 0x67E7AE);
void setText(lv_obj_t* obj, const char* text, uint32_t color = 0x67E7AE);
void setTextPitch(lv_obj_t* obj, int pitch); // Same >=2 request clamp as createText.
// Caller schedules and gates motion (visible, awake, fresh, actually charging).
// No internal timers. Phase wraps at 360; text/unsupported icons remain static.
void setMotion(lv_obj_t* obj, uint16_t phase, bool enabled);
lv_obj_t* createMeter(lv_obj_t* parent, int width, int height, int rows = 3);
void setMeter(lv_obj_t* obj, uint16_t basisPoints, bool known, uint32_t foreground = 0x67E7AE);
lv_obj_t* createIcon(lv_obj_t* parent, Icon kind, int size = 28, uint32_t color = 0xE9EDF2);
void setIcon(lv_obj_t* obj, Icon kind, uint32_t color, uint8_t level = 100, bool valid = true);
bool selfTest(); // Pure algorithm check, safe without an LVGL display.
}
