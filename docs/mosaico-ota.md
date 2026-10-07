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

## 0.10.0 display settings and pixel shift

Function and swipe now cycle Quota -> History -> OTA -> Settings. Settings expose
charge/battery idle timers, charge/battery awake brightness, locked brightness,
and pixel-shift enable. Charge time:15/30/60/120/300/600seconds or NEVER; battery:
15/30/45/60seconds, never above60. Awake brightness10..100%; lock0..100%, with
Function-only wake retained even at zero. Defaults preserve60seconds/80%/8%.
USB enumeration or valid positive charging current selects the charge profile,
including a fully charged USB-connected unit. This is not a measured VBUS signal.

Edits apply immediately in RAM and queue a coalesced save to a dedicated versioned
CRC-checked blob in `mosaico_disp`. No GUI callback writes flash. Green check means
saved, grey check means defaults loaded, gold hourglass queued, orange question
mark a save error. Owner serialization defers saves during OTA/boot health/staged
selection. A settings-only fallback supports no-network operation; provisioning
Wi-Fi later performs a checked writer handoff before any network-owner OTA writes.
Only one writer survives. Failed saves retain current RAM and previous storage;
new user edits retry, without a background busy loop. `debug display-settings`
is read-only and reports config, revision, save status and error.

Pixel shifting follows the bounded-small-offset/monotonic cadence principles in
[AOSP BurnInProtectionHelper](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/master/services/core/java/com/android/server/policy/BurnInProtectionHelper.java)
and the pixel-shift concept described by
[Samsung](https://www.samsung.com/sg/support/displays/how-to-solve-image-shift-issues-on-your-samsung-oled-monitor/).
This implementation moves native LVGL content over nine offsets within ±2px at
the existing minute cadence, without a new timer. Shift is deferred during contact,
page/rotation animation, input suppression and OTA activity. Disabling recentres.
Touch hit regions move with LVGL objects, not an extra coordinate transform.
Tests check visible-widget margins for every offset. This reduces static-pattern
risk; it cannot guarantee no burn-in or repair an already worn panel, and is not
OLED compensation/pixel-cleaning. Hardware settings, persistence, brightness
profiles, zero-brightness wake and rotated shifted touch need user acceptance.

## 0.10.1 full-charge/direct5V UI supply hint
Display profiles and lightning no longer use USB stack enumeration. A RAM-only
state machine anchors on valid gauge current >3mA, retains the external-supply
hint for the -3..3mA deadband (including full-charge zero), and clears it on valid
current <-3mA or invalid telemetry. Battery charging animation uses positive
current only; zero-current full charge retains a static lightning indicator.
This hint does not feed OTA safety checks, is not persisted and does not infer
supply from100% SOC or voltage alone. Existing OTA safety policy is unchanged.

CoreBoardV1.0 page5 wiring shows CHRG/STDBY routed to the charging LED and the
5V_IN_flag node at TP22, without a verified MCU-readable detector. Therefore this
is UI inference, not physical5V detection. A full-charge cold boot at zero current
has no positive-current anchor and remains in the battery profile; reliable
identification in that situation requires additional input evidence. To verify
this fix, start while actual charging is observable, leave the external5V source
connected through full charge, then unplug and confirm the battery profile returns.
No charger controls, fuel-gauge configuration or GPIO circuitry were modified.

## 0.10.2 OTA page stay-awake and full-zero UI policy

The OTA page never follows configured idle timeout, including CHECK/idle, offer,
DOWNLOAD, verification, UPGRADE/ready, REBOOT/ready, completed and failed states.
Local pending/busy and authoritative backend queues/busy also inhibit locking,
so a temporarily stale UI snapshot cannot open an idle-lock window. This blocks
lock-triggered radio suspension; it does not claim to fix unrelated network loss.
Exiting OTA starts a fresh normal-page idle period. Battery usage increases while
this page stays open. Existing OTA power/health/signature/slot gates are unchanged.

At the user's explicit request,0.10.2 replaces0.10.1's cold-full limitation with a
simple UI-only heuristic: exactly100% SOC and current within -3..+3mA selects the
external-supply profile without needing a prior positive-current sample. If that
sample was the only basis,99% clears it. Positive charging current still establishes
a RAM anchor that can hold neutral-current full/taper samples; negative current
below-3mA or invalid telemetry clears both states. No USB signal, manual button,
load probe or persisted supply flag is used. Static lightning remains visible
for the supply hint, while battery charging animation still needs positive current.
This is accepted inference, not a measured5V input, and never relaxes OTA safety.

## 0.11.0 weekly quota trend implementation

The history heatmaps remain30d/24h. The native line plot beneath them shows
account-wide `codex` remaining weekly allowance: rolling7d below30d, rolling24h
below24h. It uses a fixed0–100% scale, actual observation timestamps, and latest
real percentage for the selected series. It does not normalize Token totals.
Mode/tile interactions read the same local cache and perform no HTTP requests.
The compact selection row remains below the plot; original tile touch areas stay.

Mac collection now independently records successful official weekly10080-minute
quota snapshots to an additive SQLite table in the existing private database.
Onlycodex is accepted; no5h/model fallback. Retention90days, minute polling unchanged.
There was no prior percentage table: unrecorded past periods remain empty. The
projection keeps25 hourly/8 daily slots over exact rolling24h/168h bounds, selecting
latest actual samples per bucket. Missing points and collection gaps break lines;
weekly reset boundaries are broken and marked, not smoothed or converted to usage.
This charts official quota as observed, not the exact time Tokens were consumed.

Authenticated `/v2/history` combines independent Token history and quota trend
with separate availability, capture and age. Legacy `/v1/history` is unchanged;
Mosaico requestsv2 once per background refresh, StopWatch/S3 keepsv1. Malformed,
contradictory or out-of-range trend documents leave firmware cache unchanged.
Fresh percentage data does not make stale Token data look fresh; stale/missing
Token history cannot suppress newer quota observations. Empty aged ranges clear
expired graphs honestly. Optional new-table initialization failures disable only
trend recording, without taking canonical quota/legacy history offline.

Source/model/geometry/schema tests and code review are not physical display proof.
After OTA, verify chart/selection layout,24h versus7d mapping, touch/rotation and
no-data/single-point behavior. Initially a lone point is expected until more time
buckets contain observations. Existing signing, healthy alternate slot, power
thresholds, wake/idle guards, display settings and gauge profile are unchanged.

## 0.12.0 native Bluetooth session monitor

A fifth Sessions page follows the currently Bluetooth-connected client's six
Micro Agent-key slots. Function/swipe cycle Quota -> History -> OTA -> Settings ->
Sessions. It shows native default-palette status hints: idle, working, unread
completion, attention (approval or input), error, unassigned, or unknown. Counts
are explicitly bounded to these six slots, never claimed as all client threads.
Unknown semantic colors do not yield fake zero-running totals; partial evidence
uses `+?`/`--`. Brightness zero remains a preference; Off with nonzero color is
ambiguous, not proof of an empty chat. Opaque magic values do not erase clear hues.

The implementation follows the behavior in the
[official Micro documentation](https://learn.chatgpt.com/docs/features/codex-micro).
No official Micro firmware source was verified. This repository's existing BLE
vendor compatibility layer is community reverse-engineered, not OpenAI firmware
or a stable public API. Public
[Codex App Server](https://learn.chatgpt.com/docs/app-server)
supports runtime events and user-input replies, but those belong to the owning
live connection. The independent quota process is not the desktop client's live
session source, and is deliberately not used to impersonate that runtime.

Observed `v.oai.thstatus` lighting messages contain slot/light parameters, not
question text, choices or a pending request ID. This release is read-only: no
Agent-key focus, Approve/Decline, joystick, keyboard or answer controls are sent.
Amber cannot safely be treated as an answerable choice prompt. Supporting actual
choice replies later requires explicit request identity and a verified owner reply
transport; generic approval keys would not satisfy that safety boundary.

Known-slot metadata requires complete valid frames and the current generation;
partial metadata/handshake cannot invent zero idle sessions. Malformed batches
are rejected before mutation. Disconnect/failure clears live-known status. `LAST`
is the slot's first known state or actual c/b/e change, not periodic packet age;
there is no TTL that turns a long-running live chat into a stale/dead one.

BLE initializes before network/UI on Mosaico; failure is nonfatal to quota. The
main owner performs radio work outside GUI locks. GUI publishes RAM-only leases;
BLE is active only on awake Sessions, and paused on leaving it, lock, suppression
or OTA. Network power management may close, never spuriously reopen, that lease.
Terminal monitor faults do not reboot the quota monitor or reset bonds. Partial
SDK resources can remain allocated until ordinary restart; no claim of full
controller teardown or measured battery savings is made. Existing S3 control and
recovery behavior is preserved. Hardware pairing, vendor interoperability, radio
quiescence and Wi-Fi/heap coexistence still need physical verification.

### Static resource planning

The user authorized larger fonts/assets on existing SPI NOR or NAND. This first
monitor page has no received chat titles/question text and uses existing Latin
and dot widgets, so it does not require a new font asset, partition change or NAND
activation. Prefer a separately versioned/hash-verified asset bundle in the
existing NOR resource area if multilingual content becomes available; retain
rollback-compatible assets and avoid re-uploading unchanged resources per app OTA.
NAND should be introduced only when capacity actually demands it, with explicit
mount/data-preservation and power-state validation. No NOR/NAND asset writes were
performed for this release.

## 0.12.1 lock-screen slot indicators and bounded refresh

The lock screen adds six non-interactive rounded dashed cards with dot digits1–6,
centred as a384px row. Working hints are purple; unread-completion hints orange;
unknown/idle/other states are neutral. Cache validity, receipt freshness and age are
explicit: unavailable or expired data is not shown as a fresh coloured state.
Battery icon plus actual percent text are centred using measured font metrics,
including `?`, `0%`, `100%*`; the clock/quota/reset groups remain centred. Existing
±2px burn-in movement and Function-only wake remain unchanged.

Lock entry and the existing minute refresh queue a RAM-only request. The main
owner briefly enables BLE, waits for real complete post-request per-slot frames,
and closes early when all six slots are refreshed. Requests are at least60s apart;
the owner deadline is8s. Requests arriving slightly before that cooldown expires
remain pending, rather than being lost due to UI/owner scheduling drift. Active
requests coalesce. Wake, OTA/boot-health or terminal failure cancels the window.
No new GUI radio call, timer, HTTP request, key event, controller initialization or
NVS operation was added. Radio ownership is still main-only; network power policy
may not close an admitted lock refresh window. Raw display lock state, rather than
radio profile state, admits requests even in diagnostic power-profile0.

Complete-frame receipt sequence/time is independent of `LAST` lighting-change
time. Handshake, unrelated metadata and an old known63 cannot prove a refresh.
Timeout with partial complete evidence caches only those slots; no fresh evidence
keeps prior cache marked cached, without advancing its capture time. Generations
are isolated; a newly connected host cannot inherit another host's coloured slots.
Cached sample state survives the intentional physical disconnect, but actual HCI
connection reporting is not falsified. `debug sessions` reads cached ready/link,
known/cache/fresh masks, window counts, intent duration and errors without waking
or querying radios. The8s deadline is cooperative owner intent; actual radio stop
latency and power consumption need physical measurement and are not claimed.

After OTA, temporarily set the active supply profile to a finite idle timeout if
it is normally NEVER. Verify the card row and battery centring, then watch two or
more minute reconnect windows with a changed slot on the PC. Confirm normal
brightness/lock remains, Function wakes only once, and OTA still prevents sleep.

## 0.12.2 remove lock explanatory cache text

The user rejected the lock-screen CACHED/SYNC/STALE age caption as redundant UI.
The entire caption widget and formatter are removed, with no replacement text or
status badge. The six numbered state icons, battery centring, conservative colour
rules and minute BLE acquisition are unchanged. Cache freshness/window errors
remain available only through the existing read-only diagnostic interface.

## 0.12.3 shared-model icon simulator and visual gate

Quota uses an allowance-wallet symbol rather than a clock/gauge silhouette. The
coin has a round ring with `C`; reset cards show `R`; Credits replaces Points.
The install/update icon is a round two-arrow refresh symbol with a stable outline,
not a deforming shape. Coin/card rotation remains, with readable C/R front and back.

All dotted meters keep their original filled-dot count and stationary coordinates.
A bright charging core travels between two genuinely dim/desaturated shoulders,
which recover smoothly to the base colour. The selected STRONG DARK model keeps
filled dots brighter than unfilled ones. Zero/unknown/stopped bars do not gain fake
filled highlights; a single occupied column pulses in brightness only. The sweep
cycle is6s, quota/reset travel right-to-left and OTA left-to-right. Existing
visibility, stale-data, lock, slide and rotation gates remain unchanged.

### Mandatory visual workflow for future icon work

Run `tools/preview_dot_model.py` with the existing IDF Python/Pillow environment.
It cross-compiles the same pure C++ samplers used by the firmware and exports
bounded primitive commands from a `.preview` object section. Pillow rasterizes
those commands; it does not maintain independent icon masks or motion formulas.
The generated JSON hashes and frame metadata accompany PNG contact sheets/GIFs.
Review actual1x sizes and nearest4x enlargements, fronts/edges/backs and animation
phase strips using the image-view tool BEFORE integration/build/release. New icons
must first be added to this shared simulator path. Keep rejected proposals as
ignored evidence, not as the release's visual proof. Do not add a full-screen
canvas or install a new toolchain to produce these previews.

For0123, the first proposal was viewed and rejected for a diamond-shaped refresh
outline and white-only shoulders. The v2 round-arcs/true dim-skirts proposal was
viewed at actual28/32/88 sizes and accepted with STRONG DARK. Final integrated
models must be re-exported and reviewed again. Source/image acceptance cannot
prove physical OLED gamma/readability, so final hardware observation remains a
separate gate. Existing native masks/fonts suffice; no NOR/NAND asset write or
partition change is required.

## Settings sheet (0.13.0)

Settings is not a fifth carousel page. On quota, history, Micro/Sessions or OTA
(idle), swipe upward to open its animated sheet. Function or a downward swipe
dismisses it without advancing the underlying page. Locked Function still only
wakes. OTA download/verify/install closes settings and keeps OTA awake.

Four tiles group TIMEOUT, BRIGHTNESS, BURN-IN and WIRELESS. Existing icons and
fonts are reused. WiFi and BT locked refresh periods can be independently set
to 1, 2, 5, 10, 15, 30 or 60 minutes. The default remains WiFi5min/BT1min;
settings-v1 blobs are read without writing, and user edits save v2 in the same
CRC-protected24-byte blob. Lock screen rendering still updates once/minute;
that does not force a network poll. BLE update windows remain bounded8seconds;
WiFi1min windows are capped45seconds to leave an offline gap on connection failure.

WIRELESS provides SSID and a masked password editor with the LVGL screen keyboard.
Existing passwords are never returned to UI. An empty password is submitted only
with OPEN explicitly selected. SAVE queues an atomic CRC-protected SSID/password
pair (`quota_net/wifi_ui`); endpoint/token/tailnet are not changed. REBOOT is a
separate explicit action to apply credentials, avoiding unsafe reconstruction of
live WiFi/netif/tailnet. Both save and reboot are owner-executed and blocked across
OTA/boot-health/staged-image work; reboot also waits for pending display settings
persistence. A persistence error does not reboot and may be retried.

This is not initial server provisioning: if the existing quota owner cannot start
or endpoint configuration is absent, WiFi editing reports unavailable rather
than pretending to save. The original serial recovery/provisioning route remains.
The Bluetooth product name is unchanged: our bridge matches HID identifiers,
but this is not proof that the official desktop client accepts arbitrary names.
No network/gauge/flash-layout changes are made merely by installing this UI.

Settings previews use exact source rectangles and reused icon primitives but are
not LVGL screenshots; keyboard font/key composition is illustrative. Root viewed
tiles, all four detail groups and keyboard layout to check overlap, then ran
actual-source gesture/control and owner fault traces. Physical touch, glyph
readability, radio timing and NVS power-loss recovery still need board acceptance.

## Experimental iTWT comparison (0.13.2 controlled trial, default disabled)

ESP32-S31 and the installed IDF6.1 support station iTWT. The existing driver
protocol default on this HE target already permits802.11ax alongside legacy
modes, but this application previously created no TWT agreement. An AX router
is not sufficient evidence: it must accept an individualTWT request. See the
[official IDF6.1 example](https://raw.githubusercontent.com/espressif/esp-idf/v6.1/examples/wifi/itwt/README.md)
and [iTWT API](https://raw.githubusercontent.com/espressif/esp-idf/v6.1/components/esp_wifi/include/esp_wifi_he.h).

This experiment uses modem sleep only: no tickless/light/deep sleep, CPU clock,
SPI/display/PSRAM, BLE schedule, SDK, NVS, endpoint or router changes. Existing
lock radio-off remains the default and is a separate third control. Keeping
association can reduce wake reconnection delay, but might use MORE energy than
minutes-long radio-off, especially with an active Tailscale control connection.
TWT's negotiated nominal wake duration is not measured RF duty cycle or watts.

Runtime USB commands (no persistent opt-in):

- `debug display-lock`, then `debug twt baseline`: retained association with
  ordinary `WIFI_PS_MIN_MODEM`, without negotiated TWT.
- `debug display-lock`, then `debug twt on`: request non-triggered, unannounced
  iTWT SUGGEST (512*2^11 microseconds interval,64*256microseconds minimum wake).
  Only actual accepted status1 and valid returned parameters enable retention;
  ESP_OK from setup merely means submission. AP may change parameters.
- `debug twt status`: read RAM-only negotiated PHY/actual interval/wake duration,
  state/reason, cleanup status, fetch statistics. No credentials/AP identifiers.
- `debug twt off`: restore original policy. Unlock and OTA also end the trial.

Both arms require locked display, leave brightness/CPU/BLE/refresh intervals
unchanged and have a ten-minute RAM lease. The owner checks lease/cancellation
between existing synchronous network requests, not a hard real-time timer;
commands do not interrupt an in-flight HTTP operation. Setup/connect stage is
limited to10seconds and starts no HTTP/tailnet setup while negotiating. Unsupported
PHY, rejection, loss or timeout falls back without automatic negotiation retries.
Restart resets default OFF. Only event-scalar copying occurs in callbacks; WiFi
calls and cleanup belong to the network owner.

Each trial uses a distinct echoed twt_id. The0131 cleanup race failed on hardware; the following old policy is NOT the current cleanup contract. Cleanup previously attempted to prove an established local
flow bitmap empty, or confirm STA stop; cancelling a still-pending setup requires
STA stop because an empty bitmap cannot disprove a late acceptance. Prior PS is
restored; failed cleanup is retained as debt with at most two attempts and blocks
new trials/OTA rather than pretending success. OTA discovery/download/install/
reboot paths are guarded. No automatic reboot on experiment failure.

Acceptance proceeds first with USB for AP negotiation/normal service/rollback,
then with matched pure-battery conditions for energy. USB charging/zero-current
readings cannot establish a saving. Gauge current/capacity are observations, not
an externally calibrated power analyzer. Compare OFF, BASELINE and TWT at matched
SOC/brightness/display/CPU/BLE/server workload; also compare time from Function
wake to a fresh quota response, failure rate and lock connectivity. Do not enable
TWT by default without actual AP compatibility and net energy/latency evidence.

### 0.13.2 callback-drain incident correction and current acceptance

The0131 teardown/stop sequence caused a real load fault in
`he_twt_teardown_txcb`. An empty local flow bitmap did not prove its asynchronous
TX callback had returned.0132 never directly disconnects/stops from cleanup:
wait for setup outcome, submit teardownALL once, wait for matchingSUCCESS, then
use the same PP task's synchronous ioctl to establish a callback-drain barrier
and empty flow bitmap before restoring PS/allowing the original radio-off path.
Both same-iteration OTA cleanup gates exit before any normal radio stop. Lost
ACK, overflow or restoration failure retains cleanup debt and blocks new trials,
OTA and normal radio-off; there is no unsafe timed-sleep/forced-stop fallback.
The10s deadline belongs to the owner state machine. The SDK ioctl uses an
unbounded semaphore wait, so this is NOT a hard driver-call timeout guarantee.
The ordering argument was checked against the installed S31 library binaries;
it is not a guarantee for an unreviewed future SDK version.

Controlled networkOTA installed0132 intoota_0 withVALID state, boot76;0131ota_1
is the rollback app and remains defaultOFF. Two acceptedTWT trials completed
unlock/off cleanup and normalradio-off without increasing boot76. A baseline
associated modem-sleep trial also exited. The saved panic record is historical
boot75/current reset3, not a new panic. No long-term stability claim is made.

However, a forced refresh underTWT recorded zero data-fetch attempts in an80s
observation while tailnetMap startup completed near its end. Same-experience
service/QoS and net energy have NOT passed acceptance. ExplicitOFF during a
pending cleanup also briefly reports Driver259 despite successful later drain
(a diagnostic command-state quirk, not evidence of a new crash). The device is
left OFF; stable server publication is restored0130, with controlled0132 retained
privately. Do not enable this prototype by default or infer saving percentages.
IoTPowerV1 capture is separately ready; see `docs/iotpower-measurement.md`.