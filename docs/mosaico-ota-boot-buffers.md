# Boot-reserved OTA transfer buffers

Mosaico startup calls `NetworkQuota::prepareOtaBuffers()` after HAL initialization,
before `MosaicoSessions::init()` (BLE) and `NetworkQuota::begin()` (Wi-Fi). It tries
once per boot to reserve:

- 4096 bytes with INTERNAL | 8BIT | DMA for the NOR write source.
- 8192 bytes with SPIRAM | 8BIT for HTTP/JSON only, never NOR writes.

The boot log reports ready/error and sizes. Both ranges must validate before
publication: the chunk's first and last bytes are DRAM, internal and DMA-capable;
the body's endpoints are external RAM. Failure frees partial allocations and
latches failure for this boot; normal app startup continues, without retries or
restart. Successful allocations stay resident for the boot. This costs 4 KiB of
internal SRAM plus 8 KiB PSRAM; power cost has not been measured.

Only the existing network owner's admitted OTA path acquires these buffers. The
acquisition validates endpoints/capacities again and allocates nothing. Missing or
invalid buffers fail before manifest transfer/erase. No NAND sharing, new task,
lock, request format, image layout, power gate, signature policy or boot selector
is introduced. The existing `writeChunk` maximum 4096-byte/DRAM guards remain.
The persistent JSON body is cleared at entry/exit; each request resets byte 0,
used length and terminator; JSON parsing remains bounded to the returned length.

Host-only regression (configured full-path SDK Python, `PYTHONPATH=tools`):
`-m unittest tools.test_mosaico_ota_boot_buffers tools.test_mosaico_ota_memory
 tools.test_mosaico_ota_power tools.test_mosaico_ota_locked_admission`.
Tests extract actual allocator, transfer-buffer scope and source integration;
cover partial cleanup, failed-boot latch, no allocation on acquire, wrong-memory
rejection, startup ordering and unchanged OTA safety boundaries. Logs:
`artifacts/ota-boot-buffer-tests-run.log`, `artifacts/ota-boot-buffer-tests/*.log`.
These are offline checks, not a firmware build, hardware OTA acceptance or proof
that a particular board has enough available memory at startup.
