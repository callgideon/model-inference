#!/usr/bin/env python3
"""LAB-HOSTING (AP-05): the private-deployment gate on ap5 - PostgreSQL 57556 (every migration
through 0062), the candidate engine (`fake_vllm.py`'s app) on 57557 as a real process, the
hosting controller as real processes, the routes over ASGI.

    apps/infrx-api/.venv/bin/python tests/integration/lab_hosting/runner.py [--out DIR] [-k EXPR]

Runs `scenarios.py` (never collected by `pytest tests/integration`) under INFRX_D_TASK=ap5
and writes `<out>/verdict.json`: per scenario PASS / FAIL / BLOCKED / INVALID / NOT RUN (the
classification is lab_rollout's, `case_status`), the gate the worst of them; exit
0 / 1 / 3 / 3 / 4. A run that cannot take `/tmp/infrx-ap5.runner.lock` is BLOCKED.
Label: isolated - no GPU, no real model, a declared engine image, no gateway private
admission path; the real candidate smoke is a coordinator window (infra/lab/hosting).
"""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LOCK = Path("/tmp") / "infrx-ap5.runner.lock"
SCENARIOS = {
    "g01": "the private lifecycle through the API: deploy, identity, smoke, ready, retire",
    "g02": "one slot, one candidate: a second deployment is capacity_unavailable",
    "g03": "a controller process killed mid-create is replaced; its engine adopted, once",
    "g04": "a wrong runtime never becomes ready; its resources are torn down",
}
LABEL = ("isolated: ap5 PostgreSQL, fake vLLM as a real process, controller processes; no GPU, "
         "no real model, declared engine image, no gateway private admission path")


def _rollout():
    """lab_rollout's runner (its JUnit case classification), loaded under its own name."""
    spec = importlib.util.spec_from_file_location(
        "lab_hosting_rollout_runner", HERE.parent / "lab_rollout" / "runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("-k", dest="keyword", default=None)
    args = parser.parse_args(argv)
    r = _rollout()
    started = datetime.now(timezone.utc)
    out = args.out or Path(os.environ.get("TMPDIR", "/tmp")) / \
        f"infrx-ap5-hosting-{started:%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    result = {gid: {"title": title, "status": r.NOT_RUN, "reasons": []}
              for gid, title in SCENARIOS.items()}
    lock = LOCK.open("a")
    began = time.monotonic()
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        for entry in result.values():
            entry.update(status=r.BLOCKED, reasons=[f"BLOCKED[lock] {LOCK} is held"])
    else:
        junit = out / "scenarios.xml"
        argv_ = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfEs",
                 "--import-mode=prepend", "-o", "junit_family=xunit1",
                 f"--junitxml={junit}", str(HERE / "scenarios.py"),
                 *(["-k", args.keyword] if args.keyword else [])]
        with (out / "scenarios.log").open("w") as sink:
            subprocess.run(argv_, cwd=REPO, stdout=sink, stderr=subprocess.STDOUT,
                           env={**os.environ, "INFRX_D_TASK": "ap5"}, timeout=3600)
        if junit.exists():
            for case in ET.fromstring(junit.read_text()).iter("testcase"):
                gid = case.get("name", "")[5:8]
                if gid in result:
                    status, reason = r.case_status(case)
                    result[gid]["status"] = status
                    result[gid]["reasons"] += [reason] if reason else []
    finally:
        lock.close()
    verdict = r.worst(entry["status"] for entry in result.values())
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                          text=True).stdout.strip()
    (out / "verdict.json").write_text(json.dumps({
        "task": "AP-05", "gate": "LAB-HOSTING", "verdict": verdict, "exit": r.EXIT[verdict],
        "label": LABEL, "head": head, "key": "ap5", "ports": {"postgres": 57556, "engine": 57557},
        "started": started.isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - began, 1),
        "scenarios": result, "evidence": {"junit": str(out / "scenarios.xml"),
                                          "log": str(out / "scenarios.log")},
        "reproduce": "apps/infrx-api/.venv/bin/python tests/integration/lab_hosting/runner.py "
                     "--out <dir>"}, indent=2))
    for gid, entry in result.items():
        print(f"{entry['status']:>8}  {gid}  {entry['title']}")
    print(f"gate {verdict} -> {out / 'verdict.json'}")
    return r.EXIT[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
