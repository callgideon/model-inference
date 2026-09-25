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
from urllib.parse import urlsplit

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

# point -> candidates (module, class, method, before|after, only when the call returned
# something); the first that exists in the tree is held. Each is a step of the real code path
# the brief names. F2C-L R110 folds the post-commit rechecks and attach into `admit_ready`, so
# on a tree that has it `admission` holds after that one transaction and `readiness` (the
# post-admission step) no longer exists (`has_point`): the race it holds open is gone.
POINTS = {
    "upload": (("infrx.media.uploads", "MediaUploads", "put_upload", "after", False),),
    # D10 (codex/d10-durable) puts `admit_ready` on the ReadinessStore port's adapter
    "admission": (("infrx.state.lifecycle", "PgLifecycle", "admit_ready", "after", False),
                  ("infrx.state.jobstore", "PgJobStore", "admit_ready", "after", False),
                  ("infrx.state.jobstore", "PgJobStore", "admit_credit", "after", False)),
    "readiness": (("infrx.gateway.routes.relay", "Relay", "_admitted", "before", False),),
    "attachment": (("infrx.media.attachments", "PgAttachments", "put", "after", False),),
    "prep": (("infrx.state.jobstore", "PgJobStore", "prepared", "before", False),),
    "outbox": (("infrx.state.jobstore", "PgJobStore", "dispatch_pending", "after", True),),
    "claim": (("infrx.state.jobstore", "PgJobStore", "claim", "after", False),),
    "output": (("infrx.state.journal", "PgStreamStore", "append", "after", True),),
    "settle": (("infrx.state.jobstore", "PgJobStore", "complete_credit", "before", False),),
}


def point_targets(point: str) -> list:
    """(owner class, candidate) of every candidate this tree has, in POINTS order."""
    found = []
    for candidate in POINTS[point]:
        module, cls, attr = candidate[:3]
        try:
            owner = getattr(importlib.import_module(module), cls)
        except (ImportError, AttributeError):
            continue
        if callable(getattr(owner, attr, None)):
            found.append((owner, candidate))
    return found


def point_target(point: str):
    """(owner class, candidate) of the first candidate this tree has, else None."""
    return next(iter(point_targets(point)), None)


def has_point(point: str) -> bool:
    return point_target(point) is not None


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
    """Hold at `point` on EVERY candidate the tree has; the first one the process calls
    holds, once. Phase 2: D10's `PgLifecycle.admit_ready` exists before the gateway calls it
    (G7 composes it); holding only the first candidate would wait on a step never taken."""
    found = point_targets(point)
    if not found:
        raise BypassTargetMissing(f"fault point {point}: none of {POINTS[point]}")
    fired: list[int] = []
    for owner, candidate in found:
        _hold_on(point, marker, owner, candidate, fired)


def _hold_on(point: str, marker: Path, owner, candidate, fired: list[int]) -> None:
    _, _, attr, when, needs_result = candidate
    original = getattr(owner, attr)

    async def hold(args) -> None:
        """Held until the test SIGKILLs the process, or releases it by deleting the marker."""
        fired.append(os.getpid())
        partial = marker.with_suffix(".part")
        partial.write_text(json.dumps({"point": point, "pid": os.getpid(),
                                       "jobs": _summary(args)}))
        partial.replace(marker)
        while marker.exists():
            await asyncio.sleep(0.05)

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


def fake_only(env: dict) -> list[str]:
    """What makes a box environment NOT the namespace's real services in pilot mode (the
    failure oracle: a fake-only substitution is a non-pass). Empty when it is real."""
    want = {"INFRX_MODE": "pilot", "S3_ENDPOINT_URL": harness.s3_endpoint(),
            "VALKEY_URL": harness.valkey_url()}
    wrong = [name for name, value in want.items() if env.get(name) != value]
    # this namespace's PostgreSQL, any login (WR-4: the dedicated runtime login is real too)
    real, got = urlsplit(harness.pg_dsn("")), urlsplit(env.get("DATABASE_URL", ""))
    if (got.hostname, got.port) != (real.hostname, real.port) or not got.path.strip("/"):
        wrong.append("DATABASE_URL")
    if not env.get("S3_MEDIA_BUCKET"):
        wrong.append("S3_MEDIA_BUCKET")
    return wrong


