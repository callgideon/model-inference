#!/usr/bin/env python3
"""R32/R40/R83 for B1: one single-edit defect per decision `test_runner.py` claims, through
the shared runner with `require_every_case`. The PostgreSQL drills (`test_runner_pg.py`) are
outside the runner (N2/T2I's pattern).

    uv run --frozen pytest -q tests/b/runner/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/b/runner/test_mutants.py   # all
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Result, Runner  # noqa: E402

SUITE = "tests/b/runner/test_runner.py"
P = "evaluation/runner/__init__.py"

FREEZE = "test_b1_freeze_pins_the_run_and_the_same_record_is_the_same_cases"
REFUSE = "test_b1_freeze_refuses_an_unbound_evaluator_unbounded_spend_and_another_provider"
SCORES = "test_b1_a_run_scores_every_case_once_and_records_each_attempts_cost"
RETRY = "test_b1_a_transient_failure_is_retried_under_the_same_key_and_bounded"
KILLED = "test_b1_a_worker_killed_mid_attempt_is_a_new_bounded_attempt_after_recovery"
CANCEL = "test_b1_a_durable_cancel_stops_spending_and_drops_the_in_flight_result"
DUP = "test_b1_duplicate_delivery_scores_each_case_once"
WALLET = "test_b1_an_exhausted_dev_wallet_stops_the_run_with_its_cases_unresolved"
BUDGET = "test_b1_the_credit_budget_bounds_spending"
REVOKED = "test_b1_revoked_data_fails_its_case_without_dispatch"
GIVEBACK = "test_b1_a_402_gives_its_attempt_back_so_billing_never_exhausts_a_case"
RESUME = "test_b1_a_created_run_resumes_after_a_revocation_and_ends_revoked"
HARNESS = "test_b1_harness_outcomes_are_results_and_bounds_fail_the_attempt"
LARGE = "test_b1_an_oversized_output_is_scored_and_recorded_without_its_body"
NOREF = "test_b1_a_case_without_its_reference_is_recorded_unscored"
CONC = "test_b1_cases_run_with_bounded_concurrency"
HTTP = "test_b1_the_http_dev_endpoint_speaks_openai_with_the_key_and_prices_by_the_card"
MEMBER = "test_b1_only_a_current_member_allowed_to_run_evaluations_schedules"
UNEXPECTED = "test_b1_an_unexpected_error_fails_its_case_visibly_and_the_delivery_goes_on"
VIDEO = "test_b1_a_finite_video_case_is_sent_as_its_presigned_clip_with_its_span"
CLIPS = "test_b1_only_the_runs_providers_clip_within_the_cap_is_signed"
PRESIGN = "test_b1_the_s3_store_presigns_a_bounded_sigv4_get_of_one_object"
S3 = "media/s3.py"


def m(name, invariant, old, new, *cases, dies_by=(), occurrences=1, file=P):
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- B1.a freeze (EVAL-REPRO)
    m("b1_any_record_is_a_run", "only an evaluation run is frozen",
      "    if not isinstance(run, lab.EvalRun):\n", "    if False:\n", REFUSE,
      dies_by=("AttributeError",)),
    m("b1_evaluator_unbound", "the evaluator spec is the one the run's ref names",
      "    if evaluator_ref(evaluator, provider_org_id=run.provider_org_id,",
      "    if False and evaluator_ref(evaluator, provider_org_id=run.provider_org_id,", REFUSE),
    m("b1_evaluator_digest_blind", "the evaluator ref digest is over the whole spec",
      "hashlib.sha256(lab.canonical(spec)).hexdigest()",
      'hashlib.sha256(lab.canonical(spec["metric"])).hexdigest()', REFUSE),
    m("b1_metric_unchecked", "an unknown metric is refused",
      "        EvaluatorSpec.model_validate(evaluator)\n",
      "        EvaluatorSpec.model_construct()\n", REFUSE),
    m("b1_any_budget_unit", "only a CREDIT budget bounds provider_dev spend",
      'limits = [b.limit for b in run.budgets if b.limit.unit == "CREDIT"]',
      "limits = [b.limit for b in run.budgets]", REFUSE),
    m("b1_budget_optional", "a run without a CREDIT budget is refused",
      "    if not limits:\n", "    if False:\n", REFUSE, dies_by=("IndexError",)),
    m("b1_cases_all_samples", "the cases are the first max_cases sample ids (R168)",
      "[:run.max_cases])", ")", FREEZE),
    m("b1_holdout_is_every_case", "the holdout is the manifest's",
      "if c in manifest.splits.holdout)", ")", FREEZE),
    m("b1_split_digest_blind", "the split digest is the manifest's splits",
      "split_digest(manifest.splits.model_dump())", "split_digest({})", FREEZE),
    m("b1_harness_defaults_added", "the frozen harness is the published record's bytes",
      "exclude_unset=True", "exclude_unset=False", FREEZE),
    m("b1_resume_regated", "a created run resumes without the scheduling gate (0-B-R2)",
      "    run = await store.resolve(run_ref, provider_org_id=provider_org_id)\n",
      "    run = await store.resolve(run_ref, provider_org_id=provider_org_id)\n"
      "    await store.create_run(run_ref, provider_org_id=provider_org_id)\n", RESUME,
      dies_by=("Forbidden",)),
    m("b1_resume_evaluator_unchecked", "a resumed run still refuses another evaluator spec",
      "_pinned(store, run, run_ref, evaluator, _checked(run, evaluator))",
      "_pinned(store, run, run_ref, evaluator, run.budgets[0].limit.amount)", RESUME),
    # --- B1.b the loop (EVAL-DURABLE)
    m("b1_budget_unchecked", "the run's recorded CREDIT stops leasing at its limit",
      "                    >= self._frozen.limit:", "                    >= self._frozen.limit and False:",
      BUDGET),
    m("b1_budget_off_by_one", "reaching the limit exactly stops the run",
      "                    >= self._frozen.limit:", "                    > self._frozen.limit:",
      BUDGET),
    m("b1_stop_not_sticky", "a stop ends every worker's loop",
      "        while self._stop is None:\n", "        while True:\n", GIVEBACK),
    m("b1_terminal_run_raises", "a delivery of a finished run is a no-op",
      "            except errors.AlreadyTerminal:\n                return",
      "            except errors.StateConflict:\n                return", DUP,
      dies_by=("AlreadyTerminal",)),
    m("b1_cancel_unreported", "a cancelled run reports the stop",
      '("cancelled" if status["state"] == "cancelled" else None)', "None", CANCEL),
    m("b1_single_worker", "`concurrency` workers lease in parallel",
      "for i in range(self._limits.concurrency)", "for i in range(1)", CONC,
      dies_by=("BrokenBarrierError",)),
    m("b1_shared_worker_id", "each worker leases under its own id",
      'self._work(f"{self._worker}/{i}", loop)', "self._work(self._worker, loop)", CONC,
      dies_by=("BrokenBarrierError",)),
    # the kill mode is D7's own refusal (0034: a run under an unregistered evaluator is
    # `not_found`), which the fake store raises as D7 does
    m("b1_evaluator_unregistered", "freeze registers the run's evaluator in D7 first (R167)",
      "    await store.put_evaluator(evaluator,", "    None and store.put_evaluator(evaluator,",
      FREEZE, dies_by=("NotFound",)),
    m("b1_attempts_unbounded", "past max_attempts a case fails without a call",
      'if lease["attempt"] > self._limits.max_attempts:', "if False:", KILLED),
    m("b1_attempts_off_by_one", "max_attempts attempts are allowed",
      'if lease["attempt"] > self._limits.max_attempts:',
      'if lease["attempt"] >= self._limits.max_attempts:', KILLED),
    m("b1_rights_unchecked", "each attempt re-reads the access gate",
      "        if case_id not in await self._store.accessible_samples(",
      "        if False and case_id not in await self._store.accessible_samples(", REVOKED),
    m("b1_rights_wrong_purpose", "the access gate is provider_sharing (R160)",
      '                purpose="provider_sharing"):\n            return await self._finish(',
      '                purpose="training"):\n            return await self._finish(', REVOKED),
    m("b1_wallet_not_stopping", "an exhausted dev wallet stops the run",
      '            self._stop = "wallet_exhausted"\n', "            pass\n", GIVEBACK),
    m("b1_wallet_is_a_failure", "a 402 leaves the case unresolved, not failed",
      "        except errors.InsufficientCredit:\n", "        except errors.InvalidApiKey:\n",
      WALLET),
    m("b1_402_attempt_kept", "a 402 gives its attempt back, never spending max_attempts",
      "            return await self._release(lease, bill)",
      "            return self._abandon(case_id, bill)", WALLET, GIVEBACK),
    m("b1_402_release_refusal_raises", "a release refused by the fence is abandoned, not raised",
      "        except errors.StaleLease:\n            pass\n",
      "        except errors.IdempotencyConflict:\n            pass\n", CANCEL,
      dies_by=("StaleLease",)),
    m("b1_stale_replay_unreported", "an attempt refused by the fence is abandoned",
      "        except errors.StaleLease:\n            return self._abandon(case_id, bill)",
      "        except errors.StaleLease:\n            return None", CANCEL),
    m("b1_bound_unnamed", "a replay bound fails the attempt naming the bound",
      'reason=f"bound:{bound.bound}"', 'reason="bound"', HARNESS),
    m("b1_bound_cost_dropped", "a failed attempt keeps its cost",
      'reason=f"bound:{bound.bound}", bill=bill)', 'reason=f"bound:{bound.bound}")', HARNESS),
    m("b1_refusal_unnamed", "a failed attempt names its refusal",
      "reason=refused.code", 'reason="failed"', RETRY),
    m("b1_no_fence_before_a_call", "every paid call is fenced first",
      "        await self._store.heartbeat(lease, lease_s=self._limits.lease_s)   # the fence\n",
      "        pass\n", CANCEL),
    m("b1_key_not_the_attempt", "a call is keyed by its attempt",
      "key = f\"{lease['idempotency_key']}:{bill.calls}\"",
      "key = f\"{lease['case_id']}:{bill.calls}\"", SCORES, KILLED),
    m("b1_key_not_per_call", "each call of an attempt has its own key",
      "key = f\"{lease['idempotency_key']}:{bill.calls}\"",
      "key = f\"{lease['idempotency_key']}:0\"", HARNESS),
    m("b1_seed_dropped", "the run's seed is sent",
      "seed=self._frozen.run.seed,", "seed=0,", SCORES),
    m("b1_retries_unbounded", "at most dispatch_retries retries",
      "for left in range(self._limits.dispatch_retries, -1, -1):",
      "for left in range(self._limits.dispatch_retries + 1, -1, -1):", RETRY),
    m("b1_no_retry", "a retryable refusal is retried",
      "                if not left:\n", "                if True:\n", RETRY),
    m("b1_final_refusal_retried", "a final refusal is not retried",
      "RETRYABLE = (errors.RateLimitError, errors.ServerError)",
      "RETRYABLE = (errors.RateLimitError, errors.ServerError, errors.InvalidRequest)", RETRY),
    m("b1_5xx_not_retried", "5xx is retryable",
      "RETRYABLE = (errors.RateLimitError, errors.ServerError)",
      "RETRYABLE = (errors.RateLimitError,)", RETRY),
    m("b1_prompt_tokens_last", "usage sums every call",
      "bill.prompt_tokens += done.prompt_tokens", "bill.prompt_tokens = done.prompt_tokens",
      HARNESS),
    m("b1_completion_tokens_last", "usage sums every call",
      "bill.completion_tokens += done.completion_tokens",
      "bill.completion_tokens = done.completion_tokens", HARNESS),
    m("b1_charge_last", "an attempt's cost sums every call",
      "bill.charged += done.charged", "bill.charged = done.charged", HARNESS),
    m("b1_calls_unreported", "usage counts the calls",
      '"usage": {"calls": bill.calls,', '"usage": {"calls": 1,', HARNESS),
    m("b1_abandoned_charge_hidden", "an abandoned attempt's charge is reported",
      "            self._unrecorded += bill.charged\n", "            pass\n", CANCEL),
    m("b1_stale_finish_unreported", "a finish refused by the fence is abandoned",
      '            return self._abandon(lease["case_id"], bill)', "            return None",
      CANCEL),
    m("b1_failures_unreported", "a failed case's reason is reported",
      "        if reason:\n", "        if False:\n", RETRY, REVOKED),
    # --- B1.c results (EVAL-DURABLE, EVAL-REPRO)
    m("b1_score_not_exact", "the score is exact match against the reference",
      "float(outcome.output == reference)", "float(outcome.output is not None)", SCORES),
    m("b1_blocked_scored", "a non-comparable outcome has no score",
      "if outcome.comparable and reference is not _MISSING else None",
      "if reference is not _MISSING else None", HARNESS),
    m("b1_missing_reference_scored", "a case without its reference has no score",
      "if outcome.comparable and reference is not _MISSING else None",
      "if outcome.comparable else None", NOREF),
    m("b1_missing_reference_silent", "a missing reference is a visible reason",
      '            reasons.append("missing_reference")\n', "            pass\n", NOREF),
    m("b1_split_unrecorded", "each result names its case's split",
      "if case_id in getattr(f.manifest.splits, name))", "if True)", SCORES),
    m("b1_latency_dropped", "each result carries its latency",
      '"latency_ms": int((time.monotonic() - started) * 1000),', '"latency_ms": None,', SCORES),
    m("b1_result_under_another_evaluator", "the result is under the run's evaluator",
      '"evaluator_ref": f.run.evaluator_ref,', '"evaluator_ref": f.run.serving_ref,', SCORES),
    m("b1_oversize_sent", "an oversized output is dropped before the finish",
      "        if len(text) > MAX_BODY:\n", "        if False:\n", LARGE,
      dies_by=("InvalidRequest",)),
    m("b1_oversize_unreported", "a dropped output says why",
      'reasons=[*reasons, "output_too_large"]', "reasons=reasons", LARGE),
    # --- WR-B-4: who schedules (the L2 port)
    m("b1_schedule_unchecked", "freeze asks the L2 port first",
      "    await may_schedule(access, user_id=user_id, provider_org_id=provider_org_id)\n",
      "", MEMBER),
    m("b1_viewer_schedules", "the role must carry run_evaluation",
      "if not membership.permits(ProviderCapability.run_evaluation, now, provider_org_id):",
      "if not membership.permits(ProviderCapability.read_aggregate_health, now,"
      " provider_org_id):", MEMBER),
    m("b1_revoked_member_confirmed", "a revoked membership is no workspace (404, not 403)",
      "    if membership is None or not membership.permits(\n"
      "            ProviderCapability.read_aggregate_health, now, provider_org_id):",
      "    if membership is None:", MEMBER, dies_by=("Forbidden",)),
    # --- B-R7/B-R8
    m("b1_unexpected_error_masked", "an unexpected error names itself",
      'reason=f"error:{type(broken).__name__}"', 'reason="attempts_exhausted"', UNEXPECTED),
    m("b1_missing_content_unnamed", "a missing content object is named",
      "            if raw is None:\n", "            if False:\n", UNEXPECTED),
    m("b1_unrecorded_not_budgeted", "an unrecorded charge counts toward the budget (B-R8)",
      'Credit(status["costs"].get("CREDIT", "0")) + self._unrecorded',
      'Credit(status["costs"].get("CREDIT", "0"))', BUDGET),
    # --- the HTTP dev endpoint (L3 stand-in)
    m("b1_http_media_as_text", "a clip is sent, not dropped for the prompt alone",
      '"content": content if media else prompt}]', '"content": prompt}]', HTTP),
    m("b1_http_video_part_shaped", "a clip is a `video_url` part of exactly {url} (R58)",
      '{"type": "video_url", "video_url": {"url": url}}', '{"type": "video_url", "url": url}',
      HTTP),
    m("b1_http_seed_dropped", "the seed is sent", '"seed": seed,', '"seed": 0,', HTTP),
    m("b1_http_tool_results_sent", "a tool result is refused, not sent as a refused role (B-R3)",
      "        if tool_results:        # B-R3", "        if False:        # B-R3", HTTP),
    m("b1_http_key_dropped", "the idempotency key is sent",
      '"Idempotency-Key": idempotency_key}', '"Idempotency-Key": "k"}', HTTP),
    m("b1_http_transport_final", "a lost connection is retryable",
      "        except httpx.TransportError:\n", "        except httpx.ConnectError:\n", HTTP,
      dies_by=("RemoteProtocolError",)),
    m("b1_http_402_final", "402 is an exhausted wallet",
      "if answer.status_code == 402:", "if answer.status_code == 403:", HTTP),
    m("b1_http_429_final", "429 is retryable",
      "if answer.status_code == 429 or answer.status_code >= 500:",
      "if answer.status_code >= 500:", HTTP),
    m("b1_http_5xx_final", "5xx is retryable",
      "if answer.status_code == 429 or answer.status_code >= 500:",
      "if answer.status_code == 429:", HTTP),
    m("b1_http_refusal_accepted", "another refusal is final",
      "        if answer.status_code != 200:\n", "        if False:\n", HTTP,
      dies_by=("KeyError",)),
    m("b1_http_arguments_raw", "tool arguments are decoded",
      'json.loads(c["function"]["arguments"])', 'c["function"]["arguments"]', HTTP),
    m("b1_http_debit_swapped", "the charge is the card's debit of (prompt, completion)",
      'charged=self._rate_card.debit(usage["prompt_tokens"],\n'
      '                                                        usage["completion_tokens"])',
      'charged=self._rate_card.debit(usage["completion_tokens"],\n'
      '                                                        usage["prompt_tokens"])', HTTP),
)


# --- WR-E6L-J11: a finite-video case to the dev endpoint
MEDIA = (
    m("b1_media_unsigned", "the endpoint gets the clip's URL, never the object key",
      "        media = [await clip_url(", "        media = media or [await clip_url(", VIDEO),
    m("b1_media_another_provider", "a clip is signed for the run's own provider",
      "self._frozen.run.provider_org_id, ref,", '"", ref,', VIDEO),
    m("b1_media_span_dropped", "the clip's span is the case's `span_ms`",
      '_at(case, "sample.span_ms")', '_at(case, "sample.duration_ms")', VIDEO),
    m("b1_clip_any_provider", "only lab/<the run's provider>/media/ is signed (R227)",
      '    if not re.fullmatch(re.escape(media_key(provider_org_id, "")) + "[0-9a-f]{64}", '
      "media_ref):", "    if False:", CLIPS),
    m("b1_clip_any_path", "a media ref is a digest, never a path",
      '+ "[0-9a-f]{64}", media_ref)', '+ ".*", media_ref)', CLIPS),
    m("b1_clip_over_cap", "a clip is at most MAX_VIDEO_MS (82 s)",
      "span[1] <= span[0] + lab.MAX_VIDEO_MS", "span[1] <= span[0] + lab.MAX_VIDEO_MS + 1",
      CLIPS),
    m("b1_clip_empty", "a clip is at least 1 ms", "0 <= span[0] < span[1]",
      "0 <= span[0] <= span[1]", CLIPS),
    m("b1_clip_negative_start", "a clip starts at 0 or later", "0 <= span[0] < span[1]",
      "span[0] < span[1]", CLIPS),
    m("b1_clip_fractional_ms", "a span is whole milliseconds",
      "all(type(ms) is int for ms in span)", "True", CLIPS),
    m("b1_clip_extra_bounds", "a span is exactly [start, end]", "len(span) == 2",
      "len(span) >= 2", CLIPS),
    m("b1_clip_no_span", "a missing span is refused by name", "isinstance(span, list) and ",
      "", CLIPS, dies_by=("TypeError",)),
    m("b1_clip_long_lived", "a clip URL lives 600 s", "PRESIGN_S = 600",
      "PRESIGN_S = 604800", VIDEO, CLIPS),
    m("b1_clip_span_unsent", "the span travels as the URL's media fragment",
      '    return f"{url}#t={span[0] / 1000:g},{span[1] / 1000:g}"', "    return url",
      VIDEO, CLIPS),
    m("b1_clip_span_in_ms", "the fragment is in seconds", "{span[1] / 1000:g}", "{span[1]}",
      VIDEO, CLIPS),
    m("b1_presign_unbounded", "the URL expires as asked",
      "            ExpiresIn=expires_s)", "            )", PRESIGN, file=S3),
    m("b1_presign_sigv2", "the URL is SigV4", ' signature_version="s3v4",', "", PRESIGN,
      file=S3),
    m("b1_presign_unprefixed", "the URL names the prefixed key",
      '"Key": self.prefix + key},', '"Key": key},', PRESIGN, file=S3),
    # coordinator wirings at the lab-eval-media merge (WR-LEM-m1/m2/m3)
    m("b1_clip_ref_unanchored", "a media ref is anchored at its end (nothing after the digest)",
      "    if not re.fullmatch(re.escape(", "    if not re.match(re.escape(", CLIPS),
    m("b1_clip_bool_ms", "a span of booleans is refused (bool is not a whole ms)",
      "all(type(ms) is int for ms in span)", "all(isinstance(ms, int) for ms in span)", CLIPS),
    m("b1_presign_head", "the presigned URL is a GET of the object, never a HEAD",
      '"get_object", Params=', '"head_object", Params=', PRESIGN, file=S3),
)
MUTANTS += MEDIA


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


RUNNER = Runner(name="b1", targets=(SUITE,), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the B1 runner mutation list"))
