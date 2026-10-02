"""AP-08 (api-judge-2): the judge loop end to end through the REAL process
`python -m infrx.lab.workers judge` on ap8 - a run queued through the session doors (as
`POST /lab/v1/judge/runs` queues it) -> the role's START pass freezes the sample, reserves and
sends ONE batch to the local judge fake (ap8's 57564) -> SIGTERM, restart: nothing is sent
again -> the COLLECT pass stores the results and settles -> the CALIBRATION pass grades them
against the operator's gold set (`JUDGE_GOLD_SET`) and publishes `insufficient` (below
MIN_PAIRS reference labels) -> the Lab door shows it. Consumer CREDIT never moves.

Declared fakes (isolated mode, verification.md): the trace store (ap7's ClickHouse + S3) and
AP-07's eligible read are in memory (`cli_site/sitecustomize.py`), the judge is the local fake
priced at J2's TEST rates. It is NOT a live judge pass (P-10 BLOCKED).

    INFRX_D_TASK=ap8 uv run --frozen pytest -q tests/ap08/test_judge_cli_pg.py
"""
from __future__ import annotations

import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from infrx.contracts.tasklocal import local_services
from infrx.judge.rubric import MARLIN_VIDEO_V1
from infrx.lab.judge_api.service import JudgeApi
from tests.d import pgharness
from tests.d import test_d6j_judge as j
from tests.d import test_l2sql_access as l2
from tests.j import fakes as j1
from tests.j.submit import fakes
from tests.j.submit.judge_fake import JudgeFake

from .conftest import pg_reason
from .test_judge_doors_pg import doors, run

pytestmark = pytest.mark.skipif(pg_reason() is not None, reason=f"{pg_reason()}")

API_DIR = pathlib.Path(__file__).resolve().parents[2]
SITE = pathlib.Path(__file__).with_name("cli_site")
FAKE_PORT = local_services("ap8")["judge-fake"].host_port
NEMO, CONFIG, RUN = j.NEMO, "0c000000-0000-4000-8000-0000000004c1", \
    "7a000000-0000-4000-8000-0000000004a1"
TRACES = tuple(fakes.rid(n) for n in (21, 22, 23))
WAIT_S = 60.0


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def answer(sample_id: str, media: bool) -> list[str]:
    """The judge fake's output for one sample: a valid v1 result (no media: limited)."""
    payload = {c.name: {"score": 5, "rationale": "fine"}
               for c in MARLIN_VIDEO_V1.criteria_for(media=media)}
    payload.update(overall_pass=media, notes="")
    return [sample_id, json.dumps(payload)]


class Role:
    """One `python -m infrx.lab.workers judge` process on ap8's database and judge fake."""

    def __init__(self, env: dict[str, str]) -> None:
        self.port = int(env["LAB_WORKER_HEALTH_PORT"])
        self.proc = subprocess.Popen([sys.executable, "-m", "infrx.lab.workers", "judge"],
                                     cwd=API_DIR, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT)

    def ready(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/readyz", timeout=2) as r:
                return r.status == 200
        except OSError:
            return False

    def stop(self) -> tuple[int, str]:
        self.proc.send_signal(signal.SIGTERM)
        out, _ = self.proc.communicate(timeout=30)
        return self.proc.returncode, out.decode(errors="replace")


def until(what: str, probe, role: Role) -> None:
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if probe():
            return
        if role.proc.poll() is not None:
            break
        time.sleep(0.2)
    code, out = role.stop() if role.proc.poll() is None else (role.proc.returncode,
                                                             role.proc.stdout.read().decode())
    raise AssertionError(f"{what} never happened (exit {code}):\n{out[-4000:]}")


def test_ap08_cli__queued_to_calibrated_through_the_judge_role_process(pg, tmp_path) -> None:
    """Failure oracle: no start job in the role (the run stays queued), a second batch after
    the restart, a run never settled, results graded with nothing, a calibration absent or
    `calibrated` on one reference pair, consumer CREDIT moved."""
    conn = pg
    grantor = l2.org(conn, j.C1)
    credit = conn.execute("select sum(ledger_total), sum(reserved_total) "
                          "from infrx.credit_wallets").fetchone()
    d = doors(conn)
    run(d.call(l2.DEV, "lab_judge_configure_keyed", NEMO, CONFIG, grantor, l2.MODEL,
               j1.JUDGE_MODEL, 1, 2))
    run(d.call(l2.DEV, "lab_judge_request_run", NEMO, RUN, CONFIG, j.PAYER))
    world = tmp_path / "world.json"
    world.write_text(json.dumps({"grantor": grantor, "model": l2.MODEL, "traces": [
        {"request_id": t, "video": n == 0} for n, t in enumerate(TRACES)]}))
    gold = tmp_path / "gold.json"
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
           "PYTHONPATH": f"{SITE}{os.pathsep}{API_DIR}", "AP08_WORLD": str(world),
           "LAB_DATABASE_URL": pgharness.dsn(conn.info.dbname),
           "LAB_WORKER_HEALTH_PORT": str(free_port()),
           "JUDGE_PROVIDER_URL": f"http://127.0.0.1:{FAKE_PORT}",
           "CLICKHOUSE_URL": "http://127.0.0.1:9/ap8-fake", "S3_TRACE_BUCKET": "ap8-fake",
           "JUDGE_MODE": "live", "JUDGE_LIVE_BUDGET_USD": "1", "JUDGE_GOLD_SET": str(gold)}

    def state() -> str | None:
        row = conn.execute("select state from infrx.lab_judge_runs where run_id = %s",
                           (RUN,)).fetchone()
        return row and row[0]

    fake = JudgeFake(FAKE_PORT)
    try:
        # the gold set the operator reviewed: the two frozen samples (known once sent)
        gold.write_text(json.dumps({"provider_org_id": NEMO, "org_id": grantor,
                                    "judge_model": j1.JUDGE_MODEL, "rubric_version": 1,
                                    "reviewed_by": "operator@infrx.test",
                                    "review_ref": "ap8 gold set", "labels": [
                                        {"sample_id": t, "verdict": "correct"}
                                        for t in TRACES]}))
        first = Role(env)
        until("the role answering /readyz", first.ready, first)
        until("the start pass sending the run", lambda: state() == "submitted", first)
        assert first.stop()[0] == 0
        assert len(fake.posts) == 1, "one batch"
        sent = [i["sample_id"] for i in fake.posts[0]["items"]]
        assert len(sent) == 2 and set(sent) <= set(TRACES)
        batch = fake.batches[fake.posts[0]["submit_key"]]
        fake.outputs[batch] = [answer(s, s == TRACES[0]) for s in sent]

        second = Role(env)
        until("the collect pass settling the run", lambda: state() == "completed", second)
        until("the calibration pass publishing", lambda: conn.execute(
            "select count(*) from infrx.lab_judge_calibrations").fetchone()[0] > 0, second)
        code, out = second.stop()
        assert code == 0, out[-2000:]
        assert len(fake.posts) == 1, "the restart sent the batch again"
    finally:
        fake.close()
    stored = conn.execute("select count(*), bool_and(accepted) from infrx.lab_judge_results "
                          "where run_id = %s", (RUN,)).fetchone()
    assert stored == (2, True)
    shown = run(JudgeApi(doors(conn)).calibration(l2.DEV, NEMO, CONFIG))
    assert TRACES[0] in sent, "the run's frozen sample holds the one trace with video"
    assert (shown.state, shown.labels) == ("insufficient", 1), "limited results never pair"
    assert conn.execute("select sum(ledger_total), sum(reserved_total) "
                        "from infrx.credit_wallets").fetchone() == credit
