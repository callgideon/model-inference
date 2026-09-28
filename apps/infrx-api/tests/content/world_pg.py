"""C2's world on real PostgreSQL (WR-C2-5): lab-sql's `PgContentRefs` (0041) over L2's grants
and memberships (`PgAccessStore`, 0028) on the database clock (`infrx.now()`, R7). The
projection, tombstones and objects stay T3's (in memory, or M3's S3ObjectStore on MinIO).

Every case gets its own database, a copy of one template (migrations + the static seed: the
two providers and their models, the users, C1 and C2 with their owners), so each starts as
the in-memory world does: memberships, grants, jobs and traces written through `World`.
The surface the cases read (`store.now/grants/memberships/revoke`, `refs.jobs/refs`) is the
fake's, answered from the tables.

    INFRX_D_TASK=lab-c2 uv run --frozen pytest -q tests/content -k pg
"""
from __future__ import annotations

import asyncio
import os

from psycopg.types.json import Jsonb

from infrx.contracts.v2 import records as v2
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_content import PgContentRefs

from ..d import checks_credit as cc
from ..d import pgharness
from .world import A, B, C1, C2, DEV_A, DEV_A2, DEV_B, MODEL_A, MODEL_B, T0, VIEWER_A, World

KEY = "lab-c2"
OWNERS = {C1: "0c100000-0000-4000-8000-0000000000c1", C2: "0c200000-0000-4000-8000-0000000000c2"}
TEMPLATE = f"{pgharness.DATABASE}_c2_template"
CASE = f"{pgharness.DATABASE}_c2_case"
_built: list[bool] = []


def unavailable() -> str | None:
    if os.environ.get("INFRX_D_TASK") != KEY:
        return f"C2 on PostgreSQL only on its task-local key (INFRX_D_TASK={KEY})"
    return pgharness.unavailable()


def _seed(conn) -> None:
    for user in (DEV_A, DEV_A2, VIEWER_A, DEV_B, *OWNERS.values()):
        conn.execute("insert into auth.users (id, email) values (%s, %s)",
                     (user, f"{user[:8]}-{user[-2:]}@example.com"))
    for org, owner in OWNERS.items():
        conn.execute("insert into public.organizations (id, name, slug, created_by) values "
                     "(%s, %s, %s, %s)", (org, org[:2], f"grantor-{org[:2]}", owner))
        conn.execute("insert into public.org_members (org_id, user_id, role) values "
                     "(%s, %s, 'owner')", (org, owner))
    for provider, model, slug in ((A, MODEL_A, "a"), (B, MODEL_B, "b")):
        conn.execute("insert into infrx.provider_orgs (provider_org_id, slug, display_name, "
                     "created_by) values (%s, %s, %s, 'ops')", (provider, f"c2-{slug}", slug))
        conn.execute(
            "insert into public.models (id, name, provider, description, status, base_url, "
            "served_model, input_usd_per_m, output_usd_per_m, context_tokens, "
            "input_modalities, output_modalities, model_uuid, provider_org_id) values "
            "(%s, 'm', 'p', 'd', 'live', 'https://m.example', 'm', 1, 1, 1024, '{text}', "
            "'{text}', %s, %s)", (f"{slug}/model", model, provider))
    conn.execute("select infrx_test.freeze(%s)", (T0,))


