"""G8 points 2-3: the pilot transition legacy_usd -> credit on a REAL PostgreSQL (CREDIT-CUTOVER).

The database starts where the hosted pilot is (research/plan/21 §2): legacy USD admission
on, CREDIT admission and the signup grant off, USD test grants on two organizations, a
USD job in flight, the seed's provisional card named by the public listing. Every case
drives the operator CLI (`cli.main`) over the operator tool's own PostgreSQL composition.

Rates here are FIXTURE rates - local test values, labelled so in the card's approval and
version, never a launch price (P-01 is not decided).

    INFRX_D_TASK=g8 uv run --frozen pytest -q tests/g/ops/test_transition_pg.py
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.records import Admission, Usage
from infrx.operations import cli, transition

from . import pgworld
from .pgworld import R, admit_credit, admit_legacy, drift, footprint, needs_pg, settle

pytestmark = needs_pg

SEED_CARD = transition.SEED_PROVISIONAL                       # rc_marlin2b_2026_09_provisional
MINTED = "rc_marlin2b_20260922T120000Z_provisional_p01"       # what marlin_release() mints
# FIXTURE rates: labelled local test values, not a price (P-01).
FIXTURE_CARD = "rc_fixture_g8_approved_20260920"
FIXTURE_IN, FIXTURE_OUT = "0.50000000", "1.50000000"
FIXTURE_APPROVAL = "FIXTURE-G8 local test approval (not a launch price)"
ACTIVATE = ["credit-transition", "--card", FIXTURE_CARD, "--input-rate", FIXTURE_IN,
            "--output-rate", FIXTURE_OUT]


#: The straddle case's hard bound: its drain bound is 0.5 s, so a bounded run is far inside.
HARD_S = 30.0


def run(coro):
    return asyncio.run(coro)


def pilot(label: str):
    """The hosted pilot's shape: legacy on, CREDIT off, a USD job in flight, the seed's
    provisional card named by the listing. The id `marlin_release` mints cannot exist
    there: 0007's `rate_card_version` CHECK refuses its upper-case `T`/`Z` (asserted)."""
    w = pgworld.world(label)
    w.set_flag("credit_admission", False)
    w.set_flag("signup_grant", False)
    refused = pgworld.cc.attempt(
        w.owner, "insert into infrx.rate_card_versions (rate_card_version, model_id, "
        "deployment_revision_id, serving_version_id, input_rate_per_million, "
        "output_rate_per_million, effective_at, approved_by, provisional) "
        "select %s, model_id, deployment_revision_id, serving_version_id, 400, 1200, "
        "'2026-09-02T00:00:00Z', 'provisional - P-01 pending', true "
        "from infrx.rate_card_versions where rate_card_version = %s", (MINTED, SEED_CARD))
    assert refused is not None and "rate_card_version_check" in refused, refused
    request, admission = run(admit_legacy(w, "usd-in-flight"))
    return w, request, admission


def cli_run(w, argv, capsys, *, operator=True):
    env = {cli.OPERATOR_KEY_ENV: w.operator_secret} if operator else {}
    code = cli.main(argv, ops=w.ops, environ=env, prompt=lambda _: pytest.fail("prompted"))
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def publish_fixture_card(w, capsys, *, effective_at="2026-09-20T00:00:00+00:00",
                         version=FIXTURE_CARD, key="pub-fixture"):
    code, out, err = cli_run(w, [
        "publish-card", "--model", "nemostation/marlin-2b", "--card-version", version,
        "--input-rate", FIXTURE_IN, "--output-rate", FIXTURE_OUT, "--approved-by",
        FIXTURE_APPROVAL, "--effective-at", effective_at, "--idempotency-key", key,
        "--reason", R], capsys)
    assert code == 0, err
    return out


def codes(report) -> set[str]:
    return {blocker["code"] for blocker in report["blockers"]}


