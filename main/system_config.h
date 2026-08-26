/*
 * SPDX-License-Identifier: MIT
 */
#pragma once

#ifndef STOPWATCH_MICRO_VERSION
#define STOPWATCH_MICRO_VERSION "0.0.0-dev"
#endif

namespace system_config {

inline constexpr char ProductName[]     = "Codex Micro";
inline constexpr char FirmwareVersion[] = STOPWATCH_MICRO_VERSION;
inline constexpr bool DisplayAlwaysOn   = true;

}  // namespace system_config