def fresh_database() -> str:
    """A new copy of the seeded template for one case."""
    pgharness.ensure()
    with pgharness.connect("postgres") as admin:
        if not _built:
            admin.execute(f'drop database if exists "{TEMPLATE}" with (force)')
            admin.execute(f'create database "{TEMPLATE}"')
            pgharness.apply(TEMPLATE, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
            with pgharness.connect(TEMPLATE) as conn:
                _seed(conn)
            _built.append(True)
        admin.execute(f'drop database if exists "{CASE}" with (force)')
        admin.execute(f'create database "{CASE}" template "{TEMPLATE}"')
    return CASE


class _Grants:
    """`FakeAccessStore.grants`: (grantor, provider) -> the CURRENT version (R166)."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def get(self, key, default=None):
        rows = self.conn.execute("select infrx.lab_access_grants(%s)", (Jsonb({
            "grantor_org_id": key[0], "recipient_provider_org_id": key[1]}),)).fetchone()[0]
        return v2.AccessGrant.model_validate(rows[-1]) if rows else default

    def __getitem__(self, key):
        grant = self.get(key)
        if grant is None:
            raise KeyError(key)
        return grant


class _Memberships:
    """`FakeAccessStore.memberships`: a write inserts the pair's row, or revokes it."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def __getitem__(self, key) -> v2.ProviderMembership:
        rows = self.conn.execute("select infrx.lab_provider_memberships(%s)", (Jsonb({
            "user_id": key[1]}),)).fetchone()[0]
        mine = [r for r in rows if r["provider_org_id"] == key[0]]
        return v2.ProviderMembership.model_validate(
            {k: v for k, v in mine[-1].items() if k != "provider_name"})

    def __setitem__(self, key, m: v2.ProviderMembership) -> None:
        if m.revoked_at is not None:
            self.conn.execute("update infrx.provider_memberships set revoked_at = %s where "
                              "provider_org_id = %s and user_id = %s and revoked_at is null",
                              (m.revoked_at, *key))
            return
        self.conn.execute("insert into infrx.provider_memberships (provider_org_id, user_id, "
                          "role, granted_by, granted_at) values (%s, %s, %s, %s, %s)",
                          (*key, m.role.value, m.granted_by, m.granted_at))


class PgStore:
    """The access-store surface the cases use: reads on the tables, writes through L2's
    `PgAccessStore` as the grantor organization's owner."""

    def __init__(self, conn, access: PgAccessStore) -> None:
        self.conn, self.access = conn, access
        self.grants, self.memberships = _Grants(conn), _Memberships(conn)

    @property
    def now(self):
        return self.conn.execute("select infrx.now()").fetchone()[0]

    def put_grant(self, grant: v2.AccessGrant) -> None:
        asyncio.run(self.access.put_grant(OWNERS[grant.grantor_org_id], {
            "grantor_org_id": grant.grantor_org_id,
            "recipient_provider_org_id": grant.recipient_provider_org_id,
            "model_ids": list(grant.model_ids), "categories": [str(c) for c in grant.categories],
            "purposes": [str(p) for p in grant.purposes], "retention_days": grant.retention_days,
            **({"expires_at": grant.expires_at.isoformat()} if grant.expires_at else {})}))

    def revoke(self, grantor_org_id: str, provider_org_id: str, at) -> None:
        asyncio.run(self.access.revoke_grant(OWNERS[grantor_org_id], grantor_org_id,
                                             provider_org_id))


class _Jobs:
    """`FakeContentRefs.jobs` on `infrx.jobs`: (org, request) -> (model, created_at). A write
    is the test's own edit of the grantor's job (triggers and pins off for it: the world
    moves the job's model and age, as the fake's dict does)."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def __getitem__(self, key):
        row = self.conn.execute("select model_id::text, created_at from infrx.jobs where "
                                "org_id = %s and request_id = %s", key).fetchone()
        if row is None:
            raise KeyError(key)
        return row

    def __setitem__(self, key, value) -> None:
        (org, request), (model, created_at) = key, value
        with self.conn.transaction():
            self.conn.execute("set local session_replication_role = replica")
            if self.conn.execute("select 1 from infrx.jobs where request_id = %s",
                                 (request,)).fetchone() is None:
                self.conn.execute(cc.credit_job(request, f"job_{request.replace('-', '')}", org,
                                                "00000000-0000-4000-8000-00000000c2c2",
                                                model=model))
            self.conn.execute("update infrx.jobs set model_id = %s, created_at = %s where "
                              "request_id = %s", (model, created_at, request))


class PgRefs(PgContentRefs):
    def __init__(self, conn, connect) -> None:
        super().__init__(connect)
        self.conn, self.jobs = conn, _Jobs(conn)

    @property
    def refs(self) -> dict:
        """sha256(handle) -> the stored row (the handle itself is never stored)."""
        cursor = self.conn.execute("select * from infrx.lab_content_refs")
        names = [d.name for d in cursor.description]
        return {row[names.index("handle_sha256")]: dict(zip(names, row)) for row in cursor}


class PgWorld(World):
    def __init__(self, conn, objects=None) -> None:
        self.conn = conn
        self.connect = connector(pgharness.dsn(CASE))
        super().__init__(objects)

    def _store(self):
        return PgStore(self.conn, PgAccessStore(self.connect))

    def _refs(self):
        return PgRefs(self.conn, self.connect)

    def advance(self, seconds: float) -> None:
        self.conn.execute("select infrx_test.advance(%s)", (seconds,))
