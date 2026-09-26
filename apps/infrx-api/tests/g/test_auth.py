#!/usr/bin/env python3
"""DUR-RLS: tenant identity, and what happens when there is none.

The resolver is driven directly here (a request is just its headers, as F1's `Auth`
sees it) so the cache and refusal behaviour is tested without an HTTP round trip;
`test_intake`/`test_validate` cover the same refusals through the route.
"""
import asyncio
import hashlib
import inspect

import httpx
import pytest

from infrx.auth import keys
from infrx.auth.context import API_KEY_ROLE, AuthResolver
from infrx.config import RuntimeMisconfigured
from infrx.contracts import errors
from infrx.contracts.fakes import FACTORIES
from infrx.contracts.records import Role

from . import support

REVOKED = {**support.ROW, "revoked_at": "2026-01-01T00:00:00Z"}


class Req:
    def __init__(self, token=support.TOKEN):
        self.headers = {"authorization": f"Bearer {token}"} if token else {}


def resolver(config=None, *, rows=(support.ROW,), down=False, seen=None, **kw):
    rt = support.runtime(config, sb=support.supabase(rows=rows, down=down, seen=seen))
    return AuthResolver(rt, **kw), rt


def context(resolved, request=None):
    return asyncio.run(resolved.context(request or Req()))


def test_dur_rls__a_known_key_becomes_an_auth_context():
    resolved, _rt = resolver()
    auth = context(resolved)
    assert (auth.org_id, auth.key_id, auth.principal) == (support.ORG, support.KEY, support.KEY)
    assert auth.role is API_KEY_ROLE is Role.service
    assert auth.is_operator is False and auth.legacy_key is False
    # r1 R24: identity only. D1 supplies the real version; until then it is 0, and
    # authorization is rechecked by `admit` either way.
    assert auth.entitlement_version == 0


def test_dur_rls__the_entitlement_version_source_is_injectable():
    resolved, _rt = resolver(entitlement_version=lambda org_id: 7)
    assert context(resolved).entitlement_version == 7


FAILURES = (("unknown key", {"rows": ()}, errors.InvalidApiKey, "invalid_api_key"),
            ("revoked key", {"rows": (REVOKED,)}, errors.InvalidApiKey, "invalid_api_key"),
            ("identity source down", {"down": True}, errors.DependencyUnavailable,
             "dependency_unavailable"))


@pytest.mark.parametrize("name,kw,error,code", FAILURES, ids=[f[0] for f in FAILURES])
def test_dur_rls__an_unusable_key_is_refused_with_a_stable_code(name, kw, error, code):
    resolved, _rt = resolver(**kw)
    with pytest.raises(error) as raised:
        context(resolved)
    assert raised.value.code == code


def test_dur_rls__no_bearer_token_is_401():
    resolved, _rt = resolver()
    with pytest.raises(errors.InvalidApiKey):
        context(resolved, Req(token=""))


def test_dur_rls__a_configuration_with_no_identity_source_accepts_nothing():
    """F1 answers "allowed, no row" when neither the legacy key nor Supabase is
    configured - the `O-FAILOPEN` hazard. The new ingress has no tenant then, so it
    refuses, in every mode rather than only in `pilot`."""
    resolved, _rt = resolver(support.settings("dev", supabase_url="", supabase_key=""))
    with pytest.raises(errors.InvalidApiKey):
        context(resolved)


def test_dur_rls__an_unconfigured_gateway_accepts_no_request():
    """The same rule through the route, which is where `O-FAILOPEN` would bite: an
    install run that lost its parameters serves 401, not 200."""
    from fastapi.testclient import TestClient

    calls, accept = support.recorder()
    app, _ = support.cutover_app(support.settings("dev", supabase_url="", supabase_key=""),
                                 ingress_deps=support.deps(accept=accept))
    response = TestClient(app).post(support.CHAT_PATH, headers=support.AUTH, json=support.BODY)
    assert response.status_code == 401, response.text
    assert support.error_of(response)["code"] == "invalid_api_key"
    assert calls == []


