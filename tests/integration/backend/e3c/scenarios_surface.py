"""E3C s10 (runtime / browser / operator role denials, RV-09 / D-31) and s13 (CATALOG-TRUTH,
RV-01; coordinator update 1).

s10: the database login the box processes actually use cannot reach an owner role or
rewrite money; the Supabase browser roles (`anon`, `authenticated`) can execute no function
and write no table outside the console's granted surface - measured over the catalog and
exercised through PostgREST; an operator credential spends nothing and a consumer credential
operates nothing.

s13: what `GET /v1/models` publishes is compared with what this deployment's admission
does: no zero-data-retention claim while serving stores content, no advertised parameter
admission refuses, no concurrency promise; and, where the tree has F2C-C's projection, no
`violations()` against the running serving profile."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

import stack                                            # noqa: E402

# What 0001/0004/0005/0008 grant the browser roles on purpose (the console's read surface).
BROWSER_FUNCTIONS = {
    "public.is_operator", "public.is_org_member", "public.is_org_owner",
    "public.is_service_client", "public.principal_uuid", "public.visible_principal",
    "public.org_usage_summary", "public.org_usage_daily", "public.org_balance",
    "public.org_wallet_summary", "public.console_wallet_summary",
    "public.console_usage_summary", "public.console_usage_daily",
    "public.console_legacy_usd_statement"}
# 0021 (D10, C0/U4): the signed-in consumer's own reads, auth.uid()-scoped, granted to
# `authenticated` only - never `anon` (anon executing them is still reported).
SIGNED_IN_FUNCTIONS = {"public.consumer_jobs", "public.consumer_job_result"}
BROWSER_WRITES = {("public.profiles", "UPDATE"), ("public.organizations", "UPDATE"),
                  ("public.api_keys", "UPDATE")}


RUNTIME_PROBES = {
    "rewrite a ledger row": "update infrx.credit_ledger set reason = reason where false",
    "delete a job": "delete from infrx.jobs where false",
    "grant itself credit": "insert into public.credit_ledger (org_id, delta_usd, kind, reason) "
                           "select id, 1, 'grant', 'e3c probe' from public.organizations limit 0",
    "DDL in infrx": "create table infrx.e3c_probe (x int)",
}


def test_s10_the_runtime_login_cannot_become_an_owner_or_rewrite_money(workdir):
    """WR-4: the box's DATABASE_URL is D10's dedicated login (0021 `infrx_runtime`, given
    LOGIN on the clone as the operator would). As that login - itself, and after `set role
    service_role` if it can - it is no owner, rewrites no money and runs no DDL (every probe
    rolls back). Then the box must SERVE on it: gateway and worker started on that login, one
    text job succeeds and settles once (else the dedicated login is a probe, not the runtime)."""
    import psycopg
    with world.composed(workdir, start=(), runtime_login=True) as trip:
        dsn, allowed = trip.box.env["DATABASE_URL"], []
        with psycopg.connect(dsn, autocommit=True) as conn:
            who, owner = conn.execute(
                "select session_user, rolsuper or pg_has_role(session_user, (select relowner "
                "from pg_class where oid = 'infrx.credit_ledger'::regclass), 'MEMBER') from "
                "pg_roles where rolname = session_user").fetchone()
            assert who == world.RUNTIME_ROLE, f"the box's login is {who}"
            if owner:
                allowed.append("reach the money tables' owner by `reset role`")
            for what, sql in RUNTIME_PROBES.items():
                for role in (None, "service_role"):
                    try:
                        with conn.transaction():
                            if role:
                                conn.execute(f"set local role {role}")
                            conn.execute(sql)
                            allowed.append(f"{what} (as {role or who})")
                            raise _Rollback
                    except (_Rollback, psycopg.errors.InsufficientPrivilege):
                        pass
        assert not allowed, f"the runtime login can: {allowed} (RV-09 / D-31)"
        secret = dsn.split(":", 2)[2].split("@", 1)[0]
        try:
            for role in ("worker", "gateway"):
                trip.box.start(role)
        except RuntimeError as refused:
            raise AssertionError("the box cannot serve on the dedicated runtime login: "
                                 + str(refused).replace(secret, "***")[-900:]) from None
        answer = trip.send(trip.world.alpha, "sync", world.TEXT, "e3c-s10-runtime")
        assert answer.status_code == 200, answer.text[:300]
        world.settled_once(trip, answer.headers["inference-id"])


class _Rollback(Exception):
    pass


def browser_surface(trip) -> dict:
    """Functions `anon`/`authenticated` can EXECUTE in a schema they can USE, and tables
    they can write, beyond the granted console surface (trigger functions excluded)."""
    rows = trip.db("""
        select r.rolname, n.nspname || '.' || p.proname
          from pg_proc p join pg_namespace n on n.oid = p.pronamespace
          cross join (values ('anon'), ('authenticated')) r(rolname)
         where n.nspname in ('public', 'infrx') and p.prorettype <> 'trigger'::regtype
           and has_schema_privilege(r.rolname, n.oid, 'USAGE')
           and has_function_privilege(r.rolname, p.oid, 'EXECUTE')""")
    writes = trip.db("""
        select r.rolname, n.nspname || '.' || c.relname, w.priv
          from pg_class c join pg_namespace n on n.oid = c.relnamespace
          cross join (values ('anon'), ('authenticated')) r(rolname)
          cross join (values ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE')) w(priv)
         where n.nspname in ('public', 'infrx') and c.relkind in ('r', 'p', 'v')
           and has_schema_privilege(r.rolname, n.oid, 'USAGE')
           and has_table_privilege(r.rolname, c.oid, w.priv)""")
    return {"functions": sorted({(role, fn) for role, fn in rows
                                 if fn not in BROWSER_FUNCTIONS
                                 and not (role == "authenticated" and fn in SIGNED_IN_FUNCTIONS)}),
            "writes": sorted({(role, table, priv) for role, table, priv in writes
                              if not (role == "authenticated"
                                      and (table, priv) in BROWSER_WRITES)})}


def denial(answer) -> str:
    """`<status> <code>: <message>` of a PostgREST answer - the REASON a deny case denied
    (E3A-WR-2: with the hosted auth.uid() a JWT's subject is seen, so a deny must hold for
    its own reason, not because no user was seen)."""
    try:
        body = answer.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    return f"{answer.status_code} {body.get('code', '')}: {str(body.get('message', ''))[:120]}"


def test_s10_the_browser_roles_reach_nothing_outside_the_console_surface(workdir,
                                                                         record_property):
    import httpx
    with world.composed(workdir, start=()) as trip:
        extra = browser_surface(trip)
        rest, alpha = trip.box.env["SUPABASE_URL"], trip.world.alpha
        member = {"Authorization": f"Bearer {stack.jwt('authenticated', alpha.user_id)}"}
        with httpx.Client(base_url=rest, timeout=10.0) as http:
            attempts = {
                "insert credit_ledger": http.post("/credit_ledger", headers=member, json={
                    "org_id": alpha.org_id, "delta_usd": 5, "kind": "grant", "reason": "x"}),
                "api_keys audience -> operator": http.patch(
                    "/api_keys", params={"id": f"eq.{alpha.key_id}"}, headers=member,
                    json={"audience": "operator"}),
                "claim another user's grant": http.post(
                    "/rpc/claim_signup_grant", headers=member,
                    json={"p_user_id": trip.world.beta.user_id}),
            }
        record_property("denials", {what: denial(answer) for what, answer in attempts.items()})
        let_through = {what: answer.status_code for what, answer in attempts.items()
                       if answer.status_code < 400}
        assert not extra["functions"] and not extra["writes"] and not let_through, \
            f"the browser roles reach: {extra}; PostgREST let through {let_through}"


def test_s10_a_signed_in_consumer_reads_its_own_jobs_and_no_others(workdir, record_property):
    """E3A-WR-2 (hosted `auth.uid()`, which reads `request.jwt.claims`): through the journey
    PostgREST, 0021's `consumer_jobs` / `consumer_job_result` answer a tenant's JWT with that
    tenant's job, another tenant's JWT with none of it, and `anon` not at all. On the pinned
    image's `auth.uid()` (the legacy per-claim GUC PostgREST v13 does not set) the member's
    own read is refused 'not signed in' - so every deny above would pass for the wrong
    reason."""
    import httpx
    with world.composed(workdir) as trip:
        alpha, beta = trip.world.alpha, trip.world.beta
        answer = trip.send(alpha, "sync", world.TEXT, "e3c-s10-own")
        assert answer.status_code == 200, answer.text[:300]
        request_id = answer.headers["inference-id"]

        def as_(user) -> dict:
            return {"Authorization": f"Bearer {stack.jwt('authenticated', user.user_id)}"}
        rest = trip.box.env["SUPABASE_URL"]
        with httpx.Client(base_url=rest, timeout=10.0) as http:
            own = http.post("/rpc/consumer_jobs", headers=as_(alpha),
                            json={"p_request_id": request_id})
            other = http.post("/rpc/consumer_jobs", headers=as_(beta),
                              json={"p_request_id": request_id})
            other_result = http.post("/rpc/consumer_job_result", headers=as_(beta),
                                     json={"p_request_id": request_id})
            anon = http.post("/rpc/consumer_jobs", json={"p_request_id": request_id})
        record_property("reads", {"own": denial(own), "other": denial(other),
                                  "other_result": denial(other_result), "anon": denial(anon)})
        assert own.status_code == 200, f"the member's own read: {denial(own)}"
        assert [row["request_id"] for row in own.json()] == [request_id], own.json()
        assert other.status_code >= 400 or other.json() == [], \
            f"another tenant reads the job: {other.json()}"
        assert other_result.status_code >= 400, \
            f"another tenant reads the result: {denial(other_result)}"
        assert anon.status_code >= 400, f"anon reads consumer_jobs: {denial(anon)}"


def test_nc_roles_browser__s10_detects_a_browser_write_grant_on_the_ledger(workdir):
    """Negative control: `authenticated` granted INSERT on the legacy ledger on this clone (a
    one-line defect); s10's browser-surface oracle must report it."""
    with world.composed(workdir, start=()) as trip:
        assert stack.current_database() == trip.world.database
        stack.defect("grant insert on public.credit_ledger to authenticated")
        with pytest.raises(AssertionError, match="the browser roles reach"):
            extra = browser_surface(trip)
            assert not extra["functions"] and not extra["writes"], \
                f"the browser roles reach: {extra}"


def test_s10_operator_and_consumer_credentials_stay_in_their_lane(workdir):
    """A consumer key cannot operate (CLI refuses it); an operator key runs no inference; a
    revoked operator key operates nothing."""
    with world.composed(workdir, start=("worker", "gateway")) as trip:
        alpha = trip.world.alpha
        status, answer = world.cli(trip, "grant", "--user", alpha.user_id, "--idempotency-key",
                                   "g-consumer", "--reason", "e3c", secret=alpha.secret)
        assert status == 1 and answer.get("error") in ("forbidden", "invalid_api_key"), answer
        refused = trip.http.post("/v1/chat/completions", json={
            "model": stack.CREDIT_ALIAS, "messages": world.TEXT},
            headers={"Authorization": f"Bearer {trip.world.operator_secret}"})
        assert refused.status_code in (401, 403), refused.text
        (key_id, org_id), = trip.db("select id::text, org_id::text from public.api_keys where "
                                    "audience = 'operator' and revoked_at is null")
        status, answer = world.cli(trip, "revoke-key", "--org", org_id, "--key-id", key_id,
                                   "--idempotency-key", "r-operator", "--reason", "e3c")
        assert status == 0, answer
        status, answer = world.cli(trip, "grant", "--user", alpha.user_id, "--idempotency-key",
                                   "g-after-revoke", "--reason", "e3c")
        assert status == 1, f"a revoked operator key still operates: {answer}"


# ------------------------------------------------------------------ s13


def discovery(trip) -> dict:
    answer = trip.http.get("/v1/models", headers=trip.headers(trip.world.alpha))
    assert answer.status_code == 200, answer.text
    return answer.json()


# One value per parameter name the validator knows (G7 publishes `capability.parameters` and
# `unsupported_parameters` from `validate.SUPPORTED`/`UNSUPPORTED`). `model`, `messages`
# and `stream` are the request itself.
SAMPLES = {"max_tokens": 8, "max_completion_tokens": 8, "n": 1, "seed": 7, "stop": ["zz"],
           "temperature": 0.5, "top_p": 0.9, "presence_penalty": 0.0,
           "frequency_penalty": 0.0,
           "tools": [{"type": "function", "function": {"name": "f", "parameters": {}}}],
           "tool_choice": "none", "parallel_tool_calls": False,
           "functions": [{"name": "f", "parameters": {}}], "function_call": "none",
           "response_format": {"type": "json_object"}, "logit_bias": {}, "logprobs": True,
           "top_logprobs": 1, "price_snapshot": {}}
ITSELF = {"model", "messages", "stream"}


def test_s13_discovery_claims_nothing_serving_contradicts(workdir):
    """G7's `/v1/models` (F2C-C `PublishedModel` entries): no zero-data-retention claim
    (serving stores payloads, media, results: RV-01), no concurrency promise, a video cap no
    longer than the approved 82 s, and every advertised parameter really accepted by
    admission - every parameter it lists as refused really refused."""
    with world.composed(workdir) as trip:
        doc, problems = discovery(trip), []
        assert doc.get("data"), f"discovery publishes nothing for the served model: {doc}"
        for model in doc["data"]:
            name, cap = model["id"], model["capability"]
            if (model.get("compliance") or {}).get("zdr") or \
                    model["retention"]["zero_data_retention"]:
                problems.append(f"{name}: claims zero data retention")
            if "capacity" in model:
                problems.append(f"{name}: promises a capacity/concurrency")
            if (cap.get("video") or {}).get("max_seconds", 0) > 82:
                problems.append(f"{name}: advertises {cap['video']['max_seconds']} s video")
            for listed, accepted in ((cap["parameters"], True),
                                     (cap["unsupported_parameters"], False)):
                for param in sorted(set(listed) - ITSELF):
                    if param not in SAMPLES:
                        problems.append(f"{name}: `{param}` has no probe (extend SAMPLES)")
                        continue
                    answer = trip.send(trip.world.alpha, "sync", world.TEXT, None,
                                       **{param: SAMPLES[param]})
                    if accepted and answer.status_code == 400:
                        problems.append(f"{name}: advertises `{param}`, admission refuses "
                                        f"it ({world.code(answer)})")
                    if not accepted and answer.status_code != 400:
                        problems.append(f"{name}: lists `{param}` as refused, admission "
                                        f"answers {answer.status_code}")
        assert not problems, problems


def running_profile(trip):
    """The serving profile of the box as it runs: its own environment's settings, the
    served deployment and serving revision read from the clone's catalog, the validator's
    allow-list, the MIME types both the validator and the media profile accept, and the
    modes this box serves (sync, SSE when the revision declares it, async: /v1/jobs)."""
    import asyncio

    from infrx import config
    from infrx.contracts.records import ExecutionMode
    from infrx.contracts.v2 import published_model as pm
    from infrx.contracts.v2.records import CredentialAudience
    from infrx.gateway.routes import validate
    from infrx.media.prepare import MediaProfile
    from infrx.state.catalog import PgCatalogDirectory
    from infrx.state.jobstore import connector
    settings = config.from_env(trip.box.env)
    catalog = PgCatalogDirectory(connector(stack.harness.pg_dsn(trip.world.database)))

    async def rows():
        deployment = await catalog.resolve(settings.model_id,
                                           audience=CredentialAudience.consumer,
                                           endpoint_id=None)
        return deployment, await catalog.serving_revision(deployment.serving_version_id)
    deployment, serving = asyncio.run(rows())
    modes = [ExecutionMode.sync, ExecutionMode.async_] + \
        ([ExecutionMode.stream] if serving.capability.stream_output else [])
    return pm.serving_profile(
        limits=settings.pilot, deployment=deployment, serving=serving,
        parameters=validate.SUPPORTED, refused=validate.UNSUPPORTED,
        video_mime=set(settings.allowed_video_mime) & set(MediaProfile().allowed_mime),
        fps=int(settings.fps), min_frames=settings.min_frames,
        max_frames=settings.max_frames, max_pixels_per_frame=settings.px_per_frame,
        execution_modes=modes)


def test_s13_discovery_matches_the_running_serving_profile(workdir):
    """F2C-C's `violations(published, profile)` is empty for every entry G7 serves, against
    the approved release profile (82 s, `published_fixtures.deployed_profile`) AND against
    the profile of the box actually running (its environment and the clone's catalog)."""
    from infrx.contracts.v2 import published_model as pm
    from infrx.contracts.v2.published_fixtures import deployed_profile
    with world.composed(workdir) as trip:
        entries = [pm.PublishedModel.model_validate(e) for e in discovery(trip)["data"]]
        assert entries, "discovery publishes nothing for the served model"
        running, problems = running_profile(trip), []
        for entry in entries:
            for label, profile in (("approved", deployed_profile()), ("running", running)):
                problems += [f"{entry.id} vs {label}: {v}"
                             for v in pm.violations(entry, profile)]
        assert not problems, problems
