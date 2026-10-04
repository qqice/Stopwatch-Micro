/* SPDX-License-Identifier: MIT
 * Address-specific cache-safety mitigation, not a proven startup-panic diagnosis.
 * Called during single CDC0 console registration before HAL/network startup.
 */
#include <sdkconfig.h>
#if !defined(MOSAICO_BOARD) || !CONFIG_IDF_TARGET_ESP32S31 || !CONFIG_IDF_TARGET_ARCH_RISCV
#error "VFS internal ops wrapper requires the Mosaico S31 main RISC-V app"
#endif
#include <esp_attr.h>
#include <esp_heap_caps.h>
#include <esp_vfs.h>
#include <esp_vfs_ops.h>
#include <vfs_tinyusb.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <string.h>

static DRAM_ATTR esp_vfs_fs_ops_t *internal_ops;
static DRAM_ATTR atomic_flag registering = ATOMIC_FLAG_INIT;

esp_err_t __real_esp_vfs_register_fs(const char *base_path, const esp_vfs_fs_ops_t *vfs, int flags, void *ctx);
esp_err_t __wrap_esp_vfs_register_fs(const char *base_path, const esp_vfs_fs_ops_t *vfs, int flags, void *ctx);
esp_err_t __wrap_esp_vfs_register_fs(const char *base_path, const esp_vfs_fs_ops_t *vfs, int flags, void *ctx)
{
    if (!(flags & ESP_VFS_FLAG_STATIC) || !base_path || strcmp(base_path, VFS_TUSB_PATH_DEFAULT))
        return __real_esp_vfs_register_fs(base_path, vfs, flags, ctx);
    if (!vfs) return ESP_ERR_INVALID_ARG;
#ifdef CONFIG_VFS_SUPPORT_DIR
    if (vfs->dir) return ESP_ERR_INVALID_ARG;
#endif
#ifdef CONFIG_VFS_SUPPORT_TERMIOS
    if (vfs->termios) return ESP_ERR_INVALID_ARG;
#endif
#if CONFIG_VFS_SUPPORT_SELECT
    if (vfs->select) return ESP_ERR_INVALID_ARG;
#endif
    if (atomic_flag_test_and_set(&registering)) return ESP_ERR_INVALID_STATE;
    if (internal_ops) {
        atomic_flag_clear(&registering);
        return ESP_ERR_INVALID_STATE; // Never overwrite a table retained by open FDs.
    }
    // Initialize fresh allocated storage, like SDK esp_vfs_duplicate_fs_ops(),
    // without mutating declared const members of an already-live static object.
    esp_vfs_fs_ops_t *ops = heap_caps_malloc(sizeof(*ops), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!ops) {
        atomic_flag_clear(&registering);
        return ESP_ERR_NO_MEM;
    }
    memcpy(ops, vfs, sizeof(*ops));
    const esp_err_t result = __real_esp_vfs_register_fs(base_path, ops, flags, ctx);
    if (result == ESP_OK) internal_ops = ops; // STATIC ownership: retain for firmware lifetime.
    else heap_caps_free(ops);
    atomic_flag_clear(&registering); // Failed registration may safely retry.
    return result;
}