def test_dur_rls__the_shared_legacy_key_is_not_an_identity():
    """It authenticates in F1 and carries no organization, so it cannot be metered,
    entitled or suspended. `dev` still starts with one; the request is still 401."""
    config = support.settings("dev", legacy_key="legacy-key", supabase_url="", supabase_key="")
    resolved, _rt = resolver(config)
    with pytest.raises(errors.InvalidApiKey):
        context(resolved, Req(token="legacy-key"))


PILOT_REFUSALS = (("a shared legacy key", {"legacy_key": "legacy-key"}, "GATEWAY_API_KEY"),
                  ("no identity source", {"supabase_url": " "}, "SUPABASE_URL"),
                  ("no service role key", {"supabase_key": ""}, "SUPABASE_SERVICE_ROLE_KEY"))


@pytest.mark.parametrize("name,kw,setting", PILOT_REFUSALS, ids=[r[0] for r in PILOT_REFUSALS])
def test_dur_rls__pilot_refuses_to_build_a_resolver_it_cannot_trust(name, kw, setting):
    """r1 R51, belt and braces with `config.validate_runtime`: the mode is set on the
    runtime *after* validation here, exactly as a bypassed or forgotten startup hook
    would leave it, and the resolver still refuses - naming settings, never values."""
    rt = support.runtime(support.settings("dev", **kw))
    rt.mode = "pilot"
    with pytest.raises(RuntimeMisconfigured) as raised:
        AuthResolver(rt)
    assert setting in str(raised.value)
    assert (kw.get("legacy_key") or "x") not in str(raised.value)


def test_dur_rls__the_bounded_key_and_miss_caches_are_preserved():
    """F1's caps still hold: the hash is caller-supplied, so an unbounded cache is a
    memory-exhaustion vector, and a flood of misses must not evict real keys."""
    config = support.settings(key_cache_max=2, miss_cache_max=2)
    resolved, rt = resolver(config)
    for n in range(5):
        context(resolved, Req(f"real-{n}"))
    assert len(rt.auth.keys) == 2, rt.auth.keys

    resolved, rt = resolver(config, rows=())
    for n in range(5):
        with pytest.raises(errors.InvalidApiKey):
            context(resolved, Req(f"miss-{n}"))
    assert len(rt.auth.misses) == 2 and rt.auth.keys == {}


def test_dur_rls__a_flood_of_misses_cannot_evict_a_real_key():
    rows = [support.ROW]
    rt = support.runtime(support.settings(key_cache_max=2, miss_cache_max=2),
                         sb=httpx.AsyncClient(
                             base_url="https://fake.supabase.co/rest/v1",
                             transport=httpx.MockTransport(
                                 lambda request: httpx.Response(200, json=list(rows)))))
    resolved = AuthResolver(rt)
    context(resolved)                                   # the real key is cached
    rows.clear()                                        # everything else is a miss
    for n in range(5):
        with pytest.raises(errors.InvalidApiKey):
            context(resolved, Req(f"miss-{n}"))
    assert hashlib.sha256(support.TOKEN.encode()).hexdigest() in rt.auth.keys


def test_dur_rls__a_cached_key_survives_an_unreachable_identity_source():
    """F1's fail-open-for-known-keys behaviour: a cached key keeps working while
    Supabase is down, and only an unknown key gets the retryable 503."""
    seen = []
    resolved, rt = resolver(seen=seen)
    context(resolved)
    assert len(seen) == 1
    rt.sb = support.supabase(down=True)
    assert context(resolved).org_id == support.ORG      # served from the cache
    with pytest.raises(errors.DependencyUnavailable):
        context(resolved, Req("never-seen"))


def test_dur_rls__the_legacy_key_comparison_is_still_constant_time():
    """A timing-safe comparison is not observable from behaviour, so the invariant is
    stated where it lives: `Auth.authenticate` compares with `hmac.compare_digest`
    and never with `==`."""
    source = inspect.getsource(keys.Auth.authenticate)
    assert "hmac.compare_digest(token.encode(), s.legacy_key.encode())" in source


