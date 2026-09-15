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
