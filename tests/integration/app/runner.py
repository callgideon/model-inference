#!/usr/bin/env python3
"""E3A: the APP-LOCAL runner - the App in a real browser over the composed backend world.

    apps/infrx-api/.venv/bin/python tests/integration/app/runner.py --out DIR
        [--only check,check] [--break-seam fixture-port|grant-guard]

1. **backend**: E3C's world, reused (`backend/e3c/runner.provision` + `world.composed`), in the
   free task-local block `e4b` (56800-56899, compose `infrx-e4b-*`): PostgreSQL with every
   migration, Valkey, S3-compatible store, PostgREST over a fresh clone, E2's controlled
   engine, and the gateway and worker as their own processes on the dedicated runtime login.
2. **edge** (`edge.py`): the clone's Supabase origin for the App - an auth stand-in and a proxy
   to that PostgREST - plus a loopback control API the browser specs use for the harness's
   side (the mailbox, database facts, operator CLI, engine faults, the test clock).
3. **App**: `next build` + `next start` on 127.0.0.1:56870 with ONLY the local values below
   (never a hosted URL or key; `local_env` refuses anything else).
4. **browser**: Playwright (`apps/app/tests/e2e/`), one serial journey; each test is a check.
5. **verdict**: `<out>/verdict.json` in E2C's gate shape, the manifest's E3A test_ids as
   cells. A cell's `journey` is the worst of the checks under it; its `verdict` is that, except
   for a DELEGATED cell (an oracle the journey does not exercise), which is NOT RUN unless a
   check under it fails. A check a lane has not merged yet is NOT RUN with the reason; an
   absent check is NOT RUN. Gate = worst of cells and stages (FAIL > INVALID > BLOCKED >
   NOT RUN > PASS); exit 0 / 1 / 3 / 3 / 4.
6. teardown of everything it started: no `infrx-e4b` container (`docker ps -a`) and the
   edge, control and App ports free again.

`--break-seam` removes one control and runs the checks guarding it; the gate must FAIL:
`fixture-port` answers the App's reads from recorded fixtures (a mocked C0 port) and
`grant-guard` drops the once-only signup grant guard on the clone (E3B's db09 defect).

Label: the App on real auth/DB/object/runtime adapters with a controlled engine locally - not
Marlin quality, not GPU capacity, not hosted behaviour, not a real email.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
APP_DIR = REPO / "apps" / "app"
E3C = REPO / "tests" / "integration" / "backend" / "e3c"
NAMESPACE = "e4b"
BLOCK = range(56801, 56900)                       # tasklocal TASK_BLOCKS["e4b"] extra ports
EDGE_PORT, CONTROL_PORT, APP_PORT = 56860, 56861, 56870
# The browser's App origin. Next's `nextUrl` rewrites a loopback host to `localhost`, so the App's
# own redirects (the email callback) land there; browsing 127.0.0.1 would drop its cookies.
APP_ORIGIN = f"http://localhost:{APP_PORT}"
LOCK = Path("/tmp") / f"infrx-{NAMESPACE}.app-runner.lock"
PASS, FAIL, BLOCKED, INVALID, NOT_RUN = "PASS", "FAIL", "BLOCKED", "INVALID", "NOT RUN"
RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 3, FAIL: 4}
EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}
MERGED = ("implemented", "integrated")
# The manifest's E3A test_ids (tasks.json), in its order. test_runner.py holds them equal.
TEST_IDS = ("DUR-ADMIT", "DUR-CAP", "DUR-FENCE", "DUR-OUTPUT", "DUR-SETTLE", "DUR-OUTBOX",
            "DUR-RLS", "MEDIA-SEC", "API-MODES", "API-STREAM", "CONSOLE-FLOWS", "CREDIT-GRANT",
            "CREDIT-IDENTITY", "CREDIT-UNITS", "CREDIT-RATE", "CREDIT-SPEND", "APP-JOURNEY")
# Each browser test is one check (`<check>: ...` in journey.e2e.ts). `lanes`: whose merge it
# needs (the spec skips as NOT RUN until the manifest says merged).
CHECKS = {
    "signup-verify-grant": (("CREDIT-GRANT", "CREDIT-IDENTITY", "CREDIT-UNITS",
                             "CONSOLE-FLOWS"), ()),
    "signin-claim": (("CONSOLE-FLOWS",), ()),
    "create-key": (("CONSOLE-FLOWS",), ("C3A", "U2")),
    "text-sync": (("API-MODES", "DUR-ADMIT", "DUR-SETTLE"), ()),
    "sse-stream": (("API-STREAM", "DUR-OUTPUT"), ()),
    "async-poll": (("API-MODES", "DUR-OUTPUT", "DUR-OUTBOX"), ()),
    "video-upload": (("API-MODES", "MEDIA-SEC"), ()),
    "retry": (("DUR-SETTLE", "CREDIT-SPEND"), ()),
    "usage-balance": (("CREDIT-SPEND", "CREDIT-UNITS", "CREDIT-RATE", "DUR-SETTLE",
                       "CONSOLE-FLOWS"), ()),
    "refresh": (("CONSOLE-FLOWS",), ()),
    "request-detail": (("CONSOLE-FLOWS", "DUR-OUTPUT"), ("U4",)),
    "accounting-uncertainty": (("DUR-SETTLE", "CREDIT-SPEND"), ()),
    "rate-rejection": (("DUR-CAP", "DUR-ADMIT", "DUR-FENCE", "DUR-OUTBOX"), ()),
    "low-funds": (("CREDIT-SPEND", "DUR-ADMIT"), ()),
    "provider-route-denial": (("DUR-RLS", "CONSOLE-FLOWS"), ()),
    "operator-controls": (("CONSOLE-FLOWS",), ("U3",)),
    "isolation": (("DUR-RLS", "CREDIT-IDENTITY", "CREDIT-GRANT", "MEDIA-SEC"), ()),
    "revoke-key": (("CONSOLE-FLOWS", "DUR-ADMIT"), ("C3A", "U2")),
    "expired-result": (("DUR-OUTPUT",), ()),
}
# Cells whose 04-verification oracle the journey does not exercise. The checks under them are
# a journey slice, reported as `journey`, never the oracle: NOT RUN unless one of them fails.
# ponytail: text, not an import of E3C's same-SHA verdict; E3A proper imports it (s05, s08) or
# adds journey injections (worker SIGKILL mid-lease, Valkey flush, rate publish, concurrency).
DELEGATED = {
    "DUR-CAP": "concurrent admissions across keys/orgs: the journey admits on one key, one "
               "request at a time; E3B dr02c/dr12 (a full key or index refused) and E3C s09 "
               "(concurrent grants) carry parts, no case races admissions across keys",
    "DUR-FENCE": "a stale worker generation racing a new one: the journey stops the worker "
                 "before any lease; E3C s05 (worker crash at claim/output/settle), E3B "
                 "dr03/dr05/db07/db11",
    "DUR-OUTBOX": "Valkey data or ack lost, delivery replayed, dispatcher restarted: the journey "
                  "loses nothing; E3C s05[outbox] and s08 (lost Valkey index), E3B dr13/db10",
    "CREDIT-RATE": "a rate published while jobs wait or run: the journey publishes none; E3B "
                   "dr07c/db13",
}
SEAMS = {
    "fixture-port": {"checks": ("signup-verify-grant", "text-sync", "usage-balance"),
                     "what": "the App's reads answered from recorded fixtures (the first answer "
                             "per read, replayed) instead of the live database: a mocked C0 port"},
    "grant-guard": {"checks": ("signup-verify-grant",),
                    "what": "the once-only signup grant guard dropped on the clone (E3B db09: "
                            "entitlement key, one-grant index, replay answer, once guard)"},
}
TITLE = re.compile(r"^([a-z][a-z-]+): ")
MARK = re.compile(r"\b(BLOCKED|INVALID|NOT RUN)\[([^\]]*)\]")
# Hosted Supabase's `auth.uid()` reads PostgREST v12+'s `request.jwt.claims`; the pinned image's
# reads only the legacy per-claim GUC, so through the journey PostgREST every signed-in read would
# see no user. The same replacement C0 (apps/app/tests/c/realdb/stack.py) and D10
# (tests/d/test_postgrest_d10.py) apply; E3A-WR-2 asks for it in E3C's clone template.
HOSTED_AUTH_UID = (
    "create or replace function auth.uid() returns uuid language sql stable as $f$ select "
    "coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''), nullif(nullif("
    "current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub', ''))::uuid $f$")
PER_KEY_CAP = 2                   # MAX_ACTIVE_JOBS_PER_KEY on the box: rate-rejection reaches it


class Stop(Exception):
    """A stage could not go on; it is already recorded."""


def worst(statuses) -> str:
    return max(statuses, key=RANK.__getitem__, default=NOT_RUN)


# ------------------------------------------------------------------ pure rules (test_runner.py)


def merged_lanes(tasks: dict) -> set[str]:
    return {task["id"] for task in tasks.get("tasks", []) if task.get("status") in MERGED}


def local_env(env: dict[str, str]) -> dict[str, str]:
    """The App's environment, refused unless every URL is loopback inside the block and no
    name points at a hosted service. Returns it unchanged."""
    for name, value in env.items():
        if value.startswith(("http://", "https://")):
            url = urlsplit(value)
            if url.scheme != "http" or url.hostname != "127.0.0.1" or url.port not in BLOCK:
                raise ValueError(f"{name} is not a loopback URL in the {NAMESPACE} block")
        if re.search(r"supabase\.co|amazonaws\.com|callbill\.ai|vercel\.app", value):
            raise ValueError(f"{name} names a hosted service")
    return env


def case_status(result: dict, annotations: list[dict]) -> tuple[str, str]:
    """One Playwright test result -> (status, reason)."""
    status = result.get("status")
    if status == "passed":
        return PASS, ""
    if status == "skipped":
        text = " ".join(a.get("description") or "" for a in annotations if a.get("type") == "skip")
        mark = MARK.search(text)
        return ({"BLOCKED": BLOCKED, "INVALID": INVALID}.get(mark.group(1), NOT_RUN)
                if mark else NOT_RUN, text.strip() or "skipped without a reason")
    message = " ".join((e.get("message") or "") for e in result.get("errors") or [])
    message = re.sub(r"\x1b\[[0-9;]*m", "", message).strip()[:600]
    if "INVALID[" in message:
        return INVALID, message
    return FAIL, message or f"status {status}"


def specs(suite: dict):
    for spec in suite.get("specs", []):
        yield spec
    for child in suite.get("suites", []):
        yield from specs(child)


def classify(report: dict | None, selected: set[str] | None = None) -> dict:
    """Per check: status and reason, from Playwright's JSON report."""
    checks = {name: {"status": NOT_RUN, "reason": "absent from the browser report"}
              for name in CHECKS}
    for suite in (report or {}).get("suites", []):
        for spec in specs(suite):
            match = TITLE.match(spec.get("title", ""))
            if not match or match.group(1) not in checks:
                continue
            for test in spec.get("tests", []):
                results = test.get("results") or [{"status": "skipped"}]
                status, reason = case_status(results[-1], test.get("annotations") or [])
                checks[match.group(1)] = {"status": status, "reason": reason}
    for name, entry in checks.items():
        if selected is not None and name not in selected:
            entry.update(status=NOT_RUN, reason="not selected (--only / --break-seam)")
    return checks


