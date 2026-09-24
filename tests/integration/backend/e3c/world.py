"""E3C: the composed corrective stack, its process entrypoints and its fault points.

    python tests/integration/backend/e3c/world.py gateway|worker|collector   # a box process

Everything here REUSES E3B (S3 reconciliation §5: "reuse the harness; do not rebuild it"):
E2's stack (`harness`), E3B's clones, tenants and PostgREST (`stack`), the pilot box's two
processes and a journey's reads (`pilotbox`), and E2's controlled protocol engine
(`fake_vllm`, a real HTTP process). What E3C adds is small and named:

* **namespace `e3c`** (tasklocal block 56900-56999, containers `infrx-e3c-*`, PostgREST
  `infrx-e3crest*`). `harness.NAMESPACES` has no `e3c` row yet (wiring request WR-1): until it
  lands, `load_harness()` adds exactly that row when the module is loaded, and nothing else.
* **`Box`**: `pilotbox.PilotBox`, whose processes start through THIS file so a fault point
  or a bypass is installed before the product's own entrypoint runs. A second gateway is a
  second Box on the next port over the same environment.
* **fault points** (`POINTS`): `INFRX_E3C_BARRIER=<point>` holds the process at a named
  step of the real code path (it writes `barrier-<role>.json` and awaits forever); the test
  then SIGKILLs the process GROUP it started (`Box.kill`). Nothing is ever killed by name,
  and only processes this Box spawned, or `harness.Faults` on containers the namespace owns.
* **bypasses** (`BYPASSES`): `INFRX_E3C_BYPASS=<name>` removes one corrective control in
  the process (the negative controls). A bypass whose target is absent refuses to start the
  process (exit 5) so a renamed seam is NOT RUN, never a vacuous pass.
* **collector**: one real `MediaCollector` pass in a fresh process over the box's object
  store and the job store's liveness - the RV-03 question ("does a fresh process know what
  is live?") asked of the real S3 and PostgreSQL.

Label (brief E3C): this proves ORCHESTRATION with a controlled protocol engine. It says
nothing about Marlin quality, GPU capacity or hosted behaviour.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import signal
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
INTEGRATION = BACKEND.parent
NAMESPACE = "e3c"
# E3C's block offset from E2's layout (tasklocal TASK_BLOCKS["e3c"]: 56900 = 55500 + 1400).
OFFSET = 1400
BARRIER_ENV, BYPASS_ENV = "INFRX_E3C_BARRIER", "INFRX_E3C_BYPASS"
COMPOSE_ENV = "INFRX_E3C_COMPOSE_FILE"
BYPASS_MISSING = 5          # a box process whose bypass target is gone exits with this
# Lanes whose merge a BLOCKED case may name (tasks.json E3C start + integration deps).
LANES = ("E2C", "F2C", "D10", "M5", "M6", "W5", "G7", "G8", "I8", "E1C")


def load_harness():
    """E2's harness in namespace `e3c`. `harness.NAMESPACES` lacks the row (WR-1), so the
    one row is added to the source as it loads; once WR-1 merges the source is untouched."""
    if "harness" in sys.modules:
        return sys.modules["harness"]
    path = INTEGRATION / "harness.py"
    source = path.read_text()
    if os.environ.get("INFRX_E2_NAMESPACE") == NAMESPACE and f'"{NAMESPACE}"' not in source:
        # ponytail: load-time row until WR-1 adds `"e3c": 1400` to harness.NAMESPACES.
        anchor = 'NAMESPACES = {"e2": 0,'
        if anchor not in source:
            raise RuntimeError("harness.NAMESPACES moved: WR-1's shim no longer applies")
        source = source.replace(anchor, f'NAMESPACES = {{"{NAMESPACE}": {OFFSET}, "e2": 0,', 1)
    module = types.ModuleType("harness")
    module.__file__ = str(path)
    sys.modules["harness"] = module
    exec(compile(source, str(path), "exec"), module.__dict__)     # noqa: S102 - our own file
    override = os.environ.get(COMPOSE_ENV)
    if override:
        # `runner.py --s3-image` (WR-2): E2's compose file with the one S3 image line swapped,
        # under this checkout's ownership label (INFRX_E2_CHECKOUT, set by the runner).
        module.COMPOSE_FILE = Path(override)
    return module


harness = load_harness()
sys.path[:0] = [p for p in (str(INTEGRATION), str(BACKEND)) if p not in sys.path]
import pilotbox                                         # noqa: E402
import stack                                            # noqa: E402

# ------------------------------------------------------------------ vocabulary


def blocked(*lanes: str, why: str):
    """Skip as BLOCKED on named lanes (the runner maps it; it is never a pass)."""
    import pytest
    unknown = [lane for lane in lanes if lane not in LANES]
    if not lanes or unknown:
        raise AssertionError(f"BLOCKED must name known lanes, got {lanes}")
    pytest.skip(f"BLOCKED[{','.join(lanes)}] {why}")


def invalid(why: str):
    """Skip as INVALID: the case could not establish its own premise (never a pass)."""
    import pytest
    pytest.skip(f"INVALID[premise] {why}")


def need_stack():
    if harness.NAMESPACE != NAMESPACE:
        invalid(f"namespace {harness.NAMESPACE!r}, not {NAMESPACE!r}: run through e3c/runner.py")
    if not stack.has_stack():
        import pytest
        pytest.skip(f"BLOCKED[E2C] no {harness.PROJECT} stack: run "
                    "tests/integration/backend/e3c/runner.py")


# ------------------------------------------------------------------ fault points

# point -> (module, class, method, before|after, only when the call returned something).
# Each is a step of the real code path the brief names; the box process holds there.
POINTS = {
    "upload": ("infrx.media.uploads", "MediaUploads", "put_upload", "after", False),
    "admission": ("infrx.state.jobstore", "PgJobStore", "admit_credit", "after", False),
    "readiness": ("infrx.gateway.routes.relay", "Relay", "_admitted", "before", False),
    "attachment": ("infrx.media.attachments", "PgAttachments", "put", "after", False),
    "prep": ("infrx.state.jobstore", "PgJobStore", "prepared", "before", False),
    "outbox": ("infrx.state.jobstore", "PgJobStore", "dispatch_pending", "after", True),
    "claim": ("infrx.state.jobstore", "PgJobStore", "claim", "after", False),
    "output": ("infrx.state.journal", "PgStreamStore", "append", "after", True),
    "settle": ("infrx.state.jobstore", "PgJobStore", "complete_credit", "before", False),
}


def _summary(args) -> list:
    """Job ids only - never a body, a key or a URL."""
    out = []
    for arg in args:
        job = arg if isinstance(arg, str) else getattr(arg, "job_id", None) \
            or getattr(arg, "request_id", None)
        if isinstance(job, str) and len(job) <= 64:
            out.append(job)
    return out


def install_barrier(point: str, marker: Path) -> None:
    module, cls, attr, when, needs_result = POINTS[point]
    owner = getattr(importlib.import_module(module), cls)
    original = getattr(owner, attr)
    fired: list[int] = []

    async def hold(args) -> None:
        fired.append(os.getpid())
        partial = marker.with_suffix(".part")
        partial.write_text(json.dumps({"point": point, "pid": os.getpid(),
                                       "jobs": _summary(args)}))
        partial.replace(marker)
        await asyncio.Event().wait()             # until the test's SIGKILL

    async def barrier(self, *args, **kw):
        if when == "before" and not fired:
            await hold(args)
        result = await original(self, *args, **kw)
        if when == "after" and not fired and (result or not needs_result):
            await hold((*args, result))
        return result

    setattr(owner, attr, barrier)


# ------------------------------------------------------------------ bypasses (negative controls)

class BypassTargetMissing(RuntimeError):
    pass


def _target(module: str, path: str):
    try:
        owner = importlib.import_module(module)
        for part in path.split("."):
            owner = getattr(owner, part)
        return owner
    except (ImportError, AttributeError) as gone:
        raise BypassTargetMissing(f"{module}.{path}: {gone}") from None


def _upload_local() -> None:
    """UPLOAD-RESTART removed: a handle this process did not create is unknown here (RV-02's
    behaviour), whatever durable adapter serves uploads underneath."""
    app = _target("infrx.gateway.app", "create_app")
    mine: set[str] = set()

    def create_app(*args, **kw):
        built = app(*args, **kw)
        store = built.state.runtime.media_store
        from infrx.contracts import errors
        for name in ("create_upload", "put_upload", "finalize_upload", "materialize"):
            if not hasattr(store, name):
                raise BypassTargetMissing(f"media_store.{name}")

        def refuse(handle: str) -> None:
            if handle not in mine:
                raise errors.NotFound(f"no upload {handle} (bypass: process-local)")

        create, put, finalize, materialize = (store.create_upload, store.put_upload,
                                              store.finalize_upload, store.materialize)

        async def create_upload(org_id, constraints):
            ticket = await create(org_id, constraints)
            mine.add(ticket["upload_handle"])
            return ticket

        async def put_upload(org_id, handle, *rest):
            refuse(handle)
            return await put(org_id, handle, *rest)

        async def finalize_upload(org_id, handle):
            refuse(handle)
            return await finalize(org_id, handle)

        async def materialize_(org_id, source):
            if source.startswith("infrx-upload:"):
                refuse(source.removeprefix("infrx-upload:"))
            return await materialize(org_id, source)
        store.create_upload, store.put_upload = create_upload, put_upload
        store.finalize_upload, store.materialize = finalize_upload, materialize_
        return built
    sys.modules["infrx.gateway.app"].create_app = create_app


def _expiry_recompute() -> None:
    """RESULT-EXPIRY removed: the reported and enforced expiry is recomputed from the route's
    CURRENT `RESULT_TTL_S` over the settlement time - the base tree's `Jobs.result_expiry`."""
    jobs = _target("infrx.gateway.routes.jobs", "Jobs")
    _target("infrx.gateway.routes.jobs", "Jobs.result_expiry")
    from datetime import timedelta

    from infrx.contracts.records import JobState

    def result_expiry(self, outcome):
        if (outcome is None or outcome.state is not JobState.succeeded
                or outcome.usage is None or not outcome.result_ref):
            return None
        return outcome.settled_at + timedelta(seconds=self.relay.limits.result_ttl_s)
    jobs.result_expiry = result_expiry


