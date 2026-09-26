"""G8-FLAG (GAP-I3-1, I3R-7, R144) at the seam the mutants edit: the `flag` verb over a
scripted flag store, and `PgTransition.set_flag`'s one statement over a recording
connection. `test_flag_pg.py` proves the same verb on PostgreSQL (outside the runner).
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json

import pytest

from infrx.operations import cli, transition

from . import fakes

R = "cutover rollback: grant closed"


@dataclasses.dataclass
class FlagStore:
    """`infrx.feature_flags` as `inventory()["flags"]` shows it; records every write with
    the lock bound it was given (0-G8FLAG-R3: the bound is the operator's)."""

    flags: dict = dataclasses.field(default_factory=lambda: {
        f: {"enabled": True, "updated_by": "seed", "reason": "seed", "updated_at": "t0"}
        for f in ("credit_admission", "legacy_usd_admission", "signup_grant")})
    writes: list = dataclasses.field(default_factory=list)
    locked: set = dataclasses.field(default_factory=set)

    async def inventory(self, alias=None) -> dict:
        return {"flags": {n: dict(row) for n, row in self.flags.items()}}

    async def set_flag(self, name, enabled, actor, reason, *, lock_timeout_s) -> bool:
        if name in self.locked:
            raise transition.FlagLocked(name)
        self.writes.append((name, enabled, actor, reason, lock_timeout_s))
        row = self.flags[name]
        if row["enabled"] == enabled:
            return False
        row.update(enabled=enabled, updated_by=actor, reason=reason,
                   updated_at=f"t{len(self.writes)}")
        return True


def cli_run(w, argv, capsys, *, operator=True):
    env = {cli.OPERATOR_KEY_ENV: w.operator_secret} if operator else {}
    code = cli.main(argv, ops=w.ops, environ=env, prompt=lambda _: "")
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), \
        (json.loads(out.err) if out.err.strip() else None)


def flag(name, switch, key):
    return ["flag", "--name", name, switch, "--idempotency-key", key, "--reason", R]


def world():
    w = fakes.world()
    w.ops.transitions = FlagStore()
    return w


def test_flag__signup_grant_goes_off_and_on_through_the_audited_writer_as_the_operator(capsys):
    """GAP-I3-1: `flag --name signup_grant --off` writes through the transition's writer
    as the operator session's principal (never an argument), audited once per key; a
    second `--off` changes nothing but is audited under its own key; a replay of a key
    answers the recorded result without a second write; another payload under the key is
    `idempotency_conflict`. Oracle: ignoring `--off`, an actor taken from an argument, or
    a write outside `_once` all differ."""
    w, principal = world(), fakes.OPERATOR_KEY
    store = w.ops.transitions
    code, off, _ = cli_run(w, flag("signup_grant", "--off", "k1"), capsys)
    assert code == 0 and off == {"name": "signup_grant", "enabled_before": True,
                                 "enabled_after": False, "changed": True, "replayed": False,
                                 "actor": principal, "updated_at": "t1"}, off
    assert store.writes == [("signup_grant", False, principal, R, 5.0)]   # default bound
    assert store.flags["signup_grant"]["updated_by"] == principal
    code, again, _ = cli_run(w, flag("signup_grant", "--off", "k2"), capsys)
    assert code == 0 and (again["changed"], again["enabled_after"]) == (False, False), again
    code, on, _ = cli_run(w, [*flag("signup_grant", "--on", "k3"), "--lock-timeout-s", "0.25"],
                          capsys)
    assert code == 0 and (on["changed"], on["enabled_after"]) == (True, True), on
    assert store.writes[-1] == ("signup_grant", True, principal, R, 0.25), store.writes
    audited = [(e.idempotency_key, e.actor_principal, e.action, e.after["operation"],
                e.after["request"]) for e in w.audit.entries]
    assert audited == [(k, principal, "admin_set_entitlements", "flag",
                        {"name": "signup_grant", "enabled": v})
                       for k, v in (("k1", False), ("k2", False), ("k3", True))], audited
    code, replay, _ = cli_run(w, flag("signup_grant", "--off", "k1"), capsys)
    assert code == 0 and replay == {**off, "replayed": True}, replay
    assert len(store.writes) == 3 and store.flags["signup_grant"]["enabled"] is True
    code, _, err = cli_run(w, flag("signup_grant", "--on", "k1"), capsys)
    assert code == 1 and err["error"] == "idempotency_conflict", err
    assert len(store.writes) == 3 and len(w.audit.entries) == 3
    with pytest.raises(SystemExit):                      # no actor argument exists
        cli.main([*flag("signup_grant", "--off", "k4"), "--actor", "someone"], ops=w.ops,
                 environ={cli.OPERATOR_KEY_ENV: w.operator_secret})


