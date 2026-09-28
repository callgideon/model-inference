#!/usr/bin/env python3
"""R32 for J2: one single-edit defect per decision `tests/j/submit` claims, through the shared
runner (`tests/contracts/mutants.py`). The copies run the fake world only (`-m "not pg"`); the
PostgreSQL param of the world case runs in the suite on the lane's key.

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/j/submit/test_mutants.py
    uv run --frozen python tests/j/submit/mutants.py --list

`tests/j/mutants.py` (J1's list) carries the one J2 mutant its module-coverage case needs.
"""
from __future__ import annotations

import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(API_DIR) not in sys.path:        # `python tests/j/submit/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401

SUITE = "tests/j/submit"
J = "judge/submit.py"

DRY = "test_j2__dry_run_mode_never_reserves_or_egresses"
GRANT = "test_j2__no_current_judging_grant_no_reservation_and_no_content_read"
WORLD = "test_j2__the_judge_is_checked_against_the_current_grant"
PAYER = "test_j2__only_the_providers_own_named_payer_pays"
EXPIRED = "test_j2__an_expired_grant_refuses_before_the_reservation"
REVOKED = "test_j2__a_grant_revoked_after_the_reservation_blocks_egress_and_releases_it"
UNPRICED = "test_j2__an_unpriced_judge_model_never_reserves"
WORST = "test_j2__the_reservation_is_the_worst_case_in_provider_usd_for_the_named_payer"
RACE = "test_j2__concurrent_runs_stay_under_the_payers_budget"
DOUBLE = "test_j2__a_double_submit_is_one_intent_and_one_provider_batch"
CONTENT = "test_j2__only_stored_content_of_the_grantor_leaves_by_durable_request_id"
EMPTY = "test_j2__a_run_with_no_content_left_is_cancelled_without_egress"
RETAINED = "test_j2__deleted_or_expired_content_never_reaches_the_judge"
REJECT = "test_j2__a_definite_rejection_releases_the_reservation"
UNKNOWN = "test_j2__an_unknown_submit_outcome_is_quarantined_and_never_retried"
ADOPT = "test_j2__reconciliation_adopts_the_providers_evidence_without_resubmitting"
NONE = "test_j2__reconciliation_with_no_provider_record_releases_the_hold"
SENDING = "test_j2__a_run_still_submitting_is_not_reconciled_under_its_sender"
ONCE = "test_j2__duplicate_and_late_results_settle_and_project_once"
SCORES = "test_j2__malformed_foreign_and_no_media_results_are_never_a_pass"
SKIPPED = "test_j2__a_result_for_a_sample_skipped_before_egress_is_stored_nowhere"
L_ONE = "test_j2_ledger__one_intent_one_batch_and_the_worst_case_held"
L_RACE = "test_j2_ledger__concurrent_runs_stay_under_the_payers_budget"
L_REVOKED = "test_j2_ledger__a_revocation_before_egress_releases_the_hold"
L_UNKNOWN = "test_j2_ledger__an_unknown_outcome_is_held_then_reconciled_from_evidence"
L_RESULTS = "test_j2_ledger__results_of_the_sent_samples_are_stored_and_settled_once"
L_GRANT = "test_j2_ledger__a_developer_without_a_judging_grant_reserves_nothing"
H_HOST = "test_j2_http__egress_is_refused_to_anything_but_the_local_fake"
H_TRIP = "test_j2_http__submit_and_collect_round_trip_through_the_fake"
H_REJECT = "test_j2_http__a_rejection_releases_the_hold"
H_LOST = "test_j2_http__a_lost_answer_is_ambiguous_one_request_and_reconciled"
H_5XX = "test_j2_http__a_5xx_is_ambiguous_even_when_it_carries_an_id"
H_DROP = "test_j2_http__a_request_the_provider_never_took_is_released_by_reconciliation"