def _revoke_ignored() -> None:
    """Revocation removed: a bearer the gateway once accepted is accepted for ever."""
    auth = _target("infrx.auth.keys", "Auth")
    original = _target("infrx.auth.keys", "Auth.authenticate")
    seen: dict[str, dict] = {}

    async def authenticate(self, req, *args, **kw):
        token = req.headers.get("authorization", "")
        row, status = await original(self, req, *args, **kw)
        if status is None and row is not None:
            seen[token] = row
        elif status == 401 and token in seen:
            return seen[token], None
        return row, status
    auth.authenticate = authenticate


def _tenant_blind() -> None:
    """Tenant scoping removed on the owned CREDIT read: a handle is served to whoever asks."""
    store = _target("infrx.state.jobstore", "PgJobStore")
    original = _target("infrx.state.jobstore", "PgJobStore.get_owned_credit")

    async def get_owned_credit(self, org_id, job_handle):
        rows = await self._query("select org_id::text from infrx.jobs where job_handle = %s",
                                 (job_handle,))
        return await original(self, rows[0][0] if rows else org_id, job_handle)
    store.get_owned_credit = get_owned_credit


BYPASSES = {"upload-local": _upload_local, "expiry-recompute": _expiry_recompute,
            "revoke-ignored": _revoke_ignored, "tenant-blind": _tenant_blind}


