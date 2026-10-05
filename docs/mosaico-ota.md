# Mosaico safe dual-slot OTA migration plan

Status: unattended signed OTA accepted on hardware (2026-10-05).
Device-initiated update to0.6.1, failing-candidate rollback and persistent failed-hash
suppression were observed using read-only USB diagnostics, with no update/wake trigger.
Factory build/layout remains separate from the new rollback-enabled OTA build.

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
boot/rollback acceptance have run. Endpoints/client/protocol are implemented and the bounded hardware update/rollback acceptance passed.

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

## Implemented update protocol

The existing authenticated quota service serves `/v1/ota/manifest` and 4096-byte
JSON/base64 chunks bound to the release SHA256. A loaded release is immutable
until service restart. Firmware verifies the compiled P-256 public key against
canonical metadata before `esp_ota_begin`, then enforces sequential offsets,
size, final SHA256, SDK image validation and the exact alternate-slot layout.
Only the network owner transfers data, so Wi-Fi/tailnet pause, CPU downclock and
deferred gauge work cannot race OTA. Unsigned network content is never booted.
No Secure Boot/anti-rollback eFuses are enabled; signed old releases remain usable.

An explicit runtime command `debug ota-update CONFIRM_EXTERNAL_POWER` triggers
network OTA; the human confirms USB/external power, not an unreliable SOC value.
No1200baud touch or physical BOOT is needed after the initial migration.
`tools/mosaico_ota_update.py --port COM12 --confirm-external-power` waits through
normal CDC re-enumeration and accepts only the opposite slot in VALID state.
Automatic signed checks are implemented; no private signing keys are placed on the Mac server.

Startup health requires real app view/key manager/serial initialization plus
completed UI execution, board I2C/display/touch/buttons, a submitted frame and
healthy internal/PSRAM heaps. A20sec continuous healthy window marks VALID; a
90sec independent timer rejects a stuck candidate. Health outcomes share a lock.
Server availability, Wi-Fi connectivity and battery percentage are not required
for startup acceptance. Pending health suppresses idle downclock/radio pause.

## Bounded initial migration and recovery

`tools/mosaico_ota_migrate.py` freezes images and an exact plan into a restricted
private bundle. It checks chip/project/checksum/digest/PSRAM-XIP, rollback loader
config, preserved partition locations and a known-running baseline. Candidate is
staged at0x400000; the existing0x20000 app is left untouched. Loader/table/selection
follow only after the candidate verifies. Initial selection is ota0 seq1 VALID
and ota1 seq2 NEW; the loader performs the NEW-to-PENDING transition on first boot.

Before writes, only their actual erase sectors are saved with live ROM MD5 and
local SHA256. The subsequent same-candidate OTA acceptance test writes app0, so
its exact erase span is also backed up now and bound to candidate SHA/length.
A larger test image requires its actual sector coverage; no blanket full-slot
backup or erase is used. The source is the already verified private prefix;
no further bulk dump is read. All flash regions outside the initial write sectors
have before/after MD5 protection, including the original working app0.

Esptool high-level write retries are disabled to prevent an implicit reset during
partial migration. Any failed write/hash check stops before the next image and
retains ROM recovery. Private rollback files record exact offsets, lengths and
restore ordering; recovery writes require separately confirmed physical identity.
## Automatic policy and current acceptance boundary

### Staged action UI (0.8.0 and later)

The default is automatic **discovery**, not automatic installation. Background
checks do not publish busy Checking or wake the locked display. The transparent
action tile has an orange dashed frame and dot text; disabled actions remain
visible in dark orange. Back/LATER buttons are removed. Function and horizontal
swipes navigate the three pages with a200ms panel slide.

CHECK explicitly queries a signed release when Wi-Fi/tailnet is ready. A valid
offer displays DOWNLOAD and actual version/hash/slot information. DOWNLOAD
binds the full displayed SHA256 and permits battery power, but requires a valid
sealed gauge, CFG/CAL clear and >=3500mV before erase and periodically during
writes. It stops at ReadyInstall after full hash/SDK/version validation, without
selecting a boot slot or rebooting.

