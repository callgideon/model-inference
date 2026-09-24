"""G8 acceptance on a REAL PostgreSQL with the composed gateway (CREDIT-CUTOVER / -GRANT / -SPEND).

From the hosted pilot's shape (USD admission on, CREDIT off) the operator publishes the
approved (FIXTURE-labelled) card, runs the transition, then onboards a fresh verified
individual headlessly: grant, key, a real HTTP request through `create_app` (CREDIT
regime, the PostgreSQL adapters it composes from DATABASE_URL), the worker's durable
steps with a scripted engine answer, the result over HTTP, the exact statement, the key's
revocation and the denial. A second individual cannot read, cancel or spend the first's
job or wallet, and neither multiplies an entitlement.

Engine: scripted (no GPU); everything the accounting touches is the real store. The key
lookup is PostgREST's `api_keys` query answered from the same database. Rates are FIXTURE
values, labelled, never a launch price (P-01).

    INFRX_D_TASK=g8 uv run --frozen pytest -q tests/g/ops/test_acceptance_pg.py
"""
from __future__ import annotations

import asyncio
import json
import re
import types
from decimal import Decimal

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from infrx.config import Settings
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import Usage
from infrx.gateway.app import create_app
from infrx.media.store import InMemoryObjectStore
from infrx.operations import cli
from infrx.scheduling.memory import MemoryScheduler

from . import pgworld
from .pgworld import R, individual, needs_pg, settle, signup_rows
from .test_transition_pg import (ACTIVATE, FIXTURE_CARD, cli_run, pilot,
                                 publish_fixture_card)