def test_credit_cutover__the_dry_run_inventories_everything_and_writes_nothing(capsys):
    """The report the coordinator runs on the box: flags, the USD job in flight and its
    USD hold, both organizations' USD statements (rollout_hold, USD, never CREDIT), both
    provisional card ids (the seed's, named by the listing; the minted one, absent), every
    key without its hash, what would change and why it cannot yet. It needs no operator
    key and changes no row.
    Oracle: a dry run that wrote (a flag, an audit row) or hid the USD in flight differs."""
    w, request, _ = pilot("g8_dry")
    before = footprint(w)
    code, report, _ = cli_run(w, ["credit-transition", "--dry-run", "--card", SEED_CARD,
                                  "--input-rate", "400", "--output-rate", "1200"], capsys,
                              operator=False)
    assert footprint(w) == before, "the dry run wrote"
    assert code == 1 and codes(report) == {"card_unapproved", "in_flight"}, report["blockers"]
    inv = report["inventory"]
    assert inv["in_flight"] == {"legacy_usd": {"preparing": 1}}, inv["in_flight"]
    assert inv["holds"]["legacy_usd/held"]["count"] == 1
    assert inv["holds"]["legacy_usd/held"]["unit"] == "USD"
    statements = {s["org_id"]: s for s in inv["usd"]["statements"]}
    assert statements[b.ORG_A] == {"org_id": b.ORG_A, "balance": "25.00000000", "entries": 1,
                                   "rollout_hold": True}, statements
    assert inv["flags"]["legacy_usd_admission"]["enabled"] is True
    assert not inv["flags"]["credit_admission"]["enabled"]
    assert report["would_change"] == [
        {"flag": "legacy_usd_admission", "enabled": False,
         "why": "freeze new legacy_usd admission"},
        {"flag": "credit_admission", "enabled": True, "why": "credit admission"},
        {"flag": "signup_grant", "enabled": True, "why": "credit admission"}]
    assert report["restart_with"] == {"ACCOUNTING_REGIME": "credit",
                                      "ACTIVE_RATE_CARD_VERSION": SEED_CARD}
    public = report["public_card"]
    assert public["seed_provisional"] == {"id": SEED_CARD, "present": True,
                                          "named_by_listing": True}, public
    assert public["minted_provisional"] == [] and public["listing_names_now"] == SEED_CARD
    cards = {c["rate_card_version"]: c for c in inv["cards"]}
    assert cards[SEED_CARD]["named_by_listing"] is True and cards[SEED_CARD]["provisional"]
    assert cards[SEED_CARD]["jobs_pinned"] == 0          # the USD job pins a price version
    assert MINTED not in cards
    keys = inv["keys"]
    assert {k["audience"] for k in keys} >= {"consumer", "operator"}
    text = json.dumps(report)
    assert "key_hash" not in text and "hash-" not in text and w.operator_secret[9:] not in text
    assert any("rollout_hold" in note for note in report["notes"])


def test_credit_cutover__activation_refuses_an_unapproved_missing_or_unlisted_card(capsys):
    """P-01: the live command names the card AND restates its rates; a provisional card,
    an unknown one, restated rates that differ and a card the listing does not name are
    refused before anything changes (no flag, no audit row), each with the report.
    Oracle: an activation that trusted the card name alone would pin a provisional or
    mispriced card as the public price."""
    w, _, _ = pilot("g8_refuse")
    publish_fixture_card(w, capsys, version="rc_fixture_g8_future", key="pub-future",
                         effective_at="2026-12-01T00:00:00+00:00")
    before = footprint(w)
    cases = {
        "card_unapproved": [*ACTIVATE[:2], SEED_CARD, "--input-rate", "400", "--output-rate",
                            "1200"],
        "card_missing": ACTIVATE,                                   # not published yet
        "card_rates_mismatch": [*ACTIVATE[:2], "rc_fixture_g8_future", "--input-rate", "0.5",
                                "--output-rate", "9"],
        "card_not_listed": [*ACTIVATE[:2], "rc_fixture_g8_future", *ACTIVATE[3:]],
    }
    for n, (code_wanted, argv) in enumerate(cases.items()):
        code, report, err = cli_run(w, [*argv, "--idempotency-key", f"t-{n}", "--reason", R],
                                    capsys)
        assert code == 1 and code_wanted in codes(report), (code_wanted, report["blockers"])
        assert json.loads(err)["error"] == "state_conflict"
        assert report["applied"] == []
    code, _, err = cli_run(w, ["credit-transition", "--idempotency-key", "t-x", "--reason", R],
                           capsys)
    assert code == 1
    assert footprint(w) == before, "a refused activation changed something"


