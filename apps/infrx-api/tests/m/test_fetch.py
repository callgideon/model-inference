#!/usr/bin/env python3
"""MEDIA-SEC: the new fetch path, against the fixtures that broke the old one.

    uv run --frozen pytest -q tests/m/test_fetch.py

Every case here is an attack: a name that resolves differently the second time, a
redirect into the metadata service, a body that is longer than it says, one that is
compressed, one that never ends, a URL carrying credentials. None of them touches the
network, a socket or the wall clock.
"""
from __future__ import annotations

import asyncio

import httpx as httpx_module
import pytest
from infrx.contracts import errors
from infrx.contracts.limits import DEFAULTS
from infrx.media import fetch

from . import support

MP4 = b"\x00\x00\x00 ftypmp42" + b"\x00" * 16
URL = "https://example.com/clips/v.mp4?token=secretvalue"
SMALL = DEFAULTS.replace(max_media_bytes=64)


def fetcher(*, resolve=None, transport=None, limits=DEFAULTS, monotonic=None, log=None):
    return fetch.MediaFetcher(limits,
                              resolve=resolve or support.resolver([support.PUBLIC]),
                              transport=transport.transport if transport else None,
                              monotonic=monotonic or support.Ticker(),
                              log=log or support.Records())


def refusal(fetched, url=URL):
    """Run a fetch that must be refused and return the typed error."""
    with pytest.raises(errors.DomainError) as caught:
        asyncio.run(fetched.fetch(url))
    return caught.value


# --- pinning ---------------------------------------------------------------------
def test_the_connection_is_pinned_to_the_validated_address():
    """MEDIA-SEC: the request goes to the address that was validated, with the original
    host in the `Host` header and in the TLS SNI, so the certificate is still checked
    against the name the caller asked for. Resolve-then-let-httpx-resolve-again is the
    rebinding window the legacy path documents."""
    transport = support.Transport(support.response(body=MP4))
    got = asyncio.run(fetcher(transport=transport).fetch(URL))
    assert transport.pinned == [support.PUBLIC]
    assert transport.hosts == ["example.com"]
    assert [r.extensions.get("sni_hostname") for r in transport.requests] == ["example.com"]
    assert got.data == MP4 and got.mime == "video/mp4"
    assert got.digest == fetch.digest_of(MP4) and got.host == "example.com"
    # the path and query reach the origin unchanged, only the host is replaced
    assert transport.requests[0].url.raw_path == b"/clips/v.mp4?token=secretvalue"


def test_an_ipv6_answer_is_pinned_as_a_bracketed_literal():
    """MEDIA-SEC: the IPv6 form of the same pin, which a naive string join breaks."""
    transport = support.Transport(support.response(body=MP4))
    resolve = support.resolver([support.PUBLIC_V6])
    asyncio.run(fetcher(resolve=resolve, transport=transport).fetch(URL))
    assert transport.pinned == [support.PUBLIC_V6]
    assert str(transport.requests[0].url).startswith(f"https://[{support.PUBLIC_V6}]/")
    assert transport.hosts == ["example.com"]


def test_a_rebinding_resolver_cannot_reach_an_internal_address():
    """MEDIA-SEC: the resolver answers public once and internal next. The first hop is
    pinned to the validated address, and the second answer is validated too, so the
    redirect that would have used it is refused before any connection."""
    transport = support.Transport(support.response(302, location=URL),
                                 support.response(body=MP4))
    resolve = support.resolver([support.PUBLIC], [support.METADATA])
    error = refusal(fetcher(resolve=resolve, transport=transport))
    assert error.reason == "blocked-address" and isinstance(error, errors.MediaFetchFailed)
    assert len(transport.requests) == 1, "the second hop connected anyway"
    assert resolve.calls == ["example.com", "example.com"]


def test_one_private_answer_among_many_is_refused():
    """MEDIA-SEC: a name resolving to a public and an internal address is a rebinding
    attempt, not a fallback: no answer of that name is used."""
    resolve = support.resolver([support.PUBLIC, support.METADATA])
    transport = support.Transport(support.response(body=MP4))
    error = refusal(fetcher(resolve=resolve, transport=transport))
    assert error.reason == "blocked-address"
    assert transport.requests == []


