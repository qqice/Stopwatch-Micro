"""Pinned direct Packet.dll header-only capture; import is inert.

Runtime requires the reviewed Windows x64 Packet.dll hash. No fallback.
Primary ABI references (declarations, not copied implementation):
https://github.com/nmap/npcap/blob/master/Common/Packet32.h
https://github.com/nmap/npcap/blob/master/Common/npcap-bpf.h

ADAPTER is opaque: its deprecated internal members (and conditional AirPcap
tail) must never be dereferenced. PacketAllocatePacket owns PACKET allocation.
Windows LONG/UINT/DWORD are explicitly 32-bit, BOOLEAN is 8-bit. API is cdecl.
No direct HwFilter, mode, OID, monitor-mode, wpcap, install, or global PATH
operation. Native OpenAdapter may query/set lookahead OIDs; this is not
promiscuous mode or a global packet-filter change. Observations are NIC/kernel
visible metadata, not proof of over-air transmission or acknowledgement.
SetBpf success precedes allocation/receive. No pre-filter receive/drain is
permitted. Root verified the pinned ordinary-NPF implementation returns kernel
BIOCSETF status directly, with validation and owned-queue reset on success.
The offline harness retains a separate queue-contract assertion for tests.
"""
import argparse
import ctypes as C
import hashlib
import ipaddress
import json
import os
import struct
import time
from pathlib import Path

from mosaico_lan_tcp_headers import (
    BpfInsn, BpfProgram, Config, Timeval, UnsafeRecord,
    build_filter as _legacy_build_filter, parse_header as _legacy_parse_header,
    private_ipv4, bounded_int, AND_K, JGE_K, JEQ_K, LSH_K, ST,
)

DLL_PATH = r"C:\Windows\System32\Npcap\Packet.dll"
RUNTIME_ENABLED = True  # Root-approved pinned implementation; no bypass CLI.
DLL_SHA256 = "db4bf11294f7db23ebe809fd805888da65dd33ebe50915948542360033c79c9e"
LOCAL = "192.168.1.154"
INTERFACE_GUID = "{37A4C093-963F-4B07-A251-E5FF93D8BF0A}"
ADAPTER_NAME = ("\\Device\\NPF_" + INTERFACE_GUID).encode("ascii")
MAX_SNAP = 62  # L2 with <=2 VLAN tags + IPv4 without options + base TCP.
MAX_BATCH = 65536


def build_filter(local, peer, port):
    """Narrow the legacy program to IHL==5: no IP options copied by kernel.

    Preserve all branch offsets by replacing the sole IHL >=5 comparison
    with ==5. Structural drift fails closed rather than patching arbitrary BPF.
    """
    rows = _legacy_build_filter(local, peer, port)
    matches = [index for index, row in enumerate(rows)
               if row[0] == JGE_K and row[3] == 5
               and rows[index - 1] == (AND_K, 0, 0, 15)
               and rows[index + 1] == (LSH_K, 0, 0, 2)
               and rows[index + 2] == (ST, 0, 0, 1)]
    if len(matches) != 1:
        raise ValueError()
    index = matches[0]
    _, yes, no, value = rows[index]
    rows[index] = (JEQ_K, yes, no, value)
    return rows


def parse_header(reader, caplen, wire_len, local, peer, port):
    """Reject IPv4 options before any TCP parsing or option field reads."""
    if not 54 <= caplen <= MAX_SNAP:
        raise UnsafeRecord()
    prefix, ether = 14, reader(12, 2)
    for _ in range(2):
        if ether not in (0x8100, 0x88A8):
            break
        ether = reader(prefix + 2, 2)
        prefix += 4
    if ether != 0x0800 or reader(prefix, 1) != 0x45:
        raise UnsafeRecord()
    return _legacy_parse_header(reader, caplen, wire_len, local, peer, port)


class Adapter(C.Structure):
    """Opaque LPADAPTER; Packet API owns it, no guessed internal layout."""


