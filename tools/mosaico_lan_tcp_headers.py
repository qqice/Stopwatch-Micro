"""Target-only Npcap TCP base-header metadata; import is inert.

No packet bytes, IP/TCP options, descriptions, or error text are persisted.
Per-handle classic BPF returns exactly L2 + IPv4 IHL + 20 bytes, not a
full-packet expression filter. Numeric direction: 1 local TX, 2 local RX.
Runtime is disabled (outcome 6) pending proven kernel-only installation. Header capture observes NIC wire traffic,
not application send queues. Npcap RET truncation must work as documented;
this module never falls back to capturing payload and stripping it.
"""
import argparse
import ctypes as C
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path

DLL_PATH = r"C:\Windows\System32\Npcap\wpcap.dll"
MAX_SNAP = 102  # Two VLAN tags + maximum IPv4 header + fixed TCP header.
PRIVATE = tuple(ipaddress.IPv4Network(n) for n in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))

# Windows ABI types must not use host-dependent c_long (LP64 elsewhere).
class Timeval(C.Structure):
    _fields_ = [("tv_sec", C.c_int32), ("tv_usec", C.c_int32)]


class PcapHeader(C.Structure):
    _fields_ = [("ts", Timeval), ("caplen", C.c_uint32), ("length", C.c_uint32)]


class Sockaddr(C.Structure):
    _fields_ = [("family", C.c_uint16), ("data", C.c_ubyte * 14)]


class PcapAddr(C.Structure):
    pass


PcapAddr._fields_ = [("next", C.POINTER(PcapAddr)), ("addr", C.POINTER(Sockaddr)),
                    ("netmask", C.POINTER(Sockaddr)), ("broadaddr", C.POINTER(Sockaddr)),
                    ("dstaddr", C.POINTER(Sockaddr))]


class PcapIf(C.Structure):
    pass


PcapIf._fields_ = [("next", C.POINTER(PcapIf)), ("name", C.c_char_p),
                  ("description", C.c_char_p), ("addresses", C.POINTER(PcapAddr)),
                  ("flags", C.c_uint32)]


class BpfInsn(C.Structure):
    _fields_ = [("code", C.c_uint16), ("jt", C.c_uint8), ("jf", C.c_uint8),
               ("k", C.c_uint32)]


class BpfProgram(C.Structure):
    _fields_ = [("length", C.c_uint32), ("instructions", C.POINTER(BpfInsn))]


class PcapStats(C.Structure):
    # First three fields are portable. Reserve space for Npcap-specific tail
    # fields; do not interpret or log ABI-dependent tail data.
    _fields_ = [("recv", C.c_uint32), ("drop", C.c_uint32),
               ("ifdrop", C.c_uint32), ("reserved", C.c_uint32 * 13)]


# Standard classic-BPF codes used by this deliberately small assembler.
LD_IMM, LD_MEM, LDX_MEM, ST = 0x00, 0x60, 0x61, 0x02
LD_B_IND, LD_H_IND, LD_W_IND, LD_H_ABS, LD_LEN = 0x50, 0x48, 0x40, 0x28, 0x80
ADD_K, ADD_X, AND_K, LSH_K, RSH_K = 0x04, 0x0C, 0x54, 0x64, 0x74
TAX, TXA, JEQ_K, JGE_K, JGE_X, JA, RET_A, RET_K = 0x07, 0x87, 0x15, 0x35, 0x3D, 0x05, 0x16, 0x06


class Assembler:
    def __init__(self):
        self.rows = []
        self.labels = {}

    def label(self, name):
        if name in self.labels:
            raise ValueError("duplicate label")
        self.labels[name] = len(self.rows)

    def emit(self, code, k=0, yes=None, no=None):
        self.rows.append((code, k, yes, no))

    def finish(self):
        result = []
        for position, (code, k, yes, no) in enumerate(self.rows):
            if code == JA:
                k = self.labels[k] - position - 1
                if k < 0:
                    raise ValueError("backward jump")
                result.append((code, 0, 0, k))
            elif code in (JEQ_K, JGE_K, JGE_X):
                jt = self.labels[yes] - position - 1
                jf = self.labels[no] - position - 1
                if not (0 <= jt <= 255 and 0 <= jf <= 255):
                    raise ValueError("conditional jump out of bounds")
                result.append((code, jt, jf, k))
            else:
                result.append((code, 0, 0, k))
        return result


