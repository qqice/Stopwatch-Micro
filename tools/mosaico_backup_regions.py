#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Export erase-aligned rollback ranges from an existing snapshot, verified by ROM MD5.

No flash read stream, flash write, erase, reset, or stub upload is performed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

import esptool

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".artifacts/private/mosaico"
EXPECTED_MAC = "30:ed:a0:f4:0b:fc"
NOR_SIZE = 16 * 1024 * 1024
SECTOR = 4096
ALLOWED = ((0x2000, 0x9000), (0x9000, 0xA000),
           (0x10000, 0x12000), (0x20000, 0x7F0000))


def prepare(source, manifest, output_dir):
    """Validate all inputs and freeze source slices before opening the device."""
    output_dir = output_dir.resolve()
    if (not output_dir.is_relative_to(PRIVATE.resolve())
            or output_dir == PRIVATE.resolve() or not output_dir.is_dir()
            or any(output_dir.iterdir())):
        raise ValueError("output-dir must be a prepared empty private subdirectory")
    blob = source.read_bytes()
    if not blob or len(blob) > NOR_SIZE:
        raise ValueError("source must be a nonempty existing NOR snapshot of at most 16MiB")
    plan = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or not isinstance(plan.get("regions"), list) or not plan["regions"]:
        raise ValueError("manifest requires a nonempty regions list")
    regions = []
    for region in plan["regions"]:
        if not isinstance(region, dict):
            raise ValueError("region must contain integer offset and length")
        offset, length = region.get("offset"), region.get("length")
        if (type(offset) is not int or type(length) is not int or offset < 0
                or length <= 0 or offset % SECTOR or length % SECTOR):
            raise ValueError("regions must have positive length and 4KiB-aligned integer bounds")
        end = offset + length
        if end > len(blob) or not any(start <= offset and end <= stop for start, stop in ALLOWED):
            raise ValueError("range is outside the source or the allowed rollback regions")
        data = blob[offset:end]
        regions.append({"offset": offset, "length": length,
                        "file": f"offset-{offset:08x}.bin", "data": data,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "md5": hashlib.md5(data).hexdigest()})
    regions.sort(key=lambda item: item["offset"])
    for left, right in zip(regions, regions[1:]):
        if left["offset"] + left["length"] > right["offset"]:
            raise ValueError("rollback regions must not overlap")
    names = [region["file"] for region in regions] + ["backup-regions.json"]
    if any((output_dir / name).exists() or (output_dir / name).is_symlink() for name in names):
        raise ValueError("all export filenames must be new")
    return output_dir, regions, len(blob), hashlib.sha256(blob).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", required=True)
    args = parser.parse_args()
    output_dir, regions, source_size, source_sha = prepare(args.source, args.manifest, args.output_dir)
    esp = esptool.detect_chip(args.port, connect_mode="no-reset", connect_attempts=5)
    try:
        if esp.IS_STUB:
            raise SystemExit("Refused: physical fresh ROM required; active stub detected")
        if esp.IMAGE_CHIP_ID != 32 or esp.CHIP_NAME != "ESP32-S31":
            raise SystemExit("Refused: wrong chip")
        mac = ":".join(f"{byte:02x}" for byte in esp.read_mac())
        if mac != EXPECTED_MAC:
            raise SystemExit("Refused: wrong physical unit")
        security = esp.get_security_info()
        if security["flags"] & 1 or security["flash_crypt_cnt"].bit_count() % 2:
            raise SystemExit("Refused: unexpected secure boot / flash encryption state")
        esptool.attach_flash(esp, flash_type="nor")
        if (esp.flash_id() >> 16) & 255 not in (0x18, 0x38):
            raise SystemExit("Refused: flash is not the expected 16MiB NOR")
        esp.flash_set_parameters(NOR_SIZE)  # Volatile geometry only, not a flash write.
        for region in regions:
            if esp.flash_md5sum(region["offset"], region["length"]) != region["md5"]:
                raise SystemExit(f"Refused: device/source MD5 mismatch at {region['offset']:#x}")
        # No exports until every requested range has passed its live ROM digest check.
        for region in regions:
            path = output_dir / region["file"]
            with path.open("xb") as stream:
                stream.write(region["data"])
                stream.flush()
                os.fsync(stream.fileno())
            if hashlib.sha256(path.read_bytes()).hexdigest() != region["sha256"]:
                raise SystemExit("Export integrity check failed; no complete manifest written")
        record = {"complete": True, "chip": esp.CHIP_NAME, "mac": mac,
                  "source_bytes": source_size, "source_sha256": source_sha,
                  "transport": "rom-md5-only", "erase_alignment": SECTOR,
                  "regions": [{key: value for key, value in region.items() if key != "data"}
                              for region in regions],
                  "flash_write_performed": False, "sensitive": True,
                  "scope": "Only the explicitly planned 4KiB-aligned rollback ranges",
                  "restore_note": "Read-only backup export. Restoration is not performed; "
                                  "requires separate authorization and exact recorded offsets/lengths. "
                                  "Never restore or erase outside these ranges."}
        with (output_dir / "backup-regions.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(record, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        print(f"ROLLBACK_REGIONS_VERIFIED count={len(regions)}", flush=True)
    finally:
        esp._port.close()


if __name__ == "__main__":
    main()
