# Optional Mosaico Delta OTA

Delta changes the transfer, not firmware authorization or the A/B layout. The
device still constructs a complete image in the inactive application slot and
uses the existing signed manifest, image SHA-256, SDK image validation, version
check, installation consent/power checks and boot-health rollback.

## Enable on the quota server

Install `tools/requirements-delta.txt` into a **project-private virtualenv** and
run `quota_service.py` using that interpreter. No signing key is needed by the
server. Add these optional fields to its existing private configuration:

```json
{
  "ota_delta_enabled": true,
  "ota_delta_base_dir": "ota-bases",
  "ota_delta_max_percent": 60
}
```

The directory is relative to the configuration file and must remain within its
parent directory. Existing bind addresses, device token and other configuration
remain unchanged. Restart the service after changing the configuration. The
default is `ota_delta_enabled: false`; setting it to false forces normal full
OTA without removing the original update routes.

With Delta enabled, the server atomically archives each verified current release
as `<base directory>/<full image SHA-256>.bin`. It never overwrites an existing
different file. To seed an older release, copy its original, complete
`firmware.bin` into that directory using its SHA-256 as the filename. No image is
selected solely by a version string. Missing or corrupted baselines cause full
OTA, not a failed update. Archives are not automatically deleted: keep the
baselines used by deployed devices, and remove obsolete ones only when desired.

## Protocol and bounded fallback

1. The original `/v1/ota/manifest` remains unchanged, including its P-256
   signature. Old clients continue to download the full image.
2. A capable client hashes its **complete running image**, including its appended
   image digest, and requests `/v1/ota/plan?base=<SHA>&target=<SHA>`.
3. The server generates a raw `detools`/heatshrink patch on demand, independently
   replays it to the target, and caches at most two patches. At most one generator
   runs at a time. All routes retain the existing Bearer authentication.
4. A patch is offered only if it is at most 2 MiB and **strictly smaller than** the
   configured percentage of the full image (default 60%). Disabled/unavailable
   codec, unknown baseline, insufficient savings or busy generation returns a
   full-download plan. No SDK/toolchain changes are needed.
5. `/v1/ota/delta?sha256=<patch SHA>&offset=<offset>` uses the original 4096-byte,
   Base64-JSON chunk envelope. The device checks exact base/target hashes and
   lengths, stores the bounded patch in PSRAM and verifies its SHA-256 before
   decoding. Optional-buffer allocation failure uses full OTA instead.
6. Espressif's vendored `esp_delta_ota` reconstructs from the running slot.
   Reads and output lengths are bounded; decoded bytes pass through the existing
   internal-RAM write buffer, never a PSRAM NOR-write source. The reconstructed
   image must match the signed target hash before normal final verification.
7. A patch/network/codec failure can restart the full transfer **once for the same
   authorized target**. It aborts only the inactive-slot write. A real write,
   power or safety failure that has revoked the active transaction is not revived.
   The existing download deadline still applies; loss of power cannot be hidden
   by fallback. No running-slot write or automatic ROM/BOOT operation is added.

The patch is a transport optimization, not a separate signing authority. The
server holds no firmware signing key. The decoder and parser still treat patch
bytes and JSON as untrusted inputs; size, nesting, offset and output limits apply.

## UI and compatibility

The OTA page changes its presentation with the actual operation:

- Preparing and waiting for the server's Delta plan: animated point-matrix loader,
  current/target versions, **no invented percentage or presumed FULL mode**.
- Downloading: its own progress bar using actual transferred bytes (patch bytes
  for Delta, full image bytes for full OTA).
- Local merging: a separate cyan progress bar using complete image bytes written
  to the inactive slot, **not** the already-100% patch download count.
- Final image verification and installation: an animated loader where the SDK
  does not expose measurable progress. Boot-health checking retains its existing
  measured progress.
- Ready to install/reboot: verified image metadata and the existing confirmation
  button. Hash/signature rows are hidden when irrelevant to the active operation;
  versions and other controls use the space instead.

A purple point-matrix `DELTA` or orange `FULL` wordmark appears only after the
mode is known, and follows a full-transfer fallback. The original controls stay
visible but dimmed when unavailable; layout changes do not change consent, power,
signature or rollback policy. The completed/idle page never fabricates a mode
that was not retained across reboot.

Status headings also use the point-matrix font. Slot numbers have a larger,
vertically centered right column; the slot name and chip icon occupy the left
column. The awake battery icon is fixed to the top-right margin independently of
percentage text width. Terminal success keeps the existing green check, and
terminal failure uses a red cross on the same 9×9 dot grid and 88-pixel widget.
After 15 seconds without interaction, success/failure returns to the quota page.
Touch/settings interaction postpones this; an active backend operation, pending
install, pending reboot or boot-health check is never dismissed by that timer.

### Merge/display coordination

The Mosaico display uses PSRAM DMA, while reconstruction densely reads/writes
NOR. To remove their overlap during merge, each small decoder batch takes the
existing LVGL mutex and drains queued display DMA through the public
`esp_lcd_panel_io_tx_param(io, -1, nullptr, 0)` operation before flash work. In the
pinned IDF 6.1 SPI implementation this drains the queue without sending any LCD
command/data, so brightness and display timing are unchanged. The batch releases
the GUI afterward, allowing progress updates between batches. Finalization and
the tail write use the same window. Callbacks cannot access flash without a held
window, including failure cleanup. If the optional window cannot be acquired,
the existing full-download fallback remains available.

The 256-byte patch batch is a byte bound, not a guarantee of wall-clock lock
duration. SDK SPI-drain waits retain their existing hardware-driver behavior.
This is a targeted mitigation for reported merge-region artifacts, not proof of
the sole cause or a claim to fix unrelated cold-start/standby display issues;
real-device visual acceptance remains necessary.

`ota_presentation_model.h` is the shared, side-effect-free layout/progress model.
`tools/preview_ota_stages.py` exports that actual model and point-matrix primitives
for inspection before deployment; ordinary preview text approximates Montserrat
and is not a device screenshot. Compile-time tests check all visible rectangles
for bounds and overlap across stages, modes and slot combinations.

An older server without `/plan`, or a server with Delta disabled, is compatible:
the device uses the original full-image route. The first installation of a
Delta-capable client on an older firmware must itself be a full OTA. Future
updates can use Delta automatically when the server has the matching baseline.

Delta reduces network bytes; it still writes the entire inactive image. It does
not promise fewer NOR erases/writes or a particular end-to-end speedup.

## Verification

`tools/test_ota_delta.py` checks server policy, authentication, thresholds,
immutable chunks, concurrent generation and exclusive archival. Existing OTA
tests continue to cover full updates and protection boundaries.
`tools/package_delta_native_tests.py` packages the **actual** production decoder,
vendored codec and cJSON for the ASan/UBSan harness under
`tools/delta_native_harness/`. It uses real SHA-256 with host SDK shims; this does
not substitute for real device flash, power or boot-rollback validation.

Pinned upstream and the local signed-offset overflow fix are documented in
`components/esp_delta_ota/UPSTREAM.md`. No ESP-IDF installation is modified.
