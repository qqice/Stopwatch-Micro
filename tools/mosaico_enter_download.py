#!/usr/bin/env python3
"""Experimental runtime CDC -> ROM transition; never writes flash or partitions.
Snapshots are not atomic reset admission. Use manual BOOT during gauge maintenance.
"""
import argparse
import contextlib
import io
import re
import time
import serial
from serial.tools import list_ports
from serial_debug_test import DebugClient

def fields(result):
    return dict(item.split("=", 1) for item in result.details.split() if "=" in item)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    args = parser.parse_args()
    ports = [p for p in list_ports.comports() if p.device.upper() == args.port.upper()]
    if len(ports) != 1 or (ports[0].vid, ports[0].pid) != (0x303A, 0x4001):
        raise SystemExit("Refused: explicit port is not the expected Mosaico runtime CDC interface")
    # Reject unrelated ROM interfaces already present before the transition.
    previous_rom = {(p.device, p.hwid) for p in list_ports.comports()
                    if (p.vid, p.pid) == (0x303A, 0x0020)}
    runtime_serial = ports[0].serial_number
    client = DebugClient(args.port)
    captured = io.StringIO()
    try:
        # Suppress ordinary firmware logs: this helper must not dump peer/session
        # metadata or provisioning material to an upgrade transcript.
        with contextlib.redirect_stdout(captured):
            client.handshake(30)
            if client.command("debug status", "status").status != "PASS":
                raise SystemExit("Refused: no runtime status")
            if not re.search(r"DBG STATUS firmware=\S*mosaico", captured.getvalue()):
                raise SystemExit("Refused: runtime is not this Mosaico monitor firmware")
            if client.command("debug display-wake", "display-wake").status != "PASS":
                raise SystemExit("Refused: cannot leave the background reload window")
            deadline = time.monotonic() + 15
            while True:
                power = fields(client.command("debug power", "power"))
                if power.get("locked") == "0" and power.get("phase") == "0":
                    break
                if time.monotonic() >= deadline:
                    raise SystemExit("Refused: no safe awake phase")
                time.sleep(0.25)
            info = client.command("debug gauge-boot", "gauge-boot", 5)
            if info.status == "FAIL" or fields(info).get("state") not in ("0", "1", "2"):
                raise SystemExit("Refused: gauge transaction requires inspection")
            gauge = client.command("debug gauge", "gauge", 5)
            values = fields(gauge)
            try:
                operation = int(values["op_status"], 16)
            except (KeyError, ValueError):
                raise SystemExit("Refused: gauge safety status unavailable")
            if (gauge.status != "PASS" or values.get("valid") != "1"
                    or ((operation >> 1) & 3) != 3 or operation & 0x0401):
                raise SystemExit("Refused: gauge must be sealed, outside CFG and CAL")
            if time.monotonic() >= deadline:
                raise SystemExit("Refused: safety checks exceeded the short awake window")
            if client.command("debug display-wake", "display-wake", 3).status != "PASS":
                raise SystemExit("Refused: final wake failed")
            final_power = fields(client.command("debug power", "power", 3))
            if final_power.get("locked") != "0" or final_power.get("phase") != "0":
                raise SystemExit("Refused: awake phase changed before ROM request")
        # Gauge snapshot waits for its transaction lock; awake phase prevents a
        # new background reload from starting. Only now touch the CDC baud rate.
        print("Mosaico runtime verified; requesting volatile ROM boot, no flash write")
        try:
            client.serial.baudrate = 1200
        except serial.SerialException:
            # A disconnect can race the host control-transfer acknowledgement.
            # Success is determined ONLY by a fresh ROM enumeration below.
            pass
    finally:
        client.close()
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        found = [p for p in list_ports.comports()
                 if (p.vid, p.pid) == (0x303A, 0x0020)
                 and (p.device, p.hwid) not in previous_rom
                 and (not runtime_serial or not p.serial_number
                      or p.serial_number == runtime_serial)]
        if len(found) == 1:
            print("ROM_CANDIDATE_PORT=" + found[0].device)
            print("Next step must verify chip/MAC/security/layout before any write")
            return
        if len(found) > 1:
            raise SystemExit("Ambiguous ROM devices: do not select an unrelated board")
        time.sleep(0.5)
    raise SystemExit("ROM enumeration failed; no flash write. Manual BOOT/replug is required")

if __name__ == "__main__":
    main()