def test_dur_rls__admission_rechecks_revocation_on_the_identity_we_pass():
    """The cache is identity, never authorization: a key revoked after the ingress
    cached it is refused by `admit` inside its own transaction (r1 R10)."""
    from fastapi.testclient import TestClient

    harness = FACTORIES["jobstore"]()
    harness.extra["grant"](support.ORG, "1.00")
    calls, accept = support.recorder()
    app, mounted = support.cutover_app(ingress_deps=support.deps(accept=accept))
    tc = TestClient(app)
    # The R62 pin: the v1 fake prices it, so admission reaches the key recheck (review H3).
    body = {"model": support.MODEL_REVISION, "messages": [{"role": "user", "content": "hi"}]}
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=body).status_code == 202
    # the ingress still holds a valid cache entry ...
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=body).status_code == 202
    harness.extra["revoke_key"](support.KEY)
    _auth, request, idem = calls[1]
    harness.clock.advance((request.created_at - harness.clock.now()).total_seconds())
    with pytest.raises(errors.InvalidApiKey):
        asyncio.run(harness.port.admit(request, idem))


# --- ported from the F1 legacy cases, which retire with the shim (review r1 item 6) --
def test_dur_rls__a_cached_key_expires_and_is_looked_up_again():
    """The TTL is what makes a revocation take effect without a restart. Time moves
    because the injected clock says so, not because the test waits."""
    now, seen = [1_790_000_000.0], []
    rt = support.runtime(support.settings(), sb=support.supabase(seen=seen),
                         clock=lambda: now[0])
    resolved = AuthResolver(rt)
    context(resolved)
    assert len(seen) == 1
    now[0] += rt.settings.key_ttl - 1                 # inside the TTL: served cached
    context(resolved)
    assert len(seen) == 1
    now[0] += 2                                      # past it: looked up again
    context(resolved)
    assert len(seen) == 2


def test_dur_rls__a_miss_expires_on_its_own_shorter_ttl():
    now, seen = [1_790_000_000.0], []
    rt = support.runtime(support.settings(), sb=support.supabase(rows=(), seen=seen),
                         clock=lambda: now[0])
    resolved = AuthResolver(rt)
    for _ in range(2):
        with pytest.raises(errors.InvalidApiKey):
            context(resolved)
    assert len(seen) == 1, seen                      # the miss was cached
    now[0] += rt.settings.miss_ttl + 1
    with pytest.raises(errors.InvalidApiKey):
        context(resolved)
    assert len(seen) == 2


def test_dur_rls__a_revocation_takes_effect_when_the_cache_expires():
    now = [1_790_000_000.0]
    rows = [dict(support.ROW)]
    rt = support.runtime(support.settings(),
                         sb=httpx.AsyncClient(
                             base_url="https://fake.supabase.co/rest/v1",
                             transport=httpx.MockTransport(
                                 lambda request: httpx.Response(200, json=list(rows)))),
                         clock=lambda: now[0])
    resolved = AuthResolver(rt)
    assert context(resolved).key_id == support.KEY
    rows[0]["revoked_at"] = "2026-01-01T00:00:00Z"
    assert context(resolved).key_id == support.KEY            # still cached, by design
    now[0] += rt.settings.key_ttl + 1
    with pytest.raises(errors.InvalidApiKey):
        context(resolved)


ORGLESS = (("no org_id", {k: v for k, v in support.ROW.items() if k != "org_id"}),
           ("a null org_id", {**support.ROW, "org_id": None}),
           ("no key id", {k: v for k, v in support.ROW.items() if k != "id"}))


@pytest.mark.parametrize("name,row", ORGLESS, ids=[r[0] for r in ORGLESS])
def test_dur_rls__an_identity_row_without_a_tenant_is_not_an_identity(name, row):
    resolved, _rt = resolver(rows=(row,))
    with pytest.raises(errors.InvalidApiKey):
        context(resolved)


def test_dur_rls__a_malformed_identity_row_fails_closed_without_a_trace():
    """A non-UUID or uppercase organization is our bug, not the caller's: it is a
    typed internal error, and no pydantic validation text reaches anyone."""
    from infrx.contracts import errors as contract_errors

    resolved, _rt = resolver(rows=({**support.ROW, "org_id": "NOT-A-UUID"},))
    with pytest.raises(contract_errors.InternalError) as raised:
        context(resolved)
    assert raised.value.code == "internal_error"
    assert "NOT-A-UUID" not in str(raised.value)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and not hasattr(fn, "pytestmark"):
            fn()
            print("ok", name)
