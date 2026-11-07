#pragma once
#include <lvgl.h>

namespace mosaico_transition {
// GUI-thread only. Each transition owns fresh snapshots; never shared with DMA.
class Snapshot {
public:
    Snapshot() = default;
    ~Snapshot();
    Snapshot(const Snapshot&) = delete;
    Snapshot& operator=(const Snapshot&) = delete;
    // RGB565 black matte: caller must ensure no visible fixed sibling should
    // show through transparent panel corners (outgoing quota-target panels stay live).
    bool begin(lv_obj_t* first, lv_obj_t* second = nullptr);
    bool beginInset(lv_obj_t* content, int32_t trimBottom = 12);
    void clear();
    lv_obj_t* visualFor(lv_obj_t* original) const;
    int32_t yFor(lv_obj_t* original) const;
    void setX(lv_obj_t* original, int32_t x);
    void setY(lv_obj_t* original, int32_t y);
private:
    struct Entry {
        lv_obj_t* original = nullptr;
        lv_obj_t* image = nullptr;
        lv_draw_buf_t* buffer = nullptr;
        int32_t extent = 0;
        bool hidden = false;
    };
    bool capture(Entry& entry, lv_obj_t* original);
    Entry _entries[2]{};
};
}
