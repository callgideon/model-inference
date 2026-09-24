#!/usr/bin/env python3
"""R32/R83 for PREP-WORKER (I2B-R5): every invariant the preparation loop claims is killable
by a named case, through the shared runner (`tests/contracts/mutants.py`).

Two lists. `MUTANTS` names the service-free cases of `tests/w/test_prep_worker.py` (the
runner and the service on F's fakes, E2's fake engine in process, the composition), so it
runs anywhere. `PG_MUTANTS` name the `_pg__` cases (the real `python -m infrx.worker` on
PostgreSQL): the copy inherits the lane's harness settings (`INFRX_D_TASK`, its Valkey, its
MinIO). The fake-store edits the two exported conformance cases this lane added kill
(`dur_fence__prepared_stores_the_exact_prompt_count_once`,
`credit_prepare__the_count_reaches_the_credit_work`) are in F's own list,
`tests/contracts/mutants.py` (`fake_prepared_*`, `fake_*_drops_the_count`), where R32
requires every exported case to be covered.

    uv run --frozen pytest -q tests/w/test_prep_worker_mutants.py
    INFRX_MUTANTS=all INFRX_D_TASK=d5 INFRX_D2_VALKEY_PORT=55467 \\
      INFRX_D2_VALKEY_CONTAINER=infrx-d5-valkey INFRX_M_S3_ENDPOINT=http://127.0.0.1:55781 \\
      INFRX_M_S3_LOCAL_CREDS=1 uv run --frozen pytest -q tests/w/test_prep_worker_mutants.py
    uv run --frozen python -m tests.w.prep_worker_mutants --list
"""
from __future__ import annotations

import pathlib
import re
import shutil

from ..contracts import mutants as shared
from ..contracts.mutants import Result, Runner, _m
from . import worker_main_mutants

API_DIR = shared.API_DIR
SUITE_FILE = "tests/w/test_prep_worker.py"
P = "worker/preparation.py"
V = "worker/service.py"
MAIN = "worker/__main__.py"
PORTS = "contracts/ports.py"
FV = "../../../tests/integration/fake_vllm.py"          # E2's fake engine, from `infrx/`
PREFLIGHT = "../deploy/preflight.py"
CONFIG = "config.py"
STORE = "state/jobstore.py"

SIG = "test_prep_worker__prepared_takes_a_keyword_count_on_every_adapter"
TEXT = "test_prep_worker__a_text_job_is_queued_with_the_engines_own_count"
VIDEO = "test_prep_worker__a_video_job_is_prepared_from_its_durable_attach"
NO_ATTACH = "test_prep_worker__a_job_whose_attach_never_lands_prepares_nothing"
FOREIGN = "test_prep_worker__a_media_ref_of_another_org_is_not_found"
CHECKED = "test_prep_worker__the_engines_answer_is_checked_and_never_guessed"
INSIDE = "test_prep_worker__a_video_count_inside_the_pinned_budget_is_the_count"
NO_COUNT = "test_prep_worker__a_tokenizer_that_cannot_count_prepares_nothing"
REQUEUE = "test_prep_worker__a_refused_attempt_is_requeued_when_its_lease_lapses"
RENEW = "test_prep_worker__the_lease_is_renewed_while_preparation_runs"
HUNG = "test_prep_worker__a_tokenizer_that_never_answers_is_bounded_by_the_budget"
DEFAULT_RENEW = "test_prep_worker__the_default_renewal_keeps_a_long_preparation_alive"
UNTYPED = "test_prep_worker__an_untyped_renewal_failure_is_not_swallowed"
TWICE = "test_prep_worker__a_candidate_offered_twice_is_a_lost_claim_not_a_dead_runner"
ZERO = "test_prep_worker__a_zero_preparation_pool_refuses_startup_by_name"
PG_DISAGREE = ("test_prep_worker_pg__a_tokenize_count_that_disagrees_with_the_usage_settles_"
               "at_the_usage")
