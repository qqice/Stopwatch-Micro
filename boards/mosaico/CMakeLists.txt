cmake_minimum_required(VERSION 3.16)
if(DEFINED IDF_TARGET AND NOT IDF_TARGET STREQUAL "esp32s31")
    message(FATAL_ERROR "Mosaico requires preview target esp32s31")
endif()
set(IDF_TARGET "esp32s31" CACHE STRING "Mosaico chip target")
get_filename_component(STOPWATCH_ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(MOSAICO_BOARD ON)
set(EXTRA_COMPONENT_DIRS
 "${STOPWATCH_ROOT}/components/mooncake"
 "${STOPWATCH_ROOT}/components/mooncake_log"
 "${STOPWATCH_ROOT}/components/smooth_ui_toolkit"
 "${STOPWATCH_ROOT}/components/microlink-source/components/microlink"
 "${STOPWATCH_ROOT}/components/microlink-source/components/microlink/components/wireguard_lwip")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
if(NOT DEFINED PROJECT_VER)
    set(PROJECT_VER "0.10.2-mosaico-ota")
endif()
project(Stopwatch-Mosaico)
