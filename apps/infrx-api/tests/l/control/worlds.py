"""The LAB-PUBLISH world: L2's two-provider LAB-ACCESS world (`tests/l/access/worlds.py`) plus
the registry A3 seeded, on two stores - the fake of lab-sql's L3-SQL seam (`FakeControl`) and,
marked `pg`, `PgControlStore` beside A3's own `PgRegistry`/`PgCatalogDirectory` on the lane's
task-local PostgreSQL (`INFRX_D_TASK=l3`: port 57502).

Provider A is NemoStation: Marlin (`nemostation/marlin-2b`) listed at version 1 on its active
public prod deployment, priced by the provisional card (the v2 fixtures = the operator seed).
Provider B has a model and nothing deployed. ADMIN_A/ADMIN_B administer them; DEV_A, BOTH and
DEV_B are developers, VIEWER_A a viewer, CONSUMER_ONLY a consumer with no provider membership.
The engine is fake in both worlds (L3's oracle: a real registry and a fake engine).
"""
from __future__ import annotations

from infrx.auth.context import auth_context
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2 import ports as v2ports
from infrx.contracts.v2 import records as v2
from infrx.lab.control import LabControl
from infrx.lab.control.fakes import FakeControl, FakeEngine
from infrx.operations.service import OperatorSession

from tests.l.access import worlds as access

IDS = v2fix.IDS
ALIAS = v2fix.PUBLIC_MODEL_ID
OPERATOR = OperatorSession(ops=None, principal="operator:ops-1")     # what Operations.operator
#: A platform operator's Lab session (`public.profiles.is_operator`): E3L-F4's rejection door.
OPS_USER = "e1000000-0000-4000-8000-0000000000f0"
CONSUMER = auth_context(audience="consumer", org_id=IDS.consumer_org,  # (secret) hands back
                        key_id=IDS.consumer_key, user_id=IDS.consumer_user)


def built(name: str):
    return v2fix.BUILDERS[name]()


def serving(w, label: str, *, provider: str | None = None, model: str | None = None,
            **update) -> v2.ServingRevision:
    """A new serving revision of A's Marlin (same artifact, a new label and identity)."""
    base = built("serving_revision.json")
    return base.model_copy(update={
        "serving_version_id": w.new_id(label), "revision_label": label,
        "provider_org_id": provider or base.provider_org_id, "model_id": model or base.model_id,
        "engine_options_digest": "sha256:" + label.encode().hex().ljust(64, "0")[:64],
        **update})


def pin(w, requested_model: str, auth: v2.AuthContextV2 = CONSUMER) -> v2.AdmissionPins:
    """What admission pins for this caller now (`pin_admission` over the catalog)."""
    import asyncio

    async def go():
        catalog = w.catalog
        deployment = await catalog.resolve(requested_model, audience=auth.audience,
                                           endpoint_id=auth.endpoint_id)
        if deployment is None:
            return v2ports.pin_admission(auth=auth, requested_model=requested_model,
                                         deployment=None, serving=None, rate_card=None,
                                         policy=None)
        return v2ports.pin_admission(
            auth=auth, requested_model=requested_model, deployment=deployment,
            serving=await catalog.serving_revision(deployment.serving_version_id),
            rate_card=await catalog.active_rate_card(deployment.deployment_revision_id),
            policy=await catalog.data_access_policy(deployment.deployment_revision_id))[0]
    return asyncio.run(go())


class FakeWorld(access.FakeWorld):
    OPS_USER = OPS_USER
    A, MODELS = IDS.provider_org, {IDS.provider_org: IDS.model,
                                   access.FakeWorld.B: "d0000009-0000-4000-8000-000000000009"}
    NAMES = {A: "NemoStation", access.FakeWorld.B: "Other Lab"}
    REVISIONS = {A: [IDS.prod_deployment], access.FakeWorld.B: ["rev-b"]}
    ADMIN_A = "e0000000-0000-4000-8000-0000000000a1"
    ADMIN_B = "e0000000-0000-4000-8000-0000000000b1"

    def __init__(self) -> None:
        super().__init__()
        for provider, user in ((self.A, self.ADMIN_A), (self.B, self.ADMIN_B)):
            self.store.memberships[(provider, user)] = v2.ProviderMembership(
                provider_org_id=provider, user_id=user, role=v2.ProviderRole.administrator,
                granted_by="ops", granted_at=access.T0)
        prod, card = built("deployment_revision_public.json"), built("rate_card_marlin.json")
        self.control_store = FakeControl(
            clock=self.store, slugs={self.A: "nemostation", self.B: "other-lab"},
            models={self.MODELS[self.A]: (ALIAS, self.A),
                    self.MODELS[self.B]: ("other/model", self.B)},
            policy=built("data_access_policy.json"))
        c = self.control_store
        c.endpoints[prod.endpoint_id] = (self.A, "marlin-2b", v2.Environment.prod)
        for row in (built("serving_revision.json"), prod, card):
            c.put_now(row)
        c.list_now(ALIAS, prod.deployment_revision_id, card.rate_card_version)
        c.operators = {OPS_USER}
        self.engine = FakeEngine()
        self.catalog = self.wallets = c
        self.control = LabControl(self.access, c, c, c, self.engine)
        self._ids = 0

    def new_id(self, _label: str) -> str:
        self._ids += 1
        return f"a5000000-0000-4000-8000-{self._ids:012d}"

    def key_scope(self, key_id: str) -> tuple[str, str, str]:
        row = self.control_store.keys[key_id]
        return row["provider_org_id"], row["endpoint_id"], row["key_hash"]

    def retire(self, deployment_revision_id: str) -> None:
        """The platform retiring a deployment outside L3 (0007's service_role UPDATE), as
        `PgControlStore` reads it back: a retired revision is never public (E3L-F4)."""
        c = self.control_store
        c.deployments[deployment_revision_id] = c.deployments[deployment_revision_id] \
            .model_copy(update={"state": v2.DeploymentState.retired,
                                "visibility": v2.Visibility.private})