class Overlapped(C.Structure):
    # Windows OVERLAPPED: union {DWORD Offset, OffsetHigh; PVOID Pointer}.
    # Both alternatives occupy one pointer-sized slot on supported Windows x64.
    _fields_ = [("Internal", C.c_size_t), ("InternalHigh", C.c_size_t),
                ("offset_union", C.c_void_p), ("hEvent", C.c_void_p)]


class Packet(C.Structure):
    _fields_ = [("hEvent", C.c_void_p), ("OverLapped", Overlapped),
                ("Buffer", C.c_void_p), ("Length", C.c_uint32),
                ("ulBytesReceived", C.c_uint32), ("bIoComplete", C.c_uint8)]


class BpfHeader(C.Structure):
    _fields_ = [("bh_tstamp", Timeval), ("bh_caplen", C.c_uint32),
                ("bh_datalen", C.c_uint32), ("bh_hdrlen", C.c_uint16)]


class BpfStats(C.Structure):
    _fields_ = [("recv", C.c_uint32), ("drop", C.c_uint32),
                ("ifdrop", C.c_uint32), ("capt", C.c_uint32)]


class NetType(C.Structure):
    _fields_ = [("LinkType", C.c_uint32), ("LinkSpeed", C.c_uint64)]


def bind_packet_api(dll):
    """Declare only approved API subset on an injected object; does not load it."""
    adapter, packet = C.POINTER(Adapter), C.POINTER(Packet)
    declarations = {
        "PacketOpenAdapter": ([C.c_char_p], adapter),
        "PacketCloseAdapter": ([adapter], None),
        "PacketSetBpf": ([adapter, C.POINTER(BpfProgram)], C.c_uint8),
        "PacketGetNetType": ([adapter, C.POINTER(NetType)], C.c_uint8),
        "PacketAllocatePacket": ([], packet),
        "PacketInitPacket": ([packet, C.c_void_p, C.c_uint32], None),
        "PacketFreePacket": ([packet], None),
        "PacketReceivePacket": ([adapter, packet, C.c_uint8], C.c_uint8),
        "PacketGetStatsEx": ([adapter, C.POINTER(BpfStats)], C.c_uint8),
        "PacketSetReadTimeout": ([adapter, C.c_int32], C.c_uint8),
        "PacketSetBuff": ([adapter, C.c_int32], C.c_uint8),
        "PacketSetMinToCopy": ([adapter, C.c_int32], C.c_uint8),
    }
    for name, (arguments, result) in declarations.items():
        function = getattr(dll, name)
        function.argtypes, function.restype = arguments, result
    return dll


def validate_config(config):
    if private_ipv4(config.local) != LOCAL or private_ipv4(config.peer) == LOCAL:
        raise ValueError()
    if type(config.port) is not int or not 1024 <= config.port <= 65535:
        raise ValueError()
    if type(config.ttl) is not int or not 1 <= config.ttl <= 120:
        raise ValueError()
    if not config.output.is_absolute():
        raise ValueError()


class NumericParser(argparse.ArgumentParser):
    def error(self, message):
        # Never emit untrusted input, descriptions, or exception strings.
        raise SystemExit(2)


def parse_args(argv=None):
    parser = NumericParser(add_help=False)
    parser.add_argument("--local", required=True, type=private_ipv4)
    parser.add_argument("--peer", required=True, type=private_ipv4)
    parser.add_argument("--port", required=True, type=bounded_int(1024, 65535))
    parser.add_argument("--ttl", required=True, type=bounded_int(1, 120))
    parser.add_argument("--output", required=True, type=Path)
    config = Config(**vars(parser.parse_args(argv)))
    try:
        validate_config(config)
    except (ValueError, argparse.ArgumentTypeError):
        parser.error(None)
    return config


