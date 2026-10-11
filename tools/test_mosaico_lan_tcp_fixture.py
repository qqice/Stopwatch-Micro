import contextlib
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import mosaico_lan_tcp_fixture as fixture


class SimulatedSocket:
    def __init__(self, amounts, incoming=b"", fail_at=None):
        self.amounts = iter(amounts)
        self.incoming = incoming
        self.fail_at = fail_at
        self.data = bytearray()
        self.shutdowns = []

    def send(self, data):
        if self.fail_at is not None and len(self.data) >= self.fail_at:
            raise OSError(10054, "not for logs")
        amount = next(self.amounts, len(data))
        if amount is None:
            raise BlockingIOError()
        amount = min(amount, len(data))
        self.data.extend(data[:amount])
        return amount

    def recv(self, amount, flags):
        assert amount == 1 and flags == socket.MSG_PEEK
        return self.incoming

    def shutdown(self, how):
        self.shutdowns.append(how)


class SimulatedIO:
    def __init__(self, sock, blocked=False, eof=True):
        self.now = 0.0
        self.sock = sock
        self.blocked = blocked
        self.eof = eof

    def clock(self):
        return self.now

    def wait(self, reads, writes, errors, timeout):
        if writes and not self.blocked:
            return [], writes, []
        if self.sock.shutdowns and self.eof:
            return reads, [], []
        self.now += timeout
        return [], [], []


