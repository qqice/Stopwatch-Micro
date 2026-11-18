"""Poll active ChatGPT promotional API grants from a Mac login Keychain.

This optional collector uses an undocumented dashboard endpoint. It never reads
browser storage, redirects requests, renews sessions, or logs/saves credentials.
Session timing is observational and in-process, not the actual issuance or TTL.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from decimal import Decimal, DecimalException, ROUND_HALF_UP
from pathlib import Path
from typing import Callable


POLL_SECONDS = 15 * 60
KEYCHAIN_CHECK_SECONDS = 5 * 60
MAX_BACKOFF_SECONDS = 60 * 60
MAX_BODY = 256 * 1024
UINT32_MAX = 2**32 - 1
KEYCHAIN_SERVICE = "com.qqice.mosaico-beacon.openai-api-billing"
KEYCHAIN_ACCOUNT = "platform-session"
HOST = "api.openai.com"
PATH = "/v1/dashboard/billing/credit_grants"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0")
PROMO_PREFIX = "chatgpt_api_credits_promo_"
_ORG = re.compile(r"org-[A-Za-z0-9_-]{1,128}\Z", re.ASCII)
_SESSION = re.compile(rb"sess-[A-Za-z0-9_-]{12,2048}\Z", re.ASCII)


def valid_organization(value: object) -> bool:
    return isinstance(value, str) and _ORG.fullmatch(value) is not None


def read_session() -> bytes | None:
    """Access the one configured Keychain item without putting its value in argv."""
    keychain = Path.home() / "Library/Keychains/login.keychain-db"
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
             "-a", KEYCHAIN_ACCOUNT, "-w", str(keychain)], capture_output=True,
            timeout=10, stdin=subprocess.DEVNULL, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and _SESSION.fullmatch(token) else None


def get_grants(organization: str, token: bytes) -> tuple[int, bytes]:
    """Only talk to the verified TLS host/path; never follow HTTP redirects."""
    if not valid_organization(organization) or not _SESSION.fullmatch(token):
        raise ValueError("invalid API credit request configuration")
    conn = http.client.HTTPSConnection(HOST, timeout=12)
    try:
        conn.request("GET", PATH, headers={
            "Authorization": "Bearer " + token.decode("ascii"),
            "OpenAI-Organization": organization,
            "Accept": "application/json", "User-Agent": UA,
            "Origin": "https://platform.openai.com",
            "Referer": "https://platform.openai.com/",
        })
        with conn.getresponse() as response:
            if response.status != 200:
                return response.status, b""
            data = bytearray()
            deadline = time.monotonic() + 12
            while len(data) <= MAX_BODY:
                if time.monotonic() >= deadline:
                    raise TimeoutError("API credit response timeout")
                chunk = response.read1(min(8192, MAX_BODY + 1 - len(data)))
                if not chunk:
                    return 200, bytes(data)
                data.extend(chunk)
            raise ValueError("API credit response too large")
    finally:
        conn.close()


@dataclass(frozen=True)
class Snapshot:
    remaining_cents: int
    expires_epoch: int
    captured_epoch: int


def parse_grants(data: bytes, now: int) -> Snapshot:
    """Sum remaining active promo grants, with the earliest nonempty expiry."""
    if not 1 <= now <= UINT32_MAX or len(data) > MAX_BODY:
        raise ValueError("invalid API credit snapshot bounds")
    decoded = json.loads(data, parse_float=Decimal, parse_int=Decimal)
    if not isinstance(decoded, dict) or decoded.get("object") != "credit_summary":
        raise ValueError("invalid API credit summary")
    grants = decoded.get("grants")
    if (not isinstance(grants, dict) or grants.get("object") != "list"
            or not isinstance(grants.get("data"), list)
            or ("has_more" in grants and grants["has_more"] is not False)):
        raise ValueError("missing or incomplete API credit grants")
    remaining = Decimal(0)
    nearest = 0
    seen: set[str] = set()
    for grant in grants["data"]:
        if not isinstance(grant, dict) or not isinstance(grant.get("id"), str) or not grant["id"]:
            raise ValueError("invalid API credit grant")
        ident = grant["id"]
        if not ident.startswith(PROMO_PREFIX):
            continue
        if ident in seen:
            raise ValueError("duplicate API promotional grant")
        seen.add(ident)
        scheduled, held = grant.get("is_scheduled"), grant.get("is_held_for_prepaid_transition")
        if type(scheduled) is not bool or type(held) is not bool:
            raise ValueError("missing API credit grant status")
        values = tuple(grant.get(k) for k in ("effective_at", "expires_at", "grant_amount", "used_amount"))
        if not all(isinstance(x, Decimal) and x.is_finite() for x in values):
            raise ValueError("invalid API credit grant amount or timestamp")
        effective, expires, amount, used = values
        if not (0 <= effective < expires <= UINT32_MAX and 0 <= used <= amount <= UINT32_MAX):
            raise ValueError("API credit grant out of range")
        if not scheduled and not held and effective <= now < expires and used < amount:
            expiry = int(expires)
            nearest = min(nearest, expiry) if nearest else expiry
            remaining += amount - used
    cents = int((remaining * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if cents > UINT32_MAX:
        raise ValueError("API promo credit balance out of range")
    return Snapshot(cents, nearest, now)


class ApiCreditCollector:
    """Isolated optional poller. Status always preserves the last good snapshot."""

    def __init__(
        self, organization: str, *,
        session_reader: Callable[[], bytes | None] = read_session,
        fetch: Callable[[str, bytes], tuple[int, bytes]] = get_grants,
        wall_now: Callable[[], float] = time.time,
        log: Callable[[str], None] | None = None,
    ) -> None:
        if not valid_organization(organization):
            raise ValueError("invalid API credit organization")
        self._organization = organization
        self._session_reader = session_reader
        self._fetch = fetch
        self._now = wall_now
        self._log = log if log is not None else lambda msg: print(msg, file=sys.stderr, flush=True)
        self._lock = threading.Lock()
        self._state = "unavailable"
        self._snapshot = Snapshot(0, 0, 0)
        # Only the polling thread touches these fields. The digest remains in RAM.
        self._session_digest: bytes | None = None
        self._blocked = False
        self._failures = 0
        self._first_ok = self._last_ok = self._first_401 = 0

    def status(self) -> dict[str, str | int]:
        with self._lock:
            state, last = self._state, self._snapshot
        return {"state": state, "remaining_cents": last.remaining_cents,
                "expires_epoch": last.expires_epoch, "captured_epoch": last.captured_epoch}

    def _set(self, state: str, latest: Snapshot | None = None) -> None:
        with self._lock:
            self._state = state
            if latest is not None:
                self._snapshot = latest

    def _transient(self, state: str | None = None) -> int:
        self._failures = min(self._failures + 1, 3)
        self._set(state or ("stale" if self.status()["captured_epoch"] else "unavailable"))
        return min(POLL_SECONDS * (2**self._failures), MAX_BACKOFF_SECONDS)

    def _observed(self, reason: str) -> None:
        span = self._last_ok - self._first_ok if self._first_ok else 0
        self._log(f"API CREDIT session_observed event={reason} first_ok_epoch={self._first_ok} "
                  f"last_ok_epoch={self._last_ok} first_401_epoch={self._first_401} "
                  f"observed_ok_span_seconds={max(0, span)}")

    def poll_once(self) -> int:
        """Return the next wait, without doing a second request in this poll."""
        token = self._session_reader()
        if token is None:
            if not self._blocked:
                self._set("stale" if self.status()["captured_epoch"] else "unavailable")
            return KEYCHAIN_CHECK_SECONDS
        digest = hashlib.sha256(token).digest()
        if self._session_digest is None or not hmac.compare_digest(digest, self._session_digest):
            if self._session_digest is not None:
                self._observed("replacement")
            self._session_digest = digest
            self._first_ok = self._last_ok = self._first_401 = self._failures = 0
            self._blocked = False
        if self._blocked:
            return KEYCHAIN_CHECK_SECONDS
        try:
            status, data = self._fetch(self._organization, token)
            if status == 401:
                self._blocked = True
                self._first_401 = int(self._now())
                self._set("reauth")
                self._observed("http401")
                return KEYCHAIN_CHECK_SECONDS
            if status == 403:
                return self._transient("forbidden")
            if status != 200:
                return self._transient()
            snap = parse_grants(data, int(self._now()))
        except (OSError, ValueError, OverflowError, RecursionError,
                DecimalException, http.client.HTTPException):
            # No exception text, response body, or headers in logs.
            return self._transient()
        self._last_ok = snap.captured_epoch
        if not self._first_ok:
            self._first_ok = self._last_ok
        self._observed("success")
        self._failures = 0
        self._set("ok", snap)
        return POLL_SECONDS

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            stop.wait(self.poll_once())