UPGRADE binds that verified image and requires fresh >=3900mV plus enumerated
USB or positive charging current>3mA and a valid sealed gauge. A full battery
on USB remains eligible with zero charging current. It commits the candidate
journal and boot selection, then stops at ReadyReboot. REBOOT explicitly starts
the selected image; a subsequent hardware power cycle also applies it. After
healthy startup the action returns to CHECK. ReadyInstall/ReadyReboot allow
navigation and retain the verified image; discovery cannot overwrite it.
Download/verification/install/boot-health keep navigation locked. INSTALL and
REBOOT are handled before network gates and need no network after verification.

The next page shows actual downloaded bytes/percentage. Full-image SHA256,
SDK image validation and the signed version check complete before the verified
installation page appears. Installation has an activity animation, not a made-up
flash percentage. After reboot, health-check progress covers the real acceptance
window; failure retains the existing rollback behavior. Download already writes
the inactive slot, so installation selects it and REBOOT applies it.

For authorized USB maintenance, `debug ota-bypass CONFIRM_EXTERNAL_POWER`
queues the full download/install/reboot pipeline without screen approvals. The existing
`debug ota-update CONFIRM_EXTERNAL_POWER` remains compatible. Neither command
bypasses power/gauge checks, signature validation, image validation or rollback.
Normal OTA needs no ROM/BOOT transition. Fully automatic approval remains an
opt-in NVS policy; it is not enabled by default.

The host observer can issue that command and verify the alternate slot becomes
VALID: `python tools/mosaico_ota_update.py --port COM12
--confirm-external-power --bypass`. Use the actual runtime CDC port; this does
not reset the board into ROM or touch the bootloader/partition table.

Schema2 signs the version from the actual SDK application descriptor together
with image size/hash and fixed board/layout identity. The firmware/server still
accept schema1 for bootstrapping older devices (`--schema1` on the signing tool).
Schema1 does not authenticate a version before download: the UI does not invent
one. Private signing keys remain local and must never be copied to the server.

The device checks a signed release once per hour using an already-online quota
window (including the first eligible window after a VALID boot). It does not
wake radios solely for OTA. Default installation policy is conservative: sealed
valid gauge, CFG/CAL off, measured cell voltage at least3900mV, plus enumerated
USB or positive charging current over3mA. This is not a precise VBUS/SOC meter;
pure-battery operation defers automatic installation. Manual confirmation remains
available as an explicit maintenance override.

The complete current image (including the appended digest) is hashed once per
boot. Identical releases are skipped. An independent `mosaico_ota/attempt` NVS
record is committed before boot selection; rollback suppresses that same hash
on the next boot, preventing a repeated failure/reinstall loop. It records only
the most recent attempt, not a permanent historical blacklist. Power failure
between journal commit and selection can conservatively suppress an unstarted
release; an explicit manual retry is permitted. Existing network credentials are
not changed by this separate namespace.

A real transfer panic was captured in retained36-byte RTC state and mapped to
lwIP `tcp_output` NULL+0x0c. WireGuard decrypted RX bypassed its configured
`tcpip_input` callback with a direct `ip_input`; the project patch now dispatches
through `netif->input` with success-only ownership transfer. SDK/TLS/source-IP/
replay rules are unchanged. Fresh dependency replay and host ownership checks pass.

The first automatic candidate then failed in startup VFS operations-table access.
A Mosaico-local compatibility wrapper now retains the TinyUSB static ops table in
internal RAM, without patching SDK/managed code. This is a targeted cache-safety
mitigation, not a proven comprehensive explanation of that startup exception.
RTC capture/query, wrapper ABI/link layout and builds have been checked, but the
new candidate passed physical startup and bounded unattended upgrade/rollback tests.
A guarded1200baud request still fails Windows ROM re-enumeration; initial recovery
uses physical BOOT. This is separate from network OTA and is not an accepted
unattended update transport.

