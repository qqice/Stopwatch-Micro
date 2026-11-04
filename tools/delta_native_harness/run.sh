#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p objects
CFLAGS="-std=c99 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -Isdk -Icjson -Ivendor/esp_delta_ota/include -Ivendor/esp_delta_ota/detools/c -Ivendor/esp_delta_ota/detools/c/heatshrink -DDETOOLS_CONFIG_FILE_IO=0 -DDETOOLS_CONFIG_COMPRESSION_NONE=0 -DDETOOLS_CONFIG_COMPRESSION_LZMA=0 -DDETOOLS_CONFIG_COMPRESSION_CRLE=0"
xcrun clang $CFLAGS -Dcalloc=delta_test_calloc -c vendor/esp_delta_ota/src/esp_delta_ota.c -o objects/esp_delta_ota.o
xcrun clang $CFLAGS -c vendor/esp_delta_ota/detools/c/detools.c -o objects/detools.o
xcrun clang $CFLAGS -c vendor/esp_delta_ota/detools/c/heatshrink/heatshrink_decoder.c -o objects/heatshrink.o
xcrun clang $CFLAGS -c cjson/cJSON.c -o objects/cjson.o
xcrun clang++ -std=c++17 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -Wno-deprecated-declarations -Isdk -Icjson -Iproduction -Ivendor/esp_delta_ota/include test.cpp base64.mm objects/*.o -framework Foundation -o delta_native_tests
./delta_native_tests "$@"
