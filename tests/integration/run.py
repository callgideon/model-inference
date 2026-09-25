#!/usr/bin/env python3
"""THE documented command: provision fresh isolated services and run smoke + contract
tests, with no production credential anywhere.

    apps/infrx-api/.venv/bin/python tests/integration/run.py

Stages, in order, each reported with its own status:

  preflight  docker reachable, pinned images present, port range free, namespace clean
  services   `docker compose -p infrx-e2` up on digest-pinned images, then readiness
  migrate    apply apps/app/supabase/migrations to the fresh database, install the
             test-only clock, generate seeded fixtures
  rls        the DUR-RLS role matrix (anon / member / owner / operator / service_role)
  engine     the exported engine conformance suite against the fake vLLM over real HTTP
  suites     the canonical `make` targets plus this suite, so cross-module discovery is
             measured rather than assumed
  backend    (--layer 3 only, E3B) PostgREST beside the stack, then the backend gate
             suite: journeys, crash drills, schema backstops. Pending cases are counted
             by the id that unblocks them and make the run exit 3, never 0
  teardown   remove exactly what this project created, and prove nothing is left

Exit codes:  0 everything ran and passed
             1 something failed
             3 a service-backed stage could not run (reported PENDING, never PASS)

`--layer 1` runs only what needs no container. `--layer 3` is `all` plus the E3B backend
stage. `--canary` additionally proves an INTENTIONAL failure is detected rather than skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness                                    # noqa: E402
import pgstate                                    # noqa: E402

PASS, FAIL, PENDING, SKIP = "PASS", "FAIL", "PENDING", "SKIP"

# Fake servers this process started, so a signal handler can take them down. r1 review, same
# pass: a SIGTERM used to kill run.py with exit 143, no teardown and an orphaned fake vLLM
# holding a task-local port.
LIVE_SERVERS: list = []


class Interrupted(BaseException):
    """A signal asked us to stop. A BaseException so no `except Exception` swallows it, and
    an exception rather than an exit so `main`'s `finally` still tears the stack down."""

    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(f"interrupted by signal {signum}")


def _on_signal(signum, _frame):
    for server in list(LIVE_SERVERS):
        try:
            server.stop()
        except Exception:                          # noqa: BLE001 - we are already dying
            pass
        finally:
            if server in LIVE_SERVERS:
                LIVE_SERVERS.remove(server)
    raise Interrupted(signum)


@contextmanager
def signals_handled():
    """SIGTERM as well as SIGINT: the reviewer killed run.py with SIGTERM and got exit 143,
    no teardown and an orphaned server. Both now raise into `main`, whose `finally` tears the
    stack down; the previous handlers are restored on the way out."""
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[signum] = signal.signal(signum, _on_signal)
        except ValueError:                         # not the main thread: nothing to install
            pass
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


class Report:
    def __init__(self) -> None:
        self.stages: list[dict] = []
        self.started = datetime.now(timezone.utc)
        self._last = time.monotonic()
        # Confirmation G-B2: the tree as the run STARTS, and `as_json` records the end too. This
        # catches a tree dirty at the start and cleaned before the end (or the reverse); an edit
        # made AND undone between the two samples is not seen (verification RUN-N1).
        self.head = git_head()

    def add(self, stage: str, status: str, detail: object = None, **extra) -> dict:
        # Review H7: how long each stage took (since the previous stage ended).
        now = time.monotonic()
        seconds, self._last = round(now - self._last, 1), now
        entry = {"stage": stage, "status": status, "detail": detail,
                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "seconds": seconds, **extra}
        self.stages.append(entry)
        mark = {PASS: "ok  ", FAIL: "FAIL", PENDING: "PEND", SKIP: "skip"}[status]
        print(f"[{mark}] {stage}: {_short(detail)}", flush=True)
        return entry

    @property
    def exit_code(self) -> int:
        if any(entry["status"] == FAIL for entry in self.stages):
            return 1
        if any(entry["status"] == PENDING for entry in self.stages):
            return 3
        return 0

    def as_json(self) -> str:
        # Review H7: which tree and which namespace a report is evidence for - at the start
        # and at the end of the run (G-B2); the report is evidence for one clean tree only
        # when both say so.
        return json.dumps({"git_head": self.head, "git_head_end": git_head(),
                           "namespace": harness.NAMESPACE,
                           "started": self.started.isoformat(timespec="seconds"),
                           "seconds": round((datetime.now(timezone.utc)
                                             - self.started).total_seconds(), 1),
                           "exit_code": self.exit_code, "stages": self.stages},
                          indent=2, default=str)