Do not claim unattended OTA complete until a read-only observer verifies that
publishing a signed new release, WITHOUT a USB update/wake command, produces a
VALID alternate-slot boot, and that a controlled failing candidate returns to
its healthy predecessor and is not repeatedly reinstalled. The accepted0.6.1 release is restored on the Mac after the controlled failure test.
Long-term reliability and all possible power-loss points are not implied by this test.
## Hardware acceptance, 2026-10-05

1. Exact-sector-only app1 and selection stage verified, including untouched-region
   hashes; no full NOR dump, bootloader/table rewrite, NAND or eFuse operation.
2. Device independently checked the already-online window, verified/downloaded
   the signed0.6.1 release and booted ota0.20sec health confirmation became VALID.
   The observer issued only ping/status/ota-status/boot/panic, not update/wake.
3. A signed controlled failure candidate was published after the new boot. The
   device independently downloaded it; two software-reset boots returned to the
   healthy0.6.1 ota0. The candidate never became accepted. Its exact same hash
   was reported already_attempted after reboot and remained suppressed for95sec.
4. Latest four boot records were stage7 with reset reasons1/3/3/3 (no new PANIC),
   retained panic query returned no_saved_panic. The failed release was withdrawn;
   the Mac now offers the healthy0.6.1 signed release again.

The brief fault marker was not captured by the USB reconnecting observer; the
acceptance uses complete authenticated transfer, software boot records, healthy
predecessor/version return and post-reboot persisted-hash suppression. A partial
USB diagnostic line caused an observer-only early exit; required-field parsing
was used for the resumed verification. No extra firmware trigger was sent.

Signing key stays private on the Windows workstation, never in Git or on the Mac.
Preserve `.artifacts/private/mosaico/ota-signing/signing-key.pem`; future releases
must be signed with its matching key. Build using the dedicated rollback-enabled
OTA sdkconfig, not the legacy factory profile; its signature is approval to run
that complete firmware. Manual ROM recovery remains distinct from unattended OTA.

### Confirmation UI deployment, 2026-10-05

0.7.0 bootstrapped from 0.6.1 using a schema1 signed release over network OTA,
then 0.7.1 exercised schema2 and `ota-bypass` over runtime CDC. Both alternate
slots reached VALID without ROM/BOOT operations. Before the bypass, the new
offer remained idle with zero bytes downloaded across two observations 30s
apart. Final 0.7.1 runs in ota0; no saved panic, quota data fresh, server status
and authenticated OTA endpoints HTTP200, unauthenticated OTA HTTP401.

Both S31 builds/image validations and S3 syntax regression passed; 129 host
tests (one skipped) and seven automatic OTA source tests passed. These do not
establish on-screen visual quality or physical UPGRADE/LATER touch acceptance;
those require the user's observation. The network/USB bypass flow is hardware
verified, and the screen callback/hash binding is covered by source review/tests.

## Network panic isolation and hardening (0.7.3+)

The exact application ELF maps one previous panic to `wifi_nvs_load`. Wi-Fi
storage was changed to RAM only after init, leaving its default init-time NVS
path enabled. Mosaico now sets `wifi_init_config_t.nvs_enable=0` before init;
application-owned credential/configuration NVS is unchanged.

