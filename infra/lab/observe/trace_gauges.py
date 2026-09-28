#!/usr/bin/env python3
"""I2L-OBS (WR-T-5): T3's trace loss, lag and retention gauges as a Prometheus textfile.

    python infra/lab/observe/trace_gauges.py --out /var/lib/infrx/metrics/lab-traces.prom

Runs in the release image (infrx-lab-trace-gauges.service, every minute), read-only: T3's
`retention.gauges` over ClickHouse (`CLICKHOUSE_URL`), the feedback outbox lag over PostgreSQL
(`LAB_DATABASE_URL`, optional) and the size of the worker's sealed spool segments
(`TRACE_SPOOL_DIR`, mounted read-only). The alarms over them are `alerts.json` (T3's RULES).
A failure writes `infrx_trace_gauges_up 0` (never silence), prints only the error's type and
exits 1: no DSN, key or row content is ever printed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


async def collect(retention, *, outbox=None, spool_dir: Path | None = None) -> dict[str, float]:
    from infrx.traces.retention.policy import gauges
    from infrx.traces.spool import segment_names
    values = await gauges(retention, outbox=outbox)
    if spool_dir is not None:
        values["infrx_trace_spool_bytes"] = sum(
            (spool_dir / name).stat().st_size for name in segment_names(spool_dir))
    return values


def write(out: Path, values: dict[str, float] | None) -> int:
    """Atomically: the gauges and `up 1`, or `up 0` alone."""
    lines = [f"{name} {value:g}" for name, value in sorted((values or {}).items())]
    lines.append(f"infrx_trace_gauges_up {1 if values is not None else 0}")
    part = out.with_name(out.name + ".part")
    part.write_text("\n".join(lines) + "\n")
    part.replace(out)
    return 0 if values is not None else 1


def run(out: Path, build) -> int:
    """`build()` -> (retention, outbox or None, spool dir or None)."""
    try:
        retention, outbox, spool_dir = build()
        values = asyncio.run(collect(retention, outbox=outbox, spool_dir=spool_dir))
    except Exception as failure:                  # noqa: BLE001 - reported as up 0
        print(f"trace gauges failed: {type(failure).__name__}", file=sys.stderr)
        return write(out, None)
    return write(out, values)


def from_env(env=os.environ):
    import clickhouse_connect

    from infrx.state.jobstore import connector
    from infrx.traces.feedback import ClickHouseFeedbackProjection
    from infrx.traces.feedback.pg import PgFeedbackOutbox
    from infrx.traces.retention import ClickHouseRetentionStore, Retention
    from infrx.traces.ship import ClickHouseProjection
    client = clickhouse_connect.get_client(dsn=env["CLICKHOUSE_URL"])
    retention = Retention(ClickHouseRetentionStore(client), ClickHouseProjection(client),
                          ClickHouseFeedbackProjection(client), None)
    dsn, spool = env.get("LAB_DATABASE_URL", ""), env.get("TRACE_SPOOL_DIR", "")
    return (retention, PgFeedbackOutbox(connector(dsn)) if dsn else None,
            Path(spool) if spool else None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    return run(parser.parse_args(argv).out, from_env)


if __name__ == "__main__":
    sys.exit(main())
