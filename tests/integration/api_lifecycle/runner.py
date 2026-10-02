#!/usr/bin/env python3
"""AP-11: the resumable API-only lifecycle runner (research/plan/api-lifecycle/verification.md).

    apps/infrx-api/.venv/bin/python tests/integration/api_lifecycle/runner.py \\
        --mode inspect|isolated|live|cleanup --state <private dir>/state.json \\
        [--config config.json --secrets secrets.json | --world ap11] [--only 09,10] [--out DIR]

* **inspect** runs only read-only stages (01); a mutation is refused before it leaves.
* **isolated** runs every stage against the configured task-local origins; `--world ap11`
  composes them on ap11's own services (world.py: PostgreSQL 57567, Valkey 57568, MinIO
  57569, the gateway and worker processes, tests/integration/fake_vllm.py). Declared fixtures
  (config `fixtures`) stand in for APIs not on the base and are named in the verdict.
* **live** is refused (INVALID) unless the config names its origins, identities, target, an
  exact budget and `max_requests` <= 6, and declares no fixture.
* **cleanup** removes only the resources the state file says this run created.

Every product step is an HTTP request to FastAPI - never SQL, the operator CLI or a Supabase
RPC. A mutation's Idempotency-Key and request hash are written to the 0600 state file BEFORE
it is sent; after a restart a recorded outcome is reconciled with a GET and a pending one is
retried with its original key (never a new one). A stage that passed is not repeated.

Verdict `<out>/verdict.json` (redacted): per stage PASS / FAIL / BLOCKED / NOT RUN with
reasons and evidence (UTC start/end, route templates, statuses, correlation and resource
ids, assertions, versions, counters). The gate is the worst selected stage (FAIL > INVALID >
BLOCKED > NOT RUN > PASS); exit 0 / 1 / 3 / 3 / 4 as tests/integration/ENVIRONMENT.md.
An interrupted stage (unknown outcome) is NOT RUN with a resume instruction. A stage whose
API is not on the base is BLOCKED naming the AP package and the route. Only the lifecycle
verdict is this runner's; API-boundary, quality and operations stay NOT RUN here.
`research/plan/api-lifecycle/probe.py` is the separate gap audit: never called from here.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, UTC
from pathlib import Path

if __package__ in (None, ""):                       # run as a script: import as the package
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "api_lifecycle"                   # noqa: A001

import httpx  # noqa: E402

from api_lifecycle import stages as contracts  # noqa: E402
from api_lifecycle.stages import Blocked, Failed  # noqa: E402
from api_lifecycle.state import InvalidRun, Secrets, State, digest, utc_now  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
TASK, GATE, BASE = "AP-11", "API-LIFECYCLE", "cd9f517c"
RUNNER = "tests/integration/api_lifecycle/runner.py"
PASS, FAIL, BLOCKED, INVALID, NOT_RUN = "PASS", "FAIL", "BLOCKED", "INVALID", "NOT RUN"
RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 3, FAIL: 4}
EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}
MODES = ("inspect", "isolated", "live", "cleanup")
LIVE_MAX_REQUESTS = 6                 # verification.md: six accepted requests, not a load test
ISOLATED_MAX_REQUESTS = 20


class Interrupted(Exception):
    """A request whose outcome is unknown (timeout, reset, signal): resume, never re-create."""


def worst(statuses) -> str:
    return max(statuses, key=RANK.__getitem__, default=NOT_RUN)


def status_of(checks: list[bool], blocked: list[str], missing: list[str]) -> str:
    """A stage's status: any failed assertion is FAIL; else a blocked prerequisite or an
    absent route is BLOCKED; a stage that asserted nothing is never a pass."""
    if not all(checks):
        return FAIL
    if blocked or missing:
        return BLOCKED
    return PASS if checks else NOT_RUN


def load_config(path: Path | None, mode: str) -> dict:
    if path is None:
        raise InvalidRun("--config is required (or --world ap11 in isolated mode)")
    config = json.loads(path.read_text())
    if mode == "live":
        from infrx.contracts import api
        problems = []
        origins = config.get("origins") or {}
        if not origins or not all(str(v).startswith(("https://", "http://"))
                                  for v in origins.values()):
            problems.append("origins")
        if not config.get("identities"):
            problems.append("identities")
        try:
            api.Money.model_validate(config.get("budget") or {})
        except ValueError:
            problems.append("budget (an exact {amount, unit} in CREDIT, USD or PROVIDER_USD)")
        most = config.get("max_requests")
        if type(most) is not int or not 1 <= most <= LIVE_MAX_REQUESTS:
            problems.append(f"max_requests (1..{LIVE_MAX_REQUESTS})")
        if config.get("fixtures"):
            problems.append("no fixtures (a live run seeds nothing behind the runner's back)")
        if problems:
            raise InvalidRun("live mode refused: the config must name " + ", ".join(problems))
    if not config.get("target"):
        raise InvalidRun("the config names no target")
    return config


class Session:
    """The HTTP side of one run: one client per origin, the shared state and budget."""

    def __init__(self, mode: str, config: dict, secrets: Secrets, state: State,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.mode, self.config, self.secrets, self.state = mode, config, secrets, state
        self.transport, self.clients = transport, {}
        self.max_requests = int(config.get("max_requests") or ISOLATED_MAX_REQUESTS)

    def client(self, origin: str) -> httpx.Client:
        if origin not in self.clients:
            base = (self.config.get("origins") or {}).get(origin)
            if not base:
                raise Blocked(f"BLOCKED[config] no {origin!r} origin is configured")
            self.clients[origin] = httpx.Client(
                base_url=base, transport=self.transport, follow_redirects=False,
                timeout=float(self.config.get("timeout_s", 120)))
        return self.clients[origin]

    def close(self) -> None:
        for client in self.clients.values():
            client.close()


def new_evidence() -> dict:
    return {"started": utc_now(), "ended": None, "exchanges": [], "assertions": [],
            "versions": {}, "counters": {"requests": 0, "inference": 0}, "owned": [],
            "fixtures": [], "resumed": []}


class Context:
    """What a stage implementation may do: HTTP through `call`/`mutate`, assertions, and
    reads of earlier stages' published outputs. Identity only from the secrets file."""

    def __init__(self, session: Session, sid: str, evidence: dict) -> None:
        self.session, self.sid, self.ev, self.published = session, sid, evidence, {}

    @property
    def config(self) -> dict:
        return self.session.config

    @property
    def mode(self) -> str:
        return self.session.mode

    # --- identities ----------------------------------------------------------------
    def identity(self, name: str) -> dict:
        found = (self.config.get("identities") or {}).get(name)
        if not found:
            raise Blocked(f"BLOCKED[config] identity {name!r} is not configured")
        return found

    def credential(self, name: str) -> str:
        """A consumer key is either minted through the key API (AP-03, recorded in the
        state) or, in isolated mode only, a fixture the config declares by name."""
        found = self.identity(name)
        secret = found["secret"]
        fixture = secret in (self.config.get("fixtures") or {})
        if found.get("audience") == "consumer" \
                and name not in self.session.state.data.get("minted", {}) and not fixture:
            raise Blocked(f"BLOCKED[AP-03] {name}'s consumer key must be created through POST "
                          "/console/v1/keys (stage 08); a seeded key is only an isolated "
                          "mode's declared fixture")
        value = self.session.secrets.get(secret)
        if not value:
            raise Blocked(f"BLOCKED[secrets] {secret!r} is absent from the secrets file")
        if fixture and secret not in self.ev["fixtures"]:
            self.ev["fixtures"].append(secret)
        return value

    # --- HTTP -------------------------------------------------------------------------
    def _spend(self) -> None:
        counters = self.session.state.counters
        if counters["inference"] >= self.session.max_requests:
            raise Blocked(f"BLOCKED[budget] the request budget ({self.session.max_requests} "
                          f"inference requests) is spent: {counters['inference']} recorded")
        counters["inference"] += 1
        self.ev["counters"]["inference"] += 1
        self.session.state.save()

    def call(self, method: str, route: str, *, origin: str = "gateway", actor: str | None = None,
             params: dict | None = None, query: dict | None = None, json: object = None,
             content: bytes | None = None, headers: dict | None = None,
             inference: bool = False) -> httpx.Response:
        if self.mode == "inspect" and method not in ("GET", "HEAD"):
            raise InvalidRun(f"inspect mode refused {method} {route} (reads only)")
        if inference:
            self._spend()
        sent = {"X-Request-Id": f"ap11-{uuid.uuid4()}", **(headers or {})}
        if actor is not None:
            sent["Authorization"] = f"Bearer {self.credential(actor)}"
        self.session.state.counters["requests"] += 1
        self.ev["counters"]["requests"] += 1
        began = time.monotonic()
        try:
            response = self.session.client(origin).request(
                method, route.format(**(params or {})), params=query, json=json,
                content=content, headers=sent)
        except httpx.TransportError as lost:
            raise Interrupted(f"{method} {route}: {type(lost).__name__}") from None
        finally:
            self.session.state.save()
        self.ev["exchanges"].append({
            "method": method, "route": route, "origin": origin, "status": response.status_code,
            "request_id": response.headers.get("X-Request-Id") or sent["X-Request-Id"],
            "inference_id": response.headers.get("X-Inference-Id"),
            "location": response.headers.get("Location"),
            "ms": round((time.monotonic() - began) * 1000, 1), "at": utc_now()})
        return response

    def mutate(self, name: str, method: str, route: str, *, extract, reconcile=None,
               inference: bool = False, origin: str = "gateway", actor: str | None = None,
               params: dict | None = None, json: object = None, content: bytes | None = None,
               headers: dict | None = None) -> dict:
        """One state-recorded mutation. Done: its recorded outputs (after `reconcile`, a GET,
        confirms them). Pending (outcome unknown): retried with the ORIGINAL key. New: the
        key and hash are saved, then the request is sent; the safe `extract` is recorded."""
        state = self.session.state
        request_hash = digest({"origin": origin, "method": method, "route": route,
                               "params": params, "json": json,
                               "content": hashlib.sha256(content).hexdigest() if content else None})
        entry = state.mutation(name)
        if entry is not None and entry["request_hash"] != request_hash:
            raise InvalidRun(f"{name} is recorded with another request: a different body must "
                             "never reuse its Idempotency-Key (start a new state file)")
        if entry is not None and entry["status"] == "done":
            outputs = entry["outputs"]
            if reconcile is not None:
                self.require(f"the recorded {name} is found again on reconcile",
                             reconcile(outputs), None)
            self.ev["resumed"].append(name)
            return outputs
        if entry is None:
            if inference:
                self._spend()
            entry = state.begin(name, method, route, request_hash)
        response = self.call(method, route, origin=origin, actor=actor, params=params, json=json,
                             content=content, headers={"Idempotency-Key": entry["key"],
                                                       **(headers or {})})
        outputs = extract(response)
        state.finish(name, outputs)
        return outputs

    def key(self, name: str) -> str:
        return self.session.state.mutation(name)["key"]

    # --- assertions and outputs ------------------------------------------------------
    def check(self, name: str, ok, detail=None) -> bool:
        self.ev["assertions"].append({"name": name, "ok": bool(ok),
                                      "detail": None if detail is None else str(detail)[:300]})
        return bool(ok)

    def require(self, name: str, ok, detail=None) -> None:
        if not self.check(name, ok, detail):
            raise Failed(name)

    def version(self, name: str, value) -> None:
        self.ev["versions"][name] = value

    def publish(self, **outputs) -> None:
        self.published.update(outputs)

    def outputs(self, sid: str) -> dict:
        recorded = self.session.state.data["stages"].get(sid)
        if recorded is None or recorded["status"] != PASS:
            raise Blocked(f"BLOCKED[stage] needs stage {sid} to pass first "
                          f"(it is {recorded['status'] if recorded else 'not run'})")
        return recorded["outputs"]

    def own(self, kind: str, resource_id: str, cleanup: dict | None) -> None:
        self.session.state.own(kind, resource_id, cleanup)
        self.ev["owned"].append({"kind": kind, "id": resource_id})


