"""AP-11 isolated mode: ap11's own task-local stack, composed for one runner invocation.

* PostgreSQL on 57567 (`infrx-ap11-postgres`, the Supabase image by digest, through
  apps/infrx-api/tests/d/pgharness.py under INFRX_D_TASK=ap11): every migration 0001-0059,
  GoTrue's `auth.users` columns and the hosted `auth.uid()` (as E3B's template), the
  PROVISIONAL Marlin seed (P-01: a label, never a price).
* Valkey on 57568 (`infrx-ap11-valkey`) and MinIO on 57569 (`infrx-ap11-s3`), E2's pinned
  digests, labelled with this checkout; only containers carrying the label are ever removed.
* ClickHouse (`infrx-ap11-clickhouse`, the E2 digest) with the three trace schemas, ONLY when
  ap11's tasklocal key names a `clickhouse` port (WR-AP11C-1); without it the world composes
  no trace storage and says so (config `traces`/`traces_missing`).
* tests/integration/fake_vllm.py (the controlled engine), the gateway and the worker as their
  own processes (backend/pilotbox.py's PilotBox) with the wave-7 switches on - IDENTITY_API,
  AUTH_FACADE, CONSOLE_READS, CONSOLE_ACTIONS_API, CONSOLE_DATA_USE, and TRACE_PUMPS with the
  trace storage - and the Lab control unit (`infrx.lab.control.app`, the box's
  infrx-lab-control) as a third process with LAB_JUDGE_API (and LAB_ARTIFACTS, composed once
  api-artifacts-2's composition is on the base: the world probes the unit and lists AP-04 in
  `composed` only when it answers). A loopback edge stands in for the Supabase origin: GoTrue's
  `GET /auth/v1/user` over HS256 sessions this world signs and `GET /auth/v1/settings`, and
  PostgREST's `/api_keys?key_hash=eq.` read/touch over the database (no PostgREST port is on
  ap11's key). Host processes bind loopback ports the kernel assigns, never another lane's.

FIXTURES, declared in the config and named in the verdict - each stands in for something no
API on the base provides, never for an API that is: the individuals' verified sign-up and the
identity-provider sessions (GoTrue's own flow; the auth facade forwards to it), the operator's
two feature flags (no flag API), the NemoStation administrator membership (operator
onboarding), the seeded Marlin listing (stage 07's publication is AP-06), and - while
`lab/control/app.py` composes `actors=None` - AP-01's SessionActors on the Lab unit (the
judge/review family answers 503 without it; api-identity-2 composes it, WR-AP11C-2). Keys,
grants, data use, judge and reviews go through their APIs. Nothing here is real-GPU,
real-judge or hosted evidence.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import importlib.util
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from api_lifecycle.stages import Blocked

HERE = Path(__file__).resolve().parent
INTEGRATION = HERE.parent
REPO = INTEGRATION.parent.parent
TASK = "ap11"
LABEL = "ai.infrx.ap11.checkout"
VALKEY_IMAGE = ("valkey/valkey@sha256:"
                "d2e18f3410b6f616de1417f570fa55261af2898b9c5b2cfb6781ce2373ea43d1")
MINIO_IMAGE = ("pgsty/minio@sha256:"
               "b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372")
CLICKHOUSE_IMAGE = ("clickhouse/clickhouse-server@sha256:"
                    "87e0a5b72f5465b18eacca7c76850e7ff551c9795c50e451f5646299e5e24146")
S3_USER, S3_PASSWORD, BUCKET = "infrxap11minio", "infrx-ap11-local-secret", "infrx-ap11"
TRACE_BUCKET, CH_USER, CH_PASSWORD, CH_DATABASE = ("infrx-ap11-traces", "infrx_ap11",
                                                  "infrx-ap11-local", "infrx_ap11")
MODEL_UUID = "d0000001-0000-4000-8000-000000000001"         # the seed's Marlin model
STAND_IN_ENV = "INFRX_AP11_STAND_IN"                        # where lab_unit marks a stand-in
NO_CLICKHOUSE = ("WR-AP11C-1: tasklocal key ap11 names no ClickHouse port, so the world "
                 "composes no trace storage")
MODEL = "nemostation/marlin-2b"
CARD = "rc_marlin2b_2026_09_provisional"
PROVIDER = "b0000001-0000-4000-8000-000000000001"           # the seed's NemoStation
HOSTED_AUTH_UID = (
    "create or replace function auth.uid() returns uuid language sql stable as $f$ select "
    "coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''), nullif(nullif("
    "current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub', ''))::uuid $f$")


def services():
    from infrx.contracts.tasklocal import local_services
    return local_services(TASK)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300)
    if check and done.returncode != 0:
        raise Blocked(f"BLOCKED[stack] docker {args[0]}: {done.stderr.strip()[-300:]}")
    return done


def _ours(name: str) -> bool | None:
    """True: ours; False: someone else's; None: absent."""
    found = _docker("inspect", "--format", "{{json .Config.Labels}}", name, check=False)
    if found.returncode != 0:
        return None
    return (json.loads(found.stdout or "null") or {}).get(LABEL) == str(REPO)