@pytest.mark.parametrize("address", support.INTERNAL)
def test_no_internal_address_form_is_reachable(address):
    """MEDIA-SEC: the whole policy in one case - loopback, private, link-local and the
    metadata address, CGNAT, unspecified, the v4-mapped, NAT64, 6to4 and Teredo spellings
    of the same, a scoped literal and text that is not an address at all."""
    transport = support.Transport(support.response(body=MP4))
    error = refusal(fetcher(resolve=support.resolver([address]), transport=transport))
    assert error.reason == "blocked-address", address
    assert transport.requests == []


def test_the_address_policy_does_not_depend_on_the_interpreters_tables():
    """MEDIA-SEC (review B1): `ipaddress` disagrees with itself across builds. Deprecated
    site-local `fec0::/10` reports `is_global` True, and upstream CPython 3.12.0-3.12.3
    does not carry the 6to4/NAT64 ranges this host's build has, so the policy states them
    itself instead of inheriting whatever the interpreter happens to know."""
    from infrx.media.video import address_allowed

    for address in (*support.INTERNAL, "::ffff:192.88.99.1", "::ffff:192.0.0.9",
                    "::ffff:198.18.0.1"):
        assert not address_allowed(address), address
    for address in (support.PUBLIC, support.PUBLIC_V6, support.PUBLIC_MAPPED,
                    "1.1.1.1", "8.8.8.8", "2620:fe::fe"):
        assert address_allowed(address), address


def test_a_v4_mapped_public_address_is_judged_as_the_v4_it_names():
    """The other half of B1: the mapped unwrap is a decision, not decoration. Without it
    mapped addresses must retain the embedded IPv4 policy, including explicit deny ranges.
    Newer interpreter builds already classify mapped public space as public; the
    denied mapped forms below still distinguish correct unwrapping on those builds."""
    transport = support.Transport(support.response(body=MP4))
    resolve = support.resolver([support.PUBLIC_MAPPED])
    got = asyncio.run(fetcher(resolve=resolve, transport=transport).fetch(URL))
    assert got.data == MP4
    assert transport.pinned == [support.PUBLIC_MAPPED] and transport.hosts == ["example.com"]


def test_a_dns_failure_or_an_empty_answer_is_a_refusal_not_a_crash():
    for resolve in (support.resolver(OSError("no such host")), support.resolver([])):
        error = refusal(fetcher(resolve=resolve, transport=support.Transport()))
        assert error.reason == "dns"


# --- redirects -------------------------------------------------------------------
def test_every_redirect_hop_is_validated_and_repinned():
    """MEDIA-SEC: a public first hop redirecting to an internal name. The hop is parsed,
    resolved and validated again; only the name changed, which is the whole attack."""
    # https on both hops, so the *address* is what refuses this one and not the scheme
    transport = support.Transport(support.response(302, location="https://internal.test/v.mp4"),
                                 support.response(body=MP4))
    resolve = support.resolver([support.PUBLIC], [support.METADATA])
    error = refusal(fetcher(resolve=resolve, transport=transport))
    assert error.reason == "blocked-address"
    assert resolve.calls == ["example.com", "internal.test"]
    assert len(transport.requests) == 1


def test_a_chain_that_became_confidential_stays_confidential():
    """Review r2: `secure` was decided at hop 0, so `http -> https -> http` was fetched -
    the https hop's `Location`, with its signed query string, went out in plaintext."""
    transport = support.Transport(
        support.response(302, location="https://example.com/signed/v.mp4?sig=deadbeefsig"),
        support.response(302, location="http://example.com/plain/v.mp4"),
        support.response(body=MP4))
    error = refusal(fetcher(transport=transport), "http://example.com/start/v.mp4")
    assert error.reason == "insecure-redirect"
    assert len(transport.requests) == 2, "the plaintext hop was fetched"


