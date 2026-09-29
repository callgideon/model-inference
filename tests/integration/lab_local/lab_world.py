"""LAB-LOCAL's world: the whole Lab stood up locally with EVERY switch ON (the E4-ON
composition), on the e3l block's real services (runner.py provisions them).

What runs, each as its own process on this stack:

* the gateway (E3C's composed box, `world.Box`) with every gateway switch ON, on 0021's
  dedicated `infrx_runtime` login (`ROLLOUT_ROUTING` refuses any other);
* the consumer worker (`python -m infrx.worker`) with `TRACE_PUMPS` and `LAB_EVAL_WORKER` ON;
* every Lab worker role (`python -m infrx.lab.workers <role>`) on its own env, as its unit;
* the Lab control factory (`infrx.lab.control.app:create_app`, E3L's `control_box.py`);
* the Lab web (`apps/lab`'s production build, `next start`) behind a local TLS terminator (the
  pinned Caddy, `tls internal`): production refuses an http origin, so the pages are served
  over https exactly as the Lab's own origin would be.

The one stand-in is `SupabaseStandIn`: this stack runs no GoTrue, so `/auth/v1/user` answers a
live HS256 token of this stack's PostgREST secret as that user (E3L's session rule) and every
other path is proxied to PostgREST (`/rest/v1` stripped, as hosted Supabase's gateway does).
The engine, teacher and training connectors are local fakes: E2's controlled vLLM, J2's judge
fake as the teacher, and the default manual-bundle connector (no automatic trainer exists).
"""
from __future__ import annotations

import base64
import contextlib
import dataclasses
import hashlib
import hmac
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
INTEGRATION = HERE.parent
NAMESPACE = "e3l"          # borrowed under E3L's runner lock (runner.py LOCK)
KEY = "lab-on"             # tasklocal: the D harness PostgreSQL/Valkey of the E4-ON stage


def _load(package: str, name: str):
    """A sibling gate's module under a name unique to its package (WR-E7L-5)."""
    key = f"{package}.{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, INTEGRATION / package / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


operate = _load("lab_operate", "lab_world")      # E3L's world: seeded providers, sessions
world, stack, harness = operate.world, operate.stack, operate.harness

#: Every deployment switch (08 §5), where it is read, and ON here. `test_lab_local_runner`
#: holds this equal to `DeploymentSettings`' boolean fields, so a new switch the composition
#: forgets fails layer 1.
GATEWAY_SWITCHES = ("FEEDBACK_API", "TRACE_EXPORT_API", "LAB_CONTROL", "LAB_TRACES",
                    "ROLLOUT_ROUTING", "LAB_EVALS", "LAB_PIPELINES", "LAB_RELEASES",
                    "LAB_CHECKPOINTS", "LAB_DATASETS", "LAB_TEACHERS")
WORKER_SWITCHES = ("TRACE_PUMPS", "LAB_EVAL_WORKER")
#: Switches the brief names that this base does not carry yet: recorded, never claimed.
PENDING_SWITCHES: dict[str, str] = {}
ROLES = ("eval", "checkpoints", "judge", "annotation", "training", "rollout", "datasets")
#: Roles whose work source is not on this base: R198 says each refuses by name (exit 2, R211:
#: never restarted). Each case proves that refusal and stays NOT RUN until the lane lands.
PENDING_ROLES = {
    "checkpoints": ("WR-B3-3", "registry adapter and L3's dev deployer"),
    "training": ("P-11", "training has no worker pass"),
    "rollout": ("WR-LSQ-9", "the rollout pass needs"),
}
#: The consumer worker's Lab seam (R198): refuses by name, so Lab work never runs there.
EVAL_SEAM_REFUSAL = "LAB_EVAL_WORKER needs an evaluator source"

