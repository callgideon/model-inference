#!/usr/bin/env python3
"""r1 R32 / R83 for W3: every invariant the W3 cases claim must be killable.

Two kinds of target, one kill rule (`tests/contracts/mutants.py`):

* **Python** (`worker/*.py`) runs through the shared pytest runner, one process per
  mutant, with W3's case files as the target.
* **The engine pin** (`models/marlin2b/serve.sh`, `serving-version.json`) cannot: the
  runner compiles every mutated file as Python. Those edits are applied to a throwaway
  copy of `models/` and the named case's own check runs in process under
  `assertion_kill`, the rule track D's migration list uses. The check must pass on the
  unmutated copy first (R83 (b)), or the mutant is `broken_runner`.

    uv run --frozen pytest -q tests/w/test_w3_mutants.py              # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_w3_mutants.py
    uv run --frozen python -m tests.w.w3_mutants --list
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Outcome, Result, Runner, _m
from . import test_serving as serving

S = "marlin2b/serve.sh"
J = "marlin2b/serving-version.json"
PIN_FILES = ("common/env.sh", "marlin2b/model.env", S, J)

LAUNCH = "test_perf_pilot__the_engine_starts_pinned_on_loopback_with_the_recorded_flags"
ONE_SOURCE = "test_perf_pilot__a_pinned_setting_has_one_source"
RECORD = "test_perf_pilot__the_serving_record_matches_the_code_it_pins"
PIN_CHECKS = {LAUNCH: serving.check_pinned_launch,
              ONE_SOURCE: serving.check_one_source_per_setting,
              RECORD: serving.check_record_matches_the_code}
DIGEST = "sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42"

PIN_MUTANTS: tuple[Mutant, ...] = (
    _m("image_is_a_moving_tag", "the engine is pulled by registry digest (R76, I0 gate)",
       S, f"IMAGE=${{IMAGE:-vllm/vllm-openai@{DIGEST}}}",
       "IMAGE=${IMAGE:-vllm/vllm-openai:nightly}", LAUNCH),
    _m("published_beyond_loopback", "the engine is reachable on loopback only",
       S, '-p "127.0.0.1:$PORT:8000"', '-p "$PORT:8000"', LAUNCH),
    _m("media_mount_writable", "the engine reads prepared media, never writes it",
       S, 'media=(-v "$PROCESSING_CACHE_DIR:$PROCESSING_CACHE_DIR:ro")',
       'media=(-v "$PROCESSING_CACHE_DIR:$PROCESSING_CACHE_DIR")', LAUNCH),
    _m("media_mounted_elsewhere", "the file:// path the adapter sends exists in the engine",
       S, 'media=(-v "$PROCESSING_CACHE_DIR:$PROCESSING_CACHE_DIR:ro")',
       'media=(-v "$PROCESSING_CACHE_DIR:/media:ro")', LAUNCH),
    _m("media_root_not_allowed", "R61 (2): the engine may open files under the root",
       S, 'flags=(--allowed-local-media-path "$PROCESSING_CACHE_DIR")', "flags=()", LAUNCH),
    _m("max_num_seqs_default_32", "the default is the recorded (pilot) value",
       S, "ENGINE_MAX_NUM_SEQS=${ENGINE_MAX_NUM_SEQS:-8}",
       "ENGINE_MAX_NUM_SEQS=${ENGINE_MAX_NUM_SEQS:-32}", LAUNCH),
    _m("max_num_seqs_ignores_its_setting", "ENGINE_MAX_NUM_SEQS is the one source",
       S, '--max-num-seqs "$ENGINE_MAX_NUM_SEQS"', "--max-num-seqs 8", ONE_SOURCE),
    _m("caller_overrides_a_pinned_flag", "a second value on the command line is refused",
       S, "--max-num-seqs*|--allowed-local-media-path*|--api-key*)", "--never-pinned*)",
       ONE_SOURCE),
    _m("api_key_passes_through", "the engine takes no API key",
       S, "|--api-key*)", ")", ONE_SOURCE),
    _m("relative_root_accepted", "the media root is absolute",
       S, "    /*) ;;", "    /*|*) ;;", ONE_SOURCE),
    _m("missing_root_accepted", "a root that does not exist is refused, not created by docker",
       S, '  test -d "$PROCESSING_CACHE_DIR" || {', "  true || {", ONE_SOURCE),
    _m("record_flag_drift", "the record's flags are the flags served",
       J, '    "bfloat16",', '    "float16",', LAUNCH),
    _m("record_digest_stale", "engine_options_digest is recomputed from the flags",
       J, '"engine_options_digest": "sha256:3c4b', '"engine_options_digest": "sha256:3c4c',
       LAUNCH),
    _m("record_names_one_eos_id", "both EOS ids are the pinned artifact's (R61 (3))",
       J, "    248044,\n", "", RECORD),
    _m("record_setting_is_not_the_contract", "the recorded max-num-seqs is the pilot setting",
       J, '"ENGINE_MAX_NUM_SEQS": "8"', '"ENGINE_MAX_NUM_SEQS": "32"', RECORD),
    _m("record_digest_source_invented", "R76: provenance is one of the recorded sources",
       J, '"digest_source": "registry_oid"', '"digest_source": "registry"', RECORD),
)


def _worded(check):
    """Outside pytest an `assert` carries no message, and the shared `_first_line` indexes
    the first line of one; give it the check's name instead."""
    def run(*args):
        try:
            check(*args)
        except AssertionError as failure:
            raise AssertionError(str(failure).strip() or f"{check.__name__} failed") from None
    return run


