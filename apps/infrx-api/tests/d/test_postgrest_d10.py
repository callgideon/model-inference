#!/usr/bin/env python3
"""D10.d item 4 (DUR-RLS): the role matrix through the REAL browser path - the pinned
Supabase image and PostgREST (E3B's pinned v13.0.4), signed JWTs, no service key. Only on
the Supabase image (`INFRX_D1_IMAGE=supabase`); a skip otherwise, never a pass.

PostgREST runs in the lane's own container (`infrx-d10-postgrest`) on the lane's own docker
network (`infrx-d10-net`), reaching the lane's PostgreSQL container by name; nothing is
published on the host (the host talks to the container's bridge address). Both are removed
at the end, and a foreign container with either name is refused, never touched.

    INFRX_D_TASK=d10 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_postgrest_d10.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
import time

import pytest
from infrx.state import migrations

from . import checks_admission as ca
from . import checks_content as ck
from . import checks_credit as cc
from . import pgharness

POSTGREST = ("postgrest/postgrest@sha256:"
             "a312f4b2e48530a01fc26f5310d547d6c26d087858360e164522e415723a7732")  # v13.0.4
NAME, NETWORK, DB_ALIAS = "infrx-d10-postgrest", "infrx-d10-net", "infrx-d10-db"
LABEL = "ai.infrx.d10.checkout"
# Local, per-container test secrets (the pattern of pgharness.PASSWORD): nothing hosted.
JWT_SECRET = "infrx-d10-local-jwt-secret-not-a-real-one-00"
AUTHN_PASSWORD = "infrx-d10-authenticator-local"
DB = f"{pgharness.DATABASE}_rest"


def _docker(*args, check=True):
    return subprocess.run(("docker", *args), capture_output=True, text=True, check=check)


def _image_present() -> bool:
    return _docker("image", "inspect", POSTGREST, check=False).returncode == 0


_reason = pgharness.unavailable() or (
    None if pgharness.ON_SUPABASE else "needs INFRX_D1_IMAGE=supabase (PostgREST's own image)")
if _reason is None and not _image_present():
    _reason = f"the pinned PostgREST image is not present ({POSTGREST[:40]}…)"
pytestmark = pytest.mark.skipif(_reason is not None, reason=f"not run: {_reason}")


def _jwt(sub: str | None, role: str = "authenticated") -> str:
    def enc(doc) -> str:
        return base64.urlsafe_b64encode(json.dumps(doc, separators=(",", ":")).encode()
                                        ).rstrip(b"=").decode()
    head = enc({"alg": "HS256", "typ": "JWT"})
    body = enc({"role": role, "exp": int(time.time()) + 600, **({"sub": sub} if sub else {})})
    signature = hmac.new(JWT_SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def _ours(name: str, kind: str = "container") -> bool | None:
    probe = _docker(kind, "inspect", "-f", "{{json .Config.Labels}}" if kind == "container"
                    else "{{json .Labels}}", name, check=False)
    if probe.returncode != 0:
        return None
    labels = json.loads(probe.stdout.strip() or "null") or {}
    return labels.get(LABEL) == pgharness.checkout()


def _up() -> str:
    """The network and PostgREST, both labelled ours; the base URL on the bridge."""
    for name, kind in ((NAME, "container"), (NETWORK, "network")):
        if _ours(name, kind) is False:
            pytest.fail(f"refusing to touch {kind} {name}: not created by this checkout")
    _docker("rm", "-f", NAME, check=False)
    if _ours(NETWORK, "network") is None:
        _docker("network", "create", "--label", f"{LABEL}={pgharness.checkout()}", NETWORK)
    _docker("network", "connect", "--alias", DB_ALIAS, NETWORK, pgharness.CONTAINER,
            check=False)
    pgharness._sb("postgres", f"alter role authenticator with login password '{AUTHN_PASSWORD}'")
    # The image's init script defines `auth.uid()` from the per-claim GUC only
    # (`request.jwt.claim.sub`); hosted projects run GoTrue's migration, which also reads
    # `request.jwt.claims` - the only form PostgREST >= 10 sets. Install the hosted body
    # (the shim's, `infrx/state/supabase_shim.sql`) so the principal is the token's subject.
    pgharness._sb(DB, "create or replace function auth.uid() returns uuid language sql stable "
                      "as $f$ select coalesce(nullif(current_setting('request.jwt.claim.sub', "
                      "true), ''), nullif(nullif(current_setting('request.jwt.claims', true), "
                      "'')::jsonb ->> 'sub', ''))::uuid $f$")
    _docker("run", "-d", "--name", NAME, "--network", NETWORK,
            "--label", f"{LABEL}={pgharness.checkout()}",
            "-e", f"PGRST_DB_URI=postgres://authenticator:{AUTHN_PASSWORD}@{DB_ALIAS}:5432/{DB}",
            "-e", "PGRST_DB_SCHEMAS=public", "-e", "PGRST_DB_ANON_ROLE=anon",
            "-e", f"PGRST_JWT_SECRET={JWT_SECRET}", POSTGREST)
    address = _docker("inspect", "-f", "{{(index .NetworkSettings.Networks \"" + NETWORK
                      + "\").IPAddress}}", NAME).stdout.strip()
    import httpx
    base, last = f"http://{address}:3000", ""
    for _ in range(60):
        try:
            if httpx.get(base + "/", timeout=2).status_code < 500:
                return base
        except httpx.HTTPError as failed:
            last = type(failed).__name__
        time.sleep(0.5)
    pytest.fail(f"{NAME} never answered: {last}")


def _down() -> None:
    if _ours(NAME):
        _docker("rm", "-f", NAME, check=False)
    _docker("network", "disconnect", NETWORK, pgharness.CONTAINER, check=False)
    if _ours(NETWORK, "network"):
        _docker("network", "rm", NETWORK, check=False)


def test_the_browser_role_matrix_through_postgrest() -> None:
    import httpx
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    conn = pgharness.connect(DB)
    ca.seed_admission(conn)
    world = ca.World(conn)
    settled, _ref = ck._settled_with_result(conn, world, result_ttl_s=600.0)
    base = _up()
    try:
        me, other = cc.CONSUMER_1, cc.CONSUMER_2

        def rpc(name, body, token):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            return httpx.post(f"{base}/rpc/{name}", json=body, headers=headers, timeout=10)

        mine = rpc("consumer_jobs", {"p_limit": 10}, _jwt(me))
        assert mine.status_code == 200 and settled.request_id in \
            {row["request_id"] for row in mine.json()}, mine.text
        row = next(r for r in mine.json() if r["request_id"] == settled.request_id)
        assert row["unit"] == "CREDIT" and row["result_available"] is True, row
        assert rpc("consumer_jobs", {"p_request_id": settled.request_id}, _jwt(other)
                   ).json() == [], "a guessed id answered another individual"
        result = rpc("consumer_job_result", {"p_request_id": settled.request_id}, _jwt(me))
        assert result.status_code == 200 and result.json() == \
            f"result of {settled.request_id}", result.text
        foreign = rpc("consumer_job_result", {"p_request_id": settled.request_id}, _jwt(other))
        assert foreign.status_code >= 400 and "not_found" in foreign.text, foreign.text
        anon = rpc("consumer_jobs", {}, None)
        assert anon.status_code in (401, 403), anon.text
        # 0024 (D10-APP-SQL): the own-ledger page and the consumer_jobs filters resolve
        # through PostgREST's named-argument dispatch (one function each, no ambiguity)
        ledger = rpc("consumer_credit_ledger", {"p_limit": 2}, _jwt(me))
        wallet = cc.wallet_of(conn, me)
        own = [str(e) for e, in conn.execute(
            "select entry_id from infrx.credit_ledger where wallet_id = %s order by "
            "created_at desc, entry_id desc limit 2", (wallet,))]
        assert ledger.status_code == 200 and [r["entry_id"] for r in ledger.json()] == own \
            and all(isinstance(r["amount"], str) and "wallet_id" not in r and "actor" not in r
                    for r in ledger.json()), ledger.text
        theirs = rpc("consumer_credit_ledger", {}, _jwt(other))
        assert theirs.status_code == 200 and not {r["entry_id"] for r in theirs.json()} & \
            set(own), theirs.text
        capped = rpc("consumer_credit_ledger", {"p_limit": 101}, _jwt(me))
        assert capped.status_code == 400 and "invalid_request" in capped.text, capped.text
        assert rpc("consumer_credit_ledger", {}, None).status_code in (401, 403)
        by_key = rpc("consumer_jobs", {"p_key_id": ca.C1_KEY, "p_limit": 10}, _jwt(me))
        assert by_key.status_code == 200 and settled.request_id in \
            {r["request_id"] for r in by_key.json()}, by_key.text
        none = rpc("consumer_jobs", {"p_model": "nobody/none"}, _jwt(me))
        assert none.status_code == 200 and none.json() == [], none.text
        # a browser session never writes a key's audience or provider scope
        patch = httpx.patch(f"{base}/api_keys?id=eq.{ca.C1_KEY}", json={"audience": "operator"},
                            headers={"Authorization": f"Bearer {_jwt(me)}",
                                     "Content-Type": "application/json"}, timeout=10)
        assert patch.status_code in (401, 403), patch.text
        # the D10 relations are not reachable from the browser API at all
        hidden = httpx.get(f"{base}/content_objects", headers={
            "Authorization": f"Bearer {_jwt(me)}"}, timeout=10)
        assert hidden.status_code == 404, hidden.text
        print(f"PostgREST {POSTGREST[-12:]}: own rows, isolation, result, anon {anon.status_code},"
              f" key scope {patch.status_code}, infrx hidden {hidden.status_code}")
    finally:
        _down()
