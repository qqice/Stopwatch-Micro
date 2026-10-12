"""Synthetic-only tests: no DLL, COM, SSH, NIC or real capture."""
import contextlib
import ctypes as C
import importlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import mosaico_lan_packet_headers as candidate
from test_mosaico_lan_tcp_headers import packet, interpret, CanaryReader, LOCAL, PEER, PORT


def record(frame, caplen, *, hdrlen=20, datalen=None, padding=True):
    header = struct.pack("<iiIIH", 123, 456, caplen,
                         len(frame) if datalen is None else datalen, hdrlen)
    row = header + b"\0" * (hdrlen - len(header)) + frame[:caplen]
    return row + b"\0" * ((-len(row)) & 3) if padding else row


class FakeApi:
    def __init__(self, data=b"", bpf=True, receive=True, allocate=True, medium=0,
                 queue_reset_verified=True, stats_ok=True, reported_length=None,
                 failure=None, on_receive=None):
        self.calls = []
        self.data, self.bpf, self.receive, self.allocate, self.medium = data, bpf, receive, allocate, medium
        self.adapter, self.packet = candidate.Adapter(), candidate.Packet()
        self.offline_queue_reset_verified = queue_reset_verified
        self.stats_ok, self.reported_length = stats_ok, reported_length
        self.failure, self.on_receive = failure, on_receive
        self.timeouts = []

    def PacketOpenAdapter(self, name):
        self.calls.append("open")
        assert name == candidate.ADAPTER_NAME
        return C.pointer(self.adapter)

    def PacketGetNetType(self, adapter, pointer):
        self.calls.append("net")
        C.cast(pointer, C.POINTER(candidate.NetType)).contents.LinkType = self.medium
        return 1

    def PacketSetBpf(self, adapter, pointer):
        self.calls.append("bpf")
        program = C.cast(pointer, C.POINTER(candidate.BpfProgram)).contents
        assert program.length > 0
        return self.bpf

    def PacketAllocatePacket(self):
        self.calls.append("allocate")
        return C.pointer(self.packet) if self.allocate else None

    def PacketInitPacket(self, pointer, buffer, size):
        self.calls.append("init")
        self.buffer = buffer
        pointer.contents.Buffer = C.addressof(buffer)
        pointer.contents.Length = size

    def PacketReceivePacket(self, adapter, pointer, sync):
        self.calls.append("receive")
        assert self.calls.index("bpf") < self.calls.index("receive")
        assert self.bpf and sync == 1
        C.memmove(self.buffer, self.data, len(self.data))
        pointer.contents.ulBytesReceived = len(self.data) if self.reported_length is None else self.reported_length
        if self.on_receive:
            self.on_receive()
        return self.receive

    def PacketSetBuff(self, adapter, size):
        self.calls.append("buff")
        assert size == candidate.MAX_BATCH
        return self.failure != "buff"

    def PacketSetReadTimeout(self, adapter, timeout):
        self.calls.append("timeout")
        assert 1 <= timeout <= 250
        self.timeouts.append(timeout)
        return self.failure != "timeout"

    def PacketSetMinToCopy(self, adapter, size):
        self.calls.append("mincopy")
        assert size == 0
        return self.failure != "mincopy"

    def PacketGetStatsEx(self, adapter, pointer):
        self.calls.append("stats")
        stats = C.cast(pointer, C.POINTER(candidate.BpfStats)).contents
        stats.recv, stats.drop, stats.ifdrop, stats.capt = 12, 2, 0, 10
        return self.stats_ok

    def PacketFreePacket(self, pointer):
        self.calls.append("free")

    def PacketCloseAdapter(self, pointer):
        self.calls.append("close")


