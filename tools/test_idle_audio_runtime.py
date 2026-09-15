"""Verify audio auto-suspend, radio idle and minute-rate display on hardware."""
import argparse
import time
from serial_debug_test import DebugClient

def values(result):
    return dict(x.split("=", 1) for x in result.details.split() if "=" in x)

def drain(c, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end: c.serial.readline()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", required=True)
    a = p.parse_args()
    c = DebugClient(a.port)
    try:
        c.handshake()
        c.command("debug display-wake", "display-wake", 5)
        for _ in range(2):
            c.command("debug tone 880 150", "tone", 5)
            drain(c, 1)
            assert values(c.command("debug power", "power", 5)).get("audio_suspended") == "1"
        c.command("debug display-lock", "display-lock", 5)
        deadline = time.monotonic() + 60
        while True:
            v = values(c.command("debug power", "power", 5))
            if v.get("phase") == "2": break
            if time.monotonic() > deadline: raise RuntimeError("did not reach radio-off phase")
            drain(c, 3)
        assert v.get("audio_suspended") == "1" and v.get("cpu_mhz") == "80", v
        assert v.get("wifi_running") == "0" and v.get("bt_advertising") == "0" and v.get("bt_connected") == "0", v
        before = values(c.command("debug display", "display", 5))
        drain(c, 65)
        after = values(c.command("debug display", "display", 5))
        assert int(after["refreshes"]) - int(before["refreshes"]) == 1, (before, after)
        assert 1 <= int(after["frames"]) - int(before["frames"]) <= 3, (before, after)
        print("HOST IDLE AUDIO PASS replay=2 audio_suspended=1 wifi_off=1 ble_disconnected=1 cpu80=1 minute_refresh=1 energy_measured=0")
    finally:
        try: c.command("debug display-wake", "display-wake", 5)
        finally: c.close()

if __name__ == "__main__": main()
