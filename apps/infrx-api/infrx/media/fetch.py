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
# One address policy in the repository: F1's, which M tightened in place (review B1) so
# that it states the tunnel, IPv4-compatible and special-purpose ranges itself instead of
# inheriting whatever the interpreter's tables happen to say. Reusing it means a fix
# reaches both paths and the two can never drift apart.
from .video import address_allowed

LOG = logging.getLogger(__name__)

# httpx logs one request line per request at INFO - "HTTP Request: GET <url> ..." - and
# httpcore logs the same target at DEBUG. For a pinned fetch that line carries the
# validated IP *and* the caller's query string (a signed URL, a capability token) into the
# process log, on every hop, which is exactly what this module takes care never to write
# itself. Stated at import so no logging configuration elsewhere can raise a level and
# turn it back on (review B2); the process's own logging config is an integration note.
TRANSPORT_LOGGERS = ("httpx", "httpcore")


def silence_transport_logs(names=TRANSPORT_LOGGERS, level=logging.WARNING):
    for name in names:
        logging.getLogger(name).setLevel(level)


silence_transport_logs()

ALLOWED_MIME = frozenset(m.strip().lower()
                         for m in DEFAULT_ALLOWED_VIDEO_MIME.split(",") if m.strip())
UNDECLARED_TYPES = ("", "application/octet-stream", "binary/octet-stream")
# How long after the aggregate limit the real-clock backstop fires. The fetcher's own
# checks run on the injected clock and produce a typed refusal naming the host and the
# reason; this exists only for the phases no injected clock can see, so it must not win
# the race against them.
BACKSTOP_GRACE_S = 1.0
# M4: the first time a download's head is shown to `early`, and then each time it has
# doubled - so at most seven looks up to the 64 MiB cap, and none for a body so small that
# refusing it early saves nothing worth a look.
EARLY_LOOK_BYTES = 1 << 20
DATA_PREFIX = "data:"
HTTP_PREFIXES = ("http://", "https://")

# The closed vocabulary of refusal reasons. Operator-only: a customer sees the fixed
# message of the error code, never one of these.
REASONS = ("malformed-url", "unsupported-scheme", "insecure-redirect", "credentials-in-url",
           "no-host", "dns", "blocked-address", "bad-redirect", "too-many-redirects",
           "http-status", "encoded-body", "unsupported-type", "too-large", "empty-body",
           "timeout", "bad-data-url", "bad-base64", "unsupported-source", "fetch-failed")


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


def video_mime(content_type: str | None, url: httpx.URL, allowed=ALLOWED_MIME) -> str | None:
    """An allow-listed type for this response, or None. The URL's extension is used
    only when the server declines to say what it sent."""
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared in allowed:
        return declared
    if declared in UNDECLARED_TYPES:
        path = url.path
        dot = path.rfind(".")
        guess = EXT_MIME.get(path[dot:].lower()) if dot >= 0 else None
        return guess if guess in allowed else None
    return None


def raw_host_of(url: httpx.URL) -> str:
    """The host in the form that goes on the wire.

    `url.host` is the decoded, unicode form, and handing that to `getaddrinfo` resolves it
    through IDNA2003 while the `Host` header and SNI carry httpx's IDNA2008 encoding:
    `faß.de` would be *resolved* as `fass.de` and *addressed* as `xn--fa-hia.de`, which is
    a validated destination that is not the one connected to. One spelling everywhere.
    """
    return url.raw_host.decode("ascii")


def parse_source(url: str) -> httpx.URL:
    """The URL a caller (or a `Location` header) supplied, or a typed refusal."""
    try:
        target = httpx.URL(url)
    except Exception:
        raise refused("malformed-url") from None
    if target.scheme not in ("http", "https"):
        # `file:`, `gopher:`, `data:` and `javascript:` all arrive here, from a caller or
        # from a `Location` header.
        raise refused("unsupported-scheme")
    if not target.raw_host:
        raise refused("no-host")
    if target.userinfo:
        # Credentials in the URL would be sent to the pinned address and logged by
        # anything that echoes the source; they are never a legitimate video source.
        raise refused("credentials-in-url", host=raw_host_of(target))
    return target


