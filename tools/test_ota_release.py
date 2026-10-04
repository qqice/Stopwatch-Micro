"""Host-only OTA protocol, authentication, immutability, and signer tests."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ota_release import ImmutableReleaseStore, METADATA, canonical_message
import mosaico_ota_release as signer
import quota_service


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.image = bytes(range(256)) * 20
        self.sha = hashlib.sha256(self.image).hexdigest()
        self.manifest = dict(METADATA, size=len(self.image), sha256=self.sha,
                             signature=base64.b64encode(bytes.fromhex("3006020101020101")).decode())
        self.write_release()

    def write_release(self):
        (self.directory / "firmware.bin").write_bytes(self.image)
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest))

    def test_chunks_and_frozen_release(self):
        store = ImmutableReleaseStore(self.directory)
        (self.directory / "firmware.bin").write_bytes(b"changed")
        (self.directory / "manifest.json").write_text("{}")
        self.assertEqual(json.loads(store.response("/v1/ota/manifest")), self.manifest)
        result = b""
        for offset in (0, 4096):
            payload = store.response(f"/v1/ota/chunk?sha256={self.sha}&offset={offset}")
            self.assertLess(len(payload), 8192)
            chunk = json.loads(payload)
            self.assertEqual(chunk["offset"], offset)
            result += base64.b64decode(chunk["data"], validate=True)
        self.assertEqual(result, self.image)

    def test_invalid_queries(self):
        store = ImmutableReleaseStore(self.directory)
        valid = f"sha256={self.sha}&offset=0"
        for query in ("", valid + "&offset=1", valid + "&extra=1", "sha256=&offset=0",
                      valid.replace(self.sha, "f" * 64), valid.replace("offset=0", "offset=-1"),
                      valid.replace("offset=0", "offset=00"), valid.replace("offset=0", "offset=5120"),
                      valid + "&", "offset=0", valid + "#fragment"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                store.response("/v1/ota/chunk?" + query)
        with self.assertRaises(ValueError):
            store.response("/v1/ota/manifest?x=1")
        with self.assertRaises(LookupError):
            store.response("/v1/ota/anything")

    def test_corrupt_release(self):
        for field, value in (("schema", True), ("board", "other"), ("size", True), ("size", 0),
                             ("sha256", self.sha.upper()), ("signature", "not base64"), ("extra", 1)):
            saved = self.manifest.copy()
            self.manifest[field] = value
            self.write_release()
            with self.subTest(field=field), self.assertRaises(ValueError):
                ImmutableReleaseStore(self.directory)
            self.manifest = saved
        self.write_release()
        (self.directory / "firmware.bin").write_bytes(b"wrong")
        with self.assertRaises(ValueError):
            ImmutableReleaseStore(self.directory)
        self.write_release()
        path = self.directory / "manifest.json"
        path.write_text(path.read_text().replace('"schema": 1', '"schema": 1, "schema": 1'))
        with self.assertRaises(ValueError):
            ImmutableReleaseStore(self.directory)

    def test_http_auth_before_parsing(self):
        server = quota_service.ThreadingHTTPServer(("127.0.0.1", 0), quota_service.make_handler(
            quota_service.SnapshotStore(), "test-token-0123456789", release_store=ImmutableReleaseStore(self.directory)))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            for path in ("/v1/ota/manifest", "/v1/ota/chunk?bad=1", "/v1/ota/unknown"):
                with self.assertRaises(urllib.error.HTTPError) as result:
                    urllib.request.urlopen(base + path)
                self.assertEqual(result.exception.code, 401)
            for path, status in (("/v1/ota/manifest", 200), (f"/v1/ota/chunk?sha256={self.sha}&offset=0", 200),
                                 ("/v1/ota/chunk?bad=1", 400), ("/v1/ota/unknown", 404)):
                req = urllib.request.Request(base + path, headers={"Authorization": "Bearer test-token-0123456789"})
                if status == 200:
                    with urllib.request.urlopen(req) as response:
                        data = response.read()
                        self.assertEqual(int(response.headers["Content-Length"]), len(data))
                        self.assertEqual(response.headers["Cache-Control"], "no-store")
                else:
                    with self.assertRaises(urllib.error.HTTPError) as result:
                        urllib.request.urlopen(req)
                    self.assertEqual(result.exception.code, status)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_signer_and_exclusive_output(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        keypath = self.directory / "test-key.pem"
        keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
        output = self.directory / "release"
        with patch.object(signer, "validate_image"):
            manifest = signer.sign_release(self.directory / "firmware.bin", keypath, output)
            key.public_key().verify(base64.b64decode(manifest["signature"]),
                                    canonical_message(manifest["size"], manifest["sha256"]), ec.ECDSA(hashes.SHA256()))
            ImmutableReleaseStore(output)
            saved = (output / "manifest.json").read_bytes()
            with self.assertRaises(ValueError):
                signer.sign_release(self.directory / "firmware.bin", keypath, output)
            self.assertEqual((output / "manifest.json").read_bytes(), saved)
        self.assertEqual(set(p.name for p in output.iterdir()), {"firmware.bin", "manifest.json"})


if __name__ == "__main__":
    unittest.main()
