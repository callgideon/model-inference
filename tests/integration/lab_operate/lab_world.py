"""E3L's world: E3C's composed App box (`backend/e3c/world.py`) in namespace `e3l`, plus the
Lab rows the scenarios judge - on the same fresh clone, through the merged Lab code only.

Two providers: A is the seed's NemoStation (it owns the served Marlin model and the private dev
deployment); B is added here with a model of its own. Users: DEV_A and DEV_B (developers),
VIEWER_A (viewer), ADMIN_A and ADMIN_B (administrators), and the two consumer tenants E3C provisions (alpha, beta), who hold no
provider membership. Grants: alpha's organization -> A for provider_sharing (A's model,
request content); beta's organization grants nobody. Writes go through the L2-SQL RPCs as the
granting owner; reads go through `LabAccess` over `PgAccessStore` (service_role) and through
the Lab session door (0030, `public.lab_provider_memberships`) over PostgREST as the signed-in
user - the same door the Lab web calls.

`NOT_RUN` is the one vocabulary for a case that waits on an unmerged lane: the runner maps
it to NOT RUN, never a pass, and the reason carries the exact rerun command.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
INTEGRATION = HERE.parent
E3C = INTEGRATION / "backend" / "e3c"
NAMESPACE = "e3l"
sys.path[:0] = [p for p in (str(E3C), str(INTEGRATION), str(INTEGRATION / "backend"))
                if p not in sys.path]
import world                                            # noqa: E402  E3C's composed box

import stack                                            # noqa: E402

harness = world.harness
RUNNER = "tests/integration/lab_operate/runner.py"
RERUN = f"apps/infrx-api/.venv/bin/python {RUNNER} --out <dir>"
PROVIDER_A, PROVIDER_B = stack.SEED_PROVIDER_ORG, "b0000010-0000-4000-8000-000000000010"
MODEL_A, MODEL_B = stack.SEED_MODEL, "d0000009-0000-4000-8000-000000000009"
DEV_A, DEV_B = "d1000000-0000-4000-8000-00000000000a", "d1000000-0000-4000-8000-00000000000b"
VIEWER_A = "e1000000-0000-4000-8000-00000000000a"
ADMIN_A = "a1000000-0000-4000-8000-00000000000a"      # A's administrator: proposes publication
ADMIN_B = "a1000000-0000-4000-8000-00000000000b"      # B's administrator
OPERATOR = "operator:e3l"                             # the operator session's audited principal
#: the seed's private dev endpoint as a credential names it (E3C s16's PRIVATE_MODEL)
PRIVATE_MODEL = "nemostation/marlin-2b-dev"


def not_run(sid: str, *lanes: str, why: str):
    """Skip as NOT RUN on unmerged lanes, naming the exact rerun (never a pass)."""
    import pytest
    pytest.skip(f"NOT RUN[{','.join(lanes)}] {why}; rerun after the merge: {RERUN} --only {sid}")


def rest_url() -> str:
    """The composed world's PostgREST (`stack.journey_postgrest`), the Lab session door."""
    return stack.postgrest_url(stack.JOURNEY_POSTGREST_PORT)


@contextlib.contextmanager
def lab_trip(workdir, start=()):
    """E3C's composed clone (no box process unless `start` names one) with the Lab seeded."""
    with world.composed(workdir, start=start) as trip:
        seed_lab(trip)
        yield trip


def seed_lab(trip) -> None:
    """Provider B, its model, the three provider users and alpha's grant to A (see above)."""
    alpha = trip.world.alpha
    sql(trip, "insert into infrx.provider_orgs (provider_org_id, slug, display_name, created_by) "
            "values (%s, 'other-lab', 'Other Lab', 'e3l')", PROVIDER_B)
    sql(trip, "insert into public.models (id, name, provider, description, status, base_url, "
            "served_model, input_usd_per_m, output_usd_per_m, context_tokens, input_modalities, "
            "output_modalities, model_uuid, provider_org_id) values ('other/model', 'o', 'o', "
            "'o', 'live', 'https://o.example', 'o', 1, 1, 1024, '{text}', '{text}', %s, %s)",
            MODEL_B, PROVIDER_B)
    sql(trip, "insert into auth.users (id, email, email_confirmed_at) values "
            "(%s, 'dev-a@e3l.invalid', infrx.now()), (%s, 'dev-b@e3l.invalid', infrx.now()), "
            "(%s, 'viewer-a@e3l.invalid', infrx.now()), (%s, 'admin-a@e3l.invalid', "
            "infrx.now()), (%s, 'admin-b@e3l.invalid', infrx.now())",
            DEV_A, DEV_B, VIEWER_A, ADMIN_A, ADMIN_B)
    sql(trip, "insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by, "
            "granted_at) values (%s, %s, 'developer', 'e3l', infrx.now() - interval '1 day'), "
            "(%s, %s, 'developer', 'e3l', infrx.now() - interval '1 day'), "
            "(%s, %s, 'viewer', 'e3l', infrx.now() - interval '1 day'), "
            "(%s, %s, 'administrator', 'e3l', infrx.now() - interval '1 day'), "
            "(%s, %s, 'administrator', 'e3l', infrx.now() - interval '1 day')",
            PROVIDER_A, DEV_A, PROVIDER_B, DEV_B, PROVIDER_A, VIEWER_A, PROVIDER_A, ADMIN_A,
            PROVIDER_B, ADMIN_B)
    grant(trip, alpha, PROVIDER_A, MODEL_A, "provider_sharing")