BLOCK = harness.PORT_RANGE.start
STANDIN_PORT = BLOCK + 50            # the Supabase stand-in (auth + REST)
LAB_PORT = BLOCK + 60                # next start, loopback http
LAB_TLS_PORT = BLOCK + 61            # the TLS terminator in front of it (the Lab origin)
TEACHER_PORT = BLOCK + 62            # J2's judge fake as the annotation teacher
LAB_GATEWAY_OFFSETS = (2, 5, 6, 7)   # the Lab-routes gateway: stack.GATEWAY_PORT + 2 (57042) ...
ROLE_PORTS = {role: BLOCK + 63 + n for n, role in enumerate(ROLES)}     # 57063-57069
LAB_ORIGIN = f"https://localhost:{LAB_TLS_PORT}"
STANDIN_URL = f"http://127.0.0.1:{STANDIN_PORT}"
GATEWAY_URL = f"http://127.0.0.1:{stack.GATEWAY_PORT}"
TLS_CONTAINER = "infrx-e3llab-tls"   # not `infrx-e3l-*`: E2's guard would call it foreign
CONTROL_LOGIN = "infrx_lab_control"   # 0043 (WR-I2L-4) + 0044: the control factory's login
CHECKPOINT_KEY = "lab-local-key"
CHECKPOINT_SECRET = "6c61622d6c6f63616c2d636865636b706f696e742d7365637265742d3030303031"


def clickhouse_url() -> str:
    return (f"http://{harness.CH_USER}:{harness.CH_PASSWORD}@127.0.0.1:"
            f"{harness.PORTS['clickhouse_http']}/{harness.CH_DATABASE}")


def switch_env(spool: Path) -> dict[str, str]:
    """Every switch ON, with what each needs on this stack (names, local literals only)."""
    return {**{name: "true" for name in GATEWAY_SWITCHES + WORKER_SWITCHES},
            "CLICKHOUSE_URL": clickhouse_url(), "S3_TRACE_BUCKET": stack.media_bucket(),
            "TRACE_SPOOL_DIR": str(spool),
            "LAB_TEACHER_URL": f"http://127.0.0.1:{TEACHER_PORT}",
            "LAB_CHECKPOINT_KEYS": json.dumps({CHECKPOINT_KEY: {
                "provider_org_id": operate.PROVIDER_A, "secret": CHECKPOINT_SECRET}})}


def claims(token: str) -> dict | None:
    """A live HS256 token of this stack's secret, with a subject; else None."""
    try:
        head, body, sig = token.split(".")
        mac = hmac.new(stack.JWT_SECRET.encode(), f"{head}.{body}".encode(),
                       hashlib.sha256).digest()
        if not hmac.compare_digest(base64.urlsafe_b64encode(mac).rstrip(b"=").decode(), sig):
            return None
        found = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return None
    return found if found.get("exp", 0) > time.time() and found.get("sub") else None


class SupabaseStandIn:
    """`/auth/v1/user` judged here; everything else proxied to PostgREST (`/rest/v1` off)."""

    def __init__(self, rest_url: str, port: int = STANDIN_PORT) -> None:
        rest = rest_url.rstrip("/")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, status: int, body: bytes, headers=()) -> None:
                self.send_response(status)
                for name, value in headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _proxy(self) -> None:
                if self.path.split("?")[0] == "/auth/v1/user":
                    token = (self.headers.get("authorization") or "").removeprefix("Bearer ")
                    found = claims(token)
                    body = json.dumps({"id": found["sub"], "aud": found["role"],
                                       "role": found["role"]} if found
                                      else {"msg": "invalid JWT"}).encode()
                    return self._answer(200 if found else 401, body,
                                        [("Content-Type", "application/json")])
                path = self.path.removeprefix("/rest/v1")
                length = int(self.headers.get("content-length") or 0)
                data = self.rfile.read(length) if length else None
                keep = {k: v for k, v in self.headers.items()
                        if k.lower() not in ("host", "content-length", "connection")}
                request = urllib.request.Request(rest + path, data=data, headers=keep,
                                                 method=self.command)
                try:
                    with urllib.request.urlopen(request, timeout=30) as answer:
                        status, body, headers = answer.status, answer.read(), answer.headers
                except urllib.error.HTTPError as refused:
                    status, body, headers = refused.code, refused.read(), refused.headers
                self._answer(status, body, [(k, v) for k, v in headers.items()
                                            if k.lower() in ("content-type", "content-range")])

            do_GET = do_POST = do_PATCH = do_DELETE = do_HEAD = _proxy   # noqa: N815

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{port}"            # STANDIN_URL on the default port

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def session(user: str) -> str:
    return operate.session(user)


def session_cookie(user: str) -> str:
    """The Lab's `sb-infrx-lab-auth` cookie as @supabase/ssr writes it (`base64-` + JSON)."""
    token = session(user)
    payload = {"access_token": token, "refresh_token": "lab-local-no-refresh",
               "token_type": "bearer", "expires_in": 3600,
               "expires_at": int(time.time()) + 3600,
               "user": {"id": user, "aud": "authenticated", "role": "authenticated"}}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"sb-infrx-lab-auth=base64-{raw}"


