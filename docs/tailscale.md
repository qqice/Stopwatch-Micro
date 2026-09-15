# Tailscale transport

This firmware vendors the pinned MicroLink source for the device-side
Tailscale-compatible transport. It is an independent, reverse-engineered
implementation; it is not an official Tailscale client and its protocol
compatibility must be validated on the target tailnet before it is relied on.

The StopWatch build deliberately excludes MicroLink's cellular, Wi-Fi/cellular
switching, and HTTP configuration-server sources. The firmware never exposes a
configuration web UI, and it does not place Wi-Fi credentials or a Tailscale
auth key in `sdkconfig.defaults` or source control. Provisioning supplies those
values locally. The component uses PSRAM for its control-plane buffers; the
firmware checks that at least 80 KiB of internal RAM remains before starting
MicroLink.

`CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=0` directs ordinary heap allocations to
PSRAM whenever possible. This preserves internal RAM for FreeRTOS task stacks,
DMA-capable buffers, BLE, and Wi-Fi. Capability-constrained allocations still
select internal memory when required; the transport's 80 KiB free-internal and
16 KiB largest-block guard remains the final startup decision.

DERP uses certificate verification with the ESP-IDF certificate bundle and a
hostname-bound TLS session. Time synchronization must complete before startup,
because certificate validity checks are intentionally not bypassed. The
control-plane Noise authentication and its random-number generator are also
left enabled and failures abort connection setup.

Application traffic intended for a tailnet peer must use `microlink_tcp_*`.
The patched TCP path fails closed when it has no assigned VPN address or cannot
bind the socket to that address, so it cannot fall back to a plaintext LAN or
default-route connection. The control-plane implementation uses its upstream
Noise-based protocol path; this integration does not disable certificate,
authentication-tag, or random-number validation.

The vendored revision and compatibility/security patch are reproducible:

```powershell
python .\fetch_repos.py
```

Do not forward a public port to the device. Access control remains a tailnet
policy decision, and the later application endpoint must authenticate its own
requests in addition to tunnel membership.

## Configure an explicit tailnet listener

Add the PC's Tailscale IP to `additional_hosts` in the private service config,
keeping `server_host` bound to its LAN address. The service binds these two
addresses explicitly, not all interfaces. Restart the LAN quota scheduled task.
All endpoints retain the same read-only device token.

## Provision this firmware

Use an auth key from your own tailnet, saved in a private local file:

```powershell
python tools/provision_tailscale.py --key-file .artifacts/tailscale-auth-key.txt --url http://100.x.y.z:8765/v1/status --port COM24
```

The destination must be an IPv4 tailnet address in 100.64.0.0/10 with an explicit
port. HTTP here is carried inside the authenticated WireGuard tunnel. It does not
fall back to a LAN connection. The auth key is stored in NVS and never embedded in
the public binary. A first configuration activates on the next network cycle;
changing an existing tailnet configuration requires a normal restart. Use
`debug tailscale` and `debug network` for credential-free connection diagnostics.


ESP-IDF 6.1 network-interface compatibility: bridge support/client-data slots are enabled so
ESP-Netif stores its pointer separately from the foreign WireGuard netif state. This does not
create an Ethernet/Wi-Fi bridge. It prevents the IDF status callback from interpreting a
WireGuard context as an ESP-Netif object. The fixed source configuration includes the low-rate
Wi-Fi RX/BA buffer counts used on the device, so clean builds reproduce the tested setup.

Before registering, the device runs ChaCha20-Poly1305 known-answer, tampered-tag rejection,
and deterministic full Noise msg1 tests. `debug tailscale-crypto` exposes their result.
A previously configured but unreadable tailnet namespace fails closed instead of reverting to LAN.

Verified on StopWatch with ESP-IDF6.1: crypto known-answer/tampered-tag/full-msg1 tests PASS;
tailnet registered and WireGuard TCP to PC quota port succeeded; network accepted=1 after
3 initial connection-not-ready polls, HAL selftest17/17, internal_free53615 bytes.
This verifies the tested tailnet path; it is not a general-purpose VPN interoperability audit.
