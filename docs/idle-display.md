# Low-power idle screen

After 60 seconds without a touch or physical-key interaction, the watch dims to
8% brightness and shows only quota availability/value and battery status on a
black screen. It updates those values once per minute and moves the content on
each update. This reduces static-image wear, not the physical panel's internal
scan rate. Network traffic and host attention events do not wake it.

The first touch or physical-key gesture wakes the display and is consumed until
release, preventing accidental host commands. Active microphone/dial gestures
postpone locking.

Diagnostics: `debug display`, `debug display-lock`, `debug display-wake`.
`python tools/test_display_runtime.py --port COM24` checks one full minute against
the actual completed display-frame counter. Do not touch the device during this
test. It ends with the normal screen awake.

On-device acceptance: automatic idle lock reached brightness8; over the following65s,
lock refresh count1->2 and completed display frames81->82. Debug wake restored brightness70.
Host regression30/30 passed. Raw evidence: .artifacts/idle-display-runtime.log.