def test_a_redirect_may_not_downgrade_to_plaintext():
    """Review nonblocking: the `Location` of an https fetch carries the caller's query
    string, so a hop to http would put a signed URL on the wire in the clear. A fetch that
    *started* as http is unchanged - it was never confidential."""
    transport = support.Transport(support.response(302, location="http://example.com/v.mp4"),
                                  support.response(body=MP4))
    error = refusal(fetcher(transport=transport))
    assert error.reason == "insecure-redirect"
    assert len(transport.requests) == 1
    plain = support.Transport(support.response(302, location="http://example.com/v.mp4"),
                              support.response(body=MP4))
    assert asyncio.run(fetcher(transport=plain).fetch("http://example.com/v.mp4")).data == MP4


def test_a_relative_redirect_is_resolved_against_the_hop_it_came_from():
    transport = support.Transport(support.response(302, location="/other/v.mp4"),
                                  support.response(body=MP4))
    asyncio.run(fetcher(transport=transport).fetch(URL))
    assert transport.requests[1].url.raw_path == b"/other/v.mp4"
    assert transport.hosts == ["example.com", "example.com"]


def test_a_redirect_chain_over_the_budget_is_refused():
    """MEDIA-SEC: MEDIA_FETCH_MAX_REDIRECTS hops, then no more - a chain is a fetch that
    never ends as much as a body that never ends."""
    transport = support.Transport(support.response(302, location=URL))
    error = refusal(fetcher(transport=transport))
    assert error.reason == "too-many-redirects"
    assert len(transport.requests) == DEFAULTS.media_fetch_max_redirects + 1 == 4


def test_a_redirect_without_a_location_is_refused():
    """A 3xx is a redirect to httpx whether or not it says where to; without a
    destination it is refused rather than read as content."""
    transport = support.Transport(support.response(302, mime=None, body=b"<html>"))
    error = refusal(fetcher(transport=transport))
    assert error.reason == "bad-redirect"


def test_a_redirect_to_another_scheme_or_to_credentials_is_refused():
    for location in ("file:///etc/passwd", "gopher://example.com/", "https://u:pw@example.com/v"):
        transport = support.Transport(support.response(302, location=location),
                                      support.response(body=MP4))
        error = refusal(fetcher(transport=transport))
        assert error.reason in ("unsupported-scheme", "credentials-in-url"), location
        assert len(transport.requests) == 1


# --- the URL itself --------------------------------------------------------------
def test_credentials_in_the_url_are_refused():
    """MEDIA-SEC: a URL carrying credentials would send them to the pinned address and
    leak them into anything that echoes the source."""
    error = refusal(fetcher(transport=support.Transport()),
                    "https://user:hunter2@example.com/v.mp4")
    assert error.reason == "credentials-in-url"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://example.com/v.mp4",
                                 "ftp://example.com/v.mp4", "https:///v.mp4", "not a url"])
def test_only_http_urls_with_a_host_are_fetched(url):
    error = refusal(fetcher(transport=support.Transport()), url)
    assert error.reason in ("unsupported-scheme", "no-host")


# --- bytes -----------------------------------------------------------------------
def test_an_oversize_body_is_aborted_mid_stream():
    """MEDIA-SEC: the cap is enforced on the stream. The rest of the body is never read,
    so a body that lies about its length gains nothing by it."""
    stream = support.Chunks([b"x" * 40] * 8)
    transport = support.Transport(support.response(stream=stream,
                                                  headers={"content-length": "10"}))
    error = refusal(fetcher(transport=transport, limits=SMALL))
    assert error.reason == "too-large" and isinstance(error, errors.RequestTooLarge)
    assert errors.http_status(error.code) == 413
    assert stream.read <= SMALL.max_media_bytes + 40, "the whole body was read anyway"


def test_a_declared_oversize_body_is_refused_before_it_is_read():
    stream = support.Chunks([b"x" * 8])
    transport = support.Transport(support.response(stream=stream,
                                                  headers={"content-length": "999999"}))
    error = refusal(fetcher(transport=transport, limits=SMALL))
    assert error.reason == "too-large"
    assert stream.read == 0


