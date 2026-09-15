# Usage history

The LAN quota service stores a private SQLite database beside its configuration
by default: `.artifacts/private/history.sqlite3`. Set `history_db` in the local
service configuration to choose another relative path. The database contains
only sample epochs, cumulative lifetime token counts, and official daily token
buckets. It never stores OpenAI credentials, account identifiers, request text,
or device tokens.

`GET /v1/history` uses the same Bearer token as `/v1/status`. It returns 30
calendar days and 24 local hours using UTC+08:00. Daily values are official
`startDate` source buckets and retain their source dates. Hours are observed differences of
the later cumulative sample, so they are not an exact hourly billing record.

`observed` requires near-full hourly sampling coverage. `partial` marks a long
gap or incomplete coverage. `missing` means there is no defensible observed
value, including a baseline-only interval. `correction` marks a negative
cumulative jump such as a reset; it is never converted to zero. Future hours
and unavailable historical days remain `missing`.

Samples older than 90 days are removed. The service does not infer historical
hours from official daily totals.

UI: Blue B cycles Command -> History -> Agent when a host is connected, and Command ->
History offline. DAYS uses green squares, HOURS uses blue squares; grey is missing and an
orange correction marker is not a numeric zero. Tap a square for the exact value and quality.
The first touch/key while idle-locked is consumed for wake. Host Agent controls remain available.

Hourly data begins when this service starts sampling; it cannot backfill prior hours from day
buckets. A partial observation is not an exact hourly bill. Keep a separate database when
switching accounts, since cumulative counters from different accounts are not comparable.

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