# ------------------------------------------------------------------ processes


@dataclasses.dataclass
class Proc:
    name: str
    process: subprocess.Popen
    log: Path

    def tail(self, lines: int = 12) -> str:
        text = self.log.read_text(errors="replace") if self.log.exists() else ""
        return " | ".join(text.strip().splitlines()[-lines:])

    def stop(self) -> int | None:
        if self.process.poll() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(os.getpgid(self.process.pid), sig)
                try:
                    self.process.wait(timeout=20)
                    break
                except subprocess.TimeoutExpired:
                    continue
        return self.process.returncode


def spawn(name: str, argv: list[str], env: dict, workdir: Path, cwd: Path) -> Proc:
    log = workdir / f"{name}.log"
    with log.open("wb") as sink:
        process = subprocess.Popen(argv, env=env, stdout=sink, stderr=subprocess.STDOUT,
                                   cwd=str(cwd), start_new_session=True)
    return Proc(name, process, log)


def wait_ready(proc: Proc, url: str, timeout: float = 60.0, verify: bool = True) -> str | None:
    """None once `url` answers 200; else why not (the process exited, or the last answer)."""
    import httpx
    end, last = time.monotonic() + timeout, "no answer"
    while time.monotonic() < end:
        if proc.process.poll() is not None:
            return f"exited {proc.process.returncode}: {proc.tail()}"
        try:
            answer = httpx.get(url, timeout=3.0, verify=verify)
            if answer.status_code == 200:
                return None
            last = f"{answer.status_code} {answer.text[:200]}"
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(0.2)
    return f"not ready in {timeout:.0f}s: {last} {proc.tail()}"


def role_env(role: str, trip, lab_dsn: str, spool: Path) -> dict[str, str]:
    """The role's env file, as its unit hands it over: nothing of the gateway's (E6L j09)."""
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("AWS_", "LAB_", "DATABASE_", "S3_", "INFRX_", "CLICKHOUSE",
                                 "JUDGE_", "TRACE_"))}
    return {**base, **stack.s3_env(), "PYTHONPATH": str(harness.API_ROOT),
            "LAB_DATABASE_URL": lab_dsn, "LAB_WORKER_HEALTH_PORT": str(ROLE_PORTS[role]),
            "LAB_S3_BUCKET": stack.media_bucket(), "LAB_S3_ENDPOINT": harness.s3_endpoint(),
            "LAB_S3_PREFIX": trip.box.env["S3_MEDIA_PREFIX"],
            "LAB_EVAL_ENDPOINT_URL": trip.box.url + "/v1",
            "LAB_EVAL_ENDPOINT_KEY": trip.world.alpha.secret,
            "JUDGE_PROVIDER_URL": f"http://127.0.0.1:{TEACHER_PORT}",
            "LAB_TEACHER_URL": f"http://127.0.0.1:{TEACHER_PORT}",
            "CLICKHOUSE_URL": clickhouse_url(), "S3_TRACE_BUCKET": stack.media_bucket(),
            "LAB_OPERATOR_ID": operate.OPERATOR,
            "LAB_ANNOTATION_TEACHER": "dry-run", "LAB_TRAINING_CONNECTOR": "manual-bundle",
            "PYTHONUNBUFFERED": "1"}


@contextlib.contextmanager
def teacher():
    """J2's judge fake (tests/j/submit/judge_fake.py) on this block: the local teacher."""
    if str(harness.API_ROOT) not in sys.path:
        sys.path.insert(0, str(harness.API_ROOT))
    from tests.j.submit.judge_fake import JudgeFake
    fake = JudgeFake(TEACHER_PORT)
    try:
        yield fake
    finally:
        fake.close()