def build_filter(local, peer, port):
    """BPF scratch: M0 L2 length; M1 IP IHL; M2 IP total; M3 direction;
    M4 TCP header length; M5 fixed TCP header end. No payload/options loads.
    """
    a = Assembler()
    e, label = a.emit, a.label
    e(LD_H_ABS, 12)
    e(JEQ_K, 0x0800, "base14", "tag0")
    label("tag0"); e(JEQ_K, 0x8100, "vlan1", "tag0ad")
    label("tag0ad"); e(JEQ_K, 0x88A8, "vlan1", "reject")
    label("vlan1"); e(LD_H_ABS, 16)
    e(JEQ_K, 0x0800, "base18", "tag1")
    label("tag1"); e(JEQ_K, 0x8100, "vlan2", "tag1ad")
    label("tag1ad"); e(JEQ_K, 0x88A8, "vlan2", "reject")
    label("vlan2"); e(LD_H_ABS, 20)
    e(JEQ_K, 0x0800, "base22", "reject")
    for name, size in (("base14", 14), ("base18", 18), ("base22", 22)):
        label(name); e(LD_IMM, size); e(ST, 0); e(JA, "ip")
    label("ip"); e(LDX_MEM, 0); e(LD_B_IND, 0); e(AND_K, 0xF0)
    e(JEQ_K, 0x40, "ihl", "reject")
    label("ihl"); e(LD_B_IND, 0); e(AND_K, 15)
    e(JGE_K, 5, "ihl_ok", "reject")
    label("ihl_ok"); e(LSH_K, 2); e(ST, 1)
    e(LD_H_IND, 2); e(ST, 2)
    e(LD_H_IND, 6); e(AND_K, 0x3FFF)
    e(JEQ_K, 0, "proto", "reject")
    label("proto"); e(LD_B_IND, 9)
    e(JEQ_K, 6, "src", "reject")
    label("src"); e(LD_W_IND, 12)
    e(JEQ_K, int(ipaddress.IPv4Address(local)), "from_local", "src_peer")
    label("src_peer"); e(JEQ_K, int(ipaddress.IPv4Address(peer)), "from_peer", "reject")
    label("from_local"); e(LD_W_IND, 16)
    e(JEQ_K, int(ipaddress.IPv4Address(peer)), "outbound", "reject")
    label("outbound"); e(LD_IMM, 1); e(ST, 3); e(JA, "tcp")
    label("from_peer"); e(LD_W_IND, 16)
    e(JEQ_K, int(ipaddress.IPv4Address(local)), "inbound", "reject")
    label("inbound"); e(LD_IMM, 2); e(ST, 3)
    label("tcp"); e(LD_MEM, 0); e(LDX_MEM, 1); e(ADD_X); e(TAX)
    e(LD_B_IND, 12); e(AND_K, 0xF0); e(RSH_K, 2)
    e(JGE_K, 20, "tcp_len", "reject")
    label("tcp_len"); e(ST, 4); e(LDX_MEM, 1); e(ADD_X); e(TAX)
    e(LD_MEM, 2); e(JEQ_K, 0, "length_unknown", "length_known")
    label("length_known"); e(JGE_X, 0, "total_ok", "reject")
    label("total_ok"); e(LD_MEM, 0); e(LDX_MEM, 2); e(ADD_X); e(TAX); e(JA, "frame_length")
    label("length_unknown"); e(LD_MEM, 0); e(ADD_X); e(TAX)
    label("frame_length"); e(LD_LEN); e(JGE_X, 0, "frame_ok", "reject")
    label("frame_ok"); e(LD_MEM, 0); e(LDX_MEM, 1); e(ADD_X); e(TAX)
    e(LD_B_IND, 19)  # Prove all 20 base TCP bytes are available, not options.
    e(LD_MEM, 3); e(JEQ_K, 1, "server_src", "server_dst")
    label("server_src"); e(LD_H_IND, 0)
    e(JEQ_K, port, "accept", "reject")
    label("server_dst"); e(LD_H_IND, 2)
    e(JEQ_K, port, "accept", "reject")
    label("accept"); e(TXA); e(ADD_K, 20); e(RET_A)
    label("reject"); e(RET_K, 0)
    return a.finish()


