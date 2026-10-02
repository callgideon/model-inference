"""WR-I2L-2: the Lab control service, `uvicorn --factory infrx.lab.control.app:create_app`
on 127.0.0.1:8003 (`infra/lab/app/lab.json` `control`).

A thin FastAPI factory, separate from the consumer gateway: `/readyz` (200 while its database
answers `infrx.now()`, else 503) and ONLY the Lab routers, so the gateway keeps LAB_CONTROL off
while this unit serves them (WR-I2L-2b). Composed from its own `INFRX_LAB_*` settings - never
the runtime's `DATABASE_URL` or service-role key - the way `compose.lab_surfaces` composes the
gateway's:
`lab_control` over L3 (`Operations`), and `lab_traces` only when `CLICKHOUSE_URL` and
`S3_TRACE_BUCKET` are set (one without the other is refused by `compose.lab_traces`).
A missing `INFRX_LAB_*` setting refuses startup by name (`RuntimeMisconfigured`).

WR-LDP-2 (R237: the box serves the Lab only from its own units): every other Lab family -
datasets, evaluations, pipelines (teacher batches included), releases/optimizations and,
given `LAB_CHECKPOINT_KEYS`, the checkpoint receiver - is `compose.lab_surfaces`' composition on
this login. No `LAB_*` switch but `LAB_TEACHERS` (P-10 teacher egress, default off) is read for
them: this unit is the switch, so the App gateway keeps every Lab switch OFF. Its connections never `set role` (LDP-F7: the Lab login is a member of
no role). The Lab objects are the Lab workers' (`LAB_S3_BUCKET`); without it, `NoObjects`.
"""
from __future__ import annotations

import dataclasses
import os
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ...config import RuntimeMisconfigured
from ...contracts import errors

MODE = "lab-control"
DATABASE_URL, SUPABASE_URL = "INFRX_LAB_DATABASE_URL", "INFRX_LAB_SUPABASE_URL"
SUPABASE_KEY = "INFRX_LAB_SUPABASE_ANON_KEY"      # the publishable key GoTrue wants as `apikey`
REQUIRED = (DATABASE_URL, SUPABASE_URL, SUPABASE_KEY)


class NoEngine:
    """Until G/W's engine smoke adapter exists (WR-L3-2), a smoke answers 503."""

    async def smoke(self, serving, deployment) -> bool:
        raise errors.DependencyUnavailable("the engine smoke adapter is not wired (WR-L3-2)")


class NoObjects:
    """Without `LAB_S3_BUCKET` every use of the Lab objects is a typed 503."""

    def __getattr__(self, name):
        raise errors.DependencyUnavailable("the Lab objects are not configured (LAB_S3_BUCKET)")


def _settings() -> dict[str, str]:
    values = {name: os.environ.get(name, "").strip() for name in REQUIRED}
    missing = tuple(name for name, value in values.items() if not value)
    if missing:
        raise RuntimeMisconfigured(MODE, missing)
    return values


def _store():
    """The control service's own database login (`INFRX_LAB_DATABASE_URL`, never the
    runtime's `DATABASE_URL`)."""
    from ...state.jobstore import connector
    from ...state.lab_control import PgControlStore
    return PgControlStore(connector(os.environ[DATABASE_URL], set_role=False))


def _compose(lab: dict[str, str], store):
    """`compose.lab_surfaces`' composition on the Lab's own login: L3's operations are the gateway's
    one `lab_operations` (WR-LAB-API-2c); `store` serves `/readyz` only."""
    import httpx

    from ...config import from_env
    from ...gateway.lab_auth import GoTrueSessions
    from ...gateway.routes.lab_control import LabControl as Routes
    from ...state.jobstore import connector
    from ...state.lab_access import PgAccessStore
    from ..access import LabAccess
    from ..compose import lab_operations, lab_traces
    settings, connect = from_env(), connector(lab[DATABASE_URL], set_role=False)
    # ponytail: process-lifetime client, as in `compose.lab_surfaces`.
    sessions = GoTrueSessions(httpx.AsyncClient(base_url=lab[SUPABASE_URL].rstrip("/"),
                                                timeout=httpx.Timeout(5, connect=2)),
                              lab[SUPABASE_KEY])
    access = LabAccess(PgAccessStore(connect))
    operations = lab_operations(connect, access)
    control = Routes(sessions, access, operations)
    pilot = settings.pilot
    traces = lab_traces(settings, connect, sessions, access) \
        if pilot.clickhouse_url.strip() or pilot.s3_trace_bucket.strip() else None
    # WR-1 (AP-08): the judge/review family on the unit's login, only with LAB_JUDGE_API; its
    # doors are `_actors` (the routes answer 503 while IDENTITY_API is off).
    judge = None
    if settings.deployment is not None and settings.deployment.lab_judge_api:
        from ..judge_api.doors import SessionDoors
        from ..judge_api.service import JudgeApi
        judge = JudgeApi(SessionDoors(connect))
    actors = _actors(settings, sessions, connect)
    # WR-AP06-2 (AP-06): the publication door, only with LAB_PUBLICATION, over L3's operations
    # and 0060's receipts on the unit's login (its `lab_control_*`/`control_op_*` grants).
    # Readiness is AP-05's adapter and the dev credentials SR-AP06-1's: 503 until composed.
    publication = None
    if settings.deployment is not None and settings.deployment.lab_publication:
        if actors is None:
            raise RuntimeMisconfigured(MODE, detail="LAB_PUBLICATION needs AP-01's session "
                                       "actors on the unit (IDENTITY_API)")
        from ...gateway.routes.operator_publication import Publication
        from ...state.control_ops import PgControlOps
        publication = Publication(operations, PgControlOps(connect))
    # WR-AP04-2 (AP-04): model projects and artifacts on the unit's login, only with
    # LAB_ARTIFACTS; the Lab objects are required (a missing LAB_S3_BUCKET refuses startup).
    artifacts = None
    if settings.deployment is not None and settings.deployment.lab_artifacts:
        from ..artifacts.compose import surface
        from ..workers.__main__ import lab_objects
        artifacts = surface(connect, lab_objects(MODE, os.environ))
    # WR-AP05-2 (AP-05): private deployments on the unit's login (0060-0062), only with
    # LAB_HOSTING; without a configured slot (`HOSTING_*`) the profile reads `unavailable`.
    hosting = None
    if settings.deployment is not None and settings.deployment.lab_hosting:
        from ...state.control_ops import PgControlOps
        from ..artifacts.store import PgArtifactStore
        from ..compose import lab_control
        from ..hosting import LabHosting
        from ..hosting.controller import target_from
        from ..hosting.store import PgHostingStore
        hosting = LabHosting(access, lab_control(connect, access), PgArtifactStore(connect),
                             PgControlOps(connect), PgHostingStore(connect),
                             target_from(os.environ))
    return SimpleNamespace(settings=settings, clock=time.time, lab_judge=judge, actors=actors,
                           identity=actors and actors.identity,
                           lab_access=access if actors else None,
                           lab_publication=publication, lab_artifacts=artifacts,
                           lab_hosting=hosting, auth_facade=_auth_facade(settings, lab),
                           **_families(settings, lab, connect)), control, traces


