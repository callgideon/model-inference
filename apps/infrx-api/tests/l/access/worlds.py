"""The LAB-ACCESS world on two stores: the fake of lab-sql's RPC seam and `PgAccessStore` on
the lane's task-local PostgreSQL (`INFRX_D_TASK=l2`: port 57501, migrations 0001-0027).

Two consumer organizations and two providers. BOTH is in both products: the OWNER of
CONSUMER_1's organization (the grantor to provider A) and a provider-A developer.
CONSUMER_ONLY owns CONSUMER_2's organization (the grantor to provider B) and holds no provider
membership. Grants: CONSUMER_1 -> A for provider_sharing, CONSUMER_2 -> B for capture, both
written at T0 without an expiry; each world then starts a minute later. The clock is the
store's (R7): the fake's injected `now`, the database's `infrx.now()`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import DEFAULT_START
from infrx.contracts.v2 import records as v2
from infrx.lab.access import LabAccess
from infrx.lab.access.fakes import FakeAccessStore
from psycopg.types.json import Jsonb

from tests.d import checks_admission as ca
from tests.d import checks_credit as cc

T0 = DEFAULT_START                    # 2026-09-20 12:00Z: behind any process clock running this
FAR = datetime(2099, 1, 1, tzinfo=timezone.utc)     # ahead of any process clock running this
CONTENT = v2.DataCategory.request_content


class FakeWorld:
    A, B = "a0000000-0000-4000-8000-00000000000a", "b0000000-0000-4000-8000-00000000000b"
    C1, C2 = "c1000000-0000-4000-8000-0000000000c1", "c2000000-0000-4000-8000-0000000000c2"
    DEV_A, DEV_B = "d0000000-0000-4000-8000-00000000000a", "d0000000-0000-4000-8000-00000000000b"
    VIEWER_A = "e0000000-0000-4000-8000-00000000000a"
    BOTH = "f0000000-0000-4000-8000-0000000000ff"
    CONSUMER_ONLY = "f1000000-0000-4000-8000-0000000000f1"
    NAMES = {A: "Provider A", B: "Provider B"}
    MODELS = {A: "marlin-2b", B: "marlin-2b"}
    REVISIONS = {A: ["rev-a"], B: ["rev-b"]}

    def __init__(self) -> None:
        self.store = FakeAccessStore(now=T0, provider_names=dict(self.NAMES))
        for provider, user, role in ((self.A, self.DEV_A, v2.ProviderRole.developer),
                                     (self.B, self.DEV_B, v2.ProviderRole.developer),
                                     (self.A, self.VIEWER_A, v2.ProviderRole.viewer),
                                     (self.A, self.BOTH, v2.ProviderRole.developer)):
            self.store.memberships[(provider, user)] = v2.ProviderMembership(
                provider_org_id=provider, user_id=user, role=role, granted_by="ops",
                granted_at=T0 - timedelta(days=1))
        self.put_grant(self.C1, self.A, v2.DataPurpose.provider_sharing)
        self.put_grant(self.C2, self.B, v2.DataPurpose.capture)
        for provider, (revision,) in self.REVISIONS.items():
            self.store.rows[provider] = [{
                "deployment_revision_id": revision, "window_start": T0 - timedelta(hours=1),
                "window_end": T0, "requests": 40, "errors": 1, "p95_latency_ms": 900}]
        self.advance(60)
        self.access = LabAccess(self.store)

    def now(self) -> datetime:
        return self.store.now

    def advance(self, seconds: float) -> None:
        self.store.now += timedelta(seconds=seconds)

    def freeze(self, at: datetime) -> None:
        self.store.now = at

    def put_grant(self, grantor: str, provider: str, *purposes: v2.DataPurpose,
                  expires_in: timedelta | None = None) -> None:
        """The next version of the pair's grant, effective now (lab_put_access_grant)."""
        prev = self.store.grants.get((grantor, provider))
        now = self.store.now
        self.store.put_grant(v2.AccessGrant(
            grant_id=prev.grant_id if prev else str(uuid.uuid4()),
            version=prev.version + 1 if prev else 1, grantor_org_id=grantor,
            recipient_provider_org_id=provider, model_ids=(self.MODELS[provider],),
            categories=(CONTENT,), purposes=purposes, retention_days=30, effective_at=now,
            expires_at=now + expires_in if expires_in else None))

    def revoke_grant(self, grantor: str, provider: str) -> None:
        self.store.revoke(grantor, provider, self.store.now)

    def revoke_membership(self, provider: str, user: str) -> None:
        key = (provider, user)
        self.store.memberships[key] = self.store.memberships[key].model_copy(
            update={"revoked_at": self.store.now})