PG_INT = "test_prep_worker_pg__a_count_past_postgresql_int_is_context_length_exceeded"
SERVICE = "test_prep_worker__the_service_prepares_and_drains_its_preparation_pool"
DEAD = "test_prep_worker__a_dead_preparation_runner_ends_the_service"
COMPOSE = "test_prep_worker__the_worker_composes_the_preparation_pool"
READ_ONLY = "test_prep_worker__a_media_root_the_worker_cannot_write_refuses_startup"
PG_RUN = "test_prep_worker_pg__the_worker_process_prepares_and_runs_an_admitted_job"
# TOKCOST: the count memo
REPEAT = "test_prep_worker__a_repeated_video_body_is_counted_by_the_engine_once"
OWN_BODY = "test_prep_worker__a_memo_answers_only_its_own_body_media_and_revision"
KEY = "test_prep_worker__the_memo_key_names_the_media_digests_and_the_credit_revision"
STALE = "test_prep_worker__a_stale_memo_is_asked_again"
BOUNDED = "test_prep_worker__the_memo_is_bounded_least_recently_used_first"
NOT_MEMOIZED = "test_prep_worker__a_refused_video_count_is_never_memoized"   # fix round B1
PG_DRAIN = "test_prep_worker_pg__sigterm_releases_a_preparation_and_the_next_worker_prepares_it"

_WAIT = "{waiting, self._pool, self._reaper, *self.loop._tasks,\n"
_PREP_DRAIN = "self.preparation.drain(self.loop.limits.preparation_lease_ttl_s)"
_REFUSED = "        except errors.DomainError as refused:\n            log.warning("

