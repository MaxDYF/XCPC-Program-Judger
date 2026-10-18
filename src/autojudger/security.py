"""Public HTTP boundary: host isolation, upload limits and per-IP quotas."""

from __future__ import annotations

from collections import deque
import ipaddress
import math
import os
import threading
import time
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


MAX_REQUEST_BYTES = 22 * 1024 * 1024
SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", b"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),
)


def _hostname(value: str) -> str | None:
    if not value or any(char in value for char in "@/\\?#"):
        return None
    try:
        parsed = urlsplit("//" + value)
        _ = parsed.port
    except ValueError:
        return None
    return parsed.hostname.lower() if parsed.hostname else None


def _same_origin(value: str, host: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and parsed.netloc.lower() == host.lower()


def _client_ip(scope, headers: dict[bytes, str], public_hosts: set[str], host: str) -> str:
    """Trust Cloudflare's visitor header only across the local tunnel boundary."""
    peer = (scope.get("client") or ("unknown", 0))[0]
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if peer_address.is_loopback and _hostname(host) in public_hosts:
        forwarded = headers.get(b"cf-connecting-ip", "")
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return str(peer_address)


class SubmissionLimiter:
    """Atomic sliding-window counter; this service intentionally runs one worker."""

    def __init__(self, limit: int = 5, window_seconds: int = 600, max_ips: int = 10000,
                 clock=time.monotonic):
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_ips = max_ips
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def take(self, address: str) -> int:
        """Return 0 when accepted, otherwise seconds until the next slot."""
        now = self.clock()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits.get(address)
            if hits is None:
                if len(self._hits) >= self.max_ips:
                    for key, values in list(self._hits.items()):
                        if values[-1] <= cutoff:
                            del self._hits[key]
                if len(self._hits) >= self.max_ips:
                    return self.window_seconds
                hits = self._hits[address] = deque()
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, math.ceil(self.window_seconds - (now - hits[0])))
            hits.append(now)
            return 0


_submission_limiter = SubmissionLimiter()


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app
        self._request_slots = threading.BoundedSemaphore(4)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value.decode("latin-1") for key, value in scope["headers"]}
        host = headers.get(b"host", "")
        allowed = {"localhost", "127.0.0.1", "::1"}
        public_hosts = {value.strip().lower() for value in os.getenv("AUTOJUDGER_PUBLIC_HOST", "").split(",") if value.strip()}
        allowed.update(public_hosts)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + list(SECURITY_HEADERS)
            await send(message)

        async def reject(status: int, message: str, retry_after: int | None = None):
            response = JSONResponse({"detail": message}, status_code=status)
            if retry_after is not None:
                response.headers["Retry-After"] = str(retry_after)
            await response(scope, receive, secured_send)

        if _hostname(host) not in allowed:
            await reject(400, "Invalid host")
            return

        if scope["method"] in ("POST", "PUT", "PATCH", "DELETE"):
            if (headers.get(b"x-xcpc-problemjudger-request") != "1"
                    and headers.get(b"x-autojudger-request") != "1") or headers.get(b"sec-fetch-site") == "cross-site":
                await reject(403, "Request origin rejected")
                return
            if any(not _same_origin(headers[key], host) for key in (b"origin", b"referer") if key in headers):
                await reject(403, "Request origin rejected")
                return
            length = headers.get(b"content-length")
            if length is not None and (not length.isdigit() or int(length) > MAX_REQUEST_BYTES):
                await reject(413, "Request is too large")
                return
            if scope["method"] == "POST" and scope["path"] in ("/jobs", "/evaluate"):
                retry_after = _submission_limiter.take(_client_ip(scope, headers, public_hosts, host))
                if retry_after:
                    await reject(429, "每个 IP 在 10 分钟内最多提交 5 次 PDF，请稍后重试。", retry_after=retry_after)
                    return
            if not self._request_slots.acquire(blocking=False):
                await reject(429, "Too many upload requests")
                return
            try:
                body = bytearray()
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    body.extend(event.get("body", b""))
                    if len(body) > MAX_REQUEST_BYTES:
                        await reject(413, "Request is too large")
                        return
                    if not event.get("more_body", False):
                        break
                delivered = False

                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await receive()

                await self.app(scope, replay, secured_send)
            finally:
                self._request_slots.release()
            return
        await self.app(scope, receive, secured_send)