def test_credit_cutover__freeze_drain_enable_never_converts_usd_and_history_keeps_its_unit(
        capsys):
    """The procedure end to end: publish the approved card for the listed deployment, then
    activate. With the USD job still in flight it freezes USD admission and stops (no audit
    row); new USD admission is refused while the job settles IN USD; the rerun under the
    same key enables CREDIT and the signup grant, audited once; a replay of the key and a
    rerun under a new key change nothing. USD statements move only by that job's own USD
    debit; no CREDIT entry appears for any USD organization; the settled USD job's replay
    answers its committed USD outcome and cannot settle in CREDIT; a new CREDIT admission
    pins the approved card. Oracle: a converting or relabelling transition, a switch with
    USD work in flight, or a replay that re-settled under the new regime all differ."""
    w, request, admission = pilot("g8_switch")
    published = publish_fixture_card(w, capsys)
    assert published["rate_card_version"] == FIXTURE_CARD and published["written"] == \
        [False, False, True], published                  # the listed serving/deployment reused
    credit_ledger = w.owner.execute("select * from infrx.credit_ledger order by entry_id").fetchall()
    usd_before = w.owner.execute("select org_id, sum(delta_usd) from public.credit_ledger "
                                 "group by 1 order by 1").fetchall()
    argv = [*ACTIVATE, "--idempotency-key", "switch-1", "--reason", R]

    code, report, _ = cli_run(w, argv, capsys)                    # blocked: USD in flight
    assert code == 1 and codes(report) == {"in_flight"}, report["blockers"]
    assert report["applied"] == [{"flag": "legacy_usd_admission", "enabled": False}]
    assert w.one("select enabled from infrx.feature_flags where name = 'credit_admission'") \
        is False
    assert w.one("select count(*) from infrx.audit_entries where idempotency_key = 'switch-1'") \
        == 0
    with pytest.raises(errors.DependencyUnavailable):             # frozen: 55000 maintenance
        run(admit_legacy(w, "usd-after-freeze"))
    _, _, settled = run(settle(w, request, "legacy_usd", tokens=Usage.of(2000, 500)))
    assert settled.debit > 0

    code, result, _ = cli_run(w, argv, capsys)                    # drained: enabled
    assert code == 0, result
    assert result["flags"] == {"credit_admission": True, "legacy_usd_admission": False,
                               "signup_grant": True}, result
    assert result["restart_with"] == {"ACCOUNTING_REGIME": "credit",
                                      "ACTIVE_RATE_CARD_VERSION": FIXTURE_CARD}
    assert result["public_card"]["listing_names_now"] == FIXTURE_CARD
    assert w.one("select count(*) from infrx.audit_entries where idempotency_key = 'switch-1' "
                 "and action = 'admin_set_entitlements'") == 1
    again_code, again, _ = cli_run(w, argv, capsys)               # the key's replay
    assert (again_code, again) == (0, result)
    code, fresh, _ = cli_run(w, [*ACTIVATE, "--idempotency-key", "switch-2", "--reason", R],
                             capsys)
    assert code == 0 and fresh["applied"] == [], fresh            # nothing left to change

    # USD: only the drained job's own USD debit moved it; CREDIT: not one new entry.
    usd_after = dict(w.owner.execute("select org_id::text, sum(delta_usd) from public.credit_ledger "
                                     "group by 1").fetchall())
    for org, balance in usd_before:
        expected = balance - (settled.debit if str(org) == b.ORG_A else 0)
        assert usd_after[str(org)] == expected, (org, balance, usd_after)
    assert w.owner.execute("select * from infrx.credit_ledger order by entry_id").fetchall() == \
        credit_ledger, "the transition wrote CREDIT money"
    assert drift(w) == []

    async def history():
        store = pgworld.jobs(w)
        replay = await store.lookup(b.ORG_A, b.idem(request, "usd-in-flight"))
        with pytest.raises(errors.NotFound):
            await store.get_owned_credit(b.ORG_A, admission.job_handle)
        _, outcome = await store.get_owned(b.ORG_A, admission.job_handle)
        credit_request, credit_admission = await admit_credit(w, "credit-after-switch")
        return replay, outcome, credit_admission
    replay, outcome, credit_admission = run(history())
    assert replay[0].request_id == request.request_id and replay[0].replayed
    assert (replay[1].debit, outcome.debit) == (settled.debit, settled.debit)
    assert type(replay[0]) is Admission                            # the v1 USD admission, still
    assert credit_admission.pins.rate_card_version == FIXTURE_CARD
    with pytest.raises(errors.DependencyUnavailable):
        run(admit_legacy(w, "usd-after-switch"))