def test_flag__a_regime_flag_an_unknown_flag_and_a_locked_flag_change_nothing(capsys):
    """R133/R144: a regime moves only with `credit-transition`, so `flag` refuses
    `credit_admission`/`legacy_usd_admission` naming it; an unknown name is `not_found`;
    a flag an admission holds past the bound is refused with the operator-facing message.
    None of them writes or audits. Oracle: dropping the regime refusal flips a regime
    flag behind the transition's freeze/drain; an unmapped lock escapes as a raw error."""
    w = world()
    store = w.ops.transitions
    for regime in ("credit_admission", "legacy_usd_admission"):
        code, _, err = cli_run(w, flag(regime, "--off", f"r-{regime}"), capsys)
        assert code == 1 and err["error"] == "invalid_request", err
        assert "credit-transition" in err["message"], err
    code, _, err = cli_run(w, flag("no_such_flag", "--off", "u"), capsys)
    assert code == 1 and err["error"] == "not_found", err
    store.locked.add("signup_grant")
    code, _, err = cli_run(w, flag("signup_grant", "--off", "l"), capsys)
    assert code == 1 and err["error"] == "state_conflict" and "locked" in err["message"], err
    assert store.writes == [] and w.audit.entries == []
    assert all(row["enabled"] for row in store.flags.values())


def test_flag__the_dry_run_reads_without_a_key_and_writes_nothing(capsys):
    """`--dry-run` prints the current row, needs no operator key or idempotency key, and
    writes nothing; it refuses a regime flag like the write does. 1-G8FLAG-R6: it needs no
    direction (the row alone); with one it adds the would-be change and still writes
    nothing, even given a key and a reason. A write needs a direction. Oracle: a dry run
    that wrote, required `--on/--off`, misreported the change, or showed a regime flag as
    writable here differs."""
    w = world()
    current = {"name": "signup_grant", "enabled": True, "updated_by": "seed",
               "reason": "seed", "updated_at": "t0"}
    code, row, _ = cli_run(w, ["flag", "--name", "signup_grant", "--dry-run"], capsys,
                           operator=False)
    assert code == 0 and row == current, row
    for switch, after in (("--off", False), ("--on", True)):
        code, row, err = cli_run(w, [*flag("signup_grant", switch, "d"), "--dry-run"], capsys,
                                 operator=False)
        assert code == 0 and row == {**current, "enabled_after": after,
                                     "changed": after is not True}, (row, err)
    code, _, err = cli_run(w, ["flag", "--name", "credit_admission", "--off", "--dry-run"],
                           capsys, operator=False)
    assert code == 1 and err["error"] == "invalid_request", err
    assert w.ops.transitions.writes == [] and w.audit.entries == []
    with pytest.raises(SystemExit, match="idempotency-key"):
        cli.main(["flag", "--name", "signup_grant", "--off"], ops=w.ops,
                 environ={cli.OPERATOR_KEY_ENV: w.operator_secret})
    with pytest.raises(SystemExit, match="--on or --off"):
        cli.main(["flag", "--name", "signup_grant", "--idempotency-key", "n", "--reason", R],
                 ops=w.ops, environ={cli.OPERATOR_KEY_ENV: w.operator_secret})
    assert w.ops.transitions.writes == [] and w.audit.entries == []


class Recorder:
    """A connection that records its statements and answers `changed`."""

    def __init__(self) -> None:
        self.sql: list = []

    async def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return self

    async def fetchone(self):
        return (True,)

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield

    async def close(self) -> None:
        pass


def test_flag__the_writer_is_set_feature_flag_under_the_bounded_lock():
    """R144: the verb's writer (`PgTransition.set_flag`, shared with the transition) runs
    one bounded transaction whose only write is `infrx.set_feature_flag` (the EXCLUSIVE
    table lock, then the guarded update) with the operator's name and reason. Oracle: a
    direct UPDATE of `infrx.feature_flags` skips the table lock (the PG case shows it
    then runs past an admission that holds another flag FOR SHARE)."""
    conn = Recorder()

    async def connect():
        return conn
    changed = asyncio.run(transition.PgTransition(connect).set_flag(
        "signup_grant", False, "op-key", R, lock_timeout_s=0.5))
    assert changed is True
    assert conn.sql == [("set local lock_timeout = 500", None),
                        ("set local statement_timeout = 1500", None),
                        ("select infrx.set_feature_flag(%s, %s, %s, %s)",
                         ("signup_grant", False, "op-key", R))], conn.sql