def run_stages(session: Session, selected: set[str], results: list[dict]) -> list[dict]:
    """Appends each stage's entry to `results` as it goes, so an INVALID run still reports
    what ran before it."""
    stop = None
    for stage in contracts.STAGES:
        entry = {"id": stage.sid, "title": stage.title, "proves": stage.proves,
                 "selected": stage.sid in selected, "status": NOT_RUN, "reasons": [],
                 "routes": [f"{r}" + (f" [{r.owner}]" if r.owner else "") for r in stage.routes],
                 "needs": list(stage.needs), "prerequisites": list(stage.prerequisites),
                 "resumed": False, "evidence": None}
        results.append(entry)
        if not entry["selected"]:
            entry["reasons"].append(f"not selected in {session.mode} mode / --only")
            continue
        if stop:
            entry["reasons"].append(stop)
            continue
        recorded = session.state.data["stages"].get(stage.sid)
        if recorded is not None and recorded["status"] == PASS:
            entry.update(status=PASS, resumed=True, evidence=recorded.get("evidence"),
                         reasons=[f"passed at {recorded['at']}; resumed from the state file"])
            continue
        missing = [f"BLOCKED[{owner}] {', '.join(routes)}: not on the base"
                   for owner, routes in stage.missing().items()]
        if stage.run is None:
            entry.update(status=BLOCKED, reasons=missing + [
                f"BLOCKED[{why.split()[0]}] {why.split(' ', 1)[1]}" for why in stage.prerequisites])
            continue
        evidence, blocked = new_evidence(), []
        ctx = Context(session, stage.sid, evidence)
        try:
            stage.run(ctx)
        except Blocked as why:
            blocked.append(str(why))
        except Failed:
            pass
        except (Interrupted, KeyboardInterrupt) as lost:
            evidence["ended"] = utc_now()
            entry.update(evidence=evidence, reasons=[
                f"interrupted: {lost or 'signal'} - the outcome is unknown; resume: rerun "
                "with the same --state (recorded mutations are reconciled, never re-created)"])
            stop = f"not run: stage {stage.sid} was interrupted"
            continue
        except InvalidRun:
            raise
        except Exception as bug:                   # noqa: BLE001 - a FAIL, recorded
            ctx.check(f"the stage completes (raised {type(bug).__name__})", False, bug)
        evidence["ended"] = utc_now()
        status = status_of([a["ok"] for a in evidence["assertions"]], blocked, missing)
        entry.update(status=status, evidence=evidence, reasons=blocked + missing)
        session.state.data["stages"][stage.sid] = {     # redacted here too: no echo leaks
            "status": status, "outputs": ctx.published, "at": utc_now(),
            "evidence": session.secrets.redact(evidence)}
        session.state.save()
    return results