MUTANTS = (
    # --- the contract: the port's signature --------------------------------------------
    _m("port_prepared_count_positional", "the count is keyword-only on the port",
       PORTS, "media: tuple[MediaRef, ...] = (), *,\n                       prompt_tokens",
       "media: tuple[MediaRef, ...] = (),\n                       prompt_tokens", SIG),
    # --- item 2: the count is the engine's, exactly ------------------------------------
    _m("prep_count_not_stored", "prepared stores preparation's count",
       P, "        await self.jobs.prepared(lease, refs, prompt_tokens=count)",
       "        await self.jobs.prepared(lease, refs)", TEXT, VIDEO),
    _m("prep_count_guessed", "the count is the engine's /tokenize answer, never a constant",
       P, "        count = await engine_prompt_tokens(self.engine, prepared,\n"
          "                                           timeout_s=self.limits.preparation_timeout_s)",
       "        count = 1200", TEXT),
    _m("prep_tokenize_without_the_generation_prompt",
       "the engine counts the prompt the chat route renders (the generation prompt)",
       P, ', "add_generation_prompt": True}', "}", TEXT),
    _m("prep_tokenize_without_the_video_budget",
       "a video is counted at the pinned mm_processor_kwargs the chat body carries",
       P, '        ask["mm_processor_kwargs"] = budget', "        pass", VIDEO),
    _m("prep_tokenizer_errors_untyped", "a tokenizer that does not answer is "
       "dependency_unavailable, never an untyped error",
       P, "    except (httpx.HTTPError, ValueError, TimeoutError) as failed:",
       "    except (ValueError, TimeoutError) as failed:", CHECKED),
    _m("prep_tokenizer_wait_unbounded", "a tokenizer that never answers is refused at the "
       "preparation budget, never waited on", P, "        async with asyncio.timeout(timeout_s):",
       "        async with asyncio.timeout(None):", HUNG),
    _m("prep_tokenizer_bound_not_the_budget", "the tokenizer's bound is PREPARATION_TIMEOUT_S",
       P, "timeout_s=self.limits.preparation_timeout_s)", "timeout_s=600.0)", HUNG),
    _m("prep_count_shape_unchecked", "a bool, negative or non-integer count is no count",
       P, "    if isinstance(count, bool) or not isinstance(count, int) or count < 0:",
       "    if count is None:", CHECKED),
    _m("prep_tokens_disagreement_ignored", "a count that disagrees with its tokens is no count",
       P, "    if not isinstance(tokens, list) or len(tokens) != count:", "    if False:",
       CHECKED),
    _m("prep_tokens_optional", "an answer without its tokens is no count (review L4)",
       P, "    if not isinstance(tokens, list) or len(tokens) != count:",
       "    if tokens is not None and (not isinstance(tokens, list) or len(tokens) != count):",
       CHECKED),
    _m("prep_video_count_unchecked", "a video's count is checked against the pinned budget",
       P, "        if not patches <= video <= most:", "        if False:",
       CHECKED, NO_COUNT),
    _m("prep_video_ceiling_dropped", "more video tokens than the pinned budget is refused",
       P, "        if not patches <= video <= most:",
       "        if not patches <= video:", CHECKED),
    _m("prep_video_floor_one_placeholder", "fewer than one video token per two-frame patch "
       "(an unexpanded placeholder) is refused",
       P, "        if not patches <= video <= most:",
       "        if not 1 <= video <= most:", CHECKED),
    _m("prep_video_bounds_exclusive", "both bounds of the pinned budget are inclusive",
       P, "        if not patches <= video <= most:",
       "        if not patches < video < most:", INSIDE),
    # --- item 1: media through the durable attach ---------------------------------------
    _m("prep_media_skipped", "a job with media is prepared through M's prepare",
       P, "        if work.media_refs:\n            refs = await self._media(lease.job_id)",
       "        if False:\n            refs = await self._media(lease.job_id)", VIDEO),
    _m("prep_attach_not_awaited", "the runner waits for the gateway's late durable attach "
       "(W5: the readiness barrier, for every job)",
       P, "        await self._ready(lease.job_id)\n", "", VIDEO),
    _m("prep_attach_wait_default_zero", "the product waits for a late attach (ATTACH_WAIT_S; "
       "review L6)", P, "ATTACH_WAIT_S, ATTACH_POLL_S = 10.0, 0.05",
       "ATTACH_WAIT_S, ATTACH_POLL_S = 0.0, 0.05", VIDEO),
    _m("prep_attach_wait_unbounded", "the attach wait is bounded (not_found after it)",
       P, "            if time.monotonic() >= end:", "            if False:", NO_ATTACH),
    _m("prep_foreign_ref_counted", "the engine's view is built from the PREPARED refs, so a "
       "ref of another organization is not_found before anything is counted",
       P, "prepared_request(work.model_copy(update={\"prepared_refs\": refs}), 0,",
       "prepared_request(work, 0,", FOREIGN),
    # --- items 1 and 3: the lease -------------------------------------------------------
    _m("prep_no_renewal", "the preparation lease is renewed while the attempt runs (R52)",
       P, "            lease = await self.jobs.heartbeat(lease)", "            pass", RENEW),
    _m("prep_renewal_cadence_slower_than_the_lease", "the product renews every third of "
       "PREPARATION_LEASE_TTL_S (review L2)",
       P, "limits.preparation_lease_ttl_s / 3", "limits.preparation_lease_ttl_s * 3",
       DEFAULT_RENEW),
    _m("prep_untyped_renewal_swallowed", "an untyped renewal failure propagates (review L7)",
       P, "                raise died                        # the store under the renewal: "
          "crash-only\n", "                pass\n", UNTYPED),
    _m("prep_lost_claim_kills_the_runner", "a lost claim is answered, never a dead runner "
       "(review L1)",
       P, "        try:\n            lease = await (self.readiness or self.jobs).claim_preparation("
          "job_id, self.worker_id)\n        except errors.DomainError as refused:\n",
       "        lease = await (self.readiness or self.jobs).claim_preparation(job_id, "
       "self.worker_id)\n        if False:\n            refused = None\n", TWICE),
    _m("prep_preparation_unlogged", "each preparation is logged at INFO with its count "
       "(review J-F2)",
       P, '        log.info("prepared %s: %d prompt tokens', '        log.debug("prepared %s: %d prompt tokens',
       TEXT),
    _m("prep_renewal_outlives_the_attempt", "a finished attempt stops renewing, so a refused "
       "attempt's lease lapses for recover",
       P, "            renewing.cancel()\n", "", REQUEUE),
    _m("prep_refusal_prepares_anyway", "a typed refusal prepares nothing (no guessed count)",
       P, _REFUSED, "        except errors.DomainError as refused:\n"
                    "            await self.jobs.prepared(lease, ())\n            log.warning(",
       REQUEUE, NO_COUNT, NO_ATTACH),
    _m("prep_refusal_escapes", "a typed refusal is the runner's answer, never a dead runner",
       P, _REFUSED, "        except errors.NotFound as refused:\n            log.warning(",
       NO_COUNT),
    # --- E2's fake engine: /tokenize (E-owned file) -------------------------------------
    _m("fake_tokenize_not_routed", "the fake engine serves /tokenize",
       FV, '        if path == "/tokenize" and method == "POST":', "        if False:", TEXT),
    _m("fake_tokenize_count_is_not_the_usage", "the fake's count is the usage it reports",
       FV, "        count = self.prompt_tokens if self.tokenize_count is None else "
           "self.tokenize_count\n", "        count = 1200\n", TEXT),
    _m("fake_tokenize_video_unexpanded", "the fake expands a video as the pinned profile does",
       FV, '        pads = videos * (1 if self.tokenize_fault == "unexpanded" else expanded)',
       "        pads = videos", VIDEO),
    _m("fake_tokenize_down_ignored", "the fake's tokenizer can be down",
       FV, '        if self.tokenize_fault == "down":', "        if False:", NO_COUNT),
    # --- item 3: the service (W3's pattern) ---------------------------------------------
    _m("service_preparation_not_started", "the service starts the preparation pool",
       V, "        if self.preparation is not None:\n            self._preparing =",
       "        if False:\n            self._preparing =", SERVICE),
    _m("service_preparation_not_drained", "stop() drains the preparation pool",
       V, "        preparing = None if self.preparation is None else asyncio.ensure_future(",
       "        preparing = None if True else asyncio.ensure_future(", SERVICE),
    _m("service_preparation_drain_unbounded", "the preparation drain is bounded by "
       "PREPARATION_LEASE_TTL_S, not the generation budget",
       V, _PREP_DRAIN, "self.preparation.drain(bound)", SERVICE),
    _m("service_preparation_drain_released_at_once", "an attempt that finishes inside "
       "PREPARATION_LEASE_TTL_S is prepared, not released (review L5)",
       V, _PREP_DRAIN, "self.preparation.drain(0.1)", SERVICE),
    _m("service_preparation_drain_unlogged", "the preparation drain is logged on its own line",
       V, '            log.warning("drained preparation:', '            log.debug("drained preparation:',
       SERVICE),
    _m("service_readiness_hides_preparation", "readiness counts preparations in flight",
       V, '"preparing": 0 if self.preparation is None else len(self.preparation.in_flight),',
       '"preparing": 0,', SERVICE),
    _m("service_readiness_hides_prepared", "readiness counts preparation claims",
       V, '"prepare_claimed": 0 if self.preparation is None else self.preparation.claimed,',
       '"prepare_claimed": 0,', SERVICE),
    _m("service_dead_preparation_runner_ignored", "a dead preparation runner is a death "
       "(not live; the process exits 1)",
       V, "        return [task for task in (*self.loop._tasks, *self._preparation_tasks(), "
          "self._reaper)",
       "        return [task for task in (*self.loop._tasks, self._reaper)", DEAD),
    _m("service_serve_ignores_the_preparation_pool", "a dead preparation runner ends serve",
       V, _WAIT + "                                *self._preparation_tasks()},",
       _WAIT + "                                },", DEAD),
    # --- item 1: the composition --------------------------------------------------------
    _m("main_preparation_pool_absent", "python -m infrx.worker runs the preparation pool",
       MAIN, "metrics=rt.metrics, preparation=preparation,",
       "metrics=rt.metrics, preparation=None,", COMPOSE),
    _m("main_preparation_concurrency_ignored", "PREPARATION_CONCURRENCY sizes the pool",
       MAIN, "preparation_concurrency=limits.preparation_concurrency)",
       "preparation_concurrency=1)", COMPOSE),
    _m("main_preparation_bypasses_the_credit_doors", "preparation reads through the same "
       "work doors as inference (CreditWork in the CREDIT regime)",
       MAIN, "runner=PreparationRunner(jobs=jobs, media=media,",
       "runner=PreparationRunner(jobs=store, media=media,", COMPOSE),
    _m("main_attach_not_durable", "preparation reads the attach D2's tables record",
       MAIN, "    media.attachments = PgAttachments(connect)", "    pass", COMPOSE),
    _m("main_preparation_on_the_inference_kind", "the pool claims prepare_dispatch",
       MAIN, "kind=OutboxKind.prepare_dispatch,", "kind=OutboxKind.inference_dispatch,",
       COMPOSE),
    _m("main_preparation_own_index", "both pools share one index",
       MAIN, "        scheduler=scheduler, worker_id=worker_id, kind=",
       "        scheduler=pilot.valkey_index(limits), worker_id=worker_id, kind=", COMPOSE),
    _m("main_zero_preparation_pool_accepted", "PREPARATION_CONCURRENCY=0 refuses startup, "
       "named (review L3)", CONFIG, '    "preparation_concurrency",\n', "", ZERO),
    _m("preflight_zero_preparation_pool_accepted", "the installer refuses "
       "PREPARATION_CONCURRENCY=0 (positive_int; review L3)",
       PREFLIGHT, ',\n                  "PREPARATION_CONCURRENCY": "positive_int"}', "}", ZERO),
    _m("main_media_root_read_only_accepted", "a media root the worker cannot write refuses "
       "startup",
       MAIN, "os.access(root, os.R_OK | os.W_OK | os.X_OK)", "os.access(root, os.R_OK | os.X_OK)",
       READ_ONLY),
    # --- TOKCOST: the count memo (R105: only the engine's answer to the exact body) -------
    _m("prep_memo_never_consulted", "a repeated video body is its memoized count, the engine "
       "asked once", P, "count, source = (self.memo.get(key) if key else None), ",
       "count, source = None, ", REPEAT),
    _m("prep_memo_never_filled", "the engine's checked video count is memoized",
       P, "                self.memo.put(key, count)", "                pass", REPEAT),
    _m("prep_memo_holds_text", "a text body is asked every time (no digest of a text prompt "
       "is held)", P, "tokenize_body(self.engine, prepared)) if refs else None",
       "tokenize_body(self.engine, prepared)) if True else None", REPEAT),
    _m("prep_memo_hit_logged_as_the_engine", "a memo hit is logged as the memo, never as an "
       "engine latency", P, '"memo of engine /tokenize"', '"engine /tokenize"', REPEAT),
    _m("prep_memo_key_without_the_body", "the memo answers only the exact /tokenize body "
       "(prompt, clip file, organization)", P,
       'for ref in prepared.media), ask]', "for ref in prepared.media)]", OWN_BODY),
    _m("prep_memo_key_without_the_model_revision", "the memo never answers for another "
       "serving revision (the job's model_revision)",
       P, "    facts = [prepared.model_revision, ", "    facts = [None, ", OWN_BODY),
    _m("prep_memo_key_without_the_serving_pin", "the memo never answers for another serving "
       "revision (a CREDIT job's pinned serving_version_id)",
       P, 'getattr(work, "serving_version_id", None),', "None,", KEY),
    _m("prep_memo_key_without_the_media_digests", "the memo answers only the same bytes (every "
       "whole media digest)", P,
       'sorted(f"{ref.digest}@{ref.profile_version}" for ref in prepared.media)', "[]", KEY),
    _m("prep_memo_key_without_the_media_profile", "the memo answers only the same prepared "
       "profile", P, 'f"{ref.digest}@{ref.profile_version}"', 'f"{ref.digest}"', KEY),
    _m("main_credit_work_drops_the_serving_revision", "CreditWork carries the job's pinned "
       "serving revision to the preparation runner",
       MAIN, "serving_version_id=work.request.pins.serving_version_id)",
       "serving_version_id=None)", KEY),
    _m("prep_memo_never_expires", "a memoized count older than its life is asked again",
       P, "        if self.clock() - found[0] >= self.ttl_s:", "        if False:", STALE),
    _m("prep_memo_expiry_exclusive", "a count exactly at its life is stale",
       P, "        if self.clock() - found[0] >= self.ttl_s:",
       "        if self.clock() - found[0] > self.ttl_s:", STALE),
    _m("prep_memo_ttl_not_the_retention", "the product memo lives PROCESSING_CACHE_TTL_S, the "
       "retention of the media it counted", P, "CountMemo(ttl_s=limits.processing_cache_ttl_s)",
       'CountMemo(ttl_s=float("inf"))', STALE),
    _m("prep_memo_unbounded", "the memo holds at most MEMO_ENTRIES counts",
       P, "        if len(self.counts) > self.entries:", "        if False:", BOUNDED),
    _m("prep_memo_evicts_the_recently_used", "the least recently USED count goes first",
       P, "        self.counts.move_to_end(key)\n        return found[1]",
       "        return found[1]", BOUNDED),
    # --- TOKCOST fix round: B1 (R105 fail-closed) and N1 (the product bound) -------------
    _m("prep_memo_holds_a_refused_count", "a count a check refused is never memoized (R105 "
       "fail-closed): the checks run before the put (verifier B1)",
       P, "            count, source = await self._ask(prepared), \"engine /tokenize\"\n"
          "            if key:\n                self.memo.put(key, count)\n",
       "            ask = tokenize_body(self.engine, prepared)\n"
       "            found = (await self.engine.client.post(TOKENIZE_PATH, json=ask)).json()\n"
       "            if key:\n                self.memo.put(key, found[\"count\"])\n"
       "            count = checked_count(found, ask.get(\"mm_processor_kwargs\"))\n"
       "            source = \"engine /tokenize\"\n", NOT_MEMOIZED),
    _m("prep_memo_bound_not_1024", "the memo holds at most 1024 counts (verifier N1)",
       P, "MEMO_ENTRIES = 1024", "MEMO_ENTRIES = 10**9", BOUNDED),
    _m("prep_runner_memo_unbounded", "the product runner's memo is bounded by MEMO_ENTRIES "
       "(verifier N1)", P, "CountMemo(ttl_s=limits.processing_cache_ttl_s)",
       "CountMemo(ttl_s=limits.processing_cache_ttl_s, entries=10**9)", BOUNDED),
)