def parse_batch(buffer, received, config):
    """Parse all records, returning numeric metadata only, or fail atomically.

    bh_hdrlen locates frame start; next record is WORDALIGN(hdrlen+caplen).
    Buffer's valid region includes each record's 4-byte alignment padding;
    missing/trailing bytes fail closed. Never read/return padding or IP options.
    """
    validate_config(config)
    if type(received) is not int or received < 0 or received > min(len(buffer), MAX_BATCH):
        raise UnsafeRecord()
    rows, offset = [], 0
    while offset < received:
        if received - offset < C.sizeof(BpfHeader):
            raise UnsafeRecord()
        sec, usec, caplen, datalen, hdrlen = struct.unpack_from("<iiIIH", buffer, offset)
        if hdrlen < C.sizeof(BpfHeader) or not 0 <= usec < 1000000:
            raise UnsafeRecord()
        end = offset + hdrlen + caplen
        next_offset = offset + ((hdrlen + caplen + 3) & ~3)
        if caplen > MAX_SNAP or end > received or next_offset > received:
            raise UnsafeRecord()
        start = offset + hdrlen
        def reader(position, size):
            return int.from_bytes(memoryview(buffer)[start + position:start + position + size], "big")
        metadata = parse_header(reader, caplen, datalen, config.local, config.peer, config.port)
        metadata.update(epoch_sec=sec, epoch_us=usec)
        rows.append(metadata)
        offset = next_offset
    return rows


def exercise_fake_session(api, config):
    """Offline test harness ONLY; never called by runtime/CLI.

    One fake receive tests ordering, ownership, stats and record framing. This
    is not a TTL capture loop and does not establish a production contract.
    """
    validate_config(config)
    adapter = packet = None
    rows, stats = [], BpfStats()
    outcome = 1
    try:
        adapter = api.PacketOpenAdapter(ADAPTER_NAME)
        if not adapter:
            return rows, outcome, stats
        net = NetType()
        if not api.PacketGetNetType(adapter, C.byref(net)) or net.LinkType != 0:
            return rows, outcome, stats  # Ethernet NdisMedium802_3 only.
        filter_rows = build_filter(config.local, config.peer, config.port)
        instructions = (BpfInsn * len(filter_rows))(*(BpfInsn(*row) for row in filter_rows))
        program = BpfProgram(len(instructions), instructions)
        if not api.PacketSetBpf(adapter, C.byref(program)):
            return rows, outcome, stats
        # Fake-only assertion of the separate queue-reset contract. A freshly
        # opened handle can have queued records before SetBpf. Until source
        # proves reset for the installed version, SetBpf success is insufficient.
        if getattr(api, "offline_queue_reset_verified", False) is not True:
            return rows, 7, stats
        packet = api.PacketAllocatePacket()
        if not packet:
            return rows, outcome, stats
        buffer = C.create_string_buffer(MAX_BATCH)
        api.PacketInitPacket(packet, buffer, len(buffer))
        if not api.PacketReceivePacket(adapter, packet, 1):
            return rows, outcome, stats
        received = packet.contents.ulBytesReceived
        rows = parse_batch(memoryview(buffer).cast("B"), received, config)
        if not api.PacketGetStatsEx(adapter, C.byref(stats)):
            return [], outcome, BpfStats()
        outcome = 0
        return rows, outcome, stats
    finally:
        if packet:
            api.PacketFreePacket(packet)
        if adapter:
            api.PacketCloseAdapter(adapter)


