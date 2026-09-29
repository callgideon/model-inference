#!/usr/bin/env python3
"""WR-LDP-2 + LDP-F1 (b) + LDP-F3: the Lab control unit (`infrx.lab.control.app`, the factory
behind lab-control.callbill.ai) serves every Lab route family - control, datasets,
evaluations, pipelines (teacher batches included), releases/optimizations and, with its key
directory, the checkpoint receiver - on its own `INFRX_LAB_DATABASE_URL` login, through
`gateway.pilot._lab`'s composition. No `LAB_*` switch is read for a family there: the unit is
the switch (R237), so the App gateway keeps every Lab switch OFF.

The fake half: nothing is dialled. `jobstore.connector` is replaced by a recorder whose
connections are refused the way the runtime login was in E4-ON o05 (`permission denied for
function lab_provider_memberships`), and the session verifier accepts one test token.

    uv run --frozen pytest -q tests/i/lab_control

Failure oracles: a family the unit does not mount (404 where a session refusal belongs); a
family mounted without the session dependency; a family composed on the runtime's
`DATABASE_URL` or with `set role service_role` (the Lab login is a member of no role, LDP-F7);
a store failure escaping as a 500 (LDP-F3) or as an untyped body; a mount that still waits on
a `LAB_*` switch; the Lab objects silently absent (a 400 where a 503 belongs).
"""
from __future__ import annotations

import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.gateway import lab_auth
from infrx.lab.control import app as control_app
from infrx.state import jobstore

LAB_DSN = "postgresql://infrx_lab_control@127.0.0.1:1/lab"
RUNTIME_DSN = "postgresql://infrx_runtime@127.0.0.1:2/app"
PROVIDER = "a0000000-0000-4000-8000-000000000001"
TOKEN = "eyJ0.dXNlcg.c2ln"
USER = "e0000000-0000-4000-8000-0000000000a1"
SWITCHES = ("LAB_CONTROL", "LAB_TRACES", "LAB_DATASETS", "LAB_EVALS", "LAB_PIPELINES",
            "LAB_RELEASES", "LAB_CHECKPOINTS", "LAB_TEACHERS", "ROLLOUT_ROUTING")
P = f"?provider_org_id={PROVIDER}"
#: family -> one session read of it (the control unit's whole Lab surface but traces, which
#: needs its ClickHouse backend and is covered by tests/l/control).
FAMILIES = {
    "control": "/lab/v1/control/models" + P,
    "datasets": f"/lab/v1/providers/{PROVIDER}/datasets/versions/ds@1",
    "evaluations": "/lab/v1/evaluations/runs" + P,
    "pipelines": "/lab/v1/pipelines/disagreements" + P + "&dataset_ref=ds@1",
    "teacher-batches": "/lab/v1/pipelines/teacher-batches" + P,
    "releases": "/lab/v1/releases" + P,
    "optimizations": "/lab/v1/optimizations" + P,
}
KEYS = json.dumps({"k1": {"provider_org_id": PROVIDER, "secret": "ab" * 32}})


@pytest.fixture
def unit(monkeypatch):
    """The control unit's environment with every Lab switch OFF, the runtime's DSN present
    (never to be used), and a recording connector: returns the calls `(dsn, set_role)`."""
    for name, value in {control_app.DATABASE_URL: LAB_DSN,
                        control_app.SUPABASE_URL: "http://127.0.0.1:1/",
                        control_app.SUPABASE_KEY: "anon", "DATABASE_URL": RUNTIME_DSN,
                        "SUPABASE_URL": "http://runtime.invalid",
                        "SUPABASE_SERVICE_ROLE_KEY": "service-role"}.items():
        monkeypatch.setenv(name, value)
    for name in SWITCHES:
        monkeypatch.setenv(name, "0")
    for name in ("CLICKHOUSE_URL", "S3_TRACE_BUCKET", "LAB_S3_BUCKET", "LAB_CHECKPOINT_KEYS"):
        monkeypatch.delenv(name, raising=False)
    calls: list[tuple[str, object]] = []

    def connector(dsn, *, set_role=None):
        async def connect():
            calls.append((dsn, set_role))
            raise psycopg.errors.InsufficientPrivilege(
                "permission denied for function lab_provider_memberships")
        return connect

    async def user_id(self, token):
        if token != TOKEN:
            raise errors.InvalidApiKey("not a live session")
        return USER

    monkeypatch.setattr(jobstore, "connector", connector)
    monkeypatch.setattr(lab_auth.GoTrueSessions, "user_id", user_id)
    return calls


def client() -> TestClient:
    return TestClient(control_app.create_app(), raise_server_exceptions=False)


