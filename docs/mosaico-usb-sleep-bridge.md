# USB sleep bridge candidate (IDF 6.1 / ESP32-S31)

`app_main` calls `mosaico_console_usb_service_sleep()` before the existing
StandbySleep service. This sole task-context owner clears the prior LP wake hint
and wake status, then arms the hint only while no TinyUSB active bus is observed
(`(mounted || connected) && !suspended` is false). Initial battery boot without
an enumeration also arms it. TinyUSB mount/unmount/suspend/resume/RX callbacks
only publish an atomic generation and notify the existing idle owner. A generation
change during the arm rolls it back; pending generations block sleep eligibility.
No USB payload is read or retained by the bridge.

The boot-scoped USB wake + CNNT ON requests must both succeed; otherwise the
existing sleep gate fails closed. CPU power-down/retention, PM settings, CDC FIFO,
1200-baud recovery, boot grace, UART/OTA gates and 500 ms offline idle policy are
unchanged. Connected but not yet mounted uses the existing 20 ms active cadence.
No manual light-sleep call, sleep/ISR callback calling PHY flash code, or permanent
NO_LIGHT_SLEEP lock is added.

`settings usb` reports requested/configured software observations and counters,
not hardware register readback or proof of physical USB power. PHY helpers follow
the fixed SDK official USB wake example; they do not set TinyUSB mounted/suspended
state. This is an unverified hardware candidate, not hotplug acceptance.

The fixed TinyUSB source has no `tud_reset_cb` callback. Do not add an unused
symbol and mistake it for reset coverage. No ISR/event-hook extension is included.

## Remaining reset/enumeration observation gap

This candidate does **not** prove fail-closed coverage from physical reset through
first SETUP. In the fixed TinyUSB `src/device/usbd.c`, BUS_RESET calls
`usbd_reset()` (lines 717-721), which clears `_usbd_dev` via
`configuration_reset()` (650-663), without an application callback. `connected`
is only restored on the first processed SETUP (739-742). During that gap,
mounted/connected/suspended can all be zero and the application generation may
be unchanged: the bridge cannot distinguish it from an offline battery boot,
and may arm the LP hint and report sleep-safe. The constexpr test explicitly
records this limitation, not a hardware acceptance claim.

Once first SETUP makes connected true, the fresh sleep snapshot rejects an
active bus even before mount/RX. Mount/resume/RX callbacks additionally publish
pending generation and invalidate the existing idle owner. These observations
still do not establish a hardware wake guarantee or bound pre-SETUP latency.
Closing the reset gap would require separately authorized evidence/design work;
this candidate deliberately does not broaden the ISR surface.

Offline verification: `tools.test_usb_idle_cadence` exercises actual service and
snapshot code with constexpr transition/race harnesses, supported/unsupported
SDK callback profiles, recovery gates and source invariants. The boot-grace tests
retain fault-reset recovery semantics. Logs: `.artifacts/mosaico/usb-bridge-tests.log`
and `usb-bridge-real-syntax.log`. No device/serial/network testing was performed.
