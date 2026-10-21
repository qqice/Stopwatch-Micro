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
        list(APPEND APP_SRCS "host/mosaico_lan_tcp_probe.cpp" "ota/panic_capture.cpp" "hal/mosaico/vfs_internal_ops.c")
    endif()
    list(APPEND APP_SRCS "apps/app_codex_micro/view/view_mosaico.cpp"
        "apps/app_codex_micro/view/dot_widgets.cpp" "host/quota_monitor.cpp" "ota/mosaico_ota.cpp"
        "debug/mosaico_rx_observer.cpp" "debug/mosaico_wifi_rx_observer.cpp" "debug/mosaico_rx_ingress.cpp" "main_idle_wait.cpp" "host/touch_sleep.cpp" "host/mosaico_display_settings.cpp" "host/mosaico_session_monitor.cpp" "host/standby_sleep.cpp" "host/powerbank_keepalive.cpp" "host/gauge_discharge_trace.cpp" "hal/mosaico/mosaico_nand_probe.cpp" "hal/mosaico/mosaico_nand_store.cpp" "hal/mosaico/mosaico_gauge_scale_journal.cpp" "host/gauge_trace_nand_backend.cpp" "host/mosaico_system_log.cpp")
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
set(APP_LDFRAGMENTS)
if(MOSAICO_BOARD AND CONFIG_IDF_TARGET_ESP32S31)
    list(APPEND APP_LDFRAGMENTS "${CMAKE_CURRENT_LIST_DIR}/linker/mosaico_ble_rodata.lf")
endif()
idf_component_register(
    SRCS 
        "${CMAKE_CURRENT_LIST_DIR}/main.cpp"
        ${APP_SRCS}
        ${PLATFORM_SRCS}
    INCLUDE_DIRS 
        ${MY_INCS}
    LDFRAGMENTS ${APP_LDFRAGMENTS}
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE
    "STOPWATCH_MICRO_VERSION=\"${PROJECT_VER}\""
)

if(MOSAICO_BOARD)
    target_compile_definitions(${COMPONENT_LIB} PUBLIC MOSAICO_BOARD=1 MOSAICO_NAND_STORE=1)
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

if(MOSAICO_BOARD)
    # Exact-version compatibility TUs only; never edit SDK/managed component sources.
    idf_component_get_property(nand_lib espressif__spi_nand_flash COMPONENT_LIB)
    idf_component_get_property(nand_dir espressif__spi_nand_flash COMPONENT_DIR)
    # The project-local readiness deadline uses the public esp_timer API.
    target_link_libraries(${nand_lib} PRIVATE idf::esp_timer)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_CURRENT_LIST_DIR}/../tools/mosaico_nand_driver_compat.py")
    set(nand_compat_dir "${CMAKE_CURRENT_BINARY_DIR}/nand-compat-1.4.1")
    execute_process(COMMAND "${PYTHON}" "${CMAKE_CURRENT_LIST_DIR}/../tools/mosaico_nand_driver_compat.py"
        --component "${nand_dir}" --output "${nand_compat_dir}"
        RESULT_VARIABLE nand_compat_result)
    if(NOT nand_compat_result EQUAL 0)
        message(FATAL_ERROR "Refusing unverified NAND driver version/patch anchors")
    endif()
    foreach(nand_tu nand.c nand_impl.c dhara_glue.c)
        set_source_files_properties("${nand_dir}/src/${nand_tu}" TARGET_DIRECTORY ${nand_lib} PROPERTIES HEADER_FILE_ONLY TRUE)
        target_sources(${nand_lib} PRIVATE "${nand_compat_dir}/${nand_tu}")
    endforeach()
endif()

# Keep the SPI component's includes, flags, dependencies and linker fragments.
# Only Mosaico S31 replaces the pinned driver TU; the installed SDK stays intact.
if(MOSAICO_BOARD AND CONFIG_IDF_TARGET_ESP32S31)
    idf_component_get_property(spi_lib esp_driver_spi COMPONENT_LIB)
    idf_component_get_property(spi_dir esp_driver_spi COMPONENT_DIR)
    idf_build_get_property(spi_idf_path IDF_PATH)
    set(spi_original "${spi_dir}/src/gpspi/spi_master.c")
    set(spi_compat_script "${CMAKE_CURRENT_LIST_DIR}/../tools/mosaico_spi_master_compat.py")
    set(spi_compat_dir "${CMAKE_CURRENT_BINARY_DIR}/spi-master-compat-idf61")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${spi_compat_script}" "${spi_original}")
    execute_process(COMMAND "${PYTHON}" "${spi_compat_script}"
        --idf "${spi_idf_path}" --output "${spi_compat_dir}"
        RESULT_VARIABLE spi_compat_result)
    if(NOT spi_compat_result EQUAL 0)
        message(FATAL_ERROR "Refusing unverified SPI master source/patch anchors")
    endif()
    get_target_property(spi_sources ${spi_lib} SOURCES)
    set(spi_master_matches 0)
    set(spi_kept_sources)
    foreach(spi_source IN LISTS spi_sources)
        get_filename_component(spi_absolute "${spi_source}" ABSOLUTE BASE_DIR "${spi_dir}")
        if(spi_absolute STREQUAL spi_original)
            math(EXPR spi_master_matches "${spi_master_matches} + 1")
        else()
            list(APPEND spi_kept_sources "${spi_source}")
        endif()
    endforeach()
    if(NOT spi_master_matches EQUAL 1)
        message(FATAL_ERROR "Expected exactly one original SPI master TU")
    endif()
    set_property(TARGET ${spi_lib} PROPERTY SOURCES "${spi_kept_sources}")
    target_sources(${spi_lib} PRIVATE "${spi_compat_dir}/spi_master.c")
    # Preserve the original directory's quoted-include lookup for this TU.
    set_source_files_properties("${spi_compat_dir}/spi_master.c"
        TARGET_DIRECTORY ${spi_lib} PROPERTIES INCLUDE_DIRECTORIES "${spi_dir}/src/gpspi")
endif()