def git_head() -> dict:
    """The checkout's commit and whether its tree differs from it (untracked files count).
    Either is None when git cannot answer (not a tree, or no git at all - the runtime image
    has none): an unknown tree is never recorded as a clean one, and never a crash that
    loses the report (E4B review F1/F2)."""
    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", "-C", str(harness.REPO_ROOT), *args],
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None
    status = git("status", "--porcelain")
    return {"sha": git("rev-parse", "HEAD") or None,
            "dirty": None if status is None else bool(status)}


def _short(detail: object, limit: int = 220) -> str:
    text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


# --------------------------------------------------------------------- suite runners

COUNT_PATTERNS = (
    re.compile(r"(\d+) passed"),                  # pytest
    re.compile(r"^# pass (\d+)$", re.MULTILINE),  # node --test
)


def counts(output: str) -> dict[str, int]:
    """Measured counts, parsed from the runner's own output. Nothing is hand-typed: a
    number in evidence must come from a command (the track-I lesson)."""
    found: dict[str, int] = {}
    for name, pattern in (("passed", COUNT_PATTERNS[0]), ("node_pass", COUNT_PATTERNS[1])):
        match = pattern.search(output)
        if match:
            found[name] = int(match.group(1))
    for name, pattern in (("failed", r"(\d+) failed"), ("skipped", r"(\d+) skipped"),
                          ("errors", r"(\d+) error"), ("node_fail", r"^# fail (\d+)$")):
        match = re.search(pattern, output, re.MULTILINE)
        if match:
            found[name] = int(match.group(1))
    return found


def shell(argv: list[str], *, cwd: Path, env: dict | None = None, timeout: float = 1800.0,
          needle: str | None = None):
    """`needle` is searched in the WHOLE output, not the tail: node --test prints a failure's
    detail long before its summary block, so a tail-only search reported "detected but not
    named" for a failure that was named perfectly well."""
    started = time.monotonic()
    # Its own process group (review H5): a run past its budget takes make, uv and pytest -
    # and whatever lock or container they hold - down with it, not only the direct child.
    process = subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True,
                               # pytest cuts its summary lines at the terminal width - 80
                               # columns without a tty - and drops the failure's message first.
                               env={**os.environ, "COLUMNS": "400", **(env or {})})
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        code = process.returncode
    except subprocess.TimeoutExpired:
        # E3B phase 2, measured: `make api-test` outlived 1800 s on a loaded host and the
        # traceback lost the whole report. A run past its budget is a FAILED run (exit 124,
        # the `timeout` convention) with what it printed, never a crash of the gate.
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        code, stderr = 124, stderr + f"\ntimed out after {timeout:.0f} s"
    except BaseException:
        # SIGINT/SIGTERM to run.py (`Interrupted`) no longer reaches a separate group.
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    output = stdout + stderr
    return {"argv": " ".join(argv), "cwd": str(cwd.relative_to(harness.REPO_ROOT) or "."),
            "exit": code, "seconds": round(time.monotonic() - started, 1),
            "counts": counts(output),
            # `-rs` reasons, when the run was asked for them (review F6-findings).
            "skips": sorted(set(re.findall(r"^SKIPPED \[\d+\] \S+?:\d+: (.*)$", output, re.M))),
            # Every red case by id, not only the 12-line tail (the round-2 gate's suites stage
            # reported '4 failed' while its tail named three).
            "failures": re.findall(r"^(?:FAILED|ERROR) (\S+)", output, re.M),
            # ... and each one's first line, so a flaky red can be attributed (G-N1).
            "failure_lines": re.findall(r"^((?:FAILED|ERROR) \S+.*)$", output, re.M)[:50],
            "named": None if needle is None else (needle.lower() in output.lower()),
            "tail": "\n".join(output.strip().splitlines()[-12:])}


