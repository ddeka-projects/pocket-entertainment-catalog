"""One volatile editing lease shared by all browser clients."""

from __future__ import annotations

from dataclasses import dataclass
import re
import threading
import time
from typing import Callable


_IDENTITY = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")


class LeaseError(RuntimeError):
    code = "lease_error"


class LeaseConflict(LeaseError):
    code = "lease_conflict"


class LeaseExpired(LeaseError):
    code = "lease_expired"


@dataclass(frozen=True)
class LeaseSnapshot:
    claimed: bool
    expires_at: float | None


class EditorLease:
    def __init__(
        self,
        *,
        ttl_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Lease duration must be positive.")
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._client_id: str | None = None
        self._page_id: str | None = None
        self._expires_at = 0.0

    def claim(self, client_id: str, page_id: str) -> LeaseSnapshot:
        self._validate(client_id, page_id)
        with self._lock:
            now = self._clock()
            self._expire(now)
            if self._client_id is not None and (
                self._client_id != client_id or self._page_id != page_id
            ):
                raise LeaseConflict("Another page currently holds the editing lease.")
            self._client_id = client_id
            self._page_id = page_id
            self._expires_at = now + self._ttl
            return LeaseSnapshot(True, self._expires_at)

    def heartbeat(self, client_id: str, page_id: str) -> LeaseSnapshot:
        self._validate(client_id, page_id)
        with self._lock:
            now = self._clock()
            self._expire(now)
            self._authorize(client_id, page_id)
            self._expires_at = now + self._ttl
            return LeaseSnapshot(True, self._expires_at)

    def authorize(self, client_id: str, page_id: str) -> LeaseSnapshot:
        self._validate(client_id, page_id)
        with self._lock:
            now = self._clock()
            self._expire(now)
            self._authorize(client_id, page_id)
            return LeaseSnapshot(True, self._expires_at)

    def release(self, client_id: str, page_id: str) -> None:
        self._validate(client_id, page_id)
        with self._lock:
            self._expire(self._clock())
            if self._client_id is None:
                return
            if self._client_id != client_id or self._page_id != page_id:
                raise LeaseConflict("Another page holds the editing lease.")
            self._clear()

    def snapshot(self) -> LeaseSnapshot:
        with self._lock:
            now = self._clock()
            self._expire(now)
            return LeaseSnapshot(self._client_id is not None, self._expires_at or None)

    @staticmethod
    def _validate(client_id: str, page_id: str) -> None:
        if not isinstance(client_id, str) or not _IDENTITY.fullmatch(client_id):
            raise LeaseError("client_id must contain 1-128 URL-safe characters.")
        if not isinstance(page_id, str) or not _IDENTITY.fullmatch(page_id):
            raise LeaseError("page_id must contain 1-128 URL-safe characters.")

    def _authorize(self, client_id: str, page_id: str) -> None:
        if self._client_id is None:
            raise LeaseExpired("The editing lease has expired.")
        if self._client_id != client_id or self._page_id != page_id:
            raise LeaseConflict("Another page holds the editing lease.")

    def _expire(self, now: float) -> None:
        if self._client_id is not None and now >= self._expires_at:
            self._clear()

    def _clear(self) -> None:
        self._client_id = None
        self._page_id = None
        self._expires_at = 0.0
