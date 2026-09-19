# Usage history

The quota service keeps a private SQLite database beside its configuration.
It contains cumulative account observations, reported daily buckets, and optional
local completion events (timestamp, token count, SHA256 deduplication key, source).
It never stores prompts, tool output, account credentials, or device tokens.

`GET /v1/history` uses the same Bearer token as `/v1/status`. There are 30 days
and 24 local hours (UTC+08:00). Reported daily buckets retain upstream `startDate`;
this is not the weekly quota reset period. Upstream daily/cumulative counts may
lag. Repeated fresh HTTP responses do not mean the underlying counters are current.

**Do not differentiate delayed lifetime counters into consumption hours.** On
2026-09-19 the counter stayed unchanged from16:00 through21:00 despite active use.
Zero deltas do not prove zero usage; later catch-up deltas cannot be assigned to
that arrival hour. `reported_delta` is retained for diagnostics only. Without a
local completion event, hourly `tokens` is null (`pending` or `missing`), never a
fabricated zero. Old cumulative observations remain in SQLite for audit.

Optional local collection uses Codex `event_msg/token_count` completion timestamps
and `last_token_usage.total_tokens`. Repeated cumulative totals are skipped. A
SHA256 of timestamp and usage counters deduplicates copied/forked logs and retries
across both machines. No chat text or paths leave the originating computer.
Totals include cached input as reported by Codex; cached/reasoning components are
not added a second time. Completion-time attribution is not a measurement of
how a long request consumed tokens continuously across an hour boundary.

`local` values cover only connected Windows/Mac logs, **not the whole account**.
Hours use these events; today's daily cell uses the local calendar-day sum,
with upstream value separately retained as `reported_tokens`. Older available
official daily buckets remain separate; official and local totals are never added.
No-event hours stay unknown because complete account coverage cannot be proved.
Weekly quota percentage is a distinct metric, not a fixed Token conversion.
Switching accounts requires separate log scope/databases; old local logs do not
carry enough account identity to automatically establish historical ownership.

Deployment:
- Mac service `--collect-local`: incremental scanner under the existing LaunchAgent.
- Windows `tools/local_usage_agent.py --db <private-cursor-db> --ssh-host MacMiniM4`:
  incremental scanner, durable outbox, authenticated SSH ingest (no HTTP write API).
  Configure a hidden user login startup entry. See agent `--log-file` and `--once`.
- Ingest command paths target the documented Mac deployment. Edit for other hosts.
- Startup backfills up to30 days of existing local logs; both sources retain numeric
  cursors privately. Local events/account observations retained90 days. Restarts,
  retries and archived/copy logs are idempotent. Partial JSONL appends wait for newline.
- Heartbeat timestamps are exposed as `local_sources_last_seen`; a fresh Mac source
  does not imply Windows is online. Watch details always label local coverage partial.
- Current deployment uses Windows `Startup/StopWatch-LocalUsage.vbs` and Mac
  `com.qqice.stopwatch-quota`; both are login auto-start, not pre-login services.

UI: B opens History. DAYS is green, HOURS blue. Tap a square for source and exact
count. Pending is not zero; reported daily values may lag. First locked touch wakes.
Changing mode/selection remains entirely local to the watch's cached snapshot.

Firmware diagnostics: `debug history days`, `debug history hours`, `debug history select N`,
`debug history-selftest`. The rejection selftest checks malformed arrays, excessive nesting,
and payload size while retaining the last valid cache. `tools/test_history_runtime.py` compares
one official day on the device against the authenticated host response.

## Local interaction and grouping

Both the 24-hour and 30-day arrays are fetched together by the background
network task and stored in RAM. Selection uses the current page snapshot with
no HTTP operation or shared-cache copy. Mode changes reuse both cached arrays.
Background revisions may refresh the snapshot independently.

Selection invalidates only the old/new selected cells and changed detail text.
The custom drawing callback skips cells outside the current LVGL clip. Rounded
cells show only day-of-month or time; month labels (AUG/SEP etc.) and day labels
(MM/DD) appear once at each group's first cell, in the gaps below the cells.
The selected detail retains the full date and exact token count.

`python tools/test_history_latency.py --port COM24` measures command dispatch
to a completed display frame; it is not an optical finger-to-pixel measurement.

With real history cached on the device, seven selections took 140–281 ms and
two mode switches took 281/407 ms from USB command dispatch to completed frame.
Nine samples averaged 218.6 ms; this includes USB/debug overhead and is not an
optical measurement. Exact daily token, hourly selection and missing-data
checks passed. Evidence: `.artifacts/history-latency-real-cache.log` and
`.artifacts/history-ui-cache-regression.log`.
