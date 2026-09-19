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
hostname-bound TLS session, or an exact certificate pin explicitly provided by
the authenticated DERPMap. Time synchronization must complete before startup,
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

## Cross-network quota peer and private DERP

The watch uses a single DERP connection. When the configured priority quota
peer advertises a home DERP region, connect to that region instead of blindly
using the watch's default region; otherwise a different-region target may
never receive handshake packets. This is a single-quota-peer optimization, not
general simultaneous multi-region VPN routing. The watch also publishes the
same region through a non-streaming Hostinfo update (even without STUN results),
so peers send replies to the relay it actually listens on.

The authenticated DERPMap `CertName` can select a certificate DNS name or
`sha256-raw:<64 hex digits>`. The latter verifies SHA-256 over the full leaf DER
certificate, following Tailscale's pin format. A malformed or nonmatching pin
fails closed. Ordinary nodes still use the CA bundle and hostname verification.
`InsecureForTests` is never used to bypass verification. TLS remains VERIFY_REQUIRED;
the standard trust-store attachment also satisfies the TLS library's CA-chain
configuration requirement, while the explicit pin callback enforces pin identity.
`debug tailscale-crypto` checks a matching and a modified certificate fingerprint
in addition to the existing Noise/AEAD known-answer and tamper tests.

Reference: [Tailscale DERP TLS configuration](https://github.com/tailscale/tailscale/blob/main/derp/derphttp/derphttp_client.go).

## Self-hosted DERP interoperability and roaming

The client uses authenticated DERPMap IPv4/IPv6 addresses before DNS, keeping
HostName/CertName separate for TLS identity. Explicit `none` disables a family.
Address candidates are tried after TCP failures. Non-STUN nodes in the selected
region rotate after connection failure. Persistent failures trigger another
bounded attempt after 30 seconds, rather than remaining disconnected forever.
Failed TLS allocations are released even when the socket was already closed.

Priority-peer changes are processed from both full peers and modern
`PeersChangedPatch` arrays keyed by NodeID (legacy key-object patches retained).
A changed home region is announced with a non-streaming Hostinfo update and the
relay owner reconnects. Changed DERPMap Regions trigger a full authenticated
map refresh; unchanged map snapshots do not repeatedly reconnect.

TLS modes: normal public CA + hostname; CertName DNS override; authenticated
`sha256-raw` leaf pin; administrator-supplied CA bundle for private/incomplete
chains. This deployment includes a public intermediate verified back to existing
IDF roots; see `certs/README.md`. Unknown/self-signed certificates are not accepted
merely because `InsecureForTests` is true. Do not fetch and trust arbitrary AIA
certificates at runtime. The preferred server fix is to serve a complete chain.

Scope: constrained single-priority-peer client, not the full Tailscale daemon.
Existing region/node/string limits and TLS capabilities still apply. An unreachable
node, untrusted certificate, non-meshed inconsistent nodes within one region, or
expired authorization cannot be made usable safely by ignoring errors. IPv6-only
and multi-node failover code paths require suitable networks for on-device testing;
do not infer their validation from the IPv4 single-node-per-region deployment.

`debug tailscale-crypto` also tests modern priority-peer patch transitions,
unknown NodeIDs, and zero/out-of-range regions (five cases).
Protocol reference: https://github.com/tailscale/tailscale/blob/main/tailcfg/tailcfg.go

Live deployment validation (StopWatch, ESP-IDF6.1): initial Hong Kong quota/history
accepted successfully with CA verification; while keeping the watch running,
Mac home relay was forced through901 (SZ, SHA256 pin),903 (KR, CA),902 (HK, CA).
Quota/history counters advanced in each test and Mac reported the requested
region. Automatic Mac relay selection was restored after the test. These are
three IPv4/single-node-region tests, not an assertion that every possible custom
DERP topology has been tested.

To reproduce roaming validation without changing persistent preferences:
use `tailscale debug force-prefer-derp <region>` on the quota host, keep the watch
awake, and verify new `debug network` quota/history acceptances and the host relay.
Allow control-plane propagation and at least one full polling interval; cached
values alone are not proof. Always restore `force-prefer-derp 0` in a finally/cleanup
step. `debug display-lock`, `debug power-refresh`, and `debug power` verify the
separate sleep/reconnect lifecycle after restoring automatic selection.
