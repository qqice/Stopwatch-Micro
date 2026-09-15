#!/usr/bin/env python3
"""Observe one StopWatch eco idle-radio cycle over USB Serial/JTAG.

Do not interact with the device during this test. It reports radio-off duty
time, not measured energy or battery-life savings.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

from serial_debug_test import DebugClient, discover_port  # noqa: E402


def fields(details: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in details.split() if "=" in item)


def power(client: DebugClient) -> dict[str, str]:
    result = client.command("debug power", "power", timeout=4)
    if result.status != "PASS":
        raise RuntimeError(f"power status={result.status} {result.details}")
    return fields(result.details)


def drain(client: DebugClient, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        line = client.serial.readline().decode(errors="replace").strip()
        if "IdlePower" in line:
            print(line, flush=True)


def wait_for_offline(client: DebugClient, deadline: float) -> tuple[dict[str, str], dict[str, str]]:
    first: dict[str, str] | None = None
    while time.monotonic() < deadline:
        current = power(client)
        first = first or current
        print("HOST POWER " + " ".join(f"{key}={value}" for key, value in sorted(current.items())))
        if (current.get("phase") == "2" and current.get("wifi_running") == "0" and
                current.get("bt_connected") == "0" and current.get("bt_advertising") == "0" and
                current.get("cpu_mhz") == "80"):
            return first, current
        drain(client, 12)
    raise TimeoutError("did not reach offline eco phase within 400 seconds")


def wait_next_cycle(client: DebugClient, previous_cycles: int, deadline: float) -> dict[str, str]:
    while time.monotonic() < deadline:
        current = power(client)
        print("HOST POWER " + " ".join(f"{key}={value}" for key, value in sorted(current.items())))
        if (int(current.get("cycles", "0")) >= previous_cycles + 1 and current.get("phase") == "2" and
                current.get("wifi_running") == "0" and current.get("bt_connected") == "0" and
                current.get("bt_advertising") == "0" and current.get("cpu_mhz") == "80"):
            return current
        drain(client, 12)
    raise TimeoutError("did not complete one periodic refresh cycle within 400 seconds")


def run(client: DebugClient) -> None:
    client.command("debug display-wake", "display-wake", timeout=4)
    client.command("debug power baseline", "power", timeout=4)
    warmup_deadline = time.monotonic() + 120
    while True:
        network = fields(client.command("debug network", "network", timeout=4).details)
        if int(network.get("accepted", "0")) > 0:
            break
        if time.monotonic() > warmup_deadline:
            raise RuntimeError("active network did not fetch quota before stop/start test")
        drain(client, 5)
    before_lock = power(client)
    client.command("debug power eco", "power", timeout=4)
    result = client.command("debug display-lock", "display-lock", timeout=4)
    if result.status != "PASS":
        raise RuntimeError("device interaction prevented lock")
    started = time.monotonic()
    _before, offline = wait_for_offline(client, started + 400)
    measurement_started = time.monotonic()
    off_before = int(offline.get("off_ms", "0"))
    cycles_before = int(offline.get("cycles", "0"))
    print("HOST POWER offline reached; waiting for one five-minute refresh cycle")
    after = wait_next_cycle(client, cycles_before, time.monotonic() + 400)
    cycles_after = int(after.get("cycles", "0"))
    if cycles_after < cycles_before + 1:
        raise RuntimeError(f"refresh cycle did not increment: before={cycles_before} after={cycles_after}")
    elapsed_ms = max(1, int((time.monotonic() - measurement_started) * 1000))
    off_ms = int(after.get("off_ms", "0")) - off_before
    print(f"HOST POWER radio_off_ratio={off_ms / elapsed_ms:.3f} off_ms={off_ms} elapsed_ms={elapsed_ms}")
    client.command("debug display-wake", "display-wake", timeout=4)
    deadline = time.monotonic() + 90
    while True:
        wake = power(client)
        connected = wake.get("cpu_mhz") == "240" and wake.get("wifi_connected") == "1"
        ble_ok = before_lock.get("bt_connected") != "1" or wake.get("bt_connected") == "1"
        if connected and ble_ok:
            break
        if time.monotonic() > deadline:
            raise RuntimeError("wake did not restore CPU/Wi-Fi/prior BLE connection")
        client.command("debug display-wake", "display-wake", timeout=4)
        drain(client, 5)
    print("HOST POWER PASS real_cycle=1 wake_restored=1 energy_measured=0", flush=True)



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="USB Serial/JTAG port, for example COM24")
    args = parser.parse_args()
    client: DebugClient | None = None
    try:
        client = DebugClient(discover_port(args.port))
        client.handshake()
        run(client)
        return 0
    except Exception as exc:
        print(f"HOST ERROR {exc}")
        return 2
    finally:
        if client is not None:
            try:
                client.command("debug power eco", "power", timeout=4)
                client.command("debug display-wake", "display-wake", timeout=4)
            except Exception:
                pass
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