MUTANTS: tuple[Mutant, ...] = (
    # --- before egress: mode, permission, price, budget --------------------------------------
    _m("live_mode_not_required", "only exactly `live` submits (R57)",
       J, "    if wiring.settings.judge_mode != JUDGE_MODE_LIVE:", "    if False:", DRY),
    _m("any_payer_pays", "PROVIDER_USD work is paid by this provider's own named payer",
       J, " or payer.group(2) != job.provider_org_id:", ":", PAYER),
    _m("any_ref_is_a_payer", "a grant (or any other) ref is not a payer",
       J, ' or payer.group(1) != "payer" or', " or", PAYER),
    _m("permission_not_checked_first", "no reservation or read without a current grant (L2)",
       J, "    consent = await _permitted(job, user_id, wiring)",
       '    consent = ConsentRef("unchecked", 1)', GRANT, WORLD, EXPIRED, L_GRANT),
    _m("any_purpose_judges", "provider_sharing is not external_judging",
       J, "PURPOSE = DataPurpose.external_judging", "PURPOSE = DataPurpose.provider_sharing",
       GRANT),
    _m("questions_alone_send_answers", "a judge needs both content categories granted",
       J, "CATEGORIES = (DataCategory.request_content, DataCategory.response_content)",
       "CATEGORIES = (DataCategory.request_content,)", GRANT, L_GRANT),
    _m("unpriced_model_reserves", "no approved rate, no reservation (typed refusal)",
       J, "    if rate is None:\n        raise", "    if False:\n        raise", UNPRICED,
       dies_by=("AttributeError",)),
    _m("reservation_is_one_sample", "the hold is the worst case over every sample",
       J, "worst_case(rate, job.ceilings, len(job.request_ids))",
       "worst_case(rate, job.ceilings, 1)", WORST, RACE, L_ONE, L_RACE),
    # --- the one submission ------------------------------------------------------------------
    _m("any_caller_egresses", "only the call that created the intent sends",
       J, "    if not mine:\n        return run", "    if False:\n        return run",
       DOUBLE, UNKNOWN, L_ONE),
    _m("content_read_as_the_provider", "content is read with the grantor's organization bound",
       J, "wiring.retention.read_content(job.grantor_org_id, request_id)",
       "wiring.retention.read_content(job.provider_org_id, request_id)", CONTENT),
    _m("content_read_from_the_raw_projection", "content is read through T3's Retention, so "
       "a deleted request or content past its bound never leaves (WR-OBS-3)",
       J, "        body = await wiring.retention.read_content(job.grantor_org_id, request_id)",
       '        body = await __import__("infrx.traces.ship.shipper", fromlist=["x"]).read_content('
       "\n            wiring.retention.traces, wiring.retention.objects, job.grantor_org_id,"
       " request_id)", RETAINED),
    _m("missing_content_sent", "a request with no stored content is skipped before egress",
       J, "        if body is not None:\n", "        if True:\n", CONTENT,
       dies_by=("AttributeError",)),
    _m("empty_batch_sent", "nothing left to judge is released, not sent",
       J, "    if not items:\n", "    if False:\n", EMPTY),
    _m("no_recheck_before_egress", "the permission is checked again immediately before egress",
       J, "        await _permitted(job, user_id, wiring)\n    except errors.DomainError:",
       "        pass\n    except errors.DomainError:", REVOKED, L_REVOKED),
    _m("revoked_run_keeps_its_hold", "a refusal before egress releases the hold",
       J, '        await ledger.release(run.run_id, "failed", "permission withdrawn before egress")',
       "        pass", REVOKED, L_REVOKED),
    _m("rejection_is_ambiguous", "a definite rejection releases, it does not quarantine",
       J, "    except SubmitRejected as exc:", "    except ZeroDivisionError as exc:", REJECT),
    _m("unknown_outcome_released", "an unknown outcome keeps its hold (ambiguous)",
       J, "        return await ledger.quarantine(run.run_id,",
       '        return await ledger.release(run.run_id, "failed",', UNKNOWN, L_UNKNOWN),
    # --- reconciliation and collection -------------------------------------------------------
    _m("evidence_ignored", "provider evidence of the batch is adopted",
       J, "    if external_id is None:\n        return await wiring.ledger.release",
       "    if True:\n        return await wiring.ledger.release", ADOPT, L_UNKNOWN),
    _m("no_record_keeps_the_hold", "no provider record releases the hold",
       J, '        return await wiring.ledger.release(run_id, "failed", "the provider has no such batch")',
       "        return run", NONE, L_UNKNOWN),
    _m("submitting_run_reconciled", "only an ambiguous run is reconciled",
       J, '    run = await _run(wiring.ledger, run_id, "ambiguous")',
       "    run = await wiring.ledger.run(run_id)", SENDING),
    _m("foreign_sample_projected", "a result for a sample the run never sent is stored nowhere",
       J, "ScoreLedger(run.run_id, rubric.version, run.sent_ids)",
       "ScoreLedger(run.run_id, rubric.version, [s for s, _ in polled.items])", SCORES),
    _m("skipped_sample_projected", "a result for a sample skipped before egress is stored nowhere",
       J, "ScoreLedger(run.run_id, rubric.version, run.sent_ids)",
       "ScoreLedger(run.run_id, rubric.version, run.sample_ids)", SKIPPED, L_RESULTS),
    _m("requested_ids_recorded_as_sent", "only the ids that leave are recorded as sent",
       J, 'record_sent(run.run_id, [item["sample_id"] for item in items])',
       "record_sent(run.run_id, run.sample_ids)", SKIPPED, L_RESULTS),
    _m("every_sample_has_media", "a sample sent without media is limited, never a pass",
       J, "media_available=sample_id in run.media_ids", "media_available=True", SCORES),
    _m("no_sample_has_media", "a sample sent with media is not limited",
       J, "media_available=sample_id in run.media_ids", "media_available=False", SCORES),
    _m("settled_before_done", "a run settles only when the provider is done",
       J, "    if not polled.done:\n        return run", "    if False:\n        return run", ONCE,
       dies_by=("TypeError",)),
    _m("completed_run_collected_again", "collecting a completed run is a no-op",
       J, '    if run is not None and run.state == "completed":\n        return run',
       '    if False:\n        return run', ONCE, L_RESULTS),
    # --- egress --------------------------------------------------------------------------------
    _m("any_host_egresses", "egress only to the local fake until P-10",
       J, 'parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS',
       'parts.scheme != "http"', H_HOST),
    _m("tls_to_loopback_accepted", "the local fake is plain http on loopback, nothing else",
       J, 'parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS',
       "parts.hostname not in LOCAL_HOSTS", H_HOST),
    _m("proxy_from_the_environment", "an environment proxy never carries the payload",
       J, "timeout=self.timeout_s,\n                                 trust_env=False)",
       "timeout=self.timeout_s)", H_TRIP),
    _m("http_rejection_is_ambiguous", "a 4xx is a definite rejection",
       J, "        if 400 <= response.status_code < 500:", "        if False:", H_REJECT),
    _m("a_5xx_with_an_id_is_submitted", "a 5xx is ambiguous even when it carries an id",
       J, "        if response.status_code != 200:\n            raise errors.AmbiguousSubmission",
       "        if False:\n            raise errors.AmbiguousSubmission", H_5XX),
    _m("lookup_404_raises", "no batch for the key is evidence, not an error",
       J, "        if response.status_code == 404:\n            return None",
       "        if False:\n            return None", H_DROP, dies_by=("HTTPStatusError",)),
    _m("lookup_by_the_wrong_key", "reconciliation looks up the run's own submit key",
       J, 'params={"submit_key": submit_key}', 'params={"submit_key": submit_key + "-x"}',
       H_LOST),
)

RUNNER = Runner(name="j2", targets=(SUITE,),
                extra_args=(f"--ignore={SUITE}/test_mutants.py", "-m", "not pg"))


def case_names() -> set[str]:
    import re
    pattern = re.compile(r"^def (test_\w+)", re.MULTILINE)
    return {name for path in (API_DIR / SUITE).glob("test_*.py") if path.name != "test_mutants.py"
            for name in pattern.findall(path.read_text())}


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run J2's mutation list"))
