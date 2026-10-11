"""Bounded synthetic TCP fixture. Importing this module never opens a socket.

Wire format: byte at global offset n is n % 251, without a header.
JSONL values are numeric only. IPv4 addresses are encoded as unsigned integers.
Outcome: 0 complete/EOF, 1 accept timeout, 2 total deadline, 3 peer EOF
before transmit completes, 4 unexpected inbound data, 5 drain timeout, 6 OS error.
"""
import argparse
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import select
import socket
import time

CHUNK = 512
INTERVAL = 0.5
DRAIN_SECONDS = 5.0
PRIVATE = tuple(ipaddress.IPv4Network(n) for n in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def private_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        raise argparse.ArgumentTypeError("requires a literal RFC1918 IPv4 address") from None
    if (not any(address in net for net in PRIVATE) or str(address) != value
            or (int(address) & 255) in (0, 255)):
        raise argparse.ArgumentTypeError("requires a literal RFC1918 IPv4 address")
    return value


def bounded_integer(low, high):
    def parse(value):
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("requires an integer") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"requires {low}..{high}")
        return number
    return parse


@dataclass(frozen=True)
class Config:
    bind: str
    peer: str
    port: int
    seconds: int
    output: Path
    ttl: int


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", required=True, type=private_ipv4)
    parser.add_argument("--peer", required=True, type=private_ipv4)
    parser.add_argument("--port", required=True, type=bounded_integer(1024, 65535))
    parser.add_argument("--seconds", required=True, type=bounded_integer(10, 60))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ttl", required=True, type=bounded_integer(1, 120))
    args = parser.parse_args(argv)
    if args.ttl < args.seconds + DRAIN_SECONDS:
        parser.error("ttl must include the requested duration and 5 second drain")
    if not args.output.is_absolute():
        parser.error("output must be an absolute path in an existing private directory")
    return Config(**vars(args))


def pattern(offset, count=CHUNK):
    return bytes((offset + i) % 251 for i in range(count))


def emit(output, **values):
    if any(type(v) not in (int, float) for v in values.values()):
        raise ValueError("metadata must be numeric")
    output.write(json.dumps(values, separators=(",", ":")) + "\n")
    output.flush()


def peer_status(connection):
    # Peek one byte only to distinguish EOF from unwanted data; never consume,
    # retain, decode, or log any inbound application body.
    try:
        value = connection.recv(1, socket.MSG_PEEK)
    except (BlockingIOError, InterruptedError):
        return None
    return 3 if not value else 4


def transmit(connection, seconds, deadline, record, clock=time.monotonic,
             wait=select.select):
    started = clock()
    total = CHUNK * 2 * seconds
    sent = 0
    calls = 0
    chunk = b""
    while sent < total or clock() < started + seconds:
        now = clock()
        if now >= deadline:
            return 2, sent, calls
        # Do not catch up by bursting chunks after backpressure. Complete each
        # chunk then wait until both the nominal slot and previous slot + 0.5 s.
        if not chunk and sent < total:
            due = started + (sent // CHUNK) * INTERVAL
            if sent:
                due = max(due, last_chunk_started + INTERVAL)
            if now >= due:
                chunk = pattern(sent)
                last_chunk_started = now
        target = deadline
        if not chunk:
            target = min(target, due if sent < total else started + seconds)
        readable, writable, _ = wait([connection], [connection] if chunk else [], [],
                                     max(0.0, target - now))
        if readable:
            status = peer_status(connection)
            if status is not None:
                return status, sent, calls
        if writable:
            try:
                amount = connection.send(chunk)
            except (BlockingIOError, InterruptedError):
                continue
            if amount == 0:
                return 3, sent, calls
            sent += amount
            calls += 1
            chunk = chunk[amount:]
            record(send=1, sent_bytes=sent, send_calls=calls)
    connection.shutdown(socket.SHUT_WR)
    record(shutdown_write=1, sent_bytes=sent, send_calls=calls)
    drain_deadline = min(deadline, clock() + DRAIN_SECONDS)
    while True:
        now = clock()
        if now >= drain_deadline:
            return (2 if now >= deadline else 5), sent, calls
        readable, _, _ = wait([connection], [], [], drain_deadline - now)
        if readable:
            status = peer_status(connection)
            if status is not None:
                return (0 if status == 3 else status), sent, calls


def run_fixture(config):
    # Defense in depth for callers other than CLI. Loopback/ephemeral port are
    # supported only by this internal API for offline tests, never CLI.
    for value in (config.bind, config.peer):
        address = ipaddress.IPv4Address(value)
        if not (address.is_loopback or any(address in net for net in PRIVATE)):
            raise ValueError("only RFC1918 or offline loopback addresses")
    if not (config.port == 0 or 1024 <= config.port <= 65535) or not 1 <= config.ttl <= 120:
        raise ValueError("invalid fixture budget or port")
    if not 1 <= config.seconds <= 60:
        raise ValueError("invalid fixture duration")
    # x mode is before socket creation. Never create directories or overwrite.
    with config.output.open("x", encoding="ascii", newline="\n") as output:
        started = time.monotonic()
        deadline = started + config.ttl
        accepted = rejected = sent = calls = error = 0
        outcome = 6
        listener = connection = None

        def record(**values):
            nonlocal sent, calls
            sent = values.get("sent_bytes", sent)
            calls = values.get("send_calls", calls)
            emit(output, elapsed_ms=int((time.monotonic() - started) * 1000), **values)

        record(start=1, host_pid=os.getpid(), bind_ipv4=int(ipaddress.IPv4Address(config.bind)),
               peer_ipv4=int(ipaddress.IPv4Address(config.peer)), port=config.port,
               seconds=config.seconds, ttl=config.ttl, target_bytes=CHUNK * 2 * config.seconds)
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setblocking(False)
            listener.bind((config.bind, config.port))
            listener.listen(1)
            record(ready=1, bind_ipv4=int(ipaddress.IPv4Address(config.bind)),
                   port=listener.getsockname()[1], host_pid=os.getpid())
            accept_deadline = min(deadline, started + 60)
            while connection is None:
                remaining = accept_deadline - time.monotonic()
                if remaining <= 0:
                    outcome = 2 if time.monotonic() >= deadline else 1
                    break
                readable, _, _ = select.select([listener], [], [], remaining)
                if not readable:
                    continue
                try:
                    candidate, address = listener.accept()
                except (BlockingIOError, InterruptedError):
                    continue
                if address[0] != config.peer:
                    candidate.close()
                    rejected += 1
                    # Counts are final-only to avoid rejection-driven log growth.
                    continue
                connection = candidate
                connection.setblocking(False)
                accepted = 1
                listener.close()
                listener = None
                record(accept=1, accepted=1, rejected=rejected,
                       tcp_nodelay=connection.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY),
                       so_keepalive=connection.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE))
                outcome, sent, calls = transmit(connection, config.seconds, deadline, record)
        except OSError as exc:
            error = exc.errno or 0
            outcome = 6
        finally:
            if connection is not None:
                connection.close()
            if listener is not None:
                listener.close()
            record(summary=1, outcome=outcome, errno=error, accepted=accepted,
                   rejected=rejected, sent_bytes=sent, send_calls=calls, closed=1)
        return outcome


def main(argv=None):
    return run_fixture(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