def install(environ=os.environ, workdir: Path | None = None, role: str = "") -> None:
    """What a box process installs before its entrypoint: bypasses, then its fault point."""
    for name in filter(None, environ.get(BYPASS_ENV, "").split(",")):
        BYPASSES[name]()
    point = environ.get(BARRIER_ENV, "")
    if point:
        install_barrier(point, (workdir or Path.cwd()) / f"barrier-{role}.json")


# ------------------------------------------------------------------ the box


class Box(pilotbox.PilotBox):
    """pilotbox's processes, each started through this file (`main`) so a fault point or a
    bypass is installed first. `start(role, **env)` adds environment for that start only."""

    def command(self, role: str) -> tuple[list[str], str]:
        argv, ready = super().command("worker" if role == "worker" else "gateway")
        return [sys.executable, str(Path(__file__).resolve()), role], ready

    def start(self, role: str, timeout: float = 60.0, **env: str) -> None:
        base = self.env
        self.env = {**base, **env}
        (self.workdir / f"barrier-{role}.json").unlink(missing_ok=True)
        try:
            super().start(role, timeout)
        finally:
            self.env = base

    def kill(self, role: str) -> int | None:
        """SIGKILL the process group THIS box started for `role` (never one found by name)."""
        return self.stop(role, signal.SIGKILL, timeout=10.0)

    def reached(self, role: str, timeout: float = 60.0) -> dict:
        """The fault point's marker, once the process holds there."""
        marker, end = self.workdir / f"barrier-{role}.json", time.monotonic() + timeout
        while time.monotonic() < end:
            if marker.exists():
                return json.loads(marker.read_text())
            if role in self.processes and self.processes[role].poll() is not None:
                raise AssertionError(f"the {role} exited before its fault point: "
                                     f"{self.tail(role)}")
            time.sleep(0.05)
        raise AssertionError(f"the {role} never reached its fault point in {timeout}s: "
                             f"{self.tail(role)}")