@contextlib.contextmanager
def container(service: str, image: str, inner_port: int, *args: str, env: tuple = ()):
    spec = services()[service]
    owner = _ours(spec.container)
    if owner is False:
        raise Blocked(f"BLOCKED[stack] {spec.container} belongs to another checkout")
    if owner:
        _docker("rm", "-f", "-v", spec.container, check=False)      # a crashed run of ours
    _docker("run", "-d", "--name", spec.container, "--label", f"{LABEL}={REPO}",
            "-p", f"127.0.0.1:{spec.host_port}:{inner_port}", *env, image, *args)
    try:
        yield spec.host_port
    finally:
        if _ours(spec.container):
            _docker("rm", "-f", "-v", spec.container, check=False)


def _wait(probe, what: str, timeout: float = 60.0) -> None:
    end, last = time.monotonic() + timeout, ""
    while time.monotonic() < end:
        try:
            if probe():
                return
        except Exception as exc:                   # noqa: BLE001 - retried until the deadline
            last = type(exc).__name__
        time.sleep(0.3)
    raise Blocked(f"BLOCKED[stack] {what} not ready within {timeout}s ({last})")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ PostgreSQL

def database():
    """pgharness on ap11's port with the Supabase image; (harness module, dsn)."""
    os.environ["INFRX_D_TASK"], os.environ["INFRX_D1_IMAGE"] = TASK, "supabase"
    pg = _load("ap11_pgharness", REPO / "apps/infrx-api/tests/d/pgharness.py")
    why = pg.unavailable()
    if why:
        raise Blocked(f"BLOCKED[stack] {why}")
    pg.ensure()
    pg.recreate(pg.DATABASE)
    pg._sb(pg.DATABASE, "alter table auth.users add column if not exists email_confirmed_at "
                        "timestamptz, add column if not exists deleted_at timestamptz")
    pg._sb(pg.DATABASE, HOSTED_AUTH_UID)
    from infrx.state import migrations
    files = sorted((REPO / "apps/app/supabase/migrations").glob("*.sql"))
    pg.apply(pg.DATABASE, tuple((f.name, f.read_text()) for f in files)
             + (("seed_marlin_provisional", migrations.SEED_MARLIN.read_text()),))
    return pg, pg.dsn(), [f.name for f in files]


def individuals(pg, names=("alpha", "beta")) -> dict[str, str]:
    """FIXTURE: verified individuals (GoTrue's sign-up and verification) and the operator's
    flags (the signup grant, credit admission, the Lab's judge submissions); name -> user.
    Their grant and keys are the API's (stage 08)."""
    found = {}
    with pg.connect(pg.DATABASE) as conn:
        for flag in ("signup_grant", "credit_admission", "lab_submission"):  # absent = off
            conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                         "values (%s, true, 'ap11', 'ap11 isolated world') on conflict (name) "
                         "do update set enabled = true, updated_by = 'ap11', "
                         "reason = 'ap11 isolated world'", (flag,))
        for name in names:
            found[name] = str(uuid.uuid4())
            conn.execute("insert into auth.users (id, email, email_confirmed_at) values "
                         "(%s, %s, now())", (found[name], f"{name}@ap11.invalid"))
    return found


def lab_users(pg) -> dict[str, str]:
    """FIXTURE: NemoStation's administrator and an outsider (no membership); name -> user."""
    users = {"admin": str(uuid.uuid4()), "outsider": str(uuid.uuid4())}
    with pg.connect(pg.DATABASE) as conn:
        for name, user in users.items():
            conn.execute("insert into auth.users (id, email, email_confirmed_at) values "
                         "(%s, %s, now())", (user, f"{name}@ap11.invalid"))
        conn.execute("insert into infrx.provider_memberships (provider_org_id, user_id, role, "
                     "granted_by, granted_at) values (%s, %s, 'administrator', 'ap11', "
                     "now() - interval '1 day')", (PROVIDER, users["admin"]))
    return users