# --------------------------------------------------------------------- stages

def preflight(report: Report, *, want_services: bool) -> bool:
    """Every failure here is a REPORTED stage, never a traceback (r1 review, same pass:
    a label-only foreign container used to wedge the run with an uncaught HarnessError)."""
    if not want_services:
        report.add("preflight", SKIP, "layer 1 only: no container needed")
        return False
    try:
        usable, why = harness.docker_available()
        if not usable:
            report.add("preflight", PENDING, f"docker unusable: {why}")
            return False
        # r1 B1: containers, volumes AND networks, classified by project label plus
        # `project.working_dir`, so another checkout's live stack and a hand-made same-named
        # volume are both reported instead of destroyed.
        strangers = harness.foreign_resources()
        if strangers:
            report.add("preflight", FAIL,
                       {"refusing to touch resources this checkout did not create": strangers,
                        "ours": harness.working_dir()})
            return False
        orphans = fake_server_orphans()
        if orphans:
            report.add("preflight", FAIL,
                       f"a fake vLLM this harness owns is still running from an earlier run: "
                       f"{orphans} - kill it (the run.py that started it was killed before it "
                       f"could)")
            return False
        missing = harness.images_present()
        # Whatever a previous run of THIS checkout left is ours and goes first: "provisions
        # FRESH services" is the acceptance criterion, and a leftover stack would otherwise
        # look like a busy port below.
        if harness.owned_containers():
            # E3B: a PostgREST of ours left by an interrupted --layer 3 run sits on E2's
            # network and would make this removal fail; it goes first (a foreign one is
            # refused).
            _backend_stack().postgrest_down()
            harness.down()
        busy = harness.busy_ports()
        if busy:
            report.add("preflight", FAIL,
                       f"these task-local ports are already in use: {busy} - stop whatever holds "
                       f"them (a previous `--keep` run, or "
                       f"`docker compose -p {harness.PROJECT} down -v`)")
            return False
    except harness.HarnessError as exc:
        report.add("preflight", FAIL, str(exc))
        return False
    report.add("preflight", PASS, {"docker": why, "images": harness.compose_images(),
                                   "not_yet_pulled": missing, "ports": harness.PORTS,
                                   "working_dir": harness.working_dir()})
    return True


def _orphans_from_ps(listing: str, marker: str) -> list[int]:
    """Parse `ps -axww -o pid=,command=`.

    Its own function because on Linux the procfs branch below always wins, so this one was
    dead code no test could reach and no mutant could kill - which is how the original
    truncation bug (`ps` cutting each line to the terminal width, hiding a marker that is an
    absolute path) survived review in the first place. `-ww` is what stops that, and
    `test_run.py` drives this parser directly as well as through the real `ps`.
    """
    pids = []
    for line in listing.splitlines():
        columns = line.strip().split(None, 1)
        if len(columns) == 2 and columns[0].isdigit():
            pid = int(columns[0])
            if pid != os.getpid() and marker in columns[1]:
                pids.append(pid)
    return sorted(pids)


def fake_server_orphans(proc: Path = Path("/proc")) -> list[int]:
    """PIDs of a fake vLLM this harness started and never stopped.

    Only processes whose command line names *this* checkout's `fake_vllm.py` count: another
    checkout's server is not ours to report as our leak, and nothing here kills anything.

    `proc` is injectable for one reason: pointing it at a directory that does not exist takes
    the `ps` branch on a host that has procfs, so both paths are exercised where the tests run.
    """
    marker = str((harness.HERE / "fake_vllm.py").resolve())
    # Linux exposes untruncated argv through procfs. macOS has no procfs;
    # double-wide ps explicitly disables the terminal-width truncation that the
    # original reviewer caught. This function reports PIDs; it never kills them.
    if not proc.is_dir():
        result = subprocess.run(["ps", "-axww", "-o", "pid=,command="],
                                capture_output=True, text=True, check=True)
        return _orphans_from_ps(result.stdout, marker)
    pids = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode(errors="replace")
        except OSError:
            continue                    # it exited, or it is not ours to read
        if marker in cmdline:
            pids.append(int(entry.name))
    return sorted(pids)