def cells(checks: dict) -> list[dict]:
    """The E3A test_ids: `journey` = worst of the checks under each (APP-JOURNEY is every
    check); `verdict` = that, or for a DELEGATED cell never better than NOT RUN."""
    out = []
    for test_id in TEST_IDS:
        under = [name for name, (ids, _) in CHECKS.items()
                 if test_id == "APP-JOURNEY" or test_id in ids]
        journey = worst(checks[name]["status"] for name in under)
        reasons = [f"{name}: {checks[name]['status']} - {checks[name]['reason']}"
                   for name in under if checks[name]["status"] != PASS]
        if test_id in DELEGATED:
            reasons.insert(0, f"NOT RUN[delegated] {DELEGATED[test_id]}; rerun on the merged "
                              f"SHA (journey slice {', '.join(under)}: {journey})")
        out.append({"id": test_id, "journey": journey, "checks": under, "reasons": reasons,
                    "verdict": worst([journey, NOT_RUN]) if test_id in DELEGATED else journey})
    return out


def seam_stage(seam: str, checks: dict) -> dict:
    guarded = {name: checks[name]["status"] for name in SEAMS[seam]["checks"]}
    detected = FAIL in guarded.values()
    return {"stage": f"seam:{seam}", "verdict": FAIL if detected else PASS, "guarded": guarded,
            "what": SEAMS[seam]["what"],
            "detail": "broken seam detected: the gate fails" if detected else
                      "BROKEN SEAM NOT DETECTED: the gate would pass"}


