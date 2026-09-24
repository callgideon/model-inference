#!/usr/bin/env python3
"""R32/R83 for PREP-WORKER (I2B-R5): every invariant the preparation loop claims is killable
by a named case, through the shared runner (`tests/contracts/mutants.py`).

Three lists. `MUTANTS` names the service-free cases of `tests/w/test_prep_worker.py` (the
runner and the service on F's fakes, E2's fake engine in process, the composition), so it
runs anywhere. `CONTRACT_MUTANTS` are edits to F's fake store killed by the two exported
conformance cases this lane added (`dur_fence__prepared_stores_the_exact_prompt_count_once`,
`credit_prepare__the_count_reaches_the_credit_work`), on F's own runner. `PG_MUTANTS` name
the `_pg__` cases (the real `python -m infrx.worker` on PostgreSQL): the copy inherits the
lane's harness settings (`INFRX_D_TASK`, its Valkey, its MinIO).

    uv run --frozen pytest -q tests/w/test_prep_worker_mutants.py
    INFRX_MUTANTS=all INFRX_D_TASK=d5 INFRX_D2_VALKEY_PORT=55467 \\
      INFRX_D2_VALKEY_CONTAINER=infrx-d5-valkey INFRX_M_S3_ENDPOINT=http://127.0.0.1:55781 \\
      INFRX_M_S3_LOCAL_CREDS=1 uv run --frozen pytest -q tests/w/test_prep_worker_mutants.py
    uv run --frozen python -m tests.w.prep_worker_mutants --list
"""
from __future__ import annotations

import re

from ..contracts import mutants as shared
from ..contracts.mutants import Result, Runner, _m
from . import worker_main_mutants

API_DIR = shared.API_DIR
SUITE_FILE = "tests/w/test_prep_worker.py"
P = "worker/preparation.py"
V = "worker/service.py"
MAIN = "worker/__main__.py"
PORTS = "contracts/ports.py"
S = "contracts/fakes/state.py"
FV = "../../../tests/integration/fake_vllm.py"          # E2's fake engine, from `infrx/`

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
SERVICE = "test_prep_worker__the_service_prepares_and_drains_its_preparation_pool"
DEAD = "test_prep_worker__a_dead_preparation_runner_ends_the_service"
COMPOSE = "test_prep_worker__the_worker_composes_the_preparation_pool"
READ_ONLY = "test_prep_worker__a_media_root_the_worker_cannot_write_refuses_startup"
PG_RUN = "test_prep_worker_pg__the_worker_process_prepares_and_runs_an_admitted_job"
PG_DRAIN = "test_prep_worker_pg__sigterm_releases_a_preparation_and_the_next_worker_prepares_it"
DUR = "dur_fence__prepared_stores_the_exact_prompt_count_once"
CREDIT = "credit_prepare__the_count_reaches_the_credit_work"

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
       P, "        count = await engine_prompt_tokens(self.engine, prepared)",
       "        count = 1200", TEXT),
    _m("prep_tokenize_without_the_generation_prompt",
       "the engine counts the prompt the chat route renders (the generation prompt)",
       P, ', "add_generation_prompt": True}', "}", TEXT),
    _m("prep_tokenize_without_the_video_budget",
       "a video is counted at the pinned mm_processor_kwargs the chat body carries",
       P, '        ask["mm_processor_kwargs"] = budget', "        pass", VIDEO),
    _m("prep_tokenizer_errors_untyped", "a tokenizer that does not answer is "
       "dependency_unavailable, never an untyped error",
       P, "    except (httpx.HTTPError, ValueError) as failed:",
       "    except ValueError as failed:", CHECKED),
    _m("prep_count_shape_unchecked", "a bool, negative or non-integer count is no count",
       P, "    if isinstance(count, bool) or not isinstance(count, int) or count < 0:",
       "    if count is None:", CHECKED),
    _m("prep_tokens_disagreement_ignored", "a count that disagrees with its tokens is no count",
       P, "    if tokens is not None and (not isinstance(tokens, list) or len(tokens) != count):",
       "    if False:", CHECKED),
    _m("prep_video_count_unchecked", "a video's count is checked against the pinned budget",
       P, "        if not most // TOKENS_PER_PATCH <= video <= most:", "        if False:",
       CHECKED, NO_COUNT),
    _m("prep_video_ceiling_dropped", "more video tokens than the pinned budget is refused",
       P, "        if not most // TOKENS_PER_PATCH <= video <= most:",
       "        if not most // TOKENS_PER_PATCH <= video:", CHECKED),
    _m("prep_video_floor_one_placeholder", "fewer than one video token per two-frame patch "
       "(an unexpanded placeholder) is refused",
       P, "        if not most // TOKENS_PER_PATCH <= video <= most:",
       "        if not 1 <= video <= most:", CHECKED),
    _m("prep_video_bounds_exclusive", "both bounds of the pinned budget are inclusive",
       P, "        if not most // TOKENS_PER_PATCH <= video <= most:",
       "        if not most // TOKENS_PER_PATCH < video < most:", INSIDE),
    # --- item 1: media through the durable attach ---------------------------------------
    _m("prep_media_skipped", "a job with media is prepared through M's prepare",
       P, "        if work.media_refs:\n            refs = await self._media(lease.job_id)",
       "        if False:\n            refs = await self._media(lease.job_id)", VIDEO),
    _m("prep_attach_not_awaited", "the runner waits for the gateway's late durable attach",
       P, "        while await self.media.attached(job_id) is None:", "        while False:",
       VIDEO),
    _m("prep_attach_wait_unbounded", "the attach wait is bounded (not_found after it)",
       P, "            if time.monotonic() >= end:", "            if False:", NO_ATTACH),
    _m("prep_foreign_ref_counted", "the engine's view is built from the PREPARED refs, so a "
       "ref of another organization is not_found before anything is counted",
       P, "prepared_request(work.model_copy(update={\"prepared_refs\": refs}), 0,",
       "prepared_request(work, 0,", FOREIGN),
    # --- items 1 and 3: the lease -------------------------------------------------------
    _m("prep_no_renewal", "the preparation lease is renewed while the attempt runs (R52)",
       P, "            lease = await self.jobs.heartbeat(lease)", "            pass", RENEW),
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
       FV, "        count = self.prompt_tokens\n", "        count = 1200\n", TEXT),
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
       V, f"            drains.append({_PREP_DRAIN})", "            pass", SERVICE),
    _m("service_preparation_drain_unbounded", "the preparation drain is bounded by "
       "PREPARATION_LEASE_TTL_S, not the generation budget",
       V, _PREP_DRAIN, "self.preparation.drain(bound)", SERVICE),
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
    _m("main_media_root_read_only_accepted", "a media root the worker cannot write refuses "
       "startup",
       MAIN, "os.access(root, os.R_OK | os.W_OK | os.X_OK)", "os.access(root, os.R_OK | os.X_OK)",
       READ_ONLY),
)