def test_credit_cutover__a_rollback_drains_credit_before_reopening_usd(capsys):
    """`--to legacy_usd` is the same procedure mirrored: with a CREDIT job in flight it
    freezes CREDIT admission and waits; once that job ends (a cancel: no usage, no debit,
    the hold released - R106) USD admission reopens. The CREDIT wallet ends exactly where it
    was. Oracle: a rollback with CREDIT work in flight would strand it under a USD worker."""
    w, request, _ = pilot("g8_rollback")
    run(settle(w, request, "legacy_usd"))
    publish_fixture_card(w, capsys)
    assert cli_run(w, [*ACTIVATE, "--idempotency-key", "up", "--reason", R], capsys)[0] == 0
    wallet = "select ledger_total, reserved_total from infrx.credit_wallets where owner_user_id = %s"
    before = w.owner.execute(wallet, (pgworld.cc.CONSUMER_1,)).fetchone()
    credit_request, admitted = run(admit_credit(w, "credit-in-flight"))
    assert w.owner.execute(wallet, (pgworld.cc.CONSUMER_1,)).fetchone()[1] > before[1]
    back = ["credit-transition", "--to", "legacy_usd", "--idempotency-key", "down", "--reason", R]
    code, report, _ = cli_run(w, back, capsys)
    assert code == 1 and codes(report) == {"in_flight"}
    assert report["inventory"]["in_flight"] == {"credit": {"preparing": 1}}
    outcome = run(pgworld.jobs(w).cancel(pgworld.personal_org(w, pgworld.cc.CONSUMER_1),
                                         admitted.job_handle))
    assert outcome.state.value == "cancelled"
    code, result, _ = cli_run(w, back, capsys)
    assert code == 0 and result["flags"]["legacy_usd_admission"] is True
    assert result["flags"]["credit_admission"] is False
    assert result["restart_with"] == {"ACCOUNTING_REGIME": "legacy_usd"}
    assert w.owner.execute(wallet, (pgworld.cc.CONSUMER_1,)).fetchone() == before
    assert drift(w) == []
    run(admit_legacy(w, "usd-reopened"))


