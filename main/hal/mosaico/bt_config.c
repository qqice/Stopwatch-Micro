/* SPDX-License-Identifier: MIT
 * Expand the SDK's C designated-init defaults in a C translation unit.
 * Do not reconstruct or weaken the controller's target-specific defaults. */
#include <esp_bt.h>
#include <esp_bt_main.h>
esp_bt_controller_config_t mosaico_bt_controller_config(void)
{
    esp_bt_controller_config_t cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    return cfg;
}
esp_bluedroid_config_t mosaico_bluedroid_config(void)
{
    esp_bluedroid_config_t cfg = BT_BLUEDROID_INIT_CONFIG_DEFAULT();
    return cfg;
}