class Box(pilotbox.PilotBox):
    """pilotbox's processes, each started through this file (`main`) so a fault point or a
    bypass is installed first. `start(role, **env)` adds environment for that start only."""

    def command(self, role: str) -> tuple[list[str], str]:
        argv, ready = super().command("worker" if role == "worker" else "gateway")
        return [sys.executable, str(Path(__file__).resolve()), role], ready

    def start(self, role: str, timeout: float = 60.0, **env: str) -> None:
        """Start `role` (`bind_retried`), with `env` added for this start only. A box not on
        the real services is INVALID[fake-only] before anything is spawned."""
        wrong = fake_only({**self.env, **env})
        if wrong:
            import pytest
            pytest.skip(f"INVALID[fake-only] the box is not on the e3c services: {wrong}")
        base = self.env
        self.env = {**base, **env}
        (self.workdir / f"barrier-{role}.json").unlink(missing_ok=True)
        try:
            bind_retried(lambda: super(Box, self).start(role, timeout))
        finally:
            self.env = base

    def kill(self, role: str) -> int | None:
        """SIGKILL the process group THIS box started for `role` (never one found by name)."""
        return self.stop(role, signal.SIGKILL, timeout=10.0)

    def release(self, role: str) -> None:
        """Let a held process continue past its fault point."""
        (self.workdir / f"barrier-{role}.json").unlink()

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


def bind_retried(start, attempts: int = 10):
    """`start()`, retried on a bind collision: the e3c block lies inside the kernel's
    ephemeral range (E2's documented limit) and a just-stopped server's connections can hold
    its port for a moment on this busy host."""
    for attempt in range(attempts):
        try:
            return start()
        except RuntimeError as failed:
            if "address already in use" not in str(failed) or attempt == attempts - 1:
                raise
            time.sleep(2.0)


RUNTIME_ROLE = "infrx_runtime"
#: WR-4 for the whole matrix: set to 1 once the box can serve on the dedicated login (phase 2
#: finding F-1: pilot.py's pool `set role service_role` refuses it). s10 always uses it.
RUNTIME_LOGIN_ENV = "INFRX_E3C_RUNTIME_LOGIN"


def runtime_dsn(database: str) -> str:
    """WR-4: 0021's `infrx_runtime` given LOGIN and a fresh random password on this
    namespace's cluster - the operator's out-of-band step, done here as the owner - and the
    DSN the box then uses. The password lives in memory and the box's environment only."""
    import secrets

    import psycopg
    from psycopg import sql
    owner, secret = harness.pg_dsn(database), secrets.token_hex(16)
    with psycopg.connect(owner, autocommit=True) as conn:
        conn.execute(sql.SQL("alter role {} login password {}").format(
            sql.Identifier(RUNTIME_ROLE), sql.Literal(secret)))
    parts = urlsplit(owner)
    return owner.replace(f"{parts.username}:{parts.password}@", f"{RUNTIME_ROLE}:{secret}@", 1)


@contextlib.contextmanager
def composed(workdir: Path, *, start=("worker", "gateway"), runtime_login: bool | None = None,
             **env: str):
    """E3B's journey stack with this lane's Box: two tenants on a fresh clone, PostgREST over
    it, E2's controlled engine, and the box processes `start` names (in order).
    `runtime_login` (WR-4): the box's DATABASE_URL is the dedicated runtime login, not the
    owner; None follows INFRX_E3C_RUNTIME_LOGIN."""
    import fake_vllm
    import psycopg
    need_stack()
    if runtime_login is None:
        runtime_login = os.environ.get(RUNTIME_LOGIN_ENV) == "1"
    world = stack.provision_two_tenants()
    if runtime_login:
        env = {**env, "DATABASE_URL": runtime_dsn(world.database)}
    engine = fake_vllm.FakeVllmServer(harness.PORTS["fake_vllm"])
    with stack.journey_postgrest(world.database) as rest:
        bind_retried(engine.start)
        try:
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
        finally:
            engine.stop()


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


def readiness_reader(dsn: str):
    """The `ReadinessStore.readiness` of this tree, through the port's adapter: D10's
    `infrx.state.lifecycle.PgLifecycle` first, then `PgJobStore`; None when neither has it."""
    from infrx.state.jobstore import connector
    for module, cls in (("infrx.state.lifecycle", "PgLifecycle"),
                        ("infrx.state.jobstore", "PgJobStore")):
        try:
            owner = getattr(importlib.import_module(module), cls)
        except (ImportError, AttributeError):
            continue
        reader = getattr(owner(connector(dsn)), "readiness", None)
        if callable(reader):
            return reader
    return None