def services(report: Report, *, pull: bool) -> bool:
    try:
        harness.up(pull=pull)
        versions = harness.wait_all()
        database = harness.provision_database()
    except harness.HarnessError as exc:
        report.add("services", PENDING, f"could not provision: {exc}")
        return False
    report.add("services", PASS, {"containers": harness.owned_containers(),
                                  "versions": versions, "database": database})
    return True


def migrate(report: Report, seed: int):
    import psycopg
    try:
        with psycopg.connect(harness.pg_dsn(), autocommit=True) as conn:
            applied = pgstate.apply_migrations(conn)
            clock = pgstate.install_test_clock(conn)
            offset_probe = _clock_probe(conn)
            fixtures = pgstate.seed_fixtures(conn, seed)
            before = pgstate.balances(conn)
            # 0002 documents itself as idempotent; re-applying it is how that is checked,
            # and the balance snapshot is DUR-RLS's "migrations preserve existing balances".
            conn.execute((harness.MIGRATIONS_DIR / "0002_seed_models.sql").read_text())
            after = pgstate.balances(conn)
    except Exception as exc:                       # noqa: BLE001 - reported, not hidden
        report.add("migrate", FAIL, f"{type(exc).__name__}: {exc}")
        return None
    if before != after:
        report.add("migrate", FAIL, {"balances_before": before, "balances_after": after})
        return None
    state = harness.save_state({"seed": seed, "fixtures": fixtures.to_dict(),
                                "applied": applied, "clock": clock})
    report.add("migrate", PASS, {"applied": applied, "clock": clock,
                                 "clock_probe": offset_probe,
                                 "seed": seed, "balances": before,
                                 "usage_rows": fixtures.usage_rows,
                                 "state_file": str(state)})
    return fixtures


def _clock_probe(conn) -> dict:
    """Prove the SHARED clock (`infrx.now()`, D1's) really moves, and comes back to rest.

    E2R item 2: this used to probe E2's private `infrx_e2_test.now()`, a function no
    migration and no product code ever calls. A clock probe that passes on a function
    nothing reads proves nothing about R7.
    """
    probe = pgstate.probe_clock(conn)
    if probe["moved_s"] != 3600.0 or abs(probe["at_rest_s"]) > 1.0 \
            or abs(probe["after_rollback_s"]) > 1.0:
        raise harness.HarnessError(
            f"the shared test clock does not behave as R7 requires: {probe}")
    return probe


def rls(report: Report, fixtures) -> None:
    import psycopg
    with psycopg.connect(harness.pg_dsn(), autocommit=True) as conn:
        rows = pgstate.run_role_matrix(conn, fixtures)
    failed = [row for row in rows if not row["passed"]]
    report.add("rls", FAIL if failed else PASS,
               {"cases": len(rows), "failed": failed or None,
                "roles": sorted({row["role"] for row in rows})}, rows=rows)


def engine(report: Report) -> None:
    """The exported conformance suite, unmodified, against the HTTP fake engine."""
    harness.api_on_path()
    import fake_vllm
    from infrx.contracts.conformance import run_engine_conformance
    server = fake_vllm.FakeVllmServer(harness.PORTS["fake_vllm"])
    LIVE_SERVERS.append(server)
    try:
        server.start()
        ran = run_engine_conformance(fake_vllm.engine_factory(server))
        report.add("engine", PASS, {"suite": "run_engine_conformance", "cases": ran,
                                   "transport": "http", "url": server.base_url,
                                   "skipped_hooks": []})
    except Exception as exc:                       # noqa: BLE001
        report.add("engine", FAIL, f"{type(exc).__name__}: {exc}")
    finally:
        server.stop()
        if server in LIVE_SERVERS:
            LIVE_SERVERS.remove(server)