#: F's fake store: the prompt count the two conformance cases pin.
CONTRACT_MUTANTS = (
    _m("fake_prepared_drops_the_count", "prepared stores the count",
       S, "            job.prompt_tokens = prompt_tokens\n", "", DUR, CREDIT),
    _m("fake_prepared_count_unbounded", "a count past max_input_tokens or below 0 is refused",
       S, "            if prompt_tokens is not None and not 0 <= prompt_tokens <= "
          "job.request.max_input_tokens:", "            if False:", DUR),
    _m("fake_prepared_count_bound_exclusive", "a count of exactly max_input_tokens is stored",
       S, "not 0 <= prompt_tokens <= job.request.max_input_tokens:",
       "not 0 <= prompt_tokens < job.request.max_input_tokens:", DUR),
    _m("fake_prepared_count_untyped", "a bool or non-integer count is invalid_request",
       S, "        if prompt_tokens is not None and (isinstance(prompt_tokens, bool)\n"
          "                                          or not isinstance(prompt_tokens, int)):",
       "        if False:", DUR),
    _m("fake_refused_count_changes_state", "a refused count changes nothing",
       S, "            # PREP-WORKER: the count must fit the input ceiling the hold was sized on.\n"
          "            if prompt_tokens is not None and not 0 <= prompt_tokens <= "
          "job.request.max_input_tokens:\n"
          "                raise errors.ContextLengthExceeded(\n"
          "                    f\"the prepared prompt ({prompt_tokens} tokens) exceeds "
          "max_input_tokens\")\n"
          "            now = self.clock.now()\n            job.prepared = tuple(media)\n",
       "            now = self.clock.now()\n            job.prepared = tuple(media)\n"
       "            job.state = JobState.queued\n"
       "            if prompt_tokens is not None and not 0 <= prompt_tokens <= "
       "job.request.max_input_tokens:\n"
       "                raise errors.ContextLengthExceeded(\n"
       "                    f\"the prepared prompt ({prompt_tokens} tokens) exceeds "
       "max_input_tokens\")\n", DUR),
    _m("fake_load_work_drops_the_count", "the lease holder's Work carries the count",
       S, "                        prompt_tokens=job.prompt_tokens)", "                        )",
       DUR),
    _m("fake_credit_work_drops_the_count", "the CREDIT lease holder's WorkV2 carries the count",
       S, "budgets=job.budgets, prompt_tokens=job.prompt_tokens)", "budgets=job.budgets)",
       CREDIT),
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
       "engine's count", P, "        count = await engine_prompt_tokens(self.engine, prepared)",
       "        count = 1200", PG_RUN),
    _m("service_preparation_drain_unbounded_on_postgresql", "SIGTERM releases a preparation at "
       "PREPARATION_LEASE_TTL_S; the process exits 0 inside the unit's budget",
       V, _PREP_DRAIN, "self.preparation.drain(bound)", PG_DRAIN),
    _m("prep_renewal_outlives_a_drain", "a released preparation's lease lapses, so the next "
       "worker's reaper requeues it", P, "            renewing.cancel()\n", "", PG_DRAIN),
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


RUNNER = Runner(name="prep-worker", targets=(SUITE_FILE,),
                layout=worker_main_mutants._layout)
PG_RUNNER = Runner(name="prep-worker-pg", targets=(SUITE_FILE,),
                   layout=worker_main_mutants._pg_layout, env=worker_main_mutants.PG_RUNNER.env)


def run_mutant(mutant) -> Result:
    """`MUTANTS` take the shared runner's baseline; the other two lists are not a module's
    `MUTANTS`, so their cases run unmutated first, once per process (R83 (b))."""
    if mutant in CONTRACT_MUTANTS:
        cases = tuple(sorted({case for m in CONTRACT_MUTANTS for case in m.cases}))
        return shared.pristine(cases, shared.CONTRACTS) or shared.run_mutant(
            mutant, shared.CONTRACTS)
    if mutant in PG_MUTANTS:
        cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
        return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run PREP-WORKER's service-free list"))