class PgWorld:
    """The same world in PostgreSQL (`seed_pg` builds it on a template database). Writes go
    through the lab-sql RPCs, or the platform's own UPDATE for a membership revocation; reads
    go through `LabAccess` over `PgAccessStore`, which runs as `service_role`."""

    A, B = cc.NEMO, cc.OTHER_PROVIDER
    DEV_A, BOTH, CONSUMER_ONLY = cc.PROVIDER_DEV_USER, cc.CONSUMER_1, cc.CONSUMER_2
    DEV_B = "d1000000-0000-4000-8000-00000000000b"
    VIEWER_A = "e1000000-0000-4000-8000-00000000000a"
    OTHER_MODEL = "d0000009-0000-4000-8000-000000000009"
    NAMES = {A: "NemoStation", B: "Other Lab"}
    MODELS = {A: cc.MODEL, B: OTHER_MODEL}

    def __init__(self, conn, dsn: str) -> None:
        from infrx.state.jobstore import connector
        from infrx.state.lab_access import PgAccessStore
        self.conn = conn
        self.C1, self.C2 = (cc.personal_org(conn, user) for user in (self.BOTH, self.CONSUMER_ONLY))
        self.owner = {self.C1: self.BOTH, self.C2: self.CONSUMER_ONLY}
        self.REVISIONS = {provider: [r for (r,) in conn.execute(
            "select distinct j.deployment_revision_id::text from infrx.jobs j join "
            "infrx.deployment_revisions d using (deployment_revision_id) "
            "where d.provider_org_id = %s order by 1", (provider,))]
            for provider in (self.A, self.B)}
        self.access = LabAccess(PgAccessStore(connector(dsn)))

    def now(self) -> datetime:
        return self.conn.execute("select infrx.now()").fetchone()[0]

    def advance(self, seconds: float) -> None:
        self.conn.execute("select infrx_test.advance(%s)", (float(seconds),))

    def freeze(self, at: datetime) -> None:
        self.conn.execute("select infrx_test.freeze(%s)", (at,))

    def _rpc(self, name: str, args: dict) -> None:
        self.conn.execute(f"select infrx.{name}(%s)", (Jsonb(args),))

    def put_grant(self, grantor: str, provider: str, *purposes: v2.DataPurpose,
                  expires_in: timedelta | None = None) -> None:
        expiry = {"expires_at": (self.now() + expires_in).isoformat()} if expires_in else {}
        self._rpc("lab_put_access_grant", {
            "actor_user_id": self.owner[grantor], "grantor_org_id": grantor,
            "recipient_provider_org_id": provider, "model_ids": [self.MODELS[provider]],
            "categories": [CONTENT.value], "purposes": [p.value for p in purposes],
            "retention_days": 30, **expiry})

    def revoke_grant(self, grantor: str, provider: str) -> None:
        self._rpc("lab_revoke_access_grant", {
            "actor_user_id": self.owner[grantor], "grantor_org_id": grantor,
            "recipient_provider_org_id": provider})

    def revoke_membership(self, provider: str, user: str) -> None:
        self.conn.execute("update infrx.provider_memberships set revoked_at = infrx.now() "
                          "where provider_org_id = %s and user_id = %s and revoked_at is null",
                          (provider, user))


def seed_pg(conn, dsn: str) -> None:
    """seed_admission's world (clock frozen at T0), plus provider B's own name and model,
    DEV_B and VIEWER_A, BOTH as an A developer, the two grants and one request by BOTH on A's
    deployment (A's aggregates); then a minute passes."""
    ca.seed_admission(conn)
    assert conn.execute("select infrx.now()").fetchone()[0] == T0
    w = PgWorld
    conn.execute("update infrx.provider_orgs set display_name = %s where provider_org_id = %s",
                 (w.NAMES[w.B], w.B))
    conn.execute(
        "insert into public.models (id, name, provider, description, status, base_url, "
        "served_model, input_usd_per_m, output_usd_per_m, context_tokens, input_modalities, "
        "output_modalities, model_uuid, provider_org_id) values ('other/model', 'o', 'o', "
        "'o', 'live', 'https://o.example', 'o', 1, 1, 1024, '{text}', '{text}', %s, %s)",
        (w.OTHER_MODEL, w.B))
    conn.execute("insert into auth.users (id, email) values (%s, 'dev-b@example.com'), "
                 "(%s, 'viewer-a@example.com')", (w.DEV_B, w.VIEWER_A))
    conn.execute("insert into infrx.provider_memberships (provider_org_id, user_id, role, "
                 "granted_by) values (%s, %s, 'developer', 'ops'), (%s, %s, 'developer', 'ops'), "
                 "(%s, %s, 'viewer', 'ops')", (w.A, w.BOTH, w.B, w.DEV_B, w.A, w.VIEWER_A))
    world = PgWorld(conn, dsn)
    world.put_grant(world.C1, w.A, v2.DataPurpose.provider_sharing)
    world.put_grant(world.C2, w.B, v2.DataPurpose.capture)
    request = ca.credit_request(ca.World(conn), ca.C1_KEY, world.C1)
    ca.admit(conn, request, b.idem(request, "l2-aggregate"), regime="credit")
    world.advance(60)
