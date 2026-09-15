# Idle radio validation

The eco profile locks the display after idle and uses a five-minute radio-off
cycle. Run one real cycle only when the device can remain untouched for about
six minutes:

```powershell
python tools/test_idle_power_runtime.py --port COM24
```

The helper waits with sparse 12-second status checks, requires offline phase 2
with Wi-Fi and BLE inactive at 80 MHz, then requires a refresh cycle increment
and return to offline state. It wakes the display and restores the eco profile
in `finally` when possible.

Its `radio_off_ratio` is the measured interval delta of `off_ms` divided by elapsed time. It is a radio duty-time
observation, not energy, current, battery-life, or charge-saving measurement.
The StopWatch battery percentage and voltage are not a Coulomb gauge, so they
cannot substantiate absolute energy claims without an external power meter.

`debug power baseline` exists for a future controlled A/B comparison. It is not
an energy benchmark by itself. `debug power radio` and `debug power eco` select
the remaining supported policies; `debug power-refresh` forces one periodic
refresh for diagnostics.

## M5PM1 measurement boundary

The official M5PM1 register map exposes VREF (0x20/21), VBAT (0x22/23), VIN (0x24/25),
5VOUT (0x26/27), and generic GPIO/temperature ADC results (0x28/29). The StopWatch
power schematic connects the battery ADC to a voltage divider; GPIO ADC-capable pins
are used for interrupt/charger status, not a calibrated system-current shunt.
The schematic's 185/425 mA annotations specify charger programming, not measured load current.
Thus voltage/state telemetry cannot give system mA, mW, mAh or coulomb-counted remaining charge.

Sources: https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/1207/M5PM1_Datasheet_CN.pdf
and https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/1242/C152-SCH_Stopwatch_PRJ_Main_VA_20251201_2026_04_24_17_46_22.pdf

Bluetooth stays initialized to preserve GATT/bond state, but connections and advertising
are stopped while locked and supported modem sleep is enabled. Wi-Fi uses esp_wifi_stop.
No automatic light/deep sleep or PMIC rail/charging changes are made, preserving USB diagnostics
and touch/button wake. CPU frequency is 80 MHz offline and 240 MHz during updates/active use.
A scheduled update starts every 300 seconds while locked; connection attempts have a 90-second
window, with bounded in-flight request/stop cleanup potentially extending the transition.

WireGuard handshake timestamps now use SNTP-synchronized Unix time in TAI64N format, rather than boot uptime, so retained peers do not reject new boots as replayed old handshakes.

## Board validation

One real scheduled cycle passed on the StopWatch with ESP-IDF 6.1:

- Offline: Wi-Fi stopped, BLE disconnected and not advertising, Bluetooth modem
  sleep reported, CPU read back at 80 MHz, clock API error 0.
- Five-minute update: quota/history retrieved through Tailscale; returned offline.
- Wake: CPU returned to 240 MHz, Wi-Fi and the prior BLE connection recovered.
- During the observed 325.313-second interval, Wi-Fi was stopped for 309.400
  seconds (95.1%). This is a single-cycle **Wi-Fi stopped-time fraction**, not an
  energy-saving percentage or a prediction of battery life.
- Hardware self-test: 17/17; WireGuard known-answer/tampered-tag test passed;
  36 host regression tests passed.

Raw local evidence: `.artifacts/idle-power-cycle.log`,
`.artifacts/idle-power-final-status.log`, `.artifacts/idle-power-host-tests.log`.
