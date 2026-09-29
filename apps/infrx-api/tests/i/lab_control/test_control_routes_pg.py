#!/usr/bin/env python3
"""The real half of WR-LDP-2 / LDP-F1 (b) / LDP-F3 / LDP-F7: the control unit on the task-local
PostgreSQL of key l4 (57503), migrations 0001-0052 applied, L3's seed (`tests/l/control`),
logged in as 0043's `infrx_lab_control` (a local password, as the operator sets one) on the
direct port - where the old `connector(dsn)` ran `set role service_role` and was refused - and,
for comparison, as the owner. The session verifier is the case's own (one test token); every
family's read runs as provider A's administrator.

    INFRX_D_TASK=l4 uv run --frozen pytest -q -s -m pg tests/i/lab_control

Failure oracles: the unit not ready on its own login (LDP-F7); a member refused (401/404) or a
family missing on it; any 500 (LDP-F3). SR-LCR-1 (0056, lab-sql-lw8): the Lab column equals the
owner column for every family (LCR-F1 closed), each pinned in `EXPECTED`; a worker-only claim
stays refused to the Lab login (42501); 0041's sample reads the families' lineage calls run on
it (0-LW8-R1).
"""
from __future__ import annotations

import json
import os
import socket

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from infrx.contracts import errors
from infrx.datasets import imports
from infrx.gateway import lab_auth
from infrx.judge import cost
from infrx.lab.control import app as control_app
from infrx.lab.workers.__main__ import plan_key
from infrx.media.store import InMemoryObjectStore
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore

from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_d8_ledgers as d8
from ...d import test_d9_rollout as d9
from ...d.test_code_mutants_live import job, ref_of
from ...j import fakes as j1
from ...j.submit.judge_fake import JudgeFake
from ...l.control import worlds
from ...n.imports.world import chunks, fixture, run
from ...n.versions.test_versions import uid
from ...p.annotations.world import RUBRIC, rows
from ...r.control.test_control import plan
from .test_control_routes import FAMILIES, PROVIDER, SWITCHES, TOKEN

pytestmark = pytest.mark.pg
_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "l4" else \
    "PostgreSQL only on the l4 task-local key (INFRX_D_TASK=l4)"
DB, LAB_PASSWORD = f"{pgharness.DATABASE}_lcr", "infrx-l4-lab-control"
PRESENT_DB = f"{pgharness.DATABASE}_lcrp"
#: one teacher batch per login, so each login runs the approval's whole submission
BATCHES = {"owner": uid(1, 0x7b), "lab": uid(1, 0x7c)}
A, ADMIN_A = worlds.PgWorld.A, worlds.PgWorld.ADMIN_A
#: family -> (status, body prefix) on BOTH logins after 0056. A 503 here is a port the unit does
#: not compose yet (evaluations' experiments: WR-B4-2; teachers off: P-10), the same on the
#: owner login - never a grant the Lab login lacks.
EXPECTED = {
    "control": (200, '{"data":[{"model_id":"nemostation/marlin-2b"'),
    "datasets": (404, '{"detail":"not_found: no such Lab record for this provider"}'),
    "evaluations": (503, '{"refusal":"unavailable"}'),
    "pipelines": (404, '{"refusal":"not_found"}'),
    "teacher-batches": (503, '{"refusal":"unavailable"}'),
    "releases": (200, '{"data":{"releases":[],"decisions":[],"proposals":[]}}'),
    "optimizations": (200, '{"data":[]}'),
}


