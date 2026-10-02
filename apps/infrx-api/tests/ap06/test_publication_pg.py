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
from .support import ALIAS, Api, approval, proposed, ready_dev, run, session

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
