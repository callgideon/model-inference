"""LAB-E2E's gate half: a Lab gate runner's UI cell (E5L o10, E6L j10, E7L i08's provider UI,
E8L k10's UI) runs its e2e suite (`apps/lab/tests/e2e/<suite>/stack.test.ts`) as a subprocess on
the l4 key and reads back the exit code, node's counts and the suite's own record
(`LAB_E2E_OUT`: which ports the gateway's own composition carries, which stand-ins it ran over).
Loaded by path (R213) from each gate's scenario module; nothing here imports a gate's helpers.

In a gate's mutant copy the suite's backend runs on this interpreter and imports the package
this process imports (the copy's, mutated), and `INFRX_LAB_DIR` names the checkout's apps/lab
(the copy has none).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

LAB = Path(os.environ.get("INFRX_LAB_DIR") or Path(__file__).resolve().parents[2])
TIMEOUT_S = 1200


def command(suite: str) -> str:
    return (f"cd apps/lab && LAB_E2E_REAL=1 INFRX_D_TASK=l4 node --test "
            f"tests/e2e/{suite}/stack.test.ts")


def run(suite: str, out: Path) -> dict:
    """The suite once: {command, exit, pass, fail, skipped, cancelled, record, tail}."""
    import infrx
    out = Path(out) / f"e2e-{suite}"
    out.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "LAB_E2E_BUILT"}
    env.update(LAB_E2E_REAL="1", INFRX_D_TASK="l4", LAB_E2E_OUT=str(out),
               INFRX_API_DIR=str(Path(infrx.__file__).resolve().parents[1]),
               INFRX_PYTHON=sys.executable, PATH=env.get("PATH", "") + ":/usr/bin:/bin")
    try:
        done = subprocess.run(["node", "--test", f"tests/e2e/{suite}/stack.test.ts"], cwd=LAB,
                              env=env, capture_output=True, text=True, timeout=TIMEOUT_S)
        code, stdout = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as late:
        code, stdout = 124, f"timed out after {TIMEOUT_S}s: {late.stdout or ''}"
    counts = {k: int(v) for k, v in
              re.findall(r"^# (pass|fail|skipped|cancelled) (\d+)$", stdout, re.M)}
    found = out / f"{suite}.json"
    record = json.loads(found.read_text()) if found.exists() else None
    (out / "stack.log").write_text(stdout)
    return {"command": command(suite), "exit": code, **counts, "record": record,
            "tail": stdout[-2000:]}


def missing(got: dict) -> list[str]:
    """Asserts the suite passed as a whole - every case ran and none failed - and answers the
    ports the gateway's own composition does not carry yet (empty: the cell is bound for real)."""
    assert (got["exit"], got.get("fail"), got.get("cancelled", 0), got.get("skipped")) == \
        (0, 0, 0, 0) and got.get("pass", 0) > 1 and got["record"] is not None, \
        f"the e2e suite did not pass: {({k: v for k, v in got.items() if k != 'tail'})}\n{got['tail']}"
    return sorted(port for port, carried in got["record"].get("composed", {}).items()
                  if not carried)