PG_MUTANTS = (
    _m("main_preparation_pool_absent_on_postgresql", "the worker process prepares the jobs "
       "the gateway admits (nothing else does)",
       MAIN, "metrics=rt.metrics, preparation=preparation,",
       "metrics=rt.metrics, preparation=None,", PG_RUN),
    _m("main_preparation_bypasses_the_credit_doors_on_postgresql", "a CREDIT job is prepared "
       "through load_work_credit in the worker process",
       MAIN, "runner=PreparationRunner(jobs=jobs, media=media,",
       "runner=PreparationRunner(jobs=store, media=media,", PG_RUN),
    _m("prep_count_guessed_on_postgresql", "the stored count and the settled usage are the "
       "engine's count", P, "        count = await engine_prompt_tokens(self.engine, prepared,\n"
       "                                           timeout_s=self.limits.preparation_timeout_s)",
       "        count = 1200", PG_RUN),
    _m("service_preparation_drain_unbounded_on_postgresql", "SIGTERM releases a preparation at "
       "PREPARATION_LEASE_TTL_S; the process exits 0 inside the unit's budget",
       V, _PREP_DRAIN, "self.preparation.drain(bound)", PG_DRAIN),
    _m("pg_prepared_count_past_int_untyped", "a count past PostgreSQL int is "
       "context_length_exceeded, typed (review L4)",
       STORE, "        if prompt_tokens is not None and prompt_tokens > INT4_MAX:",
       "        if False:", PG_INT),
    _m("fake_tokenize_count_override_ignored", "the fake's tokenize_count fault disagrees "
       "with its usage (review L8)",
       FV, "self.prompt_tokens if self.tokenize_count is None else self.tokenize_count",
       "self.prompt_tokens", PG_DISAGREE),
    _m("prep_preparation_unlogged_on_postgresql", "the worker process's log names each "
       "preparation and its count (review J-F2)",
       P, '        log.info("prepared %s: %d prompt tokens', '        log.debug("prepared %s: %d prompt tokens',
       PG_RUN),
    _m("prep_renewal_outlives_a_drain", "a released preparation's lease lapses, so the next "
       "worker's reaper requeues it", P, "            renewing.cancel()\n", "", PG_DRAIN),
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


def _layout(root: pathlib.Path) -> pathlib.Path:
    """I2B-R4's copy (W3's package and tests, E3B's integration tree) plus `deploy/`, whose
    preflight the zero-pool case loads (`tests/i/support.py`)."""
    api = worker_main_mutants._layout(root)
    shutil.copytree(API_DIR / "deploy", api / "deploy",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return api


def _pg_layout(root: pathlib.Path) -> pathlib.Path:
    api = worker_main_mutants._pg_layout(root)
    shutil.copytree(API_DIR / "deploy", api / "deploy",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return api


RUNNER = Runner(name="prep-worker", targets=(SUITE_FILE,), layout=_layout)
PG_RUNNER = Runner(name="prep-worker-pg", targets=(SUITE_FILE,), layout=_pg_layout,
                   env=worker_main_mutants.PG_RUNNER.env)


def run_mutant(mutant) -> Result:
    """`MUTANTS` take the shared runner's baseline; `PG_MUTANTS` is not a module's
    `MUTANTS`, so its cases run unmutated first, once per process (R83 (b))."""
    if mutant in PG_MUTANTS:
        cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
        return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run PREP-WORKER's service-free list"))
