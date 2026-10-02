"""AP-06 on a REAL PostgreSQL (ap6, 57558): API-PUBLISH's one-winner race and the door
composed over L3's PostgreSQL store, A3's registry/catalog and 0060's receipts.

    INFRX_D_TASK=ap6 uv run --frozen pytest -q tests/ap06/test_publication_pg.py

The race is two OS processes (`racer.py`), each its own API instance, each approving a
different proposal of the same alias at expected version 1, held behind a table lock the test
owns until both are waiting on the database, then released at once. Skips visibly off ap6.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time

import pytest

from infrx.state.control_ops import PgControlOps
from infrx.state.jobstore import connector

from tests.d import pgharness

from .conftest import CASE, pg_unavailable
from .support import (ALIAS, IDS, Api, approval, operator_actor, proposed, ready_dev, run,
                      session)

pytestmark = [pytest.mark.pg, pytest.mark.skipif(
    pg_unavailable() is not None, reason=str(pg_unavailable()))]

API_DIR = pathlib.Path(__file__).resolve().parents[2]
P = "/operator/v1/publication-proposals"


def listed(w):
    return [(v.version, v.deployment_revision_id)
            for v in run(w.control_store.listing_versions(ALIAS))]


def test_publication_pg__two_processes_racing_approvals_publish_one_listing(pg_world):
    """API-PUBLISH: two API processes approve two proposals of one alias, both at expected
    version 1, both past every check before either commits. Oracle: two new listing
    versions, two 200s, or a loser that is anything but 409 with its proposal still open."""
    w = pg_world
    first, second = (proposed(w, label) for label in ("2026-10-01", "2026-10-02"))
    holder = pgharness.connect(CASE, autocommit=False)
    holder.execute("lock table infrx.catalog_listings in access exclusive mode")
    procs = [subprocess.Popen(
        [sys.executable, "-m", "tests.ap06.racer", w.dsn, w.OPS_USER,
         prop.deployment_revision_id, source.deployment_revision_id, s.serving_version_id,
         f"race_key_{n}"], cwd=API_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True) for n, (s, source, prop) in enumerate((first, second))]
    try:
        deadline = time.monotonic() + 60
        with pgharness.connect(CASE) as watch:
            while (watch.execute("select count(*) from pg_stat_activity where datname = %s "
                                 "and wait_event_type = 'Lock'", (CASE,)).fetchone()
                   or (0,))[0] < 2:
                assert time.monotonic() < deadline, "the racers never both reached the lock"
                assert all(p.poll() is None for p in procs), [p.communicate() for p in procs]
                time.sleep(0.05)
    finally:
        holder.rollback()
        holder.close()
    answers = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        answers.append(json.loads(out))
    assert sorted(a["status"] for a in answers) == [200, 409], answers
    [won] = [a["body"] for a in answers if a["status"] == 200]
    [lost] = [a["body"] for a in answers if a["status"] == 409]
    assert lost["error"]["code"] == "state_conflict"
    assert listed(w)[1:] == [(2, won["deployment_revision_id"])]
    loser = second if won["deployment_revision_id"] == first[2].deployment_revision_id \
        else first
    assert run(w.control.store.deployment(loser[2].deployment_revision_id)).state.value \
        == "proposed_public"


def test_publication_pg__an_approval_replays_through_0060_and_rolls_back(pg_world):
    """Oracle: on PostgreSQL the approval is listing 2 and its key replays it from 0060's
    receipt (another body: 409); a rollback by the slash-carrying alias is listing 3 naming
    version 1's revision; a fund replays grant_credit's one entry."""
    w = pg_world
    _, source, prop = proposed(w)
    a = Api(w, ops=PgControlOps(connector(w.dsn)))
    a.readiness.ready(source)
    path = f"{P}/{prop.deployment_revision_id}/approve"
    first = a.post(path, approval(), key="pg-approve-1")
    assert first.status_code == 200, first.text
    assert a.post(path, approval(), key="pg-approve-1").json() == {**first.json(),
                                                                   "replayed": True}
    assert a.post(path, approval(reason="other"), key="pg-approve-1").status_code == 409
    seeded = listed(w)[0][1]
    r = a.post(f"/operator/v1/listings/{ALIAS}/rollback",
               {"to_version": 1, "expected_version": 2, "reason": "regression"},
               key="pg-rollback-1")
    assert r.status_code == 200, r.text
    assert listed(w) == [(1, seeded), (2, prop.deployment_revision_id), (3, seeded)]
    body = {"provider_org_id": w.B, "amount": "12.5", "reason": "preview budget"}
    funded = a.post("/operator/v1/dev-wallet-grants", body, key="pg-fund-1")
    assert funded.status_code == 201, funded.text
    again = a.post("/operator/v1/dev-wallet-grants", body, key="pg-fund-1")
    assert again.json() == {**funded.json(), "replayed": True}
    assert w.conn.execute("select count(*) from infrx.credit_ledger e join "
                          "infrx.credit_wallets c using (wallet_id) where "
                          "c.owner_provider_org_id = %s", (w.B,)).fetchone()[0] == 1


def test_publication_pg__a_dev_key_is_issued_once_on_postgres(pg_world):
    """Oracle: the stored provider_dev row names the provider and the dev endpoint and holds
    the secret's hash; the replay names the same key without the secret; one row exists."""
    w = pg_world
    _, d = ready_dev(w)
    a = Api(w, ops=PgControlOps(connector(w.dsn))).as_(session(w.DEV_A))
    path = f"/lab/v1/control/endpoints/{d.endpoint_id}/keys"
    first = a.post(path, {"name": "ci"}, key="pg-devkey-1", provider_org_id=w.A)
    assert first.status_code == 201, first.text
    import hashlib
    body = first.json()
    assert w.key_scope(body["key_id"]) == (w.A, d.endpoint_id,
                                           hashlib.sha256(body["secret"].encode()).hexdigest())
    again = a.post(path, {"name": "ci"}, key="pg-devkey-1", provider_org_id=w.A)
    assert again.json() == {**body, "secret": None, "secret_returned": False,
                            "replayed": True}
    assert w.conn.execute("select count(*) from public.api_keys where endpoint_id = %s",
                          (d.endpoint_id,)).fetchone()[0] == 1