class UnsafeRecord(ValueError):
    pass


def parse_header(reader, caplen, wire_len, local, peer, port):
    """reader(offset, size) returns a network-order integer. Read fixed fields
    only; reject oversnap *before* reading a TCP field or any packet body.
    """
    def read(offset, size):
        if offset < 0 or offset + size > caplen:
            raise UnsafeRecord()
        return reader(offset, size)

    if not 54 <= caplen <= MAX_SNAP or wire_len < caplen:
        raise UnsafeRecord()
    ether = read(12, 2)
    prefix = 14
    for _ in range(2):
        if ether not in (0x8100, 0x88A8):
            break
        ether = read(prefix + 2, 2)
        prefix += 4
    if ether != 0x0800:
        raise UnsafeRecord()
    vihl = read(prefix, 1)
    ihl = (vihl & 15) * 4
    if vihl >> 4 != 4 or ihl < 20:
        raise UnsafeRecord()
    tcp = prefix + ihl
    end = tcp + 20
    if caplen != end:
        raise UnsafeRecord()
    total = read(prefix + 2, 2)
    if read(prefix + 6, 2) & 0x3FFF or read(prefix + 9, 1) != 6:
        raise UnsafeRecord()
    src, dst = read(prefix + 12, 4), read(prefix + 16, 4)
    local_ip, peer_ip = int(ipaddress.IPv4Address(local)), int(ipaddress.IPv4Address(peer))
    if (src, dst) == (local_ip, peer_ip):
        direction = 1
    elif (src, dst) == (peer_ip, local_ip):
        direction = 2
    else:
        raise UnsafeRecord()
    sport, dport = read(tcp, 2), read(tcp + 2, 2)
    if (sport if direction == 1 else dport) != port:
        raise UnsafeRecord()
    tcp_length = (read(tcp + 12, 1) >> 4) * 4
    header_length = ihl + tcp_length
    if (tcp_length < 20 or (total != 0 and total < header_length)
            or wire_len < prefix + (total if total else header_length)):
        raise UnsafeRecord()
    return dict(src=src, dst=dst, sport=sport, dport=dport, seq=read(tcp + 4, 4),
                ack=read(tcp + 8, 4), flags=((read(tcp + 12, 1) & 1) << 8) | read(tcp + 13, 1),
                win=read(tcp + 14, 2), ip_len=total, header_len=header_length,
                payload_len=total - header_length if total else -1, direction=direction,
                length_unknown=int(total == 0), original_frame_len=wire_len)


def private_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        raise argparse.ArgumentTypeError("requires literal RFC1918 IPv4") from None
    if not any(address in net for net in PRIVATE) or (int(address) & 255) in (0, 255):
        raise argparse.ArgumentTypeError("requires unicast RFC1918 IPv4")
    return str(address)


def bounded_int(low, high):
    def parse(value):
        try:
            result = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("requires integer") from None
        if not low <= result <= high:
            raise argparse.ArgumentTypeError("integer out of range")
        return result
    return parse


@dataclass(frozen=True)
class Config:
    local: str
    peer: str
    port: int
    ttl: int
    output: Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", required=True, type=private_ipv4)
    parser.add_argument("--peer", required=True, type=private_ipv4)
    parser.add_argument("--port", required=True, type=bounded_int(1024, 65535))
    parser.add_argument("--ttl", required=True, type=bounded_int(1, 120))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.local == args.peer:
        parser.error("local and peer must differ")
    if not args.output.is_absolute():
        parser.error("output must be an absolute private path")
    return Config(**vars(args))


LOG_KEYS = frozenset("start ready summary packet elapsed_ms host_pid local peer port ttl epoch_sec epoch_us src dst sport dport seq ack flags win ip_len header_len payload_len direction packets payload_bytes rejected stats_ok ps_recv ps_drop ps_ifdrop drops_present outcome errno pcap_closed dlt snaplen promisc filter_installed length_unknown original_frame_len unknown_length_packets runtime_disabled".split())


def emit(output, **values):
    if not set(values) <= LOG_KEYS or any(type(value) not in (int, float) for value in values.values()):
        raise ValueError("non-whitelisted metadata")
    output.write(json.dumps(values, separators=(",", ":")) + "\n")
    output.flush()