def test_a_compressed_body_is_refused_outright():
    """MEDIA-SEC: a gzip bomb decodes far past a cap that counted the wire bytes, and a
    cap on the decoded bytes has already spent the memory. Video is compressed already,
    so the whole content coding is refused before a byte is read."""
    for coding in ("gzip", "br", "deflate", "gzip, br"):
        stream = support.Chunks([b"x" * 8])
        transport = support.Transport(support.response(
            stream=stream, headers={"content-encoding": coding}))
        error = refusal(fetcher(transport=transport, limits=SMALL))
        assert error.reason == "encoded-body", coding
        assert stream.read == 0
    # and the request asked for no coding in the first place: on the request the transport
    # actually received, not on a fresh one (review B4/R10)
    asked = support.Transport(support.response(body=MP4))
    asyncio.run(fetcher(transport=asked).fetch(URL))
    assert [r.headers["accept-encoding"] for r in asked.requests] == ["identity"]


def test_a_slow_body_stops_at_the_aggregate_deadline():
    """MEDIA-SEC: a slow-loris body arrives inside every per-read timeout and still holds
    a worker for ever. The aggregate limit is checked between chunks, on the injected
    clock, so this case costs microseconds."""
    clock = support.Ticker(step=DEFAULTS.media_fetch_timeout_s / 3)
    stream = support.Chunks([b"x" * 4] * 100, on_chunk=None)
    transport = support.Transport(support.response(stream=stream))
    error = refusal(fetcher(transport=transport, monotonic=clock))
    assert error.reason == "timeout"
    assert stream.read < 400, "the whole slow body was read"


def test_a_slow_redirect_chain_stops_at_the_aggregate_deadline():
    """MEDIA-SEC (review B4/R13): the budget is checked at the *start* of every hop, so a
    chain of slow redirects inside the budget stops resolving and connecting the moment it
    is gone, rather than running to the redirect limit."""
    clock = support.Ticker(step=DEFAULTS.media_fetch_timeout_s * 0.4)
    transport = support.Transport(support.response(302, location=URL))
    resolve = support.resolver([support.PUBLIC])
    error = refusal(fetcher(transport=transport, resolve=resolve, monotonic=clock))
    assert error.reason == "timeout"
    assert 1 <= len(transport.requests) < DEFAULTS.media_fetch_max_redirects + 1, \
        "the chain ran to the redirect limit instead of stopping at the deadline"
    assert len(resolve.calls) == len(transport.requests), \
        "a hop was resolved after the budget was gone"


def test_the_budget_is_recomputed_after_the_name_is_resolved():
    """MEDIA-SEC (review r2): resolution spends the budget too. A resolver that takes the
    whole of it used to be followed by a request carrying a budget that was already gone,
    because `remaining` was computed before the lookup and never again."""
    clock = support.Ticker()
    transport = support.Transport(support.response(body=MP4))

    async def slow(host):
        clock.now += DEFAULTS.media_fetch_timeout_s * 2      # the lookup took that long
        return [support.PUBLIC]

    error = refusal(fetcher(transport=transport, resolve=slow, monotonic=clock))
    assert error.reason == "timeout"
    assert transport.requests == [], "a request was sent on a budget that was already spent"


def test_each_request_carries_what_is_left_of_the_budget():
    """MEDIA-SEC (review B3): without this the per-read timeout stays the whole budget on
    every hop, so one stalled read after a passing check doubles the total."""
    clock = support.Ticker(step=1.0)
    transport = support.Transport(support.response(302, location="/second/v.mp4"),
                                  support.response(body=MP4))
    asyncio.run(fetcher(transport=transport, monotonic=clock).fetch(URL))
    budgets = [request.extensions["timeout"] for request in transport.requests]
    assert len(budgets) == 2
    assert budgets[0]["read"] < DEFAULTS.media_fetch_timeout_s
    assert budgets[1]["read"] < budgets[0]["read"], "the second hop got the whole budget again"
    for budget in budgets:
        assert budget["connect"] <= DEFAULTS.media_fetch_connect_timeout_s
        assert budget["read"] == budget["write"] == budget["pool"]
    # and when less is left than the connect budget, connecting gets the remainder:
    # a 3 s connect inside a 2 s aggregate limit is not a limit
    short = DEFAULTS.replace(media_fetch_timeout_s=2.0)
    tight = support.Transport(support.response(body=MP4))
    asyncio.run(fetcher(transport=tight, limits=short, monotonic=support.Ticker()).fetch(URL))
    tightest = tight.requests[0].extensions["timeout"]
    assert tightest["connect"] == tightest["read"] == 2.0 < short.media_fetch_connect_timeout_s