def durably_ready(trip, request_id: str) -> bool:
    """Whether the store records the job as eligible to execute (F2C-L `ReadinessStore.
    readiness`, D10's adapter). A tree with no readiness record has no durable eligibility
    at all: False, so executing such a job is executing before eligibility (RV-05)."""
    reader = readiness_reader(harness.pg_dsn(trip.world.database))
    return reader is not None and asyncio.run(reader(request_id)) is not None


def executed(trip, request_id: str) -> dict:
    """What ran for the job: preparation claims, a stored prompt count, inference attempts,
    debits."""
    counted, = trip.one("select prepared_prompt_tokens is not null from infrx.jobs where "
                        "request_id = %s", request_id)
    return {"preparation": attempts(trip, request_id, "preparation"), "prepared": int(counted),
            "inference": attempts(trip, request_id, "inference"),
            "debits": len(money(trip, request_id)["debits"])}


def attempts(trip, request_id: str, kind: str = "inference") -> int:
    return trip.db("select count(*) from infrx.attempts where job_id = %s and kind = %s",
                   request_id, kind)[0][0]


def terminal(trip, request_id: str, timeout: float = 60.0) -> str:
    try:
        return wait_for(lambda: next((state for _, state in trip.db(
            "select request_id, state from infrx.jobs where request_id = %s", request_id)
            if state in pilotbox.FINISHED), None), timeout, f"job {request_id} terminal")
    except AssertionError as late:
        raise AssertionError(f"{late}; {diagnose(trip, request_id)}") from None


def diagnose(trip, request_id: str) -> dict:
    """The durable facts about a stuck job, for the failure message (ids and states only)."""
    return {
        "now": str(trip.one("select infrx.now()")[0]),
        "job": trip.db("select state, attempts, published, settlement_state, outcome_cause, "
                       "preparation_deadline_at::text, queue_deadline_at::text from infrx.jobs "
                       "where request_id = %s", request_id),
        "attempts": trip.db("select kind, generation, worker_id, expires_at::text, "
                            "released_at is not null from infrx.attempts where job_id = %s "
                            "order by kind, generation", request_id),
        "outbox": trip.db("select kind, claimed_at is not null, acknowledged_at is not null "
                          "from infrx.outbox where aggregate_id = %s order by created_at",
                          request_id)}


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
    # a report printed with a non-zero exit (a dry run's blockers) is still the answer
    text = (done.stdout if done.returncode == 0 or not done.stderr.strip()
            else done.stderr).strip().splitlines()
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
    """One `MediaCollector` pass in THIS (fresh) process, composed as the pilot composes the
    media store (M5: D10's `PgLifecycle` as ticket authority and content lifecycle): the box's
    object store, the job store's liveness, the processing cache of the box. What it deleted,
    by key.

    Phase 2: over a durable ticket authority the base collector has no process-local tickets
    (`MediaUploads.uploads` raises) and stops before any delete - the durable retention pass
    over the content rows is M6's. That answers `{"blocked": "M6", ...}`: a pass that cannot
    run is not a pass that kept everything (never a vacuous green)."""
    from infrx.config import from_env
    from infrx.gateway import pilot
    from infrx.media.gc import MediaCollector
    from infrx.media.prepare import ProcessingCache
    from infrx.media.uploads import MediaUploads
    from infrx.state.jobstore import PgJobStore, connector
    settings = from_env()
    limits = settings.pilot
    lifecycle = pilot._pg_lifecycle(connector(limits.database_url), limits) \
        if hasattr(pilot, "_pg_lifecycle") else None
    store = MediaUploads(pilot.object_store(settings), limits=limits, uploads=lifecycle,
                         content=lifecycle,
                         cache=ProcessingCache(limits.processing_cache_dir,
                                               ttl_s=limits.processing_cache_ttl_s))
    if lifecycle is not None and not hasattr(store.tickets, "records"):
        return {"blocked": "M6", "why": "the durable ticket authority has no process-local "
                "records; the base MediaCollector stops before any delete (gc.py) and no "
                "durable retention pass is in the tree"}
    jobs = PgJobStore(connector(limits.database_url), limits=limits)
    swept = await MediaCollector(store, is_live=jobs.is_live, grace_s=grace_s).sweep()
    return {"deleted": swept.deleted, "uploads_expired": swept.uploads_expired}


def collector_blocked(answers: list[dict]) -> None:
    """BLOCKED[<lane>] when a collector process answered that its pass cannot run here."""
    lanes = sorted({answer["blocked"] for answer in answers if "blocked" in answer})
    if lanes:
        blocked(*lanes, why=next(a["why"] for a in answers if "blocked" in a))


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
