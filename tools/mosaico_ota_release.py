#!/usr/bin/env python3
"""Validate and sign a Mosaico app release locally; never publish private keys."""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import json

from ota_release import MAX_IMAGE_SIZE, METADATA, canonical_message, valid_version


def validate_image(image: bytes) -> None:
    from esptool.bin_image import LoadFirmwareImage
    if not 288 <= len(image) <= MAX_IMAGE_SIZE:
        raise ValueError("image outside OTA app capacity")
    if image[0] != 0xE9 or struct.unpack_from("<H", image, 12)[0] != 32:
        raise ValueError("not an ESP32-S31 image")
    if struct.unpack_from("<I", image, 32)[0] != 0xABCD5432 or image[80:112].split(b"\0")[0] != b"Stopwatch-Mosaico":
        raise ValueError("wrong app descriptor or project")
    loaded = LoadFirmwareImage("esp32s31", image)
    if loaded.chip_id != 32 or loaded.checksum != loaded.calculate_checksum():
        raise ValueError("invalid image chip or checksum")
    if not loaded.append_digest or loaded.stored_digest != loaded.calc_digest or loaded.data_length + 32 != len(image):
        raise ValueError("invalid appended image digest or trailing data")
    xip = False
    for segment in loaded.segments:
        if 0x40000000 <= segment.addr < 0x44000000:
            raise ValueError("Flash-XIP conflicts with preserved PSRAM-XIP boot contract")
        xip |= 0x50000000 <= segment.addr < 0x54000000
    if not xip:
        raise ValueError("PSRAM-XIP mapping missing")


def sign_release(image_path: Path, key_path: Path, output_dir: Path, *, schema1: bool = False) -> dict:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    with image_path.open("rb") as source:
        image = source.read(MAX_IMAGE_SIZE + 1)
    validate_image(image)
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("signing key must be ECDSA P-256")
    version = image[48:80].split(b"\0", 1)[0].decode("ascii")
    if not valid_version(version):
        raise ValueError("invalid app descriptor version")
    sha = hashlib.sha256(image).hexdigest()
    signature = key.sign(canonical_message(len(image), sha, None if schema1 else version), ec.ECDSA(hashes.SHA256()))
    manifest = dict(METADATA, size=len(image), sha256=sha,
                    signature=base64.b64encode(signature).decode("ascii"))
    if not schema1:
        manifest.update(schema=2, version=version)
    output_dir = Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise ValueError("release output must be new or empty")
    # Unique staged files, then exclusive hard links: no existing release is overwritten.
    # manifest.json is the final publication marker; consumers only load after it exists.
    staged, published = [], []
    try:
        for name, data in (("firmware.bin", image), ("manifest.json", json.dumps(manifest, separators=(",", ":")).encode("utf-8"))):
            with tempfile.NamedTemporaryFile(dir=output_dir, prefix=".ota-", delete=False) as handle:
                staged.append(Path(handle.name))
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            final = output_dir / name
            os.link(staged[-1], final)
            published.append(final)
            staged[-1].unlink()
        return manifest
    except BaseException:
        for path in reversed(published):
            path.unlink(missing_ok=True)
        raise
    finally:
        for path in staged:
            path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--schema1", action="store_true", help="legacy bootstrap manifest without signed version")
    args = parser.parse_args()
    try:
        manifest = sign_release(args.image, args.key, args.output_dir, schema1=args.schema1)
    except Exception:
        # Do not echo exception contents: third-party parsers may include sensitive input.
        print("OTA RELEASE ERROR validation or exclusive publication failed", file=sys.stderr)
        return 2
    print(f"OTA RELEASE PASS size={manifest['size']} sha256={manifest['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
