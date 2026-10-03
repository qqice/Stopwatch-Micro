"""Validate the preserved Mosaico layout and XIP app before app-only flashing."""
import argparse, hashlib, os, struct, sys
from pathlib import Path

def validate(prefix: bytes, image: bytes, compiled_table: bytes, idf: Path, bootloader_hash: str | None = None):
    sys.path.insert(0, str(idf / "components/partition_table"))
    from gen_esp32part import PartitionTable
    if len(prefix) < 0xa000:
        raise ValueError("read the complete protected prefix through the table at0x9000")
    if bootloader_hash and hashlib.sha256(prefix[:0x9000]).hexdigest() != bootloader_hash:
        raise ValueError("unvalidated bootloader; do not assume another unit's boot contract")
    actual = PartitionTable.from_binary(prefix[0x9000:0x9c00]); actual.verify()
    expected = PartitionTable.from_binary(compiled_table); expected.verify()
    if actual.to_binary() != expected.to_binary():
        raise ValueError("live layout differs from compiled layout; do not overwrite the device table")
    app = next((p for p in actual if p.name == "factory" and p.type == 0 and p.subtype == 0), None)
    if app is None or app.offset != 0x20000 or len(image) > app.size:
        raise ValueError("image does not fit the verified factory app")
    if len(image) < 288 or image[0] != 0xe9 or struct.unpack_from("<H", image, 12)[0] != 32:
        raise ValueError("not an ESP32-S31 image")
    if image[80:112].split(b"\0")[0] != b"Stopwatch-Mosaico":
        raise ValueError("wrong project image")
    position = 24; xip = False
    for _ in range(image[1]):
        if position + 8 > len(image): raise ValueError("truncated segment header")
        address, size = struct.unpack_from("<II", image, position)
        position += 8 + size
        if position > len(image): raise ValueError("truncated segment data")
        if 0x40000000 <= address < 0x44000000:
            raise ValueError("Flash-XIP image conflicts with this preserved PSRAM-XIP boot contract")
        xip |= 0x50000000 <= address < 0x54000000
    if not xip: raise ValueError("PSRAM-XIP mapping missing")
    return app.offset, app.size

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prefix", type=Path, required=True)
    p.add_argument("--image", type=Path, required=True)
    p.add_argument("--table", type=Path, required=True)
    p.add_argument("--bootloader-sha256")
    p.add_argument("--idf", type=Path, default=Path(os.environ.get("IDF_PATH", "C:/esp/v6.1/esp-idf")))
    a = p.parse_args()
    offset, size = validate(a.prefix.read_bytes(), a.image.read_bytes(), a.table.read_bytes(), a.idf, a.bootloader_sha256)
    print(f"MOSAICO APP GUARD PASS offset={offset:#x} capacity={size} image={a.image.stat().st_size}")
if __name__ == "__main__": main()
