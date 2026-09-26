#!/usr/bin/env python3
"""Prove an old release's runtime SQL on a newer schema (I8 KNOWN-GOOD-PROOF; task-local only).

    apps/infrx-api/.venv/bin/python infra/runbooks/schema_proof.py <40-hex target sha> \
        [--candidate <rev carrying the migrations, default HEAD>] [--suite tests/d/test_x.py ...] \
        [--work <scratch dir>] [--task d10]
    SCHEMA_PROOF_DSN=... (optional, never an argument): a database migrated by deploy/migrate.py

What it does, each line printed as `PASS|FAIL <what> <detail>`:
  schema   with SCHEMA_PROOF_DSN: that database's `supabase_migrations.schema_migrations`
           (read-only) holds exactly the candidate's files - same versions, and each version's
           statements are the file (migrate.py: byte for byte; the Supabase CLI's split: every
           statement verbatim, in order, nothing else but whitespace, `;` and comments) - so the
           proof below is about THAT catalog
  <suite>  `git archive <target> apps/infrx-api` into the scratch dir, the candidate's
           `apps/app/supabase/migrations` in place of the target's own, and the candidate's
           task-local port registry (test data only: the old one lacks newer lanes' ports);
           `uv sync --frozen` there; then every one of the TARGET's own tests/d suites (and
           PROBE), one pytest process each, on its own harness - which builds its database
           from the migration directory it finds, i.e. the candidate's 0001..NNNN, so its
           admit/claim/settle/journal/read SQL runs on that catalog. The SHAPE cases are
           deselected by name and printed as SKIP with their reason. A suite PASSes only
           when pytest exits 0 with at least one pass and no skip (pytest exits 0 on skips).
Exit 0 every line PASS, 1 any FAIL, 2 usage. `through` = the candidate's newest migration.
Only task-local services: --task's PostgreSQL and Valkey (the candidate's registry); the DSN is read.
"""
from __future__ import annotations

import argparse
import os
import re
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = "apps/app/supabase/migrations"
REGISTRY = "apps/infrx-api/infrx/contracts/tasklocal.py"
DSN_ENV = "SCHEMA_PROOF_DSN"
# Cases of the target's own tests/d that cannot hold on a newer catalog for a reason that is
# not the old runtime's SQL. Each is deselected BY NAME and printed as SKIP with its reason;
# anything else that fails is a FAIL. Measured at bda1586/4226315 on 0001-0023 (every one
# passes on the target's own 0001-0018): KNOWN-GOOD-PROOF evidence; the last three on 0001-0025
# (each passes on 0001-0023): KNOWN-GOOD-PROOF-2 evidence.
SHAPE = {
    # the old tree's catalog enumerations: the schema grew (0019-0023 objects, grants to the
    # runtime/monitor logins, consumer read functions); not SQL the old runtime runs
    "tests/d/test_credit_schema.py::test_d1r_leaves_the_0001_0005_schema_unchanged": "enumerates the old catalog",
    "tests/d/test_credit_schema.py::test_credit_units__no_conversion_and_explicit_regimes": "scans every function, incl. 0021's consumer_jobs",
    "tests/d/test_credit_schema.py::test_credit_privileges__service_reads_money_and_writes_through_seams": "enumerates the old EXECUTE surface",
    "tests/d/test_credit_schema.py::test_rerun__applying_d1r_twice_is_a_no_op": "re-applies an old migration over the new catalog",
    "tests/d/test_schema_postgres.py::test_dur_rls__the_mutation_boundary_is_narrow_and_fails_closed": "enumerates the old grants (0021 grants infrx_runtime)",
    "tests/d/test_schema_postgres.py::test_dur_rls__the_execute_surface_is_enumerated": "enumerates the old EXECUTE surface",
    # test-rig SQL/strictness, not the runtime's path
    "tests/d/test_admission.py::test_media__uploads_finalize_once_and_objects_delete_only_when_idle":
        "the test's own UPDATE of infrx.media_uploads; the old runtime keeps uploads in memory (media/uploads.py) and never writes that table",
    "tests/d/test_settle.py::test_credit_spend__sql_settle_equals_v2_settle_on_the_grid":
        "validates terminalize's raw outcome strictly; the runtime reads only _OUTCOME_FIELDS (jobstore._outcome), so 0020's result_expires_at is ignored",
    "tests/d/test_admission.py::test_results__write_once_owner_read_and_the_prepared_prompt_count":
        "reads a result before the job's outcome (0020: result_pending); the runtime reads only a committed outcome's result_ref - the probe proves that path",
    "tests/d/test_store_requests.py::test_put_result__write_once_reference_and_owner_read":
        "the same pre-outcome read through the adapter; the probe proves the post-outcome read",
    # 0024/0025: the browser key insert and the browser surface; not the old runtime's SQL
    "tests/d/test_schema_postgres.py::test_dur_rls__browser_roles_cannot_reach_protected_state":
        "its browser positive control 'owner creates a key' uses an unverified owner; 0024 (C3A WR-C3A-4) "
        "admits a browser api_keys INSERT only for a verified individual - the App's path; the old runtime "
        "inserts keys only through PgTenantStore on the service seam (test_operations_pg)",
    "tests/d/test_credit_schema.py::test_operator_seams__audit_keys_suspension_usage_holds":
        "its 'deployed console's own insert' is a browser api_keys INSERT by an unverified consumer, which "
        "0024 (C3A WR-C3A-4) refuses by design - the App's path; the old runtime's key/suspension SQL "
        "(PgTenantStore, set_suspension) is walked by test_operations_pg",
    "tests/d/test_schema_postgres.py::test_dur_rls__the_browser_privilege_surface_is_enumerated":
        "enumerates the old browser surface (0025 grants authenticated SELECT on operator_wallet_drift / "
        "operator_unknown_usage)",
}
# The one path none of the target's suites walks end to end on PostgreSQL: its runtime's
# admit -> prepare -> claim -> complete (its own terminalize SQL) -> the gateway's result read
# (`read_result(org, outcome.result_ref)`, only ever for a committed outcome). Written into the
# scratch copy only, over the target's own harness and conformance helpers.
PROBE = '''
import asyncio

import pytest

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.conformance.jobs import _running

from . import pgstore


def test_schema_proof__the_old_runtime_admits_claims_settles_and_reads_its_result():
    h = pgstore.factory()

    async def body():
        request, admission, lease = await _running(h)
        done = await h.port.complete(lease, b.outcome(lease.job_id, h))
        assert done.state.value == "succeeded" and done.result_ref.startswith("infrx-result:")
        _, outcome = await h.port.get_owned(request.org_id, admission.job_handle)
        assert outcome.result_ref == done.result_ref
        text = await h.extra["store"].read_result(request.org_id, outcome.result_ref)
        assert text == "results/test/result.json"
        with pytest.raises(errors.NotFound):
            await h.extra["store"].read_result(b.ORG_B, outcome.result_ref)
        (bal,) = h.extra["conn"].execute("select count(*) from infrx.wallet_reconciliation "
                                         "where ledger_drift <> 0 or reserved_drift <> 0").fetchone()
        assert bal == 0
    asyncio.run(body())
'''


