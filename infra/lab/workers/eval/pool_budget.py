#!/usr/bin/env python3
"""I5: the Lab workers' pooler budget, separate from the consumer's (I8's pool_budget.py).

    # a box: the consumer env file and the Lab role env files that exist (absent = OFF)
    python infra/lab/workers/eval/pool_budget.py --consumer-env-file /etc/marlin2b-gateway.env \
        --lab-env-dir /etc/infrx-lab
    # a proposed change on a host
    apps/infrx-api/.venv/bin/python infra/lab/workers/eval/pool_budget.py --runtime-port 6543 \
        --enable eval --set eval.LAB_EVAL_CONCURRENCY=8

The consumer's rows and verdicts are I8's own `budget()` (same knobs, same limits); the Lab
adds one row per ENABLED role (`/etc/infrx-lab/<role>.env` exists). `PgLabDataStore` opens
one connection per call and holds none between calls, so a role's peak is its concurrency
plus one (the outbox relay / lease reaper). Three verdicts, exit 1 if any fails:
* `lab`: the Lab's peak fits its own allotment (`--lab-limit`, default 20 transaction-pooler
  clients) - the Lab cannot grow into the consumer's share by raising a concurrency;
* `lab-session`: no Lab role on the session pooler (5432): its 15 slots are the consumer's;
* `transaction`: the consumer's transaction-mode peak plus the Lab's plus the reserved
  headroom fits the pooler's client limit.
The DSN itself, and every other value, is never printed.
"""
from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[4]
I8 = runpy.run_path(str(REPO / "infra" / "runbooks" / "pool_budget.py"))
ROLES = {"datasets": 2, "eval": 4, "checkpoints": 1}     # the entry point's defaults (WR-B-5)
LAB_LIMIT = 20


def concurrency_name(role: str) -> str:
    return f"LAB_{role.upper()}_CONCURRENCY"


def budget(consumer_env: dict[str, str], lab_envs: dict[str, dict[str, str]], units: Path, *,
           runtime_mode: str | None = None, lab_limit: int = LAB_LIMIT,
           headroom: int = I8["HEADROOM"], txn_limit: int = I8["TXN_CLIENT_LIMIT"]) -> dict:
    """`lab_envs`: role -> its env file, for the enabled roles only. Pure."""
    consumer = I8["budget"](consumer_env, units, runtime_mode=runtime_mode, headroom=headroom,
                            txn_limit=txn_limit)
    rows = []
    for role, env in sorted(lab_envs.items()):
        if role not in ROLES:
            raise ValueError(f"unknown Lab role {role!r}")
        n = int(env.get(concurrency_name(role), ROLES[role]))
        if n < 1:
            raise ValueError(f"{concurrency_name(role)} must be at least 1")
        rows.append({"client": f"lab {role} worker", "processes": 1, "peak": n + 1,
                     "mode": I8["mode_of"](env.get("LAB_DATABASE_URL", "")),
                     "note": f"{concurrency_name(role)} = {n}, plus the relay/reaper"})
    lab_peak = sum(row["peak"] for row in rows)
    on_session = [row["client"] for row in rows if row["mode"] != "transaction"]
    txn_peak = consumer["verdicts"]["transaction"]["peak"] + lab_peak
    verdicts = {
        "lab": {"peak": lab_peak, "limit": lab_limit, "ok": lab_peak <= lab_limit},
        "lab-session": {"roles": on_session, "ok": not on_session},
        "transaction": {"peak": txn_peak, "headroom": headroom, "limit": txn_limit,
                        "ok": txn_peak + headroom <= txn_limit},
    }
    warnings = [f"Lab transactions share the pooler's {I8['SESSION_LIMIT']} server connections "
                f"with the consumer: at full Lab load ({lab_peak}) consumer transactions can "
                f"queue behind them (staging measurement owed, RUNBOOK.md)"] if lab_peak else []
    return {"consumer": consumer, "lab": rows, "verdicts": verdicts, "warnings": warnings,
            "ok": all(v["ok"] for v in verdicts.values())}


def render(result: dict) -> str:
    lines = [I8["render"](result["consumer"]), ""]
    lines += [f"{row['client']:24} {row['mode']:11} {row['peak']:>5}  {row['note']}"
              for row in result["lab"]] or ["no Lab role enabled"]
    for name, v in result["verdicts"].items():
        detail = (f"roles not on the transaction pooler: {v['roles']}" if name == "lab-session"
                  else f"peak {v['peak']}{' + headroom ' + str(v['headroom']) if 'headroom' in v else ''}"
                       f" {'<=' if v['ok'] else '>'} limit {v['limit']}")
        lines.append(f"{'PASS' if v['ok'] else 'FAIL'} {name}: {detail}")
    lines += [f"WARN {w}" for w in result["warnings"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--consumer-env-file", help="the consumer's deployed env file")
    ap.add_argument("--lab-env-dir", help="the directory of <role>.env files (absent = OFF)")
    ap.add_argument("--enable", action="append", default=[], choices=sorted(ROLES),
                    help="a role proposed ON with its defaults (no env file needed)")
    ap.add_argument("--set", action="append", default=[], metavar="ROLE.NAME=VALUE",
                    help="override a Lab role's knob (enables that role)")
    ap.add_argument("--units", default=str(REPO / "apps" / "infrx-api" / "deploy"))
    ap.add_argument("--runtime-port", type=int, help="the consumer DSN's port, when no env file")
    ap.add_argument("--lab-limit", type=int, default=LAB_LIMIT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    consumer = I8["read_env"](Path(a.consumer_env_file).read_text()) \
        if a.consumer_env_file else {}
    labs: dict[str, dict[str, str]] = {}
    if a.lab_env_dir:
        for role in ROLES:
            path = Path(a.lab_env_dir) / f"{role}.env"
            if path.is_file():
                labs[role] = I8["read_env"](path.read_text())
    proposed = "postgresql://lab@127.0.0.1:6543/postgres"      # names the port only
    for role in a.enable:
        labs.setdefault(role, {"LAB_DATABASE_URL": proposed})
    for pair in a.set:
        target, _, value = pair.partition("=")
        role, _, name = target.partition(".")
        labs.setdefault(role, {"LAB_DATABASE_URL": proposed})[name] = value
    runtime = None
    if a.runtime_port is not None:
        runtime = {I8["SESSION_PORT"]: "session", I8["TRANSACTION_PORT"]: "transaction"}.get(
            a.runtime_port, "direct")
    result = budget(consumer, labs, Path(a.units), runtime_mode=runtime,
                    lab_limit=a.lab_limit)
    print(json.dumps(result, indent=1) if a.json else render(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
