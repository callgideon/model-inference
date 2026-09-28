#!/usr/bin/env python3
"""I5: the Lab workers' pooler budget is separate from the consumer's and never eats into it.

    uv run --frozen pytest -q tests/i/lab_eval/test_pool_budget.py
"""
from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[3]
REPO = API.parents[1]
UNITS = API / "deploy"
PB = runpy.run_path(str(REPO / "infra" / "lab" / "workers" / "eval" / "pool_budget.py"))
I8 = runpy.run_path(str(REPO / "infra" / "runbooks" / "pool_budget.py"))
TXN = "postgresql://lab:MARKER-SECRET@127.0.0.1:6543/postgres"
SESSION = "postgresql://lab:MARKER-SECRET@127.0.0.1:5432/postgres"
CONSUMER = {"DATABASE_URL": "postgresql://gw:MARKER-SECRET@127.0.0.1:6543/postgres"}


def test_i5_the_consumer_budget_is_i8s_and_each_enabled_role_adds_its_concurrency_plus_one():
    """Failure oracle: consumer rows computed differently from I8's (two budgets that
    disagree), a role counted without its relay/reaper connection, a disabled role counted,
    or the Lab left out of the pooler's transaction total."""
    labs = {"eval": {"LAB_DATABASE_URL": TXN},
            "checkpoints": {"LAB_DATABASE_URL": TXN, "LAB_CHECKPOINTS_CONCURRENCY": "3"}}
    r = PB["budget"](CONSUMER, labs, UNITS)
    assert r["consumer"] == I8["budget"](CONSUMER, UNITS)
    assert {row["client"]: row["peak"] for row in r["lab"]} == {
        "lab checkpoints worker": 4, "lab eval worker": 5}
    consumer_peak = r["consumer"]["verdicts"]["transaction"]["peak"]
    assert r["verdicts"]["lab"] == {"peak": 9, "limit": 20, "ok": True}
    assert r["verdicts"]["transaction"]["peak"] == consumer_peak + 9 and r["ok"]
    assert r["warnings"] and "server connections" in r["warnings"][0]
    off = PB["budget"](CONSUMER, {}, UNITS)
    assert off["lab"] == [] and off["verdicts"]["transaction"]["peak"] == consumer_peak
    assert off["warnings"] == [] and off["ok"]


def test_i5_the_lab_cannot_take_the_consumers_share():
    """Failure oracle: a Lab concurrency raised past its allotment passing, a Lab role on the
    session pooler (the consumer's 15 slots) passing, the transaction limit judged without
    the headroom or off by one, a zero or unknown role accepted."""
    big = PB["budget"](CONSUMER, {"eval": {"LAB_DATABASE_URL": TXN,
                                           "LAB_EVAL_CONCURRENCY": "20"}}, UNITS)
    assert big["verdicts"]["lab"] == {"peak": 21, "limit": 20, "ok": False} and not big["ok"]
    edge = PB["budget"](CONSUMER, {"eval": {"LAB_DATABASE_URL": TXN,
                                            "LAB_EVAL_CONCURRENCY": "19"}}, UNITS)
    assert edge["verdicts"]["lab"]["ok"]
    session = PB["budget"](CONSUMER, {"datasets": {"LAB_DATABASE_URL": SESSION}}, UNITS)
    assert session["verdicts"]["lab-session"] == {"roles": ["lab datasets worker"], "ok": False}
    labs = {"eval": {"LAB_DATABASE_URL": TXN}}
    need = PB["budget"](CONSUMER, labs, UNITS)["verdicts"]["transaction"]["peak"] + 2
    assert PB["budget"](CONSUMER, labs, UNITS, txn_limit=need)["ok"]
    tight = PB["budget"](CONSUMER, labs, UNITS, txn_limit=need - 1)
    assert not tight["verdicts"]["transaction"]["ok"] and not tight["ok"]
    for bad in ({"eval": {"LAB_EVAL_CONCURRENCY": "0"}}, {"judge": {}}):
        with pytest.raises(ValueError):
            PB["budget"](CONSUMER, bad, UNITS)


def test_i5_the_cli_reads_only_the_enabled_roles_env_files_and_never_prints_a_dsn(
        tmp_path, capsys):
    """Failure oracle: a role without its env file counted as enabled, the exit code not
    following the verdicts, or a DSN (its password) printed."""
    (tmp_path / "eval.env").write_text(f"LAB_DATABASE_URL={TXN}\nLAB_EVAL_CONCURRENCY=6\n")
    consumer = tmp_path / "gateway.env"
    consumer.write_text(f"DATABASE_URL={CONSUMER['DATABASE_URL']}\n")
    args = ["--consumer-env-file", str(consumer), "--lab-env-dir", str(tmp_path)]
    assert PB["main"]([*args, "--json"]) == 0
    out = capsys.readouterr().out
    assert [row["client"] for row in json.loads(out)["lab"]] == ["lab eval worker"]
    assert PB["main"](args) == 0
    text = capsys.readouterr().out
    assert "MARKER-SECRET" not in out + text and "PASS lab: peak 7" in text
    assert PB["main"]([*args, "--set", "eval.LAB_EVAL_CONCURRENCY=40"]) == 1
    assert "FAIL lab: peak 41" in capsys.readouterr().out
