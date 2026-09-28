"""E7L's world: one Lab database on the e7l stack's PostgreSQL (every migration, the test clock,
D7's seed), E2's MinIO through `S3ObjectStore`, the merged N1/N2/N3/P1/P2/P3/H1/B1/B2 code, and
host processes inside the e7l block - nothing else.

* **People and grants** (D7's seed, `checks_credit`/`test_l2sql_access`): NEMO's developer DEV
  reviews and trains, its administrator ADMIN assigns reviews. CONSUMER_1's grant to NEMO is
  the handle the owned benchmark is registered under (the content is ours,
  `ownership: provider_owned`); it is widened here to external_judging (a teacher batch needs
  it and training). CONSUMER_2 (`TRACER`) grants NEMO its request, response and feedback
  content of NEMO's model: iteration 2's post-deployment traces. RACER grants NEMO request
  and response content: the traces i04 revokes, so a revocation touches nothing else.
* **Fixtures** (`fixtures/improve.*`): the owned benchmark (train/validation/holdout declared)
  and every expectation - human label rows, the teacher's answers, the corrections, the
  export each iteration must produce, each synthetic endpoint's right answers, each
  checkpoint's reported training loss, the traces and the decisions. The expectations are
  the fixture's, never the implementation's.
* **Stand-ins** (said so in the verdict): D8's `LabelLog`/`RunLedger` and J2/D6J's judge
  ledger are the P lanes' fakes (`FakeLabelLog`, `FakeRunLedger`, `FakeJudgeLedger`): the
  lab-sql-integration-2 lane swaps them for `infrx.state.lab_pipeline`'s adapters. P3's
  `Evaluations` port (B3's, not on the base: WR-E7L-1) is `Evaluations` below over the real
  B1 runner. N3's content port is N3's `FakeContent` over the real L2 directory and T3's
  real `Retention` (C2's adapter is not composed: WR-N3-1); the T3 projections are in memory
  (no ClickHouse projection writer on this key).
* **Endpoints**: B1's `DevWallet` served as `/v1/chat/completions` on
  `runner.ENDPOINT_PORTS`, reached through B1's `HttpDevEndpoint`; each answers a benchmark
  question right only for the ids the fixture declares for it.

`NOT_RUN` is the one vocabulary for a case that waits on an unmerged lane: the runner maps
it to NOT RUN, never a pass, and the reason carries the exact rerun command.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import hashlib
import json
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
INTEGRATION = HERE.parent
REPO = HERE.parents[2]
API = REPO / "apps" / "infrx-api"
FIXTURES = HERE / "fixtures"
sys.path[:0] = [p for p in (str(INTEGRATION), str(INTEGRATION / "backend"), str(API))
                if p not in sys.path]
import harness  # noqa: E402 - E2's harness, namespace e7l

import stack                                            # noqa: E402

RUNNER = "tests/integration/lab_improve/runner.py"
RERUN = f"apps/infrx-api/.venv/bin/python {RUNNER} --out <dir>"
DATABASE = f"{harness.PG_DATABASE}_lab"         # infrx_e7l_lab: D1's clock gate needs infrx_%
KEY = "e7l-provider-dev-key"                    # the synthetic endpoints' local bearer
DECL = json.loads((FIXTURES / "improve.json").read_text())
ROWS = [json.loads(line) for line in (FIXTURES / "improve.jsonl").read_text().splitlines()]
BY_Q = {row["q"]: row for row in ROWS}
CONFIG = {"objective": "sft", "adaptation": "lora", "base_model": "marlin-2b",
          "hyperparameters": {"epochs": 1}, "environment": {"trainer": "provider-own"}}


def _gate():
    """runner.py by path under a unique name (`runner` is also E3C's module name)."""
    import importlib.util
    if "e7l_runner" not in sys.modules:
        spec = importlib.util.spec_from_file_location("e7l_runner", HERE / "runner.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["e7l_runner"]


ENDPOINT_PORTS, SERVICE_PORTS = _gate().ENDPOINT_PORTS, _gate().SERVICE_PORTS


def not_run(sid: str, *lanes: str, why: str):
    """Skip as NOT RUN on unmerged lanes, naming the exact rerun (never a pass)."""
    import pytest
    pytest.skip(f"NOT RUN[{','.join(lanes)}] {why}; rerun after the merge: {RERUN} --only {sid}")


def fixture_hashes() -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(FIXTURES.iterdir()) if p.is_file()}


def uid(n: int, tag: int) -> str:
    return f"{tag:08x}-0000-4000-8000-{n:012x}"


def run(coro):
    return asyncio.run(coro)


def pin_of(ids) -> str:
    """P3's bundle pin of a holdout: the digest of its sorted ids (never the ids' content)."""
    from infrx.contracts.lab import records
    return hashlib.sha256(records.canonical(sorted(ids))).hexdigest()


# ------------------------------------------------------------------------------ the world
class Lab:
    """The session's world. Built once (`conftest.lab`), torn down at the end."""

    def __init__(self) -> None:
        import psycopg
        from infrx.contracts import errors
        from infrx.contracts.v2 import records as v2
        from infrx.contracts.v2.money_units import ProviderUsd
        from infrx.lab.access import LabAccess
        from infrx.media.s3 import S3ObjectStore
        from infrx.state.feedback import PgFeedbackService
        from infrx.state.jobstore import connector
        from infrx.state.lab_access import PgAccessStore
        from infrx.state.lab_data import PgLabDataStore, grant_ref

        import pgstate
        from tests.d import checks_credit as cc
        from tests.d import test_d7_lab_data as d7
        from tests.d import test_l2sql_access as l2
        from tests.j import fakes as j1
        from tests.j.submit import fakes as j2
        from tests.p.annotations.world import FakeLabelLog
        from tests.p.training.world import FakeRunLedger
        self.cc, self.d7, self.l2, self.j1, self.errors = cc, d7, l2, j1, errors
        self.NEMO, self.DEV, self.ADMIN = cc.NEMO, cc.PROVIDER_DEV_USER, cc.PROVIDER_ADMIN_USER
        self.C1, self.TRACER, self.RACER = cc.CONSUMER_1, cc.CONSUMER_2, cc.RACER
        self.database = harness.provision_database(DATABASE)
        self.dsn = harness.pg_dsn(DATABASE)
        self.conn = psycopg.connect(self.dsn, autocommit=True)
        self.migrations = pgstate.apply_migrations(self.conn)
        pgstate.install_test_clock(self.conn)
        d7.seed(self.conn)
        self.conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                          "values ('feedback', true, 'e7l', 'D6F rows behind N3') on conflict "
                          "(name) do update set enabled = true")
        widened = l2.ok(self.conn, "lab_put_access_grant", l2.scope(
            self.conn, purposes=["provider_sharing", "training", "external_judging"]))
        self.grant = grant_ref(v2.AccessGrant.model_validate(widened))
        self.put_trace_grant(self.TRACER)
        self.put_trace_grant(self.RACER)
        for name in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_ENDPOINT_URL",
                     "AWS_ENDPOINT_URL_S3"):
            os.environ.pop(name, None)
        os.environ.update(stack.s3_env())               # MinIO's local literals only
        self.s3 = harness.s3_client()
        with contextlib.suppress(Exception):            # already there
            self.s3.create_bucket(Bucket=harness.S3_BUCKET)
        self.prefix = f"{harness.OBJECT_PREFIX}{uuid.uuid4().hex}/"
        self.objects = S3ObjectStore.connect(harness.S3_BUCKET, self.prefix,
                                             harness.s3_endpoint())
        self.store = PgLabDataStore(connector(self.dsn))
        self.directory = PgAccessStore(connector(self.dsn))
        self.access = LabAccess(self.directory)
        self.feedback_service = PgFeedbackService(connector(self.dsn))
        self.log, self.runs = FakeLabelLog(), FakeRunLedger()
        self.payer = f"lab:payer:{self.NEMO}:{uid(1, 0x9a7)}@sha256:{'a' * 64}"
        self.rubric = f"lab:rubric:{self.NEMO}:{uid(1, 0xcb7)}@sha256:{'c' * 64}"
        self.teacher_budget = ProviderUsd("50.00000000")
        self.judge = j2.FakeJudgeLedger({self.payer: self.teacher_budget},
                                        now=self.db_now())
        self.teacher = self.teacher_fake()
        self.evals = Evaluations(self)
        self.frozen: dict[tuple[str, str], object] = {}
        self.wallets: list = []
        self.trace_rows: list = []
        self.cache: dict = {}                      # the scenarios' memoized stages

    def close(self) -> None:
        self.teacher.close()
        listed = self.s3.list_objects_v2(Bucket=harness.S3_BUCKET, Prefix=self.prefix)
        for item in listed.get("Contents", []):
            self.s3.delete_object(Bucket=harness.S3_BUCKET, Key=item["Key"])
        self.conn.close()

    # ---------------------------------------------------------------------- db and grants
    def sql(self, statement: str, *args) -> list[tuple]:
        cursor = self.conn.execute(statement, args)
        return cursor.fetchall() if cursor.description else []

    def db_now(self):
        return self.sql("select infrx.now()")[0][0]

    def put_trace_grant(self, owner: str, *, categories=None) -> dict:
        """(Re)grant NEMO the owner's request/response (+ feedback for TRACER) content of
        NEMO's model for provider_sharing and training."""
        if categories is None:
            categories = ["request_content", "response_content"] + (
                ["feedback"] if owner == self.TRACER else [])
        return self.l2.ok(self.conn, "lab_put_access_grant", self.l2.scope(
            self.conn, owner=owner, categories=categories,
            purposes=["provider_sharing", "training"]))

    def revoke(self, owner: str) -> None:
        self.l2.ok(self.conn, "lab_revoke_access_grant", {
            "actor_user_id": owner, "grantor_org_id": self.l2.org(self.conn, owner),
            "recipient_provider_org_id": self.NEMO})

    def org(self, user: str) -> str:
        return self.l2.org(self.conn, user)

    # ----------------------------------------------------------------- the benchmark (N1)
    def import_benchmark(self, n: int = 1):
        from infrx.datasets.imports import FORMAT, Importer
        spec = {"format": FORMAT, "provider_org_id": self.NEMO, "import_id": uid(n, 0x1e7),
                "dataset_id": uid(n, 0xda7), "version": 1, "created_at": "2026-09-28T10:00:00Z",
                "grant_ref": self.grant, **DECL["spec"]}
        data = (FIXTURES / "improve.jsonl").read_bytes()

        async def pieces():
            for start in range(0, len(data), 97):
                yield data[start:start + 97]
        return run(Importer(self.store, self.objects).run(
            spec, pieces(), provider_org_id=self.NEMO, actor="dev@nemo"))

    @functools.cached_property
    def benchmark(self) -> str:
        return self.import_benchmark().dataset_ref

    def manifest(self, ref: str):
        return run(self.store.resolve(ref, provider_org_id=self.NEMO))

    def body(self, sample) -> dict:
        from infrx.datasets.imports import sample_key
        return json.loads(run(self.objects.get(sample_key(self.NEMO, sample.content_digest))))

    def ids(self, ref: str) -> dict[str, str]:
        """fixture row id -> sample id, over the benchmark rows of a version."""
        return {self.body(s)["original"]["id"]: s.sample_id for s in self.manifest(ref).samples
                if "id" in (self.body(s).get("original") or {})}

    def splits(self, ref: str) -> dict[str, str]:
        manifest = self.manifest(ref)
        return {i: name for name in ("train", "validation", "holdout")
                for i in getattr(manifest.splits, name)}

    # --------------------------------------------------------------------- labels (P1, P2)
    def import_labels(self, ref: str, rows: list[dict]):
        from infrx.pipelines import annotations as p1
        return run(p1.import_labels(self.store, self.log, provider_org_id=self.NEMO,
                                    actor=self.DEV, dataset_ref=ref, rubric_ref=self.rubric,
                                    rows=rows))

    def labels(self, ref: str) -> dict[str, tuple[str, str, object]]:
        """annotation ref -> (sample id, state, record) of every label logged under `ref`."""
        from infrx.pipelines.annotations import _states
        events = run(self.log.events(ref, provider_org_id=self.NEMO))
        sample = {e["annotation_ref"]: e["sample_id"] for e in events if "annotation_ref" in e}
        return {a: (sample[a], state, run(self.store.resolve(a, provider_org_id=self.NEMO)))
                for a, state in _states(events).items()}

    def review(self, ref: str, annotation_ref: str, decision: str, correction=None):
        """ADMIN assigns the sample to DEV under the rubric (once), DEV reviews."""
        from infrx.pipelines import annotations as p1
        record = run(self.store.resolve(annotation_ref, provider_org_id=self.NEMO))
        run(p1.assign(self.log, self.directory, provider_org_id=self.NEMO, user_id=self.ADMIN,
                      dataset_ref=ref, sample_id=record.sample_id, reviewer_id=self.DEV,
                      rubric_ref=self.rubric))
        return run(p1.review(self.store, self.log, self.directory, provider_org_id=self.NEMO,
                             user_id=self.DEV, dataset_ref=ref, annotation_ref=annotation_ref,
                             decision=decision, rubric_ref=self.rubric, correction=correction))

    def teacher_fake(self):
        from tests.j.submit.judge_fake import JudgeFake
        return JudgeFake(port=SERVICE_PORTS["teacher"])

    def wiring(self, **over):
        from infrx.contracts.limits import DEFAULTS
        from infrx.judge.submit import HttpJudgeProvider
        from infrx.pipelines import annotations as p1
        from infrx.pipelines.teachers import TeacherWiring
        return dataclasses.replace(TeacherWiring(
            members=self.directory, ledger=self.judge,
            provider=HttpJudgeProvider(self.teacher.url, timeout_s=2.0), store=self.store,
            objects=self.objects, labels=p1.import_labels, log=self.log,
            rates=self.j1.TEST_RATES, settings=DEFAULTS.replace(judge_mode="live"),
            redact=lambda text: text), **over)

    def batch(self, ref: str, n: int, *, chunk_size: int = 4):
        from infrx.judge import TokenCeilings
        from infrx.pipelines.teachers import TeacherBatch
        return TeacherBatch(
            batch_id=str(uuid.UUID(int=0xe7 << 32 | n, version=4)), provider_org_id=self.NEMO,
            requested_by=self.DEV, dataset_ref=ref, rubric_ref=self.rubric,
            teacher_model=self.j1.JUDGE_MODEL, prompt_version="e7l-teach-1",
            payer_ref=self.payer, chunk_size=chunk_size,
            ceilings=TokenCeilings(input_tokens=1_000, output_tokens=1_000, reasoning_tokens=0))

    def answer_posts(self) -> None:
        """The teacher fake answers each posted item by the fixture's teacher answer for its
        question (a declared None is a malformed answer)."""
        for post in self.teacher.posts:
            batch = self.teacher.batches.get(post["submit_key"])
            if batch is None:
                continue
            out = []
            for item in post["items"]:
                row = BY_Q.get(item["content"])
                if row is None or row["id"] not in DECL["teacher"]:
                    continue
                label = DECL["teacher"][row["id"]]
                out.append([item["sample_id"], "not json" if label is None
                            else json.dumps({"label": label})])
            self.teacher.outputs[batch] = out

    # ---------------------------------------------------------------- exports and training
    def export(self, ref: str, n: int):
        from infrx.pipelines import annotations as p1
        return run(p1.export(self.store, self.log, self.objects, provider_org_id=self.NEMO,
                             dataset_ref=ref, export_id=uid(n, 0xe8), adapter="sft.1",
                             now=self.db_now(), ttl_s=3600))

    def read_export(self, n: int) -> list[dict]:
        from infrx.pipelines import annotations as p1
        data = run(p1.read_export(self.store, self.objects, provider_org_id=self.NEMO,
                                  export_id=uid(n, 0xe8), now=self.db_now()))
        return [json.loads(line) for line in data.splitlines()]

    def prepare(self, ref: str, export: dict, ext: str, *, connector: str | None = None):
        from infrx.pipelines import training as p3
        return run(p3.prepare(self.store, self.objects, self.runs, provider_org_id=self.NEMO,
                              actor=self.DEV, external_run_id=ext, dataset_ref=ref,
                              config=CONFIG, export={"format": export["format"],
                                                     "export_id": export["export_id"]},
                              payer_ref=self.payer, limit="25.00000000", now=self.db_now(),
                              connector=connector or p3.MANUAL))

    def submit_manual(self, ext: str) -> dict:
        from infrx.pipelines import training as p3
        return run(p3.submit(self.store, self.objects, self.runs, p3.ManualConnector(),
                             self.directory, provider_org_id=self.NEMO, user_id=self.DEV,
                             external_run_id=ext))

    def checkpoint(self, ext: str, n: int, *, serve: str | None, data: bytes | None = None,
                   declared: str | None = None, held: bool = False) -> dict:
        """The provider uploads checkpoint `n` of run `ext` (its descriptor, with the training
        loss the fixture declares for the endpoint it deploys as) and P3 imports it."""
        from infrx.pipelines import training as p3
        cid, key = uid(n, 0xc7e), f"lab/{self.NEMO}/training/{ext}/checkpoints/{n}.json"
        if data is None:
            data = json.dumps({"format": p3.DESCRIPTOR, "base_model": CONFIG["base_model"],
                               "adaptation": CONFIG["adaptation"],
                               "files": ["adapter.safetensors"],
                               "metrics": DECL["checkpoints"][serve]}).encode()
        if data:
            run(self.objects.put_if_absent(key, data, "application/json"))
        self.evals.served[cid] = serve
        if held:
            self.evals.held.add(cid)
        return run(p3.import_checkpoint(
            self.store, self.objects, self.runs, self.evals, provider_org_id=self.NEMO,
            external_run_id=ext, checkpoint_id=cid, artifact_key=key,
            artifact_digest=declared or "sha256:" + hashlib.sha256(data).hexdigest()))

    def approve(self, ext: str, n: int) -> dict:
        from infrx.pipelines import training as p3
        return run(p3.approve(self.objects, self.runs, self.evals, self.directory,
                              provider_org_id=self.NEMO, user_id=self.DEV, external_run_id=ext,
                              checkpoint_id=uid(n, 0xc7e)))

    # ---------------------------------------------------------------- evaluation (H1, B1)
    @functools.cached_property
    def harness_ref(self) -> str:
        return run(self.store.publish({
            "schema": "lab.harness_revision.1", "provider_org_id": self.NEMO,
            "harness_id": uid(1, 0xa77), "version": 1, "created_at": "2026-09-28T10:00:00Z",
            "adapter": "text", "prompt_template": "answer {{q}}", "processor_profile": "e7l-1",
            "input_mapping": {"q": "sample.content"}, "tools": []},
            provider_org_id=self.NEMO, actor="dev@nemo"))

    def serving(self, name: str) -> str:
        digest = hashlib.sha256(f"e7l:{name}".encode()).hexdigest()
        return f"lab:serving:{self.NEMO}:{uid(sorted(ENDPOINT_PORTS).index(name) + 1, 0x5e7)}" \
               f"@sha256:{digest}"

    async def evaluate(self, name: str, ref: str):
        """Endpoint `name`'s B1 run over every case of version `ref`, once per session."""
        from infrx.contracts.lab import records
        from infrx.evaluation import runner
        if (name, ref) not in self.frozen:
            run_id = uid(len(self.frozen) + 1, 0x7e7)
            payload = {"schema": "lab.eval_run.1", "provider_org_id": self.NEMO,
                       "run_id": run_id, "created_at": "2026-09-28T10:00:00Z",
                       "dataset_ref": ref, "harness_ref": self.harness_ref,
                       "serving_ref": self.serving(name), "evaluator_ref": EVALUATOR(self.NEMO),
                       "seed": 7, "environment": "dev", "max_cases": 100, "state": "queued",
                       "idempotency_key": records.run_key(run_id),
                       "budgets": [{"limit": {"unit": "CREDIT", "value": "100.00000000"},
                                    "reserved": {"unit": "CREDIT", "value": "0.00000000"}}]}
            frozen = await runner.freeze(self.store, payload, evaluator=SPEC, access=self.access,
                                         user_id=self.DEV, provider_org_id=self.NEMO)
            with endpoint(name) as (wallet, http):
                await self.b1(http).run(frozen)
            self.wallets.append((name, wallet))
            self.frozen[(name, ref)] = frozen
        return self.frozen[(name, ref)]

    def b1(self, endpoint_):
        from infrx.evaluation.runner import Limits, Runner
        from tests.b.runner.world import DEPLOYMENT
        return Runner(self.store, self.objects, endpoint_, DEPLOYMENT, worker_id="e7l",
                      limits=Limits(lease_s=30, max_attempts=3, dispatch_retries=2,
                                    concurrency=2))

    def run_state(self, run_id: str) -> str:
        return self.sql("select state from infrx.lab_eval_runs where run_id = %s", run_id)[0][0]

    def holdout_records(self, frozen) -> list[dict]:
        from infrx.evaluation import reports
        rows = run(self.store.run_results(frozen.run.run_id, provider_org_id=self.NEMO))
        return [r for r in run(reports.case_records(frozen, rows, self.objects))
                if r["case_id"] in frozen.holdout]

    def compare(self, baseline: str, candidate: str, ref: str) -> dict:
        """B2 over the frozen holdout of `ref` only (train cases never decide)."""
        from infrx.evaluation import reports
        base, cand = run(self.evaluate(baseline, ref)), run(self.evaluate(candidate, ref))
        assert base.holdout == cand.holdout
        return reports.compare(base.run.model_dump(by_alias=True, exclude_unset=True),
                               self.holdout_records(base),
                               cand.run.model_dump(by_alias=True, exclude_unset=True),
                               self.holdout_records(cand), universe=list(base.holdout),
                               protocol=DECL["protocol"])

    # ---------------------------------------------------------------------- traces (N3)
    def admitted_request(self, owner: str, key: str, tag: str) -> str:
        """A real admitted request of `owner` (D1's admit), so D6F feedback can name it."""
        from tests.d import checks_admission as ca
        from infrx.contracts.conformance import builders as b
        request = ca.credit_request(ca.World(self.conn), key, self.org(owner))
        ca.admit(self.conn, request, b.idem(request, tag), regime="credit")
        return request.request_id

    def feedback(self, owner: str, request_id: str, value: str, tag: str) -> None:
        from psycopg.types.json import Jsonb
        from tests.d import checks_admission as ca
        self.conn.execute("select infrx.accept_feedback(%s)", (Jsonb({
            "org_id": self.org(owner), "principal": ca.C2_KEY, "by_operator": False,
            "channel": "api", "request_id": request_id, "feedback_id": f"fb_{tag:0<26}"[:29],
            "body": {"name": "correction", "value": value},
            "idem": {"org_id": self.org(owner), "operation": "feedback", "key": f"e7l-{tag}",
                     "payload_hash": "sha256:" + hashlib.sha256(tag.encode()).hexdigest()}}),))

    def trace(self, owner: str, request_id: str, question: str, output: str, serving: str,
              n: int) -> None:
        """T2I's projection row of the request (the serving version it ran on) and T3's
        content object."""
        from infrx.traces import ship
        now = self.db_now()
        key = ship.content_key(self.org(owner), f"77e70000-0000-4000-8000-{n:012x}")
        self.trace_rows.append(SimpleNamespace(
            org_id=self.org(owner), request_id=request_id, started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(minutes=59), content_stored=True, content_key=key,
            model_revision=f"{self.cc.MODEL}@{serving}"))
        self.trace_objects.seed(key, json.dumps({
            "request": {"messages": [{"role": "user", "content": question}]},
            "output": {"content": output}}).encode(), "application/json")

    @functools.cached_property
    def trace_objects(self):
        from infrx.media.store import InMemoryObjectStore
        return InMemoryObjectStore()

    @functools.cached_property
    def retention(self):
        from infrx.traces.retention import Retention
        rows = self.trace_rows

        class Traces:
            async def find(self, org_id, request_id):
                return [r for r in rows if (r.org_id, r.request_id) == (org_id, request_id)]

        class Stones:
            stones: list = []

            async def put(self, stones):
                self.stones.extend(stones)

            async def get(self, pairs):
                return {}
        return Retention(Stones(), Traces(), None, self.trace_objects, clock=self.db_now)

    def select(self, owner: str, requests: list[str], n: int):
        from tests.n.lineage.world import FakeContent

        from infrx.datasets import lineage

        async def feedback(org, request):
            return await self.feedback_service.list_owned(
                SimpleNamespace(org_id=org, is_operator=False), request)

        async def model_of(row):
            return row.model_revision.split("@")[0]
        return run(lineage.select(
            access=self.access, retention=self.retention,
            content=FakeContent(self.directory, self.retention), feedback=feedback,
            model_of=model_of, store=self.store, objects=self.objects, user_id=self.DEV,
            provider_org_id=self.NEMO, grantor_org_id=self.org(owner), model_id=self.cc.MODEL,
            selection_id=uid(n, 0x5e1), dataset_id=uid(n, 0xda8), version=1,
            created_at="2026-09-28T12:00:00Z", request_ids=requests, actor="dev@nemo"))

    def permitted(self, ref: str, purpose: str = "training") -> set[str]:
        from infrx.datasets import lineage
        return run(lineage.permitted(self.store, self.objects, ref, provider_org_id=self.NEMO,
                                     purpose=purpose, now=self.db_now()))

    def reconcile(self) -> dict:
        from infrx.datasets import lineage
        return run(lineage.reconcile(self.directory, self.retention, self.objects,
                                     provider_org_id=self.NEMO))


