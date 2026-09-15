# Battery cold boot and console errors

ESP-IDF 6.1's USB Serial/JTAG VFS returns `-1` / `EIO` when no USB host is
connected. The pinned mooncake_log dependency used `fmt::print`; its
`fwrite_fully` converts a failed write into an exception. The first HAL log
could therefore abort a battery-only boot before any display initialization.

`patches/mooncake-log-console.patch` keeps formatting and log signals, but uses
best-effort `fwrite` for output. Losing the console is not fatal. This applies
to time/level prefixes, colored output, and tagged and untagged messages.
The pinned patch is verified by `fetch_repos.py`.

## Persistent diagnostics

`debug boot` reads the last four boots from the dedicated `boot_trace` NVS
namespace. Each entry contains the boot number, ESP-IDF reset reason, last
completed stage, and milliseconds since boot. Recording errors are reported;
diagnostics never erase NVS. Records are updated only during startup, not on
every screen frame or network update.

| Stage | Meaning |
| --- | --- |
| 1 | Entered app_main and initialized the trace |
| 2 | About to initialize I2C |
| 3 | PMIC initialization returned |
| 4 | IO expander initialization returned |
| 5 | Display initialization returned |
| 6 | HAL initialization returned |
| 7 | System UI opened |

A completed stage indicates control-flow progress; use the hardware self-test
for individual peripheral health. Reset reason 1 is power-on, 4 is panic, and
11 is USB peripheral reset in the current IDF headers.

## Verification on StopWatch

Before the fix, battery boot repeatedly panicked: boots 18–20 ended at stage 1
around 67 ms with reset reason 4. Connecting USB allowed boot 21 to finish.
After the fix, the user confirmed battery-only cold boot lit the display.
The counter advanced once from 22 to 23, reset reason was 1, and stage 7 was
reached at 1224 ms. This is initialization timing, not optical display latency.
Hardware self-test passed 17/17, host regression passed 36/36, and dependency
patch verification passed. The power-button and charging configuration were
not changed by this fix.

Local evidence: `.artifacts/battery-trace-reproduced.log`,
`.artifacts/battery-console-fix-verified.log`,
`.artifacts/battery-console-fix-baseline.log`,
`.artifacts/battery-console-host-tests.log`.
