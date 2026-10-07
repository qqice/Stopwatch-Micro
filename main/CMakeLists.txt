set(APP_SRCS
    "apps/app_codex_micro/app_codex_micro.cpp"
    "apps/app_codex_micro/view/view.cpp"
    "apps/common/audio/audio.cpp"
    "apps/common/key_manager/key_manager.cpp"
    "debug/serial_debug.cpp"
    "debug/boot_trace.cpp"
    "host/host_bridge.cpp"
    "host/network_quota.cpp"
    "host/tailscale_transport.cpp"
    "host/token_history.cpp"
)

if(MOSAICO_BOARD)
    list(REMOVE_ITEM APP_SRCS "apps/app_codex_micro/view/view.cpp")
    if(CONFIG_IDF_TARGET_ESP32S31 AND CONFIG_IDF_TARGET_ARCH_RISCV)
        list(APPEND APP_SRCS "ota/panic_capture.cpp" "hal/mosaico/vfs_internal_ops.c")
    endif()
    list(APPEND APP_SRCS "apps/app_codex_micro/view/view_mosaico.cpp"
        "apps/app_codex_micro/view/dot_widgets.cpp" "host/quota_monitor.cpp" "ota/mosaico_ota.cpp"
        "host/mosaico_display_settings.cpp" "host/mosaico_session_monitor.cpp" "host/standby_sleep.cpp")
    set(PLATFORM_SRCS "hal/hal.cpp" "hal/mosaico/hal_mosaico.cpp" "hal/mosaico/bt_config.c" "hal/mosaico/usb_console.c"
        "hal/ble/codex_micro_ble.cpp" "hal/ble/hid_gatt_compat.cpp"
        "hal/utils/button/Button_Class.cpp" "hal/utils/settings/settings.cc")
else()
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

endif()

set(MY_INCS
    "${CMAKE_CURRENT_LIST_DIR}"
)

# The board-specific project includes this list from a separate main component.
list(TRANSFORM APP_SRCS PREPEND "${CMAKE_CURRENT_LIST_DIR}/")
list(TRANSFORM PLATFORM_SRCS PREPEND "${CMAKE_CURRENT_LIST_DIR}/")
idf_component_register(
    SRCS 
        "${CMAKE_CURRENT_LIST_DIR}/main.cpp"
        ${APP_SRCS}
        ${PLATFORM_SRCS}
    INCLUDE_DIRS 
        ${MY_INCS}
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    "STOPWATCH_MICRO_VERSION=\"${PROJECT_VER}\""
)

if(MOSAICO_BOARD)
    target_compile_definitions(${COMPONENT_LIB} PUBLIC MOSAICO_BOARD=1)
    if(CONFIG_IDF_TARGET_ESP32S31 AND CONFIG_IDF_TARGET_ARCH_RISCV)
        target_link_options(${COMPONENT_LIB} INTERFACE "-Wl,--wrap=esp_panic_handler"
            "-Wl,--wrap=esp_vfs_register_fs")
    endif()
    if(MOSAICO_OTA_HEALTH_TEST_FAIL)
        target_compile_definitions(${COMPONENT_LIB} PRIVATE MOSAICO_OTA_HEALTH_TEST_FAIL=1)
    endif()
endif()

# ESP-IDF 5.5.4 sizes BLE HID Output Report characteristics from the report body.
# Codex's node-hid bridge includes Report ID 6 in its 64-byte SetReport buffer,
# so the compatibility source widens that characteristic before table creation.
# It also records the matching Input CCC handle so a pre-existing authenticated
# Windows bond can restore the helper's notification state after a cold reboot.
target_link_libraries(${COMPONENT_LIB} INTERFACE
    "-Wl,--wrap=esp_ble_gatts_create_attr_tab"
)

if(MOSAICO_BOARD)
    # Exact managed-driver TU only; preserve SDK and managed component sources.
    idf_component_get_property(bmi_lib espressif__bmi270 COMPONENT_LIB)
    idf_component_get_property(bmi_dir espressif__bmi270 COMPONENT_DIR)
    set_property(SOURCE "${bmi_dir}/src/bmi270.c" TARGET_DIRECTORY ${bmi_lib}
        APPEND PROPERTY COMPILE_OPTIONS "-include"
        "${CMAKE_CURRENT_LIST_DIR}/hal/mosaico/mosaico_bmi_delay.h"
        "-DMOSAICO_BMI_DELAY_OVERRIDE=1")
endif()