The exact S31 rev0 ROM ELF maps a second panic to `tlsf_malloc`, with a failed
read in the PSRAM virtual window. Do not subtract an address constant to map
ROM PC values into the application ELF. This identifies an allocator/heap-read
failure, not the original source of corrupted/unavailable metadata. Symbols came
from [Espressif ROM ELF releases](https://github.com/espressif/esp-rom-elfs/releases/tag/20260528),
not an SDK patch or toolchain replacement.

OTA uses one reusable4KiB INTERNAL|8BIT source buffer, with backend checks on
both ends before `esp_ota_write`. Allocation failure is reported before erase.
The SDK already supports external-input32-byte bouncing, so external buffering
alone was not proved to be the cause. Internal buffering also reduces repeated
flash bus/cache transitions. Heap integrity probes run only at OTA start, each
64KiB and before final verification. Existing rollback/signature/hash/power
checks are unchanged. A44-byte RTC panic record now also stores numeric phase
and byte offset; its writer/wrapper are IRAM and context scalars are DRAM.

Phase codes:0 idle;1 allocation/probe;2 manifest HTTP/parse;3 validation/erase;
4 periodic probe;5 chunk HTTP;6 JSON/decode/free;7 write/hash;8 final verification;
9 candidate journal/selection/restart. No payload/credentials are retained.
`mosaico_ota_update.py --log <private-path>` optionally saves serial evidence;
runtime logs may be sensitive, so do not publish that file.

The definitive origin of the old TLSF failure remains unproved. Acceptance must
record real alternate-slot transfers and absence of new heap/panic failures,
not claim that source checks alone establish a universal fix.

### Hardware regression, 2026-10-05

The0.7.3 bootstrap was written only to inactive ota0 and8KiB selection metadata,
with current ota1 preserved and all complementary flash MD5 unchanged. Exact
selection metadata was backed up; no loader/table/NAND or credential writes.
Then two complete signed network upgrades passed without ROM/BOOT commands:
0.7.3 ota0 ->0.7.4 ota1 ->0.7.5 ota0. Both downloaded2,652,896 bytes and became
VALID after health checks. Logs confirmed internal4KiB source allocation; no
heap-check failure or new saved panic was observed. Final gauge remains sealed,
Design/FCC65mAh, normal quota fresh; boot46/47 were normal software resets.

Final S31 builds/image validation,142 host tests (one skipped), seven automatic
source tests and S3 syntax regressions passed. IRAM breadcrumb/wrapper, DRAM
scalars and44-byte RTC placement were checked in the linked binary. These two
successful transfers establish this bounded regression result, not long-term
reliability or the unique original cause of the TLSF fault. UI touch/animation
quality requires physical observation separately from these OTA checks.

### Later failure and allocator diagnosis

A later0.7.6 transfer failed again in ROM `tlsf_malloc`: PC2f80a2aa, failed
PSRAM block-header size read502cae90, HTTP phase5 at offset880640. The healthy
slot was retained. This invalidates any interpretation of the earlier successes
as a universal fix; the original pollution/unavailable-memory cause is unproved.

The diagnostic application uses ESP-IDF's supported source TLSF allocator
(`HEAP_TLSF_USE_ROM_IMPL=n`) and light boundary canaries, with heap functions
remaining in IRAM. This changes layout/timing and improves attribution; it is
not an SDK patch or proof that ROM alone caused corruption. Reproduce the
overlay with `boards/mosaico/sdkconfig.heapdiag.defaults` and the validated
dual-slot profile. Do not flash generated loader/table artifacts for this change.

0.8.0 diagnostic bootstrap passed. A complete0.8.1 manual DOWNLOAD remained at
ReadyInstall for15s without selection/restart; wrong hashes were rejected and
CHECK could not overwrite the retained image. With Wi-Fi actually stopped,
INSTALL selected the target and remained ReadyReboot for15s. Only explicit
REBOOT activated it, followed by VALID health acceptance. The0.8.2 USB bypass
full pipeline also passed without a new saved panic. Battery-only download and
physical orange-button/overlap/smoothness observations remain separate pending
tests; connected-USB diagnostics do not establish those observations.

For unattended recovery, reopen/reconnect runtime CDC and verify an idle VALID
current slot before retrying a signed inactive-slot download. Never switch to
ROM, erase all or overwrite the active slot as a retry shortcut. Count only
new boot/panic records; retained historical panic data is not a new failure.
At least3 repeated identical failure phase/offset positions require a stop and
manual attention; use a bounded total attempt budget rather than infinite retries.

Diagnostic regression completed0.8.0 ->0.8.1 (manual stages), then0.8.1 ->0.8.2
and0.8.2 ->0.8.3 (USB full-pipeline bypass), all alternate-slot VALID without
new saved panics or observed canary failures. Final0.8.3 runs ota1, quota fresh,
sealed nominal gauge65mAh unchanged. No additional BOOT was needed after the
diagnostic bootstrap. This is three successful diagnostic-profile transfers;
changing allocator/layout/timing still prevents claiming a unique original cause.

## Mosaico 0.9.0 clock and orientation

The quota footer shows Shanghai local time (UTC+8) and date. SNTP calibrates the
existing system RTC/HRT clock; a truly powered-off, offline boot shows `--:--`.
There is no claim of a battery-backed RTC or persisted-time accuracy after power
loss. `debug clock` reports validity and the last observed SNTP synchronization.

BMI270 acceleration alone supplies four-way orientation, with a stable 600 ms
candidate and flat/diagonal/acceleration rejection. Gyro and temperature stay
unused; the acceleration sensor is powered down during lock and OTA. No shared
peripheral power rail is toggled. The GUI reads a cache, not I2C. `debug motion`
provides read-only sensor and display-orientation diagnostics.

Rotation uses a short black-curtain fade and drained panel transactions; LVGL
rotates raw touch coordinates exactly once. Checked mirror/swap failures attempt
rollback. If rollback fails, the UI remains black and input-disabled rather than
exposing mismatched touch/display coordinates. Portrait initialization explicitly
issues the checked panel commands, since display registration alone does not.

Both swipe directions use a single-contact threshold latch with release fallback;
Function shares the page-slide animation. Rotation is deferred during touch,
page transition, lock and OTA. Source/geometry tests and compilation do not replace
physical four-direction, touch/swipe, wake and visual acceptance.

0.9.0 was deployed from0.8.3 by one signed network update to ota0 and accepted
VALID (boot54), preserving0.8.3 in ota1. NTP time was valid; BMI270 ID0x24 initialized
with zero read errors, display orientation was healthy. Physical four-way mapping,
rotated touch/swipe and clock layout remain user acceptance items.

## 0.9.1 physical feedback corrections
The user accepted both swipe directions and quota layout on0.9.0. Physical
portrait/180 orientation was correct, but90/270 were reversed. Only the BMI270
horizontal sign mapping is swapped; LVGL touch rotation is unchanged.

Time, date and clock icon now share purple dot styling; HH:MM and MM-DD have the
same pitch and widget size. The lower-right mAh caption is removed, not the SOC,
charging indication or gauge telemetry. Lock shows HH:MM above the quota, using
the existing minute refresh and pixel offset; no new timer or wake cadence.

## Battery-powered manual OTA (0.9.2 policy bootstrap)

0.9.1 can download on battery but requires external power to install. For the
first test: download0.9.2 on battery, then connect USB for its INSTALL/REBOOT.
Only after0.9.2 boots successfully can a later signed release test battery-only
INSTALL and REBOOT. Do not mistake a battery download for full battery OTA.

Manual battery installation/reboot requires fresh, valid, sealed gauge evidence,
no configuration/critical state, voltage >=3900mV, nominal/valid capacity, SOC
60..100%, and remaining capacity >=60% of full capacity. These are conservative
software gates, not measured energy guarantees. Use a charged battery for tests.
Download's existing3500mV periodic gate remains; automatic and USB bypass modes
retain their external-power policy. The final gate is checked again before the
journal/boot selector and before restart. A failed reboot power check retains the
verified selected image instead of erasing it. An optional `attempt_power` journal
records installation voltage, SOC, external-power heuristic and manual mode for
post-test inspection; USB enumeration/current is not a physical VBUS measurement.

0.9.2 UI uses larger native dots for clocks, orange lock time and purple awake
time/date. OTA meter motion is left-to-right; quota/reset meter waves are unchanged.
The lock overlay is not raised above the clock panel; entry forces repaint. The
original invisible-clock cause has not been physically established.

## 0.9.4 CHECK/idle timing correction
The CHECK response can expose an update offer. `refreshOta()` changes pages and
records a fresh activity time, after `update()` already captured its entry tick.
Unsigned subtraction with that older tick can falsely report a huge idle duration
and lock immediately. The same ordering can spuriously trigger the refresh path
when `lockDisplay()` records its own newer timestamp.

Both comparisons now recapture LVGL time after the relevant callbacks and reject
future timestamps, while preserving real one-minute expiry and uint32 rollover.
A source-extracted constexpr regression covers the callback advancing time by3ms,
60000ms expiry, future timestamps and rollover; the old implementation fails the
same harness. Hardware CHECK acceptance remains a separate user observation.