class Tests(unittest.TestCase):
    def setUp(self):
        self.config = candidate.Config(LOCAL, PEER, PORT, 120, Path.cwd() / "unused.jsonl")

    def test_abi(self):
        self.assertEqual(C.sizeof(candidate.Timeval), 8)
        self.assertEqual(C.sizeof(candidate.BpfHeader), 20)
        self.assertEqual(candidate.BpfHeader.bh_hdrlen.offset, 16)
        self.assertEqual(C.sizeof(candidate.BpfStats), 16)
        self.assertEqual(C.sizeof(candidate.BpfInsn), 8)
        if C.sizeof(C.c_void_p) == 8:
            self.assertEqual(C.sizeof(candidate.Overlapped), 32)
            self.assertEqual(C.sizeof(candidate.Packet), 64)
            self.assertEqual(candidate.Packet.Buffer.offset, 40)
            self.assertEqual(candidate.Packet.ulBytesReceived.offset, 52)
            self.assertEqual(C.sizeof(candidate.NetType), 16)

    def test_filter_and_all_record_boundaries(self):
        batch = b""
        expected = []
        for direction in (1, 2):
            for tags in ((), (0x8100,), (0x88a8, 0x8100)):
                for ihl in (20,):
                    frame, prefix, end = packet(direction, 512, tags, ihl, 60)
                    reader = CanaryReader(frame, end, (prefix + 20, prefix + ihl) if ihl > 20 else None)
                    self.assertEqual(interpret(candidate.build_filter(LOCAL, PEER, PORT), reader, len(frame)), end)
                    batch += record(frame, end, hdrlen=24)
                    expected.append(direction)
        rows = candidate.parse_batch(batch, len(batch), self.config)
        self.assertEqual([row["direction"] for row in rows], expected)
        self.assertTrue(all(row["payload_len"] == 512 for row in rows))
        self.assertTrue(all(type(value) is int for row in rows for value in row.values()))

    def test_ip_options_rejected_in_kernel_and_parser_before_options_read(self):
        for tags in ((), (0x8100,), (0x88a8, 0x8100)):
            for ihl in range(24, 61, 4):
                frame, prefix, end = packet(tags=tags, ihl=ihl)
                reader = CanaryReader(frame, end, (prefix + 20, prefix + ihl))
                self.assertEqual(interpret(candidate.build_filter(LOCAL, PEER, PORT), reader, len(frame)), 0)
                self.assertTrue(all(offset < prefix + 20 for offset, _ in reader.reads))
                with self.assertRaises(candidate.UnsafeRecord):
                    candidate.parse_header(reader, end, len(frame), LOCAL, PEER, PORT)
                data = record(frame, end)
                with self.assertRaises(candidate.UnsafeRecord):
                    candidate.parse_batch(data, len(data), self.config)

    def test_filter_structural_drift_fails_closed(self):
        with mock.patch.object(candidate, "_legacy_build_filter", return_value=[]):
            with self.assertRaises(ValueError):
                candidate.build_filter(LOCAL, PEER, PORT)

    def test_batch_overflow_nonethernet_stats_failure_fail_closed(self):
        data = b"\0" * (candidate.MAX_BATCH + 1)
        with self.assertRaises(candidate.UnsafeRecord):
            candidate.parse_batch(data, len(data), self.config)
        api = FakeApi(reported_length=candidate.MAX_BATCH + 1)
        with self.assertRaises(candidate.UnsafeRecord):
            candidate.exercise_fake_session(api, self.config)
        self.assertEqual(api.calls[-2:], ["free", "close"])
        for medium in (1, 16, 0xffffffff):
            api = FakeApi(medium=medium)
            rows, outcome, _ = candidate.exercise_fake_session(api, self.config)
            self.assertEqual((rows, outcome), ([], 1))
            self.assertEqual(api.calls, ["open", "net", "close"])
        frame, _, end = packet()
        api = FakeApi(record(frame, end), stats_ok=False)
        rows, outcome, stats = candidate.exercise_fake_session(api, self.config)
        self.assertEqual((rows, outcome, stats.recv, stats.drop), ([], 1, 0, 0))
        self.assertEqual(api.calls[-2:], ["free", "close"])

    def test_truncation_padding_oversnap_target_rejection(self):
        frame, _, end = packet()
        valid = record(frame, end)
        for data in (valid[:-1], valid + b"x", valid[:19], record(frame, end, padding=False),
                     record(frame, end + 1), record(frame, end, hdrlen=19),
                     record(frame, end, datalen=end - 1)):
            with self.subTest(length=len(data)), self.assertRaises(candidate.UnsafeRecord):
                candidate.parse_batch(data, len(data), self.config)
        for variant in (dict(wrong_peer=True), dict(wrong_port=True), dict(fragment=1),
                        dict(tags=(0x8100,) * 3), dict(protocol=17)):
            frame, _, end = packet(**variant)
            data = record(frame, end)
            with self.assertRaises(candidate.UnsafeRecord):
                candidate.parse_batch(data, len(data), self.config)
        for size in (-1, len(valid) + 1):
            with self.assertRaises(candidate.UnsafeRecord):
                candidate.parse_batch(valid, size, self.config)

    def test_offload_zero_total_unknown(self):
        frame, _, end = packet(payload=32000, total=0)
        data = record(frame, end)
        row = candidate.parse_batch(data, len(data), self.config)[0]
        self.assertEqual((row["payload_len"], row["length_unknown"]), (-1, 1))
        self.assertEqual(row["original_frame_len"], len(frame))

    def test_failure_bpf_never_allocates_receives_or_falls_back(self):
        api = FakeApi(bpf=False)
        self.assertEqual(candidate.exercise_fake_session(api, self.config)[1], 1)
        self.assertEqual(api.calls, ["open", "net", "bpf", "close"])
        api = FakeApi(medium=16)
        candidate.exercise_fake_session(api, self.config)
        self.assertEqual(api.calls, ["open", "net", "close"])
        api = FakeApi(queue_reset_verified=False)
        self.assertEqual(candidate.exercise_fake_session(api, self.config)[1], 7)
        self.assertEqual(api.calls, ["open", "net", "bpf", "close"])

    def test_owned_cleanup_success_failure_parser_and_allocate(self):
        frame, _, end = packet()
        api = FakeApi(record(frame, end))
        rows, outcome, stats = candidate.exercise_fake_session(api, self.config)
        self.assertEqual((len(rows), outcome, stats.drop), (1, 0, 2))
        self.assertEqual(api.calls[-2:], ["free", "close"])
        for api in (FakeApi(receive=False), FakeApi(allocate=False), FakeApi(b"bad")):
            try:
                candidate.exercise_fake_session(api, self.config)
            except candidate.UnsafeRecord:
                pass
            self.assertEqual(api.calls[-1], "close")
            self.assertEqual(api.calls.count("free"), int(api.allocate))

    def test_cli_exact_literal_and_silent_errors(self):
        args = ["--local", LOCAL, "--peer", PEER, "--port", str(PORT), "--ttl", "120",
                "--output", str(self.config.output)]
        self.assertEqual(candidate.parse_args(args), self.config)
        for key, value in (("--local", "192.168.1.153"), ("--peer", "localhost"),
                           ("--peer", "8.8.8.8"), ("--peer", LOCAL), ("--ttl", "121"),
                           ("--output", "relative"), ("--port", "1023")):
            altered = args.copy()
            altered[altered.index(key) + 1] = value
            error = io.StringIO()
            with contextlib.redirect_stderr(error), self.assertRaises(SystemExit):
                candidate.parse_args(altered)
            self.assertEqual(error.getvalue(), "")
        with self.assertRaises(SystemExit):
            candidate.parse_args(args + ["--force"])

    def test_import_inert_pinned_loader_and_exclusive_output(self):
        with mock.patch.object(C, "CDLL") as loader:
            importlib.reload(candidate)
            loader.assert_not_called()
            with mock.patch.object(candidate.os, "name", "posix"):
                with self.assertRaises(RuntimeError):
                    candidate.load_packet()
            with mock.patch.object(candidate.os, "name", "nt"), \
                    mock.patch("builtins.open", return_value=io.BytesIO(b"not-the-pinned-dll")):
                with self.assertRaises(RuntimeError):
                    candidate.load_packet()
            loader.assert_not_called()
            with tempfile.TemporaryDirectory() as directory:
                config = candidate.Config(LOCAL, PEER, PORT, 120, Path(directory) / "exclusive.jsonl")
                config.output.write_text("preserved")
                with mock.patch.object(candidate, "load_packet") as packet_loader:
                    with self.assertRaises(FileExistsError):
                        candidate.run_capture(config)
                    packet_loader.assert_not_called()
                self.assertEqual(config.output.read_text(), "preserved")
            loader.assert_not_called()

    def runtime(self, api, directory, ttl=1):
        clock = [0.0]
        api.on_receive = lambda: clock.__setitem__(0, clock[0] + 0.25)
        config = candidate.Config(LOCAL, PEER, PORT, ttl, Path(directory) / "runtime.jsonl")
        with mock.patch.object(candidate, "load_packet", return_value=api), \
                mock.patch.object(candidate.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(C, "CDLL") as loader:
            outcome = candidate.run_capture(config)
            loader.assert_not_called()
        records = [json.loads(line) for line in config.output.read_text().splitlines()]
        self.assertTrue(all(type(v) is int for row in records for v in row.values()))
        return outcome, records, clock[0]

    def test_runtime_ttl_ordering_and_numeric_stats(self):
        frame, _, end = packet()
        api = FakeApi(record(frame, end))
        with tempfile.TemporaryDirectory() as directory:
            outcome, rows, elapsed = self.runtime(api, directory)
        self.assertEqual((outcome, elapsed, rows[-1]["packets"]), (0, 1.0, 4))
        self.assertEqual(api.calls.count("receive"), 4)
        self.assertLess(api.calls.index("bpf"), api.calls.index("allocate"))
        self.assertLess(api.calls.index("mincopy"), api.calls.index("receive"))
        self.assertEqual(rows[1]["ready"], 1)
        self.assertEqual(rows[-1]["filter_installed"], 1)
        self.assertEqual((rows[-1]["ps_drop"], rows[-1]["drops_present"]), (2, 1))
        self.assertEqual(api.calls[-3:], ["stats", "free", "close"])

    def test_runtime_setup_failure_no_receive_and_cleanup(self):
        for api in (FakeApi(bpf=False), FakeApi(medium=16), FakeApi(failure="buff"),
                    FakeApi(failure="timeout"), FakeApi(failure="mincopy"), FakeApi(allocate=False)):
            with tempfile.TemporaryDirectory() as directory:
                outcome, rows, _ = self.runtime(api, directory)
            self.assertNotEqual(outcome, 0)
            self.assertNotIn("receive", api.calls)
            self.assertFalse(any(row.get("ready") for row in rows))
            self.assertEqual(api.calls[-1], "close")

    def test_runtime_batch_stats_receive_failure_closed(self):
        frame, _, end = packet()
        for api, expected in ((FakeApi(b"bad"), 2),
                              (FakeApi(record(frame, end), stats_ok=False), 3),
                              (FakeApi(receive=False), 1)):
            with tempfile.TemporaryDirectory() as directory:
                outcome, rows, _ = self.runtime(api, directory)
            self.assertEqual(outcome, expected)
            self.assertEqual(api.calls[-2:], ["free", "close"])
            if expected in (1, 2):
                self.assertFalse(any(row.get("packet") for row in rows))
            if expected == 3:
                self.assertEqual((rows[-1]["stats_ok"], rows[-1]["ps_recv"]), (0, 0))

    def test_pinned_loader_absolute_secure_flags_no_real_load(self):
        digest = mock.Mock()
        digest.hexdigest.return_value = candidate.DLL_SHA256
        with mock.patch.object(candidate.os, "name", "nt"), \
                mock.patch("builtins.open", mock.mock_open(read_data=b"synthetic")), \
                mock.patch.object(candidate.hashlib, "file_digest", return_value=digest), \
                mock.patch.object(C, "CDLL") as loader:
            dll = candidate.load_packet()
            loader.assert_called_once_with(candidate.DLL_PATH, winmode=0x1100)
            self.assertIs(dll, loader.return_value)
            self.assertEqual(dll.PacketSetBpf.restype, C.c_uint8)
            self.assertEqual(dll.PacketReceivePacket.argtypes[-1], C.c_uint8)


if __name__ == "__main__":
    unittest.main()
