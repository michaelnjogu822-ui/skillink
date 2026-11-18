"""Application-layer firewall.

A reverse proxy or a WAF in front of the app can be bypassed, misconfigured or
simply absent, so the application screens its own requests as well. This module
implements that screening as one ``before_request`` hook:

1. Host allow-listing, which stops Host-header injection and DNS-rebinding
   attempts from reaching the app with an unexpected hostname.
2. Method allow-listing, so scanners cannot probe with TRACE or CONNECT.
3. Path screening for the files and consoles that automated scanners request
   (``/.env``, ``/.git``, ``/wp-admin``, ``*.php``, backup files, traversal
   sequences). These answer 404 so the probe learns nothing.
4. Query-string screening for classic injection payloads.
5. Content-type and size checks on state-changing requests.
6. Fixed-window rate limits per client address, with tighter limits for
   authentication and for writes.

Every rejection is logged and kept in a small in-memory ring buffer that the
administrator can review at ``/admin/security``. The buffer is per process:
with several workers each keeps its own recent list, so it is for visibility,
not for accounting. Counters reset when the process restarts, which is an
accepted trade-off for a dependency-free design; a multi-worker deployment
should move the counters to Redis.
"""
from __future__ import annotations

import re
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import unquote, unquote_plus

from flask import current_app, render_template, request

ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ALLOWED_CONTENT_TYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data"})

# Paths that scanners are known to request. Matching is case-insensitive.
BLOCKED_PATH_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in (
        r"(^|/)\.(env|git|svn|hg)(/|$|\.)",
        r"/wp-(admin|login|content)",
        r"/phpmyadmin",
        r"/pma(/|$)",
        r"/xmlrpc\.php",
        r"\.php$",
        r"\.asp(x)?$",
        r"\.jsp$",
        r"\.cgi$",
        r"/(\.|%2e){2}(/|\\|$)",
        r"\.(bak|old|orig|sql|sqlite|db|log|ini|conf)$",
        r"/etc/(passwd|shadow)",
        r"/(vendor|composer)\.(json|lock)$",
        r"/server-status",
    )
)

# Narrow injection signatures. These are deliberately specific: a legitimate
# search for the word "select" must not be rejected.
SUSPICIOUS_QUERY_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in (
        r"union\s+(all\s+)?select",
        r"('\s*or\s*'?[0-9]+'?\s*=\s*'?[0-9]+)",
        r"<\s*script",
        r";\s*drop\s+(table|database)",
        r"\b(sleep|benchmark)\s*\(\s*[0-9]+\s*\)",
        r"information_schema",
        r"waitfor\s+delay",
        r"\.\./",
    )
)

_TITLES = {
    400: "That request could not be processed",
    404: "We couldn't find that page",
    405: "That method is not allowed",
    413: "That request is too large",
    415: "Unsupported content type",
    429: "Too many requests",
}


@dataclass(frozen=True)
class BlockedRequest:
    at: datetime
    ip: str
    method: str
    path: str
    rule: str
    status: int


class RateLimiter:
    """Fixed-window counters per (scope, client address)."""

    def __init__(self, limits: dict[str, tuple[int, int]], max_buckets: int = 20_000):
        self._limits = {scope: (max(1, int(limit)), max(1, int(window)))
                        for scope, (limit, window) in limits.items()}
        self._buckets: dict[tuple[str, str], list] = {}
        self._lock = threading.Lock()
        self.max_buckets = max_buckets

    def check(self, scope: str, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        limit, window = self._limits[scope]
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get((scope, key))
            if bucket is None or now - bucket[0] >= window:
                self._buckets[(scope, key)] = [now, 1]
                self._prune(now)
                return True, 0
            bucket[1] += 1
            if bucket[1] > limit:
                return False, max(1, int(window - (now - bucket[0])))
            return True, 0

    def _prune(self, now: float) -> None:
        if len(self._buckets) < self.max_buckets:
            return
        expired = [
            key for key, bucket in self._buckets.items()
            if now - bucket[0] >= self._limits[key[0]][1]
        ]
        for key in expired:
            self._buckets.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class FirewallLog:
    """Bounded, in-memory record of rejected requests for the admin view."""

    def __init__(self, max_entries: int = 200):
        self._entries: deque[BlockedRequest] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def record(self, ip: str, method: str, path: str, rule: str, status: int) -> None:
        entry = BlockedRequest(
            at=datetime.now(timezone.utc), ip=ip, method=method, path=path[:300],
            rule=rule, status=status,
        )
        with self._lock:
            self._entries.appendleft(entry)

    def entries(self) -> list[BlockedRequest]:
        with self._lock:
            return list(self._entries)

    def rule_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(Counter(entry.rule for entry in self._entries))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _block(rule: str, status: int, *, retry_after: int = 0, detail: str | None = None):
    ip = request.remote_addr or "unknown"
    path = request.path
    current_app.extensions["firewall_log"].record(ip, request.method, path, rule, status)
    current_app.logger.warning(
        "firewall blocked %s %s from %s (rule=%s, status=%s)", request.method, path, ip, rule, status
    )
    body = render_template(
        "errors/error.html", code=status, title=_TITLES[status],
        message=detail or "The request was refused by the server's security rules.",
    )
    response = current_app.make_response((body, status))
    if retry_after:
        response.headers["Retry-After"] = str(retry_after)
    return response


def _looks_suspicious_path(path: str) -> bool:
    lowered = unquote(path).lower()
    if "\x00" in lowered or "\\" in lowered:
        return True
    return any(pattern.search(lowered) for pattern in BLOCKED_PATH_PATTERNS)


def _looks_suspicious_query(raw: bytes) -> bool:
    text = unquote_plus(raw.decode("latin-1", "ignore")).lower()
    return any(pattern.search(text) for pattern in SUSPICIOUS_QUERY_PATTERNS)


def screen_request():
    """Return a rejection response, or None to let the request continue."""
    path = request.path

    allowed_hosts = current_app.config.get("ALLOWED_HOSTS") or ()
    if allowed_hosts:
        host = (request.host or "").split(":")[0].lower()
        if host not in allowed_hosts:
            return _block("invalid_host", 400, detail="This hostname is not served by SkillLink.")

    if request.method not in ALLOWED_METHODS:
        return _block("method_not_allowed", 405)

    if _looks_suspicious_path(path):
        return _block("suspicious_path", 404)

    if _looks_suspicious_query(request.query_string):
        return _block("suspicious_query", 400)

    if request.method in WRITE_METHODS:
        length = request.content_length or 0
        limit = current_app.config.get("MAX_CONTENT_LENGTH") or 0
        if limit and length > limit:
            return _block("oversized_body", 413)
        if length and request.mimetype not in ALLOWED_CONTENT_TYPES:
            return _block("unsupported_content_type", 415)

    if path.startswith(("/static/", "/uploads/", "/health")):
        return None

    limiter = current_app.extensions["rate_limiter"]
    key = request.remote_addr or "unknown"
    scopes = ["global"]
    if path.startswith("/auth/"):
        scopes.append("auth")
    if request.method in WRITE_METHODS:
        scopes.append("write")
    for scope in scopes:
        allowed, retry_after = limiter.check(scope, key)
        if not allowed:
            return _block(
                f"rate_limit:{scope}", 429, retry_after=retry_after,
                detail="Too many requests from this address. Wait a moment and try again.",
            )
    return None