def load_npcap():
    if os.name != "nt" or C.sizeof(C.c_void_p) != 8:
        raise OSError(0, "requires Windows x64")
    # cdecl library; no service, driver, interface, registry or firewall changes.
    dll = C.CDLL(DLL_PATH)
    declarations = {
        "pcap_findalldevs": ([C.POINTER(C.POINTER(PcapIf)), C.c_char_p], C.c_int),
        "pcap_freealldevs": ([C.POINTER(PcapIf)], None),
        "pcap_open_live": ([C.c_char_p, C.c_int, C.c_int, C.c_int, C.c_char_p], C.c_void_p),
        "pcap_datalink": ([C.c_void_p], C.c_int),
        "pcap_setfilter": ([C.c_void_p, C.POINTER(BpfProgram)], C.c_int),
        "pcap_next_ex": ([C.c_void_p, C.POINTER(C.POINTER(PcapHeader)),
                          C.POINTER(C.POINTER(C.c_ubyte))], C.c_int),
        "pcap_stats": ([C.c_void_p, C.POINTER(PcapStats)], C.c_int),
        "pcap_close": ([C.c_void_p], None),
    }
    for name, (arguments, result) in declarations.items():
        function = getattr(dll, name)
        function.argtypes = arguments
        function.restype = result
    return dll


def find_device(dll, local):
    devices = C.POINTER(PcapIf)()
    error = C.create_string_buffer(256)
    if dll.pcap_findalldevs(C.byref(devices), error) != 0:
        raise OSError(0, "enumeration failed")
    names = []
    try:
        device = devices
        seen_devices = set()
        while device:
            identity = C.addressof(device.contents)
            if identity in seen_devices or len(seen_devices) >= 50:
                raise OSError(0, "enumeration limit")
            seen_devices.add(identity)
            address = device.contents.addresses
            seen_addresses = set()
            matched = False
            while address:
                identity = C.addressof(address.contents)
                if identity in seen_addresses or len(seen_addresses) >= 50:
                    raise OSError(0, "address enumeration limit")
                seen_addresses.add(identity)
                sockaddr = address.contents.addr
                if sockaddr and sockaddr.contents.family == 2:
                    # sockaddr_in's IPv4 bytes occupy offsets 4..7; no strings,
                    # descriptions, other addresses, or hardware data logged.
                    octets = bytes(sockaddr.contents.data[2:6])
                    if str(ipaddress.IPv4Address(octets)) == local:
                        matched = True
                address = address.contents.next
            if matched:
                names.append(device.contents.name)
            device = device.contents.next
    finally:
        dll.pcap_freealldevs(devices)
    if len(names) != 1 or not names[0]:
        raise OSError(0, "local IPv4 must identify exactly one device")
    return names[0]


def run_capture(config):
    """Runtime is deliberately disabled, with no override or fallback.

    libpcap 1.10.5 pcap_setfilter_npf can return success after PacketSetBpf
    fails, silently choosing userland filtering. Thus its public success
    result does not prove kernel truncation or pre-filter queue flushing.
    Until a proven public kernel-only path is implemented, never load any
    DLL, enumerate adapters, open a handle, or read a capture packet here.
    """
    private_ipv4(config.local)
    private_ipv4(config.peer)
    if config.local == config.peer or not 1024 <= config.port <= 65535 or not 1 <= config.ttl <= 120:
        raise ValueError("invalid target or TTL")
    with config.output.open("x", encoding="ascii", newline="\n") as output:
        emit(output, start=1, host_pid=os.getpid(), local=int(ipaddress.IPv4Address(config.local)),
             peer=int(ipaddress.IPv4Address(config.peer)), port=config.port, ttl=config.ttl,
             elapsed_ms=0)
        emit(output, summary=1, runtime_disabled=1, outcome=6, errno=0, packets=0,
             payload_bytes=0, unknown_length_packets=0, rejected=0, stats_ok=0,
             ps_recv=0, ps_drop=0, ps_ifdrop=0, drops_present=0, pcap_closed=0,
             elapsed_ms=0)
    return 6

def main(argv=None):
    return run_capture(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
