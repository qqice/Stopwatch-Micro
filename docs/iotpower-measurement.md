# IoTPower V1 independent capture

The collector uses the vendor's native parser; it does **not** guess serial
frames or bind output-setting/firmware-update functions. Verified source is
PowerAnalyzer3.0.100.0 from the official Store link in the
[LuatOS client documentation](https://wiki.luatos.org/iotpower/pc.html).
The GUI was never run; its embedded AMD64 parser was extracted with .NET PE
metadata, and actual cdecl signatures/exports were checked. The temporary GUI
package was then uninstalled. Native binaries remain ignored private artifacts,
not redistributed in this repository.

On this computer:
- V1: COM21, CP2102 VID:PID10c4:ea60, serial0001 (verify again before use).
- DLL: `C:\Workspace\Stopwatch-Micro\.artifacts\iotpower\native\iot_parser.dll`
- Approved SHA256: `100bc284bc998252d0be924629f1c16e0264122549df10697357444296d244e7`.
- Python: `C:\Espressif\tools\python\v6.1\venv\Scripts\python.exe`.

Example read-only capture with physically disabled output:

```powershell
& C:\Espressif\tools\python\v6.1\venv\Scripts\python.exe `
  C:\Workspace\Stopwatch-Micro\tools\iotpower_capture.py `
  --port COM21 `
  --dll C:\Workspace\Stopwatch-Micro\.artifacts\iotpower\native\iot_parser.dll `
  --dll-sha256 100bc284bc998252d0be924629f1c16e0264122549df10697357444296d244e7 `
  --seconds 300 `
  --output C:\Workspace\Stopwatch-Micro\.artifacts\iotpower\run-001
```

The output directory must be new; existing captures are never overwritten.
`--list` only enumerates ports and loads no DLL. To READ a manually enabled
supply, explicitly add `--allow-enabled-output`; this still never changes voltage,
current limit, output state or firmware. Default capture refuses enabled output.
The V1's initialization query yields a fresh status before samples are accepted;
there is no documented fresh pre-initialization status getter.

`samples.csv` contains voltageV, currentA, powerW, sample indices and batch host
arrival timestamps. `summary.json` contains means/peaks, per-received-sample Wh,
nominal sample coverage and completion/error state. Vendor getter units areV/mA;
conversion toA occurs once. [Official V1 documentation](https://wiki.luatos.org/iotpower/power/index.html)
lists10kHz; host batch timestamps are NOT sample timestamps. TheV1 interface
exposes no documented packet sequence or raw-frame API. Therefore packet loss
remains unknown, delay flags/coverage are only heuristics, and
`whole_wall_time_energy_Wh` stays null. Received-sample energy must not silently
include unobserved wall time. Zero/no-load capture proves the link, NOT board
power or calibration accuracy.

## Whole-board comparison

Use one supply path: V1 output+ to the board's verified5V INPUT, output- toGND.
Disconnect the Mosaico USB power cable during measurement; never parallel
unverified5V sources. Confirm the board-side voltage and avoid a current limit
that causes brownouts. With the battery stably full and negligible net battery
charging/discharging, V1 output energy approximates whole-board5V input energy,
including regulator/charger losses. A full100% indicator alone cannot prove
zero battery energy exchange. Do not extrapolate5V input results directly to
battery runtime at3.7V without conversion-path evidence.

Compare original radio-OFF, associated modem-sleep baseline and acceptedTWT at
matched brightness/CPU/BLE/refresh/workload/SOC. Average input power is sampled
V*I; energy is integrated over validated observation time. Report savings as
`(P_baseline-P_trial)/P_baseline*100%`, with repeated windows and uncertainty.
TWT wake duration is not measured current/duty cycle. Firmware0.13.1 acceptedTWT
but panicked on exit; it is suspended.0.13.2 has a callback-drain fix pending
controlled hardware acceptance. Do NOT do TWT energy comparisons until exit,
radio-off and recovery checks pass.

For another machine, obtain the official licensed Store package and use
`tools/extract_iotpower_native.ps1` under PowerShell7. It reads metadata only and
refuses replacing a differing DLL. Review the new ABI/provenance/hash before
passing it to the loader; do not blindly trust a hash for an unknown binary.