# ------------------------------------------------------------------ the Supabase edge

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def session(user: str, secret: str, ttl_s: int = 3600) -> str:
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"sub": user, "role": "authenticated", "aud": "authenticated",
                            "exp": int(time.time()) + ttl_s}).encode())
    mac = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(mac)}"


def claims(token: str, secret: str) -> dict | None:
    try:
        head, body, mac = token.split(".")
        want = _b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(want, mac):
            return None
        found = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return None
    return found if found.get("exp", 0) > time.time() and found.get("sub") else None


COLUMNS = frozenset({"id", "org_id", "revoked_at", "audience", "user_id", "created_by",
                     "provider_org_id", "endpoint_id", "scope"})


@contextlib.contextmanager
def edge(pg, secret: str):
    """GoTrue's user read and PostgREST's api_keys read/touch, loopback only."""
    from urllib.parse import parse_qs, urlsplit

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status: int, payload=None) -> None:
            body = b"" if payload is None else json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _key_hash(self, query: dict) -> str | None:
            value = (query.get("key_hash") or [""])[0]
            return value[3:] if value.startswith("eq.") else None

        def do_GET(self):  # noqa: N802
            url = urlsplit(self.path)
            if url.path == "/auth/v1/settings":         # the auth facade's availability read
                return self._send(200, {"external": {"email": True}, "disable_signup": False})
            if url.path == "/auth/v1/user":
                token = (self.headers.get("authorization") or "").removeprefix("Bearer ")
                found = claims(token, secret)
                if found is None:
                    return self._send(401, {"msg": "invalid JWT"})
                return self._send(200, {"id": found["sub"], "aud": "authenticated",
                                        "role": "authenticated"})
            query = parse_qs(url.query)
            columns = (query.get("select") or ["id"])[0].split(",")
            digest_ = self._key_hash(query)
            if url.path != "/api_keys" or digest_ is None or not set(columns) <= COLUMNS:
                return self._send(404, {"message": "not served by the ap11 edge"})
            with pg.connect(pg.DATABASE) as conn:
                cursor = conn.execute(
                    f"select {', '.join(columns)} from public.api_keys where key_hash = %s",
                    (digest_,))
                names = [c.name for c in cursor.description]
                rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
            return self._send(200, rows)

        def do_PATCH(self):  # noqa: N802
            url = urlsplit(self.path)
            digest_ = self._key_hash(parse_qs(url.query))
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if url.path != "/api_keys" or digest_ is None or set(body) != {"last_used_at"}:
                return self._send(404, {"message": "not served by the ap11 edge"})
            with pg.connect(pg.DATABASE) as conn:
                conn.execute("update public.api_keys set last_used_at = %s where key_hash = %s",
                             (body["last_used_at"], digest_))
            return self._send(204)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------------ the composition

def _box_class():
    sys.path[:0] = [p for p in (str(INTEGRATION / "backend"), str(INTEGRATION))
                    if p not in sys.path]
    pilotbox = _load("ap11_pilotbox", INTEGRATION / "backend" / "pilotbox.py")

    class Box(pilotbox.PilotBox):
        """PilotBox's gateway and worker, plus the Lab control unit as role `lab` on its own
        environment (the box's infrx-lab-control: never the runtime's settings)."""

        lab_env: dict[str, str] = {}
        lab_port = 0

        def command(self, role: str) -> tuple[list[str], str]:
            if role != "lab":
                return super().command(role)
            return ([sys.executable, "-m", "uvicorn", "--factory", "api_lifecycle.world:lab_unit",
                     "--host", "127.0.0.1", "--port", str(self.lab_port), "--log-level",
                     "warning"], f"http://127.0.0.1:{self.lab_port}/readyz")

        def start(self, role: str, timeout: float = 60.0) -> None:
            if role != "lab":
                return super().start(role, timeout)
            self.starts.setdefault("lab", 0)
            saved, self.env = self.env, self.lab_env
            try:
                super().start(role, timeout)
            finally:
                self.env = saved

        def close(self) -> None:          # the Valkey and the bucket go with their containers
            for role in list(self.processes):
                self.stop(role)
    return pilotbox, Box


