#!/usr/bin/env python3
"""Serve the latest Codex quota snapshot to trusted devices on a local network.

The service deliberately exposes one read-only endpoint.  It is intended for a
private LAN during development; deploy it behind Tailscale or HTTPS before
making it reachable outside that network.
"""

from __future__ import annotations

import argparse
import hmac
import json
import ipaddress
import sys
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from stopwatch_bridge import AppServerClient, BridgeError, UsageSnapshot, locate_codex


POLL_SECONDS = 60.0
MAX_AGE_SECONDS = 120
CLIENT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ServiceConfig:
    server_host: str
    server_port: int
    device_token: str
    additional_hosts: tuple[str, ...] = ()


def load_config(path: Path) -> ServiceConfig:
    """Load the local secret configuration without echoing any credential."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError("unable to load LAN service configuration") from exc
    if not isinstance(raw, dict):
        raise BridgeError("LAN service configuration must be a JSON object")
    host = raw.get("server_host", "127.0.0.1")
    port = raw.get("server_port", 8765)
    token = raw.get("device_token")
    if not isinstance(host, str) or not host or len(host) > 255:
        raise BridgeError("invalid server_host in LAN service configuration")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise BridgeError("invalid server_port in LAN service configuration")
    if (
        not isinstance(token, str)
        or not token.isascii()
        or len(token) < 16
        or len(token) > 512
    ):
        raise BridgeError("invalid device_token in LAN service configuration")
    additional=raw.get("additional_hosts", [])
    if not isinstance(additional,list) or len(additional)>3:
        raise BridgeError("invalid additional_hosts")
    for address in [host]+additional:
        if not isinstance(address,str):raise BridgeError("service bind addresses must be strings")
        try:
            ip=ipaddress.ip_address(address)
        except ValueError as exc:
            raise BridgeError("service bind addresses must be IP literals") from exc
        if ip.is_unspecified or not (ip.is_private or ip in ipaddress.ip_network('100.64.0.0/10')):
            raise BridgeError("service must bind explicit LAN, loopback or tailnet addresses")
    return ServiceConfig(host, port, token, tuple(dict.fromkeys(x for x in additional if x!=host)))


class SnapshotStore:
    """Thread-safe latest-snapshot cache.  No value is invented before a read succeeds."""

    def __init__(
        self,
        *,
        wall_now: Callable[[], float] = time.time,
        monotonic_now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._wall_now = wall_now
        self._monotonic_now = monotonic_now
        self._snapshot: UsageSnapshot | None = None
        self._saved_at_monotonic: float | None = None
        self._initial_age_seconds = 0
        self._lock = threading.Lock()

    def save(self, snapshot: UsageSnapshot) -> None:
        initial_age = max(0, int(self._wall_now()) - snapshot.captured_epoch)
        with self._lock:
            self._snapshot = snapshot
            self._saved_at_monotonic = self._monotonic_now()
            self._initial_age_seconds = initial_age

    def status(self) -> tuple[dict[str, Any], bool]:
        with self._lock:
            snapshot = self._snapshot
            saved_at_monotonic = self._saved_at_monotonic
            initial_age = self._initial_age_seconds
        if snapshot is None:
            return self._unavailable(), False
        if saved_at_monotonic is None:  # Defensive: no cache entry can be partially valid.
            return self._unavailable(), False
        age = initial_age + max(0, int(self._monotonic_now() - saved_at_monotonic))
        available = age <= MAX_AGE_SECONDS
        return {
            "version": 1,
            "captured_epoch": snapshot.captured_epoch,
            "remaining_bp": snapshot.remaining_basis_points,
            "reset_epoch": snapshot.reset_epoch,
            "reset_credits": snapshot.reset_credits,
            "age_seconds": age,
            "available": available,
        }, available

    @staticmethod
    def _unavailable() -> dict[str, Any]:
        return {
            "version": 1,
            "captured_epoch": None,
            "remaining_bp": None,
            "reset_epoch": None,
            "reset_credits": None,
            "age_seconds": None,
            "available": False,
        }


class QuotaCollector:
    """Poll App Server independently from HTTP clients and retain only quota data."""

    def __init__(self, client_factory: Callable[[], AppServerClient], store: SnapshotStore) -> None:
        self._client_factory = client_factory
        self._store = store
        self._client: AppServerClient | None = None

    def poll_once(self) -> None:
        if self._client is None:
            self._client = self._client_factory()
            self._client.start()
        self._store.save(self._client.read_usage())

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def run_collector(collector: QuotaCollector, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            collector.poll_once()
        except BridgeError as exc:
            print(f"QUOTA SERVICE ERROR {exc}", file=sys.stderr)
            collector.close()
        stop.wait(POLL_SECONDS)


def is_authorized(authorization: str, device_token: str) -> bool:
    """Compare ASCII wire values only, so compare_digest always receives bytes."""
    try:
        supplied = authorization.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(supplied, f"Bearer {device_token}".encode("ascii"))


def make_handler(store: SnapshotStore, device_token: str) -> type[BaseHTTPRequestHandler]:
    class QuotaHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            self.request.settimeout(CLIENT_TIMEOUT_SECONDS)
            super().setup()

        def log_message(self, _format: str, *_args: object) -> None:
            # Authorization headers and request paths must not enter console logs.
            return

        def do_GET(self) -> None:  # noqa: N802 - required BaseHTTPRequestHandler name
            if self.path != "/v1/status":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            authorization = self.headers.get("Authorization", "")
            if not is_authorized(authorization, device_token):
                self.send_error(HTTPStatus.UNAUTHORIZED)
                return
            body, available = store.status()
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            self.send_response(HTTPStatus.OK if available else HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return QuotaHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(".artifacts/lan-config.json"),
        help="JSON file containing server_host, server_port, and device_token",
    )
    parser.add_argument("--codex-path", type=Path, help="explicit desktop-managed codex.exe")
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    servers=[]
    try:
        config = load_config(args.config)
        collector = QuotaCollector(lambda: AppServerClient(locate_codex(args.codex_path)), SnapshotStore())
        stop = threading.Event()
        worker = threading.Thread(target=run_collector, args=(collector, stop), daemon=True)
        for host in (config.server_host,)+config.additional_hosts:
            servers.append(ThreadingHTTPServer((host, config.server_port), make_handler(collector._store, config.device_token)))
    except (BridgeError, OSError) as exc:
        for server in servers: server.server_close()
        print(f"QUOTA SERVICE ERROR {exc}", file=sys.stderr)
        return 2
    worker.start()
    serving=[threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.5},daemon=True) for server in servers]
    for thread in serving:thread.start()
    try:
        while not stop.wait(1):
            if any(not thread.is_alive() for thread in serving):return 2
    except KeyboardInterrupt:
        return 130
    finally:
        stop.set()
        for server in servers:
            server.shutdown()
            server.server_close()
        worker.join(timeout=2)
        collector.close()


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
