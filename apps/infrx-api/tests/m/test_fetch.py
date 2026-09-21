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

    for address in support.INTERNAL:
        assert not address_allowed(address), address
    for address in (support.PUBLIC, support.PUBLIC_V6, support.PUBLIC_MAPPED,
                    "1.1.1.1", "8.8.8.8", "2620:fe::fe"):
        assert address_allowed(address), address


def test_a_v4_mapped_public_address_is_judged_as_the_v4_it_names():
    """The other half of B1: the mapped unwrap is a decision, not decoration. Without it
    every `::ffff:` form is refused as reserved space, so this is the case that dies."""
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
    transport = support.Transport(support.response(302, location="http://internal.test/v.mp4"),
                                 support.response(body=MP4))
    resolve = support.resolver([support.PUBLIC], [support.METADATA])
    error = refusal(fetcher(resolve=resolve, transport=transport))
    assert error.reason == "blocked-address"
    assert resolve.calls == ["example.com", "internal.test"]
    assert len(transport.requests) == 1


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
    # and the request asked for no coding in the first place
    assert support.Transport().requests == []


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
            ("data:;base64," + encoded, "unsupported-type"),
            ("data:video/mp4;base64,", "unsupported-source")):
        with pytest.raises(errors.DomainError) as caught:
            fetch.decode_data_url(url, SMALL)
        expected = "empty-body" if reason == "unsupported-source" else reason
        assert caught.value.reason == expected, url