@contextlib.contextmanager
def lab_tls(workdir: Path):
    """The pinned Caddy terminating TLS for the Lab origin (`tls internal`), host network."""
    image = next(line.split("=", 1)[1].split()[0] for line in
                 (harness.API_ROOT / "deploy" / "lib.sh").read_text().splitlines()
                 if line.startswith("CADDY_IMAGE="))
    subprocess.run(["docker", "rm", "-f", TLS_CONTAINER], capture_output=True)
    done = subprocess.run(
        ["docker", "run", "-d", "--pull", "never", "--name", TLS_CONTAINER, "--network", "host",
         "--label", f"ai.infrx.lab-local.checkout={harness.REPO_ROOT}", image, "caddy",
         "reverse-proxy", "--from", f"localhost:{LAB_TLS_PORT}", "--to",
         f"127.0.0.1:{LAB_PORT}", "--internal-certs", "--disable-redirects"],
        capture_output=True, text=True)
    (workdir / "lab-tls.log").write_text(done.stdout + done.stderr)
    if done.returncode != 0:
        raise RuntimeError(f"the Lab TLS terminator did not start: {done.stderr[-300:]}")
    try:
        yield LAB_ORIGIN
    finally:
        logs = subprocess.run(["docker", "logs", TLS_CONTAINER], capture_output=True, text=True)
        (workdir / "lab-tls.log").write_text(logs.stdout + logs.stderr)
        subprocess.run(["docker", "rm", "-f", TLS_CONTAINER], capture_output=True)


def control_url(port: int | None = None) -> str:
    """The control factory's base URL: the port `ControlService.start` bound (the first free
    of E3L's CONTROL_PORTS), its first by default (the build inlines no server name)."""
    return f"http://127.0.0.1:{port or operate.CONTROL_PORTS[0]}"


def lab_control_dsn(database: str, connect=None) -> str:
    """LDP-R4: the control factory on its own login, 0043's `infrx_lab_control` (noinherit,
    connection limit 10, exactly its grants), given LOGIN and a fresh password here as the
    operator does out of band (never the owner DSN). The password stays in memory and the
    factory's environment."""
    import secrets
    from urllib.parse import urlsplit

    from psycopg import sql
    if connect is None:
        import psycopg
        connect = psycopg.connect
    owner, secret = harness.pg_dsn(database), secrets.token_hex(16)
    with connect(owner, autocommit=True) as conn:
        conn.execute(sql.SQL("alter role {} login password {}").format(
            sql.Identifier(CONTROL_LOGIN), sql.Literal(secret)))
    parts = urlsplit(owner)
    return owner.replace(f"{parts.username}:{parts.password}@", f"{CONTROL_LOGIN}:{secret}@", 1)


def lab_web_env(api_url: str, supabase_url: str, control: str | None = None) -> dict[str, str]:
    """The Lab web's names (infra/lab/app/lab.json `lab-web`), every API URL set."""
    return {"LAB_CONTROL_URL": control or control_url(), "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"),
            "NEXT_TELEMETRY_DISABLED": "1", "NEXT_PUBLIC_LAB_URL": LAB_ORIGIN,
            "NEXT_PUBLIC_SUPABASE_URL": supabase_url,
            "NEXT_PUBLIC_SUPABASE_ANON_KEY": stack.jwt("anon", ttl_s=12 * 3600),
            **{name: api_url for name in ("LAB_DATASETS_API_URL", "LAB_TRACES_API_URL",
                                          "LAB_EVALS_API_URL", "LAB_PIPELINES_API_URL",
                                          "LAB_RELEASES_API_URL")}}


# ------------------------------------------------------------------ the composition