async def resolve_all(host: str) -> list[str]:
    """Every A/AAAA answer for the host. The default resolver; tests inject their own."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def decode_data_url(url: str, limits: PilotSettings = DEFAULTS, allowed=ALLOWED_MIME) -> Fetched:
    """Strict, bounded base64.

    The bound is applied to the *encoded* text before anything is decoded, so a
    gigabyte `data:` URL never becomes three quarters of a gigabyte of process
    memory, and `validate=True` refuses the padding and whitespace tricks that make
    two different texts decode to the same bytes.
    """
    # M4: the payload is sliced out of the text once and decoded from it directly; the
    # slice-then-partition and `b64decode`'s ASCII re-encoding were two more full copies of
    # up to 85 MiB of text (M4 evidence). `a2b_base64(strict_mode=True)` is exactly what
    # `b64decode(validate=True)` calls, and a non-ASCII text is a ValueError in both.
    comma = url.find(",", len(DATA_PREFIX))
    if comma < 0:
        raise refused("bad-data-url")
    header, payload = url[len(DATA_PREFIX):comma], url[comma + 1:]
    parameters = [part.strip().lower() for part in header.split(";")]
    if "base64" not in parameters[1:]:
        raise refused("bad-data-url")            # only base64 is a bounded encoding
    mime = parameters[0]
    if mime not in allowed:
        raise refused("unsupported-type", exc=errors.UnsupportedMedia)
    cap = limits.max_media_bytes
    if len(payload) > (cap + 2) // 3 * 4:
        raise refused("too-large", exc=errors.RequestTooLarge)
    try:
        data = binascii.a2b_base64(payload, strict_mode=True)
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
                 monotonic=time.monotonic, log: logging.Logger = LOG,
                 allowed_mime=ALLOWED_MIME) -> None:
        self.limits = limits
        self.resolve = resolve or resolve_all
        self.transport = transport
        self.monotonic = monotonic
        self.log = log
        # Configurable like the legacy path's ALLOWED_VIDEO_MIME, without this module
        # reading the environment: G passes the setting in.
        self.allowed_mime = frozenset(allowed_mime)

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

    async def fetch(self, url: str, *, early=None) -> Fetched:
        """Materialize an http(s) source once. Raises a typed `DomainError`.

        `early(head)` (M4), when given, is shown the body received so far once it reaches
        `EARLY_LOOK_BYTES` and each time it has doubled since; it refuses by raising, and
        the rest of the body is then never read. A true answer says the head has settled
        everything it can, and it is not shown again."""
        try:
            # The backstop on the real clock: the injected `monotonic` bounds the phases
            # this module can see, and this bounds the ones it cannot (a resolver or a
            # transport that never returns at all). It deliberately fires a moment *after*
            # the per-phase budgets, so a refusal that can name its host and reason does.
            async with asyncio.timeout(self.limits.media_fetch_timeout_s + BACKSTOP_GRACE_S):
                return await self._fetch(url, early)
        except errors.DomainError as refusal:
            self.log.warning("media fetch refused: host=%s reason=%s",
                             getattr(refusal, "host", "") or "-",
                             getattr(refusal, "reason", "unknown"))
            raise
        except (TimeoutError, httpx.TimeoutException):
            self.log.warning("media fetch refused: reason=%s", "timeout")
            raise refused("timeout") from None
        except Exception as exc:
            # Never echoed and never chained: an upstream error message is a blind-SSRF
            # oracle, and the type name alone is enough for an operator.
            self.log.warning("media fetch failed: type=%s", type(exc).__name__)
            raise refused("fetch-failed") from None

    async def _fetch(self, url: str, early=None) -> Fetched:
        limits = self.limits
        cap = limits.max_media_bytes
        expires_at = self.monotonic() + limits.media_fetch_timeout_s
        body = bytearray()
        secure = False
        async with self.client() as client:
            for hop in range(limits.media_fetch_max_redirects + 1):
                target = parse_source(url)
                host = raw_host_of(target)
                if hop and secure and target.scheme != "https":
                    # A redirect must not downgrade: the signed query string of the
                    # `Location` would then travel in plaintext. Once a hop has been
                    # confidential the rest of the chain must stay so - `http -> https ->
                    # http` used to pass, because only hop 0 decided.
                    raise refused("insecure-redirect", host=host)
                secure = secure or target.scheme == "https"
                remaining = expires_at - self.monotonic()
                if remaining <= 0:
                    raise refused("timeout", host=host)
                address = await self._pin(host, remaining)
                # Resolution is part of the budget, not before it: the request that
                # follows must carry what is left *after* the name was looked up, or a
                # slow resolver hands the read phase a budget that was already spent.
                remaining = expires_at - self.monotonic()
                if remaining <= 0:
                    raise refused("timeout", host=host)
                # No Set-Cookie from one hop reaches the next, and nothing from a previous
                # fetch reaches this one: a cookie is ambient authority we never want.
                client.cookies.clear()
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
                        # asked for, so pinning does not weaken verification. The timeout
                        # is what is *left* of the aggregate budget, so one stalled read
                        # cannot double the total (review B3).
                        extensions={"sni_hostname": host, "timeout": self._budget(remaining)}),
                    stream=True)
                try:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location:
                            raise refused("bad-redirect", host=host)
                        url = str(target.join(location))
                        continue              # re-parsed, re-resolved and re-pinned above
                    if response.status_code != 200:
                        raise refused("http-status", host=host)
                    coding = response.headers.get("content-encoding", "").strip().lower()
                    if coding not in ("", "identity"):
                        raise refused("encoded-body", host=host)
                    mime = video_mime(response.headers.get("content-type"), target,
                                      self.allowed_mime)
                    if mime is None:
                        raise refused("unsupported-type", host=host,
                                      exc=errors.UnsupportedMedia)
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > cap:
                        raise refused("too-large", host=host, exc=errors.RequestTooLarge)
                    # M4: the digest is taken as the bytes arrive, so it is finished when the
                    # last one lands, and the body is copied once at the end. Measured on the
                    # 64 MiB cap: one full copy and one full hash less on the event loop, and
                    # a high-water of about 2x the body instead of 3x (M4 evidence).
                    hasher = hashlib.sha256()
                    look = EARLY_LOOK_BYTES
                    async for chunk in response.aiter_raw():
                        body += chunk
                        if len(body) > cap:
                            # Aborted mid-body: the rest is never read, so a lying
                            # Content-Length buys an attacker nothing.
                            raise refused("too-large", host=host, exc=errors.RequestTooLarge)
                        hasher.update(chunk)
                        if early is not None and len(body) >= look:
                            look = 2 * len(body)
                            if early(body):
                                early = None      # settled: no later look can change it
                        if self.monotonic() >= expires_at:
                            raise refused("timeout", host=host)
                    if not body:
                        raise refused("empty-body", host=host)
                    return Fetched(mime=mime, data=bytes(body),
                                   digest="sha256:" + hasher.hexdigest(), host=host)
                finally:
                    await response.aclose()
            raise refused("too-many-redirects")

    def _budget(self, remaining: float) -> dict[str, float]:
        """What is left of the aggregate limit, as httpx's per-phase timeouts."""
        connect = min(self.limits.media_fetch_connect_timeout_s, remaining)
        return {"connect": connect, "read": remaining, "write": remaining, "pool": remaining}

    async def _pin(self, host: str, remaining: float) -> str:
        """Resolve once, within the remaining budget, and validate every answer.

        One private answer among many is a rebinding attempt, not a fallback; and the
        address returned here is the one the connection uses, so the name is never
        resolved a second time. Resolution is inside the budget because a name server
        that never answers is as effective a way to hold a worker as a body that never
        ends (review B3).
        """
        try:
            # The caller guarantees `remaining > 0` (the hop-start check), so the floor
            # only ever applies when that check is missing: a non-positive `wait_for`
            # timeout would otherwise refuse by accident and make the real guard invisible.
            addresses = await asyncio.wait_for(self.resolve(host), max(remaining, 0.001))
        except TimeoutError:
            raise refused("timeout", host=host) from None
        except Exception:
            raise refused("dns", host=host) from None
        if not addresses:
            raise refused("dns", host=host)
        if not all(address_allowed(address) for address in addresses):
            raise refused("blocked-address", host=host)
        return addresses[0]
