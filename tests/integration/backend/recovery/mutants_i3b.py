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
    # ---------------- wiring helpers
    Mutant("i3bm16", "a reaped inference lease is a requeue, a reaped preparation a redispatch",
           METRICS, 'action="prepare_redispatched" if preparing else "requeued")',
           'action="requeued")', OBSERVE, "ob09"),
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
    Mutant("i3bm24", "every metric family has a dashboard panel", DASHBOARD,
           '        {"title": "GPU utilization", "metric": "infrx_gpu_utilization_ratio", '
           '"by": ["gpu"], "unit": "ratio"},\n', "", OBSERVE, "ob10"),
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
