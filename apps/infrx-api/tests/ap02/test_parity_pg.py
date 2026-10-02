"""AP-02 parity (02a exit): the App's current TS read adapters and the new API, on the SAME seeded
ap2 rows, give identical balances, strings and states.

`parity_harness.ts` runs credit-reads.ts / request-reads.ts / request-view-model.ts /
operator-reads.ts unchanged under node, through a PostgREST-equivalent client (the browser's
`authenticated` session, `json_agg` rendering); this case reads the same database through the
routes and compares field by field. Oracle: any drift in a balance, a ledger amount, "spent", a
job's unit/hold/charge/state, a result's read outcome or an operator figure fails here.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
from datetime import datetime
from urllib.parse import urlsplit

import pytest

from tests.d import checks_content as ck

from .conftest import client, session
from .test_projections_pg import _walk

pytestmark = pytest.mark.pg
HARNESS = pathlib.Path(__file__).with_name("parity_harness.ts")
#: request-view-model.ts RESULT_STATUS: the status each ResultRead state is served with.
RESULT_STATUS = {"ready": 200, "pending": 409, "withheld": 409, "no_result": 404,
                 "not_found": 404, "expired": 410, "unavailable": 503, "signed_out": 401}


def _ts(dsn: str, seeded) -> dict:
    node = shutil.which("node", path="/usr/bin:/bin:/usr/local/bin")
    if node is None:
        pytest.skip("node is not installed (the App's adapters run under node)")
    url = urlsplit(dsn)
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "PGHOST": url.hostname or "127.0.0.1",
           "PGPORT": str(url.port), "PGUSER": url.username or "postgres",
           "PGPASSWORD": url.password or "", "PGDATABASE": url.path.lstrip("/"),
           "AP02_USER": seeded.me, "AP02_ORG": seeded.my_org, "AP02_OPERATOR": seeded.operator}
    done = subprocess.run([node, "--no-warnings", str(HARNESS)], env=env, capture_output=True,
                          text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


def _instant(value: str | None):
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def _amount(money: dict | None) -> str | None:
    return None if money is None else money["amount"]


def test_parity__the_apps_adapters_and_the_api_read_the_same(db, seeded):
    dsn, conn = db
    # distinct instants: operator-reads.ts orders the audit by `at` alone (no tiebreak)
    ck.at(conn, conn.execute("select infrx.now() + interval '1 second'").fetchone()[0])
    conn.execute("insert into infrx.audit_entries (id, actor_principal, action, target_org_id, "
                 "reason, idempotency_key) values (gen_random_uuid(), 'operator:x', "
                 "'admin_grant', %s, 'parity', 'ap02-parity')", (seeded.my_org,))
    ts = _ts(dsn, seeded)
    me = client(dsn, session(seeded.me, seeded.my_org))
    op = client(dsn, session(seeded.operator, operator=True))

    # 02a: the wallet, "spent", the ledger and the legacy statement
    credits = me.get("/console/v1/credits").json()
    w = ts["wallet"]["value"]
    assert (w["walletId"], w["ledgerTotal"], w["reservedTotal"], w["available"],
            w["signupGrantedAt"]) == \
        (credits["wallet_id"], _amount(credits["ledger_total"]), _amount(credits["reserved_total"]),
         _amount(credits["available"]), credits["signup_granted_at"])
    assert ts["spent"] == _amount(credits["spent"]) and ts["spent"] is not None
    ledger = _walk(me, "/console/v1/credit-ledger", 2)
    assert [(e["id"], e["createdAt"], e["kind"], e["amount"], e["requestId"], e["reason"])
            for e in ts["ledger"]] == \
        [(e["entry_id"], e["created_at"], e["kind"], _amount(e["amount"]), e["request_id"],
          e["reason"]) for e in ledger]
    legacy = me.get("/console/v1/legacy-statement").json()
    assert ts["legacy"]["value"] == {"balance": _amount(legacy["balance"]),
                                     "entryCount": legacy["entry_count"],
                                     "rolloutHold": legacy["rollout_hold"]}

    # 02b: every job, its read outcome and its result
    jobs = _walk(me, "/console/v1/requests", 2)
    assert [j["requestId"] for j in ts["jobs"]] == [j["request_id"] for j in jobs]
    for t, a in zip(ts["jobs"], jobs, strict=True):
        assert (t["createdAt"], t["requestedModel"], t["modelRevision"], t["executionMode"],
                t["state"], t["outcomeCause"], t["regime"], t["settlementState"],
                t["usageCertainty"], t["promptTokens"], t["completionTokens"], t["hold"],
                t["holdState"], t["charged"], t["resultExpiresAt"]) == \
            (a["created_at"], a["requested_model"], a["model_revision"], a["execution_mode"],
             a["state"], a["outcome_cause"], a["accounting_regime"], a["settlement_state"],
             a["usage_certainty"], a["prompt_tokens"], a["completion_tokens"], _amount(a["hold"]),
             a["hold_state"], _amount(a["charged"]), a["result_expires_at"]), a["request_id"]
        assert t["unit"] == a["hold"]["unit"], "every seeded job holds in its own unit"
        assert ts["access"][a["request_id"]] == a["result_access"]
        read = ts["results"][a["request_id"]]
        r = me.get(f"/console/v1/requests/{a['request_id']}/result")
        assert RESULT_STATUS[read["state"]] == r.status_code, (a["request_id"], read, r.text)
        if read["state"] == "ready":
            assert read["text"] == r.json()["text"]
    assert {j["state"] for j in ts["jobs"]} >= {"succeeded", "failed", "running"}

    # 02c: key options and the operator projections
    keys = _walk(me, "/console/v1/keys", 2)
    assert sorted((k["id"], k["name"], k["prefix"]) for k in ts["keys"]["value"]) == \
        sorted((k["id"], k["name"], k["prefix"]) for k in keys)
    view = ts["operator"]
    accounts = {a["wallet_id"]: a for a in _walk(op, "/operator/v1/accounts", 2)}
    assert {a["walletId"] for a in view["accounts"]["value"]} == set(accounts)
    for t in view["accounts"]["value"]:
        a = accounts[t["walletId"]]
        assert (t["userId"], t["orgId"], t["email"], t["suspended"], t["suspensionReason"],
                t["ledgerTotal"], t["reservedTotal"], t["available"],
                _instant(t["signupGrantedAt"])) == \
            (a["user_id"], a["org_id"], a["email"], a["suspended"], a["suspension_reason"],
             _amount(a["ledger_total"]), _amount(a["reserved_total"]), _amount(a["available"]),
             _instant(a["signup_granted_at"]))
    unknown = _walk(op, "/operator/v1/unknown-usage", 2)
    assert [(u["requestId"], u["orgId"], u["hold"], _instant(u["reconcileAfter"]))
            for u in view["unknownUsage"]["value"]] == \
        [(u["request_id"], u["org_id"], u["hold"], _instant(u["reconcile_after"]))
         for u in unknown]
    assert view["drift"]["value"] == _walk(op, "/operator/v1/wallet-drift", 2) == []
    audit = _walk(op, "/operator/v1/audit", 2)
    assert [(e["id"], e["action"], e["reason"], e["idempotencyKey"])
            for e in view["audit"]["value"]] == \
        [(e["id"], e["action"], e["reason"], e["idempotency_key"]) for e in audit]
    assert os.environ.get("INFRX_D_TASK") == "ap2"