class FixtureTests(unittest.TestCase):
    def argv(self, output):
        return ["--bind", "192.168.1.154", "--peer", "192.168.1.216",
                "--port", "45123", "--seconds", "10", "--ttl", "30",
                "--output", str(output)]

    def test_parser_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.argv(Path(directory) / "run.jsonl")
            self.assertEqual(fixture.parse_args(args).ttl, 30)
            for option, values in {
                "--bind": ["0.0.0.0", "127.0.0.1", "8.8.8.8", "localhost", "::1", "192.168.001.1", "192.168.1.255", "192.168.1.0"],
                "--peer": ["224.0.0.1", "169.254.1.1", "100.64.0.1"],
                "--port": ["0", "1023", "65536"],
                "--seconds": ["9", "61"],
                "--ttl": ["0", "14", "121"],
                "--output": ["relative.jsonl"],
            }.items():
                for value in values:
                    altered = args.copy()
                    altered[altered.index(option) + 1] = value
                    with self.subTest(option=option, value=value), contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit):
                            fixture.parse_args(altered)

    def test_pattern_split_and_partial_send(self):
        sock = SimulatedSocket([1, 13, None, 37, 100, 1])
        simulated = SimulatedIO(sock)
        records = []
        result = fixture.transmit(sock, 1, 9, lambda **kw: records.append(kw),
                                  simulated.clock, simulated.wait)
        self.assertEqual(result[0:2], (0, 1024))
        self.assertGreater(result[2], 2)
        self.assertEqual(sock.data, fixture.pattern(0, 1024))
        self.assertEqual(fixture.pattern(0, 123) + fixture.pattern(123, 901), sock.data)
        self.assertEqual(sock.shutdowns, [socket.SHUT_WR])
        self.assertEqual(simulated.now, 1)
        self.assertEqual(records[-1]["sent_bytes"], 1024)

    def test_backpressure_total_deadline(self):
        sock = SimulatedSocket([])
        simulated = SimulatedIO(sock, blocked=True)
        self.assertEqual(fixture.transmit(sock, 1, 0.2, lambda **kw: None,
                                         simulated.clock, simulated.wait), (2, 0, 0))
        self.assertEqual(simulated.now, 0.2)
        self.assertEqual(sock.shutdowns, [])

    def test_drain_timeout_and_total_deadline(self):
        for deadline, expected, final in [(20, 5, 6), (3, 2, 3)]:
            sock = SimulatedSocket([])
            simulated = SimulatedIO(sock, eof=False)
            result = fixture.transmit(sock, 1, deadline, lambda **kw: None,
                                      simulated.clock, simulated.wait)
            self.assertEqual(result[0], expected)
            self.assertEqual(simulated.now, final)
            self.assertEqual(sock.shutdowns, [socket.SHUT_WR])

    def test_unexpected_data_no_body_consumption(self):
        sock = SimulatedSocket([], incoming=b"X")
        simulated = SimulatedIO(sock)
        result = fixture.transmit(sock, 1, 9, lambda **kw: None,
                                  simulated.clock, simulated.wait)
        self.assertEqual(result[0], 4)
        self.assertEqual(sock.incoming, b"X")

    def test_output_exclusive_before_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exists.jsonl"
            path.write_text("preserved")
            config = fixture.Config("127.0.0.1", "127.0.0.1", 0, 1, path, 1)
            with mock.patch.object(fixture.socket, "socket") as constructor:
                with self.assertRaises(FileExistsError):
                    fixture.run_fixture(config)
                constructor.assert_not_called()
            self.assertEqual(path.read_text(), "preserved")

    def test_loopback_wrong_peer_close_and_valid_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.jsonl"
            ready = threading.Event()
            port = []
            original_emit = fixture.emit

            def capture(output, **values):
                original_emit(output, **values)
                if values.get("ready"):
                    port.append(values["port"])
                    ready.set()

            config = fixture.Config("127.0.0.1", "127.0.0.1", 0, 1, path, 4)
            outcomes = []
            with mock.patch.object(fixture, "emit", side_effect=capture):
                thread = threading.Thread(target=lambda: outcomes.append(fixture.run_fixture(config)))
                thread.start()
                self.assertTrue(ready.wait(2))
                with socket.socket() as wrong:
                    wrong.settimeout(2)
                    wrong.bind(("127.0.0.2", 0))
                    wrong.connect(("127.0.0.1", port[0]))
                    self.assertEqual(wrong.recv(1), b"")
                with socket.create_connection(("127.0.0.1", port[0]), timeout=2) as client:
                    body = bytearray()
                    while True:
                        part = client.recv(73)
                        if not part:
                            break
                        body.extend(part)
                    client.shutdown(socket.SHUT_WR)
                thread.join(3)
                self.assertFalse(thread.is_alive())
            self.assertEqual(outcomes, [0])
            self.assertEqual(body, fixture.pattern(0, 1024))
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertTrue(all(type(v) in (int, float) for row in records for v in row.values()))
            self.assertEqual(records[-1]["rejected"], 1)
            self.assertEqual(records[-1]["accepted"], 1)
            self.assertEqual(records[-1]["closed"], 1)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port[0]))

    def test_process_deadline_without_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timeout.jsonl"
            code = ("from pathlib import Path; import mosaico_lan_tcp_fixture as f; "
                    f"raise SystemExit(f.run_fixture(f.Config('127.0.0.1','127.0.0.1',0,1,Path({str(path)!r}),1)))")
            started = time.monotonic()
            result = subprocess.run([sys.executable, "-c", code], cwd=Path(fixture.__file__).parent,
                                    capture_output=True, timeout=4)
            self.assertEqual(result.returncode, 2)
            self.assertLess(time.monotonic() - started, 4)
            summary = json.loads(path.read_text().splitlines()[-1])
            self.assertEqual(summary["outcome"], 2)
            self.assertEqual(summary["closed"], 1)

    def test_os_error_preserves_partial_counts_and_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "error.jsonl"
            listener = mock.Mock()
            candidate = mock.Mock()
            listener.getsockname.return_value = ("127.0.0.1", 45123)
            listener.accept.return_value = (candidate, ("127.0.0.1", 50000))
            candidate.getsockopt.return_value = 0

            def failed_transmit(connection, seconds, deadline, record):
                record(send=1, sent_bytes=7, send_calls=1)
                raise OSError(10054, "never log this body")

            config = fixture.Config("127.0.0.1", "127.0.0.1", 0, 1, path, 4)
            with mock.patch.object(fixture.socket, "socket", return_value=listener), \
                    mock.patch.object(fixture.select, "select", return_value=([listener], [], [])), \
                    mock.patch.object(fixture, "transmit", side_effect=failed_transmit):
                self.assertEqual(fixture.run_fixture(config), 6)
            candidate.close.assert_called_once()
            listener.close.assert_called_once()
            summary = json.loads(path.read_text().splitlines()[-1])
            self.assertEqual(summary["sent_bytes"], 7)
            self.assertEqual(summary["send_calls"], 1)
            self.assertEqual(summary["errno"], 10054)
            self.assertNotIn("never log", path.read_text())
    def test_import_inert(self):
        import importlib
        with mock.patch.object(socket, "socket") as constructor:
            importlib.reload(fixture)
            constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
