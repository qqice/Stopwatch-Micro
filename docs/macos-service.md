# macOS quota service

The service also runs on macOS with Python 3.10+ and a locally authenticated
Codex App Server. No Windows account tokens need to be copied.

Deploy `tools/quota_service.py`, `tools/stopwatch_bridge.py` and
`tools/history_store.py` under a private user-owned directory. Supply
`--codex-path` explicitly (for a ChatGPT app installation this may be
`/Applications/ChatGPT.app/Contents/Resources/codex`).

Use a mode-0600 config containing `server_host` (the Mac Tailscale IPv4),
`server_port: 8765`, `device_token` (same as the watch), and
`history_db: "history.sqlite3"`. Bind only the explicit tailnet IP, not `0.0.0.0`.
Keep the config, logs and SQLite database in a mode-0700 private directory.

Install a user LaunchAgent with absolute paths in ProgramArguments:

- Python executable
- quota_service.py
- --config and the private config path
- --codex-path and the Codex executable path

Set RunAtLoad=true, KeepAlive=true, ThrottleInterval=30, Umask=63 (octal077),
HOME and PATH, a WorkingDirectory, and private StandardOutPath/StandardErrorPath.
Load with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.qqice.stopwatch-quota.plist`.
Inspect with `launchctl print gui/$(id -u)/com.qqice.stopwatch-quota`.
This is **login auto-start**, not a pre-login system daemon. If Tailscale is not
ready at login, launchd retries the failed listener startup. The Mac must remain
awake to serve; do not silently change global power settings.

Before migrating history, verify both App Servers identify the same account.
Copy a SQLite online-backup snapshot, not a live database file without its WAL.
The migration does not invent samples for any collection gap.

Probe `/v1/status` and `/v1/history` with Bearer authentication from another
Tailnet device; both must be available and fresh. An unauthenticated request
must return401. Disable ambient HTTP proxies for these private-address probes.

Provision the watch's new tailnet URL with `tools/provision_tailscale.py`,
retaining its registration key and Wi-Fi settings, then restart normally.
Validate both quota and history acceptance on the watch before considering the
migration complete. Keep the old service and URL as a rollback option.
