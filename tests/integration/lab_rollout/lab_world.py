"""E8L's world: one Lab database on the e8l stack's PostgreSQL (every migration through 0043, the
test clock, D8's seeded registry), the merged R1/R2/R3/B2 code over it, G2's relay on its
contract fakes for the consumer's money, and synthetic endpoints over HTTP - nothing else.

* **Registry and people** (`tests/d/test_d8_requests.seed`): NEMO's alias
  `nemostation/marlin-2b` listed on its prod endpoint (serving version `cc.SERVING`), a second
  serving version `serving_2` (candidate A), C1's grant to NEMO for provider_sharing. Added
  here: a third serving version (candidate B, the A/B leg), and `subjects(n)`: consumer
  organisations owned by C1, each granting NEMO provider_sharing (R1's eligibility is D9's
  `release_eligible`: a current grant from the subject's org), plus the relay world's own
  org (`v2_fakes.IDS.consumer_org`) with the same grant.
* **D9** (0033/0039/0043): a release is launched with `PgReleaseStore.start` (the plan digest
  frozen); R1 reads it through `PgRoutingReleases` on 0021's `infrx_runtime` login (E3C's
  WR-4 step: LOGIN and a fresh password on this cluster only); R2 decides through
  `PgReleaseStore`. `shadow_limit` has no RPC yet (0043: "until an operator raises it"): the
  operator's raise is an owner UPDATE here.
* **L3** (0032): R2's `ServingControl` is L3's own `operations.Serving` over `LabControl` on
  `PgControlStore` and `PgCatalogDirectory`; its `ControlReads` (WR-LSQ-9, 0044) are the real
  `PgControlStore` on the control service's own `infrx_lab_control` login (WR-E8L-3), as
  `pilot.control_serving` composes them.
* **B2 input**: owned case records (`records`), not B1 runs - B1 -> B2 on D7 and MinIO is
  E6L's j05-j07. The runs they name are published D7 eval-run records differing in serving
  only, so the report binds to real refs.
* **Endpoints**: `candidate` - B1's `DevWallet` (provider_dev, funded by the provider) behind
  `/v1/chat/completions`, the shadow duplicate's target; `engine` - a stub answering vLLM's
  `/version` and `/v1/models` for W3's capability probe (the repo's fake_vllm has no
  `/version`).

`NOT_RUN` is the one vocabulary for a case that waits on an unmerged lane: the runner maps
it to NOT RUN, never a pass, and the reason carries the exact rerun command.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import sys
import threading
import types
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
INTEGRATION = HERE.parent
REPO = HERE.parents[2]
API = REPO / "apps" / "infrx-api"
sys.path[:0] = [p for p in (str(INTEGRATION), str(INTEGRATION / "backend"), str(API))
                if p not in sys.path]
import harness  # noqa: E402 - E2's harness, namespace e8l

import stack  # noqa: E402,F401 - conftest and mutants read lab_world.stack

RUNNER = "tests/integration/lab_rollout/runner.py"
RERUN = f"apps/infrx-api/.venv/bin/python {RUNNER} --out <dir>"
DATABASE = f"{harness.PG_DATABASE}_lab"         # infrx_e8l_lab: D1's clock gate needs infrx_%
KEY = "e8l-provider-dev-key"                    # the synthetic endpoint's local bearer
CONTROLLER = "00000090-0000-4000-8000-0000000000e8"
OPERATOR = "00000091-0000-4000-8000-0000000000e8"


def _gate():
    """runner.py by path under a unique name (`runner` is also E3C's module name)."""
    import importlib.util
    if "e8l_runner" not in sys.modules:
        spec = importlib.util.spec_from_file_location("e8l_runner", HERE / "runner.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["e8l_runner"]


ENDPOINT_PORTS = _gate().ENDPOINT_PORTS


def not_run(sid: str, *lanes: str, why: str):
    """Skip as NOT RUN on unmerged lanes, naming the exact rerun (never a pass)."""
    import pytest
    pytest.skip(f"NOT RUN[{','.join(lanes)}] {why}; rerun after the merge: {RERUN} --only {sid}")


def fixture_hashes() -> dict[str, str]:
    """The owned fixtures live in code: the world and the scenario modules."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(HERE.glob("*.py")) if p.name.startswith(("lab_world", "scenarios_"))}


def uid(n: int, tag: int) -> str:
    return f"{tag:08x}-0000-4000-8000-{n:012x}"


def run(coro):
    return asyncio.run(coro)


def save(workdir: Path, name: str, value) -> str:
    """A case artifact beside the verdict; answers its sha256."""
    data = json.dumps(value, indent=1, sort_keys=True, default=str).encode()
    (workdir / name).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------------------ the world
class Lab:
    """The session's world. Built once (`conftest.lab`), torn down at the end."""

    def __init__(self) -> None:
        import psycopg
        from tests.d import checks_credit as cc
        from tests.d import test_d7_lab_data as d7
        from tests.d import test_d8_requests as q8

        import pgstate
        self.cc, self.d7, self.q8 = cc, d7, q8
        self.NEMO, self.C1, self.DEV, self.ALIAS = q8.NEMO, q8.C1, q8.DEV, q8.ALIAS
        self.database = harness.provision_database(DATABASE)
        self.dsn = harness.pg_dsn(DATABASE)
        self.conn = psycopg.connect(self.dsn, autocommit=True)
        self.migrations = pgstate.apply_migrations(self.conn)
        pgstate.install_test_clock(self.conn)
        q8.seed(self.conn)
        self.serving_b, self.label_b = uid(3, 0x5e), "2026-09-28-cand-b"
        self.sql("insert into infrx.serving_versions (serving_version_id, model_version_id, "
                 "model_id, provider_org_id, revision_label, prompt_harness_ref, "
                 "preprocessor_profile_version, runtime_image_ref, runtime_image_digest, "
                 "engine_options_digest, precision, capability, created_by) select %s, "
                 "model_version_id, model_id, provider_org_id, %s, prompt_harness_ref, "
                 "preprocessor_profile_version, runtime_image_ref, runtime_image_digest, "
                 "engine_options_digest, precision, capability, 'e8l' from "
                 "infrx.serving_versions where serving_version_id = %s",
                 self.serving_b, self.label_b, cc.SERVING)
        # WR-E8L-2b (R191/R208): a candidate's serving_ref is L3's own
        # operations.serving_ref identity - a real deployment revision's
        # infrx.lab_serving_ref(deployment_revision_id), never a bare serving version id.
        # serving_2's deployment (q8.W['deployment_2'], private/dev on cc.DEV_ENDPOINT) and
        # its ref (q8.W['candidate_ref']) are q8.seed's own (WR-E8L-2's fixture); serving_b
        # (the A/B leg, this world's own) gets the same treatment here.
        self.deployment_b = uid(4, 0x5e)
        self.sql("insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
                 "provider_org_id, environment, serving_version_id, visibility, state, "
                 "max_input_tokens, max_output_tokens, created_by) values (%s, %s, %s, 'dev', "
                 "%s, 'private', 'ready_private', 30720, 2048, 'e8l')",
                 self.deployment_b, cc.DEV_ENDPOINT, self.NEMO, self.serving_b)
        # The baseline ref is never resolved by `release_active` (only a candidate is, R191/
        # R208) and `q8.policy()` itself always writes `q8.serving_ref(cc.SERVING)` as the
        # stored policy's own `baseline_ref` (opaque, by design - see its docstring): this
        # world's BASE must be that exact same opaque stand-in, or every assignment/report
        # this world records against the baseline no longer matches the policy's own field.
        self.BASE = q8.serving_ref(cc.SERVING)
        self.CAND = q8.W["candidate_ref"]
        self.CAND_B = self.sql("select infrx.lab_serving_ref(%s)", self.deployment_b)[0][0]
        assert self.BASE and self.CAND and self.CAND_B, "0045 resolved no ref for a real row"
        self.PIN = f"{self.ALIAS}@{q8.W['label_2']}"
        self.PIN_B = f"{self.ALIAS}@{self.label_b}"
        self.runtime = runtime_dsn(DATABASE)
        self.control = runtime_dsn(DATABASE, "infrx_lab_control")
        from infrx.contracts.conformance.v2_fakes import IDS
        self.grant_org(IDS.consumer_org, "e8l-relay")
        self._subjects: list[str] = []
        self.policies = 0

    def close(self) -> None:
        self.conn.close()
        if "objects" in self.__dict__:                  # this session's prefix only
            client = harness.s3_client()
            for item in client.list_objects_v2(Bucket=harness.S3_BUCKET,
                                               Prefix=self.prefix).get("Contents", []):
                client.delete_object(Bucket=harness.S3_BUCKET, Key=item["Key"])

    def sql(self, statement: str, *args) -> list[tuple]:
        cursor = self.conn.execute(statement, args)
        return cursor.fetchall() if cursor.description else []

    def ok(self, rpc: str, args: dict):
        return self.q8.ok(self.conn, rpc, args)

    # ------------------------------------------------------------------ subjects (R1)
    def grant_org(self, org_id: str, slug: str) -> str:
        """A consumer org owned by C1 granting NEMO provider_sharing (eligible for R1)."""
        self.sql("insert into public.organizations (id, name, slug, created_by) values "
                 "(%s, %s, %s, %s)", org_id, slug, slug, self.C1)
        self.sql("insert into public.org_members (org_id, user_id, role) values "
                 "(%s, %s, 'owner')", org_id, self.C1)
        self.ok("lab_put_access_grant", {
            "actor_user_id": self.C1, "grantor_org_id": org_id,
            "recipient_provider_org_id": self.NEMO, "model_ids": [self.cc.MODEL],
            "categories": ["request_content"], "purposes": ["provider_sharing"],
            "retention_days": 30})
        return org_id

    def subjects(self, n: int) -> list[str]:
        """`n` eligible subject orgs (made once, shared by the cases)."""
        while len(self._subjects) < n:
            i = len(self._subjects) + 1
            self._subjects.append(self.grant_org(uid(i, 0x5b1), f"e8l-subject-{i}"))
        return self._subjects[:n]

    def ungranted(self) -> str:
        org = uid(1, 0x0e1)
        if not self.sql("select 1 from public.organizations where id = %s", org):
            self.sql("insert into public.organizations (id, name, slug, created_by) values "
                     "(%s, 'ungranted', 'e8l-ungranted', %s)", org, self.C1)
        return org

    # ------------------------------------------------------------------ D9 releases
    def quiesce(self) -> None:
        """Stop every live rollout (one live per endpoint), through D9's own transition."""
        for policy_id, fence in self.sql("select policy_id::text, fence from infrx.lab_rollouts "
                                         "where state in ('running', 'paused', 'approved')"):
            self.ok("lab_rollout_transition", {
                "provider_org_id": self.NEMO, "policy_id": policy_id, "fence": fence,
                "action": "stop", "decided_by": self.DEV, "reason": "e8l case boundary"})

    def policy(self, *, weights=(1000,), mode="canary", cohort="account", candidates=None,
               version=1, policy_id=None) -> dict:
        """A NEMO policy on the alias's prod endpoint (D8's shape): baseline = the listed
        serving version, candidates = serving_2 (and serving_b)."""
        if policy_id is None:
            self.policies += 1
            policy_id = uid(self.policies, 0xb8)
        refs = candidates or (self.CAND, self.CAND_B)
        body = self.q8.policy(0, weights=weights)
        return {**body, "policy_id": policy_id, "version": version, "mode": mode,
                "cohort": cohort, "candidates": [{"serving_ref": r, "weight_bp": w}
                                                 for r, w in zip(refs, weights)]}

    @functools.cached_property
    def objects(self):
        """The Lab objects on the e8l MinIO under this session's own prefix
        (`test/e8l/<uuid>/`): where `launch` stores each release's plan (R241) and what the
        composed releases route (`pilot.lab_releases`, k10) reads."""
        import uuid

        from infrx.media.s3 import S3ObjectStore
        client = harness.s3_client()
        with contextlib.suppress(Exception):            # already there
            client.create_bucket(Bucket=harness.S3_BUCKET)
        self.prefix = f"{harness.OBJECT_PREFIX}{uuid.uuid4().hex}/"
        return S3ObjectStore(client, harness.S3_BUCKET, self.prefix)

    def launch(self, body: dict, plan, *, shadow_limit: int = 0):
        """Publish and start the release with `plan`'s digest frozen, its plan stored beside
        it first (R241: `rollout launch`'s order, so the composed page lists every release
        of this world); (policy, ref)."""
        from infrx.contracts.lab import records as lab
        from infrx.datasets.imports import write_once
        from infrx.lab.workers.__main__ import plan_key
        from infrx.rollouts import control as r2
        self.quiesce()
        ref = self.d7.publish(self.conn, body)
        run(write_once(self.objects, plan_key(self.NEMO, body["policy_id"]),
                       plan.model_dump_json().encode()))
        run(self.releases().start(ref, provider_org_id=self.NEMO,
                                  plan_digest=r2.plan_digest(plan), decided_by=self.DEV,
                                  reason="e8l launch"))
        if shadow_limit:
            self.sql("update infrx.lab_rollouts set shadow_limit = %s where policy_ref = %s",
                     shadow_limit, ref)
        return lab.parse(body), ref

    def releases(self):
        from infrx.state.jobstore import connector
        from infrx.state.lab_rollout import PgReleaseStore
        return PgReleaseStore(connector(self.dsn))

    def routing(self, dsn: str | None = None):
        """R1's port over D9 on the runtime login, every call recorded (`.asked`)."""
        from infrx.state.jobstore import connector
        from infrx.state.lab_rollout import PgRoutingReleases
        return Recorded(PgRoutingReleases(connector(dsn or self.runtime, set_role=False)))

    def assignments(self, ref: str) -> list[dict]:
        return [row[0] for row in self.sql(
            "select to_jsonb(a) from infrx.lab_rollout_assignments a join infrx.lab_rollouts o "
            "using (policy_id) where o.policy_ref = %s order by a.request_id", ref)]

    def decisions(self, ref: str) -> list[tuple]:
        return self.sql("select e.action, e.to_state, e.decided_by::text, e.reasons from "
                        "infrx.lab_rollout_events e join infrx.lab_rollouts o using "
                        "(policy_id) where o.policy_ref = %s and e.action <> 'start' "
                        "order by e.fence", ref)

    def assign_failed_job(self, ref: str, policy_id: str, rid: str, serving: str) -> None:
        """WR-LIVE-K09: one admitted CREDIT job, failed (`engine_error`) 10 ms after its
        admission and settled at 0, that R1 assigned to `serving` under revision `ref` - the
        shape of tests/d/test_code_mutants_live.py's `job`, copied (never imported across
        test packages). A fixture written with `session_replication_role = replica`: D9's
        read (0054) and the pass are under test, not admission or settlement."""
        cc = self.cc
        org = cc.personal_org(self.conn, cc.CONSUMER_1)
        with self.conn.transaction():
            self.conn.execute("set local session_replication_role = replica")
            self.conn.execute(cc.credit_job(rid, f"e8l-{rid}", org, cc.PROVIDER_WALLET))
            self.conn.execute(
                "update infrx.jobs set state = 'failed', outcome_cause = 'engine_error', "
                "settlement_state = 'settled', usage_certainty = 'authoritative', "
                "result_ref = 'r', settled_at = admitted_at + interval '10 milliseconds', "
                "debit = 0 where request_id = %s", (rid,))
            self.conn.execute(
                "insert into infrx.lab_rollout_assignments (policy_id, request_id, policy_ref, "
                "cohort_digest, serving_ref, pinned_by) values (%s, %s, %s, %s, %s, 'cohort')",
                (policy_id, rid, ref, "sha256:" + "c" * 64, serving))

    def controller(self, serving=None):
        from infrx.rollouts import control as r2
        return r2.Controller(self.releases(), serving or self.serving(), actor_id=CONTROLLER)

    # ------------------------------------------------------------------ L3 (the alias)
    def serving(self):
        """R2's `ServingControl`: L3's `operations.Serving` on the real control store."""
        from infrx.lab.control import LabControl
        from infrx.lab.control.operations import Serving
        from infrx.state.catalog import PgCatalogDirectory
        from infrx.state.jobstore import connector
        from infrx.state.lab_control import PgControlStore
        control = LabControl(None, PgControlStore(connector(self.dsn)), None,
                             PgCatalogDirectory(connector(self.dsn)), None)
        # LabControl.rollback reads the session's principal only (the audited actor)
        return Serving(control, PgControlStore(connector(self.control, set_role=False)),
                       types.SimpleNamespace(principal="e8l-controller"))

    async def python_serving_ref(self, deployment_revision_id: str) -> str:
        """L3's own `operations.serving_ref` over the same store 0045's SQL function reads
        (WR-E8L-2b: the two computations must agree byte-for-byte, or a ref this world builds
        with one is not the ref `release_active`/R2 check with the other)."""
        from infrx.lab.control.operations import serving_ref
        from infrx.state.catalog import PgCatalogDirectory
        from infrx.state.jobstore import connector
        from infrx.state.lab_control import PgControlStore
        store, catalog = (PgControlStore(connector(self.dsn)),
                          PgCatalogDirectory(connector(self.dsn)))
        deployment = await store.deployment(deployment_revision_id)
        serving = await catalog.serving_revision(deployment.serving_version_id)
        return serving_ref(deployment, serving)

    def listing(self) -> tuple[int, str]:
        """(version, deployment_revision_id) of the alias's newest listing."""
        version, deployment = self.sql(
            "select version, deployment_revision_id::text from infrx.catalog_listings where "
            "public_model_id = %s order by version desc limit 1", self.ALIAS)[0]
        return version, deployment

    def promote(self, serving_version_id: str, n: int) -> str:
        """The operator's promotion (L3 publish): a public active deployment of the candidate
        on the alias's endpoint, its rate card and a new listing version naming it."""
        endpoint, model = self.cc.PROD_ENDPOINT, self.cc.MODEL
        deployment, card = uid(n, 0xde8), f"rc_e8l_{n}"
        version = self.listing()[0] + 1
        self.sql("insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
                 "provider_org_id, environment, serving_version_id, visibility, state, "
                 "max_input_tokens, max_output_tokens, created_by) values (%s, %s, %s, 'prod', "
                 "%s, 'public', 'active', 30720, 2048, 'e8l')", deployment, endpoint,
                 self.NEMO, serving_version_id)
        self.sql("insert into infrx.rate_card_versions (rate_card_version, model_id, "
                 "deployment_revision_id, serving_version_id, input_rate_per_million, "
                 "output_rate_per_million, effective_at, approved_by, provisional) values "
                 "(%s, %s, %s, %s, 400, 1200, '2026-09-01', 'e8l', true)", card, model,
                 deployment, serving_version_id)
        self.sql("insert into infrx.catalog_listings (public_model_id, version, model_id, "
                 "deployment_revision_id, serving_version_id, rate_card_version, effective_at, "
                 "approved_by) values (%s, %s, %s, %s, %s, %s, '2026-09-01', 'e8l')",
                 self.ALIAS, version, model, deployment, serving_version_id, card)
        return deployment

    def restore_alias(self, version: int) -> None:
        """Put the alias back on listing `version` (L3's own CAS), if it moved on."""
        from infrx.state.jobstore import connector
        from infrx.state.lab_control import PgControlStore
        current = self.listing()[0]
        if current != version:
            run(PgControlStore(connector(self.dsn)).rollback(
                self.ALIAS, to_version=version, expected_version=current, actor="e8l",
                reason="case boundary"))

    # ------------------------------------------------------------------ B2 evidence
    def runs(self, tag: int, base: str, cand: str) -> tuple[dict, dict]:
        """Two published NEMO eval-run records over one dataset and harness, serving `base`
        and then `cand` (H1: a single-factor pair)."""
        d7 = self.d7
        dataset = d7.publish(self.conn, d7.manifest(uid(1, tag), n=1, tag=tag))
        harness_ref = d7.publish(self.conn, d7.harness(uid(3, tag)))
        out = tuple({**d7.eval_run(uid(n, tag), dataset, harness_ref), "serving_ref": ref}
                    for n, ref in ((4, base), (5, cand)))
        for payload in out:
            d7.publish(self.conn, payload)
        return out

    def report(self, runs, candidate: str, protocol: dict):
        """B2's `compare` over the owned case records of `candidate` (see `records`)."""
        from infrx.evaluation import reports
        universe, base = records("baseline")
        _, cand = records(candidate)
        return reports.compare(runs[0], base, runs[1], cand, universe=universe,
                               protocol=protocol)

    def now(self):
        from datetime import datetime
        return datetime.fromisoformat(self.sql("select infrx.now()::text")[0][0])


def runtime_dsn(database: str, role: str = "infrx_runtime") -> str:
    """E3C's WR-4 step: 0021's `infrx_runtime` (or 0043's `infrx_lab_control`) given LOGIN and
    a fresh random password on this namespace's cluster (the operator's out-of-band step, done
    here as the owner)."""
    import secrets
    from urllib.parse import urlsplit

    import psycopg
    from psycopg import sql
    owner, secret = harness.pg_dsn(database), secrets.token_hex(16)
    with psycopg.connect(owner, autocommit=True) as conn:
        conn.execute(sql.SQL("alter role {} login password {}").format(
            sql.Identifier(role), sql.Literal(secret)))
    parts = urlsplit(owner)
    return owner.replace(f"{parts.username}:{parts.password}@", f"{role}:{secret}@", 1)


class Recorded:
    """A port whose every call is logged by name (`asked`)."""

    def __init__(self, port) -> None:
        self._port, self.asked = port, []

    def __getattr__(self, name):
        target = getattr(self._port, name)

        async def call(*args, **kwargs):
            self.asked.append(name)
            return await target(*args, **kwargs)
        return call


# ------------------------------------------------------------------ R2's plan and live
PROTOCOL = {"confidence": 0.95, "margin": 0.05, "min_cases": 10,
            "metric_source": "deterministic_metric",
            "required_slices": {"math": {"margin": 0.0, "min_cases": 5}}}
PLAN = {"horizon_s": 3_600, "min_requests": 20, "max_error_rate": 0.05, "max_p99_ms": 5_000,
        "max_skew_bp": 2_000, "min_quality_coverage": 0.5, "max_lag_s": 300,
        "budget": {"unit": "CREDIT", "value": "10.00000000"}, "protocol": PROTOCOL}


def plan(**changes):
    from infrx.rollouts import control as r2
    return r2.Plan.model_validate({**PLAN, **changes})


def plan_file(workdir: Path) -> Path:
    """The plan file `rollout launch --plan` reads, absolute: the worker runs with cwd=API, so a
    relative `runner.py --out <dir>` would hand it a path that names nothing there (WR-LR5-RV2)."""
    return workdir.resolve() / "plan.json"


def live(counts, policy_id: str, *, now, errors_=0, p99=1_000, covered=None,
         spent="1.00000000", unit="CREDIT", lag_s=0):
    """R2's `Live` from R1's own counts for this policy (the arms the router served), with
    the injected health, latency and spend of the case."""
    from infrx.contracts.lab import records as lab
    from infrx.rollouts import control as r2
    cand, base = counts[(policy_id, "candidate")], counts[(policy_id, "baseline")]
    return r2.Live(observed_until=now - timedelta(seconds=lag_s),
                   baseline=r2.Arm(requests=base, errors=0, p99_ms=1_000),
                   candidate=r2.Arm(requests=cand, errors=errors_, p99_ms=p99),
                   quality_covered=cand if covered is None else covered,
                   spent=lab.Amount(unit=unit, value=spent), candidate_healthy=True)


# ------------------------------------------------------------------ owned case records
MATH, LOOKUP = 12, 28           # 40 cases, two per cluster; math is the required slice


def records(name: str) -> tuple[list[str], list[dict]]:
    """(universe, records) of a synthetic endpoint's run. Declared, not computed from code:
    baseline gets every math case and 4 of 28 lookups (16/40); `improving` gets all 40;
    `regressing` every lookup and no math (28/40: an aggregate gain hiding a math slice
    regression); `thin` only 8 cases answered (the rest missing). Each case costs 0.01 CREDIT
    on the baseline and 0.02 on a candidate."""
    universe = [uid(i, 0xca5e) for i in range(1, MATH + LOOKUP + 1)]
    out = []
    for i, case in enumerate(universe):
        math = i < MATH
        right = {"baseline": math or i < MATH + 4, "improving": True,
                 "regressing": not math, "thin": True}[name]
        if name == "thin" and i >= 8:
            continue
        out.append({"case_id": case, "score": 1.0 if right else 0.0, "cluster": f"c{i // 2}",
                    "slices": ["math" if math else "lookup"], "latency_ms": 100 + i,
                    "costs": [{"unit": "CREDIT",
                               "value": "0.01000000" if name == "baseline" else "0.02000000"}]})
    return universe, out


# ------------------------------------------------------------------ synthetic endpoints
def _bind(make, port: int):
    """`make(port)` on the endpoint's own port, else the first free spare of the block."""
    import errno
    for candidate in (port, *_gate().SPARE_PORTS):
        try:
            return make(candidate), candidate
        except OSError as busy:
            if busy.errno != errno.EADDRINUSE:
                raise
    raise OSError(errno.EADDRINUSE, f"address already in use: {port} and every spare port")


@contextlib.contextmanager
def candidate_endpoint(funded: str = "1000"):
    """The candidate's provider_dev endpoint (B1's `DevWallet`, funded by the provider);
    yields (wallet, HttpDevEndpoint)."""
    from infrx.evaluation.runner import HttpDevEndpoint
    from tests.b.runner.world import RATE_CARD, DevWallet, serve
    wallet = DevWallet(funded, answer=lambda prompt: "candidate output nobody may see")
    server, port = _bind(lambda p: serve(wallet, p, KEY), ENDPOINT_PORTS["candidate"])
    try:
        yield wallet, HttpDevEndpoint(f"http://127.0.0.1:{port}", api_key=KEY,
                                      model="e8l-candidate", rate_card=RATE_CARD)
    finally:
        server.shutdown()
        server.server_close()


class Shadows:
    """R1's `ShadowRunner` for the case: the duplicate goes to the provider-funded candidate
    endpoint over HTTP; whatever comes back is dropped (only `runs` is kept)."""

    def __init__(self, http) -> None:
        self.http, self.runs = http, []

    async def run(self, release, serving_ref, request):
        self.runs.append((serving_ref, request.model_revision))
        await self.http.complete(prompt=f"shadow {request.request_id}", media=[],
                                 tool_results=[], seed=None,
                                 idempotency_key=f"shadow:{request.request_id}:{serving_ref}")


@contextlib.contextmanager
def engine(version: str, models: tuple[str, ...]):
    """A vLLM stub answering `/version` and `/v1/models`; yields its base URL."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            body = {"version": version} if self.path == "/version" else \
                {"object": "list", "data": [{"id": m, "object": "model"} for m in models]}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server, port = _bind(lambda p: ThreadingHTTPServer(("127.0.0.1", p), Handler),
                         ENDPOINT_PORTS["engine"])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