# The skips `make api-test` may report, each attributed. Remove an entry the day its cause
# lands: D4's pgtesting `stream` hook ("missing optional hook 'stream'", tests/d's conformance)
# landed with 0017, so none is expected today.
KNOWN_API_SKIPS: tuple[str, ...] = ()
# pytest's short summary for the make targets and the backend suite: failures and errors (its
# default, "fE") AND skip reasons. A bare `-rs` REPLACES the default, and a red run then names
# no failing case at all (confirmation G-N1, measured).
SUITE_ADDOPTS = "-rfEs"


def make_env() -> dict[str, str]:
    """The canonical make targets' environment (E3B phase 3 review H-B1). `-rfEs` always;
    while this run's stack is up, also M1-L2's S3 cases pointed at its MinIO with MinIO's
    local literals (`INFRX_M_S3_ENDPOINT`, `INFRX_M_S3_LOCAL_CREDS`), or `make api-test`
    reports them as skips nobody attributed and the stage fails."""
    env = {"PYTEST_ADDOPTS": SUITE_ADDOPTS}
    if harness.load_state():
        env.update(INFRX_M_S3_ENDPOINT=harness.s3_endpoint(), INFRX_M_S3_LOCAL_CREDS="1")
        # IR3F-2(b): the D suites inside `make api-test` get the gate's own D task (tasklocal
        # "e3b2d") and lock/queue Valkeys, never the stack's PostgreSQL or another lane's
        env.update(INFRX_D_TASK="e3b2d", INFRX_D2_VALKEY_PORT="55468",
                   INFRX_D2_VALKEY_CONTAINER="infrx-e3b2d-valkey", INFRX_Q_VALKEY_PORT="55469")
    return env


def suites(report: Report, *, own_only: bool, leave_out: tuple[str, ...] = ()) -> None:
    """Cross-module discovery, measured. The canonical targets are the root Makefile's
    (08 §7); this suite has none yet, so it is invoked directly and `make integration` is
    an integration request. `leave_out`: suites another stage already ran (E2C-FR-2)."""
    runs = [shell([sys.executable, "-m", "pytest", "-q", "tests/integration",
                   "-p", "no:cacheprovider", *(f"--ignore={path}" for path in leave_out)],
                  cwd=harness.REPO_ROOT,
                  env={"INFRX_E2_CANARY": "off"})]
    if not own_only:
        for target in ("api-test", "console-test", "bench-test"):
            # E3B phase 2: the D suite alone has grown past 30 min on a shared host. `-rs`
            # makes pytest name every skip, so an unexpected one fails the stage (below).
            runs.append(shell(["make", target], cwd=harness.REPO_ROOT, timeout=3600.0,
                              env=make_env()))
    failed = [run["argv"] for run in runs if run["exit"] != 0]
    # E2R item 4: exit 0 is not evidence that anything ran. `make bench-test` prints
    # "not run - models/marlin2b/tests does not exist yet" and exits 0; a target whose
    # command is missing, whose suite collected nothing, or whose `if` branch echoed a
    # sentence does the same. A run that reports no passing cases at all is therefore a
    # FAILURE of this stage, not a pass with an empty count - the whole point of the stage
    # is that cross-module discovery is measured rather than assumed.
    silent = [run["argv"] for run in runs
              if not run["counts"].get("passed") and not run["counts"].get("node_pass")]
    # Review F6-findings: a skip is not a pass. `make api-test`'s skips must be the known,
    # attributed ones (none since D4's StreamStore hook landed); any other reason fails it.
    unexpected = sorted({reason for run in runs if run["argv"] == "make api-test"
                         for reason in run.get("skips", ())
                         if not any(known in reason for known in KNOWN_API_SKIPS)})
    # Confirmation G-B3: a skip COUNT with no parsed reason is a skip nobody attributed (the
    # reasons come from `-rs` lines; if they cannot be read, the count still fails the stage).
    unread = [run["argv"] for run in runs if run["argv"] == "make api-test"
              and run["counts"].get("skipped") and not run.get("skips")]
    report.add("suites", FAIL if (failed or silent or unexpected or unread) else PASS,
               {"runs": [{k: run.get(k) for k in ("argv", "exit", "counts", "skips",
                                                  "failure_lines")} for run in runs],
                "nonzero_exit": failed or None,
                "reported_no_tests": silent or None,
                "unexpected_skips": unexpected or None,
                "skips_without_reasons": unread or None}, runs=runs)


