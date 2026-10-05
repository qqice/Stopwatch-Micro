#pragma once
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_task_wdt.h"
// pdMS_TO_TICKS floors; a tick boundary can consume another tick. Two extra
// ticks guarantee the driver's minimum reset/power/blob wait without spinning.
static inline void mosaico_bmi_delay(TickType_t ticks)
{
    if (esp_task_wdt_status(NULL) == ESP_OK) esp_task_wdt_reset();
    vTaskDelay(ticks + 2);
    if (esp_task_wdt_status(NULL) == ESP_OK) esp_task_wdt_reset();
}
#ifdef MOSAICO_BMI_DELAY_OVERRIDE
#define vTaskDelay mosaico_bmi_delay
#endif
