#!/usr/bin/env python3
"""The hosted pooler's connection budget, computed from the deployed knobs (I8 slice 1).

    # box (step 71-pool-budget.sh): the installed env file on stdin, in the runtime image
    python infra/runbooks/pool_budget.py --env-file /dev/stdin --units apps/infrx-api/deploy
    # coordinator host: a proposed change, no env file needed
    apps/infrx-api/.venv/bin/python infra/runbooks/pool_budget.py --runtime-port 5432 \
        --set DATABASE_POOL_MAX_SIZE=6

Prints one table row per client of the pooler and one verdict line per pooler mode, and
exits 1 when any mode's peak (plus the reserved headroom) exceeds its limit - the
configuration that produced `EMAXCONNSESSION` on 2026-09-24 fails here before it ships.
Values come from where the runtime reads them: `DATABASE_POOL_*` through the runtime's own
`infrx.config.deployment_from_env` (so defaults and parsing are the runtime's), the gateway's
process count from `--workers` in the shipped gateway unit, and the runtime's pooler mode from
the PORT of `DATABASE_URL` (5432 session, 6543 transaction; anything else is direct). The
DSN itself, and every other value, is never printed.

Limits (research/plan/evidence/i/I8-*.md cites each):
* session mode (5432): 15 client slots - meas. 2026-09-24 on this project, Supavisor's
  refusal text "max clients reached in session mode - pool_size 15";
* transaction mode (6543): `--txn-client-limit`, default 200 = Supabase's documented "Max
  pooler clients" for Nano/Micro compute (supabase.com/docs/guides/platform/compute-and-disk).
  The project's tier is ⚠️ TO BE VERIFIED (est. Nano/Micro from pool_size 15; the coordinator
  reads `max_client_conn` with infra/runbooks/supabase_policy.py). Server connections behind
  it are the same pool_size 15: concurrent transactions above that queue in the pooler.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
SESSION_PORT, TRANSACTION_PORT = 5432, 6543
SESSION_LIMIT = 15            # meas. 2026-09-24, EMAXCONNSESSION "pool_size 15"
TXN_CLIENT_LIMIT = 200        # documented Nano/Micro "Max pooler clients"; ⚠️ tier unverified
HEADROOM = 2                  # a dead client's slot until Supavisor notices, plus one spare


def _runtime_config():
    try:
        from infrx import config
    except ImportError:
        sys.path.insert(0, str(REPO / "apps" / "infrx-api"))
        from infrx import config
    return config


def read_env(text: str) -> dict[str, str]:
    """The env file preflight writes: NAME=VALUE lines, `#` comments."""
    env = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            env[name] = value
    return env


def mode_of(dsn: str) -> str:
    """`session`, `transaction` or `direct`, from the port only (a libpq URL or key=value)."""
    if not dsn.strip():
        return "none"
    if "://" in dsn:
        try:
            port = urlsplit(dsn).port or SESSION_PORT
        except ValueError:
            return "direct"
    else:
        found = re.search(r"(?:^|\s)port\s*=\s*(\d+)", dsn)
        port = int(found.group(1)) if found else SESSION_PORT
    return {SESSION_PORT: "session", TRANSACTION_PORT: "transaction"}.get(port, "direct")


def gateway_processes(units: Path) -> int:
    """uvicorn `--workers N` in the shipped gateway unit (1 when absent): one pool each."""
    text = (units / "marlin2b-gateway.service").read_text()
    found = re.search(r"--workers\s+(\d+)", text)
    return int(found.group(1)) if found else 1


def budget(env: dict[str, str], units: Path, *, runtime_mode: str | None = None,
           monitor_mode: str = "transaction", operator_mode: str = "transaction",
           collector: int = 0, headroom: int = HEADROOM,
           session_limit: int = SESSION_LIMIT, txn_limit: int = TXN_CLIENT_LIMIT) -> dict:
    """The budget table and a verdict per pooler mode. Pure: reads `units`, nothing else."""
    config = _runtime_config()
    deployment = config.deployment_from_env(env)
    limits = config.pilot_from_env(env) if hasattr(config, "pilot_from_env") else None
    mode = runtime_mode or mode_of(env.get("DATABASE_URL", ""))
    gateways = gateway_processes(units)
    pool_min, pool_max = deployment.database_pool_min_size, deployment.database_pool_max_size
    rows = [
        # name, mode, processes, per process at startup, per process at peak, note
        ("gateway pool", mode, gateways, pool_min, pool_max,
         "DATABASE_POOL_MIN/MAX_SIZE; uvicorn --workers in marlin2b-gateway.service"),
        ("gateway startup probe", mode, gateways, 1, 0,
         "pilot.connection_pool connect() before lifespan opens the pool; closed after"),
        ("worker pool (inference + preparation + reaper)", mode, 1, pool_min, pool_max,
         "python -m infrx.worker: one pool shared by every runner"),
        ("collector (M6)", mode, 1 if collector else 0, collector, collector,
         "0 until M6 ships a process of its own; inside the worker it shares that pool"),
        ("operator CLI on the box (python -m infrx.operations.cli)", mode, 1, 0, 1,
         "one fresh connection per operation, DATABASE_URL"),
        ("monitor: infra/observe/durable.py", monitor_mode, 1, 0, 1,
         "one read-only transaction per scrape (MONITOR_DATABASE_URL)"),
        ("operator reads: drift.py, certify runner ledger half", operator_mode, 2, 0, 1,
         "6543 since 2026-09-24 (session slots were exhausted)"),
        ("console (apps/app)", "none", 0, 0, 0,
         "PostgREST over HTTPS (supabase-js, no pg driver): no pooler slot"),
        ("migrations / pgrestore dump", "session", 1, 0, 0,
         "maintenance only, runtime stopped: never concurrent with the rows above"),
    ]
    table = [{"client": name, "mode": m, "processes": n, "startup": n * start,
              "peak": n * peak, "note": note}
             for name, m, n, start, peak, note in rows]
    # Startup: the gateway's probe and pool minimum while the worker (restarted by the
    # engine's PartOf=, or not) may already hold its peak; steady: every pool at max.
    verdicts = {}
    for pooler, limit in (("session", session_limit), ("transaction", txn_limit)):
        mine = [row for row in table if row["mode"] == pooler]
        startup = sum(row["startup"] if row["client"].startswith("gateway") else row["peak"]
                      for row in mine)
        steady = sum(row["peak"] for row in mine)
        peak = max(startup, steady)
        verdicts[pooler] = {"peak": peak, "headroom": headroom, "limit": limit,
                            "ok": peak + headroom <= limit}
    warnings = []
    if limits is not None:
        demand = limits.worker_concurrency + limits.preparation_concurrency + 1
        if pool_max < demand:
            warnings.append(
                f"worker pool max {pool_max} < WORKER_CONCURRENCY + PREPARATION_CONCURRENCY + "
                f"reaper = {demand}: runners wait up to DATABASE_POOL_CONNECT_TIMEOUT_S "
                f"({deployment.database_pool_connect_timeout_s} s) for a connection at full "
                f"load - watch infrx_db_pool_requests_waiting / infrx_db_pool_timeouts_total")
    if mode == "transaction":
        warnings.append("the runtime on 6543 needs WR-I8-1 (no session SET, no server-side "
                        "prepared statements): tests/i/test_pooler.py")
    if verdicts["transaction"]["peak"] > session_limit:
        warnings.append(f"transaction-mode clients can run {verdicts['transaction']['peak']} "
                        f"transactions at once; the pooler serves {session_limit} and queues "
                        f"the rest")
    return {"runtime_mode": mode, "gateway_processes": gateways,
            "database_pool_min_size": pool_min, "database_pool_max_size": pool_max,
            "table": table, "verdicts": verdicts, "warnings": warnings,
            "ok": all(v["ok"] for v in verdicts.values())}


def render(result: dict) -> str:
    lines = [f"{'client':56} {'mode':11} {'proc':>4} {'start':>5} {'peak':>5}  note"]
    for row in result["table"]:
        lines.append(f"{row['client']:56} {row['mode']:11} {row['processes']:>4} "
                     f"{row['startup']:>5} {row['peak']:>5}  {row['note']}")
    for pooler, v in result["verdicts"].items():
        lines.append(f"{'PASS' if v['ok'] else 'FAIL'} {pooler}: peak {v['peak']} + headroom "
                     f"{v['headroom']} {'<=' if v['ok'] else '>'} limit {v['limit']}")
    lines += [f"WARN {w}" for w in result["warnings"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", help="the deployed env file (or /dev/stdin); values unread "
                                       "except DATABASE_POOL_*, concurrency and the DSN's port")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="override a knob (a proposed change)")
    ap.add_argument("--units", default=str(REPO / "apps" / "infrx-api" / "deploy"),
                    help="directory holding marlin2b-gateway.service")
    ap.add_argument("--runtime-port", type=int, help="the runtime DSN's port, when no env file")
    ap.add_argument("--collector", type=int, default=0, help="connections of an M6 process")
    ap.add_argument("--headroom", type=int, default=HEADROOM)
    ap.add_argument("--session-limit", type=int, default=SESSION_LIMIT)
    ap.add_argument("--txn-client-limit", type=int, default=TXN_CLIENT_LIMIT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    env = read_env(Path(a.env_file).read_text()) if a.env_file else {}
    for pair in a.set:
        name, _, value = pair.partition("=")
        env[name] = value
    runtime = None
    if a.runtime_port is not None:
        runtime = {SESSION_PORT: "session", TRANSACTION_PORT: "transaction"}.get(
            a.runtime_port, "direct")
    result = budget(env, Path(a.units), runtime_mode=runtime, collector=a.collector,
                    headroom=a.headroom, session_limit=a.session_limit,
                    txn_limit=a.txn_client_limit)
    print(json.dumps(result, indent=1) if a.json else render(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