def mutation(report: Report, *, layer: str) -> None:
    """R32: every invariant this task claims must be killable, on a temp copy."""
    import mutants
    stack = bool(harness.load_state())
    try:
        # E3B phase 2 (I3B req 8): E's list and I3B's, through the one runner.
        results = [mutants.run_one(mutant, stack_available=stack)
                   for mutant in mutants.all_mutants() if layer == "all" or mutant.layer == 1]
    except Exception as exc:                       # noqa: BLE001 - reported, and the JSON
        # report is still written (E3B run 3 lost it to a HarnessError in _reprovision).
        report.add("mutants", FAIL, f"the mutation run itself failed: {exc!r}")
        return
    # `mutants.summarise` is the ONE place the verdict is counted: this stage used to apply its
    # own rule and reported the two controls - which MUST survive - as survivors, failing a run
    # whose mutation list was perfectly healthy.
    summary = mutants.summarise(results)
    status = FAIL if summary["problems"] else (PENDING if summary["pending"] else PASS)
    report.add("mutants", status,
               {key: summary[key] for key in
                ("mutants", "killed", "controls_survived", "not_killed", "pending", "problems")},
               results=results)


def canary(report: Report) -> None:
    """An INTENTIONAL failure must be detected, not skipped (E2 acceptance).

    Both halves are proved: a Python case and a console case, each toggled by the same
    environment variable, each expected to FAIL. A green run here is the real failure -
    it would mean the runners cannot see a broken test.
    """
    runs = {
        "python": shell([sys.executable, "-m", "pytest", "-q", "tests/integration",
                         "-p", "no:cacheprovider", "-k", "canary"],
                        cwd=harness.REPO_ROOT, env={"INFRX_E2_CANARY": "fail"},
                        needle="E2 canary"),
        "console": shell(["pnpm", "test"], cwd=harness.REPO_ROOT / "apps" / "app",
                         env={"INFRX_E2_CANARY": "fail"}, needle="E2 canary"),
    }
    problems = []
    for half, run in runs.items():
        if run["exit"] == 0:
            problems.append(f"{half}: the canary failure was NOT detected (exit 0)")
        elif not run["named"]:
            problems.append(f"{half}: exit {run['exit']} but no canary named in the output")
    report.add("canary", FAIL if problems else PASS,
               {"expected": "both runners fail and name the canary",
                "problems": problems or None,
                "runs": {half: {"exit": run["exit"], "counts": run["counts"],
                                "named": run["named"]}
                         for half, run in runs.items()}}, runs=runs)


def _backend_stack():
    """`backend/stack.py`, imported on first use (it pulls in the contracts package)."""
    if str(harness.HERE / "backend") not in sys.path:
        sys.path.insert(0, str(harness.HERE / "backend"))
    import stack
    return stack


BACKEND_SUITE = "tests/integration/backend"
PENDING_MARK = re.compile(r"PENDING\[([A-Z0-9,-]+)\]")


