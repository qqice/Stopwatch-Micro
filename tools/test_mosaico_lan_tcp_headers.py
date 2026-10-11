import contextlib
import ctypes as C
import importlib
import io
import ipaddress
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import mosaico_lan_tcp_headers as capture

LOCAL = "192.168.1.154"
PEER = "192.168.1.216"
PORT = 39871


def packet(direction=1, payload=512, tags=(), ihl=20, tcp_len=20,
           fragment=0, version=4, protocol=6, total=None, wrong_peer=False,
           wrong_port=False, ether_type=0x0800):
    prefix = bytearray(b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c")
    for tag in tags:
        prefix += struct.pack("!HH", tag, 7)
    prefix += struct.pack("!H", ether_type)
    ip = bytearray(ihl)
    ip[0] = (version << 4) | (ihl // 4)
    struct.pack_into("!H", ip, 2, ihl + tcp_len + payload if total is None else total)
    struct.pack_into("!H", ip, 6, fragment)
    ip[9] = protocol
    local = int(ipaddress.IPv4Address(LOCAL))
    peer = int(ipaddress.IPv4Address("192.168.1.217" if wrong_peer else PEER))
    struct.pack_into("!II", ip, 12, *((local, peer) if direction == 1 else (peer, local)))
    tcp = bytearray(tcp_len)
    server_port = PORT + int(wrong_port)
    struct.pack_into("!HHII", tcp, 0, *((server_port, 50000) if direction == 1 else (50000, server_port)),
                     0x12345678, 0x87654321)
    tcp[12] = (tcp_len // 4) << 4
    tcp[13] = 0x18 if payload else 0x10
    struct.pack_into("!H", tcp, 14, 4096)
    return bytes(prefix + ip + tcp + b"X" * payload), len(prefix), len(prefix) + ihl + 20


class CanaryReader:
    def __init__(self, frame, end, ip_options=None):
        self.frame = frame
        self.end = end
        self.ip_options = ip_options
        self.reads = []

    def __call__(self, offset, size):
        self.reads.append((offset, size))
        if offset + size > len(self.frame):
            raise IndexError()
        if offset + size > self.end:
            raise AssertionError("attempt to read TCP options or application payload")
        if self.ip_options and offset < self.ip_options[1] and offset + size > self.ip_options[0]:
            raise AssertionError("attempt to read IP options")
        return int.from_bytes(self.frame[offset:offset + size], "big")


def interpret(rows, reader, length):
    """Independent minimal cBPF machine with callback loads and OOB rejection."""
    accumulator = index = pc = steps = 0
    memory = [0] * 16
    while pc < len(rows):
        code, jt, jf, k = rows[pc]
        steps += 1
        if steps > 500:
            raise AssertionError("unbounded BPF program")
        pc += 1
        try:
            if code == capture.LD_IMM:
                accumulator = k
            elif code == capture.LD_MEM:
                accumulator = memory[k]
            elif code == capture.LDX_MEM:
                index = memory[k]
            elif code == capture.ST:
                memory[k] = accumulator
            elif code == capture.LD_H_ABS:
                accumulator = reader(k, 2)
            elif code in (capture.LD_B_IND, capture.LD_H_IND, capture.LD_W_IND):
                size = {capture.LD_B_IND: 1, capture.LD_H_IND: 2, capture.LD_W_IND: 4}[code]
                accumulator = reader(index + k, size)
            elif code == capture.LD_LEN:
                accumulator = length
            elif code == capture.ADD_K:
                accumulator = (accumulator + k) & 0xffffffff
            elif code == capture.ADD_X:
                accumulator = (accumulator + index) & 0xffffffff
            elif code == capture.AND_K:
                accumulator &= k
            elif code == capture.LSH_K:
                accumulator = (accumulator << k) & 0xffffffff
            elif code == capture.RSH_K:
                accumulator >>= k
            elif code == capture.TAX:
                index = accumulator
            elif code == capture.TXA:
                accumulator = index
            elif code == capture.JA:
                pc += k
            elif code == capture.JEQ_K:
                pc += jt if accumulator == k else jf
            elif code == capture.JGE_K:
                pc += jt if accumulator >= k else jf
            elif code == capture.JGE_X:
                pc += jt if accumulator >= index else jf
            elif code == capture.RET_A:
                return accumulator
            elif code == capture.RET_K:
                return k
            else:
                raise AssertionError(f"unsupported instruction {code}")
        except IndexError:
            return 0
    raise AssertionError("BPF did not return")


class HeaderTests(unittest.TestCase):
    def setUp(self):
        self.rows = capture.build_filter(LOCAL, PEER, PORT)

    def test_windows_abi_sizes(self):
        self.assertEqual(C.sizeof(capture.Timeval), 8)
        self.assertEqual(C.sizeof(capture.PcapHeader), 16)
        self.assertEqual(capture.PcapHeader.caplen.offset, 8)
        self.assertEqual(capture.PcapHeader.length.offset, 12)
        self.assertEqual(C.sizeof(capture.BpfInsn), 8)
        self.assertEqual(C.sizeof(capture.Sockaddr), 16)
        if C.sizeof(C.c_void_p) == 8:
            self.assertEqual(C.sizeof(capture.PcapIf), 40)
            self.assertEqual(C.sizeof(capture.PcapAddr), 40)
            self.assertEqual(C.sizeof(capture.BpfProgram), 16)
            self.assertEqual(capture.BpfProgram.instructions.offset, 8)

    def test_both_directions_ack_data_vlan_and_options_no_reads(self):
        for direction in (1, 2):
            for payload in (0, 512):
                for tags in ((), (0x8100,), (0x88a8, 0x8100)):
                    for ihl, tcp_len in ((20, 20), (60, 60)):
                        frame, prefix, end = packet(direction, payload, tags, ihl, tcp_len)
                        reader = CanaryReader(frame, end, (prefix + 20, prefix + ihl) if ihl > 20 else None)
                        self.assertEqual(interpret(self.rows, reader, len(frame)), end)
                        metadata = capture.parse_header(reader, end, len(frame), LOCAL, PEER, PORT)
                        self.assertEqual(metadata["direction"], direction)
                        self.assertEqual(metadata["payload_len"], payload)
                        self.assertEqual(metadata["header_len"], ihl + tcp_len)
                        self.assertEqual(metadata["seq"], 0x12345678)
                        self.assertEqual(metadata["ack"], 0x87654321)
                        self.assertEqual(metadata["length_unknown"], 0)
                        self.assertLessEqual(end, 102)

    def test_offload_zero_total_length_unknown(self):
        for direction in (1, 2):
            frame, prefix, end = packet(direction, payload=32000, total=0)
            reader = CanaryReader(frame, end)
            self.assertEqual(interpret(self.rows, reader, len(frame)), end)
            metadata = capture.parse_header(reader, end, len(frame), LOCAL, PEER, PORT)
            self.assertEqual(metadata["payload_len"], -1)
            self.assertEqual(metadata["length_unknown"], 1)
            self.assertEqual(metadata["original_frame_len"], len(frame))

    def test_wrong_targets_fragments_invalid_ipv4_vlan3_drop(self):
        variants = [dict(wrong_peer=True), dict(wrong_port=True), dict(fragment=0x2000),
                    dict(fragment=1), dict(version=6), dict(protocol=17),
                    dict(total=39), dict(ether_type=0x86dd),
                    dict(tags=(0x8100, 0x8100, 0x8100))]
        for variant in variants:
            frame, prefix, end = packet(**variant)
            reader = CanaryReader(frame, end)
            with self.subTest(variant=variant):
                self.assertEqual(interpret(self.rows, reader, len(frame)), 0)
                with self.assertRaises(capture.UnsafeRecord):
                    capture.parse_header(reader, end, len(frame), LOCAL, PEER, PORT)
        for offset, value in ((14, 0x44), (14 + 20 + 12, 0x40)):
            frame, prefix, end = packet()
            altered = bytearray(frame)
            altered[offset] = value
            reader = CanaryReader(altered, end)
            self.assertEqual(interpret(self.rows, reader, len(altered)), 0)
            with self.assertRaises(capture.UnsafeRecord):
                capture.parse_header(reader, end, len(altered), LOCAL, PEER, PORT)

    def test_truncated_and_oversnap_rejected_without_body_read(self):
        frame, prefix, end = packet()
        for size in (0, 12, 18, 33, end - 1, len(frame) - 1):
            reader = CanaryReader(frame[:size], end)
            self.assertEqual(interpret(self.rows, reader, size), 0)
        for caplen in (end - 1, end + 1, 102, len(frame)):
            reader = CanaryReader(frame, end)
            with self.assertRaises(capture.UnsafeRecord):
                capture.parse_header(reader, caplen, len(frame), LOCAL, PEER, PORT)
            self.assertTrue(all(offset + size <= end for offset, size in reader.reads))

    def test_symbolic_jumps_forward_and_bounded(self):
        self.assertLess(len(self.rows), 256)
        for position, (code, jt, jf, k) in enumerate(self.rows):
            if code in (capture.JEQ_K, capture.JGE_K, capture.JGE_X):
                self.assertLess(position + 1 + jt, len(self.rows))
                self.assertLess(position + 1 + jf, len(self.rows))
            if code == capture.JA:
                self.assertGreaterEqual(k, 0)
                self.assertLess(position + 1 + k, len(self.rows))

    def argv(self, path):
        return ["--local", LOCAL, "--peer", PEER, "--port", str(PORT),
                "--ttl", "10", "--output", str(path)]

    def test_parser_and_log_whitelist(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.argv(Path(directory) / "headers.jsonl")
            self.assertEqual(capture.parse_args(args).port, PORT)
            for option, value in (("--local", "0.0.0.0"), ("--local", "localhost"),
                                  ("--peer", "8.8.8.8"), ("--peer", LOCAL),
                                  ("--port", "1023"), ("--port", "65536"),
                                  ("--ttl", "0"), ("--ttl", "121"), ("--output", "relative")):
                altered = args.copy()
                altered[altered.index(option) + 1] = value
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    capture.parse_args(altered)
        for values in ({"payload": 1}, {"errno": "error text"}, {"ready": True}):
            with self.assertRaises(ValueError):
                capture.emit(io.StringIO(), **values)

    def test_runtime_hard_guard_before_any_dll_load_open_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "disabled.jsonl"
            config = capture.Config(LOCAL, PEER, PORT, 120, path)
            with mock.patch.object(C, "CDLL") as dll_loader, \
                    mock.patch.object(capture, "load_npcap") as loader, \
                    mock.patch.object(capture, "find_device") as enumeration:
                self.assertEqual(capture.run_capture(config), 6)
                dll_loader.assert_not_called()
                loader.assert_not_called()
                enumeration.assert_not_called()
            records = [json.loads(line) for line in path.read_text().splitlines()]
            summary = records[-1]
            self.assertEqual(summary["runtime_disabled"], 1)
            self.assertEqual(summary["packets"], 0)
            self.assertEqual(summary["pcap_closed"], 0)
            self.assertEqual(summary["stats_ok"], 0)
            self.assertFalse(any(row.get("ready") for row in records))
            self.assertTrue(all(type(v) in (int, float) for row in records for v in row.values()))
            for option in ("--force", "--override", "--unsafe"):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    capture.parse_args(self.argv(path) + [option])
    def test_exact_device_selection_and_cycle_limit_free(self):
        sockaddr = capture.Sockaddr()
        sockaddr.family = 2
        for index, octet in enumerate(ipaddress.IPv4Address(LOCAL).packed):
            sockaddr.data[index + 2] = octet
        address = capture.PcapAddr()
        address.addr = C.pointer(sockaddr)
        device = capture.PcapIf()
        device.name = b"exact-private-device"
        device.addresses = C.pointer(address)
        dll = mock.Mock()

        def enumerate_devices(pointer, error):
            C.cast(pointer, C.POINTER(C.POINTER(capture.PcapIf)))[0] = C.pointer(device)
            return 0

        dll.pcap_findalldevs.side_effect = enumerate_devices
        self.assertEqual(capture.find_device(dll, LOCAL), b"exact-private-device")
        dll.pcap_freealldevs.assert_called_once()
        with self.assertRaises(OSError):
            capture.find_device(dll, PEER)
        device.next = C.pointer(device)
        with self.assertRaises(OSError):
            capture.find_device(dll, LOCAL)
        self.assertEqual(dll.pcap_freealldevs.call_count, 3)
    def test_exclusive_output_import_inert(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "headers.jsonl"
            path.write_text("preserved")
            with mock.patch.object(capture, "load_npcap") as loader:
                with self.assertRaises(FileExistsError):
                    capture.run_capture(capture.Config(LOCAL, PEER, PORT, 1, path))
                loader.assert_not_called()
            self.assertEqual(path.read_text(), "preserved")
        with mock.patch.object(C, "CDLL") as loader:
            importlib.reload(capture)
            loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
