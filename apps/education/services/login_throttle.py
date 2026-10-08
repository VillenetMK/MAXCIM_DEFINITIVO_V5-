from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Entry:
    failures: int = 0
    locked_until: float = 0.0
    last_failure: float = 0.0


class LoginThrottle:
    """In-memory limiter for failed password logins.

    Counts only *failed* attempts per (client IP, user id). After
    ``max_attempts`` failures the pair is locked for ``lockout_seconds``; a
    successful login clears it. Keying by the pair (not the IP alone) keeps one
    mistyped teacher from locking out a whole school behind a shared public IP,
    and (not the user id alone) keeps a stranger from locking a teacher out of
    their own account from another network.

    State lives in this process, which matches the deployment (one Gunicorn
    worker with threads). Behind several workers each would count separately;
    move the counters to a shared store (e.g. Redis) if that ever changes.
    """

    def __init__(self, max_attempts: int = 3, lockout_seconds: int = 900):
        self.max_attempts = max(1, int(max_attempts))
        self.lockout_seconds = max(1, int(lockout_seconds))
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(client_ip: str, user_id: str) -> tuple[str, str]:
        return (client_ip or "unknown", user_id.strip().lower())

    def _prune(self, now: float) -> None:
        # Drop stale entries so the dict cannot grow without bound.
        expiry = self.lockout_seconds
        for key in [
            key
            for key, entry in self._entries.items()
            if entry.locked_until <= now and now - entry.last_failure > expiry
        ]:
            del self._entries[key]

    def retry_after(self, client_ip: str, user_id: str) -> int:
        """Seconds until the pair may try again; 0 when it is not locked."""
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(self._key(client_ip, user_id))
            if entry is None or entry.locked_until <= now:
                return 0
            return max(1, int(entry.locked_until - now + 0.999))

    def record_failure(self, client_ip: str, user_id: str) -> int:
        """Register a failed attempt; returns remaining attempts (0 = locked)."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            entry = self._entries.setdefault(self._key(client_ip, user_id), _Entry())
            if entry.locked_until <= now and entry.failures >= self.max_attempts:
                # A previous lockout has expired: start a fresh window.
                entry.failures = 0
            entry.failures += 1
            entry.last_failure = now
            if entry.failures >= self.max_attempts:
                entry.locked_until = now + self.lockout_seconds
                return 0
            return self.max_attempts - entry.failures

    def record_success(self, client_ip: str, user_id: str) -> None:
        with self._lock:
            self._entries.pop(self._key(client_ip, user_id), None)
