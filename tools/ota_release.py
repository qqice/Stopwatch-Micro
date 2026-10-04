"""Immutable, read-only OTA release serving (stdlib only; no signing keys)."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MAX_IMAGE_SIZE = 0x3E0000
CHUNK_SIZE = 4096
METADATA = {"schema": 1, "board": "esp-mosaico", "chip": "esp32s31",
            "project": "Stopwatch-Mosaico", "layout": "mosaico-dual-v1"}
HASH_RE = re.compile(r"[0-9a-f]{64}")


def valid_version(version: object) -> bool:
    return isinstance(version, str) and 0 < len(version) <= 31 and all(33 <= ord(c) <= 126 for c in version)


def canonical_message(size: int, sha256: str, version: str | None = None) -> bytes:
    if version is not None and not valid_version(version):
        raise ValueError("invalid OTA version")
    return (f"MOSAICO-OTA-v{2 if version is not None else 1}\nStopwatch-Mosaico\nesp32s31\nmosaico-dual-v1\n"
            + (version + "\n" if version is not None else "")
            + f"{size}\n{sha256}\n").encode("ascii")


def validate_manifest(manifest: object) -> dict:
    if not isinstance(manifest, dict):
        raise ValueError("invalid OTA manifest fields")
    schema = manifest.get("schema")
    fields = set(METADATA) | {"size", "sha256", "signature"} | ({"version"} if schema == 2 else set())
    if set(manifest) != fields:
        raise ValueError("invalid OTA manifest fields")
    if type(schema) is not int or schema not in (1, 2) or any(manifest[k] != v for k, v in METADATA.items() if k != "schema"):
        raise ValueError("invalid OTA release metadata")
    if schema == 2 and not valid_version(manifest["version"]):
        raise ValueError("invalid OTA version")
    if type(manifest["size"]) is not int or not 0 < manifest["size"] <= MAX_IMAGE_SIZE:
        raise ValueError("invalid OTA image size")
    if not isinstance(manifest["sha256"], str) or HASH_RE.fullmatch(manifest["sha256"]) is None:
        raise ValueError("invalid OTA image hash")
    try:
        signature = base64.b64decode(manifest["signature"], validate=True)
        # P-256 ECDSA DER signatures have two positive integers (at most 33 bytes each).
        if not 8 <= len(signature) <= 72 or signature[0] != 0x30 or signature[1] != len(signature) - 2:
            raise ValueError()
        pos = 2
        for _ in range(2):
            if signature[pos] != 2 or not 1 <= signature[pos + 1] <= 33:
                raise ValueError()
            count = signature[pos + 1]
            value = signature[pos + 2:pos + 2 + count]
            if len(value) != count or value[0] & 0x80 or (count > 1 and value[0] == 0 and not value[1] & 0x80):
                raise ValueError()
            pos += 2 + count
        if pos != len(signature) or base64.b64encode(signature).decode("ascii") != manifest["signature"]:
            raise ValueError()
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError("invalid OTA signature encoding") from exc
    return manifest


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate OTA manifest field")
        result[key] = value
    return result


class ImmutableReleaseStore:
    """Read bounded fixed filenames once; later disk changes cannot mix releases."""
    def __init__(self, directory: Path):
        directory = Path(directory)
        with (directory / "manifest.json").open("rb") as handle:
            raw = handle.read(4097)
        if len(raw) > 4096:
            raise ValueError("OTA manifest too large")
        manifest = validate_manifest(json.loads(raw, object_pairs_hook=_unique_object))
        with (directory / "firmware.bin").open("rb") as handle:
            image = handle.read(MAX_IMAGE_SIZE + 1)
        if len(image) != manifest["size"] or hashlib.sha256(image).hexdigest() != manifest["sha256"]:
            raise ValueError("OTA release image does not match manifest")
        self._manifest = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        self._image = image
        self._sha256 = manifest["sha256"]

    def response(self, target: str) -> bytes:
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ValueError("invalid OTA request")
        if parsed.path == "/v1/ota/manifest":
            if parsed.query or "?" in target:
                raise ValueError("manifest does not accept query")
            return self._manifest
        if parsed.path != "/v1/ota/chunk":
            raise LookupError("unknown OTA route")
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if set(query) != {"sha256", "offset"} or any(len(v) != 1 for v in query.values()):
            raise ValueError("invalid OTA query")
        sha, offset = query["sha256"][0], query["offset"][0]
        if HASH_RE.fullmatch(sha) is None or sha != self._sha256:
            raise ValueError("release hash mismatch")
        if re.fullmatch(r"0|[1-9][0-9]{0,9}", offset) is None:
            raise ValueError("invalid OTA offset")
        start = int(offset)
        if start >= len(self._image):
            raise ValueError("OTA offset outside image")
        return json.dumps({"offset": start, "data": base64.b64encode(
            self._image[start:start + CHUNK_SIZE]).decode("ascii")}, separators=(",", ":")).encode("utf-8")