# ------------------------------------------------------------ P3's evaluation port (WR-E7L-1)
class Evaluations:
    """P3's `Evaluations` port as B3 would serve it (not on the base: WR-E7L-1). A validated
    checkpoint is served by the synthetic endpoint the scenario deployed it as (L3's
    stand-in) and B1 runs the bundle's dataset in-process (the worker process is i07's). The
    evaluation's `holdout_sha256` is computed from the holdout B1 froze - never copied from
    the ask - and its state is D7's run state. A `held` checkpoint is queued and never worked."""

    def __init__(self, lab: Lab) -> None:
        self.lab, self.served, self.held, self.done = lab, {}, set(), {}
        self.calls = 0

    async def evaluate(self, *, provider_org_id: str, checkpoint_id: str, dataset_ref: str,
                       split: str, holdout_sha256: str) -> str:
        from infrx.contracts.lab import records
        self.calls += 1
        if checkpoint_id not in self.done:
            name = self.served[checkpoint_id]
            entry = {"run_ref": None, "run_id": None, "dataset_ref": dataset_ref,
                     "split": split, "holdout_sha256": None, "serving": name}
            if checkpoint_id not in self.held:          # a held run is queued, never worked
                frozen = await self.lab.evaluate(name, dataset_ref)
                entry.update(run_ref=records.ref_of(frozen.run.model_dump(
                    by_alias=True, exclude_unset=True)), run_id=frozen.run.run_id,
                    holdout_sha256=pin_of(frozen.holdout))
            self.done[checkpoint_id] = entry
        return self.done[checkpoint_id]["run_ref"] or f"queued:{checkpoint_id}"

    async def evaluation(self, *, provider_org_id: str, checkpoint_id: str) -> dict | None:
        found = self.done.get(checkpoint_id)
        if found is None:
            return None
        state = "queued" if found["run_id"] is None else self.lab.run_state(found["run_id"])
        return {**found, "state": state}


