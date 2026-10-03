# macOS quota service

The service also runs on macOS with Python 3.10+ and a locally authenticated
Codex App Server. No Windows account tokens need to be copied.

Deploy `tools/quota_service.py`, `tools/quota_dashboard.py`, `tools/stopwatch_bridge.py` and
`tools/history_store.py` under a private user-owned directory. Supply
`--codex-path` explicitly. Prefer an independently installed, authenticated CLI
(this Mac uses `/Users/qqice/.local/bin/codex`) so desktop app updates cannot
move the service executable. An app-bundled CLI can be an explicit fallback.

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

## Detailed quota snapshots

`GET /v2/status` uses the same Bearer authentication as the original endpoints.
It preserves each official `rateLimitsByLimitId` bucket and both quota windows,
including remaining basis points, actual window duration and UTC reset epoch.
Optional plan, limit state, workspace credit balance and earned-reset count are
shown only when returned. Credits are **not tokens**; no percent-to-token
conversion is performed. Neither earned resets nor purchases can be triggered
through this read-only service.

The bounded response contains at most eight buckets (canonical Codex first),
with `total_buckets`/`truncated` indicating overflow. Missing windows and unknown
reset counts remain null, not 100% or zero. Reset time/duration can be unknown
while a valid percentage is retained. Local cache age uses monotonic time;
snapshots older than 120 seconds return HTTP503. Device cache explicitly marks
stale data and stops displaying quota values after ten minutes without updates.

`/v1/status` remains the unchanged single-window StopWatch contract; `/v1/history`
retains official daily and observation-time hourly semantics. The documented
[App Server interface](https://learn.chatgpt.com/docs/app-server) supports these
quota fields; which buckets/windows exist depends on the actual account response.
