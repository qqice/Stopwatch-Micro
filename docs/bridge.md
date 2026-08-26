# Windows wireless usage bridge

The StopWatch receives Codex controls over Bluetooth, but Codex account limits are not part of the
stock Codex Micro protocol. `tools/stopwatch_bridge.py` reads the stable
[`account/rateLimits/read`](https://developers.openai.com/codex/app-server) method from the
desktop-managed Codex App Server and forwards only the
canonical Codex remaining percentage, reset timestamp, and reset-credit count. The default
transport is the paired Codex Micro Bluetooth HID device, so the watch can update while running on
its battery with no USB cable attached.

Install the optional HID dependency into the Python environment used to run the bridge:

```powershell
python -m pip install hidapi
```

Then keep the wireless bridge running while you want live quota data:

```powershell
.\tools\stopwatch.ps1 bridge -Transport bluetooth
```

`auto` is the default. It uses Bluetooth when the matching HID collection is present and falls back
to USB Serial/JTAG only when that collection is absent. After selecting Bluetooth, an open or write
failure is retried as Bluetooth instead of silently claiming the exclusive COM port. Install both
optional packages if you want the absence fallback from the same Python environment:

```powershell
python -m pip install hidapi pyserial
.\tools\stopwatch.ps1 bridge
```

Use `-Once` to send one snapshot and exit. From an extracted release ZIP, the equivalent direct
commands are:

```powershell
python .\stopwatch_bridge.py --transport bluetooth
python .\stopwatch_bridge.py --transport bluetooth --once
```

The bridge does not require an API key or a separate login; it starts the desktop-managed Codex App
Server under the current Windows user.

The bridge discovers the newest desktop-managed `codex.exe` under
`%LOCALAPPDATA%\OpenAI\Codex\bin`. Pass `-CodexPath` only when automatic discovery cannot select the
correct executable. It never reads or copies `auth.json`, cookies, account IDs, email addresses, or
raw access tokens.

The Bluetooth transport selects only VID:PID `303A:8360`, usage page `FF00`, usage `0001`, and opens
that HID collection with the operating system's shared HID access. Codex Desktop can continue using
the same paired device for controls. Bluetooth mode never opens COM5, so USB flashing, monitoring,
and diagnostics remain available.

For an explicit USB fallback, use:

```powershell
.\tools\stopwatch.ps1 bridge -Transport usb -Port COM5
```

Passing `-Port` with the default `auto` mode also selects USB. The COM port is exclusive on Windows,
so stop a USB bridge with `Ctrl+C` before using `verify`, `monitor`, `flash`, or another serial
program. If the bridge has not refreshed for more than two minutes, the watch marks the value stale;
after ten minutes it displays the quota as unavailable instead of treating an error as zero usage.

## One-time Codex Micro settings

Open **Settings → Codex Micro** in the Codex desktop app and set:

1. **Dial → Reasoning only** so the top arc decreases/increases reasoning effort.
2. **Analog stick Up → Toggle plan mode** (this is the stock default) for the Plan button.
3. Turn on **Use separate microphone keys**. Without it, Codex Desktop merges ACT10/ACT11 and ignores
   a standalone ACT11 event.
4. Assign the otherwise-empty **ACT11** command slot to **New Task** for the New Task button.

These mappings live in Codex Desktop. The firmware emits the official `ENC_*`, `v.oai.rad`, and
`ACT11` controls and does not patch the desktop application.
