#!/usr/bin/env python3
"""r1 R32: the mutation list and its runner.

A conformance case is only worth the invariant it can *kill*. Each entry below is a
single edit to the fakes that breaks one named invariant, together with the cases
that must notice. The runner applies one mutant at a time to a **copy** of the
package in a temporary directory, runs the named cases there, and fails if the
mutant survives. Nothing is ever written inside the worktree.

    uv run --frozen pytest -q tests/contracts/test_mutants.py       # the whole list
    make api-mutants                                                # same, from the root
    uv run --frozen python -m tests.contracts.mutants --list         # names only
    uv run --frozen python -m tests.contracts.mutants judge_settle_negative

Adding a conformance case means adding a mutant for the invariant it claims. A
mutant whose anchor no longer exists fails loudly rather than being skipped: the
list is part of the suite, not documentation of it.

The second half of this file is the **shared runner** every track's list delegates to
(F2R item 9): `Mutant`, `Outcome`, `Result`, `Runner`, `run_mutant`, `main` and - for
track D's in-process migration checks - `assertion_kill`. A track supplies a `Runner`
and keeps its own list; nobody keeps their own copy of the rules.
"""
from __future__ import annotations

import argparse
import enum
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

API_DIR = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = "infrx"


@dataclass(frozen=True)
class Mutant:
    """One single-edit defect and the cases that must fail because of it."""

    name: str
    invariant: str
    file: str
    old: str
    new: str
    cases: tuple[str, ...] = field(default_factory=tuple)
    #: Exception class names, beyond an assertion, this mutant may legitimately die by.
    #: Each use is a **documented** kill mode - an invariant whose content is "this never
    #: raises" can only be broken by a raise - never a convenience for a crashing case.
    dies_by: tuple[str, ...] = ()
    #: How many places this ONE conceptual edit touches. Still a single edit; the count is
    #: declared so a duplicated anchor is an error rather than a silent partial mutation.
    occurrences: int = 1

    @property
    def path(self) -> pathlib.Path:
        return pathlib.Path(PACKAGE) / self.file


def _m(name, invariant, file, old, new, *cases, dies_by=(), occurrences=1) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by), occurrences=occurrences)


S = "contracts/fakes/state.py"          # JobStore + StreamStore
J = "contracts/fakes/judge.py"
M = "contracts/fakes/media.py"
T = "contracts/traces_accounting.py"   # the shared trace accounting (F2R item 3)
TF = "contracts/fakes/traces.py"          # the fake's own hooks: FakeClock default, crash
F = "contracts/fakes/feedback.py"
Q = "contracts/fakes/scheduling.py"
R = "contracts/records.py"
W = "contracts/wire.py"                 # public bodies: projections and input bounds
MONEY = "contracts/money.py"
E = "contracts/fakes/engine.py"
TA = "contracts/traces_accounting.py"      # the accounting the fake and the spool share
# contracts v2 (F2P; folded into this list by the wire-in, item 6)
V2_R = "contracts/v2/records.py"
V2_P = "contracts/v2/ports.py"
V2_MU = "contracts/v2/money_units.py"
V2_FX = "contracts/v2/fixtures.py"
V2_INIT = "contracts/v2/__init__.py"
V2_FAKE = "contracts/conformance/v2_fakes.py"

