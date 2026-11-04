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

The existing download page shows `DELTA` and actual patch bytes/percentage during
Delta transfer. It keeps the complete target version, SHA/signature, slot display,
buttons and animation. Rebuilding/verifying does not bypass the install step.
Fallback returns the progress display to full-image bytes.

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