@contextlib.contextmanager
def composition(workdir: Path, login_probe=None):
    """Everything above up, every switch ON. Yields a namespace with the trip, the processes
    and why each one that did not come up refused (the findings; never patched here).
    `login_probe(url)` runs against the control factory on its own login while it is up
    (WR-LCR-5: the box's only /lab/v1/* server, R245); its answer is `login_families`."""
    spool = workdir / "spool"
    spool.mkdir(parents=True, exist_ok=True)
    env = switch_env(spool)
    refused: dict[str, str] = {}
    procs: dict[str, Proc] = {}
    with SupabaseStandIn(stack.postgrest_url(stack.JOURNEY_POSTGREST_PORT)) as standin, \
            teacher(), world.composed(workdir, start=(), runtime_login=True, SUPABASE_URL=standin.url,
                           **env) as trip:
        operate.seed_lab(trip)
        seam = labgw = None
        try:
            try:                                  # R198: the consumer worker's Lab seam
                trip.box.start("worker", timeout=30.0)
                seam = "started"
            except (RuntimeError, AssertionError) as failed:
                seam = str(failed)[:1500]
            trip.box.stop("worker")
            for role, extra in (("worker", {"LAB_EVAL_WORKER": "false"}), ("gateway", {})):
                try:
                    trip.box.start(role, timeout=90.0, **extra)
                except (RuntimeError, AssertionError) as failed:
                    refused[role] = str(failed)[:1500]
            # The Lab routes on the owner login with ROLLOUT_ROUTING off: every other switch ON
            # (LDP-F1: on infrx_runtime, L2's RPCs are not granted, so the all-switches
            # gateway refuses every Lab route).
            for offset in LAB_GATEWAY_OFFSETS:    # the block is in the ephemeral range
                labgw = world.second_gateway(trip.box, port_offset=offset)
                try:
                    # WR-LC-LOCAL: not the capture gateway - the main one holds the spool's
                    # lock (one gateway process per TRACE_SPOOL_DIR, lab-capture (c)).
                    labgw.start("gateway", timeout=90.0, ROLLOUT_ROUTING="false",
                                TRACE_PUMPS="false",
                                DATABASE_URL=harness.pg_dsn(trip.world.database))
                    refused.pop("lab-gateway", None)
                    break
                except (RuntimeError, AssertionError) as failed:
                    refused["lab-gateway"] = str(failed)[:1500]
                    if "address already in use" not in str(failed):
                        break
            lab_dsn = harness.pg_dsn(trip.world.database)
            for role in ROLES:
                proc = spawn(f"lab-{role}", [sys.executable, "-m", "infrx.lab.workers", role],
                             role_env(role, trip, lab_dsn, spool), workdir, harness.API_ROOT)
                procs[f"lab-{role}"] = proc
            for role in ROLES:
                why = wait_ready(procs[f"lab-{role}"],
                                 f"http://127.0.0.1:{ROLE_PORTS[role]}/readyz", 45.0)
                if why:
                    refused[f"lab-{role}"] = why
            with operate.control_service(trip, workdir) as (control, _verifier):
                control.env["INFRX_LAB_SUPABASE_URL"] = standin.url
                control.env["INFRX_LAB_ORIGIN"] = LAB_ORIGIN
                # LDP-R4: the same factory on its own login, 0043's infrx_lab_control, FIRST
                # and alone (a later start would find the owner-login factory answering on
                # the port it could not bind); killed, then the owner-login one below serves
                # the families, judged apart from LDP-F7. o04's login case reads it.
                (workdir / "control-login").mkdir(exist_ok=True)
                own = operate.ControlService(trip, workdir / "control-login", standin.url)
                own.env.update(control.env, INFRX_LAB_DATABASE_URL=lab_control_dsn(trip.world.database))
                login_families = None
                try:
                    own.start(E3L_ENGINE_URL=trip.engine.base_url)
                    if login_probe is not None:
                        login_families = login_probe(str(own.http.base_url))
                except (RuntimeError, AssertionError) as failed:
                    refused["lab-control-login"] = str(failed)[:1500]
                finally:
                    own.kill()
                try:
                    control.start(E3L_ENGINE_URL=trip.engine.base_url)
                except (RuntimeError, AssertionError) as failed:
                    refused["lab-control"] = str(failed)[:1500]
                yield types.SimpleNamespace(trip=trip, procs=procs, refused=refused,
                                            control=control, standin=standin, env=env,
                                            workdir=workdir, seam=seam, labgw=labgw,
                                            login_families=login_families)
        finally:
            for proc in procs.values():
                proc.stop()
            if labgw is not None:
                for role in list(labgw.processes):
                    labgw.stop(role)


@contextlib.contextmanager
def lab_web(workdir: Path, api_url: str, supabase_url: str, control: str | None = None):
    """`next start` on the runner's production build, behind the TLS terminator."""
    lab = harness.REPO_ROOT / "apps" / "lab"
    if not (lab / ".next" / "BUILD_ID").exists():
        world.invalid("the Lab web is not built: the runner's lab-build stage makes it")
    proc = spawn("lab-web", [str(lab / "node_modules" / ".bin" / "next"), "start", "-H",
                             "127.0.0.1", "-p", str(LAB_PORT)],
                 lab_web_env(api_url, supabase_url, control), workdir, lab)
    try:
        with lab_tls(workdir) as origin:
            why = wait_ready(proc, f"http://127.0.0.1:{LAB_PORT}/", 90.0)
            yield types.SimpleNamespace(proc=proc, origin=origin, why=why)
    finally:
        proc.stop()
