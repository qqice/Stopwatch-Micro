#include "view_transition_snapshot.h"
#include <src/misc/cache/instance/lv_image_cache.h>
#include <esp_memory_utils.h>
#include <esp_timer.h>
#include <esp_log.h>
#include <initializer_list>

namespace mosaico_transition {
Snapshot::~Snapshot() { clear(); }
bool Snapshot::capture(Entry& entry, lv_obj_t* original) {
    if (!original) return false;
    lv_obj_update_layout(original);
    entry.original = original;
    entry.hidden = lv_obj_has_flag(original, LV_OBJ_FLAG_HIDDEN);
    // Public snapshot API draws the whole subtree, including off-screen parts.
    lv_obj_remove_flag(original, LV_OBJ_FLAG_HIDDEN);
    entry.buffer = lv_snapshot_take(original, LV_COLOR_FORMAT_RGB565);
    if (!entry.buffer || !entry.buffer->data || !esp_ptr_external_ram(entry.buffer->data)) return false;
    // Infer snapshot's symmetric extra-draw border from the public image header;
    // no private LVGL geometry API or hand-written subtree renderer is needed.
    const int32_t extra = static_cast<int32_t>(entry.buffer->header.w) - lv_obj_get_width(original);
    if (extra < 0 || extra % 2 || static_cast<int32_t>(entry.buffer->header.h) - lv_obj_get_height(original) != extra) return false;
    entry.extent = extra / 2;
    entry.image = lv_image_create(lv_obj_get_parent(original));
    if (!entry.image) return false;
    lv_obj_remove_flag(entry.image, static_cast<lv_obj_flag_t>(LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE));
    lv_image_set_src(entry.image, entry.buffer);
    if (lv_image_get_src(entry.image) != entry.buffer) return false;
    lv_obj_set_pos(entry.image, lv_obj_get_x(original) - entry.extent, lv_obj_get_y(original) - entry.extent);
    lv_obj_add_flag(original, LV_OBJ_FLAG_HIDDEN);
    return true;
}
bool Snapshot::begin(lv_obj_t* first, lv_obj_t* second) {
    clear();
    const int64_t started = esp_timer_get_time();
    const bool ready = capture(_entries[0], first) && (!second || capture(_entries[1], second));
    if (!ready) clear(); // Partial allocation/capture is an ordinary live-animation fallback.
    ESP_LOGI("view_transition", "snapshot count=%u ready=%u prepare_us=%llu", second ? 2U : 1U,
             ready ? 1U : 0U, static_cast<unsigned long long>(esp_timer_get_time() - started));
    return ready;
}
bool Snapshot::beginInset(lv_obj_t* content, int32_t trimBottom) {
    clear();
    if (!content || trimBottom != 12) return false;
    lv_obj_update_layout(content);
    auto* parent = lv_obj_get_parent(content);
    // Only the known settings main-grid: its empty bottom strip leaves the
    // sheet's transparent rounded corners live. Any changed geometry is live.
    if (!parent || lv_obj_has_flag(content, LV_OBJ_FLAG_HIDDEN) ||
        lv_obj_get_width(content) != 440 || lv_obj_get_height(content) != 346 ||
        lv_obj_get_x(content) != 0 || lv_obj_get_y(content) != 56 ||
        lv_obj_get_width(parent) != 440 || lv_obj_get_height(parent) != 402 ||
        lv_obj_get_child_count(content) != 4) return false;
    for (auto* obj : {content, parent}) {
        if (!lv_color_eq(lv_obj_get_style_bg_color(obj, LV_PART_MAIN), lv_color_hex(0x080B10)) ||
            lv_obj_get_style_bg_opa(obj, LV_PART_MAIN) != LV_OPA_COVER ||
            lv_obj_get_style_border_width(obj, LV_PART_MAIN) || lv_obj_get_style_shadow_width(obj, LV_PART_MAIN) ||
            lv_obj_get_style_outline_width(obj, LV_PART_MAIN) || lv_obj_get_style_radius(obj, LV_PART_MAIN) != 10) return false;
    }
    for (uint32_t i = 0; i < lv_obj_get_child_count(content); ++i) {
        auto* child = lv_obj_get_child(content, i);
        if (lv_obj_has_flag(child, LV_OBJ_FLAG_HIDDEN)) continue;
        // One extra pixel includes the rounded tile's antialiasing fringe.
        if (lv_obj_get_x(child) < 1 || lv_obj_get_y(child) < 1 ||
            lv_obj_get_x(child) + lv_obj_get_width(child) + 1 > 440 ||
            lv_obj_get_y(child) + lv_obj_get_height(child) + 1 > 346 - trimBottom ||
            lv_obj_get_style_shadow_width(child, LV_PART_MAIN) || lv_obj_get_style_outline_width(child, LV_PART_MAIN)) return false;
    }
    const int64_t started = esp_timer_get_time();
    lv_obj_set_style_radius(content, 0, LV_PART_MAIN);
    const bool captured = capture(_entries[0], content);
    lv_obj_set_style_radius(content, 10, LV_PART_MAIN); // Also on every allocation/image failure.
    const bool ready = captured && _entries[0].extent == 0;
    if (ready) {
        lv_image_set_inner_align(_entries[0].image, LV_IMAGE_ALIGN_TOP_LEFT);
        lv_obj_set_size(_entries[0].image, 440, 346 - trimBottom);
    } else clear();
    ESP_LOGI("view_transition", "inset ready=%u prepare_us=%llu", ready ? 1U : 0U,
             static_cast<unsigned long long>(esp_timer_get_time() - started));
    return ready;
}
void Snapshot::clear() {
    for (auto& entry : _entries) {
        // Image deletion alone does not own/free its source. Drop decoder/cache
        // references before destroying the draw buffer, per the public API.
        if (entry.image) lv_obj_delete(entry.image);
        if (entry.buffer) { lv_image_cache_drop(entry.buffer); lv_draw_buf_destroy(entry.buffer); }
        if (entry.original) {
            if (entry.hidden) lv_obj_add_flag(entry.original, LV_OBJ_FLAG_HIDDEN);
            else lv_obj_remove_flag(entry.original, LV_OBJ_FLAG_HIDDEN);
        }
        entry = {};
    }
}
lv_obj_t* Snapshot::visualFor(lv_obj_t* original) const {
    for (const auto& entry : _entries) if (entry.original == original && entry.image) return entry.image;
    return original;
}
int32_t Snapshot::yFor(lv_obj_t* original) const {
    for (const auto& entry : _entries) if (entry.original == original && entry.image) {
        lv_obj_update_layout(entry.image); // setY can precede LVGL's deferred layout pass.
        return lv_obj_get_y(entry.image) + entry.extent;
    }
    lv_obj_update_layout(original);
    return lv_obj_get_y(original);
}
void Snapshot::setX(lv_obj_t* original, int32_t x) {
    for (const auto& entry : _entries) if (entry.original == original && entry.image) { lv_obj_set_x(entry.image, x - entry.extent); return; }
    lv_obj_set_x(original, x);
}
void Snapshot::setY(lv_obj_t* original, int32_t y) {
    for (const auto& entry : _entries) if (entry.original == original && entry.image) { lv_obj_set_y(entry.image, y - entry.extent); return; }
    lv_obj_set_y(original, y);
}
}
