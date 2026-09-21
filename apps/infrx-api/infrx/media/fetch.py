"""M1: bounded, SSRF-hardened materialization of a caller-supplied media source.

The legacy path in `video.py` stays exactly as it is until G1's cutover (r1 R2/R48);
this is the new one, and it differs in the ways MEDIA-SEC requires:

* the connection is **pinned** to the address that was validated - the request URL
  carries the literal IP, the `Host` header and the TLS SNI/certificate check carry
  the original hostname - so the resolve/connect window the legacy module documents
  cannot be used to rebind a name to an internal address;
* every redirect hop is re-parsed, re-resolved, re-validated and re-pinned, within a
  redirect budget;
* the byte cap is enforced *while the body streams* (on the raw bytes, with content
  coding refused outright, so a gzip bomb cannot decode past it) and the aggregate
  time limit is enforced between chunks, so a slow-loris body cannot hold a worker;
* nothing about the URL reaches the caller or the log: a refusal carries a reason
  class, the customer sees the fixed message of its error code, and the log line
  carries the host and that reason - never a query string, credentials or upstream
  exception text (the blind-SSRF oracle the legacy module also avoids).

Everything that touches the network or the clock is injected, so the adversarial
fixtures in `tests/m` need neither.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import socket
import time
from dataclasses import dataclass

import httpx

from ..config import DEFAULT_ALLOWED_VIDEO_MIME, EXT_MIME
from ..contracts import errors
from ..contracts.limits import DEFAULTS, PilotSettings
# One address policy in the repository: F1's, which already unwraps v4-mapped
# addresses and refuses private, loopback, link-local (169.254.169.254 and fe80::/10),
# CGNAT, multicast, reserved (so the NAT64 well-known prefix 64:ff9b::/96) and
# unspecified addresses, v4 and v6. Reusing it means a fix reaches both paths.
from .video import address_allowed

LOG = logging.getLogger(__name__)

ALLOWED_MIME = frozenset(m.strip().lower()
                         for m in DEFAULT_ALLOWED_VIDEO_MIME.split(",") if m.strip())
UNDECLARED_TYPES = ("", "application/octet-stream", "binary/octet-stream")
DATA_PREFIX = "data:"
HTTP_PREFIXES = ("http://", "https://")

# The closed vocabulary of refusal reasons. Operator-only: a customer sees the fixed
# message of the error code, never one of these.
REASONS = ("malformed-url", "unsupported-scheme", "credentials-in-url", "no-host", "dns",
           "blocked-address", "bad-redirect", "too-many-redirects", "http-status",
           "encoded-body", "unsupported-type", "too-large", "empty-body", "timeout",
           "bad-data-url", "bad-base64", "unsupported-source", "fetch-failed")


def digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Fetched:
    """One materialized source: the bytes, what they are, and their identity."""

    mime: str
    data: bytes
    digest: str
    host: str = ""            # sanitized origin: host only, no scheme, port, path or query


def refused(reason: str, *, host: str = "",
            exc: type[errors.DomainError] = errors.MediaFetchFailed) -> errors.DomainError:
    """A typed refusal carrying its reason class for the log, never for the customer."""
    assert reason in REASONS, reason
    error = exc(f"media source refused ({reason})")
    error.reason, error.host = reason, host
    return error


def video_mime(content_type: str | None, url: httpx.URL) -> str | None:
    """An allow-listed type for this response, or None. The URL's extension is used
    only when the server declines to say what it sent."""
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared in ALLOWED_MIME:
        return declared
    if declared in UNDECLARED_TYPES:
        path = url.path
        dot = path.rfind(".")
        guess = EXT_MIME.get(path[dot:].lower()) if dot >= 0 else None
        return guess if guess in ALLOWED_MIME else None
    return None


def parse_source(url: str) -> httpx.URL:
    """The URL a caller (or a `Location` header) supplied, or a typed refusal."""
    try:
        target = httpx.URL(url)
    except Exception:
        raise refused("malformed-url") from None
    if target.scheme not in ("http", "https"):
        raise refused("unsupported-scheme")
    if target.userinfo:
        # Credentials in the URL would be sent to the pinned address and logged by
        # anything that echoes the source; they are never a legitimate video source.
        raise refused("credentials-in-url", host=target.host)
    if not target.host:
        raise refused("no-host")
    return target


async def resolve_all(host: str) -> list[str]:
    """Every A/AAAA answer for the host. The default resolver; tests inject their own."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def decode_data_url(url: str, limits: PilotSettings = DEFAULTS) -> Fetched:
    """Strict, bounded base64.

    The bound is applied to the *encoded* text before anything is decoded, so a
    gigabyte `data:` URL never becomes three quarters of a gigabyte of process
    memory, and `validate=True` refuses the padding and whitespace tricks that make
    two different texts decode to the same bytes.
    """
    header, comma, payload = url[len(DATA_PREFIX):].partition(",")
    if not comma:
        raise refused("bad-data-url")
    parameters = [part.strip().lower() for part in header.split(";")]
    if "base64" not in parameters[1:]:
        raise refused("bad-data-url")            # only base64 is a bounded encoding
    mime = parameters[0]
    if mime not in ALLOWED_MIME:
        raise refused("unsupported-type", exc=errors.UnsupportedMedia)
    cap = limits.max_media_bytes
    if len(payload) > (cap + 2) // 3 * 4:
        raise refused("too-large", exc=errors.RequestTooLarge)
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise refused("bad-base64") from None
    if len(data) > cap:
        raise refused("too-large", exc=errors.RequestTooLarge)
    if not data:
        raise refused("empty-body")
    return Fetched(mime=mime, data=data, digest=digest_of(data))


