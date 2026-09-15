"""Measure USB command-to-completed-frame latency, not optical touch latency."""
import argparse
import re
import time
from serial_debug_test import DebugClient

def frame(c):
    result = c.command("debug display", "display", 5)
    return int(re.search(r"frames=(\d+)", result.details).group(1))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", required=True)
    a = p.parse_args()
    c = DebugClient(a.port)
    samples = []
    try:
        c.handshake()
        c.command("debug display-wake", "display-wake", 5)
        deadline = time.monotonic() + 120
        while True:
            result = c.command("debug network", "network", 5)
            m = re.search(r"history_accepted=(\d+)", result.details)
            if m and int(m.group(1)) > 0: break
            if time.monotonic() > deadline: raise RuntimeError("No real history cache received")
            c.command("debug display-wake", "display-wake", 5)
            until = time.monotonic() + 5
            while time.monotonic() < until: c.serial.readline()
        c.command("debug history days", "history", 5)
        # Settle previous screen updates before measuring completed frames.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline: c.serial.readline()
        for action in ["select 2", "select 3", "select 8", "select 15", "hours", "select 2", "select 3", "select 8", "days"]:
            before = frame(c)
            start = time.monotonic()
            result = c.command("debug history " + action, "history", 5)
            assert result.status == "PASS", result
            while frame(c) == before:
                if time.monotonic() - start > 3: raise RuntimeError("No completed display frame")
            elapsed = (time.monotonic() - start) * 1000
            samples.append(elapsed)
            print(f"HOST HISTORY FRAME action={action!r} elapsed_ms={elapsed:.1f}", flush=True)
        print(f"HOST HISTORY LATENCY max_ms={max(samples):.1f} mean_ms={sum(samples)/len(samples):.1f} samples={len(samples)} optical=0")
        if max(samples) >= 1000: raise RuntimeError("History rendering exceeded one second")
    finally: c.close()

if __name__ == "__main__": main()
