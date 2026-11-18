"""Sign-in protection: failed-attempt throttling and timing equalisation.

SkillLink keeps these counters in process memory. That is sufficient for a
single-process deployment (the development server) but a multi-worker
deployment must move them to a shared store (Redis, or a `login_attempts`
table) so every worker sees the same counters. Keying on both the client
address and the email address means one attacker cannot lock an unrelated
account out, and one noisy IP cannot hide behind another account.
"""
from __future__ import annotations

import secrets
import threading
import time
from collections import deque

from werkzeug.security import check_password_hash, generate_password_hash

AttemptKey = tuple[str, str]

_DUMMY_HASH = None
_DUMMY_LOCK = threading.Lock()


def equalize_login_timing(password: str) -> None:
    """Spend one password comparison so an unknown email costs the same as a known one.

    Without this, an attacker can tell which email addresses are registered
    from the response time alone, because a missing user skips the expensive
    hash check.
    """
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        with _DUMMY_LOCK:
            if _DUMMY_HASH is None:
                _DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32))
    check_password_hash(_DUMMY_HASH, password or "")


class LoginThrottle:
    """Counts recent failures per (client address, email) pair."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 300, max_keys: int = 10_000):
        self.max_failures = max(1, int(max_failures))
        self.window_seconds = max(1, int(window_seconds))
        self.max_keys = max(1, int(max_keys))
        self._failures: dict[AttemptKey, deque[float]] = {}
        self._lock = threading.Lock()

    def _active_failures(self, key: AttemptKey, now: float):
        """Drop expired stamps for one key; return the deque or None."""
        stamps = self._failures.get(key)
        if stamps is None:
            return None
        cutoff = now - self.window_seconds
        while stamps and stamps[0] <= cutoff:
            stamps.popleft()
        if not stamps:
            self._failures.pop(key, None)
            return None
        return stamps

    def _evict_expired(self, now: float) -> None:
        for key in list(self._failures):
            self._active_failures(key, now)

    def failure_count(self, key: AttemptKey) -> int:
        now = time.monotonic()
        with self._lock:
            stamps = self._active_failures(key, now)
            return len(stamps) if stamps else 0

    def is_locked(self, key: AttemptKey) -> bool:
        return self.failure_count(key) >= self.max_failures

    def retry_after(self, key: AttemptKey) -> int:
        """Seconds until the oldest failure leaves the window."""
        now = time.monotonic()
        with self._lock:
            stamps = self._active_failures(key, now)
            if not stamps:
                return 0
            return max(1, int(self.window_seconds - (now - stamps[0])))

    def record_failure(self, key: AttemptKey) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._failures) >= self.max_keys:
                self._evict_expired(now)
            stamps = self._active_failures(key, now)
            if stamps is None:
                stamps = deque()
                self._failures[key] = stamps
            stamps.append(now)

    def reset(self, key: AttemptKey) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def reset_all(self) -> None:
        """Clear every counter. Used by tests and after configuration changes."""
        with self._lock:
            self._failures.clear()