class _PgRows:
    """The PG world's `control_store`: lab-sql's `PgControlStore` itself (its `ControlReads`,
    WR-LSQ-9, are what `Operations`/`Serving` take) plus the few row views the cases read on
    the fake (`servings`, `deployments`, `listings`, `endpoints`, `audit`), read back from
    the stored rows - through the ControlReads where one exists."""

    def __init__(self, store, conn, providers) -> None:
        self.store, self.conn, self.providers = store, conn, providers

    def __getattr__(self, name):                      # the ControlReads and the rest
        return getattr(self.store, name)

    def _all(self, read) -> dict:
        import asyncio
        return {getattr(r, "serving_version_id" if read == "provider_servings"
                        else "deployment_revision_id"): r
                for p in self.providers for r in asyncio.run(getattr(self.store, read)(p))}

    @property
    def servings(self) -> dict:
        return self._all("provider_servings")

    @property
    def deployments(self) -> dict:
        return self._all("provider_deployments")

    @property
    def listings(self):
        import asyncio
        store = self.store

        class Listings:
            def __getitem__(self, alias):
                return asyncio.run(store.listing_versions(alias))
        return Listings()

    @property
    def endpoints(self) -> dict:
        return {e: (p, n, v2.Environment(env)) for e, p, n, env in self.conn.execute(
            "select endpoint_id::text, provider_org_id::text, name, environment "
            "from infrx.endpoints")}

    @property
    def audit(self) -> list:
        from types import SimpleNamespace
        return [SimpleNamespace(action=a, actor=b) for a, b in self.conn.execute(
            "select action, actor from infrx.lab_control_events order by event_id")]


class PgWorld(access.PgWorld):
    """The same world in PostgreSQL. Reads go through `LabControl` over `PgControlStore`
    (L3-SQL), A3's `PgRegistry`/`PgCatalogDirectory` and `PgWalletDirectory`."""

    from tests.d import checks_credit as _cc
    ADMIN_A = _cc.PROVIDER_ADMIN_USER
    ADMIN_B = "e1000000-0000-4000-8000-0000000000b1"
    OPS_USER = OPS_USER

    def __init__(self, conn, dsn: str) -> None:
        super().__init__(conn, dsn)
        from infrx.state.catalog import PgCatalogDirectory
        from infrx.state.jobstore import connector
        from infrx.state.lab_control import PgControlStore
        from infrx.state.operations import PgRegistry, PgWalletDirectory
        connect = connector(dsn)
        self.catalog, self.wallets = PgCatalogDirectory(connect), PgWalletDirectory(connect)
        self.engine = FakeEngine()
        self.control_store = _PgRows(PgControlStore(connect), conn, (self.A, self.B))
        self.control = LabControl(self.access, self.control_store.store, PgRegistry(connect),
                                  self.catalog, self.engine)
        self._ids = 0

    def new_id(self, _label: str) -> str:
        self._ids += 1
        return f"a5000000-0000-4000-8000-{self._ids:012d}"

    def key_scope(self, key_id: str) -> tuple[str, str, str]:
        row = self.conn.execute("select provider_org_id::text, endpoint_id::text, key_hash "
                                "from public.api_keys where id = %s", (key_id,)).fetchone()
        return tuple(row)

    def retire(self, deployment_revision_id: str) -> None:
        self.conn.execute("update infrx.deployment_revisions set state = 'retired' "
                          "where deployment_revision_id = %s", (deployment_revision_id,))


def seam_missing() -> str | None:
    """Why the pg half cannot run yet: the ControlStore class, not just the module (lab-sql's
    committed 0032 ships `PgLabControlStore`, a different surface - WR-L3-1)."""
    try:
        import infrx.state.lab_control as lab_control
    except ImportError:
        return "L3-SQL is not merged: infrx.state.lab_control is absent"
    if getattr(lab_control, "PgControlStore", None) is None:
        return ("L3-SQL's ControlStore seam is absent: infrx.state.lab_control has no "
                "PgControlStore (WR-L3-1 unreconciled; the pg half is not claimed)")
    return None


def seed_pg(conn, dsn: str) -> None:
    """L2's seed (A = NemoStation with the operator seed, B = Other Lab) plus B's
    administrator and a platform operator; A's administrator is the credit seed's."""
    access.seed_pg(conn, dsn)
    conn.execute("insert into auth.users (id, email) values (%s, 'admin-b@example.com'), "
                 "(%s, 'ops@example.com')", (PgWorld.ADMIN_B, OPS_USER))
    conn.execute("update public.profiles set is_operator = true where id = %s", (OPS_USER,))
    conn.execute("insert into infrx.provider_memberships (provider_org_id, user_id, role, "
                 "granted_by) values (%s, %s, 'administrator', 'ops')",
                 (PgWorld.B, PgWorld.ADMIN_B))