def load_packet():
    if not RUNTIME_ENABLED or os.name != "nt" or C.sizeof(C.c_void_p) != 8:
        raise RuntimeError()
    with open(DLL_PATH, "rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != DLL_SHA256:
        raise RuntimeError()
    # Absolute DLL, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | DEFAULT_DIRS. No PATH
    # edit, SetDllDirectory, generic name loading, or wpcap fallback.
    return bind_packet_api(C.CDLL(DLL_PATH, winmode=0x1100))


def emit(output, **values):
    extra = {"adapter_closed", "packet_freed", "bs_capt"}
    from mosaico_lan_tcp_headers import LOG_KEYS
    if not set(values) <= LOG_KEYS | extra or any(type(v) is not int for v in values.values()):
        raise ValueError()
    output.write(json.dumps(values, separators=(",", ":")) + "\n")
    output.flush()


def run_capture(config):
    validate_config(config)
    with config.output.open("x", encoding="ascii", newline="\n") as output:
        started = time.monotonic()
        deadline = started + config.ttl
        emit(output, start=1, local=int(ipaddress.IPv4Address(config.local)),
              peer=int(ipaddress.IPv4Address(config.peer)), port=config.port,
              ttl=config.ttl, host_pid=os.getpid(), elapsed_ms=0)
        api = adapter = packet = None
        outcome, installed, stats_ok = 1, 0, 0
        packets = rejected = payload_bytes = unknown = closed = freed = 0
        stats = BpfStats()
        try:
            api = load_packet()
            adapter = api.PacketOpenAdapter(ADAPTER_NAME)
            if not adapter:
                raise RuntimeError()
            net = NetType()
            if not api.PacketGetNetType(adapter, C.byref(net)) or net.LinkType != 0:
                raise RuntimeError()
            rows = build_filter(config.local, config.peer, config.port)
            instructions = (BpfInsn * len(rows))(*(BpfInsn(*row) for row in rows))
            program = BpfProgram(len(rows), instructions)
            # Reviewed pinned ordinary-NPF SetBpf directly returns BIOCSETF
            # status; successful kernel validation resets the owned queue.
            if not api.PacketSetBpf(adapter, C.byref(program)):
                raise RuntimeError()
            installed = 1
            if (not api.PacketSetBuff(adapter, MAX_BATCH)
                    or not api.PacketSetReadTimeout(adapter, 250)
                    or not api.PacketSetMinToCopy(adapter, 0)):
                raise RuntimeError()
            packet = api.PacketAllocatePacket()
            if not packet:
                raise RuntimeError()
            buffer = C.create_string_buffer(MAX_BATCH)
            api.PacketInitPacket(packet, buffer, len(buffer))
            emit(output, ready=1, filter_installed=1, snaplen=MAX_SNAP,
                 elapsed_ms=int((time.monotonic() - started) * 1000))
            while True:
                remaining = deadline - time.monotonic()
                if remaining < 0.001:
                    break
                timeout_ms = min(250, int(remaining * 1000))
                if not api.PacketSetReadTimeout(adapter, timeout_ms):
                    raise RuntimeError()
                packet.contents.ulBytesReceived = 0
                if not api.PacketReceivePacket(adapter, packet, 1):
                    raise RuntimeError()
                try:
                    metadata = parse_batch(memoryview(buffer).cast("B"),
                                           packet.contents.ulBytesReceived, config)
                except UnsafeRecord:
                    rejected += 1
                    outcome = 2
                    break  # Invalid batch is discarded atomically; stop capture.
                for row in metadata:
                    emit(output, packet=1, **row)
                    packets += 1
                    if row["length_unknown"]:
                        unknown += 1
                    else:
                        payload_bytes += row["payload_len"]
            if outcome != 2:
                outcome = 0
        except Exception:
            outcome = 1
        finally:
            if adapter and api:
                try:
                    stats_ok = int(bool(api.PacketGetStatsEx(adapter, C.byref(stats))))
                except Exception:
                    stats_ok = 0
                if not stats_ok:
                    stats = BpfStats()
                    outcome = outcome or 3
            if packet and api:
                try:
                    api.PacketFreePacket(packet)
                    freed = 1
                except Exception:
                    outcome = outcome or 4
            if adapter and api:
                try:
                    api.PacketCloseAdapter(adapter)
                    closed = 1
                except Exception:
                    outcome = outcome or 4
            emit(output, summary=1, runtime_disabled=0, outcome=outcome, errno=0,
                 packets=packets, payload_bytes=payload_bytes, unknown_length_packets=unknown,
                 rejected=rejected, stats_ok=stats_ok, ps_recv=stats.recv,
                 ps_drop=stats.drop, ps_ifdrop=stats.ifdrop, bs_capt=stats.capt,
                 drops_present=int(bool(stats.drop or stats.ifdrop)),
                 filter_installed=installed, adapter_closed=closed, packet_freed=freed,
                 elapsed_ms=int((time.monotonic() - started) * 1000))
    return outcome


def main(argv=None):
    try:
        return run_capture(parse_args(argv))
    except (OSError, ValueError, argparse.ArgumentTypeError):
        return 2  # No stderr, exception strings or raw packet data.
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