def classify(junit_xml: str) -> dict:
    """Per-case outcome from pytest's JUnit XML: passed / failed / pending(ids) / skipped.

    A skip is PENDING only when its message carries `PENDING[<ids>]` (backend/stack.py's
    closed vocabulary); any other skip at layer 3 means a case that should have run did not.
    """
    import xml.etree.ElementTree as ET
    cases = {"passed": [], "failed": [], "pending": {}, "skipped": []}
    for case in ET.fromstring(junit_xml).iter("testcase"):
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None or case.find("error") is not None:
            cases["failed"].append(name)
        elif (skip := case.find("skipped")) is not None:
            mark = PENDING_MARK.search(skip.get("message", "") + (skip.text or ""))
            # An expected failure is not a pending case whatever its message says: only
            # `stack.pending()`'s skip is (review rv08).
            if mark and skip.get("type") != "pytest.xfail":
                for task in mark.group(1).split(","):
                    cases["pending"].setdefault(task, []).append(name)
            else:
                cases["skipped"].append(name)
        else:
            cases["passed"].append(name)
    return cases


def backend_verdict(cases: dict, exit_code: int) -> str:
    """FAIL beats PENDING beats PASS. A pending case is never a pass, a plain skip at layer 3
    is a case that did not run, and a run that ran nothing proves nothing."""
    if cases["failed"] or cases["skipped"] or not cases["passed"] or exit_code not in (0,) \
            or stale_pending(cases):
        return FAIL
    return PENDING if cases["pending"] else PASS


def stale_pending(cases: dict) -> list[str]:
    """Pending ids that name a task tasks.json marks implemented/integrated (E3B phase 2,
    review H2): a merged task blocks nothing, whichever vocabulary the skip came through -
    `stack.pending`, I3B's kit, or a hand-written skip.

    R3-1: that holds while the merged task's cutover is HELD, too. The owner of held work is
    the cutover request - an owner reference such as `G2-R1`, which is no task and so never
    stale - not the merged task. A `stack.RESIDUAL` id is excused only where every case naming
    it is one of I3B's read-only recovery cases; in any other case it is stale."""
    tasks = {task["id"]: task["status"] for task in json.loads(
        (harness.REPO_ROOT / "research" / "plan" / "tasks.json").read_text())["tasks"]}
    residual = _backend_stack().RESIDUAL
    return sorted(task for task, names in cases["pending"].items()
                  if tasks.get(task) in ("implemented", "integrated")
                  and not (task in residual and all(_is_recovery(name) for name in names)))


def _is_recovery(case: str) -> bool:
    """`classname::name` of one of I3B's cases (`tests/integration/backend/recovery/`)."""
    return "recovery" in case.split("::")[0].split(".")


def backend_summary(cases: dict, exit_code: int) -> tuple[str, dict]:
    """The backend stage's status and detail, from the classified cases - the ONE place both
    are computed, so the stage cannot report a status its own counts contradict."""
    distinct = {name for names in cases["pending"].values() for name in names}
    return backend_verdict(cases, exit_code), {
        "passed": len(cases["passed"]), "pending": len(distinct),
        "failed": len(cases["failed"]), "failed_cases": cases["failed"] or None,
        "not_run": cases["skipped"] or None,
        "stale_pending": stale_pending(cases) or None,
        "detected": sorted(name.split("::")[-1] for name in cases["passed"]
                           if "detects" in name),
        "pending_by_id": {task: len(names) for task, names in sorted(cases["pending"].items())}}


def backend(report: Report) -> None:
    """E3B phase 1: PostgREST beside E2's stack, then the backend gate suite."""
    import tempfile
    backend_stack = _backend_stack()
    try:
        postgrest = backend_stack.postgrest_up()
    except harness.HarnessError as exc:
        report.add("backend", PENDING, f"could not start PostgREST: {exc}")
        return
    try:
        with tempfile.TemporaryDirectory(prefix=f"{harness.PROJECT}-e3b-") as tmp:
            junit = Path(tmp) / "backend.xml"
            run = shell([sys.executable, "-m", "pytest", "-q", BACKEND_SUITE, "-p",
                         "no:cacheprovider", SUITE_ADDOPTS, f"--junitxml={junit}"],
                        cwd=harness.REPO_ROOT, env={"INFRX_E2_CANARY": "off"})
            cases = classify(junit.read_text()) if junit.exists() else \
                {"passed": [], "failed": ["<no junit report>"], "pending": {}, "skipped": []}
        status, summary = backend_summary(cases, run["exit"])
        # Confirmation G-N2: recorded BEFORE its teardown, so the stage's seconds are its own.
        report.add("backend", status, {"postgrest": postgrest, **summary},
                   cases=cases, runs=[run])
    finally:
        # Measured: PostgREST's pool holds sessions on `infrx_e2`, so a dirtying mutant's
        # re-provision (DROP DATABASE) later in the run is refused while it is up.
        backend_teardown(report)


