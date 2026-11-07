#!/bin/sh
set -eu
cd "$(dirname "$0")"
clang++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined -fno-omit-frame-pointer -I. test.cpp -o view_update_tests
./view_update_tests
