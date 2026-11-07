#pragma once
#include <cstdio>
#define ESP_LOGI(tag, fmt, ...) std::printf("%s " fmt "\n", tag, __VA_ARGS__)