def backend_teardown(report: Report) -> None:
    """E3B's PostgREST sits on E2's network, which cannot go while it is attached."""
    try:
        report.add("backend-teardown", PASS, {"removed": _backend_stack().postgrest_down()})
    except harness.HarnessError as exc:
        report.add("backend-teardown", FAIL, str(exc))


def teardown(report: Report) -> None:
    try:
        removed = harness.down()
    except harness.HarnessError as exc:
        report.add("teardown", FAIL, str(exc))
        return
    harness.clear_state()
    left = harness.foreign_containers()
    report.add("teardown", PASS, {"removed": removed, "still_named_ours_but_not_ours": left})


# --------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=20260921,
                        help="fixture seed; the same seed gives the same uuids and rows")
    parser.add_argument("--layer", choices=("1", "2", "all", "3"), default="all",
                        help="1 = nothing that needs a container; 3 = all + the E3B backend "
                             "gate (exits 3 while any backend case is pending)")
    parser.add_argument("--keep", action="store_true",
                        help=f"leave the stack running (still only {harness.PREFIX}* containers)")
    parser.add_argument("--pull", action="store_true", help="pull the pinned digests first")
    parser.add_argument("--canary", action="store_true",
                        help="also prove an intentional failure is detected")
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    parser.add_argument("--only-suites", action="store_true",
                        help="run only this suite, not the canonical make targets")
    parser.add_argument("--no-mutants", action="store_true",
                        help="skip the R32 mutation run (it is ~2 minutes)")
    args = parser.parse_args(argv)

    report = Report()
    want_services = args.layer in ("2", "all", "3")
    with signals_handled():
        return _run(report, args, want_services)


def _run(report: Report, args, want_services: bool) -> int:
    have_services = preflight(report, want_services=want_services)
    interrupted = None
    try:
        if have_services and services(report, pull=args.pull):
            fixtures = migrate(report, args.seed)
            if fixtures is not None:
                rls(report, fixtures)
                if args.layer == "3":
                    backend(report)
        elif want_services:
            report.add("migrate", PENDING, "no services: migration and RLS cannot run")
            report.add("rls", PENDING, "no services: the role matrix cannot run")
            if args.layer == "3":
                report.add("backend", PENDING, "no services: the backend gate cannot run")
        if args.layer != "2":
            engine(report)
            # E2C-FR-2: at layer 3 the backend stage ran BACKEND_SUITE with PostgREST up and
            # then tore it down; rerunning it here would only skip its journey cases.
            suites(report, own_only=args.only_suites,
                   leave_out=(BACKEND_SUITE,) if args.layer == "3" else ())
            if not args.no_mutants:
                mutation(report, layer="all" if have_services else "1")
            if args.canary:
                canary(report)
    except Interrupted as stop:
        interrupted = stop
        report.add("interrupted", FAIL, f"signal {stop.signum}: tearing the stack down")
    finally:
        if have_services and not args.keep and args.layer == "3":
            backend_teardown(report)          # E3B: before E2's network can go
        if have_services and not args.keep:
            teardown(report)
        elif args.keep:
            report.add("teardown", SKIP, "--keep: the stack is still up")

    payload = report.as_json()
    if args.report:
        args.report.write_text(payload)
    print(payload)
    print(f"\nexit {report.exit_code} "
          f"({'all stages passed' if report.exit_code == 0 else 'see the stages above'})")
    if interrupted is not None:
        # 128 + signal is what a shell reports for a signalled process, and the teardown above
        # has already run - which is the whole point of catching it.
        return 128 + interrupted.signum
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