def git(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, **kw)


def candidate_files(rev: str) -> dict[str, str]:
    """version -> the text of the file at `rev`, for every NNNN_name.sql."""
    names = git("ls-tree", "--name-only", rev, f"{MIGRATIONS}/", text=True).stdout.split()
    return {Path(name).name[:4]: git("show", f"{rev}:{name}", text=True).stdout
            for name in names if re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", Path(name).name)}


# What may sit around the Supabase CLI's statements in a file: whitespace, `;` and comments (a
# line comment ends at its newline). Anything else is SQL the history does not hold.
GAP = re.compile(r"(?:\s|;|--[^\n]*\n|/\*.*?\*/)*", re.S)
END = re.compile(r"(?:\s|--[^\n]*\n|/\*.*?\*/)*(?:;|\Z)", re.S)     # a statement ends at its `;`


def covers(parts: list[str], text: str) -> bool:
    """The CLI's rows ARE the file: whole statements, verbatim and in order, with only GAP before,
    between and after them - nothing omitted, added, reordered or cut. A statement may carry
    the comment in front of it, so it may start anywhere GAP reaches."""
    text, pos = text + "\n", 0
    for part in (p.strip() for p in parts):
        at = next((i for i in range(pos, GAP.match(text, pos).end() + 1)
                   if part and text.startswith(part, i) and GAP.fullmatch(text, pos, i)), None)
        if at is None or not (part.endswith(";") or END.match(text, at + len(part))):
            return False
        pos = at + len(part)
    return GAP.fullmatch(text, pos) is not None


def compare(history: list[tuple[str, list[str]]], files: dict[str, str]) -> str | None:
    """None when the history rows (version, statements) are exactly `files`, else why not.
    migrate.py records a file as one statement (byte for byte); the Supabase CLI splits it
    (`covers`: the statements, in order, are the whole file)."""
    seen = dict(history)
    if sorted(seen) != sorted(files):
        return f"history x{len(seen)} vs candidate x{len(files)}: only in history " \
               f"{sorted(set(seen) - set(files))}, only in candidate {sorted(set(files) - set(seen))}"
    moved = sorted(v for v, parts in seen.items() if parts != [files[v]] and not covers(parts, files[v]))
    return f"statements differ from the candidate's files: {moved}" if moved else None


def check_dsn(files: dict[str, str]) -> tuple[bool, str]:
    import psycopg
    try:
        with psycopg.connect(os.environ[DSN_ENV], autocommit=False) as conn:
            conn.execute("set transaction_read_only = on")
            rows = conn.execute("select version, coalesce(statements, '{}') from "
                                "supabase_migrations.schema_migrations").fetchall()
    except Exception as failure:  # noqa: BLE001 - libpq may quote the DSN; never echo it
        return False, f"{DSN_ENV}: could not read its history ({type(failure).__name__})"
    why = compare([(v, list(s)) for v, s in rows], files)
    return why is None, why or f"history = the candidate's files 0001-{max(files)}"


def scratch(target: str, candidate: str, work: Path) -> Path:
    tree = work / target[:7]
    shutil.rmtree(tree, ignore_errors=True)
    tree.mkdir(parents=True)
    for rev, path in ((target, "apps/infrx-api"), (candidate, MIGRATIONS)):
        archive = git("archive", rev, path).stdout
        subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    shutil.rmtree(tree / "apps/infrx-api/.venv", ignore_errors=True)
    (tree / REGISTRY).write_bytes(git("show", f"{candidate}:{REGISTRY}").stdout)
    (tree / "apps/infrx-api/tests/d/test_schema_proof_probe.py").write_text(PROBE)
    return tree / "apps/infrx-api"


def suites(api: Path) -> list[str]:
    """Every tests/d suite of the target (mutation runners excluded) plus the probe."""
    return sorted(str(p.relative_to(api)) for p in (api / "tests/d").glob("test_*.py")
                  if "mutants" not in p.name)


def task_env(api: Path, task: str) -> dict[str, str]:
    """INFRX_D_TASK for the PostgreSQL harness, and the task's own Valkey for the old
    `tests/d/vkstore.py`, whose default is D2's container and port (55463)."""
    valkey = runpy.run_path(str(api / "infrx/contracts/tasklocal.py"))["local_services"](task)["valkey"]
    return {"INFRX_D_TASK": task, "INFRX_D2_VALKEY_PORT": str(valkey.host_port),
            "INFRX_D2_VALKEY_CONTAINER": valkey.container}


def run_suite(api: Path, suite: str, env: dict[str, str],
              pytest: tuple[str, ...] = ("uv", "run", "--frozen", "--no-sync", "pytest")) -> tuple[bool, str]:
    skip = [case for case in SHAPE if case.startswith(f"{suite}::")]
    for case in skip:
        print(f"SKIP {case} {SHAPE[case]}", flush=True)
    done = subprocess.run([*pytest, "-q", "-p", "no:cacheprovider", suite,
                           *(f"--deselect={c}" for c in skip)],
                          cwd=api, capture_output=True, text=True,
                          env={**os.environ, **env})
    log = api.parents[1] / "logs" / f"{Path(suite).stem}.log"      # the whole run, for evidence
    log.parent.mkdir(exist_ok=True)
    log.write_text(done.stdout + done.stderr)
    tail = [line for line in done.stdout.splitlines() if line.strip()][-1:] or ["(no output)"]
    # pytest exits 0 on skips, and the old suites skip themselves without Docker, psycopg or
    # Valkey: a suite that skipped a case, or passed none, did not run the old SQL
    ran = re.search(r"\b[1-9]\d* passed\b", tail[0]) and not re.search(r"\bskipped\b", tail[0])
    return done.returncode == 0 and bool(ran), tail[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target")
    ap.add_argument("--candidate", default="HEAD")
    ap.add_argument("--suite", action="append", help="a target-relative suite (default: every tests/d suite)")
    ap.add_argument("--work", type=Path, default=Path(os.environ.get("TMPDIR", "/tmp")) / "schema-proof")
    ap.add_argument("--task", default="d10", help="INFRX_D_TASK: the port block the harness uses")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", a.target):
        ap.error("the target is a full 40-hex commit id")
    for rev in (a.target, a.candidate):
        if git("cat-file", "-e", f"{rev}^{{commit}}").returncode != 0:
            ap.error(f"{rev}: not a commit in this repository")
    files = candidate_files(a.candidate)
    if not files:
        ap.error(f"{a.candidate} carries no {MIGRATIONS}")
    print(f"target {a.target} on candidate {a.candidate} migrations 0001-{max(files)} "
          f"({len(files)} files)")
    passed = True

    def report(what: str, ok: bool, detail: str) -> None:
        nonlocal passed
        passed &= ok
        print(f"{'PASS' if ok else 'FAIL'} {what} {detail}", flush=True)

    if os.environ.get(DSN_ENV, "").strip():
        report("schema", *check_dsn(files))
    api = scratch(a.target, a.candidate, a.work)
    synced = subprocess.run(["uv", "sync", "--frozen", "--all-extras"], cwd=api,
                            capture_output=True, text=True)
    if synced.returncode != 0:
        report("uv-sync", False, (synced.stderr.strip().splitlines() or [""])[-1])
    else:
        for suite in a.suite or suites(api):
            report(suite, *run_suite(api, suite, task_env(api, a.task)))
    print(f"{'PASS' if passed else 'FAIL'} through {max(files)}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