LOGIN_PASSWORD = "infrx-ap6-role-login"       # the task-local container's only


def login(w, role: str) -> str:
    """A direct DSN for `role` (0043/0068 leave the Lab roles to the operator's login)."""
    w.conn.execute(f"alter role {role} login password '{LOGIN_PASSWORD}'")
    return w.dsn.replace(f"postgres:{pgharness.PASSWORD}@", f"{role}:{LOGIN_PASSWORD}@")


def test_publication_pg__dev_keys_and_the_wallet_answer_from_0068s_doors(pg_world):
    """WR-AS3-2, composed as the Lab unit composes it: `PgDevCredentials` on the control
    login (`infrx_lab_control`, set_role=False). Oracle: a provider's wallet is closed at 0 CREDIT, then
    exactly the operator's grant (the seeded provider's balance as stored); the listing names the issued key (never its hash); a revoke
    is 200 and another key's replay answers the first `revoked_at` with one audit entry, the
    revoker's, keyed by the first request; another provider's scope, the prod endpoint and a
    malformed id are 404 - never an empty list or a 500."""
    from infrx.lab.publication import PgDevCredentials
    w = pg_world
    _, d = ready_dev(w)
    creds = PgDevCredentials(connector(login(w, "infrx_lab_control"), set_role=False))
    a = Api(w, ops=PgControlOps(connector(w.dsn)), credentials=creds).as_(session(w.DEV_A))
    path, wallet = f"/lab/v1/control/endpoints/{d.endpoint_id}/keys", "/lab/v1/control/dev-wallet"
    seeded = a.get(wallet, provider_org_id=w.A)
    assert seeded.status_code == 200, seeded.text
    assert seeded.json() == {"provider_org_id": w.A, "opened": True,
                             "balance": {"amount": "500.00000000", "unit": "CREDIT"}}
    issued = a.post(path, {"name": "ci"}, key="pg-devkey-2", provider_org_id=w.A).json()
    listed = a.get(path, provider_org_id=w.A)
    assert listed.status_code == 200, listed.text
    [row] = listed.json()["data"]
    assert row == {**row, "key_id": issued["key_id"], "endpoint_id": d.endpoint_id,
                   "name": "ci", "prefix": issued["prefix"], "revoked_at": None}

    def revoke(key_id, key, provider=w.A):
        return a.client.delete(f"{path}/{key_id}", params={"provider_org_id": provider},
                               headers={"Idempotency-Key": key})
    first = revoke(issued["key_id"], "pg-revoke-1")
    assert first.status_code == 200 and first.json()["revoked_at"], first.text
    assert revoke(issued["key_id"], "pg-revoke-2").json() == first.json()
    assert w.conn.execute(
        "select actor_principal, idempotency_key from infrx.audit_entries where "
        "after->>'key_id' = %s", (issued["key_id"],)).fetchall() == [
        (f"lab:{w.DEV_A}", f"lab_dev_key_revoke:{issued['key_id']}:pg-revoke-1")]
    assert [k["revoked_at"] for k in a.get(path, provider_org_id=w.A).json()["data"]] \
        == [first.json()["revoked_at"]]
    prod = run(w.control_store.deployment(IDS.prod_deployment)).endpoint_id
    assert a.get(f"/lab/v1/control/endpoints/{prod}/keys", provider_org_id=w.A).status_code \
        == 404
    assert a.get("/lab/v1/control/endpoints/not-a-uuid/keys",
                 provider_org_id=w.A).status_code == 404
    assert revoke("not-a-uuid", "pg-revoke-3").status_code == 404
    a.as_(session(w.DEV_B))
    assert a.get(path, provider_org_id=w.B).status_code == 404
    assert revoke(issued["key_id"], "pg-revoke-4", w.B).status_code == 404
    assert a.get(wallet, provider_org_id=w.B).json() == {
        "provider_org_id": w.B, "opened": False,
        "balance": {"amount": "0.00000000", "unit": "CREDIT"}}
    a.as_(operator_actor(w))
    assert a.post("/operator/v1/dev-wallet-grants", {"provider_org_id": w.B, "amount": "12.5",
                                                     "reason": "preview budget"},
                  key="pg-fund-2").status_code == 201
    assert a.as_(session(w.DEV_B)).get(wallet, provider_org_id=w.B).json() == {
        "provider_org_id": w.B, "opened": True,
        "balance": {"amount": "12.50000000", "unit": "CREDIT"}}


def test_publication_pg__a_role_login_connects_without_the_switch_and_the_service_login_switches(
        pg_world, tmp_path):
    """WR-AS3-3: the Lab workers' connector on a direct (non-pooler) DSN. Oracle: a worker
    composed on the datasets role's own login (0068, member of nothing) is ready, and that
    login runs as itself; the service login still runs as `service_role`."""
    from infrx.lab.workers import __main__ as lab_workers
    from infrx.media.store import InMemoryObjectStore
    w = pg_world
    role = login(w, "infrx_lab_datasets")

    async def current_user(dsn):
        conn = await lab_workers.lab_connector(dsn)()
        try:
            return (await (await conn.execute("select current_user")).fetchone())[0]
        finally:
            await conn.close()
    worker = lab_workers.compose("checkpoints", {
        "LAB_DATABASE_URL": role, "LAB_WORKER_HEALTH_PORT": "18013",
        "LAB_S3_BUCKET": "infrx-lab"}, objects=InMemoryObjectStore())
    assert run(worker.ready()) is True
    assert run(current_user(role)) == "infrx_lab_datasets"
    assert run(current_user(w.dsn)) == "service_role"
