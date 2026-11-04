#pragma once
#include <stdint.h>
#include <stddef.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_ARG 0x102
static inline const char* esp_err_to_name(int err){return err?"injected_or_bounds":"ok";}