# ------------------------------------------------------------------ evaluator and endpoints
SPEC = {"metric": "exact_match", "reference": "sample.original.answer",
        "max_requests": 4, "max_bytes": 100_000, "max_seconds": 60}
EVALUATOR_ID = uid(1, 0xe7a)


def EVALUATOR(provider: str) -> str:                     # noqa: N802 - a derived constant
    from infrx.evaluation.runner import evaluator_ref
    return evaluator_ref(SPEC, provider_org_id=provider, evaluator_id=EVALUATOR_ID)


def answer_for(name: str):
    """Endpoint `name`'s deterministic model: the right answer for the row ids the fixture
    declares it gets right, "wrong" for the other benchmark questions, "unknown" otherwise."""
    right = set(DECL["endpoints"][name])

    def answer(prompt: str):
        row = BY_Q.get(prompt.removeprefix("answer "))
        if row is None:
            return "unknown"
        return row["answer"] if row["id"] in right else "wrong"
    return answer


def _bind(wallet, port: int):
    """The endpoint's own port, else the first free spare of the block (runner.SPARE_PORTS)."""
    import errno
    from tests.b.runner.world import serve
    for candidate in (port, *_gate().SPARE_PORTS):
        try:
            return serve(wallet, candidate, KEY), candidate
        except OSError as busy:
            if busy.errno != errno.EADDRINUSE:
                raise
    raise OSError(errno.EADDRINUSE, f"address already in use: {port} and every spare port")


@contextlib.contextmanager
def endpoint(name: str, *, funded: str = "100000"):
    """Endpoint `name` served on its port; yields (wallet, HttpDevEndpoint)."""
    from infrx.evaluation.runner import HttpDevEndpoint
    from tests.b.runner.world import RATE_CARD, DevWallet
    wallet = DevWallet(funded, answer=answer_for(name))
    server, port = _bind(wallet, ENDPOINT_PORTS[name])
    try:
        yield wallet, HttpDevEndpoint(f"http://127.0.0.1:{port}", api_key=KEY,
                                      model=f"e7l-{name}", rate_card=RATE_CARD)
    finally:
        server.shutdown()
        server.server_close()


def save(workdir: Path, name: str, value) -> str:
    """A case artifact beside the verdict; answers its sha256."""
    data = json.dumps(value, indent=1, sort_keys=True, default=str).encode()
    (workdir / name).write_bytes(data)
    return hashlib.sha256(data).hexdigest()
