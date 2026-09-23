#!/usr/bin/env python3
"""R32/R40 for I3B: one single-edit mutant per claimed invariant, each killed by a NAMED case.

    apps/infrx-api/.venv/bin/python tests/integration/backend/recovery/mutants_i3b.py --list
    apps/infrx-api/.venv/bin/python tests/integration/backend/recovery/mutants_i3b.py
    apps/infrx-api/.venv/bin/python tests/integration/backend/recovery/mutants_i3b.py --layer all

E's runner (`tests/integration/mutants.py`) does the work - temporary copy, one edit, the
kill is a pytest *failure* on the named selector and never an error - so this file is only
the list. Two additions, both local: the copy also carries `infra/` (the alert rules and
runbooks are claims too), and a layer-3 mutant needs E2's live stack (`run.py --layer 3
--keep`), otherwise it is reported pending, never killed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))                  # tests/integration

import harness                                          # noqa: E402
import mutants                                          # noqa: E402
from mutants import Mutant                              # noqa: E402

OBSERVE = "tests/integration/backend/recovery/test_observe.py"
DRILLS = "tests/integration/backend/recovery/test_recovery.py"
RESTORE = "tests/integration/backend/recovery/test_restore.py"
METRICS = "apps/infrx-api/infrx/observe/metrics.py"
ROUTE = "apps/infrx-api/infrx/observe/route.py"
HOST = "apps/infrx-api/infrx/observe/host.py"
ALERTS = "apps/infrx-api/infrx/observe/alerts.py"
RULES = "infra/alerts/alerts.json"
DASHBOARD = "infra/alerts/dashboard.json"
PGRESTORE = "infra/runbooks/pgrestore.py"

MUTANTS: tuple[Mutant, ...] = (
    Mutant("i3bc01", "CONTROL: a comment-only edit in the metrics module must SURVIVE",
           METRICS, 'OTHER = "other"', 'OTHER = "other"  # control: no behaviour change',
           OBSERVE, "i3b_ob", must_survive=True),
    # ---------------- sanitization (R59 applied to telemetry)
    Mutant("i3bm01", "a label value outside its vocabulary never reaches the exposition",
           METRICS,
           "    return value in allowed if isinstance(allowed, frozenset) "
           "else bool(allowed.fullmatch(value))",
           "    return True", OBSERVE, "ob01"),
    Mutant("i3bm02", "a tenant label is its hash, never the org id",
           METRICS, "            if label == TENANT_LABEL:\n                value = tenant_label",
           "            if False:\n                value = tenant_label", OBSERVE, "ob02"),
    Mutant("i3bm03", "a refused label value is written as `other`, not as itself",
           METRICS, "                value = OTHER\n", "                pass\n", OBSERVE, "ob01"),
    Mutant("i3bm04", "a refused label value is counted in infrx_metrics_label_rejected_total",
           METRICS, "                if count:\n                    self._reject(name)",
           "                if False:\n                    self._reject(name)", OBSERVE, "ob01"),
    Mutant("i3bm05", "a negative duration never reaches a histogram",
           METRICS, "        if not _finite(value) or value < 0:\n            return self._reject(name)\n"
                    "        key = self._key(name, labels)\n        with self._lock:\n"
                    "            # [cumulative",
           "        if not _finite(value):\n            return self._reject(name)\n"
           "        key = self._key(name, labels)\n        with self._lock:\n            # [cumulative",
           OBSERVE, "ob03"),
    Mutant("i3bm06", "a boolean is not a number",
           METRICS, "and not isinstance(value, bool) \\\n", "\\\n", OBSERVE, "ob03"),
    Mutant("i3bm70", "M2: `mount` is the configured set, not any word matching the pattern",
           METRICS, '            if label == "mount":\n                allowed = self.mounts\n',
           "", OBSERVE, "ob16"),
    Mutant("i3bm71", "M2: a mount name outside its syntax is refused at construction", METRICS,
           "        if not all(_MOUNT.fullmatch(mount) for mount in mounts):",
           "        if False:", OBSERVE, "ob16"),
    Mutant("i3bm72", "M6: a process name outside its syntax is refused", METRICS,
           "        if not _PROCESS.fullmatch(process):", "        if False:", OBSERVE, "ob16"),
    Mutant("i3bm73", "M6: an undeclared label is an error, never silently dropped", METRICS,
           "        if sorted(labels) != sorted(declared):", "        if False:", OBSERVE, "ob16"),
    Mutant("i3bm74", "M6: the histogram bounds are the declared ones", METRICS,
           "SECONDS = (0.005, 0.01, 0.025,", "SECONDS = (0.005, 0.025,", OBSERVE, "ob16"),
    # ---------------- phases and the header
    Mutant("i3bm07", "phase names are E1B's", METRICS,
           '"journal", "persist", "settle")', '"journal", "persistence", "settle")',
           OBSERVE, "ob04"),
    Mutant("i3bm08", "an undeclared phase name is never echoed into Server-Timing", METRICS,
           "    for phase in PHASES:\n        seconds = timings.get(phase)",
           "    for phase in timings:\n        seconds = timings.get(phase)", OBSERVE, "ob04"),
    Mutant("i3bm09", "histogram buckets are cumulative", METRICS,
           "                if value <= bound:\n                    counts[index] += 1\n",
           "                if value <= bound:\n                    counts[index] += 1\n"
           "                    break\n", OBSERVE, "ob05"),
    Mutant("i3bm10", "label values are escaped", METRICS,
           "def _escape(value: str) -> str:\n    return value.replace",
           "def _escape(value: str) -> str:\n    return value\n    return value.replace",
           OBSERVE, "ob05"),
    # ---------------- the route
    Mutant("i3bm11", "a request relayed by a proxy never gets /metrics", ROUTE,
           "    return peer in LOOPBACK and not any(name in request.headers for name in PROXY_HEADERS)",
           "    return peer in LOOPBACK", OBSERVE, "ob07"),
    Mutant("i3bm12", "only a loopback peer gets /metrics", ROUTE,
           "    return peer in LOOPBACK and not any(",
           "    return not any(", OBSERVE, "ob07"),
    # ---------------- host gauges
    Mutant("i3bm13", "an unreadable disk reports free ratio 0 (fails towards the alert)", HOST,
           '            reg.set("infrx_disk_free_ratio", 0.0, mount=mount)\n            continue',
           "            continue", OBSERVE, "ob08"),
    Mutant("i3bm14", "gpu_up is 0 when nvidia-smi does not answer", HOST,
           'reg.set("infrx_gpu_up", 1.0 if parsed else 0.0)', 'reg.set("infrx_gpu_up", 1.0)',
           OBSERVE, "ob08"),
    Mutant("i3bm15", "available memory is MemAvailable", HOST,
           'if name in ("MemTotal", "MemAvailable"):', 'if name in ("MemTotal", "MemFree"):',
           OBSERVE, "ob08"),
    Mutant("i3bm35", "M3: a lost GPU keeps no frozen memory/utilization series", HOST,
           "        reg.clear(family)                           # a lost GPU keeps no frozen reading",
           "        pass", OBSERVE, "ob08"),
    Mutant("i3bm36", "M3: a mount that stops answering keeps no old byte counts", HOST,
           '    reg.clear("infrx_disk_bytes")', "    pass", OBSERVE, "ob08"),
    Mutant("i3bm37", "M3: a failed nvidia-smi fabricates no memory reading", HOST,
           '    reg.set("infrx_gpu_up", 1.0 if parsed else 0.0)\n',
           '    reg.set("infrx_gpu_up", 1.0 if parsed else 0.0)\n'
           '    if not parsed:\n        reg.set("infrx_gpu_memory_bytes", 0.0, gpu="0", state="used")\n',
           OBSERVE, "ob08"),
    # ---------------- wiring helpers
    Mutant("i3bm16", "a reaped inference lease is a requeue, a reaped preparation a redispatch",
           METRICS, 'action="prepare_redispatched" if preparing else "requeued")',
           'action="requeued")', OBSERVE, "ob09"),
    Mutant("i3bm25", "a released unknown-usage hold is not a second terminal job", METRICS,
           "        elif isinstance(item, TerminalOutcome) and str(item.job_id) in released:",
           "        elif False:", OBSERVE, "ob09"),
    Mutant("i3bm17", "a terminal outcome counts its settlement", METRICS,
           '    reg.inc("infrx_settlements_total", settlement=outcome.settlement_state)',
           "    pass", OBSERVE, "ob09"),
    # ---------------- the evaluator
    Mutant("i3bm18", "an increase rule judges nothing on its first run", ALERTS,
           "            if previous is None:\n                continue",
           "            if False:\n                continue", OBSERVE, "ob12"),
    Mutant("i3bm19", "a counter that went down counts from zero", ALERTS,
           "value - before if before is not None and value >= before else value",
           "value - before if before is not None else value", OBSERVE, "ob12"),
    Mutant("i3bm20", "an unreadable source is an alert, never silence", ALERTS,
           '            firing.append({"alert": "ScrapeFailed"', '            [].append({"alert": "ScrapeFailed"',
           OBSERVE, "ob13"),
    Mutant("i3bm26", "M1: a failed source's previous samples are carried into the state",
           ALERTS, "        kept = {**(previous or {}), **samples} if failed else samples",
           "        kept = samples", OBSERVE, "ob14"),
    Mutant("i3bm27", "M1: a failure on the first run leaves the next run a first run", ALERTS,
           "    if args.state and not (failed and previous is None):",
           "    if args.state:", OBSERVE, "ob14"),
    Mutant("i3bm28", "M5: a file source older than --max-age is ScrapeFailed", ALERTS,
           "    if max_age is not None and time.time() - path.stat().st_mtime > max_age:",
           "    if False:", OBSERVE, "ob15"),
    Mutant("i3bm29", "M5: an exposition with no sample is ScrapeFailed, not silence", ALERTS,
           "            if not parsed:\n", "            if False:\n", OBSERVE, "ob15"),
    Mutant("i3bm21", "a ratio rule divides", ALERTS,
           "            values = {(): values[()] / total}", "            values = {(): values[()]}",
           OBSERVE, "ob11"),
    # ---------------- the rules and the dashboard as data
    Mutant("i3bm22", "DiskAlmostFull fires below 10% free", RULES,
           '"threshold": 0.10,\n      "threshold_status": "⚠️ TO BE VERIFIED (P-18): engineering floor; the trace',
           '"threshold": 0.01,\n      "threshold_status": "⚠️ TO BE VERIFIED (P-18): engineering floor; the trace',
           OBSERVE, "ob11"),
    Mutant("i3bm23", "every rule names a declared metric", RULES,
           '"metric": "infrx_unsettleable_jobs"', '"metric": "infrx_unsettleable_job"',
           OBSERVE, "ob10"),
    Mutant("i3bm38", "M4: an unmeasured threshold carries the ⚠️ marker", RULES,
           '"threshold_status": "⚠️ TO BE VERIFIED (P-18): engineering guess; one transaction',
           '"threshold_status": "provisional: engineering guess; one transaction',
           OBSERVE, "ob10"),
    Mutant("i3bm24", "every metric family has a dashboard panel", DASHBOARD,
           '        {"title": "GPU utilization", "metric": "infrx_gpu_utilization_ratio", '
           '"by": ["gpu"], "unit": "ratio"},\n', "", OBSERVE, "ob10"),
)


STATE = "apps/infrx-api/infrx/contracts/fakes/state.py"
KIT = "tests/integration/backend/recovery/recoverykit.py"

MUTANTS += (
    # ---------------- the drills' oracle has teeth (I3B.b), on the reference store
    Mutant("i3bm30", "reconcile catches an accepted job left undispatched after recovery",
           KIT, "                await self.scheduler.enqueue(item)", "                pass",
           DRILLS, "rc01"),
    Mutant("i3bm31", "reconcile catches output regenerated after publication "
                     "(a second executable attempt reached the customer)", STATE,
           "            if job.published:\n                # After the publication marker",
           "            if False:\n                # After the publication marker", DRILLS, "rc01"),
    Mutant("i3bm32", "reconcile catches a failed job that keeps its hold", STATE,
           "else SettlementState.released_platform_absorbed)\n            self._release_hold(wallet, hold)",
           "else SettlementState.released_platform_absorbed)\n            pass", DRILLS, "rc05"),
    Mutant("i3bm34", "D1: reconcile catches a settled debit that is not price snapshot x usage "
                     "(a consistent overcharge in ledger and outcome alike)", STATE,
           "                debit = candidate\n", "                debit = candidate + candidate\n",
           DRILLS, "rc01"),
    Mutant("i3bm33", "the index is rebuilt from the durable snapshot of queued jobs", KIT,
           "if job.state is JobState.queued)", "if job.state is JobState.running)",
           DRILLS, "rc06", layer=2),
    # ---------------- the restore procedure (the runbook's tool), on the real PostgreSQL
    Mutant("i3bm40", "a restore empties the template's default privileges first (else anon "
                     "gets ALL on the tenant tables)", PGRESTORE,
           "    if neutralize:\n", "    if False:\n", RESTORE, "bk01_a", layer=2),
    Mutant("i3bm41", "a restore re-creates the project's triggers on auth tables", PGRESTORE,
           '        for definition in meta["auth_triggers"]:\n',
           "        for definition in []:\n", RESTORE, "bk01_a", layer=2),
    Mutant("i3bm42", "a restore carries 0004's global function default", PGRESTORE,
           "        if not public_executes:\n", "        if False:\n",
           RESTORE, "bk01_a", layer=2),
    Mutant("i3bm44", "a damaged backup is refused before anything is restored", PGRESTORE,
           "        if hashlib.sha256((backup / name).read_bytes()).hexdigest() != digest:",
           "        if False:", RESTORE, "bk01d", layer=2),
    Mutant("i3bm46", "RS-1: a restore refuses a non-empty target before any write", PGRESTORE,
           "    if occupied:\n", "    if False:\n", RESTORE, "bk01e_a", layer=2),
    Mutant("i3bm47", "RS-1: a restore refuses the backup's own source", PGRESTORE,
           '    if meta.get("source") == identity(conninfo):\n', "    if False:\n",
           RESTORE, "bk01e_b", layer=2),
    Mutant("i3bm45", "the check compares catalog facts, not only rows", PGRESTORE,
           "    for name, rows in source[\"catalog\"].items():",
           "    for name, rows in {}.items():", RESTORE, "bk01c", layer=2),
    Mutant("i3bm48", "RS-7: the check's session is read-only (A6 reads LIVE hosted)", PGRESTORE,
           '        conn.execute("set default_transaction_read_only = on")\n', "",
           RESTORE, "bk01_a", layer=2),
    # RS-2: each catalog family is compared - one mutant per family drops it from CATALOG,
    # and only that family's bk01f parameter can kill it.
    Mutant("i3bm60", "RS-2: the check compares policies", PGRESTORE,
           '    "policies": "select schemaname', '    "_policies": "select schemaname',
           RESTORE, "bk01f and policies", layer=2),
    Mutant("i3bm61", "RS-2: the check compares functions and their grants", PGRESTORE,
           '    "functions": "select n.nspname', '    "_functions": "select n.nspname',
           RESTORE, "bk01f and functions", layer=2),
    Mutant("i3bm62", "RS-2: the check compares indexes", PGRESTORE,
           '    "indexes": "select schemaname', '    "_indexes": "select schemaname',
           RESTORE, "bk01f and indexes", layer=2),
    Mutant("i3bm63", "RS-2: the check compares default privileges", PGRESTORE,
           '    "default_acls": "select defaclrole', '    "_default_acls": "select defaclrole',
           RESTORE, "bk01f and default_acls", layer=2),
    Mutant("i3bm64", "RS-2: the check compares row CONTENT, not only counts", PGRESTORE,
           "f\"select count(*), md5(coalesce(string_agg(t::text, E'\\\\n' order by t::text), ''))",
           "f\"select count(*), 'md5-ignored'", RESTORE, "bk01f and rows", layer=2),
    Mutant("i3bm65", "RS-3: the check compares whether a trigger is enabled", PGRESTORE,
           ', t.tgenabled::text "', ' "', RESTORE, "bk01f and triggers", layer=2),
    Mutant("i3bm66", "RS-3: the check compares a function's pinned configuration (search_path)",
           PGRESTORE, ", coalesce(p.proconfig::text, '') from pg_proc p", " from pg_proc p",
           RESTORE, "bk01f and functions_config", layer=2),
    Mutant("i3bm43", "the maintenance switch turns off BOTH admission flags", RESTORE,
           "\"('legacy_usd_admission', 'credit_admission')\")", "\"('credit_admission')\")",
           RESTORE, "bk04", layer=2),
)

RUNBOOK_CASES = "tests/integration/backend/recovery/test_runbooks.py"
MUTANTS += (
    # ---------------- the runbooks stay the drilled procedures (I3B.c)
    Mutant("i3bm50", "every alert names a runbook section that exists", RULES,
           '"runbook": "infra/runbooks/disk.md#disk-almost-full"',
           '"runbook": "infra/runbooks/disk.md#disk-almost-ful"', RUNBOOK_CASES, "rb01"),
    Mutant("i3bm51", "restore.md runs the drilled tool's own subcommands",
           "infra/runbooks/restore.md",
           "$PY infra/runbooks/pgrestore.py dump --conninfo",
           "$PY infra/runbooks/pgrestore.py backup --conninfo", RUNBOOK_CASES, "rb02"),
    Mutant("i3bm52", "rollback.md's maintenance switch is bk04's", "infra/runbooks/rollback.md",
           "where name in ('legacy_usd_admission', 'credit_admission');",
           "where name in ('credit_admission');", RUNBOOK_CASES, "rb03"),
    Mutant("i3bm53", "every runbook step block parses as bash", "infra/runbooks/restart.md",
           "for i in $(seq 1 120); do", "for i in $(seq 1 120) do", RUNBOOK_CASES, "rb04"),
    Mutant("i3bm55", "RS-4: a failed client's DETAIL/CONTEXT lines (row data) are not re-raised",
           PGRESTORE, 'if not line.lstrip().startswith(("DETAIL:", "CONTEXT:"))',
           "if line", RUNBOOK_CASES, "rb06"),
    Mutant("i3bm56", "RS-6: rollback.md runs the maintenance switch as service_role, as bk04",
           "infra/runbooks/rollback.md", "set role service_role;\n", "\n",
           RUNBOOK_CASES, "rb03"),
    Mutant("i3bm54", "links between runbooks resolve", "infra/runbooks/rollback.md",
           "[restart.md](restart.md#drain)", "[restart.md](restart.md#draining)",
           RUNBOOK_CASES, "rb05"),
)


def _copy_with_infra(destination: Path, _copy=mutants._copy_trees) -> None:
    """E's owned trees plus `infra/`, so a mutated rule or runbook is the one read."""
    _copy(destination)
    shutil.copytree(harness.REPO_ROOT / "infra", destination / "infra",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def run(selected, *, stack_available: bool) -> dict:
    mutants._copy_trees = _copy_with_infra
    results = []
    for mutant in selected:
        result = mutants.run_one(mutant, stack_available=stack_available)
        print(f"[{result['status']:>13}] {result['id']}  {result['invariant']}", flush=True)
        results.append(result)
    return mutants.summarise(results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--layer", choices=("1", "all"), default="1")
    parser.add_argument("--only", action="append", help="a mutant id; repeatable")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.id}  layer {mutant.layer}  {mutant.path}  -k {mutant.select}\n"
                  f"        {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants")
        return 0
    selected = [m for m in MUTANTS if (args.layer == "all" or m.layer == 1)
                and (not args.only or m.id in args.only)]
    stack = bool(harness.load_state()) and bool(harness.owned_containers())
    summary = run(selected, stack_available=stack)
    printable = {key: value for key, value in summary.items() if key != "results"}
    print(json.dumps(printable, indent=2))
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2, default=str))
    return 1 if summary["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