def sql(trip, statement: str, *args) -> None:
    """One statement on the scenario's clone as its owner (writes; `trip.db` fetches rows)."""
    import psycopg
    with psycopg.connect(harness.pg_dsn(trip.world.database), autocommit=True) as conn:
        conn.execute(statement, args)


def grant(trip, tenant, provider: str, model: str, *purposes: str) -> None:
    from psycopg.types.json import Jsonb
    sql(trip, "select infrx.lab_put_access_grant(%s)", Jsonb({
        "actor_user_id": tenant.user_id, "grantor_org_id": tenant.org_id,
        "recipient_provider_org_id": provider, "model_ids": [model],
        "categories": ["request_content"], "purposes": list(purposes), "retention_days": 30}))


def revoke(trip, tenant, provider: str) -> None:
    from psycopg.types.json import Jsonb
    sql(trip, "select infrx.lab_revoke_access_grant(%s)", Jsonb({
        "actor_user_id": tenant.user_id, "grantor_org_id": tenant.org_id,
        "recipient_provider_org_id": provider}))


def access(trip):
    """L2's `LabAccess` over L2-SQL's `PgAccessStore` on the scenario's clone."""
    from infrx.lab.access import LabAccess
    from infrx.state.jobstore import connector
    from infrx.state.lab_access import PgAccessStore
    return LabAccess(PgAccessStore(connector(harness.pg_dsn(trip.world.database))))


def call(coroutine):
    return asyncio.run(coroutine)


def refused(coroutine) -> str:
    """The typed refusal's class name, or 'allowed'."""
    from infrx.contracts import errors
    try:
        call(coroutine)
    except (errors.NotFound, errors.Forbidden) as denied:
        return type(denied).__name__
    return "allowed"


def refused_as(coroutine) -> str:
    """The typed refusal's class name (any `errors.DomainError`), or 'allowed'."""
    from infrx.contracts import errors
    try:
        call(coroutine)
    except errors.DomainError as denied:
        return type(denied).__name__
    return "allowed"


def rest(rest_url: str, user: str | None, path: str, *, token: str | None = None,
         method: str = "GET"):
    """PostgREST as a signed-in user (their JWT), or with `token` as the bearer."""
    import httpx
    bearer = token if token is not None else stack.jwt("authenticated", user)
    return httpx.request(method, rest_url.rstrip("/") + path, timeout=30.0,
                         headers={"Authorization": f"Bearer {bearer}",
                                  "Content-Type": "application/json"},
                         **({"json": {}} if method == "POST" else {}))


def session_workspaces(rest_url: str, user: str) -> list[str]:
    """What the Lab web's session door answers this user: the provider ids."""
    answer = rest(rest_url, user, "/rpc/lab_provider_memberships", method="POST")
    assert answer.status_code == 200, answer.text[:300]
    return sorted(row["provider_org_id"] for row in answer.json())


def new_id() -> str:
    return str(uuid.uuid4())


# ------------------------------------------------------------------ L3 (E3L-BIND)
# L3's control operations on the scenario's clone: `LabControl` over lab-sql's
# `PgControlStore` (0032) beside A3's `PgRegistry`/`PgCatalogDirectory` - the objects the
# control factory (`infrx.lab.control.app`) composes - with one stand-in, `EngineSmoke`, for the
# engine smoke adapter that is not wired (WR-L3-2: the factory's `NoEngine` answers 503).


