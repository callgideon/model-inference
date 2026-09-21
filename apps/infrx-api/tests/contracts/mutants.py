#!/usr/bin/env python3
"""r1 R32: the mutation list and its runner.

A conformance case is only worth the invariant it can *kill*. Each entry below is a
single edit to the fakes that breaks one named invariant, together with the cases
that must notice. The runner applies one mutant at a time to a **copy** of the
package in a temporary directory, runs the named cases there, and fails if the
mutant survives. Nothing is ever written inside the worktree.

    uv run --frozen pytest -q tests/contracts/test_mutants.py     # the whole list
    make api-mutants                                             # same, from the root
    uv run --frozen python tests/contracts/mutants.py --list      # names only
    uv run --frozen python tests/contracts/mutants.py judge_settle_negative

Adding a conformance case means adding a mutant for the invariant it claims. A
mutant whose anchor no longer exists fails loudly rather than being skipped: the
list is part of the suite, not documentation of it.
"""
from __future__ import annotations

import argparse
import enum
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
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

    @property
    def path(self) -> pathlib.Path:
        return pathlib.Path(PACKAGE) / self.file


def _m(name, invariant, file, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


S = "contracts/fakes/state.py"          # JobStore + StreamStore
J = "contracts/fakes/judge.py"
M = "contracts/fakes/media.py"
T = "contracts/fakes/traces.py"
F = "contracts/fakes/feedback.py"
Q = "contracts/fakes/scheduling.py"
R = "contracts/records.py"
W = "contracts/wire.py"                 # public bodies: projections and input bounds
MONEY = "contracts/money.py"

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
       S, "            price = self._price(request, now)\n            self.journal.reserve(request.request_id)",
       "            self.journal.reserve(request.request_id)\n            price = self._price(request, now)",
       "dur_admit__a_refused_admission_reserves_nothing"),
    _m("admit_accepts_a_negative_hold", "a negative maximum hold is refused (R11)",
       S, 'hold = money_input(hold, "the maximum hold")', "hold = money.parse(hold)",
       "dur_cap__a_negative_maximum_hold_is_refused",
       "dur_admit__a_refused_admission_reserves_nothing"),
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
       "dur_fence__a_stale_worker_cannot_append"),
    _m("heartbeat_stores_the_callers_lease", "a lease is a fencing token, not a record (R29)",
       S, "            job.lease = job.lease.model_copy(update={",
       "            job.lease = lease.model_copy(update={",
       "dur_fence__a_lease_is_a_fencing_token_not_a_record"),
    _m("deadlines_do_not_bind_mutations", "deadlines bind every fenced mutation (R29)",
       S, "        self._enforce_deadlines(job)\n        return job", "        return job",
       "dur_fence__a_lease_is_a_fencing_token_not_a_record"),
    _m("preparation_deadline_unenforced", "a dead preparation worker frees its job (R29)",
       S, "            self._enforce_deadlines(job)\n            for ref in media:",
       "            for ref in media:",
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
       "        if False:", "dur_output__a_late_preparation_worker_finds_a_terminal_job"),
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
       "        if False:", "media_sec__a_refused_upload_stays_refused"),
    _m("mime_string_split_into_characters", "a single type must still be a list",
       M, "        if isinstance(raw_mimes, (str, bytes)):", "        if False:",
       "media_sec__a_refused_upload_stays_refused"),
    _m("cross_tenant_media_resolved", "media is tenant scoped",
       M, "        media = self.objects.get((org_id, ref))",
       "        media = next((value for (_o, handle), value in self.objects.items()\n"
       "                      if handle == ref), None)",
       "media_sec__another_org_cannot_resolve_or_finalize"),
    _m("staging_overwrites_content", "staged content is immutable",
       M, "                if existing.digest != ref.digest:", "                if False:",
       "media_sec__staging_never_replaces_an_existing_object"),
    _m("staging_commits_as_it_goes", "staging is all or nothing",
       M, "        self.objects.update(pending)",
       "        pass  # objects were written as the loop went (they are not, now)",
       "media_sec__a_partial_request_stages_nothing"),
    _m("staging_resolves_after_writing", "an unresolvable upload leaves nothing staged",
       M, "                owned = await self.resolve_owned(org_id, ref.handle)",
       "                self.objects.update(pending)\n"
       "                owned = await self.resolve_owned(org_id, ref.handle)",
       "media_sec__a_partial_request_stages_nothing"),
    _m("staging_validates_nothing_up_front", "staging is all or nothing",
       M, "        for ref in request.media:\n            if ref.org_id != org_id:",
       "        for ref in ():\n            if ref.org_id != org_id:",
       "media_sec__a_partial_request_stages_nothing",
       "media_sec__a_foreign_media_reference_is_not_staged"),
    _m("staging_ignores_the_org", "a body cannot name another tenant's object",
       M, '                raise errors.NotFound("media reference does not belong to this org")',
       "                pass", "media_sec__a_foreign_media_reference_is_not_staged"),
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
       "        if False:", "trace_bounds__a_capture_belongs_to_its_own_request"),
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
       T, "                       if self.metadata_bytes + envelope.metadata_bytes\n"
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
    _m("preparation_retries_unbounded", "preparation retries are bounded (R46)",
       S, "            if job.preparation_attempts > self.limits.max_prepublication_retries:",
       "            if False:",
       "dur_output__a_lost_preparation_worker_is_reaped_within_bounds"),
    _m("lost_preparation_is_never_reaped", "recover reaps a lost preparation worker (R46)",
       S, "        if (job.state is JobState.preparing and job.preparation_lease is not None\n"
          "                and now >= job.preparation_lease.expires_at):",
       "        if False:",
       "dur_output__a_lost_preparation_worker_is_reaped_within_bounds"),
    _m("load_work_is_unfenced", "load_work hands out nothing to a fenced lease (R46)",
       S, "            job = (self._fence_preparation(lease) if lease.kind is LeaseKind.preparation\n"
          "                   else self._fence(lease))",
       "            job = self.jobs[lease.job_id]",
       "dur_fence__load_work_is_fenced_and_hands_out_nothing_otherwise"),
    _m("load_work_hides_the_prepared_refs", "load_work carries what preparation produced (R46)",
       S, "                        prepared_refs=job.prepared,", "                        prepared_refs=(),",
       "dur_fence__load_work_is_fenced_and_hands_out_nothing_otherwise"),
    _m("heartbeat_renews_a_preparation_lease", "nothing renews a preparation lease (R46)",
       S, '            raise errors.InvalidRequest("only an inference lease is renewed")', "            pass",
       "dur_fence__preparation_is_claimed_and_fenced_like_execution"),
    # --- feedback -------------------------------------------------------------
    _m("feedback_operator_role_from_session", "accept always records customer (R31)",
       F, "            author_role=AuthorRole.customer,",
       "            author_role=AuthorRole.operator if auth.is_operator else AuthorRole.customer,",
       "feedback_ack__a_client_cannot_forge_provenance"),
    _m("calibration_open_to_customers", "calibration labels are operator only (R31)",
       F, '            raise errors.Forbidden("labelling a calibration set requires a platform operator")',
       "            pass", "feedback_ack__an_operator_may_label_a_calibration_set"),
    _m("calibration_not_idempotent", "a calibration label is idempotent",
       F, "        existing = self.idem.get(idem.scope)\n        if existing is not None:\n"
          "            payload_hash, feedback_id = existing\n"
          "            if payload_hash != idem.payload_hash:\n"
          "                raise errors.IdempotencyConflict(\"same label key, different payload\")\n"
          "            return self.items[feedback_id]",
       "        existing = None", "feedback_ack__an_operator_may_label_a_calibration_set"),
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
       F, "        return FeedbackList.for_viewer(rows, operator=auth.is_operator).items",
       "        return rows",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("customer_list_shows_the_operator", "an operator principal is projected to platform (R41)",
       W, "            else item.model_copy(update={\"author_principal\": PLATFORM_ACTOR})",
       "            else item",
       "feedback_ack__calibration_labels_are_operator_data"),
    _m("wire_list_publishes_labels", "the wire list applies the same projection (R35/R41)",
       W, "        if operator:\n            return cls(items=tuple(items), next_cursor=next_cursor)",
       "        if True:\n            return cls(items=tuple(items), next_cursor=next_cursor)",
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
       F, "        existing = self.idem.get(idem.scope)", "        existing = None",
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
       "        elif False:", "trace_bounds__a_capture_belongs_to_its_own_request"),
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
    _m("duplicate_handle_last_wins", "one handle carries one object per request",
       M, "            if clash is not None and clash.digest != stored.digest:", "            if False:",
       "media_sec__a_partial_request_stages_nothing"),
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
    _m("minimal_capture_trusts_the_envelope_mode", "the capture's mode decides, not the envelope",
       T, "            if envelope.mode is not TraceMode.minimal or envelope.carries_content:",
       "            if False:",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope"),
    # both halves of that guard, separately: the mode half alone let a raw-assembled
    # minimal envelope carry content past a customer's metadata-only consent (r7 F1, y3c)
    _m("minimal_capture_keeps_only_the_mode_half", "a minimal capture never stores content",
       T, "            if envelope.mode is not TraceMode.minimal or envelope.carries_content:",
       "            if envelope.mode is not TraceMode.minimal:",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope"),
    _m("minimal_capture_keeps_only_the_content_half", "a minimal capture checks the mode too",
       T, "            if envelope.mode is not TraceMode.minimal or envelope.carries_content:",
       "            if envelope.carries_content:",
       "trace_bounds__a_no_op_capture_trusts_itself_not_the_envelope"),
    _m("live_capture_trusts_the_envelope_mode", "the capture decides on the live path too (n1)",
       T, "        if envelope.mode is not self.mode:", "        if False:",
       "trace_bounds__a_live_capture_also_decides_its_own_mode"),
    _m("unrecordable_capture_counts_no_loss", "a full capture that never captured counts one loss (n2)",
       T, "        if self.no_op and self.mode is not TraceMode.full:",
       "        if self.no_op:",
       "trace_bounds__an_open_capture_past_its_deadline_is_reaped"),
    _m("drop_reason_falls_back_on_truthiness", "`none` is never a drop reason (n3)",
       T, "            reason = (TraceLossReason.abandoned if self.lost_reason is TraceLossReason.none\n"
          "                      else self.lost_reason)",
       "            reason = self.lost_reason or TraceLossReason.abandoned",
       "trace_bounds__no_loss_is_ever_counted_under_none"),
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
       "feedback_ack__acceptance_is_durable_and_provenance_is_server_set"),
    _m("media_key_ignores_the_tenant", "two orgs never share an object",
       M, '        return f"media/{org_id}/{profile_version}/{digest.split(\':\')[1][:16]}/{part}"',
       '        return f"media/{profile_version}/{digest.split(\':\')[1][:16]}/{part}"',
       "media_parity__staging_is_content_addressed_and_tenant_namespaced"),
    _m("prepared_profile_ignored", "prepared refs carry their profile version",
       M, "ref.model_copy(update={\"profile_version\": profile,",
       "ref.model_copy(update={\"profile_version\": ref.profile_version,",
       "media_parity__staging_is_content_addressed_and_tenant_namespaced"),
    _m("upload_handle_is_guessable", "an upload handle is opaque and server issued",
       M, '                "destination_ref": f"infrx-upload:{org_id}:{handle}",',
       '                "destination_ref": f"https://example.invalid/{handle}?Signature=abc",',
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
)


class Outcome(enum.StrEnum):
    """What one mutant run proved. Only `killed` counts."""

    killed = "killed"                  # pytest failed, and only named cases failed
    survived = "survived"              # the suite passed with the defect in place
    broken_runner = "broken_runner"    # syntax, import, collection or usage error
    misdeclared = "misdeclared"        # anchor missing, no case, or a case that never ran


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    detail: str

    @property
    def killed(self) -> bool:
        return self.outcome is Outcome.killed

    @property
    def ok(self) -> bool:
        """A run the suite accepts: only a kill does."""
        return self.killed


# pytest exit codes: 0 all passed, 1 tests failed, 2 interrupted, 3 internal error,
# 4 usage error, 5 no tests collected. Only 1 can mean "the case noticed".
PYTEST_TESTS_FAILED = 1
PYTEST_ALL_PASSED = 0
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) ([^\s:]+(?:::[^\s]+)?)")