def second_gateway(box: Box, port_offset: int = 1) -> Box:
    """Another gateway process over the SAME stores (env, index namespace, object prefix)."""
    other = Box({}, box.env["UPSTREAM"], box.workdir, box.port + port_offset, box.namespace)
    other.env = dict(box.env, **{pilotbox.PORT_ENV: str(box.port + port_offset)})
    other.starts = {"gateway": 100, "worker": 100}         # its logs: gateway-101.log ...
    return other


def set_clock(database: str, seconds: float) -> None:
    """`infrx.now()` for every session of the clone, `seconds` ahead of the wall."""
    import psycopg
    with psycopg.connect(harness.pg_dsn(database), autocommit=True) as conn:
        conn.execute("select infrx_test.set_offset(%s)", (seconds,))


@contextlib.contextmanager
def composed(workdir: Path, *, start=("worker", "gateway"), **env: str):
    """E3B's journey stack with this lane's Box: two tenants on a fresh clone, PostgREST over
    it, E2's controlled engine, and the box processes `start` names (in order)."""
    import fake_vllm
    import psycopg
    need_stack()
    world = stack.provision_two_tenants()
    engine = fake_vllm.FakeVllmServer(harness.PORTS["fake_vllm"])
    with stack.journey_postgrest(world.database) as rest, engine:
        engine.control(prompt_tokens=pilotbox.ENGINE_PROMPT_TOKENS)
        with psycopg.connect(harness.pg_dsn(world.database), autocommit=True) as conn:
            conn.execute("select infrx_test.unfreeze(), infrx_test.set_offset(0)")
        box = Box(stack.pilot_env(world.database, workdir, rest, **env), engine.base_url,
                  workdir, stack.GATEWAY_PORT)
        try:
            for role in start:
                box.start(role)
            yield pilotbox.Journey(world, box, engine)
        finally:
            box.close()


# ------------------------------------------------------------------ a scenario's reads and actions

TEXT = [{"role": "user", "content": "Describe the van."}]


def video(source: str) -> list:
    return [{"role": "user", "content": [{"type": "text", "text": "What happens?"},
                                         {"type": "video_url", "video_url": {"url": source}}]}]