def _auth_facade(settings, lab: dict[str, str]):
    """WR-AP09L-2 (AP-09 09c): the auth facade `/auth/v1/*` on the unit's own publishable key, only
    with AUTH_FACADE (default off): the Lab web signs in, refreshes and signs out on LAB_API_URL.
    WEB_ORIGINS names the Lab's origin (mutations and redirects)."""
    deployment = settings.deployment
    if deployment is None or not deployment.auth_facade:
        return None
    import httpx

    from ...auth_facade import AuthFacade
    origins = tuple(o.strip() for o in deployment.web_origins.split(",") if o.strip())
    return AuthFacade(httpx.AsyncClient(base_url=lab[SUPABASE_URL].rstrip("/"),
                                        timeout=httpx.Timeout(10, connect=2)),
                      lab[SUPABASE_KEY], origins=origins,
                      captcha_required=deployment.auth_captcha_required,
                      captcha_provider=deployment.auth_captcha_provider,
                      captcha_site_key=deployment.auth_captcha_site_key)


def _actors(settings, sessions, connect):
    """WR-AP01-2 (AP-01): the unit's own `SessionActors` over 0065's identity functions
    (`state.identity.PgIdentity`) on this login, no API-key door, only with IDENTITY_API (default
    off). One seam: they mount the workspaces, members, capabilities and console account routes
    and open the judge and publication doors; off, the judge family answers 503 and
    LAB_PUBLICATION refuses startup."""
    if settings.deployment is None or not settings.deployment.identity_api:
        return None
    from ...console.session import SessionActors
    from ...state.identity import PgIdentity
    origins = tuple(o.strip() for o in settings.deployment.web_origins.split(",") if o.strip())
    return SessionActors(sessions, PgIdentity(connect), origins=origins)


def _families(settings, lab: dict[str, str], connect) -> dict:
    """WR-LDP-2: `compose.lab_surfaces`' families (control and traces are composed above) with the
    Lab's own session verifier, every family on - the unit is the switch (R237)."""
    from ..compose import lab_checkpoints, lab_surfaces
    from ..workers.__main__ import lab_objects
    deployment = dataclasses.replace(
        settings.deployment, lab_control=False, lab_traces=False, lab_datasets=True,
        lab_evals=True, lab_pipelines=True, lab_releases=True,
        lab_checkpoints=bool(settings.deployment.lab_checkpoint_keys.strip()))
    unit = dataclasses.replace(settings, deployment=deployment,
                               supabase_url=lab[SUPABASE_URL].rstrip("/"),
                               supabase_key=lab[SUPABASE_KEY])
    objects = lab_objects(MODE, os.environ) if os.environ.get("LAB_S3_BUCKET", "").strip() \
        else NoObjects()
    return {**lab_surfaces(unit, connect, objects), **lab_checkpoints(unit, connect)}


def create_app() -> FastAPI:
    lab = _settings()
    store = _store()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/readyz")
    async def readyz():
        try:
            await store.db_now()
        except Exception:                     # noqa: BLE001 - the reason may carry the DSN
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    from ...gateway.routes import (auth, console_me, lab_checkpoints, lab_control, lab_datasets,
                                   lab_evaluations, lab_judge, lab_pipelines, lab_releases,
                                   lab_reviews, lab_traces, lab_workspaces, operator_publication)
    rt, control, traces = _compose(lab, store)
    lab_control.register(app, rt, control)
    lab_traces.register(app, rt, traces)
    for family in (lab_datasets, lab_evaluations, lab_pipelines, lab_releases, lab_checkpoints,
                   lab_judge, lab_reviews, lab_workspaces, console_me, operator_publication,
                   auth):
        family.register(app, rt)
    from ..artifacts.compose import WorkspaceActors, mount   # WR-AP04-2: nothing while off
    mount(app, rt)
    from ...gateway.routes import lab_deployments  # WR-AP05-2: nothing while LAB_HOSTING is off
    lab_deployments.register(app, SimpleNamespace(
        actors=WorkspaceActors(getattr(rt, "actors", None)), lab_hosting=rt.lab_hosting))
    return app