class MediaFetcher:
    """One per app. `resolve` and `transport` are the seams the fixtures use; `monotonic`
    is the injected clock the aggregate limit is measured on, so a slow-loris case costs
    microseconds and never sleeps."""

    def __init__(self, limits: PilotSettings = DEFAULTS, *, resolve=None, transport=None,
                 monotonic=time.monotonic, log: logging.Logger = LOG) -> None:
        self.limits = limits
        self.resolve = resolve or resolve_all
        self.transport = transport
        self.monotonic = monotonic
        self.log = log

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.limits.media_fetch_timeout_s,
                                  connect=self.limits.media_fetch_connect_timeout_s),
            # Every hop is validated and pinned by hand below, so httpx must not follow
            # one for us - a followed redirect resolves the name again, unpinned.
            follow_redirects=False,
            # No HTTP_PROXY/HTTPS_PROXY/NO_PROXY, no SSL_CERT_FILE: a proxy in the
            # environment would connect to the proxy instead of the validated address,
            # and the pin would prove nothing.
            trust_env=False,
            transport=self.transport)

    async def fetch(self, url: str) -> Fetched:
        """Materialize an http(s) source once. Raises a typed `DomainError`."""
        try:
            return await self._fetch(url)
        except errors.DomainError as refusal:
            self.log.warning("media fetch refused: host=%s reason=%s",
                             getattr(refusal, "host", "") or "-",
                             getattr(refusal, "reason", "unknown"))
            raise
        except httpx.TimeoutException:
            self.log.warning("media fetch refused: reason=%s", "timeout")
            raise refused("timeout") from None
        except Exception as exc:
            # Never echoed and never chained: an upstream error message is a blind-SSRF
            # oracle, and the type name alone is enough for an operator.
            self.log.warning("media fetch failed: type=%s", type(exc).__name__)
            raise refused("fetch-failed") from None

    async def _fetch(self, url: str) -> Fetched:
        limits = self.limits
        cap = limits.max_media_bytes
        expires_at = self.monotonic() + limits.media_fetch_timeout_s
        body = bytearray()
        async with self.client() as client:
            for _hop in range(limits.media_fetch_max_redirects + 1):
                target = parse_source(url)
                if self.monotonic() >= expires_at:
                    raise refused("timeout", host=target.host)
                address = await self._pin(target.host)
                response = await client.send(
                    client.build_request(
                        # copy_with keeps the raw path, query and port exactly as they
                        # arrived and brackets an IPv6 literal for us.
                        "GET", target.copy_with(host=address),
                        headers={"Host": target.netloc.decode("ascii"),
                                 # No content coding: a compressed body decodes past
                                 # every byte cap that counted the wire bytes.
                                 "Accept-Encoding": "identity"},
                        # Pinned address, original name: the TLS handshake and the
                        # certificate check still happen against the host the caller
                        # asked for, so pinning does not weaken verification.
                        extensions={"sni_hostname": target.host}),
                    stream=True)
                try:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location:
                            raise refused("bad-redirect", host=target.host)
                        url = str(target.join(location))
                        continue              # re-parsed, re-resolved and re-pinned above
                    if response.status_code != 200:
                        raise refused("http-status", host=target.host)
                    coding = response.headers.get("content-encoding", "").strip().lower()
                    if coding not in ("", "identity"):
                        raise refused("encoded-body", host=target.host)
                    mime = video_mime(response.headers.get("content-type"), target)
                    if mime is None:
                        raise refused("unsupported-type", host=target.host,
                                      exc=errors.UnsupportedMedia)
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > cap:
                        raise refused("too-large", host=target.host, exc=errors.RequestTooLarge)
                    async for chunk in response.aiter_raw():
                        body += chunk
                        if len(body) > cap:
                            # Aborted mid-body: the rest is never read, so a lying
                            # Content-Length buys an attacker nothing.
                            raise refused("too-large", host=target.host,
                                          exc=errors.RequestTooLarge)
                        if self.monotonic() >= expires_at:
                            raise refused("timeout", host=target.host)
                    if not body:
                        raise refused("empty-body", host=target.host)
                    return Fetched(mime=mime, data=bytes(body), digest=digest_of(bytes(body)),
                                   host=target.host)
                finally:
                    await response.aclose()
            raise refused("too-many-redirects")

    async def _pin(self, host: str) -> str:
        """Resolve once and validate every answer.

        One private answer among many is a rebinding attempt, not a fallback; and the
        address returned here is the one the connection uses, so the name is never
        resolved a second time.
        """
        try:
            addresses = await self.resolve(host)
        except Exception:
            raise refused("dns", host=host) from None
        if not addresses:
            raise refused("dns", host=host)
        if not all(address_allowed(address) for address in addresses):
            raise refused("blocked-address", host=host)
        return addresses[0]
