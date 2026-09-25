#!/usr/bin/env python3
"""Durable truth as metrics (I8 slice 3): what PostgreSQL says, not what a process counter
says. The gateway's in-flight gauge counts HTTP requests in that process and the Valkey
index is rebuildable; neither sees an accepted async job that is waiting, a hold nobody
settled or a job past its deadline. This does, in ONE read-only transaction per run.

    MONITOR_DATABASE_URL=... python infra/observe/durable.py --out /var/lib/infrx/metrics/durable.prom

The DSN is MONITOR_DATABASE_URL (a read-only login, D10), else DATABASE_URL moved to the
transaction pooler's port 6543 (one client there, never a session slot: pool_budget.py's
"monitor" row). Every statement is transaction-pooler safe (one transaction, SET LOCAL,
no prepared statements). Output is Prometheus text: counts, ages and timestamps only;
every label value is from a closed set in this file - no ids, prompts, keys or URLs.
A failure writes `infrx_durable_up 0` (never silence) and exits 1.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

TRANSACTION_PORT = "6543"
ACTIVE = ("preparing", "queued", "running")
TERMINAL = ("succeeded", "failed", "cancelled", "expired")
HOLD_STATES = ("held", "unknown")
DISPATCH = ("prepare_dispatch", "inference_dispatch")
GC_KINDS = ("outbox", "stream_chunks", "results")
# The causes the platform owns (PlatformFailureRate's list): a customer's cancel is not one.
PLATFORM_CAUSES = ("platform_error", "engine_error", "lost_after_publication",
                   "journal_write_failed", "retries_exhausted")
OUTBOX_RETENTION_S = 7 * 86_400          # jobstore.OUTBOX_RETENTION_S
OVERDUE_GRACE_S = 60                     # the reaper's own cadence, est.
RECENT_S = 900                           # the "recent" window of the terminal counts
STUCK_HOLD_GRACE_S = 600

QUERIES = {
    "jobs": "select state, count(*), coalesce(extract(epoch from now() - min(case state "
            "when 'preparing' then admitted_at when 'queued' then coalesce(queued_at, updated_at) "
            "else updated_at end)), 0) from infrx.jobs where state = any(%(active)s) group by state",
    "overdue": f"select count(*) from infrx.jobs where state = any(%(active)s) "
               f"and deadline_at < now() - interval '{OVERDUE_GRACE_S} seconds'",
    "recent": f"select state, count(*), count(*) filter (where outcome_cause = any(%(platform)s)) "
              f"from infrx.jobs where settled_at > now() - interval '{RECENT_S} seconds' "
              f"group by state",
    "ready": "select kind, count(*), coalesce(extract(epoch from now() - min(available_at)), 0) "
             "from infrx.outbox where kind = any(%(dispatch)s) and acknowledged_at is null "
             "and claimed_at is null and available_at <= now() group by kind",
    "holds": "select state, count(*) from infrx.credit_holds where state = any(%(holds)s) "
             "group by state",
    "unknown_overdue": "select count(*) from infrx.credit_holds where state = 'unknown' "
                       "and reconcile_after < now()",
    "stuck_holds": f"select count(*) from infrx.credit_holds h join infrx.jobs j "
                   f"on j.request_id = h.request_id where h.state = 'held' and "
                   f"(j.state = any(%(terminal)s) or j.deadline_at < now() - "
                   f"interval '{STUCK_HOLD_GRACE_S} seconds')",
    "drift": "select (select count(*) from infrx.wallet_reconciliation where ledger_drift <> 0 "
             "or reserved_drift <> 0) + (select count(*) from infrx.credit_wallet_reconciliation "
             "where ledger_drift <> 0 or reserved_drift <> 0)",
    "gc_outbox": f"select coalesce(extract(epoch from now() - min(acknowledged_at)) - "
                 f"{OUTBOX_RETENTION_S}, 0) from infrx.outbox where acknowledged_at < now() - "
                 f"interval '{OUTBOX_RETENTION_S} seconds'",
    "gc_stream_chunks": "select coalesce(extract(epoch from now() - min(expires_at)), 0) "
                        "from infrx.stream_chunks where expires_at < now()",
    "gc_results": "select coalesce(extract(epoch from now() - min(j.result_expires_at)), 0) "
                  "from infrx.job_results r join infrx.jobs j on j.request_id = r.request_id "
                  "where j.result_expires_at < now()",
}


def dsn_from_env(env=os.environ) -> str:
    explicit = env.get("MONITOR_DATABASE_URL", "").strip()
    if explicit:
        return explicit
    runtime = env.get("DATABASE_URL", "").strip()
    if not runtime:
        raise SystemExit("neither MONITOR_DATABASE_URL nor DATABASE_URL is set")
    from psycopg.conninfo import make_conninfo
    return make_conninfo(runtime, port=TRANSACTION_PORT)


def collect(conn) -> dict[tuple[str, tuple], float]:
    """{(metric, ((label, value), ...)): number}. Every closed label value is emitted, so a
    state that has no row reads 0 rather than disappearing."""
    params = {"active": list(ACTIVE), "terminal": list(TERMINAL), "holds": list(HOLD_STATES),
              "dispatch": list(DISPATCH), "platform": list(PLATFORM_CAUSES)}
    out: dict[tuple[str, tuple], float] = {}
    with conn.transaction():
        conn.execute("set transaction read only")
        conn.execute("set local statement_timeout = '10s'")

        def rows(name):
            return conn.execute(QUERIES[name], params).fetchall()

        def one(name):
            return float(conn.execute(QUERIES[name], params).fetchone()[0] or 0)
        jobs = {state: (n, age) for state, n, age in rows("jobs")}
        for state in ACTIVE:
            n, age = jobs.get(state, (0, 0))
            out[("infrx_durable_jobs", (("state", state),))] = n
            out[("infrx_durable_oldest_seconds", (("state", state),))] = float(age)
        recent = {state: (n, platform) for state, n, platform in rows("recent")}
        for state in TERMINAL:
            out[("infrx_durable_terminal_recent", (("state", state),))] = recent.get(state, (0, 0))[0]
        out[("infrx_durable_platform_failures_recent", ())] = sum(p for _, p in recent.values())
        out[("infrx_durable_overdue_jobs", ())] = one("overdue")
        ready = {kind: (n, age) for kind, n, age in rows("ready")}
        for kind in DISPATCH:
            n, age = ready.get(kind, (0, 0))
            out[("infrx_durable_ready_backlog", (("kind", kind),))] = n
            out[("infrx_durable_ready_backlog_seconds", (("kind", kind),))] = float(age)
        holds = dict(rows("holds"))
        for state in HOLD_STATES:
            out[("infrx_durable_holds", (("state", state),))] = holds.get(state, 0)
        # the existing families the reconcile rules read (alerts.json), from durable truth
        out[("infrx_holds_unknown", ())] = holds.get("unknown", 0)
        out[("infrx_durable_unknown_overdue", ())] = one("unknown_overdue")
        out[("infrx_durable_stuck_holds", ())] = one("stuck_holds")
        drift = one("drift")
        out[("infrx_reconciliation_drift", ())] = drift
        if drift == 0:
            out[("infrx_reconciliation_last_success_timestamp_seconds", ())] = time.time()
        for kind in GC_KINDS:
            out[("infrx_durable_gc_lag_seconds", (("kind", kind),))] = max(0.0, one(f"gc_{kind}"))
    return out


def render(samples: dict[tuple[str, tuple], float], up: bool) -> str:
    lines = [f'infrx_durable_up{{process="durable"}} {1 if up else 0}',
             f'infrx_durable_last_run_timestamp_seconds{{process="durable"}} {time.time():.3f}']
    for (name, labels), value in sorted(samples.items()):
        inside = ",".join(f'{k}="{v}"' for k, v in (("process", "durable"), *labels))
        lines.append(f"{name}{{{inside}}} {float(value)!r}")
    return "\n".join(lines) + "\n"


def write(path: str, text: str) -> None:
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w") as handle:
        handle.write(text)
    os.replace(temporary, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="the textfile the evaluator reads")
    a = ap.parse_args(argv)
    import psycopg
    try:
        with psycopg.connect(dsn_from_env(), prepare_threshold=None, connect_timeout=10) as conn:
            samples = collect(conn)
    except psycopg.Error as failed:
        write(a.out, render({}, up=False))
        print(f"durable: {type(failed).__name__} {failed.sqlstate or ''}", file=sys.stderr)
        return 1
    write(a.out, render(samples, up=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