def lab_unit():
    """`uvicorn --factory` target for the world's Lab unit: `infrx.lab.control.app` as the
    box runs it, except that while its composition leaves `actors=None` (this base) AP-01's
    SessionActors - the gateway's own composition, on the unit's login - is stood in, and the
    stand-in is marked for the config (a declared fixture, WR-AP11C-2)."""
    import httpx

    from infrx.console.session import PgIdentity, SessionActors
    from infrx.gateway.lab_auth import GoTrueSessions
    from infrx.lab.control import app as unit
    from infrx.state.jobstore import connector
    composed = unit._compose

    def with_actors(lab, store):
        rt, control, traces = composed(lab, store)
        if getattr(rt, "actors", None) is None:
            sessions = GoTrueSessions(httpx.AsyncClient(base_url=lab[unit.SUPABASE_URL],
                                                        timeout=httpx.Timeout(5, connect=2)),
                                      lab[unit.SUPABASE_KEY])
            rt.actors = SessionActors(sessions, PgIdentity(connector(lab[unit.DATABASE_URL],
                                                                     set_role=False)))
            Path(os.environ[STAND_IN_ENV]).write_text("SessionActors stood in")
        return rt, control, traces
    unit._compose = with_actors
    return unit.create_app()


def trace_storage(stack: contextlib.ExitStack) -> str | None:
    """ClickHouse on ap11's own port with the three trace schemas: its URL, or None when the
    key names no ClickHouse port (WR-AP11C-1)."""
    spec = services().get("clickhouse")
    if spec is None:
        return None
    import clickhouse_connect

    from infrx.traces import feedback, ship
    from infrx.traces.retention import policy
    port = stack.enter_context(container(
        "clickhouse", CLICKHOUSE_IMAGE, 8123,
        env=("-e", f"CLICKHOUSE_USER={CH_USER}", "-e", f"CLICKHOUSE_PASSWORD={CH_PASSWORD}",
             "-e", "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1")))
    connect = lambda database="default": clickhouse_connect.get_client(  # noqa: E731
        host="127.0.0.1", port=port, username=CH_USER, password=CH_PASSWORD, database=database)
    _wait(lambda: connect().command("SELECT 1") == 1, "clickhouse", timeout=90)
    connect().command(f"CREATE DATABASE IF NOT EXISTS {CH_DATABASE}")
    client = connect(CH_DATABASE)
    for schema in (ship.shipper.SCHEMA, feedback.projector.SCHEMA, policy.SCHEMA):
        for statement in (part.strip() for part in schema.read_text().split(";")):
            if statement and not all(line.strip().startswith("--") or not line.strip()
                                     for line in statement.splitlines()):
                client.command(statement)
    return f"http://{CH_USER}:{CH_PASSWORD}@127.0.0.1:{port}/{CH_DATABASE}"


def toy_artifact(root: Path) -> Path:
    """Stage 02's upload: a small safetensors-shaped directory. It verifies byte for byte; a
    serving revision of it is the API's actionable `unsupported` (stage 03 says so)."""
    root.mkdir()
    (root / "config.json").write_text(json.dumps({"architectures": ["Ap11Toy"]}))
    (root / "tokenizer.json").write_text(json.dumps({"version": "1.0", "model": {}}))
    (root / "model.safetensors").write_bytes(b"\x08\x00\x00\x00\x00\x00\x00\x00{}")
    return root


@contextlib.contextmanager
def isolated(out: Path):
    """Compose the stack; yield (config path, secrets path) in a private directory. A stack
    that cannot be composed is BLOCKED[stack] with its reason (never a product FAIL)."""
    if shutil.which("docker") is None:
        raise Blocked("BLOCKED[stack] docker is not installed")
    composing = True
    try:
        with compose(out) as provided:
            composing = False
            yield provided
    except Blocked:
        raise
    except Exception as broken:
        if not composing:
            raise
        raise Blocked(f"BLOCKED[stack] {type(broken).__name__}: {str(broken)[-400:]}") from None


