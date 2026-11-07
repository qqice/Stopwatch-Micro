#pragma once
extern bool native_external_ram;
inline bool esp_ptr_external_ram(const void*) { return native_external_ram; }
