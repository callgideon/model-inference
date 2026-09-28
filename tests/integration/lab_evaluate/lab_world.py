"""E6L's world: one Lab database on the e6l stack's PostgreSQL (every migration, the test clock,
D7's seeded grant and source), E2's MinIO through M1-L2's `S3ObjectStore`, the merged N1/N2/
H1/B1/B2/B3 code, and synthetic dev endpoints over HTTP - nothing else.

* **Provider and people**: NEMO (the seeded provider, `checks_credit`), its developer DEV and
  viewer VIEWER; OTHER, another provider. D7's seed: CONSUMER_1's grant to NEMO for
  provider_sharing + training (the handle D7 registers a source under - the imported content
  itself is ours, `ownership: provider_owned`, no customer content), and OTHER's own grant.
* **Fixtures** (`fixtures/`): owned JSONL benchmarks and their import specs
  (`benchmark.json`), including the declared known wins and regressions of each synthetic
  endpoint - the expectations are the fixture's, never the implementation's.
* **Endpoints**: `DevWallet` (B1's provider_dev stand-in: a wallet funded by an operator
  allocation, a debit per admitted request, 402 when empty, a replay per Idempotency-Key)
  served as `/v1/chat/completions` on `runner.ENDPOINT_PORTS`, reached through B1's
  `HttpDevEndpoint`. Each answers by the benchmark's question id.
* **B2's input**: `case_records` assembles B2's case records from D7's rows (results,
  attempts' costs, case states) and the manifest (cluster = group key, slices from the row):
  no product function does that yet (open issue E6L-O2).

`NOT_RUN` is the one vocabulary for a case that waits on an unmerged lane: the runner maps
it to NOT RUN, never a pass, and the reason carries the exact rerun command.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
INTEGRATION = HERE.parent
REPO = HERE.parents[2]
API = REPO / "apps" / "infrx-api"
FIXTURES = HERE / "fixtures"
sys.path[:0] = [p for p in (str(INTEGRATION), str(INTEGRATION / "backend"), str(API))
                if p not in sys.path]
import harness  # noqa: E402 - E2's harness, namespace e6l

import stack                                            # noqa: E402

#: The video benchmark's bundle: owned synthetic bytes (N1 hashes a clip, it does not decode it)
CLIPS = {"clips/a.mp4": b"\x00\x00\x00\x18ftypmp42e6l-a" * 64,
         "clips/b.mp4": b"\x00\x00\x00\x18ftypmp42e6l-b" * 64,
         "clips/notes.txt": b"not a video"}
RUNNER = "tests/integration/lab_evaluate/runner.py"
RERUN = f"apps/infrx-api/.venv/bin/python {RUNNER} --out <dir>"
DATABASE = f"{harness.PG_DATABASE}_lab"         # infrx_e6l_lab: D1's clock gate needs infrx_%
KEY = "e6l-provider-dev-key"                    # the synthetic endpoints' local bearer
BENCH = json.loads((FIXTURES / "benchmark.json").read_text())


def _gate():
    """runner.py by path under a unique name (`runner` is also E3C's module name)."""
    import importlib.util
    if "e6l_runner" not in sys.modules:
        spec = importlib.util.spec_from_file_location("e6l_runner", HERE / "runner.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["e6l_runner"]


ENDPOINT_PORTS = _gate().ENDPOINT_PORTS


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


# ------------------------------------------------------------------------------ the world
class Lab:
    """The session's world. Built once (`conftest.lab`), torn down at the end."""

    def __init__(self) -> None:
        import psycopg
        from infrx.lab.access import LabAccess
        from infrx.media.s3 import S3ObjectStore
        from infrx.state.jobstore import connector
        from infrx.state.lab_access import PgAccessStore
        from infrx.state.lab_data import PgLabDataStore

        import pgstate
        from tests.d import checks_credit as cc
        from tests.d import test_d7_lab_data as d7
        from tests.d import test_l2sql_access as l2
        self.cc, self.d7, self.l2 = cc, d7, l2
        self.NEMO, self.OTHER = cc.NEMO, cc.OTHER_PROVIDER
        self.DEV, self.VIEWER, self.OTHER_DEV = cc.PROVIDER_DEV_USER, l2.VIEWER, l2.BOTH
        self.database = harness.provision_database(DATABASE)
        self.dsn = harness.pg_dsn(DATABASE)
        self.conn = psycopg.connect(self.dsn, autocommit=True)
        self.migrations = pgstate.apply_migrations(self.conn)
        pgstate.install_test_clock(self.conn)
        d7.seed(self.conn)
        self.grant, self.source = d7.W["grant"], d7.W["source"]
        self.other_grant, self.other_source = d7.W["other_grant"], d7.W["other_source"]
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
        self.access = LabAccess(PgAccessStore(connector(self.dsn)))
        self.imported: dict[str, object] = {}
        self.runs: dict[str, dict] = {}

    def close(self) -> None:
        listed = self.s3.list_objects_v2(Bucket=harness.S3_BUCKET, Prefix=self.prefix)
        for item in listed.get("Contents", []):
            self.s3.delete_object(Bucket=harness.S3_BUCKET, Key=item["Key"])
        self.conn.close()

    # -------------------------------------------------------------------- data (N1, N2)
    def spec(self, name: str, *, n: int, provider: str | None = None,
             grant: str | None = None, **over) -> dict:
        """`fixtures/<name>`'s import spec for import `n` (ids derived, so a rerun of the
        runner publishes the same dataset refs)."""
        from infrx.datasets.imports import FORMAT
        return {"format": FORMAT, "provider_org_id": provider or self.NEMO,
                "import_id": uid(n, 0x1e6), "dataset_id": uid(n, 0xda6), "version": 1,
                "created_at": "2026-09-28T10:00:00Z", "grant_ref": grant or self.grant,
                **BENCH["common"], **BENCH["specs"][name], **over}

    def bytes_of(self, name: str) -> bytes:
        return (FIXTURES / f"{name}.jsonl").read_bytes()

    def do_import(self, name: str, *, n: int, data: bytes | None = None, accept: bool = False,
                  caller: str | None = None, **over):
        from infrx.datasets.imports import Importer

        async def pieces():
            body = self.bytes_of(name) if data is None else data
            for start in range(0, len(body), 97):                  # odd-sized pieces
                yield body[start:start + 97]
        return run(Importer(self.store, self.objects).run(
            self.spec(name, n=n, **over), pieces(), provider_org_id=caller or self.NEMO,
            actor="dev@nemo", accept_rejects=accept))

    def dataset(self, name: str, n: int):
        """`name` imported once per session with its rejects accepted (a video benchmark's
        clips uploaded into its import bundle first)."""
        if name not in self.imported:
            if "media" in BENCH["specs"][name]["fields"]:
                self.upload_clips(self.spec(name, n=n)["import_id"])
            self.imported[name] = self.do_import(name, n=n, accept=True)
        return self.imported[name]

    def upload_clips(self, import_id: str) -> None:
        from infrx.datasets.imports import bundle_key
        for path, data in CLIPS.items():
            run(self.objects.put_if_absent(bundle_key(self.NEMO, import_id, path), data,
                                           "text/plain" if path.endswith(".txt") else "video/mp4"))

    def manifest(self, ref: str, provider: str | None = None):
        return run(self.store.resolve(ref, provider_org_id=provider or self.NEMO))

    def rows(self, ref: str) -> dict[str, dict]:
        """fixture row id -> {sample_id, split, group, row} of a published manifest."""
        from infrx.datasets.imports import sample_key
        manifest = self.manifest(ref)
        where = {i: s for s in ("train", "validation", "holdout")
                 for i in getattr(manifest.splits, s)}
        out = {}
        for sample in manifest.samples:
            body = json.loads(run(self.objects.get(sample_key(self.NEMO,
                                                              sample.content_digest))))
            out[body["original"]["id"]] = {"sample_id": sample.sample_id,
                                           "split": where[sample.sample_id],
                                           "group": sample.group_key, "row": body["original"]}
        return out

    def sql(self, statement: str, *args) -> list[tuple]:
        cursor = self.conn.execute(statement, args)
        return cursor.fetchall() if cursor.description else []

    def advance(self, seconds: int) -> None:
        self.d7.advance(self.conn, seconds)

    def revoke(self) -> None:
        self.l2.ok(self.conn, "lab_revoke_access_grant", {
            "actor_user_id": self.l2.C1, "grantor_org_id": self.l2.org(self.conn, self.l2.C1),
            "recipient_provider_org_id": self.NEMO})

    def restore(self) -> None:
        self.l2.ok(self.conn, "lab_put_access_grant",
                   self.l2.scope(self.conn, purposes=["provider_sharing", "training"]))

    # ----------------------------------------------------------------- evaluation (H1, B1)
    def publish(self, payload: dict, provider: str | None = None) -> str:
        return run(self.store.publish(payload, provider_org_id=provider or self.NEMO,
                                      actor="dev@nemo"))

    def harness_ref(self, n: int, *, adapter: str = "text", template: str = "answer {{q}}",
                    tools=()) -> str:
        return self.publish({
            "schema": "lab.harness_revision.1", "provider_org_id": self.NEMO,
            "harness_id": uid(n, 0xa76), "version": 1, "created_at": "2026-09-28T10:00:00Z",
            "adapter": adapter, "prompt_template": template, "processor_profile": "e6l-1",
            "input_mapping": {"q": "sample.content"} if adapter == "text" else {},
            "tools": list(tools)})

    def serving(self, name: str) -> str:
        digest = hashlib.sha256(f"e6l:{name}".encode()).hexdigest()
        return f"lab:serving:{self.NEMO}:{uid(sorted(ENDPOINTS).index(name) + 1, 0x5e6)}" \
               f"@sha256:{digest}"

    def run_payload(self, n: int, dataset_ref: str, harness_ref: str, serving: str, *,
                    max_cases: int = 100, seed: int = 7, limit: str = "100.00000000") -> dict:
        from infrx.contracts.lab import records
        run_id = uid(n, 0x7e6)
        return {"schema": "lab.eval_run.1", "provider_org_id": self.NEMO, "run_id": run_id,
                "created_at": "2026-09-28T10:00:00Z", "dataset_ref": dataset_ref,
                "harness_ref": harness_ref, "serving_ref": serving, "evaluator_ref": EVALUATOR(
                    self.NEMO), "seed": seed, "environment": "dev", "max_cases": max_cases,
                "state": "queued", "idempotency_key": records.run_key(run_id),
                "budgets": [{"limit": {"unit": "CREDIT", "value": limit},
                             "reserved": {"unit": "CREDIT", "value": "0.00000000"}}]}

    def freeze(self, payload: dict, user: str | None = None):
        from infrx.evaluation import runner
        return run(runner.freeze(self.store, payload, evaluator=SPEC, access=self.access,
                                 user_id=user or self.DEV, provider_org_id=self.NEMO))

    def runner(self, endpoint, *, worker: str = "w", limits=None, recordings=None):
        from infrx.evaluation.runner import Limits, Runner
        from tests.b.runner.world import DEPLOYMENT
        return Runner(self.store, self.objects, endpoint, DEPLOYMENT, worker_id=worker,
                      limits=limits or Limits(lease_s=30, max_attempts=3, dispatch_retries=2,
                                              concurrency=2), recordings=recordings)

    def results(self, run_id: str) -> dict[str, dict]:
        return {case: json.loads(body) for case, body in self.sql(
            "select case_id::text, body from infrx.lab_eval_results where run_id = %s",
            run_id)}

    def case_records(self, frozen) -> list[dict]:
        """B2's input from D7: a result is its score; a failed case an error (scored 0); a
        case with neither is missing (no record)."""
        from infrx.datasets.imports import sample_key
        run_id = frozen.run.run_id
        results = self.results(run_id)
        costs: dict[str, list] = {}
        for case, unit, value in self.sql(
                "select case_id::text, cost_unit, cost_value::text from infrx.lab_eval_attempts "
                "where run_id = %s and cost_value is not null order by attempt", run_id):
            costs.setdefault(case, []).append({"unit": unit, "value": value})
        states = dict(self.sql("select case_id::text, state from infrx.lab_eval_cases "
                               "where run_id = %s", run_id))
        samples = {s.sample_id: s for s in frozen.manifest.samples}
        records = []
        for case in frozen.cases:
            body = json.loads(run(self.objects.get(sample_key(
                self.NEMO, samples[case].content_digest))))
            base = {"case_id": case, "cluster": samples[case].group_key,
                    "slices": [body["original"]["slice"]] if "slice" in body["original"] else [],
                    "costs": costs.get(case, [])}
            if case in results:
                records.append({**base, "score": results[case]["score"],
                                "latency_ms": results[case]["latency_ms"]})
            elif states.get(case) == "failed":
                records.append({**base, "score": None, "error": "failed"})
        return records


# ------------------------------------------------------------------ evaluator and endpoints
SPEC = {"metric": "exact_match", "reference": "sample.original.answer",
        "max_requests": 4, "max_bytes": 100_000, "max_seconds": 60}
EVALUATOR_ID = uid(1, 0xe6a)


def EVALUATOR(provider: str) -> str:                     # noqa: N802 - a derived constant
    from infrx.evaluation.runner import evaluator_ref
    return evaluator_ref(SPEC, provider_org_id=provider, evaluator_id=EVALUATOR_ID)


def _questions() -> dict[str, dict]:
    """The benchmark rows by question text, over every fixture with a `q`."""
    out = {}
    for name in ("text", "tools"):
        for line in (FIXTURES / f"{name}.jsonl").read_bytes().splitlines():
            with contextlib.suppress(ValueError):
                row = json.loads(line)
                if isinstance(row, dict) and isinstance(row.get("q"), str):
                    out.setdefault(row["q"], row)
    return out


QUESTIONS = _questions()
TOOL_CALLS = {"T1": [{"name": "send_email", "arguments": {"to": "team"}}],
              "T2": [{"name": "rm_rf", "arguments": {"path": "/scratch"}}],
              "T4": [{"name": "lookup_fact", "arguments": {"fact": "boiling point of water"}}]}
TOOLS = [{"name": "send_email", "effect": "actuator", "input_schema": {"type": "object"}},
         {"name": "lookup_fact", "effect": "read_only", "input_schema": {"type": "object"}}]


def answer_for(name: str):
    """Endpoint `name`'s deterministic model: the right answer for the ids the benchmark
    declares it gets right, "wrong" otherwise; a declared refusal is a 400."""
    from infrx.contracts import errors
    right = set(BENCH["endpoints"].get(name, ()))
    refuses = set(BENCH.get("refuses", {}).get(name, ()))

    def answer(prompt: str):
        row = QUESTIONS.get(prompt.removeprefix("answer "))
        if row is None:
            return "unknown"
        if row["id"] in refuses:
            raise errors.InvalidRequest("this synthetic endpoint refuses the case")
        if name == "tools":
            return TOOL_CALLS.get(row["id"], row["answer"])
        return row["answer"] if row["id"] in right else "wrong"
    return answer


ENDPOINTS = ("baseline", "baseline_v2", "checkpoint", "improving", "missing", "regressing",
             "tools", "video")


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
                                      model=f"e6l-{name}", rate_card=RATE_CARD)
    finally:
        server.shutdown()
        server.server_close()


def save(workdir: Path, name: str, value) -> str:
    """A case artifact beside the verdict; answers its sha256."""
    data = json.dumps(value, indent=1, sort_keys=True, default=str).encode()
    (workdir / name).write_bytes(data)
    return hashlib.sha256(data).hexdigest()