# --- how a mutant is allowed to die (r1 round-3 review; enforced since F2R item 9) ----
# A port is a trust boundary, so a kill that depends on an *untyped* exception is a case a
# real adapter could fail for the wrong reason: a store answering the typed `DomainError`
# the contract promises must not crash the case. Every committed mutant dies on an
# assertion, and the shared runner now **enforces** that: a death by any other exception
# is `broken_runner` unless the mutant declares the class in `dies_by`. Exactly **five**
# mutants declare one; the first two and the last are guards whose entire purpose is to
# stop an untyped error escaping:
#
# * `mime_string_accepted` - `create_upload`'s allow-list check. Removing it lets
#   `tuple(5)` raise `TypeError` out of the port, which *is* the defect; adding a second
#   defensive conversion would make the first guard unkillable, because the port would
#   then answer `invalid_request` either way. The case asserts the typed code on the
#   unmutated path.
# * `add_raises_on_a_non_byte_part` - R37 says `TraceCapture.add` **never raises into the
#   request path**. The invariant is the absence of an exception, so the only way to break
#   it is to raise one, and the only honest kill is the raise.
# * `DEPLOY-04` - with `>=`, an equal pool (min == max, valid) is refused by the typed
#   `RuntimeMisconfigured` the case asserts is NOT raised: that refusal is the defect.
# * `drop_reason_falls_back_on_truthiness` - an `AssertionError` raised inside the package
#   is not the case's observation (R83 amendment (a)); here it is the accounting base's
#   own `_drop` guard, which is the invariant's enforcement in every sink.
# * `load_work_serves_a_credit_job` - a v1 `Work` cannot be built for a CREDIT job (it has
#   no USD price), so without the guard `load_work` raises pydantic's `ValidationError` out
#   of the port instead of the typed `not_found`: that escape is the defect.
# * `load_work_credit_serves_a_legacy_job` - the mirror: a legacy job has no CREDIT terms,
#   so without the guard `load_work_credit` raises `AttributeError` (on `job.credit.pins`)
#   out of the port instead of the typed `not_found` (F2P confirmation MONEY-C1).
#
# The six `ValidationError` kills the review found are gone: `FakeFeedbackService._row`
# maps a record-validation failure to `internal_error`, because the row's fields are
# server-chosen and a bad one is our bug rather than the caller's. The two `KeyError`
# kills are gone too - the trace cases read `loss_reasons` with `.get(..., 0)`, so a
# missing count is an assertion about a number and not a dictionary lookup that explodes.
MUTANTS: tuple[Mutant, ...] = (
    # --- identity, idempotency and tenancy -----------------------------------
    _m("admit_replays_any_org", "an idempotency scope belongs to the request's own org",
       S, 'raise errors.Forbidden("the idempotency scope must name the request\'s org")', "pass",
       "dur_admit__an_idempotency_scope_belongs_to_the_requests_own_org"),
    _m("admit_accepts_foreign_media", "a request carries only its own org's media",
       S, 'raise errors.NotFound("a request may only carry its own org\'s media")', "pass",
       "dur_admit__an_idempotency_scope_belongs_to_the_requests_own_org"),
    _m("admit_readmits_a_request", "a request UUID is admitted once (R6)",
       S, 'raise errors.StateConflict(\n                    f"request {request.request_id} is already an admitted job")', "pass",
       "dur_admit__a_request_uuid_is_admitted_once"),
    _m("admit_ignores_the_payload_hash", "a changed payload under the same key is 409",
       S, 'raise errors.IdempotencyConflict("same idempotency key, different canonical payload")',
       "pass", "dur_admit__changed_payload_with_the_same_key_is_a_conflict"),
    _m("idem_mapping_expires_while_active", "an active job never loses its mapping",
       S, "            self.idem[idem.scope] = _Idem(idem.payload_hash, request.request_id)",
       "            self.idem[idem.scope] = _Idem(idem.payload_hash, request.request_id,\n"
       "                                          now + timedelta(seconds=60))",
       "dur_admit__an_active_jobs_mapping_never_expires"),
    _m("expired_mapping_admits_again", "an expired mapping is explicit, never a second job",
       S, 'raise errors.IdempotencyExpired(f"idempotency key expired at {record.expires_at}")',
       "return None", "dur_admit__expired_mapping_is_explicit_never_a_second_billable_job",
       "dur_admit__an_active_jobs_mapping_never_expires"),
    _m("get_owned_ignores_the_org", "ownership failures are not found",
       S, "if job is None or job.request.org_id != org_id:", "if job is None:",
       "dur_admit__acceptance_creates_job_hold_reservations_and_outbox",
       "api_stream__reads_are_bounded_and_ownership_checked"),
    # --- authorization at admission -------------------------------------------
    _m("admit_skips_revocation", "a revoked key is refused in the transaction",
       S, 'raise errors.InvalidApiKey(f"key {request.key_id} is revoked")', "pass",
       "dur_admit__revocation_and_suspension_are_rechecked_in_the_transaction"),
    _m("admit_skips_suspension", "a suspended org is refused in the transaction",
       S, 'raise errors.OrgSuspended(f"org {request.org_id} is suspended")', "pass",
       "dur_admit__revocation_and_suspension_are_rechecked_in_the_transaction"),
    _m("admit_skips_entitlement", "entitlement is rechecked in the transaction (R10)",
       S, 'raise errors.ModelNotEntitled(\n                    f"org {request.org_id} is not entitled to {request.model_revision}")',
       "pass", "dur_admit__revocation_and_suspension_are_rechecked_in_the_transaction"),
    _m("admit_trusts_an_unpriced_model", "an unpriced model fails closed (R45)",
       S, 'raise errors.InvalidRequest("no price snapshot for the requested model")',
       'snapshot = PriceSnapshot(price_version="pv_mutant",\n'
       '                                     model_revision=request.model_revision,\n'
       '                                     input_rate_per_million=money.ZERO,\n'
       '                                     output_rate_per_million=money.ZERO,\n'
       '                                     token_rules_version="tr_v1",\n'
       '                                     captured_at=now)',
       "dur_admit__a_refused_admission_reserves_nothing"),
    # r1 R45: the price is the store's fact. This is the shape the fake used to have.
    _m("admit_prices_from_the_request", "the price never comes from the request (R45)",
       S, "        snapshot = self.price_for(request.model_revision, now)",
       "        snapshot = request.parameters.get(\"price_snapshot\") if request.parameters else None\n"
       "        snapshot = PriceSnapshot.model_validate(snapshot) if snapshot else None",
       "dur_settle__the_store_rounds_half_up_once",
       "dur_admit__a_refused_admission_reserves_nothing"),
    _m("admit_prices_another_model", "a price snapshot names the requested model (R45)",
       S, "        if snapshot.model_revision != request.model_revision:",
       "        if False:",
       "dur_admit__a_refused_admission_reserves_nothing"),
    _m("admit_reserves_before_validating", "a refused admission reserves nothing",
       S, "            price = None if terms else self._price(request, now)\n"
          "            hold = self._derive_hold(request, price, terms)\n"
          "            self._check_balance(request.org_id, hold, terms)\n"
          "            self.journal.reserve(request.request_id)",
       "            self.journal.reserve(request.request_id)\n"
          "            price = None if terms else self._price(request, now)\n"
          "            hold = self._derive_hold(request, price, terms)\n"
          "            self._check_balance(request.org_id, hold, terms)",
       "dur_admit__a_refused_admission_reserves_nothing"),
    # r1 R53: the hold is the store's, from the snapshot it took in the same
    # transaction. These are the two ways to get that wrong.
    _m("admit_uses_a_caller_supplied_hold", "no caller-supplied hold (R53)",
       S, "            hold = self._derive_hold(request, price, terms)",
       '            hold = money.parse(request.parameters.get("hold", "0.00070000"))',
       "dur_settle__a_price_change_never_undersizes_the_hold",
       "dur_cap__a_negative_maximum_hold_is_refused"),
    _m("admit_holds_for_the_wrong_ceilings", "the hold covers the validated ceilings (R53)",
       S, "        return price.maximum_hold(request.max_input_tokens, request.max_output_tokens)",
       "        return price.maximum_hold(request.max_input_tokens, 0)",
       "dur_settle__a_price_change_never_undersizes_the_hold",
       "dur_cap__a_hold_is_checked_against_available_not_the_ledger"),
    _m("admit_checks_the_balance_before_pricing", "the balance gate sees the derived hold (R53)",
       S, "            price = None if terms else self._price(request, now)\n"
          "            hold = self._derive_hold(request, price, terms)\n"
          "            self._check_balance(request.org_id, hold, terms)",
       "            self._check_balance(request.org_id, money.ZERO, terms)\n"
          "            price = None if terms else self._price(request, now)\n"
          "            hold = self._derive_hold(request, price, terms)",
       "dur_cap__hold_cannot_exceed_the_available_balance",
       "dur_cap__a_hold_is_checked_against_available_not_the_ledger"),
    _m("settlement_prices_at_the_current_rate", "settlement uses the admitted snapshot (R53)",
       S, "                         else job.admission.price_snapshot.debit(usage.prompt_tokens,",
       "                         else (self.price_for(job.request.model_revision, now)\n"
          "                               or job.admission.price_snapshot).debit(usage.prompt_tokens,",
       "dur_settle__a_price_change_never_undersizes_the_hold"),
    _m("load_work_reports_the_current_price", "load_work carries the admitted snapshot (R53)",
       S, "                        price_snapshot=job.admission.price_snapshot, budgets=job.budgets)",
       "                        price_snapshot=(self.price_for(job.request.model_revision,\n"
          "                                                     self.clock.now())\n"
          "                                        or job.admission.price_snapshot),\n"
          "                        budgets=job.budgets)",
       "dur_settle__a_price_change_never_undersizes_the_hold"),
    _m("admit_ignores_the_balance", "a hold cannot exceed the available balance",
       S, 'raise errors.InsufficientCredit(\n                f"maximum hold exceeds available balance for org {org_id}")', "pass",
       "dur_cap__hold_cannot_exceed_the_available_balance",
       "dur_cap__concurrent_admissions_never_oversubscribe"),
    _m("admit_accepts_any_deadline", "admission bounds deadline_at (R29)",
       S, "        if request.deadline_at > ceiling:", "        if False:",
       "dur_admit__a_deadline_must_be_one_the_store_can_keep"),
    _m("admit_accepts_a_past_deadline", "a deadline already past is refused (R29)",
       S, 'raise errors.InvalidRequest("the request deadline has already passed")', "pass",
       "dur_admit__a_deadline_must_be_one_the_store_can_keep"),
    # --- capacity -------------------------------------------------------------
    _m("cap_total_not_counted", "the total active-job ceiling",
       S, '("total", len(self.active_jobs()), limits.max_active_jobs),',
       '("total", 0, limits.max_active_jobs),',
       "dur_cap__total_org_and_key_limits_reject_with_retry_guidance"),
    _m("cap_org_not_counted", "the per-org active-job ceiling",
       S, '("org", len(self.active_jobs(org_id=request.org_id)), limits.max_active_jobs_per_org),',
       '("org", 0, limits.max_active_jobs_per_org),',
       "dur_cap__total_org_and_key_limits_reject_with_retry_guidance"),
    _m("cap_key_not_counted", "the per-key active-job ceiling",
       S, '("key", len(self.active_jobs(key_id=request.key_id)), limits.max_active_jobs_per_key),',
       '("key", 0, limits.max_active_jobs_per_key),',
       "dur_cap__total_org_and_key_limits_reject_with_retry_guidance"),
    _m("cap_preparation_not_counted", "the preparation admission cap (R1)",
       S, '("preparation", preparing, limits.max_preparing_jobs),',
       '("preparation", 0, limits.max_preparing_jobs),',
       "dur_cap__admission_reserves_preparation_capacity"),
    _m("journal_reservation_unbounded", "the global journal reservation budget",
       S, "        if self.total() + want > self.limits.journal_total_bytes:", "        if False:",
       "dur_cap__journal_reservation_must_fit_the_global_budget"),
    _m("journal_per_job_unbounded", "the per-job journal ceiling",
       S, "        if stored > ceiling:", "        if False:",
       "dur_cap__a_job_cannot_store_past_its_journal_reservation"),
    _m("settlement_checks_capacity_too_late", "capacity is checked before money moves (R39)",
       S, "        if self.stream is not None:\n            # r1 R39", "        if False:\n            # r1 R39",
       "dur_settle__a_settlement_that_cannot_journal_moves_no_money"),
    _m("journal_bytes_not_released", "terminalization frees the unused reservation",
       S, "        self.reserved.pop(job_id, None)", "        pass",
       "dur_cap__stored_unexpired_bytes_keep_counting",
       "dur_settle__terminalization_releases_every_reservation"),
    _m("reservations_stay_active", "terminalization deactivates every reservation row",
       S, "            job.reservations[kind] = reservation.model_copy(update={\"active\": False})",
       "            pass", "dur_settle__terminalization_releases_every_reservation",
       "dur_cap__admission_reserves_preparation_capacity"),
    # --- fencing --------------------------------------------------------------
    _m("fence_ignores_the_worker", "the lease owner is part of the fence",
       S, 'raise errors.StaleLease(f"lease belongs to {job.lease.worker_id}")', "pass",
       "dur_fence__another_worker_at_the_same_generation_is_still_fenced"),
    _m("fence_ignores_the_generation", "generation fencing",
       S, 'raise errors.StaleLease(f"generation {lease.generation} != {job.generation}")', "pass",
       "dur_fence__a_stale_generation_is_rejected", "dur_fence__a_stale_worker_cannot_append"),
    _m("fence_ignores_expiry", "an expired lease mutates nothing",
       S, 'raise errors.StaleLease(f"lease expired at {job.lease.expires_at}")', "pass",
       "dur_fence__an_expired_lease_can_neither_renew_nor_settle",
       "dur_fence__a_stale_worker_cannot_append", occurrences=2),
    _m("heartbeat_stores_the_callers_lease", "a lease is a fencing token, not a record (R29)",
       S, "            job.lease = job.lease.model_copy(update={",
       "            job.lease = lease.model_copy(update={",
       "dur_fence__a_lease_is_a_fencing_token_not_a_record"),
    # R46 split the fence in two, so each enforcement point has its own anchor: the
    # shared two-line form matched both and the mutant edited whichever came first.
    _m("deadlines_do_not_bind_mutations", "deadlines bind every fenced mutation (R29)",
       S, "        self._enforce_deadlines(job)\n"
          "        if self.clock.now() >= job.lease.expires_at:\n"
          '            raise errors.StaleLease(f"lease expired at {job.lease.expires_at}")\n'
          "        return job",
       "        if self.clock.now() >= job.lease.expires_at:\n"
          '            raise errors.StaleLease(f"lease expired at {job.lease.expires_at}")\n'
          "        return job",
       "dur_fence__a_lease_is_a_fencing_token_not_a_record"),
    _m("preparation_deadline_unenforced", "a dead preparation worker frees its job (R29)",
       S, "            # worker cannot pick up something nobody is waiting for any more.\n"
          "            self._enforce_deadlines(job)",
       "            # worker cannot pick up something nobody is waiting for any more.",
       "dur_output__a_late_preparation_worker_finds_a_terminal_job"),
    # --- phase deadlines (R20) ------------------------------------------------
    _m("phase_deadline_uncapped", "no phase instant outlives deadline_at (R20)",
       S, "    return min(now + timedelta(seconds=budget_s), deadline_at)",
       "    return now + timedelta(seconds=budget_s)",
       "dur_output__no_phase_deadline_outlives_the_accepted_deadline",
       "dur_output__the_absolute_deadline_bounds_recovery"),
    _m("generation_deadline_unenforced", "the generation deadline ends an attempt (R20)",
       S, "        if (job.state is JobState.running and job.lease is not None\n"
          "                and now >= job.lease.generation_deadline_at):",
       "        if (job.state is JobState.running and job.lease is not None\n"
          "                and False):",
       "dur_output__the_generation_deadline_ends_a_running_attempt"),
    _m("claim_ignores_the_queue_deadline", "a job past its queue deadline is not claimable",
       S, "            if job.queue_deadline_at is not None and now >= job.queue_deadline_at:",
       "            if False:", "dur_output__a_job_past_its_queue_budget_is_not_claimable"),
    _m("budgets_read_from_config", "an accepted job keeps its admission budgets (R4)",
       S, "if now >= job.queue_deadline_at:",
       "if now >= min(job.queued_at or now, now) + timedelta(\n"
       "                    seconds=self.limits.queue_wait_interactive_s):",
       "dur_output__an_accepted_job_keeps_its_admission_budgets"),
    _m("abandoned_preparation_never_reaped", "a preparation that never returns is reaped",
       S, "        if job.state is JobState.preparing and now >= job.admission.preparation_deadline_at:",
       "        if False:", "dur_output__a_late_preparation_worker_finds_a_terminal_job", occurrences=2),
    # --- output and recovery --------------------------------------------------
    _m("publication_marker_never_set", "output committed forbids regeneration",
       S, "                job.published = True", "                pass",
       "dur_output__the_first_append_sets_the_publication_marker",
       "dur_output__loss_after_publication_is_a_terminal_failure"),
    _m("regenerates_after_publication", "a lost published attempt fails honestly",
       S, "            if job.published:", "            if False:",
       "dur_output__loss_after_publication_is_a_terminal_failure",
       "dur_output__the_first_append_sets_the_publication_marker"),
    _m("retries_unbounded", "prepublication retries are capped",
       S, "            if job.attempts >= self.limits.max_prepublication_retries:", "            if False:",
       "dur_output__prepublication_retries_are_bounded"),
    _m("worker_appends_a_terminal_event", "only settlement writes a terminal event (R30)",
       S, 'raise errors.InvalidRequest("a worker may not append a terminal event")', "pass",
       "dur_output__a_worker_cannot_forge_a_terminal_event"),
    _m("finalize_trusts_the_caller", "the terminal event comes from the stored outcome (R30)",
       S, "        if outcome != job.outcome:", "        if False:",
       "dur_output__a_worker_cannot_forge_a_terminal_event"),
    _m("finalize_after_expiry_rewrites", "an expired journal is gone, not re-minted (R30)",
       S, '            raise errors.JournalExpired(f"journal for job {job.id} has expired")',
       "            pass", "dur_output__a_worker_cannot_forge_a_terminal_event"),
    _m("terminal_event_not_written", "the terminal event belongs to the settling transaction",
       S, "            self.stream.write_terminal(job, job.outcome)", "            pass",
       "dur_settle__the_terminal_event_belongs_to_the_settling_transaction"),
    _m("oversize_event_journalled", "an oversize event is refused, never truncated",
       S, "                    raise errors.JournalWriteFailed(\n"
          "                        f\"event of {size} bytes exceeds {self.limits.journal_event_max_bytes}\")",
       "                    pass", "dur_output__an_oversize_event_is_refused"),
    _m("replay_gap_hidden", "a pruned prefix is an explicit gap",
       S, 'raise errors.ReplayGap(f"events up to {pruned_to} are no longer retained")', "pass",
       "dur_output__a_pruned_prefix_is_an_explicit_replay_gap"),
    _m("expired_journal_served", "an expired journal is 410",
       S, 'raise errors.JournalExpired(f"journal for {job_handle} has expired")', "pass",
       "dur_output__an_expired_journal_is_gone_not_regenerated"),
    _m("cursor_past_the_head_accepted", "a cursor the journal never issued is invalid",
       S, 'raise errors.InvalidCursor(f"cursor {cursor.token} is past the last event")', "pass",
       "api_stream__a_cursor_past_the_head_is_invalid"),
    _m("read_limit_unbounded", "replay is bounded",
       S, '            raise errors.InvalidRequest(f"limit must be a positive integer, not {limit!r}")',
       "            limit = 100", "api_stream__reads_are_bounded_and_ownership_checked"),
    _m("append_relays_before_commit", "append commits before it relays",
       S, "                stored.append(chunk)\n                committed.append(chunk)",
       "                committed.append(chunk)",
       "dur_output__append_commits_before_it_relays",
       "dur_output__cursors_are_generation_then_sequence"),
    # --- settlement -----------------------------------------------------------
    _m("debit_rounds_up", "one half-up rounding per debit",
       MONEY, "def half_up(value: Decimal) -> Decimal:\n    \"\"\"The single rounding a final customer debit gets.\"\"\"\n    return _q(value, decimal.ROUND_HALF_UP)",
       "def half_up(value: Decimal) -> Decimal:\n    \"\"\"The single rounding a final customer debit gets.\"\"\"\n    return _q(value, decimal.ROUND_CEILING)",
       "dur_settle__the_store_rounds_half_up_once"),
    _m("debit_rounds_down", "one half-up rounding per debit",
       MONEY, "    return _q(value, decimal.ROUND_HALF_UP)\n\n\ndef ceiling",
       "    return _q(value, decimal.ROUND_DOWN)\n\n\ndef ceiling",
       "dur_settle__the_store_rounds_half_up_once"),
    _m("envelope_checked_by_amount_only", "the token envelope, not just the hold",
       S, "            if over_envelope or candidate > hold.amount:", "            if candidate > hold.amount:",
       "dur_settle__usage_beyond_the_reserved_envelope_is_a_platform_failure"),
    _m("envelope_not_checked", "usage beyond the envelope is a platform failure",
       S, "        over_envelope = usage is not None and (", "        over_envelope = False and (",
       "dur_settle__usage_beyond_the_reserved_envelope_is_a_platform_failure"),
    _m("settles_any_cause", "only three causes can charge (R21)",
       R, "BILLABLE_CAUSES = frozenset({\n    TerminalCause.completed, TerminalCause.client_cancelled, TerminalCause.client_disconnected,\n})",
       "BILLABLE_CAUSES = frozenset(set(TerminalCause) - {TerminalCause.invalid_media})",
       "dur_settle__only_three_causes_can_charge"),
    _m("unknown_usage_released_at_once", "published tokens with unknown usage reconcile (R21)",
       S, "        if usage is None and job.published:",
       "        if usage is None and job.published and cause in BILLABLE_CAUSES:",
       "dur_output__loss_after_publication_is_a_terminal_failure"),
    _m("cancel_after_publication_is_free", "cancelling after publication reconciles",
       S, "        if usage is None and job.published:", "        if False:",
       "dur_settle__cancelling_after_publication_reconciles",
       "dur_settle__unknown_usage_is_held_then_released_as_platform_absorbed"),
    _m("held_hold_released_early", "an unknown-usage hold waits the fenced 24h",
       S, "            if not (job.terminal and fenced and now >= hold.reconcile_after):",
       "            if not (job.terminal and fenced):",
       "dur_settle__unknown_usage_is_held_then_released_as_platform_absorbed",
       "dur_settle__an_unknown_usage_hold_is_never_released_on_a_callers_clock"),
    _m("second_settlement_accepted", "exactly one settlement per job",
       S, "            if job.terminal:\n                proposal = (outcome.cause, outcome.usage, outcome.result_ref)",
       "            if False:\n                proposal = (outcome.cause, outcome.usage, outcome.result_ref)",
       "dur_settle__duplicate_completion_is_idempotent_then_conflicts",
       "dur_settle__the_winning_worker_can_always_replay_its_completion"),
    _m("outcome_settles_another_job", "an outcome settles only its own job (R10)",
       S, 'raise errors.InvalidRequest("the outcome does not belong to the leased job")', "pass",
       "dur_settle__an_outcome_settles_only_its_own_job"),
    _m("succeeded_without_a_result", "a succeeded outcome needs a result_ref (R30)",
       S, 'raise errors.InvalidRequest("a succeeded outcome requires a result reference")', "pass",
       "dur_settle__a_succeeded_outcome_needs_a_result_reference"),
    _m("money_moves_before_validation", "a rejected settlement moves no money",
       S, 'raise errors.StateConflict(f"cause {cause} cannot carry state {state}")', "pass",
       "dur_settle__a_rejected_settlement_moves_no_money"),
    _m("free_success_without_usage", "a delivered success needs authoritative usage",
       S, "            cause, state, result_ref = TerminalCause.engine_incomplete, JobState.failed, None",
       "            pass", "dur_settle__a_delivered_success_needs_authoritative_usage"),
    _m("cancel_loses_the_committed_outcome", "a completed job stays completed",
       S, "            if job.terminal:\n                # Completion won the race; a completed job stays completed.\n                return job.outcome",
       "            if False:\n                return job.outcome",
       "dur_settle__cancel_and_complete_race_has_a_single_winner",
       "dur_settle__stale_and_out_of_order_transitions_are_typed_conflicts"),
    _m("outbox_not_emitted", "every transition emits its projection",
       S, "        self._emit(job.id, OutboxKind.usage_projection, now,\n"
          "                   {\"request_id\": job.id, \"settlement_state\": settlement.value})",
       "        pass", "dur_outbox__every_transition_emits_its_projection"),
    _m("grant_accepts_a_negative_amount", "a credit grant is positive (R11)",
       S, 'amount = money_input(amount, "a credit grant")', "amount = money.parse(amount)",
       "dur_cap__a_credit_grant_is_never_negative"),
    # --- scheduler ------------------------------------------------------------
    _m("enqueue_duplicates", "a replayed event indexes one candidate",
       Q, "        if event.event_id in self.pending or event.event_id in self.inflight \\\n"
          "                or event.event_id in self.acknowledged:\n            return False",
       "        if False:\n            return False", "dur_outbox__enqueue_is_replay_safe",
       "dur_outbox__acknowledged_candidates_do_not_come_back"),
    _m("visibility_from_the_event_time", "index visibility is measured from the claim",
       Q, "            if now >= claimed_at + timedelta(seconds=self.limits.lease_ttl_s):",
       "            if now >= event.available_at + timedelta(seconds=self.limits.lease_ttl_s):",
       "dur_outbox__a_lost_worker_returns_its_candidate"),
    _m("rebuild_loses_jobs", "rebuild restores every queued job exactly once",
       Q, "        self.pending = {event.event_id: event for event in snapshot}",
       "        self.pending = {}",
       "dur_outbox__rebuild_restores_every_queued_job_exactly_once"),
    # --- media ----------------------------------------------------------------
    _m("upload_digest_trusted", "a finalized upload's digest is verified",
       M, "        digest = digest_of(upload.data)", "        digest = upload.declared_digest or digest_of(upload.data)",
       "media_sec__a_refused_upload_stays_refused"),
    _m("upload_size_unchecked", "an oversize upload is refused",
       M, "        if len(upload.data) > upload.max_bytes:", "        if False:",
       "media_sec__oversize_and_unsupported_uploads_are_refused"),
    _m("upload_type_unchecked", "the media type allow-list",
       M, "        if upload.mime not in upload.accepted_mime:", "        if False:",
       "media_sec__oversize_and_unsupported_uploads_are_refused"),
    _m("aborted_upload_refinalized", "a refused upload stays refused",
       M, '            raise errors.Conflict(f"upload {upload_handle} is {upload.state}")', "            pass",
       "media_sec__a_refused_upload_stays_refused"),
    _m("upload_expiry_ignored", "an expired upload window says so (R22)",
       M, '            raise errors.UploadExpired("the upload window closed before completion")',
       "            pass", "media_sec__an_expired_upload_window_says_so"),
    _m("caller_shapes_its_constraints", "a caller never raises its own ceiling",
       M, "        if max_bytes <= 0 or max_bytes > self.limits.max_media_bytes:", "        if False:",
       "media_sec__oversize_and_unsupported_uploads_are_refused"),
    _m("mime_string_accepted", "accepted_mime is a list, not a string or a number",
       M, "        if not isinstance(raw_mimes, (list, tuple, set, frozenset, str, bytes)):",
       "        if False:", "media_sec__a_refused_upload_stays_refused",
       dies_by=("TypeError",)),
    _m("mime_string_split_into_characters", "a single type must still be a list",
       M, "        if isinstance(raw_mimes, (str, bytes)):", "        if False:",
       "media_sec__a_refused_upload_stays_refused"),
    _m("cross_tenant_media_resolved", "media is tenant scoped",
       M, "        media = self.objects.get((org_id, ref))",
       "        media = next((value for (_o, handle), value in self.objects.items()\n"
       "                      if handle == ref), None)",
       "media_sec__another_org_cannot_resolve_or_finalize"),
    # F2R item 4: `stage` records nothing of the caller's any more (it takes only refs the
    # store produced), so "commits as it goes" / "resolves after writing" have no code left
    # to break; what they guarded is now these two refusals.
    _m("staging_overwrites_content", "a handle keeps the content the store has (F2R 4)",
       M, "            if existing is None or existing.digest != ref.digest:\n"
          "                raise errors.NotFound(f\"media {ref.handle} was not materialized",
       "            if existing is None:\n"
       "                raise errors.NotFound(f\"media {ref.handle} was not materialized",
       "media_sec__staging_never_replaces_an_existing_object",
       "media_sec__a_partial_request_stages_nothing"),
    _m("staging_takes_a_ref_it_never_made", "only a ref the store produced is staged (F2R 4)",
       M, "            if existing is None or existing.digest != ref.digest:\n"
          "                raise errors.NotFound(f\"media {ref.handle} was not materialized",
       "            if False:\n"
       "                raise errors.NotFound(f\"media {ref.handle} was not materialized",
       "media_sec__a_partial_request_stages_nothing"),
    _m("attach_takes_a_ref_it_never_made", "only a ref the store produced is attached (F2R 4)",
       M, "            if indexed is None or indexed.digest != ref.digest:", "            if False:",
       "media_sec__a_partial_request_stages_nothing"),
    _m("staging_validates_nothing_up_front", "staging is all or nothing",
       M, "        for ref in request.media:\n            # No separate `ref.org_id` check",
       "        for ref in ():\n            # No separate `ref.org_id` check",
       "media_sec__a_partial_request_stages_nothing",
       "media_sec__a_foreign_media_reference_is_not_staged"),
    _m("staging_ignores_the_org", "a body cannot name another tenant's object",
       M, "            existing = self.objects.get((org_id, ref.handle))",
       "            existing = next((value for (_o, handle), value in self.objects.items()\n"
       "                             if handle == ref.handle), None)",
       "media_sec__a_foreign_media_reference_is_not_staged",
       "media_sec__a_partial_request_stages_nothing"),
    _m("finalize_overwrites_the_tenants_object", "finalizing replaces nothing",
       M, '            raise errors.Conflict(f"handle {upload_handle} already holds different content")',
       "            pass", "media_sec__staging_never_replaces_an_existing_object"),
    # --- traces ---------------------------------------------------------------
    _m("capture_bytes_not_charged", "capture bytes are charged as they accumulate (R27)",
       T, "        self.sink.content_bytes += size\n        self.content_bytes += size",
       "        self.content_bytes += size",
       "trace_bounds__concurrent_captures_share_one_budget"),
    _m("capture_keeps_partial_content", "a breach discards the whole capture (R27)",
       T, "            self._discard(TraceLossReason.memory_budget)\n            return False",
       "            return True", "trace_bounds__concurrent_captures_share_one_budget"),
    _m("abandon_keeps_its_bytes", "abandon releases its bytes (R27)",
       T, "            # coverage figures entirely (02).\n            return\n        self._discard(reason)",
       "            # coverage figures entirely (02).\n            return\n        return",
       "trace_bounds__an_abandoned_capture_releases_its_bytes",
       "trace_bounds__an_open_capture_past_its_deadline_is_reaped"),
    _m("capture_accepts_any_envelope", "a capture belongs to its own request",
       T, "        if (envelope.request_id, envelope.org_id) != (self.request_id, self.org_id):",
       "        if False:", "trace_bounds__a_capture_belongs_to_its_own_request", occurrences=2),
    _m("add_has_no_running_total", "the content budget is a running total",
       T, "        if self.sink.content_bytes + size > self.sink.content_budget:",
       "        if size > self.sink.content_budget:",
       "trace_bounds__a_content_budget_breach_discards_the_whole_content",
       "trace_bounds__concurrent_captures_share_one_budget"),
    _m("finish_accepts_uncharged_content", "content that skipped the accounting is refused",
       T, "        if not self.lost and envelope.content_bytes > charged:", "        if False:",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope"),
    _m("queue_ceiling_ignored", "the queued-record ceiling drops rather than blocking",
       T, "TraceLossReason.queue_full if len(self.queued) >= self.limits.trace_queue_max",
       "TraceLossReason.queue_full if False",
       "trace_bounds__a_full_queue_drops_and_inference_continues",
       "trace_bounds__a_dropped_finish_releases_its_charge"),
    _m("metadata_reserve_ignored", "metadata exhaustion drops with counters",
       T, "                       if self.metadata_bytes + metadata\n"
          "                       > self.limits.trace_metadata_reserve_bytes else None):",
       "                       if False else None):",
       "trace_bounds__metadata_exhaustion_drops_with_counters"),
    _m("off_mode_offer_queued", "an off-mode request produces no trace row (R27)",
       T, "        if envelope.mode is TraceMode.off:", "        if False:",
       "trace_bounds__off_mode_produces_no_trace_at_all"),
    _m("minimal_content_queued", "minimal mode never carries content (R12)",
       T, "        if envelope.carries_content:\n            # r1 R27: content is charged",
       "        if envelope.carries_content and envelope.mode is TraceMode.full:\n"
       "            # r1 R27: content is charged",
       "trace_bounds__minimal_mode_never_carries_content"),
    _m("fsync_claimed_at_flush", "durability begins at fsync",
       T, "        if (now - self._last_fsync).total_seconds() >= self.limits.trace_fsync_interval_s:",
       "        if True:", "trace_bounds__in_memory_appended_and_fsynced_are_separate_states"),
    # --- r1 R46: preparation fencing and work loading --------------------------
    _m("prepared_is_unfenced", "prepared is fenced on the preparation lease (R46)",
       S, "            job = self._fence_preparation(lease)\n"
          "            for ref in media:",
       "            job = self.jobs[lease.job_id]\n"
          "            for ref in media:",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("preparation_generation_ignored", "a stale preparation generation is fenced (R46)",
       S, "        if job.preparation_generation != lease.generation:",
       "        if False:",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("preparation_owner_ignored", "a preparation lease names its worker (R46)",
       S, "        if job.preparation_lease.worker_id != lease.worker_id:",
       "        if False:",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("preparation_expiry_ignored", "an expired preparation lease fences nothing (R46)",
       S, "        if self.clock.now() >= job.preparation_lease.expires_at:",
       "        if False:",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("preparation_lease_kind_ignored", "the two lease kinds are not interchangeable (R46)",
       S, "        if lease.kind is not LeaseKind.preparation:\n"
          "            raise errors.StaleLease(f\"{lease.kind} lease cannot fence preparation\")",
       "        pass",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("inference_lease_kind_ignored", "a preparation lease cannot fence execution (R46)",
       S, "        if lease.kind is not LeaseKind.inference:\n"
          "            # r1 R46: the two attempt sequences have separate counters, so a preparation\n"
          "            # lease at generation 1 would otherwise pass as inference generation 1.\n"
          "            raise errors.StaleLease(f\"{lease.kind} lease cannot fence execution\")",
       "        pass",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("two_preparation_workers_at_once", "one live preparation lease per job (R46)",
       S, "            if live is not None and now < live.expires_at:",
       "            if False:",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("lost_preparation_is_never_reaped", "recover reaps a lost preparation worker (R46)",
       S, "        if (job.state is JobState.preparing and job.preparation_lease is not None\n"
          "                and now >= job.preparation_lease.expires_at):",
       "        if False:",
       "dur_output__a_lost_preparation_worker_is_reaped_within_bounds"),
    _m("load_work_is_unfenced", "load_work hands out nothing to a fenced lease (R46)",
       S, "        return (self._fence_preparation(lease) if lease.kind is LeaseKind.preparation\n"
          "                else self._fence(lease))",
       "        return self.jobs[lease.job_id]",
       "dur_fence__load_work_is_fenced_and_hands_out_nothing_otherwise"),
    _m("load_work_hides_the_prepared_refs", "load_work carries what preparation produced (R46)",
       S, "                        prepared_refs=job.prepared,\n", "                        prepared_refs=(),\n",
       "dur_fence__load_work_is_fenced_and_hands_out_nothing_otherwise"),
    # --- feedback -------------------------------------------------------------
    _m("feedback_operator_role_from_session", "accept always records customer (R31)",
       F, "            author_role=AuthorRole.customer,",
       "            author_role=AuthorRole.operator if auth.is_operator else AuthorRole.customer,",
       "feedback_ack__a_client_cannot_forge_provenance"),
    _m("calibration_open_to_customers", "calibration labels are operator only (R31)",
       F, '            raise errors.Forbidden("labelling a calibration set requires a platform operator")',
       "            pass", "feedback_ack__an_operator_may_label_a_calibration_set"),
    _m("calibration_not_idempotent", "a calibration label is idempotent",
       F, "        replay = self._replay(idem, LABEL, auth)\n        if replay is not None:\n"
          "            return replay",
       "        pass", "feedback_ack__an_operator_may_label_a_calibration_set"),
    # --- B4: the eight invariants whose mutants the reviewer found surviving --------
    _m("price_read_from_the_request_again", "the price is never read from the request (q30)",
       S, "        snapshot = self.price_for(request.model_revision, now)",
       "        carried = (request.parameters or {}).get(\"price_snapshot\")\n"
       "        snapshot = (PriceSnapshot.model_validate(carried) if carried\n"
       "                    else self.price_for(request.model_revision, now))",
       "dur_admit__a_refused_admission_reserves_nothing"),
    _m("prepared_takes_foreign_media", "prepared refs are the job's own tenant's (q22)",
       S, '                    raise errors.Forbidden("prepared media must belong to the job\'s org")',
       "                    pass",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("prepare_falls_back_to_any_job", "prepare resolves this job's refs or nothing (q23)",
       M, "        sources = self.by_job.get(job_id)\n"
          "        if sources is None:\n"
          '            raise errors.NotFound(f"no staged media for job {job_id}")',
       "        sources = self.by_job.get(job_id)\n"
          "        if sources is None:\n"
          "            sources = next(iter(self.by_job.values()), None)\n"
          "        if sources is None:\n"
          '            raise errors.NotFound(f"no staged media for job {job_id}")',
       "media_parity__staging_is_content_addressed_and_tenant_namespaced"),
    _m("load_work_reports_current_budgets", "load_work carries the R4 budgets (q16)",
       S, "                        price_snapshot=job.admission.price_snapshot, budgets=job.budgets)",
       "                        price_snapshot=job.admission.price_snapshot,\n"
          "                        budgets=Budgets.of(self.limits, job.request.execution_mode))",
       "dur_fence__load_work_is_fenced_and_hands_out_nothing_otherwise"),
    # Three mutants were **removed** in the R52/R54 pass rather than forced, because the
    # defects they described stopped being representable:
    #
    # * `heartbeat_renews_a_preparation_lease` - R52 makes `heartbeat` renew one, so
    #   "nothing renews it" is no longer the contract. `preparation_heartbeat_does_not_renew`
    #   and `preparation_heartbeat_buys_phase_time` replace it.
    # * `prepared_ignores_the_phase_deadline` - R52 clamps a preparation lease to
    #   `preparation_deadline_at`, so a live lease can never be past it and the lease-expiry
    #   refusal always fires first. `preparation_lease_outlives_its_phase` kills the clamp.
    # * `preparation_retries_unbounded` - R52 has `recover` terminalize after the last
    #   permitted loss, so the count check in `claim_preparation` is now the second line of
    #   defence and no case can observe its absence.
    #   `exhausted_preparation_is_redispatched` kills the reaper's own decision.
    #
    # Keeping an unkillable mutant would fail the run; keeping it *and* weakening a case to
    # kill it would be the false kill R40 forbids.
    # --- r1 round-3: the six non-equivalent survivors the reviewer found -----------
    _m("preparation_heartbeat_skips_the_fence", "a superseded worker renews nothing (s13)",
       S, "                job = self._fence_preparation(lease)\n"
          "                job.preparation_lease = job.preparation_lease.model_copy(update={",
       "                job = self.jobs[lease.job_id]\n"
          "                job.preparation_lease = job.preparation_lease.model_copy(update={",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("hold_rounds_half_up", "the maximum hold rounds up, never half up (s01)",
       MONEY, "def maximum_hold(max_input_tokens: int, max_output_tokens: int, input_rate: Decimal, output_rate: Decimal) -> Decimal:",
       "def maximum_hold(max_input_tokens: int, max_output_tokens: int, input_rate: Decimal, output_rate: Decimal) -> Decimal:\n"
       "    return half_up(cost(max_input_tokens, max_output_tokens, input_rate, output_rate))",
       "dur_cap__the_hold_rounds_up_never_half_up"),
    _m("replay_rederives_the_hold", "a replay reports the original hold and price (s05)",
       S, '        return self._snapshot(job).model_copy(update={"replayed": True})',
       "        current = self.price_for(job.request.model_revision, now)\n"
          '        return self._snapshot(job).model_copy(update={"replayed": True,\n'
          '            "price_snapshot": current or job.admission.price_snapshot,\n'
          "            \"maximum_hold\": self._derive_hold(job.request,\n"
          "                                              current or job.admission.price_snapshot)})",
       "dur_admit__a_replay_reports_the_original_hold_and_price"),
    _m("replay_refreshes_the_admitted_at", "a replay reports the original admission (t15)",
       S, '        return self._snapshot(job).model_copy(update={"replayed": True})',
       '        return self._snapshot(job).model_copy(update={"replayed": True,\n'
          '                                                     "admitted_at": now})',
       "dur_admit__a_replay_reports_the_original_hold_and_price"),
    _m("preparation_retries_unbounded", "preparation retries are bounded (q08)",
       S, "            if job.preparation_attempts > self.limits.max_prepublication_retries:",
       "            if False:",
       "dur_output__a_lost_preparation_worker_is_reaped_within_bounds", occurrences=2),
    _m("requeue_labelled_for_the_wrong_phase", "a requeue carries its own phase's kind (s18)",
       S, "                               kind=OutboxKind.inference_dispatch,\n"
          "                               execution_mode=job.request.execution_mode, available_at=now,",
       "                               kind=OutboxKind.prepare_dispatch,\n"
          "                               execution_mode=job.request.execution_mode, available_at=now,",
       "dur_output__every_requeued_candidate_carries_the_right_kind"),
    _m("lost_inference_emits_a_prepare_dispatch", "a lost attempt dispatches for its own phase (s18)",
       S, '            self._emit(job.id, OutboxKind.inference_dispatch, now, {"request_id": job.id,\n'
          '                                                                    "attempt": job.attempts})',
       '            self._emit(job.id, OutboxKind.prepare_dispatch, now, {"request_id": job.id,\n'
          '                                                               "attempt": job.attempts})',
       "dur_output__every_requeued_candidate_carries_the_right_kind"),
    # --- r1 R55: untrusted store inputs -----------------------------------------
    _m("attach_trusts_a_caller_supplied_org", "attach reads the org from the job row (R55)",
       M, "        org_id = self.job_org(job_id)", '        org_id = refs[0].org_id if refs else ""',
       "media_parity__staging_is_content_addressed_and_tenant_namespaced"),
    _m("attach_stores_before_validating", "a refused attach stores nothing (R55/s15)",
       M, "        org_id = self.job_org(job_id)\n        owned: list[MediaRef] = []",
       "        org_id = self.job_org(job_id)\n        self.by_job[job_id] = tuple(refs)\n"
       "        owned: list[MediaRef] = []",
       "media_parity__staging_is_content_addressed_and_tenant_namespaced"),
    _m("admit_accepts_a_zero_output_ceiling", "a zero ceiling never means a free request (R55)",
       S, "        if not 1 <= request.max_output_tokens <= limits.max_output_tokens:",
       "        if request.max_output_tokens > limits.max_output_tokens:",
       "dur_admit__the_token_ceilings_are_range_checked"),
    _m("admit_drops_the_output_ceiling_upper_bound", "the output ceiling has an upper bound (t05)",
       S, "        if not 1 <= request.max_output_tokens <= limits.max_output_tokens:",
       "        if not 1 <= request.max_output_tokens:",
       "dur_admit__the_token_ceilings_are_range_checked"),
    _m("admit_accepts_a_zero_input_ceiling", "an input ceiling is at least one (R55)",
       S, "        if request.max_input_tokens < 1:", "        if False:",
       "dur_admit__the_token_ceilings_are_range_checked"),
    _m("admit_accepts_a_request_past_the_context_cap", "the ceilings fit the context (R55)",
       S, "        if total > limits.max_context_tokens:", "        if False:",
       "dur_admit__the_token_ceilings_are_range_checked"),
    _m("claim_candidate_swallows_an_unknown_kind", "an unknown kind is typed (R55)",
       Q, "        if kind is not None and kind not in DISPATCH_KINDS:", "        if False:",
       "dur_outbox__a_candidate_carries_its_dispatch_kind"),
    _m("operator_author_without_the_marker", "an operator author is marked (R55)",
       F, "            author_principal=auth.principal, author_role=AuthorRole.operator,\n"
          "            by_operator=True,                # r1 R50: a label is always an operator's",
       "            author_principal=auth.principal, author_role=AuthorRole.operator,\n"
          "            by_operator=bool(auth.role and False),  # r1 R50",
       "feedback_ack__an_operator_may_label_a_calibration_set"),
    # --- r1 R55 / B1: the phase deadline is enforced before lease expiry -----------
    # `prepared_ignores_the_phase_deadline` was removed in the R52 pass as
    # "unrepresentable". It was not: R52's clamp made the *expiry* check fire first, so
    # `_enforce_deadlines` became dead code and the invariant regressed. That is what made
    # the mutant unkillable, and it is restored here with the case that shows the
    # difference - deleting the call must fail, and so must putting expiry back in front.
    _m("prepared_ignores_the_phase_deadline", "the phase deadline binds prepared itself (R29/R55)",
       S, "        # r1 R55/R29: the phase first, because a clamped lease expires with it.\n"
          "        self._enforce_deadlines(job)\n"
          "        if self.clock.now() >= job.preparation_lease.expires_at:",
       "        if self.clock.now() >= job.preparation_lease.expires_at:",
       "dur_output__a_heartbeating_preparation_worker_is_terminalized_on_time"),
    _m("inference_expiry_checked_before_the_deadline", "the deadline goes first, both paths (t02)",
       S, "        self._enforce_deadlines(job)\n"
          "        if self.clock.now() >= job.lease.expires_at:\n"
          '            raise errors.StaleLease(f"lease expired at {job.lease.expires_at}")\n'
          "        return job",
       "        if self.clock.now() >= job.lease.expires_at:\n"
          '            raise errors.StaleLease(f"lease expired at {job.lease.expires_at}")\n'
          "        self._enforce_deadlines(job)\n        return job",
       "dur_fence__an_overdue_inference_lease_terminalizes_in_the_same_call"),
    _m("preparation_expiry_checked_before_the_deadline", "the deadline goes first (R55)",
       S, "        # r1 R55/R29: the phase first, because a clamped lease expires with it.\n"
          "        self._enforce_deadlines(job)\n"
          "        if self.clock.now() >= job.preparation_lease.expires_at:\n"
          '            raise errors.StaleLease(f"preparation lease expired at "\n'
          '                                    f"{job.preparation_lease.expires_at}")\n'
          "        return job",
       "        if self.clock.now() >= job.preparation_lease.expires_at:\n"
          '            raise errors.StaleLease(f"preparation lease expired at "\n'
          '                                    f"{job.preparation_lease.expires_at}")\n'
          "        self._enforce_deadlines(job)\n        return job",
       "dur_output__a_heartbeating_preparation_worker_is_terminalized_on_time"),
    # --- r1 R52: the preparation lease, the tenant check and the dispatch kind ---
    _m("preparation_lease_uses_the_inference_ttl", "preparation has its own short TTL (R52)",
       S, "                expires_at=min(now + timedelta(seconds=self.limits.preparation_lease_ttl_s),\n"
          "                               job.admission.preparation_deadline_at),",
       "                expires_at=min(now + timedelta(seconds=self.limits.lease_ttl_s),\n"
          "                               job.admission.preparation_deadline_at),",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution",
       "dur_output__a_lost_preparation_worker_is_reaped_within_bounds"),
    _m("preparation_lease_outlives_its_phase", "a preparation lease never outlives the phase (R52)",
       S, "                expires_at=min(now + timedelta(seconds=self.limits.preparation_lease_ttl_s),\n"
          "                               job.admission.preparation_deadline_at),",
       "                expires_at=now + timedelta(seconds=self.limits.preparation_lease_ttl_s),",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("preparation_heartbeat_does_not_renew", "a preparation lease renews (R52)",
       S, "                job.preparation_lease = job.preparation_lease.model_copy(update={\n"
          '                    "expires_at": min(\n'
          "                        now + timedelta(seconds=self.limits.preparation_lease_ttl_s),\n"
          "                        job.admission.preparation_deadline_at)})",
       "                pass",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("preparation_heartbeat_buys_phase_time", "a renewal never extends the phase (R52)",
       S, '                    "expires_at": min(\n'
          "                        now + timedelta(seconds=self.limits.preparation_lease_ttl_s),\n"
          "                        job.admission.preparation_deadline_at)})",
       '                    "expires_at": now + timedelta(\n'
          "                        seconds=self.limits.preparation_lease_ttl_s)})",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    _m("exhausted_preparation_is_redispatched", "a spent preparation is settled, not requeued (R52)",
       S, "            if job.preparation_attempts > self.limits.max_prepublication_retries:\n"
          "                # r1 R52: but once the retries are spent, redispatching would queue work",
       "            if False:\n"
          "                # r1 R52: but once the retries are spent, redispatching would queue work",
       "dur_output__a_lost_preparation_worker_is_reaped_within_bounds"),
    _m("attach_ignores_the_tenant", "attached media belongs to the job's org (R52)",
       M, "            if ref.org_id != org_id:\n"
          '                raise errors.NotFound("media attached to a job must belong to its org")',
       "            pass",
       "media_parity__staging_is_content_addressed_and_tenant_namespaced"),
    _m("candidates_ignore_the_dispatch_kind", "a candidate carries its kind (R52)",
       Q, "            if event.available_at <= now and kind in (None, event.kind):",
       "            if event.available_at <= now:",
       "dur_outbox__a_candidate_carries_its_dispatch_kind"),
    _m("index_accepts_any_outbox_kind", "the index carries dispatch kinds only (R52)",
       R, "        if self.kind not in DISPATCH_KINDS:",
       "        if False:",
       "dur_outbox__a_candidate_carries_its_dispatch_kind"),
    # --- r1 R49/R50/R54: replays, the operator marker and the projection --------
    _m("replay_returns_a_row_of_another_kind", "a replay never crosses operations (R54)",
       F, "        if entry.made_by != operation:\n"
          "            raise errors.IdempotencyConflict(\n"
          '                f"idempotency key {idem.key!r} already belongs to another operation")',
       "        pass",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("replay_is_not_projected", "a replay is projected like a read (R54)",
       F, "        projected = visible_feedback((stored,), operator=bool(auth.is_operator))\n"
          '        assert len(projected) == 1, "accept never stores a label, so nothing is filtered"\n'
          "        return projected[0]",
       "        return stored",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("accept_does_not_mark_the_operator", "by_operator is server-set on accept (R50)",
       F, "            by_operator=bool(auth.is_operator),", "            by_operator=False,",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("masking_keys_on_the_author_role", "masking keys on by_operator, not the role (R50)",
       R, "        if not operator and item.by_operator:",
       "        if not operator and item.author_role is AuthorRole.operator:",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("operator_list_owned_returns_labels", "no list_owned returns a label (R49)",
       R, "        if item.calibration_set:\n            continue",
       "        if item.calibration_set and not operator:\n            continue",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("wire_list_skips_the_projection", "a viewer list is built only through it (R54)",
       W, '        raise ValueError("build a FeedbackList through FeedbackList.for_viewer(...), so the "\n'
          '                         "R35/R41/R49/R50 projection cannot be skipped")',
       "        return data",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("public_entry_carries_the_marker", "the marker never leaves the service (R50)",
       W, "    calibration_set: bool = False\n    rubric_version: int | None = None\n\n"
          "    @classmethod\n    def of(cls, record: Feedback) -> FeedbackEntry:",
       "    calibration_set: bool = False\n    rubric_version: int | None = None\n"
          "    by_operator: bool = False\n\n"
          "    @classmethod\n    def of(cls, record: Feedback) -> FeedbackEntry:",
       "feedback_ack__calibration_labels_are_operator_data"),
    # The record's author/marker clauses: the fake never builds an inconsistent row, so
    # each is made reachable by a fake that stores one. The record must refuse it, which
    # is what the conformance case sees.
    _m("label_stored_as_a_customer", "a label is an operator's verdict (R54)",
       F, "            author_principal=auth.principal, author_role=AuthorRole.operator,",
       "            author_principal=auth.principal, author_role=AuthorRole.customer,",
       "feedback_ack__an_operator_may_label_a_calibration_set"),
    _m("label_stored_without_the_marker", "a label is made by an operator (R50)",
       F, "            by_operator=True,                # r1 R50: a label is always an operator's",
       "            by_operator=False,               # r1 R50: a label is always an operator's",
       "feedback_ack__an_operator_may_label_a_calibration_set"),
    # --- r1 R43: one persisted calibration shape, and who may read it ----------
    _m("accept_takes_a_calibration_label_name", "calibration_label is not an input name (R43)",
       W, "        if self.name not in FEEDBACK_INPUT_NAMES:\n"
          "            raise ValueError(f\"{self.name} is set by the server, not submitted by a client\")",
       "        pass",
       "feedback_ack__the_body_is_one_valid_signal_with_a_required_key"),
    _m("feedback_text_unbounded", "feedback text is bounded at 4000 characters (R43)",
       R, "    if isinstance(value, str) and len(value) > limits.MAX_FEEDBACK_TEXT_CHARS:",
       "    if False:",
       "feedback_ack__the_body_is_one_valid_signal_with_a_required_key"),
    _m("calibration_label_is_free_text", "a label comes from a closed vocabulary (R43)",
       F, "        if label not in tuple(CalibrationLabel):",
       "        if not isinstance(label, str) or not label.strip():",
       "feedback_ack__an_operator_may_label_a_calibration_set"),
    _m("calibration_rubric_unchecked", "a label carries a bounded integer rubric version (R43)",
       F, "        if isinstance(rubric_version, bool) or not isinstance(rubric_version, int) \\\n"
          "                or not MIN_RUBRIC_VERSION <= rubric_version <= MAX_RUBRIC_VERSION:",
       "        if False:",
       "feedback_ack__an_operator_may_label_a_calibration_set"),
    _m("calibration_comment_unbounded", "a label's comment is bounded too (R43)",
       F, "        if comment is not None and (not isinstance(comment, str)\n"
          "                                   or len(comment) > MAX_FEEDBACK_TEXT_CHARS):",
       "        if False:",
       "feedback_ack__an_operator_may_label_a_calibration_set"),
    # The record's coupling validator (`records.Feedback`) is load-bearing: these two
    # store a row whose calibration fields disagree, which the record must refuse.
    _m("label_stored_without_membership", "the three calibration fields are one fact (R43)",
       F, "            value=label, comment=comment, calibration_set=True,",
       "            value=label, comment=comment, calibration_set=False,",
       "feedback_ack__an_operator_may_label_a_calibration_set"),
    _m("label_stored_without_a_rubric", "a label without a rubric version is refused (R43)",
       F, "            rubric_version=rubric_version, created_at=now)",
       "            rubric_version=None, created_at=now)",
       "feedback_ack__an_operator_may_label_a_calibration_set"),
    _m("customer_list_shows_labels", "a customer never receives a calibration label (R35)",
       F, "        return visible_feedback(rows, operator=bool(auth.is_operator))",
       "        return rows",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("customer_list_shows_the_operator", "an operator principal is projected to platform (R41)",
       R, "            item = item.model_copy(update={\"author_principal\": PLATFORM_ACTOR})",
       "            item = item",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("wire_list_publishes_labels", "the wire list applies the same projection (R35/R49)",
       W, "        visible = visible_feedback(tuple(items), operator=operator)",
       "        visible = tuple(items)",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("calibration_list_open_to_customers", "the calibration list is operator only (R35)",
       F, '            raise errors.Forbidden("calibration labels are operator data")', "            pass",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("suspended_org_may_submit_feedback", "feedback from a suspended org is refused (R33)",
       F, '            raise errors.OrgSuspended(f"org {auth.org_id} is suspended")', "            pass",
       "feedback_ack__a_suspended_organization_cannot_submit_but_can_read"),
    _m("suspension_blocks_the_read_too", "a suspended org keeps every read (R33)",
       F, "        job = self.jobs.jobs.get(request_id)\n"
          "        if job is None or job.request.org_id != auth.org_id:\n"
          "            raise errors.NotFound(f\"no request {request_id} owned by org {auth.org_id}\")\n"
          "        rows = tuple(item for item in self.items.values()",
       "        job = self.jobs.jobs.get(request_id)\n"
          "        if job is None or job.request.org_id != auth.org_id:\n"
          "            raise errors.NotFound(f\"no request {request_id} owned by org {auth.org_id}\")\n"
          "        if self.is_suspended(auth.org_id):\n"
          "            raise errors.OrgSuspended(\"suspended\")\n"
          "        rows = tuple(item for item in self.items.values()",
       "feedback_ack__a_suspended_organization_cannot_submit_but_can_read"),
    _m("feedback_body_unvalidated", "one valid signal per body (R3)",
       F, '            raise errors.InvalidRequest("a feedback submission needs a name and a value")',
       "            body = {\"name\": \"comment\", \"value\": \"empty\"}",
       "feedback_ack__the_body_is_one_valid_signal_with_a_required_key"),
    _m("feedback_key_optional", "a feedback submission needs an idempotency key (R3)",
       F, '            raise errors.InvalidRequest("an idempotency key is required for feedback")',
       "            pass", "feedback_ack__the_body_is_one_valid_signal_with_a_required_key"),
    _m("feedback_cross_tenant", "feedback ownership comes from the durable job",
       F, "        if job is None or job.request.org_id != auth.org_id:\n"
          "            raise errors.NotFound(f\"no request {request_id} owned by org {auth.org_id}\")\n"
          "        if idem.org_id != auth.org_id:",
       "        if job is None:\n"
          "            raise errors.NotFound(f\"no request {request_id} owned by org {auth.org_id}\")\n"
          "        if False:",
       "feedback_ack__ownership_does_not_wait_for_the_projection"),
    _m("feedback_replay_makes_a_second_row", "a replayed submission survives once",
       F, "        replay = self._replay(idem, ACCEPT, auth)\n        if replay is not None:\n"
          "            return replay",
       "        pass",
       "feedback_ack__replay_is_idempotent_and_a_changed_payload_conflicts"),
    # --- judge ----------------------------------------------------------------
    _m("judge_settle_negative", "a judge cost is validated money (R11)",
       J, 'actual = money_input(actual, "the actual judge cost")', "actual = money.parse(actual)",
       "judge_budget__settlement_amounts_are_validated_money"),
    _m("judge_reserve_negative", "a reservation is validated money (R11)",
       J, 'max_cost = money_input(max_cost, "the worst-case judge cost")',
       "max_cost = money.parse(max_cost)",
       "judge_budget__settlement_amounts_are_validated_money"),
    _m("judge_settle_over_reservation", "the reservation is a hard maximum",
       J, "        if actual > run.reserved_cost:", "        if False:",
       "judge_budget__settlement_cannot_exceed_the_reservation"),
    _m("judge_budget_unbounded", "the live budget is a hard cap",
       J, "        if max_cost > self.available():", "        if False:",
       "judge_budget__reservations_include_outstanding_and_ambiguous_runs"),
    _m("judge_outstanding_excludes_ambiguous", "an ambiguous run keeps its reservation",
       J, "OUTSTANDING_STATES = frozenset({JudgeRunState.reserved, JudgeRunState.submitting,\n"
          "                                JudgeRunState.submitted, JudgeRunState.ambiguous,\n"
          "                                JudgeRunState.collecting, JudgeRunState.quarantined})",
       "OUTSTANDING_STATES = frozenset({JudgeRunState.reserved, JudgeRunState.submitting,\n"
          "                                JudgeRunState.submitted,\n"
          "                                JudgeRunState.collecting, JudgeRunState.quarantined})",
       "judge_budget__reservations_include_outstanding_and_ambiguous_runs"),
    _m("judge_dry_run_authorizes", "a dry run cannot authorize a submission",
       J, '            raise errors.BudgetExceeded("dry-run mode cannot authorize a live submission")',
       "            pass", "judge_budget__the_default_is_dry_run_with_no_authorization"),
    _m("judge_reserve_resets_a_run", "one reservation and one intent per run",
       J, "            return stored\n        if not consent.allows_evaluation(now):",
       "            pass\n        if not consent.allows_evaluation(now):",
       "judge_budget__reserving_a_run_twice_does_not_reset_it"),
    _m("judge_reserve_reads_any_org", "a run id is not a capability",
       J, '                raise errors.NotFound(f"no judge run {run.run_id}")', "                pass",
       "judge_budget__a_run_and_its_consent_belong_to_one_org"),
    _m("judge_consent_org_unchecked", "a run and its consent share one org (R10)",
       J, '            raise errors.NotFound(f"consent for org {consent.org_id} does not match run "\n                                  f"{run.run_id}")',
       "            pass", "judge_budget__a_run_and_its_consent_belong_to_one_org"),
    _m("judge_consent_from_the_snapshot", "consent is rechecked at submission (R9)",
       J, "        consent = self.current_consent(run)", "        consent = run.consent",
       "judge_budget__revoked_or_missing_consent_is_refused_before_egress"),
    _m("judge_revoked_while_submitting_released", "a submitting run stays ambiguous (R28)",
       J, "            if run.state is JudgeRunState.submitting:", "            if False:",
       "judge_budget__consent_revoked_while_submitting_holds_the_reservation"),
    _m("judge_ambiguous_resubmits", "an ambiguous run never resubmits",
       J, "        if run.state in FROZEN_STATES:", "        if False:",
       "judge_budget__an_ambiguous_run_never_resubmits",
       "judge_budget__reserving_a_run_twice_does_not_reset_it"),
    _m("judge_second_provider_batch", "one provider batch per run",
       J, '                raise errors.AmbiguousSubmission(\n                    f"run {run_id} already has provider batch {run.external_batch_id}")',
       "                pass", "judge_budget__one_submission_intent_per_run"),
    _m("judge_empty_provider_id", "a provider batch id is required",
       J, '            raise errors.InvalidRequest("a provider batch id is required")', "            pass",
       "judge_budget__one_submission_intent_per_run"),
    _m("judge_settled_run_reopened", "a settled run is closed",
       J, '            raise errors.Conflict(f"run {run_id} is already {run.state}")', "            pass",
       "judge_budget__a_settled_run_is_closed"),
    _m("judge_resolution_open_to_customers", "resolving is operator only (R8)",
       J, '            raise errors.Forbidden("resolving an ambiguous run requires a platform operator")',
       "            pass", "judge_budget__an_ambiguous_run_is_resolved_only_by_an_operator"),
    _m("judge_release_takes_a_provider_id", "external_id is refused when releasing (R23)",
       J, "        if resolution is not JudgeResolution.adopt_provider_evidence and external_id is not None:",
       "        if False:", "judge_budget__an_ambiguous_run_is_resolved_only_by_an_operator"),
    _m("judge_adopt_without_evidence", "adopting evidence needs the provider id (R8)",
       J, '                raise errors.InvalidRequest(\n                    "adopting provider evidence requires the discovered provider id")',
       "                external_id = \"invented\"",
       "judge_budget__an_ambiguous_run_is_resolved_only_by_an_operator"),
    # --- r4 B1: the reviewer's surviving mutants, now each with a named case ----
    _m("balance_checked_against_the_ledger", "a hold is checked against *available* (n15)",
       S, "        if hold > wallet.available:", "        if hold > wallet.ledger_total:",
       "dur_cap__a_hold_is_checked_against_available_not_the_ledger"),
    _m("deadlines_not_applied_on_complete", "R29 binds `complete` (p3)",
       S, "            self._fence(lease)\n            settled = self._terminalize(",
       "            self._fence_without_deadlines(lease)\n            settled = self._terminalize(",
       "dur_fence__a_deadline_binds_append_and_complete"),
    _m("deadlines_not_applied_on_append", "R29 binds `append` (p2)",
       S, "            job = self.jobs._fence(lease)\n            now = self.clock.now()",
       "            job = self.jobs._fence_without_deadlines(lease)\n            now = self.clock.now()",
       "dur_fence__a_deadline_binds_append_and_complete"),
    _m("deadlines_skipped_once_published", "publication buys no extra time (n03)",
       S, "        if now >= job.request.deadline_at or (\n"
          "                job.lease is not None and now >= job.lease.generation_deadline_at):",
       "        if not job.published and (now >= job.request.deadline_at or (\n"
          "                job.lease is not None and now >= job.lease.generation_deadline_at)):",
       "dur_fence__a_deadline_binds_append_and_complete"),
    _m("terminal_check_only_on_the_first_event", "no terminal event anywhere in a batch (n14)",
       S, "            for event in events:\n                if event.type is ChunkEventType.terminal:",
       "            for event in events[:1]:\n                if event.type is ChunkEventType.terminal:",
       "dur_output__no_terminal_event_anywhere_in_a_batch"),
    _m("tombstone_measured_from_admission", "the tombstone TTL runs from terminal (n11)",
       S, "            record.expires_at = now + timedelta(seconds=self.limits.idempotency_ttl_s)",
       "            record.expires_at = job.admission.admitted_at + timedelta(\n"
       "                seconds=self.limits.idempotency_ttl_s)",
       "dur_admit__the_tombstone_ttl_runs_from_the_terminal_state"),
    _m("only_a_delta_publishes", "any committed chunk is publication (n12)",
       S, "            if committed:\n                job.published = True",
       "            if any(chunk.event_type is ChunkEventType.delta for chunk in committed):\n"
       "                job.published = True",
       "dur_output__any_committed_chunk_is_publication"),
    _m("expire_trusts_the_caller", "expiry never runs on a caller's clock (n17, R7)",
       S, "        now = min(now, self.clock.now()) if now is not None else self.clock.now()",
       "        now = now if now is not None else self.clock.now()",
       "dur_output__expiry_never_runs_on_a_callers_clock"),
    _m("settle_allowed_from_ambiguous", "only the operator path resolves ambiguity (n23)",
       J, "        if run.state not in (JudgeRunState.submitted, JudgeRunState.collecting):",
       "        if run.state not in (JudgeRunState.submitted, JudgeRunState.collecting,\n"
       "                             JudgeRunState.ambiguous):",
       "judge_budget__an_ambiguous_run_cannot_be_settled_directly"),
    _m("flush_zeroes_open_captures", "a flush leaves open captures alone (n30)",
       T, "        self.content_bytes = sum(capture.content_bytes for capture in self.captures\n"
          "                                 if not capture.closed)",
       "        self.content_bytes = 0",
       "trace_bounds__a_flush_leaves_open_captures_alone"),
    _m("dropped_finish_keeps_its_charge", "a dropped record releases its charge (m20)",
       T, "            self.content_bytes = max(0, self.content_bytes - charged)\n"
          "            if capture is not None:",
       "            if capture is not None:",
       "trace_bounds__a_dropped_finish_releases_its_charge"),
    _m("label_uses_the_operators_org", "a calibration label belongs to the row's tenant (n31)",
       F, "        org_id = job.request.org_id", "        org_id = auth.org_id",
       "feedback_ack__an_operator_may_label_a_calibration_set"),
    _m("enqueue_ignores_inflight", "a claimed candidate is not re-indexed (n36)",
       Q, "        if event.event_id in self.pending or event.event_id in self.inflight \\\n"
          "                or event.event_id in self.acknowledged:",
       "        if event.event_id in self.pending \\\n"
          "                or event.event_id in self.acknowledged:",
       "dur_outbox__a_claimed_candidate_is_not_re_indexed"),
    _m("published_deadline_released_at_once", "a published overdue job reconciles (n06)",
       S, "        if usage is None and job.published:\n            # Published output but no authoritative usage",
       "        if False:\n            # Published output but no authoritative usage",
       "dur_settle__a_published_job_past_its_deadline_reconciles",
       "dur_settle__cancelling_after_publication_reconciles"),
    # --- r4 rulings -----------------------------------------------------------
    _m("requeue_recomputes_from_the_full_budget", "a requeue gets only the remainder (R38)",
       S, "        remaining = max(0.0, job.budgets.queue_wait_s - job.queue_wait_used_s)\n"
          "        job.queued_at = now",
       "        remaining = job.budgets.queue_wait_s\n        job.queued_at = now",
       "dur_output__queue_time_is_time_spent_queued",
       "dur_output__queue_wait_does_not_restart_on_a_requeue",
       "dur_output__phase_deadlines_are_persisted_at_each_transition"),
    _m("queue_budget_from_wall_time", "queue wait is time spent queued (R38)",
       S, "        remaining = max(0.0, job.budgets.queue_wait_s - job.queue_wait_used_s)",
       "        remaining = job.budgets.queue_wait_s if job.queue_wait_used_s == 0 else 0.0",
       "dur_output__queue_time_is_time_spent_queued"),
    _m("queue_used_time_not_charged", "leaving `queued` charges the interval (R38)",
       S, "        spent = max(0.0, (now - job.queued_at).total_seconds())", "        spent = 0.0",
       "dur_output__queue_time_is_time_spent_queued"),
    _m("off_mode_capture_keeps_content", "off/minimal captures keep nothing (R37)",
       T, "        no_op = mode is not TraceMode.full or deadline_at is None",
       "        no_op = deadline_at is None",
       "trace_bounds__off_mode_produces_no_trace_at_all"),
    _m("capture_exit_does_not_abandon", "the context manager abandons (R37)",
       T, "        if not self.closed:\n            self._close(TraceLossReason.abandoned)",
       "        if False:\n            self._close(TraceLossReason.abandoned)",
       "trace_bounds__an_abandoned_capture_releases_its_bytes"),
    _m("finish_is_not_idempotent", "finish is idempotent (R37)",
       T, "        if self.result is not None:", "        if self.result is None and False:",
       "trace_bounds__an_abandoned_capture_releases_its_bytes",
       "trace_bounds__a_dropped_finish_releases_its_charge"),
    _m("capture_never_reaped", "an open capture past its deadline is reaped (R37)",
       T, "            if now >= capture.deadline_at + timedelta(seconds=grace_s):",
       "            if False:",
       "trace_bounds__an_open_capture_past_its_deadline_is_reaped"),
    _m("reaped_capture_keeps_its_bytes", "reaping releases the bytes (R37)",
       T, "                capture._close(TraceLossReason.abandoned)\n                reaped += 1",
       "                reaped += 1",
       "trace_bounds__an_open_capture_past_its_deadline_is_reaped"),
    _m("settlement_checks_capacity_too_late_2", "capacity before money (R39)",
       S, "            self.stream.check_terminal_capacity(job)", "            pass",
       "dur_settle__a_settlement_that_cannot_journal_moves_no_money"),
    # --- r5 F1/F2/F3 ----------------------------------------------------------
    _m("capture_counts_its_loss_twice", "a capture contributes at most one loss count",
       T, "            self.result = self.sink._drop(reason, counted=self.counted)",
       "            self.result = self.sink._drop(reason, counted=False)",
       "trace_bounds__an_abandoned_capture_releases_its_bytes"),
    _m("finish_forgets_its_first_result", "finish returns the first result",
       T, "        if self.result is not None:\n"
          "            # Idempotent, and the answer is the **first** one",
       "        if False:\n            # Idempotent, and the answer is the **first** one",
       "trace_bounds__an_abandoned_capture_releases_its_bytes",
       "trace_bounds__a_dropped_finish_releases_its_charge"),
    _m("add_raises_on_a_non_byte_part", "a malformed part is dropped, not raised",
       T, "        elif not isinstance(part, (bytes, bytearray, memoryview)):",
       "        elif False:", "trace_bounds__a_capture_belongs_to_its_own_request",
       dies_by=("TypeError",)),
    _m("drop_recounts_a_counted_loss", "one loss count per capture, not per call",
       T, "        self.counted = True\n        self.sink.loss_reasons[reason] += 1",
       "        self.sink.loss_reasons[reason] += 1",
       "trace_bounds__concurrent_captures_share_one_budget",
       "trace_bounds__an_abandoned_capture_releases_its_bytes"),
    _m("metadata_drop_keeps_its_charge", "every dropped path releases its charge (m20b)",
       T, "            self.content_bytes = max(0, self.content_bytes - charged)\n"
          "            if capture is not None:\n                capture.content_bytes = 0",
       "            if capture is not None:\n                capture.content_bytes = 0",
       "trace_bounds__a_dropped_finish_releases_its_charge"),
    _m("negative_grace_reaps_live_captures", "a negative grace is clamped to zero",
       T, "        grace_s = max(0.0, grace_s)", "        grace_s = grace_s",
       "trace_bounds__an_open_capture_past_its_deadline_is_reaped"),
    _m("no_deadline_capture_holds_bytes", "a capture with no deadline is never a leak (R37)",
       T, "        no_op = mode is not TraceMode.full or deadline_at is None",
       "        no_op = mode is not TraceMode.full",
       "trace_bounds__an_open_capture_past_its_deadline_is_reaped"),
    _m("minimal_capture_loses_its_metadata_row", "minimal yields its metadata row (R37/01)",
       T, "            return self.sink._enqueue(envelope, charged=0, capture=self)\n"
          "        # A `full` capture that is a no-op",
       "            return self.sink._drop(TraceLossReason.malformed)\n"
          "        # A `full` capture that is a no-op",
       "trace_bounds__off_mode_produces_no_trace_at_all",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope"),
    _m("off_mode_capture_queues_a_row", "an off-mode request has no row at all",
       T, "        if envelope.mode is TraceMode.off:\n            return self._drop(TraceLossReason.malformed)",
       "        if False:\n            return self._drop(TraceLossReason.malformed)",
       "trace_bounds__off_mode_produces_no_trace_at_all"),
    _m("terminal_reserve_from_one_cause", "the widest terminal payload, over every cause (F2)",
       S, "                     for state in JobState for cause in TerminalCause",
       "                     for state in JobState for cause in (TerminalCause.platform_error,)",
       "dur_settle__a_settlement_that_cannot_journal_moves_no_money"),
    _m("one_stuck_job_aborts_the_sweep", "recovery continues past an unsettleable job (F2)",
       S, "                except errors.DomainError as refused:", "                except _NeverRaised as refused:",
       "dur_settle__one_unsettleable_job_does_not_stop_the_sweep"),
    _m("reaped_job_keeps_its_hold", "a reaped job's hold is released",
       S, "            never_charged = cause in FREE_CAUSES or (usage is None and not job.published",
       "            never_charged = False and (usage is None and not job.published",
       "dur_settle__one_unsettleable_job_does_not_stop_the_sweep",
       "dur_settle__only_three_causes_can_charge"),
    _m("stuck_jobs_not_reported", "an unsettleable job is reported (F2)",
       S, "                    self.unsettleable[job.id] = refused.code", "                    pass",
       "dur_settle__one_unsettleable_job_does_not_stop_the_sweep"),
    # --- r6 B1/B2: one mutant per branch of the no-op finish --------------------
    _m("no_op_finish_skips_the_identity_check", "a no-op capture checks identity too",
       T, "        if (envelope.request_id, envelope.org_id) != (self.request_id, self.org_id):\n"
          "            # The same identity check the accumulating path makes",
       "        if False:\n            # The same identity check the accumulating path makes",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope"),
    _m("off_capture_queues_the_envelope", "an off-mode capture queues nothing (01)",
       T, "            return TraceOfferResult.dropped\n        if self.closed:",
       "            pass\n        if self.closed:",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope",
       "trace_bounds__off_mode_produces_no_trace_at_all"),
    # P18 split the no-op path's combined guard - `envelope.mode is not minimal or
    # carries_content` - into a general mode check that applies to **every** mode and a
    # content check for `minimal`. The two halves the r7 pass tested separately are now two
    # separate guards, so each has its own mutant and the third (which edited the combined
    # form) is gone. The mode check is edited here against the no-op *case* as well as
    # against the lattice, because both must be able to see it.
    _m("minimal_capture_trusts_the_envelope_mode", "the capture's mode decides, not the envelope",
       T, "        if envelope.mode is not self.mode:\n"
          "            # r1 R12/R37, the same rule the accumulating path enforces: **the capture",
       "        if False:\n"
          "            # r1 R12/R37, the same rule the accumulating path enforces: **the capture",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope"),
    _m("minimal_capture_keeps_only_the_mode_half", "a minimal capture never stores content",
       T, "            if envelope.carries_content:\n"
          "                self._count(TraceLossReason.malformed)\n"
          "                return self.sink._drop(TraceLossReason.malformed, counted=True)\n"
          "            return self.sink._enqueue(envelope, charged=0, capture=self)",
       "            return self.sink._enqueue(envelope, charged=0, capture=self)",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope"),
    _m("live_capture_trusts_the_envelope_mode", "the capture decides on the live path too (n1)",
       T, "        if envelope.mode is not self.mode:", "        if False:",
       "trace_bounds__a_live_capture_also_decides_its_own_mode", occurrences=2),
    _m("unrecordable_capture_counts_no_loss", "a full capture that never captured counts one loss (n2)",
       T, "        if self.no_op and self.mode is not TraceMode.full:",
       "        if self.no_op:",
       "trace_bounds__an_open_capture_past_its_deadline_is_reaped"),
    _m("drop_reason_falls_back_on_truthiness", "`none` is never a drop reason (n3)",
       T, "            reason = (TraceLossReason.abandoned if self.lost_reason is TraceLossReason.none\n"
          "                      else self.lost_reason)",
       "            reason = self.lost_reason or TraceLossReason.abandoned",
       "trace_bounds__no_loss_is_ever_counted_under_none",
       # R83 (a): the base's own `_drop` guard (`assert reason is not none`) stops this
       # defect before any count moves, in every sink built on the base - the raise is
       # what a real sink would do, so it is the declared kill mode.
       dies_by=("AssertionError",)),
    _m("no_op_full_capture_keeps_its_content", "a discarded capture finishes as metadata",
       T, '        return self.sink._enqueue(envelope.model_copy(update={\n'
          '            "content_complete": False, "content_ref": None, "content_bytes": 0,\n'
          '            "loss_reason": TraceLossReason.abandoned}), charged=0, capture=self)',
       "        return self.sink._enqueue(envelope, charged=0, capture=self)",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope"),
    _m("no_op_full_capture_reports_no_loss", "a discarded capture counts exactly one loss",
       T, "        self.lost_reason = TraceLossReason.abandoned\n"
          "        self._count(TraceLossReason.abandoned)",
       "        self.lost_reason = TraceLossReason.abandoned",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope"),
    _m("offer_accepts_content", "offer is metadata-only (R27)",
       T, "        if envelope.carries_content:\n            # r1 R27: content is charged",
       "        if False:\n            # r1 R27: content is charged",
       "trace_bounds__an_accepted_offer_is_in_memory_only"),
    _m("charge_released_only_on_queue_full", "every drop reason releases the charge (m20b)",
       T, "            if reason is None:\n                continue\n"
          "            self.content_bytes = max(0, self.content_bytes - charged)",
       "            if reason is None:\n                continue\n"
          "            if reason is TraceLossReason.queue_full:\n"
          "                self.content_bytes = max(0, self.content_bytes - charged)",
       "trace_bounds__a_dropped_finish_releases_its_charge"),
    _m("minimal_capture_accumulates", "a quiet mode charges nothing (P08)",
       T, "        no_op = mode is not TraceMode.full or deadline_at is None",
       "        no_op = mode is TraceMode.off or deadline_at is None",
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    # --- P17/P18: identity and mode, now observable inside the lattice ------------
    _m("noop_capture_trusts_the_envelope_mode", "the capture decides its own mode (P18)",
       T, "        if envelope.mode is not self.mode:\n"
          "            # r1 R12/R37, the same rule the accumulating path enforces: **the capture",
       "        if False:\n"
          "            # r1 R12/R37, the same rule the accumulating path enforces: **the capture",
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    _m("noop_capture_files_a_foreign_identity", "a row belongs to its capture (P17)",
       T, "        if (envelope.request_id, envelope.org_id) != (self.request_id, self.org_id):\n"
          "            # The same identity check the accumulating path makes: one request's envelope",
       "        if False:\n"
          "            # The same identity check the accumulating path makes: one request's envelope",
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    _m("live_capture_files_a_foreign_identity", "a live capture checks identity too (P17)",
       T, "        if (envelope.request_id, envelope.org_id) != (self.request_id, self.org_id):\n"
          "            # The single loss this capture contributes is labelled by what went wrong:",
       "        if False:\n"
          "            # The single loss this capture contributes is labelled by what went wrong:",
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    # --- P08/P10/P11: the guarded lattice invariants that now fire ---------------
    _m("minimal_capture_stores_content", "a minimal capture never stores content (P08)",
       T, "            if envelope.carries_content:\n"
          "                self._count(TraceLossReason.malformed)\n"
          "                return self.sink._drop(TraceLossReason.malformed, counted=True)\n"
          "            return self.sink._enqueue(envelope, charged=0, capture=self)",
       "            return self.sink._enqueue(envelope, charged=0, capture=self)",
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    _m("no_deadline_capture_loses_silently", "declared content that is missing says why (P10)",
       T, '            "content_complete": False, "content_ref": None, "content_bytes": 0,\n'
          '            "loss_reason": TraceLossReason.abandoned}), charged=0, capture=self)',
       '            "content_complete": False, "content_ref": None, "content_bytes": 0,\n'
          '            "loss_reason": TraceLossReason.none}), charged=0, capture=self)',
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    _m("no_deadline_capture_counts_no_loss", "the missing content is counted (P11)",
       T, "        self.lost_reason = TraceLossReason.abandoned\n"
          "        self._count(TraceLossReason.abandoned)\n"
          "        return self.sink._enqueue(envelope.model_copy(update={",
       "        self.lost_reason = TraceLossReason.abandoned\n"
          "        return self.sink._enqueue(envelope.model_copy(update={",
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    # --- F2.1: the lattice assertions the S1 review found vacuous --------------
    _m("shutdown_counted_for_nothing", "only a real loss names a reason (R42)",
       TF, "        if lost:\n            # Only a real loss names a reason.",
       "        if True:\n            # Only a real loss names a reason.",
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    _m("closed_capture_still_queues", "a capture closed before any finish stores nothing (R42)",
       T, "        if self.closed:                      # abandoned or reaped first: nothing to queue",
       "        if self.closed and self.lost_reason is TraceLossReason.memory_budget:"
       "  # abandoned or reaped first: nothing to queue",
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    _m("flush_drops_instead_of_appending", "a flush appends what it takes out of memory",
       T, "        self.appended.extend(self.queued)\n        self.queued.clear()",
       "        self.queued.clear()",
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    _m("raw_content_envelope_is_trusted", "the capture decides its mode, never the envelope (R12)",
       T, "        if envelope.mode is not self.mode:", "        if False:",
       "trace_bounds__a_live_capture_also_decides_its_own_mode", occurrences=2),
    # --- r8 R42: loss accounting cannot regress -------------------------------
    _m("count_is_not_idempotent", "one loss count per capture, whatever follows (R42)",
       T, "        if self.counted:\n            return\n        self.counted = True",
       "        self.counted = True",
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    _m("live_mode_mismatch_discards_unguarded", "a lost capture is not re-counted (R42)",
       T, "    def _count(self, reason: TraceLossReason) -> None:\n"
          '        """Record this capture\'s **single** loss (r1 R42).',
       "    def _count(self, reason: TraceLossReason) -> None:\n"
          "        self.counted = True\n"
          "        self.sink.loss_reasons[reason] += 1\n"
          '        """Record this capture\'s **single** loss (r1 R42).',
       "trace_bounds__every_bounded_capture_sequence_holds_the_invariants"),
    _m("closed_no_op_capture_queues_a_row", "a closed capture never queues a row (R42)",
       T, "        if self.closed:\n            # Already ended - abandoned, reaped, or finished.",
       "        if False:\n            # Already ended - abandoned, reaped, or finished.",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope"),
    _m("off_capture_counts_a_drop", "an off-mode capture is silent (R42)",
       T, "        if self.mode is TraceMode.off:\n"
          "            # r1 R42: an off-mode capture is **silent**.",
       "        if self.mode is TraceMode.off and False:\n"
          "            # r1 R42: an off-mode capture is **silent**.",
       "trace_bounds__off_mode_produces_no_trace_at_all"),
    # --- one per remaining case (R32: every case must be killable) -------------
    _m("replay_returns_a_new_identity", "an idempotent replay returns the original identity",
       S, "        return self._snapshot(job).model_copy(update={\"replayed\": True})",
       "        return None", "dur_admit__idempotent_replay_returns_the_same_identity",
       "dur_admit__crash_after_commit_then_retry_does_not_double_reserve",
       "dur_admit__tombstone_is_retained_after_the_terminal_state"),
    _m("replay_flag_never_set", "a replay says it is one",
       S, 'update={\"replayed\": True})', "update={})",
       "dur_admit__idempotent_replay_returns_the_same_identity",
       "dur_admit__crash_after_commit_then_retry_does_not_double_reserve"),
    _m("tombstone_not_retained", "the mapping survives 24h past terminal",
       S, "        record = self.idem.get((job.request.org_id, job.admission.operation,\n"
          "                                job.admission.idempotency_key))",
       "        record = None", "dur_admit__tombstone_is_retained_after_the_terminal_state",
       "dur_admit__expired_mapping_is_explicit_never_a_second_billable_job"),
    _m("generation_not_incremented", "claim increments the generation",
       S, "            job.generation += 1", "            job.generation = 1",
       "dur_fence__claim_increments_the_generation_from_the_database_clock",
       "dur_fence__a_stale_generation_is_rejected"),
    _m("claim_allows_a_running_job", "a job is claimed once",
       S, 'raise errors.NotClaimable(f"job {job_id} is {job.state}, not queued")', "pass",
       "dur_fence__claim_increments_the_generation_from_the_database_clock",
       "dur_outbox__the_index_never_authorizes_execution"),
    _m("recovery_never_requeues", "a lost prepublication attempt runs again",
       S, "            job.attempts += 1\n            job.state = JobState.queued\n            job.lease = None",
       "            job.attempts += 1\n            job.lease = None",
       "dur_output__recovery_requeues_only_before_publication",
       "dur_output__prepublication_retries_are_bounded"),
    _m("terminal_event_written_twice", "one terminal event per job",
       S, "        for chunk in stored:\n            if chunk.event_type is ChunkEventType.terminal:\n"
          "                return chunk                        # one terminal event, replay safe",
       "        for chunk in ():\n            pass",
       "dur_output__the_terminal_event_is_written_once_with_the_settlement",
       "dur_settle__the_terminal_event_belongs_to_the_settling_transaction"),
    _m("settlement_debits_twice", "the debit is applied once",
       S, "                wallet.ledger_total = wallet.ledger_total - debit",
       "                wallet.ledger_total = wallet.ledger_total - debit - debit",
       "dur_settle__one_settlement_with_exact_decimals",
       "dur_settle__the_store_rounds_half_up_once"),
    _m("hold_not_released_on_settlement", "the hold resolves in the settling transaction",
       S, "                wallet.reserved_total = wallet.reserved_total - hold.amount\n"
          "                wallet.ledger_total = wallet.ledger_total - debit",
       "                wallet.ledger_total = wallet.ledger_total - debit",
       "dur_settle__one_settlement_with_exact_decimals"),
    _m("platform_failure_charges", "platform failures are free",
       S, "        elif cause not in BILLABLE_CAUSES or usage is None:",
       "        elif usage is None:", "dur_settle__platform_failures_are_free",
       "dur_settle__only_three_causes_can_charge"),
    _m("feedback_outbox_missing", "a feedback projection is queued before the ack",
       F, "        self.outbox.append(OutboxEvent(event_id=self.ids.event_id(), aggregate_id=request_id,",
       "        _unused = (OutboxEvent(event_id=self.ids.event_id(), aggregate_id=request_id,",
       "feedback_ack__acceptance_is_durable_and_provenance_is_server_set", occurrences=2),
    _m("media_key_ignores_the_tenant", "two orgs never share an object",
       M, '        return f"media/{org_id}/{profile_version}/{digest.split(\':\')[1][:16]}/{part}"',
       '        return f"media/{profile_version}/{digest.split(\':\')[1][:16]}/{part}"',
       "media_parity__staging_is_content_addressed_and_tenant_namespaced"),
    _m("prepared_profile_ignored", "prepared refs carry their profile version",
       M, "ref.model_copy(update={\"profile_version\": profile,",
       "ref.model_copy(update={\"profile_version\": ref.profile_version,",
       "media_parity__staging_is_content_addressed_and_tenant_namespaced"),
    _m("upload_handle_is_guessable", "an upload handle is opaque and server issued",
       M, '                "destination_ref": f"infrx-upload:{handle}",',
       '                "destination_ref": f"https://example.invalid/{handle}?Signature=abc",',
       "media_sec__an_upload_is_owned_verified_and_immutable"),
    _m("upload_reference_names_the_org", "an upload reference carries no organization (R61 (1))",
       M, '                "destination_ref": f"infrx-upload:{handle}",',
       '                "destination_ref": f"infrx-upload:{org_id}:{handle}",',
       "media_sec__an_upload_is_owned_verified_and_immutable"),
    _m("offer_claims_durability", "an accepted offer is in memory only",
       T, "        self.queued.append(envelope)\n        self.accepted += 1",
       "        self.queued.append(envelope)\n        self.appended.append(envelope)\n"
       "        self.fsynced.append(envelope)\n        self.accepted += 1",
       "trace_bounds__an_accepted_offer_is_in_memory_only",
       "trace_bounds__in_memory_appended_and_fsynced_are_separate_states"),
    _m("judge_release_keeps_the_money", "releasing a reservation frees the budget (R8)",
       J, "            resolved = self._release(run, JudgeRunState.quarantined,\n                                     reconciled_at=self.clock.now())",
       "            resolved = run.model_copy(update={\"state\": JudgeRunState.quarantined})\n"
       "            self.runs[run_id] = resolved",
       "judge_budget__an_ambiguous_run_is_resolved_only_by_an_operator"),
    # --- F2R lane A item 2: the delta payload is exactly `{visible, raw}` (R80) ------
    _m("engine_fake_visible_is_raw", "the customer never reads the reasoning block (R58)",
       E, 'SPLIT_REASONING_VISIBLE = ("", "", "", "Two people unload boxes.")',
       "SPLIT_REASONING_VISIBLE = SPLIT_REASONING",
       "api_stream__reasoning_delimiters_split_across_chunks"),
    _m("engine_fake_keeps_the_content_alias", "a delta payload is exactly {visible, raw}",
       E, 'payload={"visible": visible, "raw": raw}))',
       'payload={"visible": visible, "raw": raw, "content": raw}))',
       "api_stream__canonical_events_end_with_authoritative_usage"),
    # --- F2R: filesystem roots are unset or absolute (PROCESSING_CACHE_DIR) -----------
    Mutant(name="relative_root_accepted", invariant="a filesystem root is unset or absolute",
           file="config.py",
           old="        if path and (path != path.strip() or not os.path.isabs(path)):",
           new="        if False:", cases=("test_a_filesystem_root_is_unset_or_absolute",)),
    Mutant(name="mpeg_allowed_again", invariant="video/mpeg is not a pilot input",
           file="config.py",
           old='DEFAULT_ALLOWED_VIDEO_MIME = "video/mp4,video/webm,video/quicktime"',
           new='DEFAULT_ALLOWED_VIDEO_MIME = "video/mp4,video/webm,video/quicktime,video/mpeg"',
           cases=("test_the_default_video_allow_list_has_no_mpeg",)),
    Mutant(name="mpeg_extension_mapped_again", invariant="no extension guess names video/mpeg",
           file="config.py", old='            ".mov": "video/quicktime"}',
           new='            ".mov": "video/quicktime", ".mpg": "video/mpeg"}',
           cases=("test_the_default_video_allow_list_has_no_mpeg",)),
    # --- lane B IR-6: deployment settings (config.py) --------------------------------
    Mutant(name="DEPLOY-01", invariant="an empty deployment value is refused, not defaulted",
           file="config.py", old='        if raw.strip() == "":', new="        if False:",
           cases=("test_an_empty_deployment_value_is_refused",)),
    Mutant(name="DEPLOY-02", invariant="a deployment bound a zero would disable is refused",
           file="config.py", old="        if getattr(deployment, name) <= 0:",
           new="        if getattr(deployment, name) < 0:",
           cases=("test_a_deployment_bound_a_zero_would_disable_is_refused",)),
    Mutant(name="DEPLOY-03", invariant="a bad deployment value refuses before anything mounts",
           file="config.py",
           old='    deployment = validate_deployment(getattr(settings, "deployment", DEPLOYMENT_DEFAULTS), mode)\n',
           new='    deployment = getattr(settings, "deployment", DEPLOYMENT_DEFAULTS)\n',
           cases=("test_a_bad_deployment_value_refuses_before_anything_mounts",)),
    Mutant(name="DEPLOY-04", invariant="the pool bounds are ordered (min == max is valid)",
           file="config.py",
           old="    if deployment.database_pool_min_size > deployment.database_pool_max_size:",
           new="    if deployment.database_pool_min_size >= deployment.database_pool_max_size:",
           cases=("test_the_pool_bounds_must_be_ordered",), dies_by=("RuntimeMisconfigured",)),
    Mutant(name="DEPLOY-05", invariant="a short console cursor secret is refused",
           file="config.py",
           old="    if _configured(secret) and len(secret) < MIN_CONSOLE_CURSOR_SECRET_CHARS:",
           new="    if _configured(secret) and len(secret) < 1:",
           cases=("test_a_short_cursor_secret_is_refused_without_echoing_it",)),
    # --- contracts v2 (F2P additive phase; `tests/contracts/v2/mutants_v2.py` until the
    # wire-in folded it here, item 6). Killed in tests/contracts/v2/test_conformance_v2.py.
    # --- SPLIT-CONTRACT ------------------------------------------------------
    _m("catalog_lists_private_dev_publicly",
       "a private dev deployment is not in the public catalog at all",
       V2_FAKE, "        if deployment.visibility is v2.Visibility.private:",
       "        if False:",
       "split_contract__a_consumer_credential_cannot_reach_a_private_dev_endpoint"),
    _m("pin_lets_a_copied_consumer_context_into_a_private_endpoint",
       "only a provider_dev audience reaches a private deployment, even when a "
       "model_copy'd consumer context carries the endpoint's ids (review B1, G1)",
       V2_P, "        if auth.audience is not CredentialAudience.provider_dev:",
       "        if False:",
       "split_contract__a_consumer_credential_cannot_reach_a_private_dev_endpoint"),
    _m("pin_ignores_the_credentials_endpoint_scope",
       "a provider credential reaches only the endpoint it was issued for",
       V2_P, "        if auth.endpoint_id != deployment.endpoint_id:",
       "        if False and auth.endpoint_id != deployment.endpoint_id:",
       "split_contract__a_consumer_credential_cannot_reach_a_private_dev_endpoint"),
    _m("pin_ignores_the_credentials_provider",
       "a member of another provider cannot reach this provider's dev endpoint",
       V2_P, "        if auth.provider_org_id != deployment.provider_org_id:",
       "        if False and auth.provider_org_id != deployment.provider_org_id:",
       "split_contract__a_consumer_credential_cannot_reach_a_private_dev_endpoint"),
    _m("catalog_ignores_the_endpoint_scope",
       "the catalog itself does not answer for a foreign endpoint",
       V2_FAKE, "            if endpoint_id != deployment.endpoint_id:\n"
             "                return None",
       "            if endpoint_id != deployment.endpoint_id:\n"
       "                pass",
       "split_contract__a_provider_credential_cannot_borrow_another_endpoint"),
    _m("a_v1_payload_is_silently_accepted",
       "schema_version is explicit; a v1 body is refused, never reinterpreted",
       V2_R, "        if self.schema_version != SCHEMA_VERSION:",
       "        if False:",
       "split_contract__an_internal_v1_payload_is_refused_not_upgraded"),
    _m("a_v1_price_snapshot_converts_to_credit",
       "there is no conversion from a v1 USD snapshot to a CREDIT rate card",
       V2_R, '    raise ValueError(f"v1 PriceSnapshot {snapshot.price_version} is denominated in "',
       '    return None or ValueError(f"v1 PriceSnapshot {snapshot.price_version} is '
       'denominated in "',
       "split_contract__an_internal_v1_payload_is_refused_not_upgraded"),
    _m("model_revision_loses_its_revision",
       "r1 R62: the consumer-facing identifier keeps its <model>@<revision> form",
       V2_R, '        return f"{self.public_model_id}@{self.revision_label}"',
       '        return f"{self.public_model_id}"',
       "split_contract__the_v1_model_revision_string_is_unchanged_r62"),
    _m("digest_provenance_claims_upstream_confirmation",
       "a served-bytes digest is never recorded as a confirmed registry oid",
       V2_FX, "        digest_source=v2.DigestSource.served_bytes,",
       "        digest_source=v2.DigestSource.registry_oid_confirmed,",
       "split_contract__the_v1_model_revision_string_is_unchanged_r62"),
    _m("an_unpinned_runtime_image_reads_as_pinned",
       "image_is_pinned is False while serve.sh pins a moving tag",
       V2_R, "        return self.runtime_image_digest is not None",
       "        return self.runtime_image_digest is None",
       "split_contract__the_v1_model_revision_string_is_unchanged_r62"),
    _m("digest_source_defaults_to_confirmed",
       "digest provenance is stated by the writer, never defaulted (review B2a)",
       V2_R, "    digest_source: DigestSource\n",
       "    digest_source: DigestSource = DigestSource.registry_oid_confirmed\n",
       "split_contract__the_v1_model_revision_string_is_unchanged_r62"),
    _m("two_surface_versions",
       "the whole changed surface carries ONE reviewed version identifier",
       V2_INIT, 'SURFACE_VERSION = "contracts-v2.0"', 'SURFACE_VERSION = "contracts-v2.1"',
       "split_contract__the_surface_carries_one_reviewed_version"),

    # --- CREDIT-UNITS --------------------------------------------------------
    _m("units_are_interchangeable",
       "arithmetic across two denominations is refused by construction",
       V2_MU, "        return other._value if type(other) is type(self) else None",
       "        return other._value if isinstance(other, Amount) else None",
       "credit_units__mixed_unit_arithmetic_is_refused_by_construction"),
    _m("raw_answers_any_unit",
       "raw(unit) refuses unless the caller names the unit it already expects",
       V2_MU, "        if unit != type(self).UNIT:", "        if False:",
       "credit_units__mixed_unit_arithmetic_is_refused_by_construction"),
    _m("history_totals_collapse_into_one_unit",
       "a mixed history reports one figure per unit and never a combined one",
       V2_R, "        return {unit: str(total(amounts, UNIT_TYPES[unit]))\n"
          "                for unit, amounts in sorted(by_unit.items())}",
       '        return {"CREDIT": str(total(amounts, UNIT_TYPES[unit]))\n'
       "                for unit, amounts in sorted(by_unit.items())}",
       "credit_units__a_mixed_history_totals_per_unit_and_never_once"),
    _m("a_legacy_row_is_read_as_credit",
       "a row's regime fixes its unit; the pair is never inferred",
       V2_R, "        expected = unit_of(self.accounting_regime.value)",
       '        expected = self.unit',
       "credit_units__a_mixed_history_totals_per_unit_and_never_once"),
    _m("a_credit_row_may_carry_a_legacy_price_version",
       "price_version is the legacy regime's field and only that",
       V2_R, '                raise ValueError("price_version is the legacy regime\'s field")',
       "                pass",
       "credit_units__a_legacy_row_invents_none_of_the_new_fields"),
    _m("the_projection_invents_a_price_version",
       "an old row's fields are read, never defaulted into something plausible",
       V2_R, '        price_version=row.get("price_version"), settled_at=row["settled_at"])',
       '        price_version=row.get("price_version", "pv_unknown"), '
       'settled_at=row["settled_at"])',
       "credit_units__a_legacy_row_invents_none_of_the_new_fields"),
    _m("a_negative_credit_rate_is_accepted",
       "a negative rate would turn a debit into a credit at settlement",
       V2_R, "        if self.input_rate_per_million.is_negative or "
          "self.output_rate_per_million.is_negative:",
       "        if False:",
       "credit_units__a_wrong_unit_or_unpriced_rate_card_cannot_exist"),
    _m("an_unapproved_rate_card_is_accepted",
       "an approved rate card names its approver",
       V2_R, '        if not self.approved_by.strip():', "        if False:",
       "credit_units__a_wrong_unit_or_unpriced_rate_card_cannot_exist"),
    _m("a_nonzero_legacy_balance_needs_no_hold",
       "a nonzero legacy USD balance is a rollout hold, not a silent write-off",
       V2_R, "        if not self.balance.is_zero and not self.rollout_hold:",
       "        if False:",
       "credit_units__a_nonzero_legacy_balance_is_a_hold_not_a_conversion"),

    # --- CREDIT-IDENTITY -----------------------------------------------------
    _m("available_ignores_reservations",
       "available is the ledger total minus active reservations",
       V2_R, "        return self.ledger_total - self.reserved_total",
       "        return self.ledger_total",
       "credit_identity__a_consumer_credential_resolves_its_own_user_wallet"),
    _m("a_provider_wallet_claims_a_signup_entitlement",
       "only an individual's consumer wallet has a signup entitlement",
       V2_R, "        return self.kind is WalletKind.consumer", "        return True",
       "credit_identity__a_provider_dev_credential_resolves_a_zero_provider_wallet"),
    _m("a_provider_credential_spends_any_wallet",
       "a provider credential spends only its own provider's wallet",
       V2_P, "    if wallet.owner_provider_org_id != auth.provider_org_id:",
       "    if False:",
       "credit_identity__a_provider_dev_credential_resolves_a_zero_provider_wallet"),
    _m("a_request_may_name_a_wallet",
       "extra=forbid is what stops a payload naming a wallet, price or identity",
       V2_R, '    model_config = ConfigDict(frozen=True, extra="forbid")',
       '    model_config = ConfigDict(frozen=True, extra="ignore")',
       "credit_identity__no_request_field_can_select_a_wallet"),
    _m("a_consumer_credential_spends_a_copied_provider_wallet",
       "a consumer credential spends a consumer wallet only, even one model_copy'd "
       "with its owner fields (review B1, G2)",
       V2_P, "        if wallet.kind is not WalletKind.consumer:", "        if False:",
       "credit_identity__a_foreign_wallet_is_forbidden_not_a_fallback"),
    _m("a_provider_credential_spends_a_copied_consumer_wallet",
       "a provider_dev credential never spends a consumer wallet, even one model_copy'd "
       "with the provider as owner (review B1, G3)",
       V2_P, "    if wallet.kind is not WalletKind.provider_dev:", "    if False:",
       "credit_identity__a_provider_dev_credential_resolves_a_zero_provider_wallet"),
    _m("a_credential_spends_another_users_wallet",
       "a credential spends only its own user's wallet",
       V2_P, "        if wallet.owner_user_id != auth.user_id:", "        if False:",
       "credit_identity__a_foreign_wallet_is_forbidden_not_a_fallback"),
    _m("the_personal_org_binding_is_not_checked",
       "the wallet's personal-org binding must match the authenticated org",
       V2_P, "        if wallet.personal_org_id != auth.org_id:", "        if False:",
       "credit_identity__a_foreign_wallet_is_forbidden_not_a_fallback"),
    _m("a_missing_wallet_resolves_to_something",
       "no wallet provisioned is not_found, never a fallback",
       V2_P, '        raise errors.NotFound("no wallet is provisioned for this credential")',
       "        return wallet or wallet",
       "credit_identity__a_foreign_wallet_is_forbidden_not_a_fallback"),
    _m("an_operator_credential_spends_a_wallet",
       "an operator credential resolves no spendable wallet",
       V2_P, '        raise errors.Forbidden("an operator credential does not spend a wallet")',
       "        return wallet",
       "credit_identity__an_operator_credential_spends_no_wallet"),
    _m("a_provider_wallet_receives_the_signup_grant",
       "a provider_dev wallet has no signup entitlement",
       V2_R, "    if not wallet.has_signup_entitlement:", "    if False:",
       "credit_identity__a_provider_wallet_has_no_grant_and_no_transfer"),
    _m("a_transfer_kind_exists",
       "the ledger vocabulary is closed and has no transfer",
       V2_R, '    inference_debit = "inference_debit"',
       '    inference_debit = "inference_debit"\n    transfer = "transfer"',
       "credit_identity__a_provider_wallet_has_no_grant_and_no_transfer"),
    _m("a_provider_wallet_may_carry_a_signup_entry",
       "a signup grant entry is only ever on a consumer wallet",
       V2_R, "            if self.wallet_kind is not WalletKind.consumer:", "            if False:",
       "credit_identity__a_provider_wallet_has_no_grant_and_no_transfer"),
    _m("the_grant_key_includes_the_campaign",
       "the grant key is the individual and the entitlement, nothing else",
       V2_R, "        return (self.user_id, self.entitlement)",
       "        return (self.user_id, self.entitlement, self.campaign_version)",
       "credit_identity__campaign_and_membership_never_reset_the_grant_key"),

    # --- CREDIT-GRANT --------------------------------------------------------
    _m("the_grant_amount_changes",
       "the initial grant is exactly +10000.00000000 CREDIT",
       V2_R, 'INITIAL_SIGNUP_GRANT = Credit("10000.00000000")',
       'INITIAL_SIGNUP_GRANT = Credit("1000.00000000")',
       "credit_grant__the_grant_is_exactly_ten_thousand_credit_once"),
    _m("an_unverified_grant_is_accepted",
       "the grant records the verification evidence it was issued against",
       V2_R, "        if not self.verification_evidence_ref.strip():", "        if False:",
       "credit_grant__the_grant_is_exactly_ten_thousand_credit_once"),
    _m("the_grant_lands_in_another_users_wallet",
       "the initial grant lands in the individual's own wallet only",
       V2_R, "    if wallet.owner_user_id != user_id:", "    if False:",
       "credit_grant__the_grant_lands_only_in_the_individuals_own_wallet"),

    # --- CREDIT-RATE ---------------------------------------------------------
    _m("an_admission_may_carry_a_foreign_card",
       "the attached rate card must be the pinned rate_card_version",
       V2_R, "        if self.rate_card.rate_card_version != self.pins.rate_card_version:",
       "        if False:",
       "credit_rate__admission_pins_model_serving_deployment_and_rate_card"),
    _m("an_admission_may_price_another_deployment",
       "the attached card must price the pinned deployment revision",
       V2_R, "        if self.rate_card.deployment_revision_id != "
          "self.pins.deployment_revision_id:",
       "        if False:",
       "credit_rate__admission_pins_model_serving_deployment_and_rate_card"),
    _m("the_pin_hardcodes_a_rate_card_version",
       "the pin records the card the catalog resolved, not a constant",
       V2_P, "                         rate_card_version=rate_card.rate_card_version,",
       '                         rate_card_version="rc_marlin2b_2026_09_provisional",',
       "credit_rate__a_rate_published_after_acceptance_does_not_move_the_job"),
    _m("publishing_a_rate_does_nothing",
       "the case really does change the published rate before settling",
       V2_FAKE, "        self.rate_cards[card.deployment_revision_id] = card", "        pass",
       "credit_rate__a_rate_published_after_acceptance_does_not_move_the_job"),
    _m("the_settlement_records_the_wrong_serving_revision",
       "a settlement records the serving revision the job was admitted with",
       V2_R, "        serving_version_id=admission.pins.serving_version_id,",
       "        serving_version_id=admission.pins.model_id,",
       "credit_rate__an_alias_moved_after_acceptance_does_not_move_the_job"),
    _m("an_unpriced_deployment_is_admitted",
       "a deployment with no approved active CREDIT card is unserveable, not free",
       V2_FAKE, "        rate_cards={prod.deployment_revision_id: card},",
       "        rate_cards={prod.deployment_revision_id: card,\n"
       "                    dev.deployment_revision_id: card},",
       "credit_rate__an_unknown_private_or_unpriced_model_is_refused"),
    _m("pin_admission_takes_a_caller_rate_card",
       "R45 extended: no parameter exists through which a caller supplies a price",
       V2_P, "def pin_admission(*, auth: AuthContextV2, requested_model: str,",
       "def pin_admission(*, auth: AuthContextV2, requested_model: str,\n"
       "                  caller_rate_card_version: str | None = None,",
       "credit_rate__a_caller_supplied_price_or_identity_is_refused"),
    _m("the_hold_rounds_like_a_charge",
       "the maximum hold rounds up; equal roundings under-reserve",
       V2_R, "        return Credit(money.maximum_hold(max_input_tokens, max_output_tokens, "
          "*self._rates()))",
       "        return Credit(money.debit(max_input_tokens, max_output_tokens, "
       "*self._rates()))",
       "credit_rate__the_hold_rounds_up_and_the_charge_rounds_half_up_once"),
    _m("the_charge_rounds_up",
       "the final charge rounds half up once, never away from the customer",
       V2_R, "        return Credit(money.debit(prompt_tokens, completion_tokens, *self._rates()))",
       "        return Credit(money.maximum_hold(prompt_tokens, completion_tokens, "
       "*self._rates()))",
       "credit_rate__the_hold_rounds_up_and_the_charge_rounds_half_up_once"),
    _m("a_settlement_may_exceed_the_hold",
       "usage beyond the reserved envelope is a platform incident, not a debit",
       V2_R, "    if charged > admission.maximum_hold:", "    if False:",
       "credit_rate__the_hold_rounds_up_and_the_charge_rounds_half_up_once"),
    _m("records_are_mutable",
       "admitted pins cannot be assigned in place (review B2b)",
       V2_R, '    model_config = ConfigDict(frozen=True, extra="forbid")',
       '    model_config = ConfigDict(frozen=False, extra="forbid")',
       "credit_rate__admitted_pins_are_immutable_and_every_pin_is_required"),
    _m("a_pin_is_optional",
       "every identity pin is required; none is left to a default (review B2c)",
       V2_R, "    deployment_revision_id: UuidStr\n    serving_version_id: UuidStr\n"
          "    rate_card_version: str\n    policy_version: str\n",
       "    deployment_revision_id: UuidStr\n    serving_version_id: UuidStr | None = None\n"
       "    rate_card_version: str\n    policy_version: str\n",
       "credit_rate__admitted_pins_are_immutable_and_every_pin_is_required"),
    _m("admission_pins_are_optional",
       "an admission always carries its pins (review B2c)",
       V2_R, "    pins: AdmissionPins\n    rate_card: RateCardSnapshot\n",
       "    pins: AdmissionPins | None = None\n    rate_card: RateCardSnapshot\n",
       "credit_rate__admitted_pins_are_immutable_and_every_pin_is_required"),
    _m("settle_ignores_certainty",
       "only authoritative engine usage settles a debit",
       V2_R, "    if usage.certainty is not records.UsageCertainty.authoritative:",
       "    if False:",
       "credit_rate__unknown_usage_is_never_settled"),

    # --- LAB-ACCESS ----------------------------------------------------------
    _m("a_role_grants_customer_content",
       "provider ownership alone yields no customer payload",
       V2_R, "        ProviderCapability.manage_dev_deployment,\n"
          "        ProviderCapability.run_evaluation,\n    }),",
       "        ProviderCapability.manage_dev_deployment,\n"
       "        ProviderCapability.run_evaluation,\n"
       "        ProviderCapability.read_customer_content,\n    }),",
       "lab_access__provider_ownership_alone_yields_no_customer_payload"),
    _m("content_access_needs_only_a_membership",
       "a current membership AND a current grant, every time",
       V2_R, "    if membership is None or grant is None:\n        return False",
       "    if membership is None:\n        return False\n    if grant is None:\n"
       "        return True",
       "lab_access__provider_ownership_alone_yields_no_customer_payload"),
    _m("revocation_is_ignored",
       "revocation blocks new access immediately, not at the next snapshot",
       V2_R, "        if self.revoked_at is not None and now >= self.revoked_at:\n"
          "            return False",
       "        if False:\n            return False",
       "lab_access__a_revoked_grant_blocks_access_immediately"),
    _m("expiry_is_ignored",
       "an expired grant authorizes nothing",
       V2_R, "        return self.expires_at is None or now < self.expires_at",
       "        return True",
       "lab_access__an_expired_grant_and_a_rival_provider_are_refused"),
    _m("the_recipient_provider_is_not_checked",
       "a grant names one recipient provider",
       V2_R, "                and provider_org_id == self.recipient_provider_org_id",
       "                and True",
       "lab_access__an_expired_grant_and_a_rival_provider_are_refused"),
    _m("the_purpose_is_not_checked",
       "capture, sharing, external judging and training are four permissions",
       V2_R, "                and purpose in self.purposes)", "                and True)",
       "lab_access__each_purpose_is_a_separate_permission"),
    _m("the_category_is_not_checked",
       "a grant names the data categories it covers",
       V2_R, "                and category in self.categories", "                and True",
       "lab_access__each_purpose_is_a_separate_permission"),
    _m("a_revoked_membership_still_permits",
       "a revoked membership and a foreign provider permit nothing",
       V2_R, "        if provider_org_id != self.provider_org_id or not self.is_current(now):\n"
          "            return False",
       "        if False:\n            return False",
       "lab_access__roles_default_deny_and_a_viewer_reaches_nothing"),
    _m("every_role_permits_everything",
       "a role permits exactly its own listed capabilities; unknown is denied",
       V2_R, "        return capability in ROLE_CAPABILITIES[self.role]", "        return True",
       "lab_access__roles_default_deny_and_a_viewer_reaches_nothing"),
    # --- F2P wire-in (item 7): the v2 composition ----------------------------------
    _m("contracts_v2_not_a_submodule", "contracts.v2 resolves by attribute access (item 1)",
       "contracts/__init__.py", '"tasklocal", "v2", "wire")', '"tasklocal", "wire")',
       "test_contracts_v2_resolves_by_attribute_access_like_every_submodule"),
    # items 3-4: the CREDIT regime of the fake JobStore (`ports.CreditJobStore`). Layered
    # refusals with no single store edit that breaks them - private/unknown/unpriced models
    # (the catalog's visibility *and* `pin_admission`), an operator or foreign wallet
    # (`resolve_wallet`), a mismatched card on the worker's view (`WorkV2`'s validator) -
    # are killed at the layer that owns them by the v2 mutants above.
    _m("credit_hold_lands_on_the_usd_wallet", "a CREDIT hold is reserved on the CREDIT wallet",
       S, "        wallet = self.credit_wallet(wallet_id) if terms is not None else self.wallet(request.org_id)",
       "        wallet = self.wallet(request.org_id)",
       "credit_admit__the_store_resolves_wallet_pins_and_card_and_holds_credit"),
    _m("credit_hold_ignores_the_ceilings", "the CREDIT hold covers both validated ceilings",
       S, "                                         request.max_output_tokens).raw(CREDIT)",
       "                                         0).raw(CREDIT)",
       "credit_admit__the_store_resolves_wallet_pins_and_card_and_holds_credit"),
    _m("credit_balance_checked_on_the_usd_wallet", "a CREDIT hold is checked on the CREDIT wallet",
       S, "        wallet = (self.credit_wallets.get(terms[3]) if terms is not None\n"
          "                  else self.wallets.get(org_id)) or _Wallet()",
       "        wallet = self.wallets.get(org_id) or _Wallet()",
       "credit_admit__the_store_resolves_wallet_pins_and_card_and_holds_credit",
       "credit_admit__refusals_leave_no_job_and_no_hold"),
    _m("get_owned_serves_a_credit_job", "a v1 read of a CREDIT job is not_found",
       S, "        if job.credit is not None:\n"
          '            raise errors.NotFound(f"job {job_handle} is a CREDIT job: use get_owned_credit")',
       "        if False:\n            pass",
       "credit_admit__the_store_resolves_wallet_pins_and_card_and_holds_credit"),
    _m("get_owned_credit_serves_a_legacy_job", "a CREDIT read of a legacy job is not_found (review M-2)",
       S, "        if job.credit is None:\n"
          '            raise errors.NotFound(f"job {job_handle} is not a CREDIT job")',
       "        if False:\n            pass",
       "credit_admit__the_store_resolves_wallet_pins_and_card_and_holds_credit"),
    _m("complete_credit_serves_a_legacy_job", "a legacy lease never settles as CREDIT (review M-2)",
       S, "        if job is not None and job.credit is None:\n"
          '            raise errors.NotFound(f"job {lease.job_id} is not a CREDIT job")',
       "        if False:\n            pass",
       "credit_settle__at_the_admitted_card_on_the_credit_wallet_only"),
    _m("complete_serves_a_credit_job", "v1 complete of a CREDIT job is not_found (review M-4)",
       S, "            if job.credit is not None and not credit:", "            if False:",
       "credit_settle__at_the_admitted_card_on_the_credit_wallet_only"),
    _m("complete_replays_a_terminal_credit_job", "v1 complete of a settled CREDIT job is not_found (MONEY-N1)",
       S, "            if job.credit is not None and not credit:",
       "            if job.credit is not None and not credit and not job.terminal:",
       "credit_settle__at_the_admitted_card_on_the_credit_wallet_only"),
    _m("credential_org_mismatch_ignored", "a key row admits only for its own organization (review M-2)",
       S, "        if auth.org_id != request.org_id:", "        if False:",
       "credit_admit__refusals_leave_no_job_and_no_hold"),
    _m("credit_wallet_by_organization", "the wallet comes from resolve_wallet, never the org (R66)",
       S, "        wallet = v2ports.resolve_wallet(auth, candidate)",
       "        wallet = next((w for w in self.wallet_directory.by_user.values()\n"
       "                       if w.personal_org_id == request.org_id), None) \\\n"
       "            or v2ports.resolve_wallet(auth, candidate)",
       "credit_admit__refusals_leave_no_job_and_no_hold"),
    _m("credit_replay_crosses_regimes", "one idempotency key never replays across regimes",
       S, "        if (job.credit is not None) is not credit:", "        if False:",
       "credit_admit__a_replay_is_pinned_and_never_crosses_regimes"),
    _m("credit_replay_not_marked", "a CREDIT replay is marked replayed",
       S, '                                               "replayed": True})',
       '                                               "replayed": False})',
       "credit_admit__a_replay_is_pinned_and_never_crosses_regimes"),
    _m("credit_settles_at_the_published_card", "a CREDIT job settles at its admitted card (R68)",
       S, "            candidate = (job.credit.rate_card.debit(usage.prompt_tokens,",
       "            candidate = (self.catalog.rate_cards[job.credit.pins.deployment_revision_id]"
       ".debit(usage.prompt_tokens,",
       "credit_settle__at_the_admitted_card_on_the_credit_wallet_only"),
    _m("credit_charge_in_the_usd_field", "a CREDIT charge never enters the USD debit field",
       S, "                    debit = money.ZERO\n", "",
       "credit_settle__at_the_admitted_card_on_the_credit_wallet_only"),
    _m("credit_settles_on_the_usd_wallet", "settlement moves the CREDIT wallet, not the USD one",
       S, "        wallet = self._wallet_of(job)", "        wallet = self.wallet(job.request.org_id)",
       "credit_settle__at_the_admitted_card_on_the_credit_wallet_only",
       "credit_settle__a_free_outcome_moves_no_credit"),
    _m("load_work_serves_a_credit_job", "the v1 work of a CREDIT job is not_found",
       S, "            if job.credit is not None:\n"
          '                raise errors.NotFound(f"job {job.id} is a CREDIT job: use load_work_credit")',
       "            if False:\n                pass",
       "credit_settle__at_the_admitted_card_on_the_credit_wallet_only",
       dies_by=("ValidationError",)),
    _m("load_work_credit_serves_a_legacy_job", "a CREDIT read of a legacy lease is not_found (MONEY-C1)",
       S, "            if job.credit is None:\n"
          '                raise errors.NotFound(f"job {job.id} is not a CREDIT job")',
       "            if False:\n                pass",
       "credit_settle__at_the_admitted_card_on_the_credit_wallet_only",
       dies_by=("AttributeError",)),
    _m("credit_free_outcome_settles", "a free CREDIT outcome has no settlement record",
       S, "        return settled, self.jobs[lease.job_id].settlement",
       "        return settled, (self.jobs[lease.job_id].settlement\n"
       "                         or settle(self.jobs[lease.job_id].credit, Usage.of(0, 0),\n"
       "                                   settled.settled_at))",
       "credit_settle__a_free_outcome_moves_no_credit"),
    _m("credit_reconcile_releases_on_the_usd_wallet",
       "the 24 h reconcile releases a CREDIT hold on its CREDIT wallet (F2P review M-1)",
       S, "            self._release_hold(self.credit_wallet(hold.wallet_id) if hold.wallet_id\n"
          "                               else self.wallet(hold.org_id), hold)",
       "            self._release_hold(self.wallet(hold.org_id), hold)",
       "credit_settle__an_unknown_usage_hold_is_reconciled_on_the_credit_wallet"),
    _m("credit_reconcile_releases_early", "a CREDIT held_unknown hold waits the fenced 24 h (MONEY-N2)",
       S, "            if not (job.terminal and fenced and now >= hold.reconcile_after):",
       "            if not (job.terminal and fenced and (hold.wallet_id is not None\n"
       "                                                 or now >= hold.reconcile_after)):",
       "credit_settle__an_unknown_usage_hold_is_reconciled_on_the_credit_wallet"),
    _m("credit_reconcile_on_the_first_credit_wallet", "each CREDIT hold is reconciled on its own wallet (MONEY-N2)",
       S, "            self._release_hold(self.credit_wallet(hold.wallet_id) if hold.wallet_id\n",
       "            self._release_hold(self.credit_wallet(next(iter(self.credit_wallets))) if hold.wallet_id\n",
       "credit_settle__an_unknown_usage_hold_is_reconciled_on_the_credit_wallet"),
    _m("legacy_projection_drops_a_half_usage", "a half-recorded usage row is refused (review M-7)",
       V2_R, "    if (prompt is None) != (completion is None):", "    if False:",
       "test_a_pre_cutover_row_keeps_its_absences_and_a_credit_row_cannot_have_them"),
    # coordinator addition (D2 handback): `Work.prompt_tokens`
    _m("work_prompt_tokens_unbounded", "Work.prompt_tokens never exceeds max_input_tokens",
       R, "        if self.prompt_tokens is not None and self.prompt_tokens > self.request.max_input_tokens:",
       "        if False:",
       "test_work_carries_preparations_prompt_count_within_the_admitted_ceiling"),
    _m("work_prompt_tokens_negative", "Work.prompt_tokens is nonnegative",
       R, "    prompt_tokens: int | None = Field(default=None, ge=0)",
       "    prompt_tokens: int | None = None",
       "test_work_carries_preparations_prompt_count_within_the_admitted_ceiling"),
    _m("work_v2_prompt_tokens_unbounded", "WorkV2.prompt_tokens never exceeds max_input_tokens",
       V2_R, "                and self.prompt_tokens > self.request.request.max_input_tokens):",
       "                and False):",
       "test_work_v2_carries_the_prompt_count_within_the_admitted_ceiling"),
    _m("v2_fixture_table_unclaimed", "every file under fixtures/v2 is claimed (item 7)",
       V2_FX, 'TABLES = ("map.json", "money_unit_cases.json")', 'TABLES = ("map.json",)',
       "test_the_fixture_root_holds_exactly_the_two_revisions"),
    # item 10: the projection over D1R's real pre-cutover rows
    _m("legacy_projection_invents_settled", "a pre-cutover row with no outcome keeps none",
       V2_R, "        outcome=records.SettlementState(outcome) if outcome is not None else None,",
       "        outcome=records.SettlementState(outcome or \"settled\"),",
       "test_a_pre_cutover_row_keeps_its_absences_and_a_credit_row_cannot_have_them"),
    _m("legacy_projection_invents_zero_usage", "a pre-cutover row with no tokens has no usage",
       V2_R, "    if prompt is not None and completion is not None:",
       "    if True:\n        prompt, completion = prompt or 0, completion or 0",
       "test_a_pre_cutover_row_keeps_its_absences_and_a_credit_row_cannot_have_them"),
    _m("credit_row_without_usage", "a CREDIT row carries its usage and outcome",
       V2_R, "            if self.usage is None or self.outcome is None:", "            if False:",
       "test_a_pre_cutover_row_keeps_its_absences_and_a_credit_row_cannot_have_them"),
    _m("v2_suite_runs_nothing", "V2_SUITES runs the whole exported v2 list (item 2)",
       "contracts/conformance/__init__.py", '"v2": (v2_cases, run_v2_conformance),',
       '"v2": (v2_cases, lambda factory=None: 0),',
       "test_the_conformance_package_exports_the_v2_suite"),
    # --- F2R-B NB-3: the cursor secret's exact boundary ------------------------------
    Mutant(name="DEPLOY-06", invariant="a 15-character cursor secret is refused",
           file="config.py",
           old="    if _configured(secret) and len(secret) < MIN_CONSOLE_CURSOR_SECRET_CHARS:",
           new="    if _configured(secret) and len(secret) < MIN_CONSOLE_CURSOR_SECRET_CHARS - 1:",
           cases=("test_the_cursor_secret_bound_is_exactly_sixteen_characters",)),
    Mutant(name="DEPLOY-07", invariant="a 16-character cursor secret is accepted",
           file="config.py",
           old="    if _configured(secret) and len(secret) < MIN_CONSOLE_CURSOR_SECRET_CHARS:",
           new="    if _configured(secret) and len(secret) <= MIN_CONSOLE_CURSOR_SECRET_CHARS:",
           cases=("test_the_cursor_secret_bound_is_exactly_sixteen_characters",)),
    # --- F2P wire-in item 5: the v2 configuration names ------------------------------
    Mutant(name="CFG-V2-01", invariant="ACCOUNTING_REGIME is one of the two v2 regimes",
           file="config.py", old="    if deployment.accounting_regime not in ACCOUNTING_REGIMES:",
           new="    if False:", cases=("test_the_accounting_regime_is_a_v2_regime",)),
    Mutant(name="CFG-V2-02", invariant="a CREDIT deployment needs an approved card (R69)",
           file="config.py", old="    if deployment.accounting_regime == CREDIT_REGIME \\\n",
           new="    if False \\\n",
           cases=("test_a_credit_deployment_needs_an_approved_rate_card",)),
    Mutant(name="CFG-V2-03", invariant="a unit-typed ceiling is never negative",
           file="config.py", old="        if value.is_negative:", new="        if False:",
           cases=("test_nonsense_numbers_are_refused_at_the_boundary",)),
    Mutant(name="CFG-V2-04", invariant="ACTIVE_RATE_CARD_VERSION is exact text",
           file="config.py", old="    if card != card.strip():", new="    if False:",
           cases=("test_the_active_rate_card_version_is_exact_text",)),
    Mutant(name="CFG-V2-05", invariant="no provider-dev allocation until an operator sets one",
           file="contracts/limits.py",
           old='    provider_dev_allocation_ceiling_credit: Credit = Credit("0.00000000")',
           new='    provider_dev_allocation_ceiling_credit: Credit = Credit("1000.00000000")',
           cases=("test_the_allocation_ceiling_is_a_credit_amount_defaulting_to_nothing",
                  "test_every_configuration_name_and_default_is_frozen")),
    Mutant(name="CFG-V2-06", invariant="a zero pilot bound refuses startup in every mode (review M-3)",
           file="config.py",
           old="        if getattr(pilot, name) <= 0:\n"
               '            raise RuntimeMisconfigured(mode, detail=f"{env_name(name)} must be positive")',
           new="        if False:\n            pass",
           cases=("test_a_bad_deployment_value_refuses_before_anything_mounts",
                  "test_a_zero_index_cap_refuses_to_start")),
    Mutant(name="CFG-V2-07", invariant="the index caps are pilot bounds a zero disables (review M-3)",
           file="config.py", old='    "max_index_items", "max_index_bytes",\n', new="",
           cases=("test_a_zero_index_cap_refuses_to_start",)),
    Mutant(name="CFG-V2-08", invariant="ACTIVE_RATE_CARD_VERSION is exact text at startup (review M-6)",
           file="config.py",
           old="            and pilot.active_rate_card_version != pilot.active_rate_card_version.strip():",
           new="            and False:", cases=("test_a_credit_deployment_needs_an_approved_rate_card",)),
    Mutant(name="CFG-V2-09", invariant="a whitespace-only card is not configured (review CFG-2)",
           file="config.py", old="            and not _configured(pilot.active_rate_card_version):",
           new="            and not bool(pilot.active_rate_card_version):",
           cases=("test_a_credit_deployment_needs_an_approved_rate_card",)),
    Mutant(name="CFG-V2-11", invariant="a whitespace-only card is unset in every regime (CONF-N2)",
           file="config.py", old="    if _configured(pilot.active_rate_card_version) \\\n",
           new="    if pilot.active_rate_card_version \\\n",
           cases=("test_a_credit_deployment_needs_an_approved_rate_card",)),
    Mutant(name="CFG-V2-12", invariant="a padded card is refused in every regime (CONF-N2)",
           file="config.py", old="    if _configured(pilot.active_rate_card_version) \\\n",
           new="    if deployment.accounting_regime == CREDIT_REGIME \\\n"
               "            and _configured(pilot.active_rate_card_version) \\\n",
           cases=("test_a_credit_deployment_needs_an_approved_rate_card",)),
    Mutant(name="CFG-V2-13", invariant="a card padded on the right only is refused (CONF-N1)",
           file="config.py",
           old="            and pilot.active_rate_card_version != pilot.active_rate_card_version.strip():",
           new="            and pilot.active_rate_card_version != pilot.active_rate_card_version.lstrip():",
           cases=("test_a_credit_deployment_needs_an_approved_rate_card",)),
    Mutant(name="CFG-V2-14", invariant="a card padded on the left only is refused (CONF-N1)",
           file="config.py",
           old="            and pilot.active_rate_card_version != pilot.active_rate_card_version.strip():",
           new="            and pilot.active_rate_card_version != pilot.active_rate_card_version.rstrip():",
           cases=("test_a_credit_deployment_needs_an_approved_rate_card",)),
    Mutant(name="CFG-V2-10", invariant="a deployment text value is measured stripped (review CFG-6)",
           file="config.py",
           old="        values[f.name] = (raw.strip() if isinstance(getattr(DEPLOYMENT_DEFAULTS, f.name), str)",
           new="        values[f.name] = (raw if isinstance(getattr(DEPLOYMENT_DEFAULTS, f.name), str)",
           cases=("test_the_cursor_secret_bound_is_exactly_sixteen_characters",)),
    Mutant(name="CFG-V2-15", invariant="a deployment text value is stripped on the left too (CONF-N3)",
           file="config.py", old="        values[f.name] = (raw.strip() if isinstance(getattr(DEPLOYMENT_DEFAULTS, f.name), str)",
           new="        values[f.name] = (raw.rstrip() if isinstance(getattr(DEPLOYMENT_DEFAULTS, f.name), str)",
           cases=("test_the_cursor_secret_bound_is_exactly_sixteen_characters",)),
    Mutant(name="CFG-V2-16", invariant="a deployment text value is stripped on the right too (CONF-N3)",
           file="config.py", old="        values[f.name] = (raw.strip() if isinstance(getattr(DEPLOYMENT_DEFAULTS, f.name), str)",
           new="        values[f.name] = (raw.lstrip() if isinstance(getattr(DEPLOYMENT_DEFAULTS, f.name), str)",
           cases=("test_the_cursor_secret_bound_is_exactly_sixteen_characters",)),
    # --- F2R: the two money-context mutants the audit found surviving ----------------
    _m("money_context_default_precision", "money arithmetic runs at 40 digits",
       MONEY, "        prec=40, rounding=decimal.ROUND_HALF_EVEN,",
       "        prec=28, rounding=decimal.ROUND_HALF_EVEN,", "test_the_money_context_is_pinned"),
    _m("money_context_traps_nothing", "an invalid money operation raises, never NaN",
       MONEY, "        traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow],",
       "        traps=[],", "test_the_money_context_is_pinned"),
    # --- F2R lane A item 5: judge sample ids and the judge-sample DTO -------------------
    _m("judge_run_duplicate_sample_ids", "a run's sample ids are unique",
       R, "        if len(set(self.sample_ids)) != len(self.sample_ids):", "        if False:",
       "test_judge_run_sample_ids_are_unique_lowercase_uuid4"),
    _m("judge_run_sample_ids_any_string", "a sample id is a lower-case UUIDv4",
       R, "    sample_ids: tuple[UuidStr, ...] = ()", "    sample_ids: tuple[str, ...] = ()",
       "test_judge_run_sample_ids_are_unique_lowercase_uuid4"),
    _m("judge_sample_request_id_optional", "a judge sample states its request, even as null",
       R, "    request_id: UuidStr | None\n    scores:", "    request_id: UuidStr | None = None\n    scores:",
       "test_the_judge_sample_dto_is_the_consoles_four_fields"),
    _m("accounting_regime_respelled", "the regime is spelled as the console spells it (IR-7)",
       R, '    legacy_usd = "legacy_usd"', '    legacy_usd = "legacy"',
       "test_enum_values_are_frozen"),
    # --- F2R lane A item 3: the metadata reserve is charged the serialized row (R81) ----
    _m("metadata_charged_as_declared", "an under-declared record is charged what it costs",
       TA, "        return max(envelope.metadata_bytes, serialized)",
       "        return envelope.metadata_bytes",
       "trace_bounds__metadata_exhaustion_drops_with_counters"),
    _m("metadata_charged_as_serialized", "an over-declared record is charged what it declared",
       TA, "        return max(envelope.metadata_bytes, serialized)", "        return serialized",
       "trace_bounds__metadata_exhaustion_drops_with_counters"),
    # F2R-A review: a zero declaration is the canonical under-declaration, and the case
    # declares exactly that, so a charge that waives it cannot pass.
    _m("metadata_charge_skips_zero", "a record declaring 0 metadata bytes is still charged",
       TA, "        metadata = self.metadata_charge(envelope, serialized)",
       "        metadata = 0 if envelope.metadata_bytes == 0 else "
       "self.metadata_charge(envelope, serialized)",
       "trace_bounds__metadata_exhaustion_drops_with_counters"),
    # --- F cancel-cause: `JobStore.cancel(..., *, cause)` (R21; G2 D-new, D5 item 3) ----
    _m("cancel_port_default_is_not_the_client", "an existing cancel(org, handle) still means client_cancelled",
       "contracts/ports.py",
       "                     cause: TerminalCause = TerminalCause.client_cancelled) -> TerminalOutcome:",
       "                     cause: TerminalCause = TerminalCause.client_disconnected) -> TerminalOutcome:",
       "test_dur_settle__cancel_takes_a_keyword_cause_that_defaults_to_client_cancelled"),
    _m("cancel_causes_widened", "only the client's causes and sync_deadline may be named",
       R, "    TerminalCause.client_cancelled, TerminalCause.client_disconnected, TerminalCause.sync_deadline,\n})",
       "    TerminalCause.client_cancelled, TerminalCause.client_disconnected, TerminalCause.sync_deadline,\n"
       "    TerminalCause.completed,\n})",
       "test_dur_settle__cancel_takes_a_keyword_cause_that_defaults_to_client_cancelled",
       "dur_settle__cancel_refuses_any_other_cause_and_changes_nothing"),
    _m("cancel_cause_dropped", "a cancel records the cause it is given (R21)",
       S, "            outcome = self._terminalize(job, cause, None, None, JobState.cancelled)",
       "            outcome = self._terminalize(job, TerminalCause.client_cancelled, None, None,"
       " JobState.cancelled)",
       "dur_settle__cancel_records_its_cause_and_settles_by_r21"),
    _m("credit_cancel_cause_dropped", "a CREDIT cancel records the cause it is given (R21)",
       S, "            outcome = self._terminalize(job, cause, None, None, JobState.cancelled)",
       "            outcome = self._terminalize(job, TerminalCause.client_cancelled, None, None,"
       " JobState.cancelled)",
       "credit_settle__cancel_records_its_cause_and_settles_by_r21"),
    _m("cancel_unknown_cause_mapped_to_client_cancelled", "a refused cause is refused, not recorded as client_cancelled",
       S, '            raise errors.InvalidRequest(f"{cause!r} is not a cancellation cause")',
       "            cause = TerminalCause.client_cancelled",
       "dur_settle__cancel_refuses_any_other_cause_and_changes_nothing"),
    _m("cancel_accepts_any_cause", "any other cause is invalid_request and changes nothing",
       S, "        if cause not in CANCEL_CAUSES:", "        if False:",
       "dur_settle__cancel_refuses_any_other_cause_and_changes_nothing"),
    _m("cancel_sync_deadline_charged_to_the_client", "sync_deadline is platform-absorbed (R21)",
       R, "BILLABLE_CAUSES = frozenset({\n    TerminalCause.completed, TerminalCause.client_cancelled, TerminalCause.client_disconnected,\n})",
       "BILLABLE_CAUSES = frozenset({\n    TerminalCause.completed, TerminalCause.client_cancelled, TerminalCause.client_disconnected,\n"
       "    TerminalCause.sync_deadline,\n})",
       "dur_settle__cancel_records_its_cause_and_settles_by_r21",
       "credit_settle__cancel_records_its_cause_and_settles_by_r21",
       "dur_settle__only_three_causes_can_charge"),
    _m("cancel_client_disconnected_absorbed_by_the_platform", "client_disconnected is the client's cause (R21)",
       R, "BILLABLE_CAUSES = frozenset({\n    TerminalCause.completed, TerminalCause.client_cancelled, TerminalCause.client_disconnected,\n})",
       "BILLABLE_CAUSES = frozenset({\n    TerminalCause.completed, TerminalCause.client_cancelled,\n})",
       "dur_settle__cancel_records_its_cause_and_settles_by_r21",
       "credit_settle__cancel_records_its_cause_and_settles_by_r21"),
)


# ======================================================================================
# The shared runner (F2R item 9)
# ======================================================================================
# Eight copies of `run_mutant` used to live under `tests/`, and they disagreed about
# what a kill is: five classified a death by *no* rule at all (`--tb=no`, so an
# undeclared `TypeError` counted as a kill), two copied pyproject.toml into the mutated
# tree and five did not (so the copy collected under different import rules than the
# suite was written for), one required every named case to notice and the others were
# satisfied by any one of them, and none of them gave a subprocess its own bytecode
# cache or temporary directory. This is the one runner; each track supplies a `Runner`
# describing its target and keeps its own list, its own self-tests and its own
# declared kill modes.
#
# The rules, in one place:
#
# * a mutant whose anchor appears **zero or two** times is `misdeclared` - it would
#   otherwise edit nothing, or edit whichever line came first;
# * a mutant that does not **compile** is `broken_runner`, decided by `compile()` here
#   rather than inferred from a pytest exit code;
# * every failing test must name one of the mutant's own cases, so a wider defect,
#   an import error or a collection error is `broken_runner`;
# * every death must be assertion-shaped (an `assert`, `AssertionError`, or pytest's
#   `Failed` for a `raises` block that did not raise) or **declared** in `dies_by`;
# * a run that collects nothing, skips its cases, or (where the track asks for it)
#   leaves a named case unbroken is `misdeclared`;
# * only `killed` is accepted by a suite.

class Outcome(enum.StrEnum):
    """What one mutant run proved. Only `killed` counts."""

    killed = "killed"                  # pytest failed, and only named cases failed
    survived = "survived"              # the suite passed with the defect in place
    broken_runner = "broken_runner"    # syntax, import, collection, crash or timeout
    misdeclared = "misdeclared"        # anchor count wrong, no case, or a case that never ran


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    detail: str = ""

    @property
    def killed(self) -> bool:
        return self.outcome is Outcome.killed

    @property
    def ok(self) -> bool:
        """A run the suite accepts: only a kill does."""
        return self.killed


#: How an honest kill dies. A rewritten assert reports as the bare expression
#: (`path:12: assert 3 == 4`) rather than as `AssertionError`, so both spellings are the
#: same outcome; `Failed` is `pytest.raises` reporting DID NOT RAISE.
ASSERTION_DEATHS = frozenset({"AssertionError", "Failed"})


def _domain_deaths() -> frozenset[str]:
    """Every `DomainError` subclass name, read from the contract rather than listed.

    r1 round 3 fixed the rule this enforces: a port is a trust boundary, so a kill that
    depends on an **untyped** exception is a case a real adapter could fail for the wrong
    reason - but the typed refusal the contract *promises* is a different thing. A case
    that says "this call is `not_found`" and gets `NotFound` out of a mutated store has
    observed exactly the invariant it claims, so the whole typed hierarchy is an honest
    death. `TypeError`, `NameError`, `KeyError`, `AttributeError`, `ValidationError`,
    `RecursionError` and the rest are not, and must be declared per mutant in `dies_by`.
    """
    if str(API_DIR) not in sys.path:          # a list run as a script by its path
        sys.path.insert(0, str(API_DIR))
    from infrx.contracts import errors

    found, pending = set(), [errors.DomainError]
    while pending:
        cls = pending.pop()
        if cls.__name__ in found:
            continue
        found.add(cls.__name__)
        pending.extend(cls.__subclasses__())
    return frozenset(found)


DOMAIN_DEATHS = _domain_deaths()
#: Anything that is not one of these has to be declared by the mutant that dies by it.
HONEST_DEATHS = ASSERTION_DEATHS | DOMAIN_DEATHS
# Generous for a handful of cases with no sleeps in them: this exists so a mutant that
# makes a case hang cannot hang the suite, not as a performance budget.
NESTED_TIMEOUT_S = 300
# pytest exit codes: 0 all passed, 1 tests failed, 2 interrupted, 3 internal error,
# 4 usage error, 5 no tests collected. Only 1 can mean "the case noticed".
PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED = 0, 1
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) ([^\s:]+(?:::[^\s]+)?)")
# `--tb=line` prints one `path:lineno: <message>` line per failure.
_DEATH = re.compile(r"^(?P<where>\S*?):\d+: (?P<message>.+)$")
_CLASS = re.compile(r"^(?P<cls>[A-Za-z_][A-Za-z0-9_.]*)(?::|$)")
# `errors?` too (R83 amendment (c)): a fixture or collection error with no test run is a
# broken copy, not a mutant that named no case - and `-rfE` lists the ERROR ids, which
# `-rf` alone left out (such a run read as `survived`).
_RAN = re.compile(r"(\d+) (?:passed|failed|skipped|errors?)")
# R83 amendment (a): an `assert` inside the package is the package's own guard, not the
# case observing anything. The exported conformance cases live in the package, so their
# assertions are still the case's own.
_PACKAGE_FRAME = re.compile(r"(?:^|/)infrx/(?!contracts/conformance/)")


@dataclass(frozen=True)
class Runner:
    """One track's target for the shared runner.

    `targets` are pytest paths; `targets_for` narrows them per mutant (track G collects
    only the files defining the named cases, because collecting its whole suite imports
    FastAPI once per mutant). `require_every_case` is track Q's stricter rule: a mutant
    may not claim coverage from a case that cannot see it. `layout` builds a copy of a
    different shape (track I mutates `deploy/`, outside the package, and one case reads
    a repository file): it fills the temporary root and returns the directory pytest runs
    in; a mutant's `file` is then relative to that directory joined with `package`.
    `env` names caller variables the copy inherits (a lane's own Valkey port, Q3 HON-3);
    every other variable of the caller stays out.
    """

    name: str
    targets: tuple[str, ...] = ()
    extra_args: tuple[str, ...] = ()
    package: str = PACKAGE
    timeout_s: float = NESTED_TIMEOUT_S
    require_every_case: bool = False
    targets_for: "Callable[[tuple[str, ...]], Sequence[str]] | None" = None
    layout: "Callable[[pathlib.Path], pathlib.Path] | None" = None
    env: tuple[str, ...] = ()

    def select(self, cases: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(self.targets_for(cases)) if self.targets_for else self.targets


CONTRACTS = None          # set below, once `Runner` exists (the module's own target)


def _failing_ids(stdout: str) -> tuple[list[str], list[str]]:
    """(failed test ids, errored test ids) from a `-rf`-style summary."""
    failed, errored = [], []
    for line in stdout.splitlines():
        match = _FAILED_LINE.match(line.strip())
        if match:
            (errored if line.strip().startswith("ERROR") else failed).append(match.group(1))
    return failed, errored


def _death_kinds(stdout: str) -> list[tuple[str, str]]:
    """Every failure as `(where it was raised, how it died)`.

    `--tb=line` prints one `path:lineno: <message>` line per failure, so both halves are
    there. Only the FAILURES section is read: `path:lineno: Something: text` is also the
    shape of pytest's *warnings* summary, and scanning the whole output reported every
    `DeprecationWarning` a suite emits as a death (measured on track G: 17 lethal mutants
    turned into `broken_runner`). The class name is reduced to its last dotted component,
    so a declaration names a class (`RuntimeMisconfigured`), not an import path that moves
    when the module does.
    """
    deaths: list[tuple[str, str]] = []
    inside = False
    for line in stdout.splitlines():
        if line.startswith("=") and "FAILURES" in line:
            inside = True
            continue
        if line.startswith("=") and "FAILURES" not in line:
            inside = False
        if not inside:
            continue
        line = line.strip()
        if line.startswith(("FAILED", "ERROR", "E ")):
            continue
        match = _DEATH.match(line)
        if not match:
            continue
        where, message = match.group("where"), match.group("message")
        if message.startswith("assert"):
            deaths.append((where, "AssertionError"))
            continue
        named = _CLASS.match(message)
        deaths.append((where, named.group("cls").rsplit(".", 1)[-1] if named else "unknown"))
    return deaths


def _undeclared(deaths: list[tuple[str, str]], allowed: set[str],
                declared: "set[str] | tuple[str, ...]" = ()) -> list[str]:
    """The deaths that are not evidence, as `class@file`.

    Two kinds of death are evidence: an assertion (including pytest's `Failed` for a
    `raises` block that did not raise), and the typed answer the contract promises - any
    `DomainError` subclass. Everything else is a case that **crashed** rather than
    asserted; it may well have noticed the defect, but "noticed something" is not the
    invariant it claims, so the mutant has to say so: `dies_by=("KeyError",)` names the
    kill mode and makes it reviewable. This is track J's round-3 review rule, applied to
    every list (`a test that crashed is not a test that noticed`); the file is reported
    with the class so a reader can see *where* it crashed without opening the log.

    An `AssertionError` raised **inside the package** (outside the exported conformance
    cases) is the package's guard firing, not the case asserting: it is evidence only when
    the mutant declares `AssertionError` in `dies_by` (R83 amendment (a)).
    """
    return [f"{kind}@{where.rsplit('/', 1)[-1]}" for where, kind in deaths
            if kind not in allowed
            or (kind in ASSERTION_DEATHS and _PACKAGE_FRAME.search(where)
                and kind not in declared)]


def case_of(test_id: str, cases: tuple[str, ...]) -> str | None:
    """Which of `cases` this node id names, or None.

    Two spellings, because both are how a track names a case: the test **function**
    (`tests/w/test_engine.py::test_api_stream__x`) and a **parametrization id** (the
    exported conformance cases run as `test_suite[jobs-dur_admit__x]`). Substring
    matching was the old rule in four copies and it let `test_a` claim `test_ab`'s
    failure; both spellings here are exact.
    """
    # The parametrization is stripped BEFORE the `::` split, because a parameter may
    # contain one: `test_no_internal_address_form_is_reachable[2001:20::1]` splits to
    # `1]` the other way round, and the case then reads as a stray failure.
    head, _, params = test_id.partition("[")
    function = head.rsplit("::", 1)[-1]
    parts = params.rstrip("]").split("-") if params else []
    for case in cases:
        if function == case or case in parts:
            return case
    return None


def _copy(root: pathlib.Path, runner: Runner) -> pathlib.Path:
    """The default copy: the package and the tests; returns where pytest runs."""
    if runner.layout is not None:
        return runner.layout(root)
    junk = shutil.ignore_patterns("__pycache__")
    shutil.copytree(API_DIR / runner.package, root / runner.package, ignore=junk)
    shutil.copytree(API_DIR / "tests", root / "tests", ignore=junk)
    # The committed pytest configuration travels with the copy (R48: importlib import
    # mode, `pythonpath=["."]`), or the copy collects under different import rules than
    # the suite was written for.
    shutil.copy2(API_DIR / "pyproject.toml", root / "pyproject.toml")
    return root


def _prepare(api: pathlib.Path, mutant: "Mutant", runner: Runner) -> Result | None:
    """Apply the one edit to the copy, and refuse an edit that cannot be evidence."""
    target = api / runner.package / mutant.file
    source = target.read_text()
    found = source.count(mutant.old)
    if found != mutant.occurrences:
        # Zero: the list no longer matches the code. Two: the edit would land on
        # whichever line came first, which is not the declared defect.
        return Result(Outcome.misdeclared,
                      f"anchor appears {found} times in {mutant.file}, expected "
                      f"{mutant.occurrences}: {mutant.old[:60]!r}")
    mutated = source.replace(mutant.old, mutant.new, mutant.occurrences)
    try:
        compile(mutated, str(target), "exec")
    except SyntaxError as broken:
        # Decided here rather than read out of a pytest exit code: a copy that does not
        # compile proves nothing, whatever the suite then says about it.
        return Result(Outcome.broken_runner,
                      f"the mutated {mutant.file} does not compile: {broken.msg} "
                      f"(line {broken.lineno})")
    target.write_text(mutated)
    return None


def _pytest(root: pathlib.Path, api: pathlib.Path, runner: Runner, targets, selection: str,
            timeout_s: float) -> subprocess.CompletedProcess:
    """One pytest process over `targets -k selection` in a copy, with its own bytecode
    cache, temporary directory and home inside that copy."""
    cache, temp = root / ".pycache", root / ".tmp"
    cache.mkdir(exist_ok=True)
    temp.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header",
         "-p", "no:cacheprovider", "-rfE", "--tb=line",
         *runner.extra_args, *targets, "-k", selection],
        cwd=api, capture_output=True, text=True, timeout=timeout_s,
        env={**{name: os.environ[name] for name in runner.env if name in os.environ},
             "PYTHONPATH": str(api), "PATH": "/usr/bin:/bin", "HOME": str(temp),
             "PYTHONPYCACHEPREFIX": str(cache), "TMPDIR": str(temp)})


# R83 amendment (b): the pristine baseline. A mutant is evidence only if its cases pass
# on the unmutated tree; otherwise a case that fails for its own reasons "kills" every
# mutant naming it. Once per list and runner per process (one extra pytest run, not one
# per mutant), over the union of the list's named cases.
BASELINE_TIMEOUT_S = 1800
_BASELINES: "dict[tuple[Runner, tuple[str, ...]], Result | None]" = {}


def _siblings(mutant: "Mutant") -> "tuple[Mutant, ...]":
    """The list (`MUTANTS` of a loaded module) this mutant belongs to, or `()` for a
    runner self-test's one-off mutant."""
    for module in list(sys.modules.values()):
        listed = getattr(module, "MUTANTS", None)
        if isinstance(listed, tuple) and any(m is mutant for m in listed):
            return listed
    return ()


def pristine(cases: tuple[str, ...], runner: Runner) -> Result | None:
    """None if every named case passes on the unmutated copy, else the refusal."""
    key = (runner, cases)
    if key not in _BASELINES:
        _BASELINES[key] = _baseline(cases, runner)
    return _BASELINES[key]


def _baseline(cases: tuple[str, ...], runner: Runner) -> Result | None:
    targets = runner.select(cases)
    with tempfile.TemporaryDirectory(prefix=f"{runner.name}-pristine-") as tmp:
        root = pathlib.Path(tmp)
        api = _copy(root, runner)
        try:
            done = _pytest(root, api, runner, targets, " or ".join(cases), BASELINE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return Result(Outcome.broken_runner,
                          f"pristine baseline did not finish within {BASELINE_TIMEOUT_S}s")
    if done.returncode == PYTEST_ALL_PASSED:
        return None
    failed, errored = _failing_ids(done.stdout or "")
    lines = ((done.stdout or done.stderr) or "").strip().splitlines()
    return Result(Outcome.broken_runner,
                  f"pristine baseline: the unmutated tree fails the list's own cases "
                  f"(pytest exit {done.returncode}): {(failed + errored)[:3] or lines[-1:]}")


def run_mutant(mutant: "Mutant", runner: Runner | None = None) -> Result:
    """Apply one mutant to a throwaway copy of the tree and run the cases it names.

    Every subprocess gets its **own** bytecode cache and temporary directory inside that
    copy (`PYTHONPYCACHEPREFIX`, `TMPDIR`), so two mutants running in parallel - or a
    mutant whose case writes a temporary file - cannot share state with each other or
    with the worktree. Nothing is ever written inside the worktree.
    """
    runner = runner or CONTRACTS
    if not mutant.cases:
        return Result(Outcome.misdeclared, "declares no case")
    targets = runner.select(mutant.cases)
    if not targets:
        return Result(Outcome.misdeclared,
                      f"no target defines any of {list(mutant.cases)}")
    siblings = _siblings(mutant)
    if siblings:
        refused = pristine(tuple(sorted({case for m in siblings for case in m.cases})), runner)
        if refused is not None:
            return refused
    with tempfile.TemporaryDirectory(prefix=f"{runner.name}-mutant-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        api = _copy(root, runner)
        refused = _prepare(api, mutant, runner)
        if refused is not None:
            return refused
        selection = " or ".join(mutant.cases)
        try:
            done = _pytest(root, api, runner, targets, selection, runner.timeout_s)
        except subprocess.TimeoutExpired:
            # A defect that makes a case hang is real, but a hang is not the proof the
            # contract asks for, and a runner that waits for ever proves nothing at all.
            return Result(Outcome.broken_runner,
                          f"the named cases did not finish within {runner.timeout_s}s")
        stdout = done.stdout or ""
        lines = (stdout or done.stderr).strip().splitlines()
        summary = lines[-1] if lines else "no output"
        if done.returncode not in (PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED):
            # 2-5: interrupted, internal error, usage error, nothing collected. The
            # mutant may well be lethal, but this run did not prove it.
            return Result(Outcome.broken_runner, f"pytest exit {done.returncode}: {summary}")
        if not _RAN.search(summary) or "no tests ran" in summary:
            return Result(Outcome.misdeclared, f"no case matched {selection!r}: {summary}")
        failed, errored = _failing_ids(stdout)
        if errored:
            return Result(Outcome.broken_runner, f"errors outside the named cases: {errored[:3]}")
        if done.returncode == PYTEST_ALL_PASSED or not failed:
            if "skipped" in summary and not failed:
                return Result(Outcome.misdeclared, f"its cases were skipped: {summary}")
            return Result(Outcome.survived, summary)
        noticed = {case_of(test_id, mutant.cases) for test_id in failed}
        stray = [test_id for test_id in failed if case_of(test_id, mutant.cases) is None]
        if stray:
            # Something the mutant did not name broke: an import-time failure, or a
            # defect with wider reach than the declaration claims.
            return Result(Outcome.broken_runner,
                          f"failures outside the named cases: {stray[:3]}")
        crashed = sorted(set(_undeclared(_death_kinds(stdout),
                                         HONEST_DEATHS | set(mutant.dies_by), mutant.dies_by)))
        if crashed:
            # The mutated code blew up rather than answering. The case may well have
            # noticed the defect, but a crash inside the package is not a proof: declare
            # the exception in `dies_by` if the invariant really is "this never raises".
            return Result(Outcome.broken_runner,
                          f"undeclared exception deaths {crashed}: {summary}")
        if runner.require_every_case:
            unproven = [case for case in mutant.cases if case not in noticed]
            if unproven:
                return Result(Outcome.misdeclared,
                              f"named cases that did not notice: {unproven}")
        return Result(Outcome.killed, summary)


# The exported cases, plus the record/config tests a record-level invariant is proved by
# (F2R items 5 and 7: a validator on `records` or `config` has no port case to die in).
# F2P wire-in item 6: the v2 conformance file is one more target of the same runner, so
# the v2 list is part of this one and there is no second classifier anywhere.
CONTRACTS = Runner(name="contracts", targets=("tests/contracts/test_conformance.py",
                                              "tests/contracts/test_fixtures.py",
                                              "tests/contracts/test_money.py",
                                              "tests/contracts/test_config_and_imports.py",
                                              "tests/contracts/v2/test_conformance_v2.py",
                                              "tests/contracts/test_cancel_cause.py"))


def main(mutants: "tuple[Mutant, ...]" = (), runner: Runner | None = None,
         description: str = "run the contracts-v1 mutation list") -> int:
    """The `python -m tests.<track>.mutants` entry point, shared by every list."""
    mutants = mutants or MUTANTS
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("names", nargs="*", help="mutants to run (default: all)")
    parser.add_argument("--list", action="store_true", help="print the list and exit")
    args = parser.parse_args()
    if args.list:
        for mutant in mutants:
            print(f"{mutant.name:52s} {mutant.invariant}")
        print(f"\n{len(mutants)} mutants over "
              f"{len({case for m in mutants for case in m.cases})} named cases")
        return 0
    chosen = [m for m in mutants if not args.names or m.name in args.names]
    bad: dict[str, list[str]] = {}
    for mutant in chosen:
        result = run_mutant(mutant, runner)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}", flush=True)
        if not result.killed:
            bad.setdefault(result.outcome.value, []).append(mutant.name)
    failures = sum(len(names) for names in bad.values())
    print(f"\n{len(chosen) - failures}/{len(chosen)} killed"
          + "".join(f"; {outcome}: {names}" for outcome, names in sorted(bad.items())))
    return 1 if bad else 0


def assertion_kill(check, *args, dies_by: tuple[type[BaseException], ...] = ()) -> Result:
    """The same kill rule for a check that runs **in process** instead of as a pytest
    subprocess: only an `AssertionError` (or a declared exception class) is a kill.

    Track D's migration list builds a database from mutated SQL and calls one of its
    checks; it cannot use the pytest runner above, but the rule that decides what its
    result means is the same one, so it lives here rather than in a second copy. A check
    that raises anything else blew up instead of asserting, which is a broken check and
    must be visible as such - `Outcome.broken_runner`, exactly as a crashed pytest case is.
    """
    try:
        check(*args)
    except AssertionError as failure:
        return Result(Outcome.killed, _first_line(failure))
    except dies_by as declared:                          # type: ignore[misc]
        return Result(Outcome.killed, _first_line(declared))
    except BaseException as wrong_class:                 # noqa: BLE001
        return Result(Outcome.broken_runner,
                      f"the check raised instead of asserting: {_first_line(wrong_class)}")
    return Result(Outcome.survived, "")


def _first_line(error: BaseException) -> str:
    # An `AssertionError()` with no message is still a failure to report, not a crash of
    # the runner reading it (D2 review H3).
    lines = str(error).strip().splitlines()
    return f"{type(error).__name__}: {(lines[0] if lines else '(no message)')[:160]}"


if __name__ == "__main__":
    sys.exit(main())
