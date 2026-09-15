set(APP_SRCS
    "apps/app_codex_micro/app_codex_micro.cpp"
    "apps/app_codex_micro/view/view.cpp"
    "apps/common/audio/audio.cpp"
    "apps/common/key_manager/key_manager.cpp"
    "debug/serial_debug.cpp"
    "host/host_bridge.cpp"
    "host/network_quota.cpp"
)

set(PLATFORM_SRCS
    "hal/hal.cpp"
    "hal/hal_audio.cpp"
    "hal/hal_button.cpp"
    "hal/hal_display.cpp"
    "hal/hal_ioe.cpp"
    "hal/hal_pmic.cpp"
    "hal/ble/codex_micro_ble.cpp"
    "hal/ble/hid_gatt_compat.cpp"
    "hal/drivers/cst820/cst820.cpp"
    "hal/utils/button/Button_Class.cpp"
    "hal/utils/settings/settings.cc"
)

set(MY_INCS
    "."
)

idf_component_register(
    SRCS 
        "main.cpp"
        ${APP_SRCS}
        ${PLATFORM_SRCS}
    INCLUDE_DIRS 
        ${MY_INCS}
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    "STOPWATCH_MICRO_VERSION=\"${PROJECT_VER}\""
)

# ESP-IDF 5.5.4 sizes BLE HID Output Report characteristics from the report body.
# Codex's node-hid bridge includes Report ID 6 in its 64-byte SetReport buffer,
# so the compatibility source widens that characteristic before table creation.
# It also records the matching Input CCC handle so a pre-existing authenticated
# Windows bond can restore the helper's notification state after a cold reboot.
target_link_libraries(${COMPONENT_LIB} INTERFACE
    "-Wl,--wrap=esp_ble_gatts_create_attr_tab"
)
