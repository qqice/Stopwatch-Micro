# LAN quota service

`tools/quota_service.py` runs beside the existing Windows bridge and exposes the
latest quota snapshot to a trusted device on the same network. It starts its own
Codex App Server client and polls current rate limits every 60 seconds. History
collection is intentionally deferred until the history interface defines its
schema and retention policy.

Create `.artifacts/lan-config.json` locally (it is intentionally not tracked):

```json
{
  "server_host": "192.168.1.10",
  "server_port": 8765,
  "device_token": "replace-with-a-random-secret-of-at-least-16-characters"
}
```

Start it from the repository root:

```powershell
python tools/quota_service.py --config .artifacts/lan-config.json
```

To start the same service automatically after Windows sign-in, install its
hidden, current-user scheduled task (this command resolves the config to an
absolute local path):

```powershell
.\tools\quota_service_task.ps1 install
```

Use `status`, `start`, or `stop` in place of `install` to manage the task.

The only endpoint is `GET /v1/status`, with `Authorization: Bearer <device_token>`.
It returns compact JSON:

```json
{"version":1,"captured_epoch":1760000000,"remaining_bp":8000,"reset_epoch":1760100000,"reset_credits":0,"age_seconds":12,"available":true}
```

`remaining_bp` is percentage remaining in basis points (8000 means 80.00%). A
fresh snapshot is available for 120 seconds. Before the first successful poll,
or when the last result is older, the endpoint returns HTTP 503 with
`available:false`; initial unknown fields are `null`, never invented values.

This is HTTP protected by a local shared secret, suitable only for a trusted LAN.
Do not expose it directly to the internet. The planned Tailscale integration can
provide encrypted device-to-server transport and avoids public port forwarding.

## Provision a StopWatch (ESP-IDF 6.1)

After building/flashing, create a private JSON file outside Git (for example
`.artifacts/private/device-network.json`):

```json
{"ssid":"YOUR_2_4_GHZ_SSID","password":"YOUR_WIFI_PASSWORD","url":"http://YOUR_PC_LAN_IP:8765/v1/status","token":"SAME_DEVICE_TOKEN_AS_SERVER"}
```

Run `python tools/provision_network.py --config .artifacts/private/device-network.json --port COM24`
using a Python environment with pyserial. Configuration is written into the device's NVS, not
compiled into the binary. First-time provisioning starts Wi-Fi immediately; replacing an existing
configuration requires a normal device restart. The debug console never echoes the secret payload.
`debug network` reports configured/connected/accepted/failures without credentials.

Use a DHCP reservation for the PC or update the device URL if the LAN address changes.
ESP32-S3 supports 2.4 GHz Wi-Fi. TLS URLs use the ESP-IDF CA certificate bundle; plain HTTP is
intended only for a trusted LAN or the later encrypted overlay, never port-forward this endpoint.
If Windows blocks inbound traffic, run `tools/enable_lan_firewall.ps1` as administrator; it permits
only the local subnet on the selected network interface and quota port.

When Bluetooth is disconnected and Wi-Fi has been configured, the device shows an independent
quota/battery screen. Normal BLE controls remain available while connected. Network responses
older than 120 seconds are rejected; received data keeps its original age. The PSRAM allocation
options in sdkconfig.defaults preserve internal RAM for Wi-Fi/BLE coexistence.

Bind server_host to the PC LAN address explicitly; omitted server_host defaults to loopback.
Disable the previous Bluetooth quota scheduled task when switching to LAN service; BLE control pairing is unaffected.
