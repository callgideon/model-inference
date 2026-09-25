"""G8 points 2-3 at the seam the mutants edit: `transition.plan` (pure) and `apply`'s
order over a scripted store, plus `publish-card`'s refusal of an unapproved price.

`test_transition_pg.py` proves the same procedure on PostgreSQL (a real-service suite
outside the mutant runner). Rates here are FIXTURE values, labelled, never a price.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
from datetime import datetime, timezone

import pytest

from infrx.contracts import errors
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2.records import CredentialAudience
from infrx.operations import cli, transition

from . import fakes

R = "cutover window 1"
CARD = "rc_fixture_g8_approved"
IN, OUT = "0.50000000", "1.50000000"
APPROVAL = "FIXTURE-G8 local test approval (not a launch price)"


def inventory(*, flying=None, drift=(), approved_by=APPROVAL, provisional=False,
              listed=CARD, legacy_on=True, credit_on=False) -> dict:
    """The shape `transition.shape` returns, reduced to what `plan` reads."""
    cards = [{"rate_card_version": transition.SEED_PROVISIONAL, "input_rate": "400.00000000",
              "output_rate": "1200.00000000", "approved_by": "provisional - P-01 pending",
              "provisional": True, "named_by_listing": listed == transition.SEED_PROVISIONAL},
             {"rate_card_version": CARD, "input_rate": IN, "output_rate": OUT,
              "approved_by": approved_by, "provisional": provisional,
              "named_by_listing": listed == CARD}]
    return {"as_of": "2026-09-20T12:00:00+00:00", "alias": v2fix.PUBLIC_MODEL_ID,
            "flags": {"legacy_usd_admission": {"enabled": legacy_on},
                      "credit_admission": {"enabled": credit_on},
                      "signup_grant": {"enabled": credit_on}},
            "in_flight": flying or {}, "unknown_usage": {}, "drift": list(drift),
            "listing": {"rate_card_version": listed}, "cards": cards,
            "usd": {"statements": [{"org_id": fakes.ORG_A, "balance": "5.00000000",
                                    "entries": 1, "rollout_hold": True}]}}


def plan(inv, **kw):
    return transition.plan(inv, **{"target": "credit", "card": CARD, "input_rate": IN,
                                   "output_rate": OUT, **kw})


def codes(report) -> list[str]:
    return [b["code"] for b in report["blockers"]]


def test_credit_cutover__only_an_approved_listed_card_at_the_restated_rates_activates():
    """P-01 / F2C-C: the card must be published, not provisional (by its flag or by its
    approval text), the one the listing names, at exactly the restated rates (compared as
    exact CREDIT, so `0.5` restates `0.50000000`). Oracle: dropping any one check lets a
    provisional, unlisted or mispriced card become the public price."""
    ready = plan(inventory(), input_rate="0.5")
    assert codes(ready) == [] and ready["restart_with"] == {
        "ACCOUNTING_REGIME": "credit", "ACTIVE_RATE_CARD_VERSION": CARD}
    assert [c["flag"] for c in ready["would_change"]] == [
        "legacy_usd_admission", "credit_admission", "signup_grant"]
    assert codes(plan(inventory(provisional=True))) == ["card_unapproved"]
    for text in ("provisional - P-01 pending", "ops (P-01)", "pending review", "  "):
        assert codes(plan(inventory(approved_by=text))) == ["card_unapproved"], text
    assert codes(plan(inventory(), output_rate="1.5000001")) == ["card_rates_mismatch"]
    assert codes(plan(inventory(listed=transition.SEED_PROVISIONAL))) == ["card_not_listed"]
    assert codes(plan(inventory(), card="rc_nowhere")) == ["card_missing"]
    assert codes(plan(inventory(), card=None)) == ["card_missing"]
    assert codes(plan(inventory(), input_rate=None)) == ["card_missing"]
    with pytest.raises(errors.InvalidRequest):
        plan(inventory(), input_rate="four hundred")


def test_credit_cutover__drift_and_the_source_regimes_work_in_flight_block_the_switch():
    """Drift blocks either direction; only the SOURCE regime's jobs in flight block (a
    CREDIT job in flight does not stop a switch to CREDIT). The rollback needs no card.
    Oracle: counting the target's jobs, or ignoring drift, switches with money unreconciled
    or with USD work that a CREDIT worker cannot finish."""
    assert codes(plan(inventory(flying={"legacy_usd": {"queued": 2}}))) == ["in_flight"]
    assert codes(plan(inventory(flying={"credit": {"running": 1}}))) == []
    assert codes(plan(inventory(drift=[{"unit": "USD"}]))) == ["drift"]
    back = transition.plan(inventory(flying={"credit": {"running": 1}}, legacy_on=False,
                                     credit_on=True), target="legacy_usd")
    assert codes(back) == ["in_flight"] and back["restart_with"] == {
        "ACCOUNTING_REGIME": "legacy_usd"} and back["public_card"] is None
    assert [c["flag"] for c in back["would_change"]] == ["credit_admission",
                                                         "legacy_usd_admission"]
    assert codes(transition.plan(inventory(drift=[{"unit": "CREDIT"}]),
                                 target="legacy_usd")) == ["drift"]
    with pytest.raises(errors.InvalidRequest):
        transition.plan(inventory(), target="usd")


def test_credit_cutover__the_report_names_the_public_card_and_every_provisional_id():
    """F11: both provisional identities are reported with the one the listing names; the
    public card after the switch is the approved one; everything else is history.
    Oracle: a report that hid the minted id or called the seed card public misleads the
    operator about what customers are priced at."""
    inv = inventory()
    inv["cards"].append({"rate_card_version": "rc_marlin2b_20260922t120000z_provisional_p01",
                         "input_rate": "400.00000000", "output_rate": "1200.00000000",
                         "approved_by": "provisional - P-01 pending", "provisional": True,
                         "named_by_listing": False})
    public = plan(inv)["public_card"]
    assert public["public"] == CARD and public["listing_names_now"] == CARD
    assert public["seed_provisional"] == {"id": transition.SEED_PROVISIONAL, "present": True,
                                          "named_by_listing": False}
    assert public["minted_provisional"] == ["rc_marlin2b_20260922t120000z_provisional_p01"]
    assert CARD not in public["history"] and transition.SEED_PROVISIONAL in public["history"]
    assert any("never converted" in note for note in plan(inv)["notes"])


@dataclasses.dataclass
class ScriptedStore:
    """The transition store over a script: jobs in flight per inventory call."""

    flying: list[int]
    flags: dict[str, bool] = dataclasses.field(default_factory=lambda: {
        "legacy_usd_admission": True, "credit_admission": False, "signup_grant": False})
    calls: list = dataclasses.field(default_factory=list)
    #: The transactions open in the database per `open_transactions` call (the last repeats).
    open: list = dataclasses.field(default_factory=lambda: [frozenset()])

    async def open_transactions(self) -> frozenset:
        now = frozenset(self.open.pop(0) if len(self.open) > 1 else self.open[0])
        self.calls.append(("open", now))
        return now

    async def inventory(self, alias: str = v2fix.PUBLIC_MODEL_ID) -> dict:
        n = self.flying.pop(0) if len(self.flying) > 1 else self.flying[0]
        self.calls.append(("inventory", n))
        inv = inventory(flying={"legacy_usd": {"running": n}} if n else {})
        inv["flags"] = {f: {"enabled": on} for f, on in self.flags.items()}
        return copy.deepcopy(inv)

    #: Flags an admission in flight holds FOR SHARE (D10): a write past its bound fails.
    locked: set = dataclasses.field(default_factory=set)
    bounds: list = dataclasses.field(default_factory=list)

    async def set_flag(self, name, enabled, actor, reason, *, lock_timeout_s) -> bool:
        self.bounds.append(lock_timeout_s)
        if name in self.locked:
            raise transition.FlagLocked(name)
        self.calls.append(("set", name, enabled))
        changed, self.flags[name] = self.flags[name] != enabled, enabled
        return changed


def apply(w, store, key="t", timeout=10.0, **kw):
    ticks = iter(range(10_000))

    async def go():
        op = await w.ops.operator(w.operator_secret)
        return await transition.apply(op, store, target="credit", card=CARD, input_rate=IN,
                                      output_rate=OUT, idempotency_key=key, reason=R,
                                      drain_timeout_s=timeout, poll_s=1.0,
                                      sleep=lambda _: asyncio.sleep(0),
                                      monotonic=lambda: float(next(ticks)), **kw)
    return asyncio.run(go())


def test_credit_cutover__apply_freezes_first_drains_bounded_and_enables_last():
    """The order is the safety: the source regime's admission is frozen before the drain
    is measured, the target is enabled only after the source reads zero in flight, the
    whole run is audited once, and a drain past its bound stops with the freeze in place
    and the target untouched. Oracle: enabling first, skipping the drain or its bound, or
    skipping the final recheck switches with USD work still running."""
    w = fakes.world()
    store = ScriptedStore(flying=[1, 1, 1, 0, 0])
    result = apply(w, store)
    sets = [c for c in store.calls if c[0] == "set"]
    assert sets == [("set", "legacy_usd_admission", False), ("set", "credit_admission", True),
                    ("set", "signup_grant", True)], store.calls
    first_zero = store.calls.index(("inventory", 0))
    assert store.calls.index(sets[1]) > first_zero > store.calls.index(sets[0])
    assert result["applied"] == [{"flag": f, "enabled": e} for _, f, e in sets]
    assert [e.after["operation"] for e in w.audit.entries] == ["transition"]
    assert apply(w, store) == result and len(w.audit.entries) == 1          # the key's replay

    stuck = ScriptedStore(flying=[2])
    with pytest.raises(transition.TransitionBlocked) as blocked:
        apply(fakes.world(), stuck, timeout=3.0)
    assert blocked.value.report["applied"] == [{"flag": "legacy_usd_admission", "enabled": False}]
    assert stuck.flags == {"legacy_usd_admission": False, "credit_admission": False,
                           "signup_grant": False}
    # A job admitted just before the freeze committed shows up after the enable: stop,
    # say so, audit nothing - the rerun finishes once it drains.
    late = ScriptedStore(flying=[0, 0, 1])
    audited = fakes.world()
    with pytest.raises(transition.TransitionBlocked) as raced:
        apply(audited, late)
    assert [b["code"] for b in raced.value.report["blockers"]] == ["in_flight"]
    assert audited.audit.entries == []
    # `freeze_only`: both regimes paused and drained, the target left off (a runtime is
    # replaced in this window); the later run under a new key enables it.
    paused, window = ScriptedStore(flying=[1, 0]), fakes.world()
    held = apply(window, paused, key="pause", freeze_only=True)
    assert held["applied"] == [{"flag": "legacy_usd_admission", "enabled": False}]
    assert not any(paused.flags.values()), paused.flags
    assert apply(window, paused, key="resume")["applied"] == [
        {"flag": "credit_admission", "enabled": True}, {"flag": "signup_grant", "enabled": True}]
    # A refused card changes nothing at all.
    refused = ScriptedStore(flying=[0])
    with pytest.raises(transition.TransitionBlocked):
        asyncio.run(_apply_with(refused, card="rc_nowhere"))
    assert [c for c in refused.calls if c[0] == "set"] == []


def test_credit_cutover__apply_waits_out_every_transaction_open_at_the_freeze():
    """Review G8-R1 / ACC-1: `require_feature` takes no lock, so an admission that read the
    flag before the freeze committed can commit its job after the drain measured zero.
    After the freeze and before the drain is measured, apply waits until every transaction
    open at that moment has ended (one that began later reads the flag frozen and does not
    count), within the drain's bound; past it the run stops with the freeze in place, the
    target untouched and nothing audited.
    Oracle: skipping the wait, or re-listing instead of waiting on the set open at the
    freeze, enables CREDIT while a USD admission can still commit."""
    store = ScriptedStore(flying=[0], open=[{"a", "b"}, {"a", "c"}, {"c", "d"}])
    apply(fakes.world(), store)
    freeze = store.calls.index(("set", "legacy_usd_admission", False))
    assert store.calls[freeze + 1:freeze + 5] == [
        ("open", {"a", "b"}), ("open", {"a", "c"}), ("open", {"c", "d"}), ("inventory", 0)], \
        store.calls
    stuck, audited = ScriptedStore(flying=[0], open=[{"a"}]), fakes.world()
    with pytest.raises(transition.TransitionBlocked) as blocked:
        apply(audited, stuck, timeout=3.0)
    assert [b["code"] for b in blocked.value.report["blockers"]] == ["open_transactions"]
    assert blocked.value.report["applied"] == [
        {"flag": "legacy_usd_admission", "enabled": False}]
    assert not any(stuck.flags.values()) and audited.audit.entries == []


def test_credit_cutover__a_flag_an_admission_holds_is_waited_for_within_the_bound():
    """D10's `require_feature` reads the flag FOR SHARE, so an admission in flight blocks
    the flag's UPDATE until it commits - forever if it is parked (the D10 merged-tree hang).
    Every flag write carries the drain's remaining bound as its lock wait; a write that runs
    out refuses with `open_transactions`, keeps what already changed and audits nothing.
    Oracle: an unbounded write (no bound, or an infinite one) hangs the transition behind
    a parked admission; an unmapped lock timeout escapes as a raw error with no report."""
    store = ScriptedStore(flying=[0])
    apply(fakes.world(), store, timeout=3.0)
    assert len(store.bounds) == 3 and all(0 <= b <= 3.0 for b in store.bounds), store.bounds
    frozen = ScriptedStore(flying=[0], locked={"legacy_usd_admission"})
    audited = fakes.world()
    with pytest.raises(transition.TransitionBlocked) as blocked:
        apply(audited, frozen, timeout=3.0)
    assert [b["code"] for b in blocked.value.report["blockers"]] == ["open_transactions"]
    assert blocked.value.report["applied"] == [] and audited.audit.entries == []
    assert frozen.flags["legacy_usd_admission"] is True and not frozen.flags["credit_admission"]
    enabling = ScriptedStore(flying=[0], locked={"credit_admission"})
    with pytest.raises(transition.TransitionBlocked) as late:
        apply(audited, enabling, timeout=3.0)
    assert late.value.report["applied"] == [{"flag": "legacy_usd_admission", "enabled": False}]
    assert not any(enabling.flags.values()) and audited.audit.entries == []


async def _apply_with(store, **kw):
    w = fakes.world()
    op = await w.ops.operator(w.operator_secret)
    return await transition.apply(op, store, target="credit", input_rate=IN, output_rate=OUT,
                                  idempotency_key="t", reason=R, **kw)


def test_credit_cutover__the_cli_dry_run_needs_no_key_and_exits_nonzero_when_blocked(capsys):
    """`--dry-run` reads and plans with no operator credential; its exit status says
    whether the activation would go through now (0) or not (1). Oracle: a blocked dry run
    answering 0 would let a scripted window proceed to the restart."""
    w = fakes.world()
    w.ops.transitions = ScriptedStore(flying=[1])
    argv = ["credit-transition", "--dry-run", "--card", CARD, "--input-rate", IN,
            "--output-rate", OUT]
    assert cli.main(argv, ops=w.ops, environ={}, prompt=lambda _: pytest.fail("prompted")) == 1
    assert [b["code"] for b in json.loads(capsys.readouterr().out)["blockers"]] == ["in_flight"]
    w.ops.transitions = ScriptedStore(flying=[0])
    assert cli.main(argv, ops=w.ops, environ={}) == 0
    assert json.loads(capsys.readouterr().out)["blockers"] == []
    assert w.ops.transitions.flags["credit_admission"] is False            # nothing written
    with pytest.raises(SystemExit, match="idempotency-key"):
        cli.main(argv[:1] + argv[2:], ops=w.ops, environ={cli.OPERATOR_KEY_ENV: "x"})


def test_credit_rate__publish_card_publishes_approved_prices_only():
    """`publish-card` prices the deployment the listing serves with an operator-approved
    card and moves the listing to it; a provisional approval is refused before anything is
    written. Oracle: a provisional card published through the approved path becomes the
    listing's public price."""
    w = fakes.world()
    at = datetime(2026, 9, 21, tzinfo=timezone.utc)

    async def go():
        op = await w.ops.operator(w.operator_secret)
        with pytest.raises(errors.InvalidRequest):
            await op.publish_card(v2fix.REQUESTED_MODEL, rate_card_version="rc_x",
                                  input_rate=IN, output_rate=OUT,
                                  approved_by="provisional - P-01 pending", effective_at=at,
                                  idempotency_key="p0", reason=R)
        assert w.audit.entries == []
        done = await op.publish_card(v2fix.REQUESTED_MODEL, rate_card_version=CARD,
                                     input_rate=IN, output_rate=OUT, approved_by=APPROVAL,
                                     effective_at=at, idempotency_key="p1", reason=R)
        deployment = await w.catalog.resolve(v2fix.REQUESTED_MODEL,
                                             audience=CredentialAudience.consumer,
                                             endpoint_id=None)
        return done, await w.catalog.active_rate_card(deployment.deployment_revision_id)
    done, active = asyncio.run(go())
    assert done["written"] == [False, False, True] and done["rate_card_version"] == CARD
    assert (active.rate_card_version, str(active.input_rate_per_million)) == (CARD, IN)