def test_control_routes__every_lab_family_is_mounted_behind_the_session_with_no_switch(unit):
    """Every family answers a call without a session as its session refusal (401), not a 404:
    it is mounted, behind `lab_auth.authenticate`, with every `LAB_*` switch OFF. A path no
    family owns stays a 404, and nothing was dialled for a refused caller."""
    c = client()
    for family, path in FAMILIES.items():
        for headers in ({}, {"authorization": "Bearer sk-infrx-" + "a" * 40}):
            answer = c.get(path, headers=headers)
            assert answer.status_code == 401, (family, answer.status_code, answer.text)
    assert c.get("/lab/v1/nothing" + P).status_code == 404
    assert c.post("/lab/v1/checkpoints", content=b"{}").status_code == 404   # no key directory
    assert unit == []


def test_control_routes__a_session_reaches_each_family_on_the_lab_login_never_the_runtimes(unit):
    """LDP-F1 (b) / LDP-F7: a forwarded session reaches each family's handler, which reads its
    store on `INFRX_LAB_DATABASE_URL` with no `set role` - never the runtime's `DATABASE_URL`
    (the `infrx_runtime` login holds no L2 grant), never `service_role`; `/readyz` too."""
    c = client()
    assert c.get("/readyz").status_code == 503 and unit == [(LAB_DSN, False)]
    for family, path in FAMILIES.items():
        before = len(unit)
        c.get(path, headers={"authorization": f"Bearer {TOKEN}"})
        assert len(unit) > before, f"{family}: the session never reached its store"
    assert set(unit) == {(LAB_DSN, False)}, set(unit)


def test_control_routes__a_failing_store_is_each_familys_typed_503_never_a_500(unit):
    """LDP-F3: a store failure that is not a domain error (here the privilege refusal o05 met)
    is every family's typed 503 - the Lab port's `unavailable`, and the datasets surface's own
    body naming the family - never a 500 and never the database's message."""
    c = client()
    for family, path in FAMILIES.items():
        answer = c.get(path, headers={"authorization": f"Bearer {TOKEN}"})
        assert answer.status_code == 503, (family, answer.status_code, answer.text)
        assert "permission" not in answer.text, family
        expected = {"detail": "the datasets service failed"} if family == "datasets" \
            else {"refusal": "unavailable"}
        assert answer.json() == expected, (family, answer.text)


def test_control_routes__the_checkpoint_receiver_is_mounted_with_its_key_directory(unit,
                                                                                 monkeypatch):
    """The signed receiver (no session: the signing key is the identity) is mounted when the
    unit is given `LAB_CHECKPOINT_KEYS`, whatever `LAB_CHECKPOINTS` says; an unsigned event is
    refused 401 before any store is touched."""
    monkeypatch.setenv("LAB_CHECKPOINT_KEYS", KEYS)
    answer = client().post("/lab/v1/checkpoints", content=b"{}")
    assert (answer.status_code, answer.json()) == (401, {"refusal": "unauthenticated"})
    assert unit == []


def test_control_routes__the_lab_objects_are_the_workers_bucket_or_a_typed_503(unit,
                                                                              monkeypatch):
    """The datasets, pipelines and releases surfaces share the Lab workers' objects
    (`LAB_S3_BUCKET`, `lab_objects`); without the bucket every use is a typed 503
    (`DependencyUnavailable`), never an `AttributeError` the datasets surface renders as 400."""
    rt, _, _ = control_app._compose(control_app._settings(), store=None)
    try:
        rt.lab_datasets.objects.get
        refused = None
    except Exception as died:              # noqa: BLE001 - compared, never a crash
        refused = type(died)
    assert refused is errors.DependencyUnavailable, refused
    from infrx.lab.workers import __main__ as workers
    seen = []
    monkeypatch.setattr(workers, "lab_objects", lambda mode, env: seen.append(mode) or "bucket")
    monkeypatch.setenv("LAB_S3_BUCKET", "lab-bucket")
    rt, _, _ = control_app._compose(control_app._settings(), store=None)
    assert rt.lab_datasets.objects == "bucket" and seen == [control_app.MODE]


def test_control_routes__the_families_verify_sessions_with_the_labs_own_auth_settings(unit):
    """Every family verifies the forwarded session at `INFRX_LAB_SUPABASE_URL` with the Lab's
    publishable key - never the runtime's `SUPABASE_URL` or its service-role key."""
    rt, _, _ = control_app._compose(control_app._settings(), store=None)
    for family in ("lab_datasets", "lab_evaluations", "lab_pipelines", "lab_releases"):
        sessions = getattr(rt, family).sessions
        assert str(sessions.client.base_url) == "http://127.0.0.1:1", family
        assert sessions.apikey == "anon", family