@contextlib.contextmanager
def compose(out: Path):
    work = Path(tempfile.mkdtemp(prefix="infrx-ap11-world-"))
    os.chmod(work, 0o700)
    with contextlib.ExitStack() as stack:
        stack.callback(shutil.rmtree, work, True)
        pg, dsn, applied = database()
        stack.callback(pg.remove)
        valkey_port = stack.enter_context(container(
            "valkey", VALKEY_IMAGE, 6379, "valkey-server", "--save", "", "--appendonly", "no"))
        s3_port = stack.enter_context(container(
            "s3", MINIO_IMAGE, 9000, "server", "/data", "--address", ":9000",
            env=("-e", f"MINIO_ROOT_USER={S3_USER}", "-e", f"MINIO_ROOT_PASSWORD={S3_PASSWORD}")))
        s3_env = {"AWS_ACCESS_KEY_ID": S3_USER, "AWS_SECRET" "_ACCESS_KEY": S3_PASSWORD,
                  "AWS_DEFAULT_REGION": "us-east-1", "AWS_EC2_METADATA_DISABLED": "true",
                  "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull}
        _bucket(s3_port, s3_env, BUCKET)
        _bucket(s3_port, s3_env, TRACE_BUCKET)
        _wait(lambda: _ping_valkey(valkey_port), "valkey")
        clickhouse = trace_storage(stack)
        people, users = individuals(pg), lab_users(pg)
        jwt_secret, anon = secrets.token_hex(24), secrets.token_hex(16)
        edge_url = stack.enter_context(edge(pg, jwt_secret))
        sys.path.insert(0, str(INTEGRATION))
        import fake_vllm
        pilotbox, Box = _box_class()
        engine = fake_vllm.FakeVllmServer(free_port())
        engine.start()
        stack.callback(engine.stop)
        engine.control(prompt_tokens=pilotbox.ENGINE_PROMPT_TOKENS)
        (work / "cache").mkdir()
        release = secrets.token_hex(20)        # a commit-shaped label of this world's own
        s3 = {"S3_ENDPOINT_URL": f"http://127.0.0.1:{s3_port}", **s3_env}
        traces = {} if clickhouse is None else {
            "CLICKHOUSE_URL": clickhouse, "S3_TRACE_BUCKET": TRACE_BUCKET}
        env = {"INFRX_MODE": "pilot", "DATABASE_URL": dsn, "S3_MEDIA_BUCKET": BUCKET,
               "S3_MEDIA_PREFIX": f"test/{TASK}/{release[:12]}/", **s3,
               "VALKEY_URL": f"valkey://127.0.0.1:{valkey_port}/0", "ACCOUNTING_REGIME": "credit",
               "ACTIVE_RATE_CARD_VERSION": CARD, "MODEL_ID": MODEL,
               "PROCESSING_CACHE_DIR": str(work / "cache"), "USAGE_LOG": str(work / "usage.jsonl"),
               "INFRX_RELEASE_SHA": release,
               "INFRX_IMAGE": "sha256:" + hashlib.sha256(release.encode()).hexdigest(),
               "SUPABASE_URL": edge_url, "SUPABASE_SERVICE" "_ROLE_KEY": secrets.token_hex(16),
               # the wave-7 web API on the gateway (each default OFF in production)
               "IDENTITY_API": "1", "AUTH_FACADE": "1", "SUPABASE_ANON_KEY": anon,
               "CONSOLE_READS": "1", "CONSOLE_DATABASE_URL": dsn,
               "CONSOLE_CURSOR_SECRET": secrets.token_hex(24), "CONSOLE_ACTIONS_API": "1",
               "CONSOLE_DATA_USE": "1",
               **({} if clickhouse is None else {"TRACE_PUMPS": "1", **traces,
                                                 "TRACE_SPOOL_DIR": str(work / "spool")})}
        box = Box(env, engine.base_url, work, free_port())
        stack.callback(box.close)
        box.lab_port, stand_in = free_port(), work / "lab-stand-in"
        box.lab_env = {**{k: v for k, v in box.env.items() if k in os.environ}, **s3, **traces,
                       "PYTHONUNBUFFERED": "1",     # `api_lifecycle.world:lab_unit` importable
                       "PYTHONPATH": os.pathsep.join((box.env["PYTHONPATH"], str(INTEGRATION))),
                       "INFRX_LAB_DATABASE_URL": dsn, "INFRX_LAB_SUPABASE_URL": edge_url,
                       "INFRX_LAB_SUPABASE_ANON_KEY": anon, "LAB_JUDGE_API": "1",
                       "LAB_ARTIFACTS": "1", STAND_IN_ENV: str(stand_in)}
        box.start("worker")
        box.start("gateway")
        box.start("lab")
        lab_url = f"http://127.0.0.1:{box.lab_port}"
        composed = ["AP-01", "AP-02", "AP-03", "AP-07", "AP-08"]
        import httpx
        from api_lifecycle.stages import unmounted
        probe = httpx.get(f"{lab_url}/lab/v1/operations/{uuid.uuid4()}", timeout=5)
        if not unmounted(probe):                # api-artifacts-2's LAB_ARTIFACTS is composed
            composed.insert(3, "AP-04")
        clip, artifact = work / "clip.mp4", toy_artifact(work / "artifact")
        clip.write_bytes(pilotbox.clip())
        sessions = {"admin_session": session(users["admin"], jwt_secret),
                    "outsider_session": session(users["outsider"], jwt_secret),
                    "alpha_session": session(people["alpha"], jwt_secret),
                    "beta_session": session(people["beta"], jwt_secret)}
        identity = ("world.py: a verified individual (GoTrue's sign-up and verification, by "
                    "SQL) with an HS256 session of the world's GoTrue stand-in")
        fixtures = {
            "alpha_session": identity, "beta_session": identity + " (a second tenant)",
            "admin_session": "world.py: NemoStation administrator membership by SQL (operator "
                             "onboarding) and an edge session",
            "outsider_session": "world.py: a verified user without membership, edge session",
            "flags": "world.py: signup_grant, credit_admission and lab_submission enabled by "
                     "SQL (operator flags; no flag API)",
            "listing": "the PROVISIONAL Marlin seed (stage 07's publication is AP-06)",
            "judge": "dry_run: JUDGE_MODE's default on the Lab unit; no judge worker or START "
                     "job is composed, nothing is sent (live judging is P-10)"}
        if stand_in.exists():
            fixtures["lab_unit_actors"] = (
                "world.py lab_unit: AP-01's SessionActors stood in on the Lab unit, whose "
                "composition leaves actors=None on this base (WR-AP11C-2, api-identity-2)")
        config = {
            "target": f"ap11-isolated-{release[:12]}", "model": MODEL, "model_uuid": MODEL_UUID,
            "origins": {"gateway": box.url, "lab": lab_url}, "composed": composed,
            "traces": clickhouse is not None,
            "traces_missing": None if clickhouse is not None else NO_CLICKHOUSE,
            "judge": "dry_run",
            "identities": {
                "consumer_a": {"audience": "consumer", "secret": "consumer_a_key"},
                "consumer_b": {"audience": "consumer", "secret": "consumer_b_key"},
                "consumer_a_capture": {"audience": "consumer", "secret": "consumer_a_capture_key"},
                "consumer_a_web": {"audience": "session", "secret": "alpha_session"},
                "consumer_b_web": {"audience": "session", "secret": "beta_session"},
                "provider_admin": {"audience": "session", "secret": "admin_session",
                                   "provider_org_id": PROVIDER},
                "outsider": {"audience": "session", "secret": "outsider_session",
                             "provider_org_id": PROVIDER}},
            "fixtures": fixtures, "artifact": {"dir": str(artifact)},
            "media": {"clip": str(clip), "mime": "video/mp4"}, "poll_timeout_s": 120,
            "trace_timeout_s": 90,
            "pins": {"release_label": release, "migrations": f"{applied[0]}..{applied[-1]}",
                     "migration_count": len(applied), "postgres": pg.IMAGE,
                     "valkey": VALKEY_IMAGE, "s3": MINIO_IMAGE, "engine": "fake_vllm.py",
                     "clickhouse": CLICKHOUSE_IMAGE if clickhouse else None,
                     "lab_unit": "infrx.lab.control.app (LAB_JUDGE_API, LAB_ARTIFACTS)",
                     "ports": {s: v.host_port for s, v in services().items()}}}
        config_path, secrets_path = work / "config.json", work / "secrets.json"
        config_path.write_text(json.dumps(config, indent=1))
        fd = os.open(secrets_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as sink:
            json.dump(sessions, sink)
        out.mkdir(parents=True, exist_ok=True)
        (out / "world-config.json").write_text(json.dumps(config, indent=1))
        yield config_path, secrets_path
        for role in ("gateway", "worker", "lab"):
            if (work / f"{role}-1.log").exists():
                shutil.copy2(work / f"{role}-1.log", out / f"{role}.log")


def _ping_valkey(port: int) -> bool:
    with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
        conn.sendall(b"PING\r\n")
        return conn.recv(16).startswith(b"+PONG")


def _bucket(port: int, env: dict, name: str) -> None:
    import boto3
    from botocore.config import Config
    client = boto3.client("s3", endpoint_url=f"http://127.0.0.1:{port}",
                          aws_access_key_id=env["AWS_ACCESS_KEY_ID"],
                          aws_secret_access_key=env["AWS_SECRET" "_ACCESS_KEY"],
                          region_name="us-east-1", config=Config(retries={"max_attempts": 1}))
    _wait(lambda: client.list_buckets() is not None, "minio")
    with contextlib.suppress(client.exceptions.BucketAlreadyOwnedByYou):
        client.create_bucket(Bucket=name)