def video_url(name: str = "clip") -> list:
    return video(f"https://{pilotbox.MEDIA_HOST}/{name}.mp4")


def code(response) -> str | None:
    try:
        return response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def wait_for(check, timeout: float, what: str, every: float = 0.1):
    """`check()`'s first truthy answer within `timeout` s, else an AssertionError naming
    `what` - every wait in a scenario is bounded and says what it waited for."""
    end, last = time.monotonic() + timeout, None
    while time.monotonic() < end:
        last = check()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"not within {timeout:.0f} s: {what} (last: {last!r})")


def job_of(trip, org_id: str, key: str) -> list[tuple]:
    """(request_id, state) of every job the org admitted under `key`."""
    return trip.db("select request_id::text, state from infrx.jobs where org_id = %s and "
                   "idempotency_key = %s", org_id, key)


def money(trip, request_id: str) -> dict:
    """The job's CREDIT hold states, debits and USD holds - what 'exactly once' is about."""
    return {"holds": [s for s, in trip.db("select state from infrx.credit_wallet_holds where "
                                          "request_id = %s", request_id)],
            "debits": [a for a, in trip.db("select amount from infrx.credit_ledger where "
                                           "request_id = %s and kind = 'inference_debit'",
                                           request_id)],
            "usd_holds": trip.db("select count(*) from infrx.credit_holds where request_id = %s",
                                 request_id)[0][0]}


def attempts(trip, request_id: str, kind: str = "inference") -> int:
    return trip.db("select count(*) from infrx.attempts where job_id = %s and kind = %s",
                   request_id, kind)[0][0]


def terminal(trip, request_id: str, timeout: float = 60.0) -> str:
    return wait_for(lambda: next((state for _, state in trip.db(
        "select request_id, state from infrx.jobs where request_id = %s", request_id)
        if state in pilotbox.FINISHED), None), timeout, f"job {request_id} terminal")


def settled_once(trip, request_id: str) -> None:
    """A succeeded job: one debit, its hold settled, no USD hold; anything else: no debit
    and no hold left held."""
    state, = trip.one("select state from infrx.jobs where request_id = %s", request_id)
    books = money(trip, request_id)
    if state == "succeeded":
        assert len(books["debits"]) == 1 and books["holds"] == ["settled"], (state, books)
    else:
        assert not books["debits"] and "held" not in books["holds"], (state, books)
    assert books["usd_holds"] == 0, books


def cli(trip, *args: str, secret: str | None = None) -> tuple[int, dict]:
    """G6B's headless operator CLI as its own process (`python -m infrx.operations.cli`) over
    the scenario's clone. The key travels in the environment only, never argv."""
    import subprocess
    env = {name: value for name, value in os.environ.items() if name not in stack.AWS_UNSET}
    env.update(DATABASE_URL=harness.pg_dsn(trip.world.database),
               INFRX_OPERATOR_KEY=secret if secret is not None else trip.world.operator_secret,
               PYTHONPATH=os.pathsep.join(filter(None, (os.environ.get("PYTHONPATH"),
                                                        str(harness.API_ROOT)))))
    done = subprocess.run([sys.executable, "-m", "infrx.operations.cli", *args], env=env,
                          cwd=str(harness.API_ROOT), capture_output=True, text=True,
                          timeout=120)
    text = (done.stdout if done.returncode == 0 else done.stderr).strip().splitlines()
    try:
        return done.returncode, json.loads(text[-1]) if text else {}
    except json.JSONDecodeError:
        return done.returncode, {"raw": text[-1][:300]}