pytestmark = needs_pg
_COLUMNS = re.compile(r"^[a-z_]+(,[a-z_]+)*$")
BODY = {"model": "nemostation/marlin-2b", "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 64}


def postgrest(dsn: str) -> httpx.AsyncClient:
    """Supabase's REST answer to the gateway's key lookup (`auth.keys`), from the database:
    `GET /api_keys?key_hash=eq.<sha256>&select=<cols>` and the `last_used_at` PATCH."""
    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/api_keys"):
            return httpx.Response(404)
        key_hash = request.url.params["key_hash"].removeprefix("eq.")
        with psycopg.connect(dsn, autocommit=True) as conn:
            if request.method == "PATCH":
                conn.execute("update public.api_keys set last_used_at = now() "
                             "where key_hash = %s", (key_hash,))
                return httpx.Response(204)
            select = request.url.params.get("select", "id,org_id,revoked_at")
            assert _COLUMNS.match(select), select
            cursor = conn.execute(f"select {select} from public.api_keys where key_hash = %s",
                                  (key_hash,))
            names = [c.name for c in cursor.description]
            rows = [{n: (None if v is None else str(v)) for n, v in zip(names, row)}
                    for row in cursor.fetchall()]
        return httpx.Response(200, json=rows)
    return httpx.AsyncClient(base_url="https://fake.supabase.invalid/rest/v1",
                             transport=httpx.MockTransport(handler))


def gateway(w, clock):
    settings = Settings(supabase_url="https://fake.supabase.invalid", supabase_key="service-role",
                        pilot=DEFAULTS.replace(infrx_mode="pilot", database_url=w.dsn,
                                               active_rate_card_version=FIXTURE_CARD))
    settings.deployment = settings.deployment.replace(
        accounting_regime="credit", infrx_release_sha="c0ffee" + "0" * 34,
        infrx_image="sha256:" + "b" * 64)
    return create_app(settings, client=object(), sb=postgrest(w.dsn), clock=lambda: clock[0],
                      objects=InMemoryObjectStore(), index=MemoryScheduler(lambda: None))


def onboard(w, user, tmp_path, capsys, name):
    env = ["--idempotency-key"]
    assert cli_run(w, ["grant", "--user", user, *env, f"g-{name}", "--reason", R], capsys)[0] == 0
    key_file = tmp_path / f"{name}.key"
    code, issued, err = cli_run(w, ["issue-key", "--user", user, "--name", name, "--secret-file",
                                    str(key_file), *env, f"k-{name}", "--reason", R], capsys)
    assert code == 0 and issued["secret_file"] == str(key_file), err
    return key_file.read_text().strip(), issued, key_file


def statement(w, key_file, capsys):
    code, out, err = cli_run(w, ["statement", "--key-file", str(key_file)], capsys,
                             operator=False)
    return code, out, err


def test_credit_cutover__a_fresh_individual_spends_reads_exact_credit_and_is_revoked(
        tmp_path, capsys):
    w, usd_request, _ = pilot("g8_accept")
    run = asyncio.run
    run(settle(w, usd_request, "legacy_usd"))                     # drain the pilot's USD job
    publish_fixture_card(w, capsys)
    code, switched, err = cli_run(w, [*ACTIVATE, "--idempotency-key", "cutover", "--reason", R],
                                  capsys)
    assert code == 0, (switched, err)
    assert switched["restart_with"]["ACTIVE_RATE_CARD_VERSION"] == FIXTURE_CARD
    alice, bob = individual(w), individual(w)
    entitlements = w.one("select count(*) from infrx.signup_entitlements")
    alice_secret, alice_key, alice_file = onboard(w, alice, tmp_path, capsys, "alice")
    bob_secret, _, bob_file = onboard(w, bob, tmp_path, capsys, "bob")
    clock = [1_790_000_000.0]
    app = gateway(w, clock)
    alice_auth = {"authorization": f"Bearer {alice_secret}"}
    bob_auth = {"authorization": f"Bearer {bob_secret}"}

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        accepted = client.post("/v1/jobs", json=BODY,
                               headers={**alice_auth, "idempotency-key": "item-1"})
        assert accepted.status_code == 202, accepted.text
        job = accepted.json()
        handle, request_id = job["job_handle"], job["request_id"]

        code, held, _ = statement(w, alice_file, capsys)            # the hold, exactly
        assert code == 0 and len(held["holds"]) == 1, held
        hold = Decimal(held["holds"][0]["amount"])
        assert held["holds"][0] == {"request_id": request_id, "state": "held",
                                    "amount": f"{hold:.8f}", "unit": "CREDIT"}
        assert held["credit"]["reserved_total"] == f"{hold:.8f}"
        assert held["credit"]["available"] == f"{Decimal('10000') - hold:.8f}"
        assert held["credit"]["spent"] == "0.00000000"

        # Bob can neither read nor cancel Alice's job (404, as an unknown handle).
        assert client.get(f"/v1/jobs/{handle}", headers=bob_auth).status_code == 404
        assert client.delete(f"/v1/jobs/{handle}", headers=bob_auth).status_code == 404

        _, _, (outcome, settlement) = run(settle(w, types.SimpleNamespace(
            request_id=request_id), "credit", tokens=Usage.of(1200, 40), text="done"))
        result = client.get(f"/v1/jobs/{handle}/result", headers=alice_auth)
        assert result.status_code == 200, result.text
        assert result.json()["state"] == "succeeded", result.json()
        assert (result.json()["usage"]["prompt_tokens"],
                result.json()["usage"]["completion_tokens"]) == (1200, 40)

        # 1200 x 0.5/1e6 + 40 x 1.5/1e6 = 0.0006 + 0.00006 at the pinned FIXTURE card.
        charged = "0.00066000"
        assert str(settlement.charged) == charged and settlement.rate_card_version == FIXTURE_CARD
        left = f"{Decimal('10000') - Decimal(charged):.8f}"
        code, spent, _ = statement(w, alice_file, capsys)
        assert code == 0 and spent["credit"] == {
            "wallet_id": spent["credit"]["wallet_id"], "kind": "consumer", "unit": "CREDIT",
            "ledger_total": left, "reserved_total": "0.00000000", "available": left,
            "spent": charged, "spent_complete": True}, spent
        assert spent["holds"] == [] and spent["usage_totals"] == {"CREDIT": charged}
        code, account, _ = cli_run(w, ["account", "--user", alice], capsys)
        assert account["credit"] == spent["credit"]
        code, untouched, _ = statement(w, bob_file, capsys)
        assert untouched["credit"]["available"] == "10000.00000000"
        assert untouched["usage_totals"] == {} and untouched["holds"] == []

        # Revocation: the operator tool and a new admission refuse at once (the admission
        # transaction reads the key row itself, whatever the gateway's key cache holds);
        # a status read is refused once the gateway's positive cache (KEY_TTL) lapses.
        assert cli_run(w, ["revoke-key", "--org", alice_key["org_id"], "--key-id",
                           alice_key["key_id"], "--idempotency-key", "rv", "--reason", R],
                       capsys)[0] == 0
        code, _, err = statement(w, alice_file, capsys)
        assert code == 1 and json.loads(err)["error"] == "invalid_api_key"
        denied = client.post("/v1/jobs", json=BODY,
                             headers={**alice_auth, "idempotency-key": "item-2"})
        assert denied.status_code == 401, denied.text
        clock[0] += app.state.runtime.settings.key_ttl + 1
        assert client.get(f"/v1/jobs/{handle}", headers=alice_auth).status_code == 401
        assert client.get(f"/v1/jobs/{handle}", headers=bob_auth).status_code == 404

    # No entitlement multiplied: two individuals, two grants, one each.
    assert w.one("select count(*) from infrx.signup_entitlements") == entitlements + 2
    assert (signup_rows(w, alice), signup_rows(w, bob)) == (1, 1)
    assert w.one("select count(*) from infrx.credit_ledger where kind = 'inference_debit' "
                 "and request_id = %s", (request_id,)) == 1
    assert pgworld.drift(w) == []


@pytest.fixture(autouse=True)
def _no_operator_prompt(monkeypatch):
    monkeypatch.delenv(cli.OPERATOR_KEY_ENV, raising=False)