def test_a_resolver_that_never_answers_is_bounded():
    """MEDIA-SEC (review B3): a name server that never replies holds a worker as
    effectively as a body that never ends. Resolution is therefore inside the hop's
    remaining budget - which is also why the refusal names the host: the real-clock
    backstop cannot, because it does not know which hop it interrupted."""
    budget = DEFAULTS.replace(media_fetch_timeout_s=0.05, media_fetch_connect_timeout_s=0.05)
    log = support.Records()

    async def never(host):
        await asyncio.get_running_loop().create_future()

    stuck = fetch.MediaFetcher(budget, resolve=never, monotonic=support.Ticker(),
                               transport=support.Transport().transport, log=log)
    with pytest.raises(errors.MediaFetchFailed) as caught:
        asyncio.run(stuck.fetch(URL))
    assert caught.value.reason == "timeout"
    assert "host=example.com reason=timeout" in log.text, log.text


def test_a_body_that_stalls_for_ever_is_bounded_by_the_backstop():
    """MEDIA-SEC (review B3): the injected clock only advances when the fetcher reads, so a
    body that stalls *between* chunks advances nothing and none of this module's own checks
    can fire. The aggregate limit therefore also holds on the real clock. The outer bound is
    the test's own: without the backstop this hangs rather than failing."""
    budget = DEFAULTS.replace(media_fetch_timeout_s=0.05)

    class Stalls(httpx_module.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 8
            await asyncio.get_running_loop().create_future()

    transport = support.Transport(support.response(stream=Stalls()))
    stalled = fetch.MediaFetcher(budget, resolve=support.resolver([support.PUBLIC]),
                                 monotonic=support.Ticker(),
                                 transport=transport.transport, log=support.Records())

    async def go():
        with pytest.raises(errors.MediaFetchFailed) as caught:
            await stalled.fetch(URL)
        return caught.value.reason

    assert asyncio.run(asyncio.wait_for(go(), 2.0)) == "timeout"


def test_a_redirect_carries_no_cookie_from_the_hop_before():
    """Review nonblocking: a `Set-Cookie` on hop 1 is ambient authority we never want to
    replay onto hop 2 - and with one client per fetch it would also survive a redirect
    back to the origin that issued it."""
    transport = support.Transport(
        support.response(302, location="https://example.com/second/v.mp4",
                         headers={"set-cookie": "session=deadbeefsession; Path=/"}),
        support.response(body=MP4))
    asyncio.run(fetcher(transport=transport).fetch(URL))
    assert len(transport.requests) == 2
    assert [r.headers.get("cookie") for r in transport.requests] == [None, None]


def test_the_resolver_and_the_wire_agree_on_one_spelling_of_the_host():
    """Review nonblocking: `url.host` is the decoded form. Resolving that goes through
    IDNA2003 (`faß.de` -> `fass.de`) while the `Host` header and SNI carry httpx's
    IDNA2008 encoding, so the address validated would not belong to the name addressed."""
    transport = support.Transport(support.response(body=MP4))
    resolve = support.resolver([support.PUBLIC])
    asyncio.run(fetcher(resolve=resolve, transport=transport).fetch("https://faß.de/v.mp4"))
    wire = "xn--fa-hia.de"
    assert resolve.calls == [wire]
    assert transport.hosts == [wire]
    assert [r.extensions["sni_hostname"] for r in transport.requests] == [wire]


def test_an_empty_body_is_not_a_video():
    transport = support.Transport(support.response(body=b""))
    assert refusal(fetcher(transport=transport)).reason == "empty-body"


# --- what arrived ----------------------------------------------------------------
@pytest.mark.parametrize("mime", ["text/html", "application/zip", "image/png", "video/x-msvideo"])
def test_a_non_video_response_is_refused(mime):
    transport = support.Transport(support.response(mime=mime, body=b"<html>not a video"))
    error = refusal(fetcher(transport=transport))
    assert error.reason == "unsupported-type" and isinstance(error, errors.UnsupportedMedia)


def test_an_undeclared_type_falls_back_to_the_extension_only():
    for mime in ("application/octet-stream", ""):
        transport = support.Transport(support.response(mime=mime, body=MP4))
        got = asyncio.run(fetcher(transport=transport).fetch("https://example.com/v.mp4"))
        assert got.mime == "video/mp4"
        transport = support.Transport(support.response(mime=mime, body=MP4))
        error = refusal(fetcher(transport=transport), "https://example.com/v.txt")
        assert error.reason == "unsupported-type"


def test_a_non_200_response_is_refused_without_its_body():
    for status in (201, 204, 400, 403, 404, 500):
        transport = support.Transport(support.response(status, body=b"secret internal page"))
        error = refusal(fetcher(transport=transport))
        assert error.reason == "http-status", status


# --- the client itself -----------------------------------------------------------
def test_the_client_follows_nothing_and_trusts_no_environment():
    """MEDIA-SEC: httpx following a redirect resolves the name again, unpinned; a proxy
    from the environment connects to the proxy instead of the validated address. Either
    one makes the pin decorative."""
    client = fetcher().client()
    assert client.follow_redirects is False
    assert client.trust_env is False
    assert client.timeout.connect == DEFAULTS.media_fetch_connect_timeout_s
    assert client.timeout.read == DEFAULTS.media_fetch_timeout_s


def test_only_one_fetch_happens_per_source():
    """MEDIA-SEC: once-only. One source is one request and one resolution; a refusal is
    final rather than retried, because a retry doubles an attacker's attempts too."""
    transport = support.Transport(support.response(body=MP4))
    resolve = support.resolver([support.PUBLIC])
    asyncio.run(fetcher(resolve=resolve, transport=transport).fetch(URL))
    assert len(transport.requests) == 1 and resolve.calls == ["example.com"]
    transport = support.Transport(support.response(500))
    refusal(fetcher(transport=transport))
    assert len(transport.requests) == 1


def test_a_refusal_tells_the_caller_and_the_log_nothing_about_the_url():
    """MEDIA-SEC: the customer gets the fixed message of an error code; the log gets a
    host and a reason class. No query string, no credentials, no upstream text - the
    blind-SSRF oracle is the whole point of this being a fixed vocabulary."""
    log = support.Records()
    error = refusal(fetcher(transport=support.Transport(support.response(503)), log=log),
                    "https://user:hunter2@example.com/v.mp4?token=secretvalue&sig=abc")
    assert error.message == errors.MESSAGES["media_fetch_failed"]
    assert error.reason in fetch.REASONS
    for leak in ("hunter2", "secretvalue", "token=", "sig=", "/v.mp4"):
        assert leak not in log.text, log.text
        assert leak not in str(error), str(error)
        assert leak not in error.message
    assert "reason=credentials-in-url" in log.text
    # an upstream exception is never echoed or chained either
    log = support.Records()

    def explode(request):
        raise RuntimeError("upstream said 10.0.0.1 refused the connection")

    import httpx
    exploding = fetch.MediaFetcher(DEFAULTS, resolve=support.resolver([support.PUBLIC]),
                                   transport=httpx.MockTransport(explode),
                                   monotonic=support.Ticker(), log=log)
    with pytest.raises(errors.MediaFetchFailed) as caught:
        asyncio.run(exploding.fetch(URL))
    assert caught.value.reason == "fetch-failed" and caught.value.__cause__ is None
    assert "10.0.0.1" not in log.text and "10.0.0.1" not in str(caught.value)
    assert "type=RuntimeError" in log.text


def test_no_logger_in_the_process_writes_a_url_an_ip_or_a_location(caplog):
    """MEDIA-SEC (review B2): reading only the injected logger proved nothing about the
    process log. httpx writes `HTTP Request: GET <pinned url>` at INFO and httpcore writes
    the same target at DEBUG, so a signed URL and the validated IP would be in the log of
    every hop. This captures **every** logger at DEBUG, over the success, redirect and
    refusal paths, and emits the exact lines httpx and httpcore would."""
    import logging

    caplog.set_level(logging.DEBUG)                      # root: every propagating logger
    signed = ("https://example.com/clips/v.mp4?X-Amz-Signature=deadbeefsig"
              "&X-Amz-Credential=AKIAEXAMPLE")
    # the module states the transports' levels itself, so those calls produce no record
    logging.getLogger("httpx").info('HTTP Request: GET https://%s/clips/v.mp4'
                                    '?X-Amz-Signature=deadbeefsig "HTTP/1.1 200 OK"',
                                    support.PUBLIC)
    logging.getLogger("httpcore.http11").debug(
        "send_request_headers.started request=<Request [b'GET'] %s>", signed)
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING

    live = fetch.MediaFetcher(DEFAULTS, resolve=support.resolver([support.PUBLIC]),
                              monotonic=support.Ticker(), log=fetch.LOG,
                              transport=support.Transport(support.response(body=MP4)).transport)
    asyncio.run(live.fetch(signed))                                    # success
    redirected = support.Transport(
        support.response(302, location="https://example.com/other/v.mp4?token=redirectsecret"),
        support.response(body=MP4))
    asyncio.run(fetch.MediaFetcher(DEFAULTS, resolve=support.resolver([support.PUBLIC]),
                                   monotonic=support.Ticker(), log=fetch.LOG,
                                   transport=redirected.transport).fetch(signed))
    with pytest.raises(errors.DomainError):                             # refusal
        asyncio.run(fetch.MediaFetcher(DEFAULTS, resolve=support.resolver([support.METADATA]),
                                       monotonic=support.Ticker(), log=fetch.LOG,
                                       transport=support.Transport().transport).fetch(signed))
    for leak in ("deadbeefsig", "AKIAEXAMPLE", "X-Amz-Signature", "redirectsecret",
                 "/clips/v.mp4", "/other/v.mp4", support.PUBLIC, support.METADATA, "?"):
        assert leak not in caplog.text, (leak, caplog.text)
    # the one thing the log does say is the host and the reason class
    assert "host=example.com reason=blocked-address" in caplog.text


def test_a_transport_timeout_is_a_timeout_refusal():
    import httpx

    def slow(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    slowly = fetch.MediaFetcher(DEFAULTS, resolve=support.resolver([support.PUBLIC]),
                                transport=httpx.MockTransport(slow),
                                monotonic=support.Ticker(), log=support.Records())
    with pytest.raises(errors.MediaFetchFailed) as caught:
        asyncio.run(slowly.fetch(URL))
    assert caught.value.reason == "timeout"


# --- data: URLs ------------------------------------------------------------------
def test_a_data_url_is_decoded_strictly_and_within_a_bound():
    import base64

    good = "data:video/mp4;base64," + base64.b64encode(MP4).decode()
    got = fetch.decode_data_url(good)
    assert got.data == MP4 and got.mime == "video/mp4" and got.digest == fetch.digest_of(MP4)

    # `validate=True`: whitespace and stray characters are refused rather than dropped,
    # so two texts cannot decode to the same bytes under one digest
    for payload in ("!!!!", "AAAA AAAA", "A\nAAA", "AAAAA"):
        with pytest.raises(errors.DomainError) as caught:
            fetch.decode_data_url("data:video/mp4;base64," + payload)
        assert caught.value.reason == "bad-base64", payload


def test_an_oversize_data_url_is_refused_before_it_is_decoded():
    """MEDIA-SEC: the bound is on the encoded text. The payload here is both far too
    large *and* invalid base64: a store that decoded first would spend the memory and
    then report the wrong reason."""
    payload = "!" * (SMALL.max_media_bytes * 4)
    with pytest.raises(errors.RequestTooLarge) as caught:
        fetch.decode_data_url("data:video/mp4;base64," + payload, SMALL)
    assert caught.value.reason == "too-large"


def test_a_data_url_must_be_base64_and_an_allowed_video_type():
    import base64

    encoded = base64.b64encode(MP4).decode()
    for url, reason in (
            ("data:video/mp4," + encoded, "bad-data-url"),          # not base64 at all
            ("data:video/mp4;charset=utf-8," + encoded, "bad-data-url"),
            ("data:video/mp4;base64" + encoded, "bad-data-url"),    # no comma
            ("data:text/html;base64," + encoded, "unsupported-type"),
            ("data:video/mpeg;base64," + encoded, "unsupported-type"),    # F2R: not served
            ("data:;base64," + encoded, "unsupported-type"),
            ("data:video/mp4;base64,", "unsupported-source")):
        with pytest.raises(errors.DomainError) as caught:
            fetch.decode_data_url(url, SMALL)
        expected = "empty-body" if reason == "unsupported-source" else reason
        assert caught.value.reason == expected, url


# --- M4: the streaming digest and one copy ------------------------------------------
def test_the_digest_covers_every_chunk_in_the_order_it_arrived():
    """M4: the digest is taken as the body streams, so it must be the digest of every byte
    in order - not of the first chunk, the last one, or a reordering."""
    chunks = [b"\x00\x00\x00 ftypmp42", b"first" * 1000, b"second" * 1000, b"end"]
    transport = support.Transport(support.response(stream=support.Chunks(chunks)))
    got = asyncio.run(fetcher(transport=transport).fetch(URL))
    assert got.data == b"".join(chunks)
    assert got.digest == fetch.digest_of(b"".join(chunks))


@pytest.mark.parametrize("looked", [False, True], ids=["no-look", "look"])
def test_a_fetched_body_is_held_at_most_about_twice(looked):
    """M4, bounded memory: the body grows once and is copied once. Measured on this path
    before M4 the high-water was ~3x the body (one growing buffer plus two full copies);
    the bound here sits between 2x and 3x so the extra copy cannot come back unnoticed.

    Review S2: `look` runs the production path's looks, with an `early` that keeps every
    head it is shown. The live buffer costs nothing to keep; a copy per look would hold up
    to the body again (meas. 2.94x), which a look that keeps nothing cannot show (2.07x)."""
    import tracemalloc

    size, chunk = 8 << 20, 64 << 10
    body = b"\x00\x00\x00 ftypmp42" + bytes(size - 16)
    chunks = [body[at:at + chunk] for at in range(0, size, chunk)]
    transport = support.Transport(support.response(stream=support.Chunks(chunks)))
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        seen = []
        got = asyncio.run(fetcher(transport=transport).fetch(
            URL, early=seen.append if looked else None))
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    assert got.data == body and len(seen) == (3 if looked else 0)     # 1, 2 and 4 MiB
    assert peak < 2.5 * size, f"peak {peak / size:.2f}x the body"


def test_a_data_url_is_decoded_from_one_copy_of_its_text():
    """M4, bounded memory: the base64 payload is sliced out once and decoded from the text.
    Before M4 the high-water was ~3.7x the decoded size (a slice, a partition and an ASCII
    re-encoding of the text, each ~1.33x); one slice plus the output is ~2.3x."""
    import base64
    import tracemalloc

    size = 8 << 20
    body = b"\x00\x00\x00 ftypmp42" + bytes(size - 16)
    url = "data:video/mp4;base64," + base64.b64encode(body).decode()
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        got = fetch.decode_data_url(url)
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    assert got.data == body and got.digest == fetch.digest_of(body)
    assert peak < 3.0 * size, f"peak {peak / size:.2f}x the decoded size"