def cleanup(session: Session) -> list[dict]:
    """Only what the state file says this run created, newest first."""
    rows, ctx = [], Context(session, "cleanup", new_evidence())
    for owned in reversed(session.state.data["owned"]):
        how = owned.get("cleanup")
        if not how:
            outcome = "nothing to clean (expires by retention)"
        else:
            answer = ctx.call(how["method"], how["route"], origin=how.get("origin", "gateway"),
                              actor=how.get("actor"), params={"id": owned["id"]})
            outcome = "cleaned" if answer.status_code < 300 else \
                "gone" if answer.status_code in (404, 410) else f"failed {answer.status_code}"
        owned["cleaned"] = outcome
        rows.append({"kind": owned["kind"], "id": owned["id"], "outcome": outcome})
    session.state.save()
    return rows


def pins(config: dict) -> dict:
    def git(*args: str) -> str:
        done = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
        return done.stdout.strip() if done.returncode == 0 else ""
    return {"base": BASE, "head": git("rev-parse", "HEAD") or "unknown",
            "dirty": bool(git("status", "--porcelain")), "target": config.get("pins", {})}


@contextlib.contextmanager
def sources(args):
    """(config path, secrets path): the given files, or the ap11 world's own."""
    if args.world is None:
        yield args.config, args.secrets
        return
    if args.mode != "isolated" or args.world != "ap11":
        raise InvalidRun("--world ap11 composes the isolated mode's task-local stack only")
    from api_lifecycle import world
    with world.isolated(args.out) as provided:
        yield provided


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=MODES, default="isolated")
    parser.add_argument("--state", type=Path, required=True, help="0600 state file (resumable)")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--secrets", type=Path, help="0600 JSON object of credentials")
    parser.add_argument("--world", choices=("ap11",), help="compose ap11's isolated stack")
    parser.add_argument("--only", default="", help="comma-separated stage ids")
    parser.add_argument("--out", type=Path, default=None, help="verdict directory")
    args = parser.parse_args(argv)
    started, clock = datetime.now(UTC), time.monotonic()
    args.out = args.out or Path(os.environ.get("TMPDIR", "/tmp")) / \
        f"infrx-ap11-{started:%Y%m%dT%H%M%SZ}"
    args.out.mkdir(parents=True, exist_ok=True)
    only = {sid.strip() for sid in args.only.split(",") if sid.strip()}
    secrets, config, results, rows, reasons = Secrets({}), {}, [], None, []
    session = None
    try:
        with sources(args) as (config_path, secrets_path):
            config = load_config(config_path, args.mode)
            secrets = Secrets.load(secrets_path)
            state = State.open(args.state, target=config.get("target"))
            session = Session(args.mode, config, secrets, state, transport)
            if args.mode == "cleanup":
                rows = cleanup(session)
                verdict = worst([FAIL if row["outcome"].startswith("failed") else PASS
                                 for row in rows] or [PASS])
            else:
                selected = {s.sid for s in contracts.STAGES
                            if (s.reads_only or args.mode != "inspect")
                            and (not only or s.sid in only)}
                run_stages(session, selected, results)
                verdict = worst(r["status"] for r in results if r["selected"])
    except InvalidRun as invalid:
        verdict, reasons = INVALID, [f"INVALID {invalid}"]
    except Blocked as missing:                     # the isolated world could not be composed
        verdict, reasons = BLOCKED, [str(missing)]
    finally:
        if session is not None:
            session.close()
    complete = args.mode == "live" and len(results) == len(contracts.STAGES) \
        and all(r["selected"] and r["status"] == PASS for r in results)
    payload = {
        "task": TASK, "gate": GATE, "mode": args.mode, "verdict": verdict, "exit": EXIT[verdict],
        "reasons": reasons, "complete_lifecycle": complete,
        "verdicts": {"lifecycle": verdict, "api_boundary": NOT_RUN, "quality": NOT_RUN,
                     "operations": NOT_RUN},
        "label": ("isolated: task-local services, a controlled engine and declared fixtures - "
                  "never the real GPU, real judge or hosted gate" if args.mode != "live"
                  else "live: the configured approved target"),
        "fixtures": config.get("fixtures") or {}, "pins": pins(config),
        "target": config.get("target"), "state_file": str(args.state),
        "counters": session.state.counters if session is not None else {},
        "started": started.isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - clock, 1),
        "stages": results, **({"cleanup": rows} if rows is not None else {}),
        "reproduce": f"apps/infrx-api/.venv/bin/python {RUNNER} --mode {args.mode} --state "
                     "<state> " + ("--world ap11" if args.world else "--config <c> --secrets <s>"),
    }
    payload = secrets.redact(payload)
    (args.out / "verdict.json").write_text(json.dumps(payload, indent=2, default=str))
    for entry in payload["stages"]:
        print(f"{entry['status']:>8}  {entry['id']}  {entry['title']}")
    for reason in payload["reasons"]:
        print(reason)
    print(f"gate {verdict} -> {args.out / 'verdict.json'}")
    return EXIT[verdict]


if __name__ == "__main__":
    import signal

    def _stop(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, _stop)
    raise SystemExit(main())
