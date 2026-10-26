# Mosaico persistent system journal

Enabled automatically on each boot. No settings-page action is required. The
existing frozen battery trace is a separate dataset and is never cleared or
rearmed by this logger.

## Coverage and privacy

Structured records cover firmware ELF identity, reset/boot identity, cached gauge
observations before/after startup reload, periodic gauge counters and mode,
network/TCP outcomes, radio/display power transitions, OTA stages, heap health,
loss/write-error counters, and saved RTC panic metadata. Gauge observations are
sequential register reads, not atomic snapshots or proof of calibration.

ESP warning/error **static format fingerprints** are captured, not arbitrary
formatted messages. Wi-Fi credentials, SSIDs, tailnet identities/keys, HTTP
payloads, conversation text, and arbitrary format arguments are not persisted.
Bootloader/ROM console text and arbitrary `printf` output are not captured.
This is a structured diagnostic journal, not a claim that every console byte is
recorded. Exported binary/JSON files should still be treated as private.

## Persistence and retention

The existing Dhara map is reused without formatting. Old trace logical pages
0–7 remain unchanged. New index copies occupy 16/17; journal data uses logical
pages 64–8255. The **runtime** reported logical capacity must accommodate these
addresses. Unknown nonblank data is rejected, never silently erased.

8191 committed 2048-byte pages are retained, with one extra spare page for
power-safe rollover. Each page holds at most 31 fixed 64-byte records. This is a
page-count retention limit, not a guaranteed number of days; event frequency
and partially filled batches affect duration. Only old pages belonging to this
journal are recycled. Header/page CRCs and alternating indexes detect incomplete
tails. Complete orphan pages can be recovered without modifying the media on a
read. The next append commits recovered metadata before spending the spare page.

Early boot uses a bounded RAM queue. The worker stack uses SDK-supported RTC internal RAM (not PSRAM), preserving
DMA-capable DRAM for NAND and Wi-Fi. Background NAND access starts only after
the running application is VALID and the gauge/voltage/temperature gates permit
it. OTA and NAND admission share a lease. Normal samples and flushes are batched
at approximately 60 seconds; low-SOC/low-voltage gauge samples use 10 seconds,
but NAND writes remain gated at >=3300 mV. No Wi-Fi wake is requested by logging.
Failures back off instead of repeatedly hammering storage. Queue loss and
pending volatile records are explicit status fields.

**A sudden power cut can lose the uncommitted RAM tail.** No NAND operation is
attempted inside the panic handler. Existing RTC panic metadata is copied into
the next healthy boot's journal. This does not promise recovery after complete
loss of RTC power or after storage has become inaccessible.

Before/after cold-boot observations preserve their original monotonic timestamp.
Their UTC field is zero rather than incorrectly assigning a later NTP timestamp.
Later records can carry a valid UTC epoch. A host may derive approximate startup
wall-clock time from a later anchor, but must label it derived.

## Diagnosis and export

USB CDC and CRC-framed UART expose:

```
debug gauge-mode
debug syslog status
debug syslog flush
debug syslog probe
debug syslog read <sequence_hi32> <sequence_lo32>
debug syslog chunk <sequence_hi32> <sequence_lo32> <0..15>
```

`probe` emits one credential-free warning-format fingerprint for acceptance tests.
`flush` queues work and does not bypass safety gates. Explicit flush/new-read
requests may retry a resource refusal immediately; automatic retries still back off.
`read` loads a persistent
page asynchronously; `chunk` reads its immutable RAM cache. Each 128-byte response
contains sequence/index/length plus chunk and page CRCs. There is no `clear` or
`format` command in this interface. As with existing NAND reads, driver ECC
refresh can physically write flash; reads use the same safety admission.

Use `tools/mosaico_system_logs.py --port COM12 --latest 4 --output <new-private-dir>`
(add `--uart` for an authorized UART port). Identify the actual port first;
COM12 is only an example. The tool verifies each chunk and assembled page,
preserves exact binary pages, decodes numeric records, hashes files, and marks
failed/incomplete exports `complete=false`. Existing output directories are
never overwritten. Read retries are idempotent; export never resets the board,
clears records, changes gauge parameters, or starts a firmware update.

The binary record schema and field meanings are in
`main/host/mosaico_system_log_model.h`; the page schema is in
`main/hal/mosaico/mosaico_nand_log_model.h`.


### Read-only gauge engine (type 13)

`debug gauge-engine` takes no arguments. It uses the existing OTA storage lease
and refuses busy/pending OTA. The only Control selectors are fixed `0x0002`,
`0x0073`, `0x0076`; each waits 20 ms, reads the two-byte LE echo at `0x3e`, then
exactly 11/23/17 bytes starting at `0x40`, using word reads plus the final
odd byte (not a new unverified bulk-read assumption). Replies use temporary
PSRAM with safe allocation failure, avoiding a new large parser stack frame. There is no unseal, arbitrary selector,
configuration write, reset-percent or OCV command in this reader. This is a
read-request transaction, not a claim that the I2C bus receives no writes.

