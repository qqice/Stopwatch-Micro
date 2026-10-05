#pragma once
#include <cstdint>

namespace mosaico_orientation {
// Public panel-interface proxy records the first forwarding failure even when
// esp_lvgl_port ignores return codes. Transaction owner holds the LVGL mutex.
struct ControlResult {
    int32_t error = 0;
    unsigned calls = 0;
    constexpr void reset() { error = 0; calls = 0; }
    constexpr int32_t record(int32_t result) {
        ++calls;
        if (error == 0 && result != 0) error = result;
        return result;
    }
    constexpr bool ok() const { return error == 0 && calls == 2; }
};
enum class RotationOutcome { Applied, Restored, Unsafe };
constexpr RotationOutcome rotationOutcome(bool applied, bool restored) {
    return applied ? RotationOutcome::Applied : restored ? RotationOutcome::Restored : RotationOutcome::Unsafe;
}

// Sensor-to-panel axes for V1.0; horizontal signs corrected by physical four-edge acceptance.
struct Model {
    uint16_t degrees = 0, candidate = 0;
    uint64_t since = 0;
    bool pending = false;
    constexpr void resetPending() { pending = false; }
    constexpr bool sample(float x, float y, float z, uint64_t ms) {
        const float ax = (x < 0 ? -x : x), ay = (y < 0 ? -y : y), az = (z < 0 ? -z : z);
        const float dominant = ax > ay ? ax : ay, minor = ax > ay ? ay : ax;
        const float norm2 = x*x + y*y + z*z;
        // Reject flat, diagonal, freefall and strong linear acceleration.
        if (!(norm2 >= .49f && norm2 <= 1.69f) ||
            dominant < .65f || dominant < az * 1.25f || dominant < minor * 1.35f) {
            resetPending(); return false;
        }
        const uint16_t next = ax > ay ? (x > 0 ? 270 : 90) : (y > 0 ? 0 : 180);
        if (next == degrees) { resetPending(); return false; }
        if (!pending || next != candidate || ms < since) {
            candidate = next; since = ms; pending = true; return false;
        }
        if (ms - since < 600) return false;
        degrees = next; resetPending(); return true;
    }
};
}