class EngineSmoke:
    """WR-L3-2 stand-in: the dev smoke passes when the controlled engine answers its model
    list (the factory's own smoke is unwired). `calls` records the revisions it judged."""

    def __init__(self, base_url: str) -> None:
        self.base_url, self.calls = base_url.rstrip("/"), []

    async def smoke(self, serving, deployment) -> bool:
        import httpx
        self.calls.append(deployment.deployment_revision_id)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return (await client.get(f"{self.base_url}/v1/models")).status_code == 200


def control(trip):
    """L3's `LabControl` on the clone (owner login, as the factory's `INFRX_LAB_DATABASE_URL`)."""
    from infrx.lab.control import LabControl
    from infrx.state.catalog import PgCatalogDirectory
    from infrx.state.jobstore import connector
    from infrx.state.lab_control import PgControlStore
    from infrx.state.operations import PgRegistry
    connect = connector(harness.pg_dsn(trip.world.database))
    return LabControl(access(trip), PgControlStore(connect), PgRegistry(connect),
                      PgCatalogDirectory(connect), EngineSmoke(trip.engine.base_url))


def operator():
    """What `Operations.operator(secret)` hands back: the audited principal only."""
    from infrx.operations.service import OperatorSession
    return OperatorSession(ops=None, principal=OPERATOR)


def serving(ctl, label: str, **update):
    """A new serving revision of A's Marlin: the seed's, under a new id, label and options."""
    import hashlib
    base = call(ctl.catalog.serving_revision(stack.SEED_SERVING))
    return base.model_copy(update={
        "serving_version_id": new_id(), "revision_label": label,
        "engine_options_digest": "sha256:" + hashlib.sha256(label.encode()).hexdigest(),
        **update})


def ready_dev(ctl, label: str):
    """DEV_A registers `label` and validates its private dev revision on endpoint `label`."""
    revision = serving(ctl, label)
    assert call(ctl.register(DEV_A, PROVIDER_A, revision)) is True
    dev = call(ctl.create_dev(DEV_A, PROVIDER_A, serving_version_id=revision.serving_version_id,
                              endpoint_name=label, max_input_tokens=30720,
                              max_output_tokens=2048))
    return revision, call(ctl.validate(DEV_A, PROVIDER_A, dev.deployment_revision_id))


def proposed(ctl, label: str):
    """register -> dev smoke -> ADMIN_A's publication proposal: (revision, proposal)."""
    revision, dev = ready_dev(ctl, label)
    return revision, call(ctl.propose(ADMIN_A, PROVIDER_A, dev.deployment_revision_id,
                                      endpoint_name="marlin-2b"))


def approve(ctl, proposal, label: str, expected_version: int, rates=("300", "900")):
    """The operator's approval of `proposal` at `rates` under the card `card_of(label)`."""
    return ctl.approve(operator(), proposal.deployment_revision_id,
                       rate_card_version=card_of(label), input_rate=rates[0],
                       output_rate=rates[1], expected_version=expected_version,
                       reason="e3l publication")


def ship(ctl, label: str, expected_version: int, rates=("300", "900")):
    """`proposed`, then the operator's approval: (revision, proposal, listing)."""
    revision, proposal = proposed(ctl, label)
    return revision, proposal, call(approve(ctl, proposal, label, expected_version, rates))


def card_of(label: str) -> str:
    return "rc_e3l_" + label.replace("-", "_")


def listing(trip) -> tuple[int, str, str]:
    """(version, deployment_revision_id, rate_card_version) of the alias's newest listing."""
    return tuple(trip.one("select version, deployment_revision_id::text, rate_card_version "
                          "from infrx.catalog_listings where public_model_id = %s "
                          "order by version desc limit 1", stack.CREDIT_ALIAS))


def pins(trip, org_id: str, key: str) -> tuple:
    """(deployment, serving, card, state) the job admitted under `key` was pinned to."""
    return tuple(trip.one("select deployment_revision_id::text, serving_version_id::text, "
                          "rate_card_version, state from infrx.jobs where org_id = %s and "
                          "idempotency_key = %s", org_id, key))


def roll_runtime(trip, card: str, roles=("gateway",)) -> None:
    """The operator's runtime roll R69 requires after a listing moves: the App's processes
    restarted on the card they approve to serve (`ACTIVE_RATE_CARD_VERSION`)."""
    for role in roles:
        trip.box.stop(role)
        trip.box.start(role, ACTIVE_RATE_CARD_VERSION=card)