Identity bytes use the official big-endian DEVICE_NUMBER/FW_VERSION/FW_BUILD
layout. Only `0220/0003/0004` sets `definition_match`. Named engine fields are
emitted only for that definition and a complete matching-echo block. Other
revisions still retain raw bytes. CEDVRemCap (offset 0), EDVThreshold (16), and
all smooth words (offsets 0,2,4,6,8,10,12,14) are unsigned LE raw values;
MaxDeltaSeconds is byte 16. XML unit labels are not trusted: these values are
internal-state evidence, not physical calibration or a correction of SOC.

Existing 60 s / low-battery 10 s system-log sampling also records one type 13.
It adds 60 ms of explicit waits plus bus/lock time on that same worker, with no
new task, network request, or change to NAND safety gates. Standard and engine
samples are sequential, **not** an atomic cross-register snapshot. Boot-before
and terminal boot-after are RAM-cached alongside existing diagnostic capture
and each enqueued once; queue-full retries acknowledge only accepted prefixes.
Existing durable append/consume semantics remain unchanged. Missing boot data
has zero transport-valid bits and must not be interpreted as a zero measurement.

Record size remains 64 bytes. `args[0]` is `validMask | phase<<16 | outcome<<24`;
`args[1..10]` contain exactly raw73[23] followed by raw76[17], packed LE. No
identity bytes or echo values fit in this record. Valid-mask bits 0/1/2 mean
complete matching-echo CEDV/smooth/identity transport; bit 3 means the exact
identity definition above. Bits 4/5/6, 7/8/9, 10/11/12 respectively record
TX/echo-read/data-read success for CEDV, smooth and identity. Echo-read success
alone does **not** imply selector match. Record flags bit 0 remains epoch-valid
only and is cleared for cached boot records. Existing type 2/3 schemas and the
48-byte standard diagnostic snapshot are unchanged.

## Startup-engine diagnostic isolation

The Mosaico build option `MOSAICO_GAUGE_DEFER_ENGINE_DIAGNOSTICS` defaults to
`ON`. This retains the R47 mitigation that showed coherent capacity counting in
two cold-start observations. It is not proof of a particular private-command
side effect or of calibrated physical capacity. When enabled, the shared
engine-read entry sends none of its optional
`0002/0073/0076` group before uptime 300000 ms. It returns immediately: the UI,
standard register samples, public `0056` qualification samples, gauge parameters
and configuration-exit sequence are unchanged. Required identity reads in the
battery driver are not suppressed.

Keep this option enabled in release builds. An existing CMake cache containing
`OFF` is not changed by a new default: explicitly configure
`-DMOSAICO_GAUGE_DEFER_ENGINE_DIAGNOSTICS=ON` when reusing such a build tree.
Explicit `OFF` is retained for deliberate diagnostic timing controls, not as a
recommended operating configuration. No new firmware installation is required
on a device already running the verified R47 image with this option enabled.

Type 13 valid-mask bit 15 (`StartupDeferred`) means intentionally not acquired,
not I/O failure or a valid zero. Cached startup snapshots stay missing; later
reads must not be backfilled with fake startup timestamps. The decoder adds
`startup_deferred` only when this flag is present. The existing `gauge-engine`
command returns `SKIP` with the deadline while deferred.

Type 15 marks the first actual group submission and its return (pass or fail).
Its timestamp is software submission/return time, not a logic-analyzer bus edge.
Markers use the existing ordinary queue/flush cadence, without forcing a new
NAND-load edge. Existing loss counters apply: missing markers do not prove no
MAC was sent. Compare the fresh standard samples around these markers and
retain load/radio/qualification changes as possible concurrent explanations.

### Why isolate these reads during startup?

The optional group is not passive bus traffic: it writes the `0002`, `0073`,
and `0076` selectors to Control, waits 20 ms per selector, then reads the shared
MAC echo/data window. It does not write data-memory payloads or commit a
capacity change. Calls are serialized with configuration work by the existing
transaction mutex; no host-side interleaving or buffer overrun was found in the
reviewed path. The official driver's required firmware-version query remains.

The group used to run immediately around configuration reload, as well as in
later periodic sampling. Delaying only the optional group was followed by
coherent RawCC/RM changes in two actual 3000-to-65 mAh cold reloads; counting
continued after the group resumed. This supports startup-sensitive observation
interference, not a claim that all MAC queries stop counting. A device-internal
interaction versus altered startup timing/load is still unresolved. In
particular, no undocumented freeze/unfreeze meaning is assigned to `0073` or
`0076`. The [TI technical reference manual](https://www.ti.com/lit/ug/sluubd4a/sluubd4a.pdf)
documents public Control/MAC access, but does not establish that private
diagnostics are safe during this specific reinitialization window.
