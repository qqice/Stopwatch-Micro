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
debug syslog read <sequence_hi32> <sequence_lo32>
debug syslog chunk <sequence_hi32> <sequence_lo32> <0..15>
```

`flush` queues work and does not bypass safety gates. `read` loads a persistent
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
