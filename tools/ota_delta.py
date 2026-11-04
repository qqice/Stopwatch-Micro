"""Bounded, optional raw detools/heatshrink OTA patch generation. No signing keys."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
from io import BytesIO
import os
from pathlib import Path
import tempfile
import threading

ALGORITHM = "detools-heatshrink-v1"
MAX_PATCH_SIZE = 2 * 1024 * 1024


def archive_image(directory: Path, image: bytes, sha: str) -> bool:
    """Best-effort exclusive publication of a validated image, never overwriting history."""
    temporary = None
    try:
        missing = []
        parent = directory
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for parent in reversed(missing):
            parent.mkdir(mode=0o700, exist_ok=True)
        destination = directory / (sha + ".bin")

        def matches_existing():
            if destination.is_symlink() or destination.resolve().parent != directory:
                return False
            with destination.open("rb") as handle:
                existing = handle.read(len(image) + 1)
            return len(existing) == len(image) and hashlib.sha256(existing).hexdigest() == sha

        if destination.exists() or destination.is_symlink():
            return matches_existing()
        fd, name = tempfile.mkstemp(prefix=".ota-archive-", dir=directory)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as handle:
            # mkstemp creates private 0600 files; fsync precedes atomic publication.
            handle.write(image)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            return matches_existing()
        return True
    except OSError:
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


class _BoundedBuffer(BytesIO):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def write(self, data):
        if self.tell() + len(data) > self.limit:
            raise ValueError("OTA output too large")
        return super().write(data)


def create_verified_patch(base: bytes, target: bytes) -> bytes:
    # Optional dependency: full OTA must remain usable without it installed.
    import detools
    output = _BoundedBuffer(MAX_PATCH_SIZE)
    detools.create_patch(BytesIO(base), BytesIO(target), output, compression="heatshrink")
    patch = output.getvalue()
    replay = _BoundedBuffer(len(target))
    detools.apply_patch(BytesIO(base), BytesIO(patch), replay)
    if replay.getvalue() != target:
        raise ValueError("OTA patch replay mismatch")
    return patch


class DeltaStore:
    """Per-release two-entry immutable cache; at most one generation at a time."""
    def __init__(self, target, target_sha, capacity, *, enabled=False, base_dir=None, max_percent=60):
        if type(enabled) is not bool or type(max_percent) is not int or not 10 <= max_percent <= 90:
            raise ValueError("invalid delta configuration")
        self._target, self._sha, self._capacity = target, target_sha, capacity
        self._enabled, self._max_percent = enabled, max_percent
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None
        self._generation_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cache = OrderedDict()
        self._archive_succeeded = (archive_image(self._base_dir, target, target_sha)
                                   if enabled and self._base_dir is not None else False)

    def plan(self, base_sha):
        def full(reason):
            return {"mode": "full", "target_sha256": self._sha, "reason": reason}
        if not self._enabled:
            return full("disabled")
        if base_sha == self._sha:
            return full("same-image")
        with self._cache_lock:
            for metadata, _ in self._cache.values():
                if metadata["base_sha256"] == base_sha:
                    return dict(metadata)
        if not self._generation_lock.acquire(blocking=False):
            return full("busy")
        try:
            if self._base_dir is None:
                return full("unknown-base")
            path = self._base_dir / (base_sha + ".bin")
            # The only accepted archive name is the validated content hash.
            if path.is_symlink() or path.resolve().parent != self._base_dir:
                return full("unknown-base")
            try:
                with path.open("rb") as handle:
                    base = handle.read(self._capacity + 1)
            except OSError:
                return full("unknown-base")
            if not 0 < len(base) <= self._capacity or hashlib.sha256(base).hexdigest() != base_sha:
                return full("invalid-base")
            try:
                patch = create_verified_patch(base, self._target)
            except Exception:
                # Codec absence or errors never prevent the signed full release.
                return full("codec-unavailable")
            if not patch or len(patch) > MAX_PATCH_SIZE or len(patch) * 100 >= len(self._target) * self._max_percent:
                return full("not-beneficial")
            sha = hashlib.sha256(patch).hexdigest()
            metadata = {"mode": "delta", "algorithm": ALGORITHM, "base_sha256": base_sha,
                        "base_size": len(base), "target_sha256": self._sha,
                        "target_size": len(self._target), "sha256": sha, "size": len(patch)}
            with self._cache_lock:
                self._cache[sha] = (metadata, patch)
                while len(self._cache) > 2:
                    self._cache.popitem(last=False)
            return dict(metadata)
        finally:
            self._generation_lock.release()

    def image(self, sha):
        with self._cache_lock:
            entry = self._cache.get(sha)
            if entry is None:
                raise LookupError("unknown OTA delta")
            return entry[1]