def run_pin_mutant(mutant: Mutant) -> Result:
    """Copy the four files, check the pristine copy, apply the one edit, check again."""
    check = _worded(PIN_CHECKS[mutant.cases[0]])
    with tempfile.TemporaryDirectory(prefix=f"w3-pin-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        models = root / "models"
        for name in PIN_FILES:
            (models / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(serving.MODELS / name, models / name)
        (root / "pristine").mkdir()
        baseline = shared.assertion_kill(check, models, root / "pristine")
        if baseline.outcome is not Outcome.survived:
            return Result(Outcome.broken_runner, f"pristine copy fails its own check: "
                                                 f"{baseline.detail}")
        target = models / mutant.file
        source = target.read_text()
        found = source.count(mutant.old)
        if found != mutant.occurrences:
            return Result(Outcome.misdeclared, f"anchor appears {found} times in "
                                               f"{mutant.file}: {mutant.old[:60]!r}")
        target.write_text(source.replace(mutant.old, mutant.new, 1))
        (root / "mutated").mkdir()
        return shared.assertion_kill(check, models, root / "mutated")


# --- the Python half: the shared pytest runner ---------------------------------------
API_DIR = shared.API_DIR
REPO = API_DIR.parents[1]
# Outside the package, read by the cases as they stand: the integration engine the drills
# start as a process, and the bench client whose phase names the timings must use.
BESIDE = ("tests/integration/fake_vllm.py", "models/marlin2b/bench.py")


def _layout(root: pathlib.Path) -> pathlib.Path:
    """The repository's shape, so `REPO`-relative paths in the cases resolve in the copy."""
    api = root / "apps" / "infrx-api"
    api.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__")
    for name in ("infrx", "tests"):
        shutil.copytree(API_DIR / name, api / name, ignore=ignore)
    shutil.copy2(API_DIR / "pyproject.toml", api / "pyproject.toml")
    for name in BESIDE:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, root / name)
    return api


RUNNER = Runner(name="w3", targets=("tests/w/test_service.py",), layout=_layout)

E = "worker/engine.py"
L = "worker/loop.py"
A = "worker/attempt.py"
V = "worker/service.py"

ROOT = "test_api_stream__the_media_root_is_the_processing_cache_setting"
REAPER = "test_ops_recover__the_reaper_requeues_lost_leases_and_dispatches_them"
RESTART = "test_ops_recover__a_restart_reaps_before_it_claims_and_survives_a_failing_store"
TIMER = "test_ops_recover__the_reaper_runs_on_its_timer"
RELEASE = "test_ops_recover__a_drain_past_its_bound_releases_and_the_job_is_requeued_not_lost"
ENDED = "test_ops_recover__a_drain_records_what_finished_inside_its_bound"
SIGTERM = "test_ops_recover__sigterm_drains_within_the_bound_and_exits_cleanly"
READY = "test_ops_recover__readiness_tells_engine_down_from_idle_from_busy_from_draining"
PUBLIC = "test_ops_recover__readiness_is_never_public_and_leaks_nothing"
REAL_RELEASE = ("test_ops_recover__on_the_integration_engine_a_released_attempt_completes_"
                "after_requeue")
REAL_LOSS = "test_ops_recover__engine_process_loss_is_a_typed_failure_and_readiness_follows_it"
TIMED = "test_perf_pilot__an_attempt_times_its_phases_in_the_bench_vocabulary"

PY_MUTANTS: tuple[Mutant, ...] = (
    # --- (2) one media root -----------------------------------------------------------
    _m("unset_root_is_guessed", "no PROCESSING_CACHE_DIR: dependency_unavailable, no path",
       E, "    if not root:\n        raise errors.DependencyUnavailable(",
       "    if False:\n        raise errors.DependencyUnavailable(", ROOT),
    _m("root_is_not_the_setting", "the adapter's root is PROCESSING_CACHE_DIR",
       E, "self.local_media_root = limits.processing_cache_dir if local_media_root is None",
       'self.local_media_root = "/srv/infrx/processing" if local_media_root is None', ROOT),
    # --- the reaper --------------------------------------------------------------------
    _m("reaper_enqueues_nothing", "every index event recover returns is enqueued",
       V, "                await self.loop.scheduler.enqueue(event)", "                pass",
       REAPER, REAL_RELEASE),
    _m("reaper_enqueues_outcomes", "only index events are dispatched",
       V, "events = [item for item in produced if isinstance(item, IndexEvent)]",
       "events = list(produced)", REAPER),
    _m("reaper_uncounted", "the reaper reports what it dispatched",
       V, "        self.reaped += len(events)\n", "", REAPER),
    _m("recover_failure_is_fatal", "a store that is down is retried, never fatal",
       V, "        except Exception as failure:              # the store is down",
       "        except LookupError as failure:              # the store is down", RESTART,
       dies_by=("ConnectionError",)),
    _m("enqueue_failure_is_fatal", "an index that is down is Q's to repair, never fatal",
       V, "            except Exception as failure:          # the outbox row is durable",
       "            except LookupError as failure:          # the outbox row is durable",
       RESTART, dies_by=("ConnectionError",)),
    _m("start_does_not_reap", "a restart requeues what died with its predecessor first",
       V, "        await self.reap_once()                    # a restart requeues",
       "        pass                    # a restart requeues", RESTART),
    _m("timer_never_reaps", "the reaper also runs on its timer",
       V, "            await asyncio.sleep(self.reap_interval_s)\n            await self.reap_once()",
       "            await asyncio.sleep(self.reap_interval_s)", TIMER),
    # --- (3) drain ---------------------------------------------------------------------
    _m("stop_ignores_its_bound", "an attempt that fits the bound is waited for",
       V, "        report = await self.loop.drain(bound)",
       "        report = await self.loop.drain(0.0)", ENDED, SIGTERM),
    _m("default_bound_is_not_the_budget", "unset, the bound is the generation budget",
       V, "bound = self.loop.limits.generation_timeout_s if self.drain_s is None",
       "bound = 30.0 if self.drain_s is None", ENDED),
    _m("released_jobs_not_recorded", "a drain records which jobs it released",
       L, "        released = tuple(self.in_flight[task] for task in pending if task in "
          "self.in_flight)", "        released = ()", RELEASE, REAL_RELEASE, PUBLIC),
    _m("ended_jobs_not_recorded", "a drain records what finished inside it, and how",
       L, "        ended = tuple((result.job_id, str(result.cause or result.refusal))\n"
          "                      for result in self.results[seen:])", "        ended = ()",
       ENDED, SIGTERM),
    _m("in_flight_never_recorded", "the loop knows which job each runner holds",
       L, "        self.in_flight[asyncio.current_task()] = candidate.job_id\n", "",
       RELEASE, READY),
    _m("in_flight_never_cleared", "a runner that finished holds nothing",
       L, "            self.in_flight.pop(asyncio.current_task(), None)\n", "", TIMER),
    _m("run_undoes_an_early_drain", "a stop straight after start still stops the pool",
       L, "        self._tasks = [asyncio.create_task(",
       "        self.draining = False\n        self._tasks = [asyncio.create_task(", ENDED),
    _m("sigterm_not_handled", "SIGTERM drains; it does not kill the process mid-attempt",
       V, "            running.add_signal_handler(sig, stop.set)",
       "            pass", SIGTERM),
    _m("serve_waits_only_for_a_signal", "a pool that died ends serve by itself",
       V, "            await asyncio.wait({waiting, self._pool}, "
          "return_when=asyncio.FIRST_COMPLETED)", "            await waiting", READY),
    # --- (4) readiness -----------------------------------------------------------------
    _m("ready_ignores_the_engine", "an engine that is not ready is not ready",
       V, '        return {"ready": engine_up and state in ("idle", "busy"),',
       '        return {"ready": state in ("idle", "busy"),', READY, REAL_LOSS),
    _m("ready_while_draining", "a draining worker is not ready",
       V, '        return {"ready": engine_up and state in ("idle", "busy"),',
       '        return {"ready": engine_up,', READY),
    _m("always_live", "a pool that died unasked is not live",
       V, '"live": state != "stopped" or self.loop.draining,', '"live": True,', READY),
    _m("health_failure_escapes", "an engine client that raises is down, not an error",
       V, "        except Exception:                         # slow, refusing or broken",
       "        except TimeoutError:                         # slow, refusing or broken",
       READY, dies_by=("RuntimeError",)),
    _m("readiness_is_public", "readiness binds loopback only",
       V, "        if not loopback:\n", "        if False:\n", PUBLIC),
    _m("probe_answers_any_method", "only GET of the two probe paths answers",
       V, 'if method != "GET" or path not in (READY_PATH, LIVE_PATH):',
       "if path not in (READY_PATH, LIVE_PATH):", PUBLIC),
    _m("readiness_leaks_job_ids", "the readiness body carries counts, never a job id",
       V, 'if key in ("finished", "released", "claimed")}}', "}}", PUBLIC),
    # --- (5) PERF-PILOT timings --------------------------------------------------------
    _m("prefill_runs_to_the_end", "prefill ends at the first delta",
       A, '"prefill", started, until=state.first_delta_at)', '"prefill", started)', TIMED),
    _m("generate_from_the_request", "generate starts at the first delta",
       A, 'self._time(result, "generate", state.first_delta_at)',
       'self._time(result, "generate", started)', TIMED),
    _m("first_delta_is_the_last", "the first delta's instant is kept",
       A, "            if state.first_delta_at is None:\n                state.first_delta_at",
       "            if True:\n                state.first_delta_at", TIMED),
    _m("journal_keeps_one_append", "every append is timed, not only the last",
       A, "result.timings[phase] = result.timings.get(phase, 0.0) + span",
       "result.timings[phase] = span", TIMED),
    _m("persist_untimed", "the result object's write is timed",
       A, '                self._time(result, "persist", began)\n', "", TIMED),
    _m("settle_untimed", "the settling call is timed",
       A, '        self._time(result, "settle", began)\n', "", TIMED),
    _m("server_timing_in_seconds", "Server-Timing durations are milliseconds",
       A, 'f"{name};dur={ms:.1f}"', 'f"{name};dur={ms / 1000:.1f}"', TIMED),
    _m("absent_phase_is_zero", "a phase that did not happen is absent, not zero",
       A, "    timings: dict[str, float] = field(default_factory=dict)",
       "    timings: dict[str, float] = field(default_factory=lambda: dict.fromkeys("
       "TIMED_PHASES, 0.0))", TIMED),
)

MUTANTS: tuple[Mutant, ...] = PIN_MUTANTS + PY_MUTANTS


def run(mutant: Mutant) -> Result:
    if mutant.file.startswith("worker/"):
        return shared.run_mutant(mutant, RUNNER)
    return run_pin_mutant(mutant)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="run W3's mutation list")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:44s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over {len({c for m in MUTANTS for c in m.cases})} "
              f"named cases")
        return 0
    chosen = [m for m in MUTANTS if not args.names or m.name in args.names]
    bad = []
    for mutant in chosen:
        result = run(mutant)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}", flush=True)
        if not result.killed:
            bad.append(mutant.name)
    print(f"\n{len(chosen) - len(bad)}/{len(chosen)} killed" + (f"; not killed: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
