# Mosaico safe dual-slot OTA migration plan

Status: proposal only. The active build still selects the preserved factory
partition table. No bootloader, table, otadata or OTA slot migration has been
performed. Firmware work and backup verification must precede write approval.

## Exact S31 layout

| Region | Offset | Size | Action |
|---|---:|---:|---|
| Bootloader | 0x2000 | must fit before0x9000 | rebuild with rollback; separate approval |
| Partition table | 0x9000 | 0x1000 | replace after verified backup |
| NVS | 0xA000 | 0x6000 | preserve byte-for-byte |
| OTA data | 0x10000 | 0x2000 | redundant selection state, included in migration |
| PHY | 0x12000 | 0x1000 | preserve byte-for-byte |
| ota_0 | 0x20000 | 0x3E0000 | first half of former factory region |
| ota_1 | 0x400000 | 0x3F0000 | second half of former factory region |
| ui_apps | 0x7F0000 | 0x320000 | preserve byte-for-byte |
| system | 0xB10000 | 0x2EE000 | preserve byte-for-byte |

Both app offsets are64KiB aligned, no intervals overlap, and both end before the
unchanged ui_apps boundary. Current image2,614,352bytes leaves1,448,880bytes in
smaller slot0. All published images must fit0x3E0000, regardless of inactive slot.
No NAND, eFuse, encryption key, anti-rollback fuse or external module is changed.

This is ESP32-S31, not the repository-root StopWatch/S3 build. Use only
`.artifacts/mosaico` and `boards/mosaico`. Table0x9000/loader0x2000 are mandatory.
PSRAM-XIP remains enabled; do not switch the image to Flash-XIP or install an
older SDK. The current local S31 build disables bootloader app rollback. The
existing live loader has not been proven to support rollback, so the plan uses
an explicitly approved matching rollback-capable loader, not an assumption.
The existing cf58a33f... guard hash is the **whole0..0x9000 prefix**, not the bare
loader file hash. A same-length live loader span was28f9b44d..., while the local
22,272byte loader file was4610d3c2...; these differ. Do not compare unlike spans
or write the root/S3 bootloader at0x0.

## Backup and restoration gate

The owner explicitly stopped the full-NOR backup requirement. Back up ONLY
sectors affected by the final approved write manifest. For each image, round
its written span to 4KiB erase boundaries; include complete partition-table
and otadata sectors when touched. Do not back up unrelated NVS/PHY/ui_apps/system
or NAND, and do not erase an entire app slot merely to install a smaller image.
Prefer leaving the working app at0x20000 untouched as the initial ota_0 baseline.

Previously acquired private prefix bytes can be reused ONLY after device MD5
matches their exact range. Export the final sector ranges, check each device MD5
and local SHA256, and save their offsets/lengths and exact restore commands.
A .partial filename alone proves nothing; a validated range is a range backup,
not a full-device backup. Keep dumps restricted/gitignored because touched
regions may contain sensitive data. Backup excludes volatile gauge RAM, NAND
and eFuses; existing gauge safety policy is unchanged. Final app backup length
cannot be fixed until the OTA-capable artifact and write manifest are frozen.
Backup is read-only. Before destructive writes, approve the exact bootloader,
partition-table, otadata and former-factory-region ranges. Maintain external USB
power. Bootloader/table migration itself is NOT power-cut safe; ROM recovery plus
the verified written-sector backups are the fallback. Dual-slot safety applies after migration.

## Firmware and migration sequence

1. Implement the OTA app-side path and health confirmation before changing the
   active table. Build separate S31 OTA artifacts and matching rollback loader;
   verify image chip/name/XIP/layout/size and do not enable anti-rollback eFuses.
2. Preserve a known-working current image as the recovery baseline. Before any
   migration, verify both app slots' intended contents and all protected hashes.
3. Stage and verify baseline and OTA-capable images within the former factory
   area. Write new loader/table/selection only under the approved migration plan.
   The final sequence must leave a bootable validated baseline until selection;
   do not claim atomicity for loader/table replacement.
4. Boot the selected OTA-capable image. Confirm main loop/watchdog, internal heap,
   screen submission, Function input setup and safe required HAL status. Do not
   require Wi-Fi/server availability or an arbitrary battery percentage to mark
   a valid app, because an offline account service must not cause firmware rollback.
5. For a candidate upgrade, download into the INACTIVE app slot, verify chip,
   board/layout/version/length/SHA and authenticated publication, then complete
   `esp_ota_end` and change boot selection. Never overwrite the running app.
6. Candidate starts pending verification; it must mark valid only after its
   bounded health checks. Reset/crash/failure before acceptance must return to
   the previous known-working app. Test both directions, invalid/truncated images,
   failed network downloads, and app-health failure before declaring safe OTA.

OTA artifact authentication must be independent of OpenAI credentials/Tailscale
auth keys. Prefer signed manifests/images over authenticated tailnet transport;
keep signing keys outside Git. Current Secure Boot is disabled, so application
signature checks are not an immutable physical-attacker-resistant boot chain.
Do not label an OTA implementation finished until inactive-slot update and real
boot/rollback acceptance have run. Endpoints/client/protocol are not implemented yet.

## Runtime USB fallback

The monitor has a1200baud volatile-ROM request. `tools/mosaico_enter_download.py`
checks firmware, awake phase and completed/noncritical gauge transaction before
requesting it. The first controlled trial reproduced Windows descriptor failure;
no flash write occurred. Thus automatic mode switching is NOT accepted as reliable.
For the initial backup/migration use manual BOOT if enumeration fails. Successful
normal OTA later avoids ROM/USB mode switching, while manual BOOT remains recovery.

Sources: [ESP-IDF6.1 S31 OTA](https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32s31/api-reference/system/ota.html),
local S31 config/partition metadata and current device prefix readbacks.


The USB helper is experimental, not firmware-side atomic reset admission.
It checks live SEALED/CFG0/CAL0, limits preflight time and rechecks awake phase,
but a physical input/concurrent console command can race host snapshots. Do not
use it during manual gauge maintenance; use manual BOOT for this migration.
The hardware backup is still incomplete; successful host mock-test output must
not be interpreted as a verified physical NOR backup.

`tools/mosaico_backup_regions.py` accepts a frozen source snapshot, an explicit
JSON list of `regions` (`offset`, `length`, both4KiB-aligned), a prepared private
empty output directory, and the explicit ROM port. It validates this unit and
only queries ROM MD5 per range; it does not stream-read, upload a stub, erase,
reset or write flash. All requested hashes must match before exclusive file
exports; the resulting manifest records exact rollback offsets and hashes.
Do not run it until final write artifacts define the actual sector ranges.