@pytest.fixture(scope="module")
def present():
    """WR-LW8-R2/R3: PRESENT records, seeded through the owner login - D8's world (D7's
    grants, the payer's budget, the teacher grant) with a dataset version (N2's importer; the
    objects in memory, shared with the unit), a running release (D7's policy, D9's start, its
    launcher's plan) -
    so a missing grant past the first `lab_resolve` shows."""
    if _reason is not None:
        pytest.skip(f"task-local PostgreSQL unavailable: {_reason}")
    from infrx.state import migrations
    pgharness.ensure()
    pgharness.recreate(PRESENT_DB)
    pgharness.apply(PRESENT_DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    owner, objects = pgharness.dsn(PRESENT_DB), InMemoryObjectStore()
    with pgharness.connect(PRESENT_DB) as conn:
        d8.seed(conn)
        conn.execute(f"alter role infrx_lab_control password '{LAB_PASSWORD}'")
        spec, _ = fixture("benchmark")
        spec = {**spec, "import_id": uid(2, 0x1c), "dataset_id": uid(2, 0xdc),
                "grant_ref": d7.W["grant"], "fields": {"content": "q", "group": "g",
                                                       "split": "split"}}
        data = b"".join(json.dumps(i).encode() + b"\n"
                        for i in rows(8, splits=("train", "train", "holdout", "validation")))
        ref = run(imports.Importer(PgLabDataStore(connector(owner)), objects).run(
            spec, chunks(data, 64), provider_org_id=A, actor="dev@nemo")).dataset_ref
        policy_id = d9.uid(41, 0xb0)
        policy = d7.publish(conn, d9.policy(policy_id, weights=(1_000,)))
        d9.start(conn, policy)
        run(imports.write_once(objects, plan_key(A, policy_id),      # the launcher's plan
                               plan().model_dump_json().encode()))
    lab = make_conninfo(owner, user="infrx_lab_control", password=LAB_PASSWORD)
    return {"lab": lab, "owner": owner}, objects, ref, policy


@pytest.fixture(scope="module")
def database():
    if _reason is not None:
        pytest.skip(f"task-local PostgreSQL unavailable: {_reason}")
    from infrx.state import migrations
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        worlds.seed_pg(conn, pgharness.dsn(DB))
        conn.execute(f"alter role infrx_lab_control password '{LAB_PASSWORD}'")
    owner = pgharness.dsn(DB)
    lab = make_conninfo(owner, user="infrx_lab_control", password=LAB_PASSWORD)
    assert conninfo_to_dict(lab)["port"] != "6543"        # the direct port: no pooler
    return {"lab": lab, "owner": owner}


def unit(monkeypatch, dsn: str, **env: str) -> TestClient:
    for name, value in {control_app.DATABASE_URL: dsn,
                        control_app.SUPABASE_URL: "http://127.0.0.1:1",
                        control_app.SUPABASE_KEY: "anon"}.items():
        monkeypatch.setenv(name, value)
    for name in SWITCHES:
        monkeypatch.setenv(name, "0")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in ("DATABASE_URL", "CLICKHOUSE_URL", "S3_TRACE_BUCKET", "LAB_S3_BUCKET",
                 "LAB_CHECKPOINT_KEYS"):
        monkeypatch.delenv(name, raising=False)

    async def user_id(self, token):
        if token != TOKEN:
            raise errors.InvalidApiKey("not a live session")
        return ADMIN_A

    monkeypatch.setattr(lab_auth.GoTrueSessions, "user_id", user_id)
    return TestClient(control_app.create_app(), raise_server_exceptions=False)


def test_control_routes_pg__every_family_is_served_on_the_lab_login_typed_never_a_500(
        database, monkeypatch):
    """On `infrx_lab_control`: `/readyz` 200 (LDP-F7), the control listing 200, and every
    family reached as A's administrator (never 401, never the membership's 404), answering
    its record (200, or 404 for the probe's absent `ds@1`) or its typed 503 - never a 500
    (LDP-F3). The owner login is the comparison column."""
    matrix = {}
    for login, dsn in database.items():
        with monkeypatch.context() as m:
            c = unit(m, dsn)
            assert c.get("/readyz").json() == {"status": "ready"}, login
            for family, path in FAMILIES.items():
                answer = c.get(path.replace(PROVIDER, A),
                               headers={"authorization": f"Bearer {TOKEN}"})
                matrix[login, family] = (answer.status_code, answer.text[:80])
    for login in database:
        print(f"\n[{login}]", {f: matrix[login, f] for f in FAMILIES})
    for (login, family), (status, text) in matrix.items():
        assert status not in (401, 500) and "workspace" not in text, (login, family, text)
        if status == 503:
            assert text in ('{"refusal":"unavailable"}',
                            '{"detail":"the datasets service failed"}'), (login, family, text)
    assert matrix["lab", "control"][0] == 200 and matrix["owner", "control"][0] == 200
    got = {(login, f): (status, text[:len(EXPECTED[f][1])])
           for (login, f), (status, text) in matrix.items()}
    assert got == {(login, f): EXPECTED[f] for login in database for f in FAMILIES}, got
    import psycopg
    with psycopg.connect(database["lab"], autocommit=True) as lab:     # SR-LCR-1: routes only
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            lab.execute("select infrx.lab_import_job_claim('{}'::jsonb)")
        # 0-LW8-R1: lineage.status / lineage.permitted (a version's page, derive, export,
        # read_part; label imports, select, export; training prepare) run 0041's reads on the
        # unit's login once a record exists - the absent `ds@1` above never reaches them.
        for function in ("lab_blocked_samples", "lab_permitted_samples"):
            args = {"provider_org_id": A, "dataset_ref": "lab:dataset:none"}
            try:
                lab.execute(f"select infrx.{function}(%s)", (Jsonb(args),))
            except psycopg.errors.InsufficientPrivilege as refused:
                pytest.fail(f"{function}: {refused.sqlstate} on the Lab login")
            except psycopg.Error:
                pass            # the function ran and refused its arguments: granted


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_control_routes_pg__present_records_answer_alike_on_both_logins(present, monkeypatch):
    """WR-LW8-R2/R3: a PRESENT dataset version (its page: lineage's 0041 reads), its
    disagreements (the pipeline's label log), a running release and - with LAB_TEACHERS on and
    JUDGE_MODE live - a teacher batch planned then approved by the administrator (the
    approval's in-request submission: D8's teacher reserve/record_sent, J2's begin_submit and
    record_submission, to a local teacher fake) answer the same on the Lab login as on the
    owner's. A grant missing past `lab_resolve` is a 503 in the lab column only."""
    logins, objects, ref, policy = present
    fake = JudgeFake(port=_free_port())
    got = {}
    try:
        for login, dsn in logins.items():
            with monkeypatch.context() as m:
                m.setattr(control_app, "NoObjects", lambda: objects)
                m.setattr(cost, "APPROVED_RATES", j1.TEST_RATES)   # the approved table is empty
                c = unit(m, dsn, LAB_TEACHERS="1", LAB_TEACHER_URL=fake.url, JUDGE_MODE="live",
                         JUDGE_LIVE_BUDGET_USD="5")
                auth, q = {"authorization": f"Bearer {TOKEN}"}, {"provider_org_id": A}
                batch = {"batch_id": BATCHES[login], "dataset_ref": ref, "rubric_ref": RUBRIC,
                         "teacher_model": j1.JUDGE_MODEL, "prompt_version": "teach-v1",
                         "payer_ref": d7.PAYER, "budget_usd": "1.00000000", "chunk_size": 4}
                answers = {
                    "version": c.get(f"/lab/v1/providers/{A}/datasets/versions/{ref}",
                                     headers=auth),
                    "disagreements": c.get("/lab/v1/pipelines/disagreements", headers=auth,
                                           params={**q, "dataset_ref": ref}),
                    "releases": c.get("/lab/v1/releases", params=q, headers=auth),
                    "plan": c.post("/lab/v1/pipelines/teacher-batches", params=q,
                                   json=batch, headers=auth),
                    "approve": c.post(f"/lab/v1/pipelines/teacher-batches/{BATCHES[login]}"
                                      "/approve", params=q, headers=auth)}
                for probe, answer in answers.items():
                    got[login, probe] = (answer.status_code, answer.json())
    finally:
        fake.close()
    for key, (status, _) in sorted(got.items()):
        print(f"\n{key}: {status}")
    status = {key: answer[0] for key, answer in got.items()}
    assert status == {(login, probe): code for login in logins for probe, code in (
        ("version", 200), ("disagreements", 200), ("releases", 200), ("plan", 201),
        ("approve", 200))}, got
    for login in logins:
        assert got[login, "version"][1] == got["owner", "version"][1], login
        assert policy in {r["policy_ref"] for r in got[login, "releases"][1]["data"]["releases"]}
        chunks = got[login, "approve"][1]["chunks"]
        assert [(x["state"], x["sent"]) for x in chunks] == [("submitted", 4), ("submitted", 2)], \
            (login, chunks)
    assert len(fake.posts) == 4                   # two chunks per login, each sent once


def test_control_routes_pg__an_assigned_running_release_reads_its_verdict_on_the_lab_login(
        present, monkeypatch):
    """WR-LR7-GRANT (0059, R259): on `infrx_lab_control` the release page lists a running
    release with one healthy assigned terminal job - its progress is D9's Live (0054's
    `lab_release_live`) and its verdict R2's hold on `no_report` read at request time (B4's
    `lab_experiments`) - the same row as the owner login's. Without 0059: a 503
    (InsufficientPrivilege) in the lab column only."""
    from infrx.lab.control.operations import serving_ref
    from infrx.state.catalog import PgCatalogDirectory
    from infrx.state.lab_control import PgControlStore

    from ...d import checks_credit as cc
    logins, objects, _, _ = present
    owner = connector(logins["owner"])
    public = run(PgControlStore(owner).deployment(cc.PUBLIC_DEPLOYMENT))
    baseline = serving_ref(public, run(PgCatalogDirectory(owner).serving_revision(
        public.serving_version_id)))
    with pgharness.connect(PRESENT_DB) as conn:
        candidate = ref_of(conn, cc.DEV_DEPLOYMENT)                  # ready_private: healthy
        payload = {**d9.policy(d9.uid(43, 0xb0), weights=(1_000,), endpoint=d9.uid(43, 0xe0)),
                   "baseline_ref": baseline,
                   "candidates": [{"serving_ref": candidate, "weight_bp": 1_000}]}
        ref = d7.publish(conn, payload)
        d9.start(conn, ref)
        run(imports.write_once(objects, plan_key(A, payload["policy_id"]), plan(
            horizon_s=1, min_requests=2, max_skew_bp=10_000).model_dump_json().encode()))
        job(conn, ref, payload, d9.uid(1, 0x3f), candidate, "succeeded", ms=20)
    rows = {}
    for login, dsn in logins.items():
        with monkeypatch.context() as m:
            m.setattr(control_app, "NoObjects", lambda: objects)
            answer = unit(m, dsn).get("/lab/v1/releases", params={"provider_org_id": A},
                                      headers={"authorization": f"Bearer {TOKEN}"})
            assert answer.status_code == 200, (login, answer.text)
            rows[login] = {r["policy_ref"]: r for r in answer.json()["data"]["releases"]}[ref]
    shown = rows["lab"]
    assert shown["progress"]["candidate"]["requests"] == 1, shown["progress"]
    assert shown["verdict"]["action"] == "hold" and "no_report" in shown["verdict"]["reasons"], \
        shown["verdict"]
    assert "refused" not in shown and shown == rows["owner"], rows
