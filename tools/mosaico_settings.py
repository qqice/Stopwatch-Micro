#!/usr/bin/env python3
"""Settings via the physically trusted local CDC/UART console.

Base64 and UART CRC provide encoding/integrity, NOT encryption/authentication.
Credential JSON must be a private local file, never a command-line password.
Wi-Fi save is asynchronous; use wifi-list to verify pending/error and only then
explicitly restart. Wi-Fi mutations are sent once, never retried automatically.
Known SSID + empty password preserves its saved password; forget cannot remove
its final profile. BLE name is fixed for protocol compatibility, not editable.
"""
from __future__ import annotations
import argparse
import base64
import contextlib
import io
import json
import re
from pathlib import Path
from serial_debug_test import DebugClient

FIELDS = {
    "charge_timeout": {0, 15, 30, 60, 120, 300, 600},
    "battery_timeout": {15, 30, 45, 60},
    "charge_brightness": range(10, 101), "battery_brightness": range(10, 101),
    "lock_brightness": range(101), "burn_in": {0, 1},
    "lock_wifi_minutes": {1, 2, 5, 10, 15, 30, 60},
    "lock_ble_minutes": {1, 2, 5, 10, 15, 30, 60},
}
SAFE_KEYS = set(FIELDS) | set("temporary lease_remaining_s runtime_revision revision saved_revision pending error ble_name_b64 ble_name_mutable wifi_available wifi_count wifi_current_index wifi_pending wifi_reboot_required reboot_required restart_pending restart_error scan_error wifi_scan_error credentials_redacted accepted_owner_pending verify_wifi_list accepted_persist_pending verify_saved_revision accepted_ram_only use_get_for_state no_changes".split())


def valid_ssid(ssid: str) -> bool:
    return (isinstance(ssid, str) and 1 <= len(ssid.encode("utf-8")) <= 32
            and all(ord(c) >= 32 and not 0x7f <= ord(c) <= 0x9f for c in ssid))


def load_credentials(path: str) -> str:
    raw = Path(path).read_bytes()
    if len(raw) > 576:
        raise ValueError("invalid credential file")
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("invalid credential file")
            value[key] = item
        return value
    data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs)
    if not isinstance(data, dict) or set(data) != {"ssid", "password"}:
        raise ValueError("invalid credential file")
    ssid, password = data["ssid"], data["password"]
    if not valid_ssid(ssid) or not isinstance(password, str):
        raise ValueError("invalid credential file")
    n = len(password)
    if (n and not 8 <= n <= 64) or any(not 32 <= ord(c) <= 126 for c in password):
        raise ValueError("invalid credential file")
    if n == 64 and not re.fullmatch(r"[0-9a-fA-F]{64}", password):
        raise ValueError("invalid credential file")
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(encoded).decode("ascii")


def build_command(args) -> str:
    action = args.action
    if action == "wifi-save":
        return "debug settings wifi save " + load_credentials(args.config)
    if action == "wifi-forget":
        if not valid_ssid(args.ssid):
            raise ValueError("invalid SSID")
        return "debug settings wifi forget " + base64.b64encode(args.ssid.encode("utf-8")).decode("ascii")
    if action == "wifi-restart":
        return "debug settings wifi restart CONFIRM"
    if action == "wifi-list":
        return "debug settings wifi list"
    if action == "set":
        if args.value not in FIELDS[args.field] or not 30 <= args.lease <= 600:
            raise ValueError("invalid settings range")
        return f"debug settings set {args.field} {args.value} {args.lease}"
    return "debug settings " + action + (" CONFIRM" if action == "save" else "")


def execute(client, command: str):
    # DebugClient prints arbitrary received lines and its TimeoutError contains
    # the entire command. Never expose either through this credential CLI.
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            result = client.command(command, "settings")
    except Exception:
        raise RuntimeError("settings request failed; outcome unknown; read state before any manual retry") from None
    rows = []
    for line in captured.getvalue().splitlines():
        match = re.fullmatch(r"DBG WIFI_PROFILE index=([0-5]) ssid_b64=([A-Za-z0-9+/=]+)", line)
        if match:
            rows.append({"index": int(match[1]), "ssid": base64.b64decode(match[2], validate=True).decode("utf-8")})
        if line.startswith("DBG SETTINGS "):
            rows.append(safe_details(line[len("DBG SETTINGS "):]))
    return {"status": result.status if result.status in {"PASS", "FAIL", "SKIP"} else "UNKNOWN",
            "details": safe_details(result.details), "rows": rows}


def safe_details(details: str) -> dict:
    return {key: value for key, value in re.findall(r"([a-z_]+)=([A-Za-z0-9+/=,-]+)(?: |$)", details)
            if key in SAFE_KEYS and (key == "ble_name_b64" or re.fullmatch(r"-?\d+", value))}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", required=True)
    p.add_argument("--uart", action="store_true", help="CRC-framed local UART; default is USB CDC")
    sub = p.add_subparsers(dest="action", required=True)
    for action in ("get", "restore", "wifi-list"):
        sub.add_parser(action)
    edit = sub.add_parser("set")
    edit.add_argument("field", choices=FIELDS)
    edit.add_argument("value", type=int)
    edit.add_argument("--lease", type=int, default=180)
    sub.add_parser("save").add_argument("confirm", choices=["CONFIRM"])
    sub.add_parser("wifi-restart").add_argument("confirm", choices=["CONFIRM"])
    sub.add_parser("wifi-save").add_argument("--config", required=True, help="private JSON with only ssid/password")
    sub.add_parser("wifi-forget").add_argument("--ssid", required=True)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    client = None
    try:
        command = build_command(args)
        # UART wake prefix is necessary after light sleep. The shared helper only
        # retries its exact read/recovery allowlist; Wi-Fi mutations send once.
        client = DebugClient(args.port, uart=args.uart, wake_preamble=args.uart)
        reply = execute(client, command)
        print(json.dumps(reply, ensure_ascii=False))
        return 0 if reply["status"] == "PASS" else 1
    except Exception:
        print("settings request failed or invalid input; no automatic retry; read state before repeating a mutation")
        return 1
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()


if __name__ == "__main__":
    raise SystemExit(main())