def port_free(port: int) -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


# ------------------------------------------------------------------ the harness's side


def control_app(trip, edge, workdir: Path, world):
    """The loopback API the specs drive the harness through. Never the App's origin."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    import pilotbox
    state: dict = {}

    def user_of(email: str) -> dict:
        user = edge.users.get(email.strip().lower())
        if user is None:
            raise LookupError(f"no such user {email!r}")
        return user

    def facts(request):
        user = user_of(request.query_params["email"])
        uid = user["id"]
        org = trip.db("select org_id::text from public.org_members where user_id = %s", uid)
        org_id = org[0][0] if org else None
        wallet = trip.db("select wallet_id::text, ledger_total::text, reserved_total::text, "
                         "available::text from infrx.credit_wallets where owner_user_id = %s",
                         uid)
        grants = trip.one("select count(*), coalesce(sum(l.amount), 0)::text from "
                          "infrx.credit_ledger l join infrx.credit_wallets w on w.wallet_id = "
                          "l.wallet_id where w.owner_user_id = %s and l.kind = 'signup_grant'",
                          uid)
        jobs = trip.db(
            "select j.request_id::text, j.job_handle, j.state, j.settlement_state, "
            "j.execution_mode, h.amount::text, h.state, (select (-l.amount)::text from "
            "infrx.credit_ledger l where l.request_id = j.request_id and l.kind = "
            "'inference_debit'), (select count(*) from infrx.credit_ledger l where "
            "l.request_id = j.request_id and l.kind = 'inference_debit') from infrx.jobs j "
            "left join infrx.credit_wallet_holds h on h.request_id = j.request_id "
            "where j.org_id = %s order by j.created_at", org_id) if org_id else []
        keys = trip.db("select id::text, name, revoked_at is not null from public.api_keys "
                       "where org_id = %s order by created_at", org_id) if org_id else []
        conserved = None
        if wallet:
            from types import SimpleNamespace
            try:
                trip.conserved(SimpleNamespace(name=user["email"],
                                               wallet=SimpleNamespace(wallet_id=wallet[0][0])))
                conserved = {"ok": True}
            except AssertionError as failed:
                conserved = {"ok": False, "detail": str(failed)[:300]}
        return JSONResponse({
            "user_id": uid, "org_id": org_id, "confirmed": user["confirmed_at"] is not None,
            "entitlements": trip.one("select count(*) from infrx.signup_entitlements where "
                                     "user_id = %s", uid)[0],
            "grant_rows": grants[0], "grant_total": grants[1],
            "wallet": dict(zip(("wallet_id", "ledger", "reserved", "available"), wallet[0]))
            if wallet else None,
            "jobs": [dict(zip(("request_id", "handle", "state", "settlement", "mode", "hold",
                               "hold_state", "charged", "debits"), row)) for row in jobs],
            "keys": [dict(zip(("key_id", "name", "revoked"), row)) for row in keys],
            "conserved": conserved})

    def mail(request):
        return JSONResponse({"links": list(edge.mail.get(
            request.query_params["email"].strip().lower(), []))})

    async def issue_key(request):
        body = await request.json()
        user = user_of(body["email"])
        tag = uuid.uuid4().hex[:8]
        path = workdir / f"key-{tag}.key"
        status, issued = world.cli(trip, "issue-key", "--user", user["id"], "--name",
                                   body.get("name", f"e3a {tag}"), "--secret-file", str(path),
                                   "--idempotency-key", f"e3a-k-{tag}", "--reason",
                                   "e3a journey key (operator CLI)")
        if status != 0:
            return JSONResponse({"error": issued}, status_code=500)
        secret = path.read_text().strip()
        path.unlink()
        return JSONResponse({"key_id": issued["key_id"], "secret": secret})

    async def adjust(request):
        body = await request.json()
        user = user_of(body["email"])
        status, out = world.cli(trip, "adjust", "--user", user["id"], "--amount",
                                body["amount"], "--idempotency-key",
                                f"e3a-a-{uuid.uuid4().hex[:8]}", "--reason", "e3a low funds")
        return JSONResponse(out, status_code=200 if status == 0 else 500)

    async def engine(request):
        return JSONResponse(trip.engine.control(**(await request.json())))

    async def worker(request):
        action = (await request.json())["action"]
        if action == "stop":
            trip.box.stop("worker")
        else:
            trip.box.start("worker")
        return JSONResponse({"worker": action})

    async def clock(request):
        world.set_clock(trip.world.database, float((await request.json())["seconds"]))
        return JSONResponse({"ok": True})

    async def auth_ttl(request):
        edge.access_ttl_s = int((await request.json())["seconds"])
        return JSONResponse({"access_ttl_s": edge.access_ttl_s})

    def stats(request):
        return JSONResponse(dict(edge.stats))

    def clip(request):
        return Response(pilotbox.clip(), media_type="video/mp4")

    async def video(request):
        return JSONResponse(world.video((await request.json())["ref"]))

    async def state_route(request):
        if request.method == "POST":
            state.update(await request.json())
        return JSONResponse(state)

    def guarded(handler):
        async def run(request):
            import inspect
            try:
                result = handler(request)
                return await result if inspect.isawaitable(result) else result
            except Exception as exc:                       # noqa: BLE001 - a harness answer
                return JSONResponse({"error": f"{type(exc).__name__}: {str(exc)[:300]}"},
                                    status_code=500)
        return run

    routes = [("/facts", facts, ["GET"]), ("/mail", mail, ["GET"]),
              ("/issue-key", issue_key, ["POST"]), ("/adjust", adjust, ["POST"]),
              ("/engine", engine, ["POST"]), ("/worker", worker, ["POST"]),
              ("/clock", clock, ["POST"]), ("/auth-ttl", auth_ttl, ["POST"]),
              ("/stats", stats, ["GET"]), ("/clip", clip, ["GET"]),
              ("/video-messages", video, ["POST"]), ("/state", state_route, ["GET", "POST"])]
    return Starlette(routes=[Route(path, guarded(fn), methods=methods)
                             for path, fn, methods in routes])


# ------------------------------------------------------------------ the App and the browser


def run_logged(name: str, argv: list[str], out: Path, env: dict, cwd: Path,
               timeout: float) -> tuple[int, float]:
    began = time.monotonic()
    with (out / f"{name}.log").open("w") as sink:
        try:
            code = subprocess.run(argv, cwd=cwd, env=env, stdout=sink, stderr=subprocess.STDOUT,
                                  timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = 124
    return code, round(time.monotonic() - began, 1)


def base_env() -> dict:
    """What a child may inherit: the toolchain, never a credential or a hosted pointer."""
    return {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR", "USER")
            if k in os.environ} | {"NEXT_TELEMETRY_DISABLED": "1", "CI": "1"}


def app_env(anon: str, service_role: str, gateway_port: int) -> dict[str, str]:
    """The App's environment: only local values (`local_env`). `next start` runs with
    NODE_ENV=production, and I2A's instrumentation (lib/deploy/env.ts) refuses a production
    build that states neither VERCEL_ENV nor INFRX_APP_ENVIRONMENT; `development` is the one
    whose loopback origins it accepts."""
    return local_env({**base_env(), "INFRX_APP_ENVIRONMENT": "development",
                      "NEXT_PUBLIC_SUPABASE_URL": f"http://127.0.0.1:{EDGE_PORT}",
                      "NEXT_PUBLIC_SUPABASE_ANON_KEY": anon,
                      "SUPABASE_SERVICE_ROLE_KEY": service_role,
                      "INFRX_API_BASE_URL": f"http://127.0.0.1:{gateway_port}",
                      "CONSOLE_CURSOR_SECRET": secrets.token_hex(24)})


class NextApp:
    """`next start` in its own process group on 127.0.0.1:APP_PORT."""

    def __init__(self, env: dict, out: Path) -> None:
        self.env, self.out, self.process = env, out, None

    ARGV = ["pnpm", "exec", "next", "start", "-H", "127.0.0.1", "-p", str(APP_PORT)]
    URL = f"http://127.0.0.1:{APP_PORT}/login"
    READY_S = 90.0

    def __enter__(self) -> "NextApp":
        import httpx
        log = (self.out / "app.log").open("w")
        self.process = subprocess.Popen(
            self.ARGV, cwd=APP_DIR, env=self.env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
        log.close()
        try:
            end = time.monotonic() + self.READY_S
            while time.monotonic() < end:
                if self.process.poll() is not None:
                    raise RuntimeError(f"next start exited {self.process.returncode}")
                try:
                    if httpx.get(self.URL, timeout=5).status_code < 500:
                        return self
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            raise RuntimeError("next start never answered /login")
        except BaseException:
            self.__exit__()                    # `with` calls __exit__ only after __enter__ returned
            raise

    def __exit__(self, *exc) -> None:
        """SIGTERM, then SIGKILL, the whole group until none of it is left - also when its
        leader already exited (a child of it may still hold the port)."""
        if self.process is None:
            return
        for signum in (signal.SIGTERM, signal.SIGKILL):
            end = time.monotonic() + 15
            try:
                os.killpg(self.process.pid, signum)
                while time.monotonic() < end:
                    self.process.poll()        # reap the leader
                    os.killpg(self.process.pid, 0)
                    time.sleep(0.2)
            except ProcessLookupError:
                return


def leftovers(ports=(EDGE_PORT, CONTROL_PORT, APP_PORT)) -> list[str]:
    """What a run left behind: its containers, and any of its ports something still holds."""
    names = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"],
                           capture_output=True, text=True).stdout.split()
    return [name for name in names if name.startswith(f"infrx-{NAMESPACE}")] + \
        [f"127.0.0.1:{port}" for port in ports if not port_free(port)]


def head() -> dict:
    sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                         text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip() != ""
    return {"sha": sha, "dirty": dirty}


# ------------------------------------------------------------------ the run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--only", default="", help="comma-separated check ids")
    parser.add_argument("--break-seam", choices=sorted(SEAMS))
    args = parser.parse_args(argv)
    started, clock = datetime.now(timezone.utc), time.monotonic()
    out = (args.out or Path(os.environ.get("TMPDIR", "/tmp")) /
           f"infrx-e3a-{started:%Y%m%dT%H%M%SZ}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    selected = {c.strip() for c in args.only.split(",") if c.strip()} or \
        (set(SEAMS[args.break_seam]["checks"]) if args.break_seam else None)
    unknown = (selected or set()) - set(CHECKS)
    if unknown:
        parser.error(f"unknown checks {sorted(unknown)}")
    tasks = json.loads((REPO / "research/plan/tasks.json").read_text())
    lanes = merged_lanes(tasks)
    stages: list[dict] = []
    checks = classify(None, selected)
    source = head()

    def stage(name: str, verdict: str, began: float, **detail) -> dict:
        row = {"stage": name, "verdict": verdict,
               "seconds": round(time.monotonic() - began, 1), **detail}
        stages.append(row)
        print(f"{verdict:8} {name} {detail.get('detail', '')}", flush=True)
        return row

    lock = LOCK.open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held = True
    except BlockingIOError:
        held = False
    report = None
    try:
        began = time.monotonic()
        problems = []
        if not held:
            problems.append(f"another run holds {LOCK}")
        if not (APP_DIR / "node_modules").is_dir():
            problems.append("apps/app/node_modules missing: pnpm install --frozen-lockfile")
        browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        if not browsers or not Path(browsers).is_dir():
            problems.append("PLAYWRIGHT_BROWSERS_PATH unset or missing: install chromium "
                            "(`pnpm exec playwright install chromium-headless-shell`) under a "
                            "scratch directory and export it")
        stray = [p.name for p in APP_DIR.glob(".env*") if p.name != ".env.example"]
        if stray:
            problems.append(f"apps/app/{stray} would inject configuration into the App")
        busy = [port for port in (EDGE_PORT, CONTROL_PORT, APP_PORT) if not port_free(port)]
        if busy:
            problems.append(f"ports busy: {busy}")
        if shutil.which("docker") is None:
            problems.append("docker missing")
        if problems:
            stage("preflight", BLOCKED, began, detail="; ".join(problems))
            raise Stop
        stage("preflight", PASS, began, lanes_merged=sorted(lanes))

        # the backend world, E3C's, in this namespace (set before E2's harness loads)
        os.environ.update(INFRX_E2_NAMESPACE=NAMESPACE, INFRX_D_TASK=NAMESPACE)
        sys.path.insert(0, str(E3C))
        spec = importlib.util.spec_from_file_location("e3c_runner", E3C / "runner.py")
        e3c = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(e3c)
        import world                                   # e3c/world.py
        # ponytail: world pins NAMESPACE = "e3c" for need_stack(); the harness it loads already
        # follows INFRX_E2_NAMESPACE. Wiring request E3A-WR-1 makes world read it too.
        world.NAMESPACE = NAMESPACE
        import run
        import stack
        harness = world.harness
        assert harness.NAMESPACE == NAMESPACE and set(harness.PORTS.values()) <= \
            set(range(56800, 56900)), harness.PORTS
        env = app_env(stack.jwt("anon", ttl_s=12 * 3600),
                      stack.jwt("service_role", ttl_s=12 * 3600), stack.GATEWAY_PORT)

        began = time.monotonic()
        code, seconds = run_logged("app-build", ["pnpm", "exec", "next", "build"], out, env,
                                   APP_DIR, 900)
        stage("app-build", PASS if code == 0 else FAIL, began, exit=code,
              log=str(out / "app-build.log"))
        if code != 0:
            raise Stop

        rep = run.Report()
        with run.signals_handled():
            began = time.monotonic()
            usable, why = e3c.provision(rep, False, False)
            stage("stack", PASS if usable else BLOCKED, began, detail=why or "provisioned",
                  services=[{k: s.get(k) for k in ("stage", "status", "seconds")}
                            for s in rep.stages])
            try:
                if not usable:
                    raise Stop
                workdir = out / "cases" / "journey"
                workdir.mkdir(parents=True, exist_ok=True)
                began = time.monotonic()
                with world.composed(workdir, runtime_login=True,
                                    MAX_ACTIVE_JOBS_PER_KEY=str(PER_KEY_CAP)) as trip:
                    if args.break_seam == "grant-guard":
                        from test_journey import _signup_grant_not_unique
                        assert stack.current_database() == trip.world.database
                        _signup_grant_not_unique()
                    harness.run(["docker", "exec", "-i", harness.assert_ours(
                        harness.container_of("postgres")), "psql", "-U", harness.PG_ADMIN_ROLE,
                        "-d", trip.world.database, "-v", "ON_ERROR_STOP=1", "-c",
                        HOSTED_AUTH_UID], timeout=120.0)
                    import edge as edge_mod
                    edge = edge_mod.Edge(harness.pg_dsn(trip.world.database),
                                         stack.postgrest_url(stack.JOURNEY_POSTGREST_PORT),
                                         stack.JWT_SECRET, APP_ORIGIN,
                                         freeze=args.break_seam == "fixture-port")
                    stage("world", PASS, began, database=trip.world.database,
                          deviations=["auth.uid() replaced by the hosted form on the clone "
                                      "(E3A-WR-2)"],
                          gateway=trip.box.url, runtime_login=True,
                          detail="gateway + worker on the runtime login, controlled engine")
                    with edge_mod.Served(edge_mod.app(edge), EDGE_PORT, "e3a-edge"), \
                            edge_mod.Served(control_app(trip, edge, workdir, world),
                                            CONTROL_PORT, "e3a-control"):
                        began = time.monotonic()
                        with NextApp(env, out):
                            stage("app", PASS, began, url=APP_ORIGIN, bound=f"127.0.0.1:{APP_PORT}")
                            began = time.monotonic()
                            grep = ["--grep", r"(^|\s)(" + "|".join(sorted(selected)) + r"): "] \
                                if selected else []
                            code, _ = run_logged("browser", [
                                "pnpm", "exec", "playwright", "test", "--config",
                                "tests/e2e/playwright.config.ts", *grep], out, {
                                    **base_env(), "PLAYWRIGHT_BROWSERS_PATH": browsers,
                                    "E3A_APP_URL": f"{APP_ORIGIN}/",
                                    "E3A_CONTROL_URL": f"http://127.0.0.1:{CONTROL_PORT}/",
                                    "E3A_GATEWAY_URL": f"{trip.box.url}/",
                                    "E3A_MODEL": stack.CREDIT_ALIAS,
                                    "E3A_LANES": ",".join(sorted(lanes)), "E3A_OUT": str(out)},
                                APP_DIR, 3600)
                            try:
                                report = json.loads((out / "playwright.json").read_text())
                            except (OSError, ValueError):
                                report = None
                            stage("browser", PASS if report is not None else INVALID, began,
                                  exit=code, edge=dict(edge.stats),
                                  detail="" if report else "no Playwright JSON report")
            except Stop:
                pass
            except Exception as exc:                      # noqa: BLE001 - reported, not raised
                stage("compose", INVALID, began, detail=f"{type(exc).__name__}: {exc}"[:600])
            except BaseException as exc:                  # pytest.skip from the reused world
                if isinstance(exc, (KeyboardInterrupt, run.Interrupted)):
                    raise
                stage("compose", INVALID, began, detail=f"{type(exc).__name__}: {exc}"[:600])
            finally:
                began = time.monotonic()
                if usable:
                    e3c.teardown(rep)
                left = leftovers()
                stage("teardown", PASS if not left else FAIL, began,
                      detail=f"no infrx-{NAMESPACE} container; ports {EDGE_PORT}, "
                             f"{CONTROL_PORT}, {APP_PORT} free" if not left else f"left {left}")
    except Stop:
        pass
    except BaseException as exc:                          # a signal: unfinished checks NOT RUN
        stage("interrupted", NOT_RUN, clock, detail=f"{type(exc).__name__}")
    finally:
        lock.close()
    checks = classify(report, selected)
    if args.break_seam and report is not None:
        stages.append(seam_stage(args.break_seam, checks))
        print(f"{stages[-1]['verdict']:8} {stages[-1]['stage']} {stages[-1]['detail']}")
    table = cells(checks)
    verdict = worst([*(row["verdict"] for row in stages), *(c["verdict"] for c in table)])
    payload = {
        "schema": "infrx.e2c.verdict/1", "gate": "APP-LOCAL", "task": "E3A",
        "verdict": verdict, "exit": EXIT[verdict], "head": source, "head_end": head(),
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - clock, 1), "argv": argv or sys.argv[1:],
        "label": "the App in a real browser on real auth-stand-in/PostgREST/PostgreSQL/Valkey/"
                 "S3-compatible/gateway/worker processes with a controlled engine; not Marlin "
                 "quality, not GPU capacity, not hosted behaviour, not a real email",
        "namespace": NAMESPACE, "ports": {"edge": EDGE_PORT, "control": CONTROL_PORT,
                                          "app": APP_PORT, "block": "56800-56899"},
        "lanes_merged": sorted(lanes), "break_seam": args.break_seam,
        "selected": sorted(selected) if selected else None,
        "stages": stages, "cells": table,
        "checks": [{"id": name, **entry, "test_ids": list(CHECKS[name][0]),
                    "needs": list(CHECKS[name][1])} for name, entry in checks.items()],
        "evidence": {"browser_report": str(out / "playwright.json"),
                     "browser_log": str(out / "browser.log"), "app_log": str(out / "app.log"),
                     "box_logs": str(out / "cases" / "journey")},
        "reproduce": "PLAYWRIGHT_BROWSERS_PATH=<dir> apps/infrx-api/.venv/bin/python "
                     "tests/integration/app/runner.py --out <dir>",
    }
    (out / "verdict.json").write_text(json.dumps(payload, indent=2, default=str) + "\n")
    for row in table:
        print(f"{row['verdict']:>8}  {row['id']}  (journey {row['journey']})")
    print(f"APP-LOCAL: {verdict} (exit {EXIT[verdict]}) - {out / 'verdict.json'}")
    return EXIT[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
