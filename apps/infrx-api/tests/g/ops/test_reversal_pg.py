"""RUNBOOK-3: the W7f reversal drilled on a REAL PostgreSQL (rollout.md §3, rollback.md).

The launch card is published and CREDIT activated under key K1 (W7f), CREDIT work is admitted
and settled, then `credit-transition --to legacy_usd` runs under key K2 with a CREDIT job in
flight. Every step drives the operator CLI (`cli.main`) over the tool's own PostgreSQL
composition. Rates are FIXTURE rates (labelled, never a price: P-01).

    INFRX_D_TASK=revoke uv run --frozen --no-sync pytest -q tests/g/ops/test_reversal_pg.py
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from infrx.contracts import errors

from .pgworld import R, admit_credit, admit_legacy, drift, footprint, needs_pg, settle
from .test_transition_pg import (ACTIVATE, FIXTURE_CARD, cli_run, codes, pilot,
                                 publish_fixture_card, run)

pytestmark = needs_pg

BACK = ["credit-transition", "--to", "legacy_usd"]
K2 = [*BACK, "--drain-timeout-s", "0.3", "--poll-s", "0.1", "--idempotency-key", "K2",
      "--reason", R]
#: FIXTURE card 0.5 / 1.5 CREDIT per million, `settle`'s usage 1,200 in / 340 out.
CHARGE = Decimal("0.00111000")


def flags(w) -> dict:
    return dict(w.owner.execute("select name, enabled from infrx.feature_flags").fetchall())


def credit_rows(w) -> tuple:
    """Every CREDIT money row, as values: the ledger, the holds and the wallet summaries."""
    return tuple(w.owner.execute(f"select * from {t} order by 1").fetchall()
                 for t in ("infrx.credit_ledger", "infrx.credit_wallet_holds",
                           "infrx.credit_wallets"))


def test_reversal_pg__credit_back_to_legacy_usd_drains_keeps_credit_exact_and_replays_nothing(
        capsys):
    """K1 activates CREDIT (W7f). A CREDIT job settles at the card; another stays in flight.
    The reversal's dry run writes nothing and names what would change. K2 past its drain
    bound freezes CREDIT and stops (exit 1, `in_flight`, nothing audited): neither regime
    admits, and a rerun before the worker has finished the job stops again, writing nothing
    (so the runbooks run it while the worker runs). The in-flight job settles IN CREDIT
    (`settle` plays the CREDIT worker); K2's rerun enables legacy_usd only; CREDIT
    admission is refused and legacy accepted. Every CREDIT row settled before stays exact.
    The two audit rows carry the operator key as actor, the regimes before and after and K1's
    card. Replays of K2 and of K1 answer the recorded results and write nothing, so a
    roll-forward re-activates only under a NEW key.
    Oracle: a reversal without the drain reopens USD with CREDIT work running; one audited as
    the forward move, or a replay that re-ran the write, differs in the audit or the flags."""
    w, usd_request, _ = pilot("rv_drill")
    run(settle(w, usd_request, "legacy_usd"))
    publish_fixture_card(w, capsys)
    code, up, err = cli_run(w, [*ACTIVATE, "--idempotency-key", "K1", "--reason", R], capsys)
    assert code == 0, err
    done_request, _ = run(admit_credit(w, "credit-settled"))
    _, _, (_, done) = run(settle(w, done_request, "credit"))
    assert (str(done.charged), done.rate_card_version) == (str(CHARGE), FIXTURE_CARD), done
    flying_request, _ = run(admit_credit(w, "credit-in-flight"))

    before = footprint(w)
    code, dry, _ = cli_run(w, [*BACK, "--dry-run"], capsys, operator=False)
    assert footprint(w) == before, "the reversal's dry run wrote"
    assert code == 1 and codes(dry) == {"in_flight"}, dry["blockers"]
    assert dry["inventory"]["in_flight"] == {"credit": {"preparing": 1}}
    assert dry["would_change"] == [
        {"flag": "credit_admission", "enabled": False, "why": "freeze new credit admission"},
        {"flag": "legacy_usd_admission", "enabled": True, "why": "legacy_usd admission"}]

    code, blocked, err = cli_run(w, K2, capsys)                  # past the drain bound
    assert code == 1 and codes(blocked) == {"in_flight"}, blocked["blockers"]
    assert json.loads(err)["error"] == "state_conflict"
    assert blocked["applied"] == [{"flag": "credit_admission", "enabled": False}]
    assert flags(w) == {"credit_admission": False, "legacy_usd_admission": False,
                        "signup_grant": True}
    assert w.one("select count(*) from infrx.audit_entries where idempotency_key = 'K2'") == 0
    with pytest.raises(errors.DependencyUnavailable):            # legacy: not until drained
        run(admit_legacy(w, "usd-while-draining"))
    with pytest.raises(errors.DependencyUnavailable):            # CREDIT: frozen
        run(admit_credit(w, "credit-while-draining"))
    # Review 0-RV3-1: nothing but a CREDIT worker ends the job, so a same-key rerun while none
    # runs stops at the bound again and changes nothing - the runbooks reverse W7f before any
    # step that stops the worker (rollback.md drill step 3 before 3b's pause).
    stuck = footprint(w)
    code, again, _ = cli_run(w, K2, capsys)
    assert (code, codes(again), again["applied"]) == (1, {"in_flight"}, []), again
    assert footprint(w) == stuck, "a rerun with no worker wrote"

    # The worker, still running, finishes it
    _, _, (_, drained) = run(settle(w, flying_request, "credit"))  # settles IN CREDIT
    assert (str(drained.charged), drained.rate_card_version) == (str(CHARGE), FIXTURE_CARD), \
        drained
    settled = credit_rows(w)
    code, down, err = cli_run(w, K2, capsys)
    assert code == 0, err
    assert down["applied"] == [{"flag": "legacy_usd_admission", "enabled": True}], down
    assert down["flags"] == {"credit_admission": False, "legacy_usd_admission": True,
                             "signup_grant": True}
    assert down["restart_with"] == {"ACCOUNTING_REGIME": "legacy_usd"}
    with pytest.raises(errors.DependencyUnavailable):
        run(admit_credit(w, "credit-after-reversal"))
    run(admit_legacy(w, "usd-after-reversal"))
    assert credit_rows(w) == settled, "the reversal or the USD admission moved CREDIT money"
    debits = w.owner.execute(
        "select request_id::text, amount from infrx.credit_ledger where kind = 'inference_debit' "
        "order by 1").fetchall()
    assert sorted(debits) == sorted([(done_request.request_id, -CHARGE),
                                     (flying_request.request_id, -CHARGE)]), debits
    assert drift(w) == []

    operator = w.one("select id::text from public.api_keys where audience = 'operator' "
                     "and revoked_at is null")
    audit = {k: (actor, action, before_, after) for k, actor, action, before_, after in
             w.owner.execute("select idempotency_key, actor_principal, action, before, after "
                             "from infrx.audit_entries where idempotency_key in ('K1', 'K2')")}
    assert set(audit) == {"K1", "K2"}
    for key, (actor, action, _, after) in audit.items():
        assert (actor, action, after["operation"]) == \
            (operator, "admin_set_entitlements", "transition"), key
    k1_before, k1 = audit["K1"][2:]
    assert k1_before["flags"] == {"legacy_usd_admission": True, "credit_admission": False,
                                  "signup_grant": False}
    assert (k1["request"]["target"], k1["request"]["card"]) == ("credit", FIXTURE_CARD)
    assert k1["result"]["flags"] == {"legacy_usd_admission": False, "credit_admission": True,
                                     "signup_grant": True}
    assert k1["result"]["restart_with"] == {"ACCOUNTING_REGIME": "credit",
                                            "ACTIVE_RATE_CARD_VERSION": FIXTURE_CARD}
    k2_before, k2 = audit["K2"][2:]
    assert k2_before["flags"] == {"legacy_usd_admission": False, "credit_admission": False,
                                  "signup_grant": True}          # the blocked run's freeze
    assert (k2["request"]["target"], k2["request"]["card"]) == ("legacy_usd", None)
    assert k2["result"] == down

    before = footprint(w)
    code, replay, _ = cli_run(w, K2, capsys)
    assert (code, replay) == (0, down) and footprint(w) == before, "K2's replay wrote"
    code, again, _ = cli_run(w, [*ACTIVATE, "--idempotency-key", "K1", "--reason", R], capsys)
    assert (code, again) == (0, up) and footprint(w) == before, "K1's replay re-activated"
    assert flags(w)["credit_admission"] is False
