"""G8-FLAG (GAP-I3-1, I3R-7, R144): the `flag` verb on a REAL PostgreSQL.

Every case drives `cli.main` over the operator tool's own PostgreSQL composition; the
world starts with every flag on (`tests/d`'s admission seed). The runtime's read is
`infrx.require_feature` (0021: FOR SHARE), exactly what an admission or a signup-grant
claim runs.

    INFRX_D_TASK=g8 uv run --frozen pytest -q tests/g/ops/test_flag_pg.py
"""
from __future__ import annotations

import threading
import time

import pytest

from . import pgworld
from .pgworld import R, footprint, needs_pg
from .test_transition_pg import HARD_S, cli_run

pytestmark = needs_pg

MONEY = ("public.credit_ledger", "infrx.wallets", "infrx.credit_holds", "infrx.credit_ledger",
         "infrx.credit_wallets", "infrx.credit_wallet_holds", "infrx.signup_entitlements",
         "infrx.jobs", "public.usage_events")


def flag(name, switch, key, *extra):
    return ["flag", "--name", name, switch, "--idempotency-key", key, "--reason", R, *extra]


def row(w, name):
    return w.owner.execute("select enabled, updated_by, reason from infrx.feature_flags "
                           "where name = %s", (name,)).fetchone()


def runtime_reads(w, name) -> bool:
    """`require_feature` as the runtime runs it: True admitted, False 55000 maintenance."""
    conn = pgworld.pgharness.connect(w.database, autocommit=False)
    try:
        conn.execute("select infrx.require_feature(%s)", (name,))
        return True
    except Exception as exc:
        assert getattr(exc, "sqlstate", None) == "55000", exc
        return False
    finally:
        conn.rollback()
        conn.close()


def audits(w, key) -> int:
    return w.one("select count(*) from infrx.audit_entries where idempotency_key = %s", (key,))


def money(w) -> dict:
    return {t: v for t, v in footprint(w).items() if t in MONEY}


def test_flag_pg__signup_grant_off_off_on_is_audited_once_per_key_and_read_at_once(capsys):
    """signup_grant on -> off -> off -> on: each key audited exactly once as the operator
    key's principal (`admin_set_entitlements`), the row attributed to it, the runtime's
    FOR SHARE read sees each change immediately; the second `--off` answers changed=false;
    a replay of k1 after k3 turned it back on answers the recorded result and writes
    nothing; another payload under k1 is idempotency_conflict. No money row moves."""
    w = pgworld.world("g8_flag_cycle")
    before = money(w)
    code, off, err = cli_run(w, flag("signup_grant", "--off", "k1"), capsys)
    assert code == 0, err
    actor = off["actor"]
    assert off["name"] == "signup_grant" and off["updated_at"], off
    assert (off["enabled_before"], off["enabled_after"], off["changed"], off["replayed"]) == \
        (True, False, True, False), off
    assert row(w, "signup_grant") == (False, actor, R)
    assert runtime_reads(w, "signup_grant") is False
    code, again, _ = cli_run(w, flag("signup_grant", "--off", "k2"), capsys)
    assert code == 0 and (again["enabled_before"], again["changed"]) == (False, False), again
    code, on, _ = cli_run(w, flag("signup_grant", "--on", "k3"), capsys)
    assert code == 0 and (on["changed"], on["enabled_after"]) == (True, True), on
    assert runtime_reads(w, "signup_grant") is True
    assert w.owner.execute(
        "select idempotency_key, actor_principal, action from infrx.audit_entries "
        "where idempotency_key in ('k1', 'k2', 'k3') order by idempotency_key").fetchall() == \
        [(k, actor, "admin_set_entitlements") for k in ("k1", "k2", "k3")]
    assert actor == w.one("select id::text from public.api_keys where audience = 'operator' "
                          "and revoked_at is null")
    code, replay, _ = cli_run(w, flag("signup_grant", "--off", "k1"), capsys)
    assert code == 0 and replay == {**off, "replayed": True}, replay
    assert row(w, "signup_grant")[0] is True and audits(w, "k1") == 1
    code, _, err = cli_run(w, flag("signup_grant", "--on", "k1"), capsys)
    assert code == 1 and '"idempotency_conflict"' in err, err
    assert audits(w, "k1") == 1 and money(w) == before


def test_flag_pg__regime_unknown_and_dry_run_write_nothing(capsys):
    """A regime flag is refused naming credit-transition (R133/R144), an unknown name is
    not_found, and `--dry-run` prints the current row with no operator key: none of them
    changes a flag, an audit row or money (the whole footprint)."""
    w = pgworld.world("g8_flag_refuse")
    before = footprint(w)
    for regime in ("credit_admission", "legacy_usd_admission"):
        code, _, err = cli_run(w, flag(regime, "--off", f"r-{regime}"), capsys)
        assert code == 1 and '"invalid_request"' in err and "credit-transition" in err, err
    code, _, err = cli_run(w, flag("no_such_flag", "--on", "u"), capsys)
    assert code == 1 and '"not_found"' in err, err
    code, current, _ = cli_run(w, ["flag", "--name", "signup_grant", "--off", "--dry-run"],
                               capsys, operator=False)
    assert code == 0 and current["name"] == "signup_grant" and current["enabled"] is True
    assert {"updated_by", "reason", "updated_at"} <= set(current), current
    assert footprint(w) == before


def test_flag_pg__an_admission_holding_a_flag_bounds_the_write(capsys):
    """R144: the writer is `set_feature_flag` (EXCLUSIVE on the table), so an admission in
    flight that holds ANY flag FOR SHARE - here a CREDIT admission's `credit_admission` -
    keeps `signup_grant`'s write waiting, at most `--lock-timeout-s`; then the verb exits 1
    with the operator-facing message, the flag unchanged and nothing audited. Once the
    admission commits the same key goes through. Oracle: a direct UPDATE of the row takes
    no table lock and changes it at once; an unbounded wait never returns (HARD_S)."""
    w = pgworld.world("g8_flag_lock")
    admission = pgworld.pgharness.connect(w.database, autocommit=False)
    admission.execute("select infrx.require_feature('credit_admission')")
    argv = flag("signup_grant", "--off", "lk", "--lock-timeout-s", "0.5")
    ran: dict = {}
    try:
        started = time.monotonic()
        t = threading.Thread(target=lambda: ran.update(out=cli_run(w, argv, capsys)),
                             daemon=True)
        t.start()
        t.join(HARD_S)
        if t.is_alive():
            pytest.fail(f"the flag write did not return within {HARD_S}s")
        elapsed = time.monotonic() - started
    finally:
        admission.rollback()
        admission.close()
    code, _, err = ran["out"]
    assert code == 1 and '"state_conflict"' in err and "locked" in err, err
    assert elapsed < 0.5 + 1.0 + 1.5, elapsed               # bound + statement margin + slack
    assert row(w, "signup_grant")[0] is True and audits(w, "lk") == 0
    code, done, _ = cli_run(w, argv, capsys)
    assert code == 0 and done["changed"] is True and runtime_reads(w, "signup_grant") is False
