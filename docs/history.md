# Official usage history

The service polls Codex app-server `account/usage/read` and
`account/rateLimits/read` every 60 seconds. This is the official account app-server
interface, not the API-platform organization usage API. Local Windows/Mac session
logs are no longer collected or included in responses. Historical local event
rows and legacy import tools remain inactive for audit/rollback only.

`GET /v1/history` requires the same Bearer token as `/v1/status`. It returns 30
calendar days and 24 hour placeholders (display timezone UTC+08:00). Daily labels
retain upstream `startDate`; they are not the weekly quota reset period.

## Delayed statistics policy

- Reconcile every returned daily bucket on every poll, including old dates and
  downward corrections. Missing buckets do not erase previously reported values.
- Missing days are `tokens: null, quality: pending`, never fabricated zeroes.
- Previously reported daily values remain provisional: upstream does not provide
  a finality marker or a reliable data-as-of timestamp. Midnight does not finalize
  yesterday. Explicit official zeroes remain zeroes.
- Preserve the last valid SQLite cache on upstream errors or empty/malformed
  responses and retry at the next poll. Quota and history polls are independent.
- `last_successful_poll_epoch` measures retrieval, not data freshness.
  `last_value_change_epoch` measures when the observed official response changed,
  not when tokens were consumed. `cache_status` becomes `stale_cache` after 180s;
  `upstream_data_delay_seconds` is null because actual lag is unknown.
- `daily_revisions` keeps observed corrections for 90 days. Cumulative observations
  are also retained 90 days. Previously known daily buckets survive response gaps.

**The official interface supplies no true hourly token buckets.** All hourly
`tokens` are null and `hourly_supported` is false. The UI explicitly explains this.
`reported_delta` is diagnostic counter arrival data only, not hourly consumption.
A delayed catch-up must not be assigned to the hour it arrives. Quota percentage
cannot be converted to tokens, either. Official daily counts include whatever
upstream reports; we do not reinterpret their accounting or timezone.

The current deployment disables Windows `Startup/StopWatch-LocalUsage.vbs` and
removes Mac `--collect-local`. The quota LaunchAgent remains login auto-start.
Neither local event heartbeats nor old local rows can freshen the official cache.

UI: B opens History. DAYS is green; HOURS is unsupported/pending. Selection and
mode changes use the cached in-memory snapshot. First locked touch wakes. Locked
refresh only shuts radios down early after BOTH quota and history succeed; failed
history retries inside the existing bounded 90-second window, then tries again on
the next 5-minute cycle. This preserves battery limits without treating quota-only
success as successful history refresh. This corrects premature shutdown only;
it does not guarantee tunnel recovery. Repeated hardware tests still reproduce
intermittent MicroLink warm-reconnect failures, even after successful full map
retrieval. Network failure and an upstream pending day are separate conditions.
`Quota-Map` logs snapshot field presence/counts, without credentials or peer keys,
to support continued diagnosis. A successful cold boot is not proof of reconnect
reliability.

## Future browser frontend (not implemented here)

Reuse the authenticated status/history APIs and their cache/source/availability
metadata. Keep OpenAI credentials on the Mac backend, not in browser JavaScript.
Serve the frontend same-origin behind Tailnet access and HTTPS; do not expose the
current plain-HTTP service or device Bearer token on the public internet. A web UI
should display last poll, last observed change, pending days and unknown upstream
lag separately. Do not draw an hourly consumption chart from `reported_delta`.

Firmware diagnostics: `debug history days`, `debug history hours`,
`debug history select N`, `debug history-selftest`.
`tools/test_history_runtime.py` compares a reported day with the host response.

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
