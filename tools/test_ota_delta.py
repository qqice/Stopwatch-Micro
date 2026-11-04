"""Host-only delta policy and transport tests; codec is optional."""
import base64
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ota_delta
from ota_release import ImmutableReleaseStore, METADATA
import quota_service


class DeltaTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / ".artifacts"
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bases = self.root / "bases"
        self.bases.mkdir()
        self.target = b"target" * 2000
        self.sha = hashlib.sha256(self.target).hexdigest()
        self.manifest = dict(METADATA, schema=2, version="test", size=len(self.target), sha256=self.sha,
                             signature=base64.b64encode(bytes.fromhex("3006020101020101")).decode())
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        (self.root / "firmware.bin").write_bytes(self.target)
        self.base = self.archive(b"base" * 2000)

    def archive(self, data):
        sha = hashlib.sha256(data).hexdigest()
        (self.bases / (sha + ".bin")).write_bytes(data)
        return sha

    def store(self, **kwargs):
        return ImmutableReleaseStore(self.root, delta_enabled=True, delta_base_dir=self.bases, **kwargs)

    def plan(self, store, base=None):
        return json.loads(store.response(f"/v1/ota/plan?base={base or self.base}&target={self.sha}"))

    def test_disabled_same_unknown_and_missing_codec(self):
        self.assertEqual(self.plan(ImmutableReleaseStore(self.root))["reason"], "disabled")
        store = self.store()
        with patch("ota_delta.create_verified_patch", side_effect=ImportError) as codec:
            self.assertEqual(self.plan(store, self.sha)["reason"], "same-image")
            self.assertEqual(self.plan(store, "f" * 64)["reason"], "unknown-base")
            codec.assert_not_called()
            self.assertEqual(self.plan(store)["mode"], "full")

    def test_archive_first_publication_and_existing_valid_image(self):
        directory = self.root / "private" / "new-bases"
        store = ImmutableReleaseStore(self.root, delta_enabled=True, delta_base_dir=directory)
        destination = directory / (self.sha + ".bin")
        self.assertTrue(store._delta._archive_succeeded)
        self.assertEqual(destination.read_bytes(), self.target)
        if os.name != "nt":
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        before = destination.stat().st_mtime_ns
        with patch("ota_delta.os.link") as link:
            self.assertTrue(ImmutableReleaseStore(self.root, delta_enabled=True,
                                                delta_base_dir=directory)._delta._archive_succeeded)
            link.assert_not_called()
        self.assertEqual(destination.stat().st_mtime_ns, before)
        self.assertEqual(list(directory.iterdir()), [destination])

    def test_archive_collision_never_overwrites_and_disabled_never_writes(self):
        destination = self.bases / (self.sha + ".bin")
        destination.write_bytes(b"preserve corrupt historical file")
        store = self.store()
        self.assertFalse(store._delta._archive_succeeded)
        self.assertEqual(destination.read_bytes(), b"preserve corrupt historical file")
        self.assertEqual(json.loads(store.response("/v1/ota/manifest")), self.manifest)
        disabled_path = self.root / "must-not-create"
        with patch("ota_delta.archive_image") as archive:
            ImmutableReleaseStore(self.root, delta_base_dir=disabled_path)
            archive.assert_not_called()
        self.assertFalse(disabled_path.exists())

    def test_archive_io_failure_preserves_full_service(self):
        with patch("ota_delta.os.link", side_effect=OSError("archive unavailable")):
            store = self.store()
        self.assertFalse(store._delta._archive_succeeded)
        self.assertEqual(json.loads(store.response("/v1/ota/manifest")), self.manifest)
        chunk = json.loads(store.response(f"/v1/ota/chunk?sha256={self.sha}&offset=0"))
        self.assertEqual(base64.b64decode(chunk["data"]), self.target[:4096])
        self.assertFalse((self.bases / (self.sha + ".bin")).exists())
        self.assertEqual(list(self.bases.iterdir()), [self.bases / (self.base + ".bin")])

    def test_corrupt_oversize_base_and_symlink(self):
        store = self.store()
        (self.bases / (self.base + ".bin")).write_bytes(b"wrong")
        with patch("ota_delta.create_verified_patch") as codec:
            self.assertEqual(self.plan(store)["reason"], "invalid-base")
            (self.bases / (self.base + ".bin")).write_bytes(b"x" * (0x3e0000 + 1))
            self.assertEqual(self.plan(store)["reason"], "invalid-base")
            codec.assert_not_called()
        link = self.bases / ("e" * 64 + ".bin")
        try:
            link.symlink_to(self.root / "firmware.bin")
        except OSError:
            return  # Windows without symlink privilege; containment also tested in config.
        self.assertEqual(self.plan(store, "e" * 64)["reason"], "unknown-base")

    def test_patch_cache_chunks_eviction_and_immutable_release(self):
        store = self.store()
        with patch("ota_delta.create_verified_patch", return_value=b"p" * 5000) as codec:
            result = self.plan(store)
            self.assertEqual(result["algorithm"], "detools-heatshrink-v1")
            self.assertEqual(result["base_size"], 8000)
            self.assertEqual(result["target_size"], len(self.target))
            self.assertEqual(result["target_sha256"], self.sha)
            (self.root / "firmware.bin").write_bytes(b"new release")
            self.assertEqual(json.loads(store.response("/v1/ota/manifest")), self.manifest)
            data = b""
            for offset in (0, 4096):
                chunk = json.loads(store.response(f'/v1/ota/delta?sha256={result["sha256"]}&offset={offset}'))
                self.assertEqual(chunk["offset"], offset)
                data += base64.b64decode(chunk["data"])
            self.assertEqual(data, b"p" * 5000)
            self.assertEqual(self.plan(store), result)
            codec.assert_called_once()
            for i in (1, 2):
                codec.return_value = bytes([i]) * 100
                self.plan(store, self.archive(bytes([i]) * 1000))
        self.assertEqual(len(store._delta._cache), 2)
        with self.assertRaises(LookupError):
            store.response(f'/v1/ota/delta?sha256={result["sha256"]}&offset=0')
        (self.root / "firmware.bin").write_bytes(self.target)
        with self.assertRaises(LookupError):
            self.store().response(f'/v1/ota/delta?sha256={result["sha256"]}&offset=0')

    def test_threshold_is_strict_and_hard_cap(self):
        for size, mode in ((7199, "delta"), (7200, "full"), (ota_delta.MAX_PATCH_SIZE + 1, "full")):
            with patch("ota_delta.create_verified_patch", return_value=b"p" * size):
                self.assertEqual(self.plan(self.store())["mode"], mode)
        with patch("ota_delta.create_verified_patch", return_value=b"p" * 1200):
            self.assertEqual(self.plan(self.store(delta_max_percent=10))["mode"], "full")

    def test_nonblocking_busy_generation(self):
        store = self.store()
        entered, release = threading.Event(), threading.Event()
        def codec(*_):
            entered.set()
            self.assertTrue(release.wait(5))
            return b"patch"
        results = []
        with patch("ota_delta.create_verified_patch", side_effect=codec) as mock:
            worker = threading.Thread(target=lambda: results.append(self.plan(store)))
            worker.start()
            self.assertTrue(entered.wait(5))
            try:
                self.assertEqual(self.plan(store)["reason"], "busy")
                mock.assert_called_once()
            finally:
                release.set()
                worker.join(5)
            self.assertEqual(results[0]["mode"], "delta")

    def test_strict_queries(self):
        store = self.store()
        query = f"base={self.base}&target={self.sha}"
        invalid = ["", query + "&base=x", query + "&extra=x", query + "&", query + "#x",
                   query.replace(self.base, self.base.upper()), query.replace(self.sha, "e" * 64),
                   query.replace("base=", "base=../")]
        for q in invalid:
            with self.subTest(q=q), self.assertRaises(ValueError):
                store.response("/v1/ota/plan?" + q)
        for q in ("sha256=x&offset=0", f"sha256={self.sha}&offset=00", f"sha256={self.sha}&offset=0&x=1"):
            with self.assertRaises(ValueError):
                store.response("/v1/ota/delta?" + q)

    def test_codec_replay_and_bounded_outputs(self):
        fake = types.SimpleNamespace()
        def create(base, target, output, **kwargs):
            self.assertEqual(kwargs, {"compression": "heatshrink"})
            self.assertEqual(base.read(), b"old")
            self.assertEqual(target.read(), b"new")
            output.write(b"patch")
        def apply(base, patch_file, output):
            self.assertEqual(patch_file.read(), b"patch")
            output.write(b"new")
        fake.create_patch, fake.apply_patch = create, apply
        with patch.dict(sys.modules, detools=fake):
            self.assertEqual(ota_delta.create_verified_patch(b"old", b"new"), b"patch")
            fake.apply_patch = lambda a, b, out: out.write(b"bad")
            with self.assertRaises(ValueError):
                ota_delta.create_verified_patch(b"old", b"new")
            fake.apply_patch = lambda a, b, out: out.write(b"too large")
            with self.assertRaises(ValueError):
                ota_delta.create_verified_patch(b"old", b"new")
            fake.create_patch = lambda a, b, out, **kw: out.write(b"p" * (ota_delta.MAX_PATCH_SIZE + 1))
            with self.assertRaises(ValueError):
                ota_delta.create_verified_patch(b"old", b"new")

    def test_config_validation(self):
        config = self.root / "config.json"
        raw = {"device_token": "0123456789abcdef"}
        config.write_text(json.dumps(raw))
        value = quota_service.load_config(config)
        self.assertFalse(value.ota_delta_enabled)
        self.assertEqual(value.ota_delta_base_dir, self.root / "private" / "ota-bases")
        for field, values in {"ota_delta_enabled": [1, "true", None],
                              "ota_delta_max_percent": [True, 9, 91, 60.0],
                              "ota_delta_base_dir": ["../escape", str(self.root), "", None]}.items():
            for value in values:
                config.write_text(json.dumps(dict(raw, **{field: value})))
                with self.subTest(field=field, value=value), self.assertRaises(quota_service.BridgeError):
                    quota_service.load_config(config)
        config.write_text(json.dumps(dict(raw, ota_delta_enabled=True, ota_delta_base_dir="bases", ota_delta_max_percent=50)))
        self.assertTrue(quota_service.load_config(config).ota_delta_enabled)

    def test_http_auth_and_status(self):
        server = quota_service.ThreadingHTTPServer(("127.0.0.1", 0), quota_service.make_handler(
            quota_service.SnapshotStore(), "0123456789abcdef", release_store=self.store()))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            root = f"http://127.0.0.1:{server.server_port}"
            paths = [(f"/v1/ota/plan?base={self.base}&target={self.sha}", 200),
                     ("/v1/ota/plan?bad=1", 400),
                     (f"/v1/ota/delta?sha256={self.sha}&offset=0", 404)]
            for path, status in paths:
                for auth in (False, True):
                    request = urllib.request.Request(root + path, headers={"Authorization": "Bearer 0123456789abcdef"} if auth else {})
                    try:
                        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request) as response:
                            actual = response.status
                    except urllib.error.HTTPError as exc:
                        actual = exc.code
                    self.assertEqual(actual, status if auth else 401)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
