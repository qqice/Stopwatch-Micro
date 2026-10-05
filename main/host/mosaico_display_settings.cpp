#include "mosaico_display_settings.h"
#include <hal/hal.h>
#include <ota/mosaico_ota.h>
#include <esp_timer.h>
#include <esp_ota_ops.h>
#include <nvs.h>
#include <mutex>
#include <atomic>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace MosaicoDisplay {
namespace {
std::mutex mutex;
// Separate from the short GUI cache lock: serial late provisioning can move
// the sole writer from fallback to network only after an in-flight commit ends.
std::mutex writerMutex;
Model model;
bool loaded = false;
enum class Owner { None, Network, Fallback };
std::atomic<Owner> owner{Owner::None};
std::atomic<TaskHandle_t> ownerTask{nullptr};
}
void init() {
    std::lock_guard<std::mutex> guard(mutex);
    if (loaded) return;
    const int brightness = GetHAL().getBackLightBrightness();
    if (brightness >= 10 && brightness <= 100)
        model.state.config.chargeBrightness = model.state.config.batteryBrightness = static_cast<uint8_t>(brightness);
    nvs_handle_t h;
    esp_err_t error = nvs_open("mosaico_disp", NVS_READONLY, &h);
    if (error == ESP_OK) {
        Blob blob{}; size_t size = blob.size();
        error = nvs_get_blob(h, "config", blob.data(), &size);
        nvs_close(h);
        Config config;
        if (error == ESP_OK && (size != blob.size() || !decode(blob, config))) error = ESP_ERR_INVALID_ARG;
        if (error == ESP_OK) { model.state.config = config; model.state.savedRevision = model.state.revision; }
    }
    model.state.error = error == ESP_ERR_NVS_NOT_FOUND ? ESP_OK : error;
    loaded = true; // Missing/corrupt data never causes an automatic write or erase.
}
bool snapshot(Snapshot& out) {
    std::unique_lock<std::mutex> guard(mutex, std::try_to_lock);
    if (!guard.owns_lock() || !loaded) return false;
    out = model.state; return true;
}
Snapshot snapshot() {
    // Read-only diagnostics may briefly wait on RAM copies, never on flash.
    std::lock_guard<std::mutex> guard(mutex);
    return model.state;
}
bool request(Config config) {
    std::unique_lock<std::mutex> guard(mutex, std::try_to_lock);
    if (!guard.owns_lock() || !loaded) return false;
    const uint32_t before = model.state.revision;
    model.request(config, esp_timer_get_time());
    const TaskHandle_t task = ownerTask.load();
    if (task && before != model.state.revision) xTaskNotifyGive(task);
    return true;
}
void service() {
    std::unique_lock<std::mutex> writer(writerMutex, std::try_to_lock);
    if (!writer.owns_lock() || ownerTask.load() != xTaskGetCurrentTaskHandle()) return;
    if (MosaicoOta::busy() || MosaicoOta::healthPending()) return;
    MosaicoOta::UiSnapshot ota{};
    if (!MosaicoOta::copyUiSnapshot(ota)) return;
    // No commits across a staged image, boot selection or selector validation.
    if ((ota.imageVerified && ota.stage != MosaicoOta::UiStage::Complete) ||
        ota.stage == MosaicoOta::UiStage::ReadyReboot || ota.stage == MosaicoOta::UiStage::BootChecking) return;
    const esp_partition_t* running = esp_ota_get_running_partition();
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    if (!running || esp_ota_get_state_partition(running, &state) != ESP_OK || state != ESP_OTA_IMG_VALID) return;
    Config config; uint32_t revision;
    {
        std::unique_lock<std::mutex> guard(mutex, std::try_to_lock);
        if (!guard.owns_lock() || !loaded || !model.due(esp_timer_get_time())) return;
        config = model.state.config; revision = model.state.revision;
        model.attemptedRevision = revision;
    }
    const Blob blob = encode(config);
    nvs_handle_t h;
    esp_err_t error = nvs_open("mosaico_disp", NVS_READWRITE, &h);
    if (error == ESP_OK) {
        error = nvs_set_blob(h, "config", blob.data(), blob.size());
        if (error == ESP_OK) error = nvs_commit(h);
        nvs_close(h);
    }
    // No flash while holding the callback lock. Late changes retain their own
    // revision/timestamp/pending bit rather than being acknowledged as saved.
    std::lock_guard<std::mutex> guard(mutex);
    model.completed(revision, error);
}
bool claimNetworkOwner() {
    // Called before the quota OTA owner loop. Waiting here quiesces any
    // fallback write; no GUI cache lock is held over that wait or flash.
    std::lock_guard<std::mutex> writer(writerMutex);
    if (owner.load() == Owner::Network) return false;
    owner.store(Owner::Network);
    ownerTask.store(xTaskGetCurrentTaskHandle()); return true;
}
void startFallbackOwner() {
    std::lock_guard<std::mutex> writer(writerMutex);
    Owner expected = Owner::None;
    if (!owner.compare_exchange_strong(expected, Owner::Fallback)) return;
    const auto task = [](void*) {
        const TaskHandle_t self = xTaskGetCurrentTaskHandle();
        {
            std::lock_guard<std::mutex> writer(writerMutex);
            // Network setup may finish before this lower-priority task starts.
            if (owner.load() == Owner::Fallback) ownerTask.store(self);
        }
        for (;;) {
            if (ownerTask.load() != self) {
                // Do not delete: a callback may already hold the old handle.
                // A late notification is harmless and can never resume writes.
                ulTaskNotifyTake(pdTRUE, portMAX_DELAY); continue;
            }
            service(); ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(250));
        }
    };
    if (xTaskCreate(task, "display_save", 4096, nullptr, 1, nullptr) != pdPASS) {
        owner.store(Owner::None);
        std::lock_guard<std::mutex> guard(mutex);
        model.state.error = ESP_ERR_NO_MEM;
    }
}
}
