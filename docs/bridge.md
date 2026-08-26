# Windows usage bridge

The StopWatch receives Codex controls over Bluetooth, but Codex account limits are not part of the
Codex Micro HID protocol. `tools/stopwatch_bridge.py` reads the stable
[`account/rateLimits/read`](https://developers.openai.com/codex/app-server) method from the
desktop-managed Codex App Server and forwards only the
canonical Codex remaining percentage, reset timestamp, and reset-credit count over USB
Serial/JTAG.

From the repository, keep this command running while you want live quota data:

```powershell
.\tools\stopwatch.ps1 bridge -Port COM5
```

Use `-Once` to send a single snapshot and exit:

```powershell
.\tools\stopwatch.ps1 bridge -Port COM5 -Once
```

From an extracted release ZIP, install `pyserial` into the Python environment you will use. An
ESP-IDF or `esptool` Python environment normally already includes it:

```powershell
python -m pip install pyserial
python .\stopwatch_bridge.py --port COM5
```

Add `--once` to the direct command for a single update. The bridge itself does not require an API
key or a separate login; it starts the desktop-managed Codex App Server under the current Windows
user.

The bridge discovers the newest desktop-managed `codex.exe` under
`%LOCALAPPDATA%\OpenAI\Codex\bin`. Pass `-CodexPath` only when automatic discovery cannot select the
correct executable. It never reads or copies `auth.json`, cookies, account IDs, email addresses, or
raw access tokens.

The COM port is exclusive on Windows. Stop the bridge with `Ctrl+C` before using `verify`,
`monitor`, `flash`, or another serial program. If the bridge has not refreshed for more than two
minutes, the watch marks the value stale; after ten minutes it displays the quota as unavailable
instead of treating an error as zero usage.

## One-time Codex Micro settings

Open **Settings → Codex Micro** in the Codex desktop app and set:

1. **Dial → Reasoning only** so the top arc decreases/increases reasoning effort.
2. **Analog stick Up → Toggle plan mode** (this is the stock default) for the Plan button.
3. Assign the otherwise-empty **ACT11** command slot to **New Task** for the New Task button.

These mappings live in Codex Desktop. The firmware emits the official `ENC_*`, `v.oai.rad`, and
`ACT11` controls and does not patch the desktop application.