def _failing_ids(stdout: str) -> tuple[list[str], list[str]]:
    """(failed test ids, errored test ids) from a `-q -rA`-style summary."""
    failed, errored = [], []
    for line in stdout.splitlines():
        match = _FAILED_LINE.match(line.strip())
        if match:
            (errored if line.strip().startswith("ERROR") else failed).append(match.group(1))
    return failed, errored


def run_mutant(mutant: Mutant) -> Result:
    """Apply one mutant to a throwaway copy of the package and run its cases.

    A kill requires all three of:

    * pytest exited 1 (tests failed) - not 2-5, which mean the *runner* broke, and not
      0, which means the defect went unnoticed;
    * at least one test failed;
    * every failing test id names one of the mutant's own cases.

    That last condition is what stops a syntax error, an import-time `NameError` or a
    collection error from counting as a kill: those fail tests the mutant never named
    (or fail before any test exists), and they are reported as `broken_runner`, which
    fails the run just as a survivor does. The worktree is never written to.
    """
    if not mutant.cases:
        return Result(Outcome.misdeclared, "declares no case")
    with tempfile.TemporaryDirectory(prefix=f"mutant-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        shutil.copytree(API_DIR / PACKAGE, root / PACKAGE,
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(API_DIR / "tests", root / "tests",
                        ignore=shutil.ignore_patterns("__pycache__"))
        target = root / mutant.path
        source = target.read_text()
        if mutant.old not in source:
            return Result(Outcome.misdeclared,
                          f"anchor not found in {mutant.file}: {mutant.old[:60]!r}")
        target.write_text(source.replace(mutant.old, mutant.new, 1))
        selection = " or ".join(mutant.cases)
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
             "-rf", "--tb=no", "tests/contracts/test_conformance.py", "-k", selection],
            cwd=root, capture_output=True, text=True,
            env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"})
        stdout = done.stdout or ""
        lines = (stdout or done.stderr).strip().splitlines()
        summary = lines[-1] if lines else "no output"
        if done.returncode not in (PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED):
            # 2-5: interrupted, internal error, usage error, nothing collected. The
            # mutant may well be lethal, but this run did not prove it.
            return Result(Outcome.broken_runner,
                          f"pytest exit {done.returncode}: {summary}")
        ran = re.search(r"(\d+) (?:passed|failed|skipped)", summary)
        if not ran or "no tests ran" in summary:
            return Result(Outcome.misdeclared, f"no case matched {selection!r}: {summary}")
        failed, errored = _failing_ids(stdout)
        if errored:
            return Result(Outcome.broken_runner, f"errors outside the named cases: {errored[:3]}")
        if done.returncode == PYTEST_ALL_PASSED or not failed:
            return Result(Outcome.survived, summary)
        stray = [test_id for test_id in failed
                 if not any(f"-{case}]" in test_id or test_id.endswith(case)
                            for case in mutant.cases)]
        if stray:
            # Something the mutant did not name broke: a syntax error, an import-time
            # failure, or a defect with wider reach than the declaration claims.
            return Result(Outcome.broken_runner,
                          f"failures outside the named cases: {stray[:3]}")
        if "skipped" in summary and not failed:
            return Result(Outcome.misdeclared, f"its cases were skipped: {summary}")
        return Result(Outcome.killed, summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="run the contracts-v1 mutation list")
    parser.add_argument("names", nargs="*", help="mutants to run (default: all)")
    parser.add_argument("--list", action="store_true", help="print the list and exit")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:42s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over "
              f"{len({case for m in MUTANTS for case in m.cases})} named cases")
        return 0
    chosen = [m for m in MUTANTS if not args.names or m.name in args.names]
    bad: dict[str, list[str]] = {}
    for mutant in chosen:
        result = run_mutant(mutant)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}")
        if not result.killed:
            bad.setdefault(result.outcome.value, []).append(mutant.name)
    failures = sum(len(names) for names in bad.values())
    print(f"\n{len(chosen) - failures}/{len(chosen)} killed"
          + "".join(f"; {outcome}: {names}" for outcome, names in sorted(bad.items())))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