def test_credit_cutover__an_admission_open_across_the_freeze_is_waited_for(capsys):
    """Two connections (review G8-R1 / ACC-1): a legacy admission passes both flag checks
    (`admit_legacy_usd` and the insert trigger) and inserts its job, then parks on
    `infrx.credit_holds`, which connection C holds in SHARE mode, while the transition
    runs. Since D10 `require_feature` reads the flag FOR SHARE, so the admission holds the
    flag row and the freeze's UPDATE waits on it: the transition must wait for it no longer
    than its bound, then stop with `open_transactions`, nothing changed, the target off and
    nothing audited. Once the admission commits, the dry run counts it in flight; after it
    settles in USD the rerun enables CREDIT.
    Oracle: without the wait the run exits 0, audited, and the USD job commits after the
    drain measured zero (the review's reproduction); without the lock bound the run never
    returns (the D10 merged-tree hang) - the case fails at `HARD_S` instead of hanging."""
    w, request, _ = pilot("g8_straddle")
    run(settle(w, request, "legacy_usd"))
    publish_fixture_card(w, capsys)
    blocker = pgworld.pgharness.connect(w.database, autocommit=False)
    blocker.execute("lock table infrx.credit_holds in share mode")
    box: dict = {}

    def admit():
        try:
            box["admitted"] = run(admit_legacy(w, "straddler"))
        except Exception as exc:                              # reported by the assert below
            box["error"] = exc
    thread = threading.Thread(target=admit)
    thread.start()
    argv = [*ACTIVATE, "--idempotency-key", "straddle", "--reason", R,
            "--drain-timeout-s", "0.5", "--poll-s", "0.1"]
    try:
        for _ in range(100):                                  # A waits on C's lock
            if w.one("select count(*) from pg_locks where not granted and "
                     "relation = 'infrx.credit_holds'::regclass"):
                break
            time.sleep(0.05)
        else:
            pytest.fail("the admission never reached the held lock")
        ran: dict = {}
        cli_thread = threading.Thread(target=lambda: ran.update(out=cli_run(w, argv, capsys)),
                                      daemon=True)
        cli_thread.start()
        cli_thread.join(HARD_S)
        if cli_thread.is_alive():
            pytest.fail(f"the transition did not return within {HARD_S}s (unbounded flag lock)")
        code, report, _ = ran["out"]
    finally:
        blocker.rollback()
        blocker.close()
        thread.join(30)
    assert code == 1 and codes(report) == {"open_transactions"}, report
    assert report["applied"] == []                            # the freeze waited, then gave up
    assert w.one("select enabled from infrx.feature_flags where name = 'legacy_usd_admission'") \
        is True
    assert w.one("select enabled from infrx.feature_flags where name = 'credit_admission'") \
        is False
    assert w.one("select count(*) from infrx.audit_entries where idempotency_key = 'straddle'") \
        == 0
    assert "admitted" in box, box                             # it committed once released
    code, dry, _ = cli_run(w, ["credit-transition", "--dry-run", *ACTIVATE[1:]], capsys,
                           operator=False)
    assert code == 1 and codes(dry) == {"in_flight"}, dry["blockers"]
    assert dry["inventory"]["in_flight"] == {"legacy_usd": {"preparing": 1}}
    run(settle(w, box["admitted"][0], "legacy_usd"))          # it settles IN USD
    code, result, _ = cli_run(w, argv, capsys)
    assert code == 0 and result["flags"]["credit_admission"] is True, result
    assert drift(w) == []


def test_credit_cutover__overlapping_admissions_cannot_stretch_a_flag_write_past_its_bound():
    """V-G8TL-1: admissions overlap, each holding the flag FOR SHARE (0021 `require_feature`),
    so the flag's UPDATE waits on one MultiXact after another. `lock_timeout` bounds each
    wait, not their sum; the write as a whole must end within its bound (plus the fixed
    statement margin), refused (`FlagLocked`) or done.
    Oracle: with only `lock_timeout` the write returned after 6.7-34 s against a 2 s bound
    (four 50 ms lockers); eight 200 ms lockers keep the row share-locked, so it never does."""
    w = pgworld.world("g8_overlap")
    stop = threading.Event()

    def locker():
        conn = pgworld.pgharness.connect(w.database, autocommit=False)
        try:
            while not stop.is_set():
                try:
                    conn.execute("select infrx.require_feature('legacy_usd_admission')")
                    conn.execute("select pg_sleep(0.2)")
                    conn.commit()
                except Exception:                             # the flag is off: refused
                    conn.rollback()
        finally:
            conn.close()
    lockers = [threading.Thread(target=locker, daemon=True) for _ in range(8)]
    for t in lockers:
        t.start()
    box: dict = {}

    def write():
        started = time.monotonic()
        try:
            box["changed"] = run(w.ops.transitions.set_flag(
                "legacy_usd_admission", False, "g8-probe", R, lock_timeout_s=2.0))
        except transition.FlagLocked as exc:
            box["refused"] = exc
        box["elapsed"] = time.monotonic() - started
    try:
        time.sleep(0.3)                                       # the lockers overlap by now
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        writer.join(HARD_S)
        assert not writer.is_alive(), f"the flag write did not return within {HARD_S}s"
    finally:
        stop.set()
        for t in lockers:
            t.join(10)
    assert "elapsed" in box, box
    assert box["elapsed"] < 2.0 + 1.0 + 1.5, box              # bound + margin + slack