def individual(trip, name: str, workdir: Path):
    """A verified individual provisioned ONLY through the operator CLI: `auth.users` (the
    signup the product receives), then `grant` and `issue-key --secret-file`. The tenant
    shape `pilotbox.Journey` reads (name, secret, org_id, wallet.wallet_id)."""
    import psycopg
    from types import SimpleNamespace
    with psycopg.connect(harness.pg_dsn(trip.world.database), autocommit=True) as conn:
        user, org = stack.seed_individual(conn, name)
    status, granted = cli(trip, "grant", "--user", user, "--idempotency-key", f"g-{name}",
                          "--reason", "e3c scenario")
    assert status == 0 and granted["amount"].startswith("10000"), granted
    secret_file = workdir / f"{name}.key"
    secret_file.unlink(missing_ok=True)
    status, issued = cli(trip, "issue-key", "--user", user, "--name", f"{name} key",
                         "--secret-file", str(secret_file), "--idempotency-key", f"k-{name}",
                         "--reason", "e3c scenario")
    assert status == 0 and issued["org_id"] == org, issued
    secret = secret_file.read_text().strip()
    secret_file.unlink()                     # never left beside the evidence
    return SimpleNamespace(name=name, user_id=user, org_id=org, key_id=issued["key_id"],
                           secret=secret, grant=granted,
                           wallet=SimpleNamespace(wallet_id=granted["wallet_id"]))


def collectors(trip, count: int = 1, grace_s: float = 0.0) -> list[dict]:
    """`count` collector processes at once over the box's stores; each one's JSON answer."""
    import subprocess
    argv = [sys.executable, str(Path(__file__).resolve()), "collector", str(grace_s)]
    running = [subprocess.Popen(argv, env=trip.box.env, cwd=str(harness.REPO_ROOT),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True) for _ in range(count)]
    answers = []
    for process in running:
        out, err = process.communicate(timeout=120)
        answers.append(json.loads(out.strip().splitlines()[-1]) if process.returncode == 0
                       else {"exit": process.returncode, "stderr": err.strip()[-600:]})
    return answers


def objects(trip, under: str = "media/") -> set[str]:
    """Object keys under the box's own prefix (never another run's)."""
    prefix = trip.box.env["S3_MEDIA_PREFIX"] + under
    listed = harness.s3_client().list_objects_v2(Bucket=harness.S3_BUCKET, Prefix=prefix)
    return {item["Key"][len(trip.box.env["S3_MEDIA_PREFIX"]):]
            for item in listed.get("Contents", [])}


# ------------------------------------------------------------------ the collector process

async def collect_once(grace_s: float) -> dict:
    """One `MediaCollector` pass in THIS (fresh) process: the box's object store, the job
    store's liveness, the processing cache of the box. What it deleted, by key."""
    from infrx.config import from_env
    from infrx.gateway import pilot
    from infrx.media.gc import MediaCollector
    from infrx.media.prepare import ProcessingCache
    from infrx.media.uploads import MediaUploads
    from infrx.state.jobstore import PgJobStore, connector
    settings = from_env()
    limits = settings.pilot
    store = MediaUploads(pilot.object_store(settings), limits=limits,
                         cache=ProcessingCache(limits.processing_cache_dir,
                                               ttl_s=limits.processing_cache_ttl_s))
    jobs = PgJobStore(connector(limits.database_url), limits=limits)
    swept = await MediaCollector(store, is_live=jobs.is_live, grace_s=grace_s).sweep()
    return {"deleted": swept.deleted, "uploads_expired": swept.uploads_expired}


def main(argv: list[str]) -> int:
    role = argv[1] if len(argv) > 1 else ""
    workdir = Path(os.environ.get(pilotbox.CALLS_ENV, "calls.log")).parent
    try:
        install(workdir=workdir, role=role)
    except BypassTargetMissing as gone:
        print(f"e3c: bypass target missing: {gone}", file=sys.stderr, flush=True)
        return BYPASS_MISSING
    if role == "gateway":
        return pilotbox.main([argv[0], "gateway"])
    if role == "worker":
        from infrx.worker.__main__ import main as worker
        return worker()
    if role == "collector":
        grace = float(argv[2]) if len(argv) > 2 else 0.0
        print(json.dumps(asyncio.run(collect_once(grace))), flush=True)
        return 0
    raise SystemExit("usage: world.py gateway|worker|collector [grace_s]")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
