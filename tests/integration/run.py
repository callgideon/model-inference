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
  teardown   remove exactly what this project created, and prove nothing is left

Exit codes:  0 everything ran and passed
             1 something failed
             3 a service-backed stage could not run (reported PENDING, never PASS)

`--layer 1` runs only what needs no container. `--canary` additionally proves an
INTENTIONAL failure is detected rather than skipped.
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

    def add(self, stage: str, status: str, detail: object = None, **extra) -> dict:
        entry = {"stage": stage, "status": status, "detail": detail,
                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}
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
        return json.dumps({"started": self.started.isoformat(timespec="seconds"),
                           "seconds": round((datetime.now(timezone.utc)
                                             - self.started).total_seconds(), 1),
                           "exit_code": self.exit_code, "stages": self.stages},
                          indent=2, default=str)


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
    result = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                            timeout=timeout, env={**os.environ, **(env or {})})
    output = result.stdout + result.stderr
    return {"argv": " ".join(argv), "cwd": str(cwd.relative_to(harness.REPO_ROOT) or "."),
            "exit": result.returncode, "seconds": round(time.monotonic() - started, 1),
            "counts": counts(output),
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
            harness.down()
        busy = harness.busy_ports()
        if busy:
            report.add("preflight", FAIL,
                       f"these task-local ports are already in use: {busy} - stop whatever holds "
                       f"them (a previous `--keep` run, or `docker compose -p infrx-e2 down -v`)")
            return False
    except harness.HarnessError as exc:
        report.add("preflight", FAIL, str(exc))
        return False
    report.add("preflight", PASS, {"docker": why, "images": harness.compose_images(),
                                   "not_yet_pulled": missing, "ports": harness.PORTS,
                                   "working_dir": harness.working_dir()})
    return True


def fake_server_orphans() -> list[int]:
    """PIDs of a fake vLLM this harness started and never stopped.

    Only processes whose command line names *this* checkout's `fake_vllm.py` count: another
    checkout's server is not ours to report as our leak, and nothing here kills anything.
    """
    marker = str((harness.HERE / "fake_vllm.py").resolve())
    # Linux exposes untruncated argv through procfs. macOS has no procfs;
    # double-wide ps explicitly disables the terminal-width truncation that the
    # original reviewer caught. This function reports PIDs; it never kills them.
    if not Path("/proc").is_dir():
        result = subprocess.run(["ps", "-axww", "-o", "pid=,command="],
                                capture_output=True, text=True, check=True)
        pids = []
        for line in result.stdout.splitlines():
            columns = line.strip().split(None, 1)
            if len(columns) == 2 and columns[0].isdigit():
                pid = int(columns[0])
                if pid != os.getpid() and marker in columns[1]:
                    pids.append(pid)
        return sorted(pids)
    pids = []
    for entry in Path("/proc").iterdir():
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


def suites(report: Report, *, own_only: bool) -> None:
    """Cross-module discovery, measured. The canonical targets are the root Makefile's
    (08 §7); this suite has none yet, so it is invoked directly and `make integration` is
    an integration request."""
    runs = [shell([sys.executable, "-m", "pytest", "-q", "tests/integration",
                   "-p", "no:cacheprovider"], cwd=harness.REPO_ROOT,
                  env={"INFRX_E2_CANARY": "off"})]
    if not own_only:
        for target in ("api-test", "console-test", "bench-test"):
            runs.append(shell(["make", target], cwd=harness.REPO_ROOT))
    failed = [run["argv"] for run in runs if run["exit"] != 0]
    # E2R item 4: exit 0 is not evidence that anything ran. `make bench-test` prints
    # "not run - models/marlin2b/tests does not exist yet" and exits 0; a target whose
    # command is missing, whose suite collected nothing, or whose `if` branch echoed a
    # sentence does the same. A run that reports no passing cases at all is therefore a
    # FAILURE of this stage, not a pass with an empty count - the whole point of the stage
    # is that cross-module discovery is measured rather than assumed.
    silent = [run["argv"] for run in runs
              if not run["counts"].get("passed") and not run["counts"].get("node_pass")]
    report.add("suites", FAIL if (failed or silent) else PASS,
               {"runs": [{k: run[k] for k in ("argv", "exit", "counts")} for run in runs],
                "nonzero_exit": failed or None,
                "reported_no_tests": silent or None}, runs=runs)


def mutation(report: Report, *, layer: str) -> None:
    """R32: every invariant this task claims must be killable, on a temp copy."""
    import mutants
    stack = bool(harness.load_state())
    results = [mutants.run_one(mutant, stack_available=stack) for mutant in mutants.MUTANTS
               if layer == "all" or mutant.layer == 1]
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
    parser.add_argument("--layer", choices=("1", "2", "all"), default="all",
                        help="1 = nothing that needs a container")
    parser.add_argument("--keep", action="store_true",
                        help="leave the stack running (still only infrx-e2-* containers)")
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
    want_services = args.layer in ("2", "all")
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
        elif want_services:
            report.add("migrate", PENDING, "no services: migration and RLS cannot run")
            report.add("rls", PENDING, "no services: the role matrix cannot run")
        if args.layer != "2":
            engine(report)
            suites(report, own_only=args.only_suites)
            if not args.no_mutants:
                mutation(report, layer="all" if have_services else "1")
            if args.canary:
                canary(report)
    except Interrupted as stop:
        interrupted = stop
        report.add("interrupted", FAIL, f"signal {stop.signum}: tearing the stack down")
    finally:
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
