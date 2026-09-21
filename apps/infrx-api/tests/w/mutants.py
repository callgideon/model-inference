#!/usr/bin/env python3
"""r1 R32 for the W adapter: every invariant `tests/w` claims must be killable.

Each entry is one single edit to `infrx/worker/` that breaks one named invariant,
with the cases that must fail because of it. The runner copies the package and the
tests into a temporary directory, applies one mutant there, runs only the named
cases, and fails if the mutant survives. The worktree is never written to.

    uv run --frozen pytest -q tests/w/test_mutants.py            # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_mutants.py
    uv run --frozen python tests/w/mutants.py --list

A kill needs pytest to exit 1, at least one failure, and every failure to be one of
the mutant's own cases: a syntax error or an import failure makes the named cases
error out instead, which is reported as `broken_runner` and fails the run just as a
survivor does (`test_mutants.py` exercises each outcome).

ponytail: `tests/contracts/mutants.py` has the same machinery but hardcodes its own
pytest target, and it is not this track's file to parameterise. If a third track needs
it, ask the coordinator to move `Mutant`/`run_mutant` into the contracts package with
the target as an argument, and delete this copy.
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
SUITE = ("tests/w/test_engine.py", "tests/w/test_reasoning.py")

E = "worker/engine.py"
R = "worker/reasoning.py"


@dataclass(frozen=True)
class Mutant:
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


MUTANTS: tuple[Mutant, ...] = (
    # --- translation ----------------------------------------------------------
    _m("body_sends_the_model_revision", "vLLM is asked for its served name",
       E, '"model": self.served_model,          # the engine\'s served name, not the revision',
       '"model": prepared.model_revision,',
       "test_api_stream__the_body_carries_the_served_name_the_ceiling_and_the_salt"),
    _m("ceiling_not_sent", "the engine is given the request's ceiling",
       E, '"max_tokens": ceiling,', '"max_tokens": self.limits.max_output_tokens,',
       "test_api_stream__the_body_carries_the_served_name_the_ceiling_and_the_salt",
       "test_api_stream__the_output_ceiling_is_validated_and_enforced"),
    _m("usage_not_requested", "usage is requested, or the settlement is unknown",
       E, '"stream_options": {"include_usage": True},', '"stream_options": {},',
       "test_api_stream__the_body_carries_the_served_name_the_ceiling_and_the_salt"),
    _m("customer_url_reaches_the_engine", "the engine never sees a customer URL",
       E, "        for part, ref in zip(parts, prepared.media):", "        for part, ref in ():",
       "test_api_stream__prepared_media_replaces_the_customers_url"),
    _m("media_parts_not_counted", "prepared refs and media parts must agree",
       E, "        if len(parts) != len(prepared.media):", "        if False:",
       "test_api_stream__prepared_media_replaces_the_customers_url"),
    _m("two_videos_accepted", "one video per request",
       E, "        if len(videos) > 1:", "        if False:",
       "test_api_stream__prepared_media_replaces_the_customers_url"),
    _m("frame_budget_rewritten", "the video budget is F1's own function",
       E, '            body["mm_processor_kwargs"] = self._media.budget_kwargs(seconds)',
       '            body["mm_processor_kwargs"] = {"fps": 2.0, "min_frames": 4,\n'
       '                                          "max_frames": 240,\n'
       '                                          "size": {"shortest_edge": 4096,\n'
       '                                                   "longest_edge": 200704}}',
       "test_api_stream__the_video_token_budget_is_f1s_own_function"),
    # --- tenant namespacing ---------------------------------------------------
    _m("salt_ignores_the_tenant", "the cache salt is per tenant",
       E, '    parts = [tenant, prepared.profile_version, prepared.model_revision,',
       '    parts = [prepared.profile_version, prepared.model_revision,',
       "test_api_stream__the_cache_salt_is_per_tenant_and_per_profile"),
    _m("salt_shared_when_the_tenant_is_unknown", "an unknown tenant shares with nobody",
       E, '        tenant = "|".join(orgs) if orgs else prepared.request_id',
       '        tenant = "|".join(orgs) if orgs else "shared"',
       "test_api_stream__the_cache_salt_is_per_tenant_and_per_profile"),
    _m("salt_ignores_the_profile_version", "the profile version namespaces the cache",
       E, "    parts = [tenant, prepared.profile_version, prepared.model_revision,",
       "    parts = [tenant, prepared.model_revision,",
       "test_api_stream__the_cache_salt_is_per_tenant_and_per_profile"),
    _m("media_uuid_unsalted", "multimodal uuids share the prefix cache's namespace",
       E, '    return hashlib.sha256(f"{salt}\\x1f{ref.digest}".encode()).hexdigest()[:32]',
       '    return hashlib.sha256(ref.digest.encode()).hexdigest()[:32]',
       "test_api_stream__prepared_media_replaces_the_customers_url"),
    # --- refused options ------------------------------------------------------
    _m("unknown_parameters_forwarded", "an unsupported option is refused explicitly",
       E, "            if name in REFUSED_PARAMETERS or name not in PASSTHROUGH_PARAMETERS:\n"
          '                raise errors.UnsupportedParameter(f"{name} is not supported", param=name)',
       "            if False:\n"
          '                raise errors.UnsupportedParameter(f"{name} is not supported", param=name)',
       "test_api_stream__unsupported_options_are_refused_explicitly"),
    _m("n_above_one_accepted", "n>1 is refused",
       E, "                if value != 1:", "                if False:",
       "test_api_stream__unsupported_options_are_refused_explicitly"),
    # --- ceilings -------------------------------------------------------------
    _m("ceiling_lower_bound_dropped", "a zero ceiling is not a valid request",
       E, "        if not 1 <= ceiling <= self.limits.max_output_tokens:",
       "        if ceiling > self.limits.max_output_tokens:",
       "test_api_stream__the_output_ceiling_is_validated_and_enforced"),
    _m("context_unchecked", "the prompt plus the ceiling must fit the context",
       E, "        if total > self.limits.max_context_tokens:", "        if False:",
       "test_api_stream__the_output_ceiling_is_validated_and_enforced"),
    _m("deltas_past_the_ceiling_accepted", "more deltas than the ceiling is a violation",
       E, "            if stream.deltas > ceiling:", "            if False:",
       "test_api_stream__the_output_ceiling_is_validated_and_enforced"),
    _m("usage_past_the_ceiling_accepted", "usage beyond the envelope is a platform failure",
       E, "        if usage.completion_tokens > ceiling:", "        if False:",
       "test_api_stream__the_output_ceiling_is_validated_and_enforced"),
    # --- usage ----------------------------------------------------------------
    _m("usage_estimated_from_deltas", "output chunks never become a token count",
       E, "        if usage is None:\n            stream.malformed_usage = True",
       "        if usage is None:\n            stream.malformed_usage = True\n"
       "            usage = Usage.of(stream.prepared.prompt_tokens, stream.deltas)\n"
       "            stream.usage = usage\n"
       "            return EngineEvent(type=ChunkEventType.usage, payload={}, usage=usage)",
       "test_api_stream__missing_or_malformed_usage_is_explicitly_unknown"),
    _m("usage_totals_not_checked", "a usage object that does not add up is unknown",
       E, "    if total is not None and (isinstance(total, bool) or not isinstance(total, int)\n"
          "                             or total != prompt + completion):",
       "    if total is not None and (isinstance(total, bool) or not isinstance(total, int)):",
       "test_api_stream__missing_or_malformed_usage_is_explicitly_unknown"),
    _m("usage_strings_trusted", "token counts are integers or the usage is unknown",
       E, "        if isinstance(value, bool) or not isinstance(value, int) or value < 0:\n"
          "            return None",
       "        if value is None:\n            return None",
       "test_api_stream__missing_or_malformed_usage_is_explicitly_unknown"),
    _m("two_usage_events", "at most one usage event per stream",
       E, "        if obj.get(\"usage\") is not None and not stream.usage_seen:",
       "        if obj.get(\"usage\") is not None:",
       "test_api_stream__at_most_one_usage_event_per_stream"),
    # --- failure classes ------------------------------------------------------
    _m("pre_header_status_ignored", "a non-200 is an engine error, not a stream",
       E, "                if response.status_code != 200:", "                if False:",
       "test_api_stream__transport_engine_and_incomplete_failures_are_distinct"),
    _m("in_stream_error_swallowed", "an error object in the stream is an engine error",
       E, "        if obj.get(\"error\") is not None:", "        if False:",
       "test_api_stream__transport_engine_and_incomplete_failures_are_distinct"),
    _m("incomplete_looks_complete", "output that just stopped is not a clean answer",
       E, "        if not stream.complete and stream.stall is None and stream.finish_reason is None:\n"
          '            raise EngineIncomplete("the stream ended without a completion marker",\n'
          "                                   deltas=stream.deltas)",
       "        if False:\n"
          '            raise EngineIncomplete("the stream ended without a completion marker",\n'
          "                                   deltas=stream.deltas)",
       "test_api_stream__transport_engine_and_incomplete_failures_are_distinct"),
    _m("transport_failure_hidden", "a dead connection is a transport failure",
       E, "        except httpx.HTTPError as failure:\n"
          "            raise EngineTransportError(f\"{type(failure).__name__}: {failure}\",\n"
          '                                       stage="stream" if stream.started else "pre_headers") from None',
       "        except httpx.HTTPError as failure:\n            pass",
       "test_api_stream__transport_engine_and_incomplete_failures_are_distinct"),
    _m("engine_detail_unbounded", "operator detail is bounded",
       E, "        self.detail = detail[:DETAIL_MAX_CHARS]", "        self.detail = detail",
       "test_api_stream__transport_engine_and_incomplete_failures_are_distinct"),
    # --- timers ---------------------------------------------------------------
    _m("stall_never_stops_the_stream", "a stall stops the stream",
       E, "                    if stall is not None:", "                    if False:",
       "test_api_stream__a_stall_ends_the_stream_and_says_which_bound_it_passed"),
    _m("first_token_deadline_ignored", "the store's first-token instant binds the worker",
       E, "        if stream.deltas == 0 and lease.first_token_deadline_at is not None \\\n"
          "                and now >= lease.first_token_deadline_at:",
       "        if False:",
       "test_api_stream__a_stall_ends_the_stream_and_says_which_bound_it_passed",
       "test_api_stream__a_generation_deadline_ends_the_attempt"),
    _m("inter_event_budget_ignored", "the inter-event budget binds the worker",
       E, "        if stream.last_event_at is not None and stream.deltas > 0 \\\n"
          "                and now >= stream.last_event_at + timedelta(seconds=self.limits.tpot_stall_s):",
       "        if False:",
       "test_api_stream__a_stall_ends_the_stream_and_says_which_bound_it_passed"),
    # A keepalive comment is evidence the socket is open, not evidence of progress: if it
    # reset the inter-event timer, a stalled engine that pings would never be cut off.
    _m("keepalives_count_as_progress", "a keepalive is not an event",
       E, '        if not line.startswith("data:"):\n'
          "            return []                                    # blank separator, or a `:` keepalive",
       '        if not line.startswith("data:"):\n'
          "            stream.last_event_at = now\n            return []",
       "test_api_stream__a_stall_ends_the_stream_and_says_which_bound_it_passed"),
    _m("read_timeout_is_a_failure_not_a_stall", "silence after headers is a stall",
       E, "            if not stream.started:\n"
          "                raise EngineTransportError(f\"{type(failure).__name__}: {failure}\",\n"
          '                                           stage="pre_headers") from None',
       "            if True:\n"
          "                raise EngineTransportError(f\"{type(failure).__name__}: {failure}\",\n"
          '                                           stage="pre_headers") from None',
       "test_api_stream__a_stall_ends_the_stream_and_says_which_bound_it_passed"),
    # --- cancellation ---------------------------------------------------------
    _m("cancellation_ignored_mid_stream", "cancelling reaches the engine",
       E, "                    if lease.job_id in self.cancelled:\n"
          "                        stream.cancelled = True\n                        break",
       "                    if False:\n"
          "                        stream.cancelled = True\n                        break",
       "test_api_stream__cancellation_closes_the_upstream_stream"),
    _m("cancelled_request_still_sent", "a cancelled request is never sent",
       E, "        if lease.job_id in self.cancelled:\n"
          "            # Cancelled before the request was sent: nothing ran, so zero tokens is the",
       "        if False:\n"
          "            # Cancelled before the request was sent: nothing ran, so zero tokens is the",
       "test_api_stream__a_request_cancelled_before_it_starts_is_never_sent"),
    _m("cancelled_usage_estimated", "a truncated stream leaves the usage unknown",
       E, "            yield EngineEvent(type=ChunkEventType.usage,\n"
          '                              payload={"reason": "cancelled", "certainty": "unknown"})',
       "            yield EngineEvent(type=ChunkEventType.usage,\n"
          '                              payload={"reason": "cancelled"},\n'
          "                              usage=Usage.of(prepared.prompt_tokens, stream.deltas))",
       "test_api_stream__cancellation_closes_the_upstream_stream"),
    # --- readiness ------------------------------------------------------------
    _m("drain_is_not_observable", "drain stops reporting ready",
       E, "        if self.drained:\n            return {\"ready\": False, \"drained\": True,",
       "        if False:\n            return {\"ready\": False, \"drained\": True,",
       "test_f_contract__health_drain_and_the_capability_probe"),
    _m("health_ignores_the_engine", "an engine that does not answer is not ready",
       E, "            ready = response.status_code == 200", "            ready = True",
       "test_f_contract__health_drain_and_the_capability_probe"),
    _m("capability_probe_accepts_any_model", "the pinned engine must serve the model",
       E, "        if self.served_model not in models:", "        if False:",
       "test_f_contract__health_drain_and_the_capability_probe"),
    _m("capability_probe_accepts_any_version", "the version pin is checked",
       E, "        if self.require_version is not None and version != self.require_version:",
       "        if False:", "test_f_contract__health_drain_and_the_capability_probe"),
    # --- junk -----------------------------------------------------------------
    _m("junk_relayed_as_content", "a line that is not JSON is never relayed",
       E, "        except ValueError:\n            stream.malformed_lines += 1\n            return []",
       "        except ValueError:\n            stream.malformed_lines += 1\n"
       "            return [EngineEvent(type=ChunkEventType.delta,\n"
       '                                payload={"content": payload, "visible": payload})]',
       "test_api_stream__junk_lines_are_counted_and_never_relayed"),
    # --- the reasoning filter -------------------------------------------------
    _m("close_delimiter_tail_forgotten", "a split `</think>` is still recognised",
       R, "            self._tail = buf[-(len(CLOSE) - 1):]", '            self._tail = ""',
       "test_api_stream__every_chunk_split_filters_to_the_same_text",
       "test_api_stream__the_close_delimiter_may_straddle_any_boundary"),
    _m("open_delimiter_prefix_forgotten", "a split `<think>` is still recognised",
       R, "        if OPEN.startswith(stripped):          # a proper prefix: it may still complete\n"
          "            self._held = buf\n            return \"\"",
       "        if False:\n            self._held = buf\n            return \"\"",
       "test_api_stream__every_chunk_split_filters_to_the_same_text",
       "test_api_stream__an_unclosed_block_never_becomes_visible"),
    _m("reasoning_text_leaks_before_the_close", "reasoning is dropped, not relayed",
       R, "            self._tail = buf[-(len(CLOSE) - 1):]\n            return \"\"",
       "            self._tail = buf[-(len(CLOSE) - 1):]\n            return buf",
       "test_api_stream__every_chunk_split_filters_to_the_same_text"),
    # `if self._reasoning: ...` cannot be mutated on its own: `_held` is empty inside the
    # block, so returning it changes nothing. The leak has to come from the tail the
    # close-delimiter detector is holding, which is what this hands back.
    _m("unclosed_reasoning_leaks", "an unclosed block never becomes visible",
       R, '        held, self._held, self._tail = self._held, "", ""\n'
          "        if self._reasoning:\n"
          '            return ""                          # unclosed reasoning stays reasoning',
       '        held, self._held, tail, self._tail = self._held, "", self._tail, ""\n'
          "        if self._reasoning:\n            return tail",
       "test_api_stream__an_unclosed_block_never_becomes_visible"),
    _m("any_occurrence_stripped", "only a leading block is a delimiter",
       R, "        self._held, self._decided = \"\", True   # it was never a delimiter: emit it whole\n"
          "        return buf",
       "        self._held, self._decided = \"\", True\n        return buf.replace(OPEN, \"\")",
       "test_api_stream__only_a_leading_block_is_a_delimiter",
       "test_api_stream__every_chunk_split_filters_to_the_same_text"),
    _m("held_prefix_never_released", "a partial delimiter that never completes is text",
       R, "        held, self._held, self._tail = self._held, \"\", \"\"",
       "        held, self._held, self._tail = \"\", \"\", \"\"",
       "test_api_stream__a_partial_delimiter_at_the_end_is_released",
       "test_api_stream__every_chunk_split_filters_to_the_same_text"),
    _m("whitespace_after_the_close_kept", "the whitespace around the block goes with it",
       R, "        if self._eat_ws:\n            text = text.lstrip()", "        if False:\n            text = text.lstrip()",
       "test_api_stream__only_a_leading_block_is_a_delimiter",
       "test_api_stream__every_chunk_split_filters_to_the_same_text"),
)


class Outcome(enum.StrEnum):
    killed = "killed"
    survived = "survived"
    broken_runner = "broken_runner"
    misdeclared = "misdeclared"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    detail: str

    @property
    def killed(self) -> bool:
        return self.outcome is Outcome.killed

    @property
    def ok(self) -> bool:
        return self.killed


PYTEST_ALL_PASSED = 0
PYTEST_TESTS_FAILED = 1
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) ([^\s:]+(?:::[^\s]+)?)")


def _failing_ids(stdout: str) -> tuple[list[str], list[str]]:
    failed, errored = [], []
    for line in stdout.splitlines():
        match = _FAILED_LINE.match(line.strip())
        if match:
            (errored if line.strip().startswith("ERROR") else failed).append(match.group(1))
    return failed, errored


def run_mutant(mutant: Mutant) -> Result:
    """Apply one mutant to a throwaway copy and run the cases it names."""
    if not mutant.cases:
        return Result(Outcome.misdeclared, "declares no case")
    with tempfile.TemporaryDirectory(prefix=f"w-mutant-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        shutil.copytree(API_DIR / PACKAGE, root / PACKAGE,
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(API_DIR / "tests", root / "tests",
                        ignore=shutil.ignore_patterns("__pycache__"))
        # the committed pytest configuration, so the copy collects exactly as the
        # worktree does (importlib mode, `pythonpath=["."]`)
        shutil.copy2(API_DIR / "pyproject.toml", root / "pyproject.toml")
        target = root / mutant.path
        source = target.read_text()
        if mutant.old not in source:
            return Result(Outcome.misdeclared,
                          f"anchor not found in {mutant.file}: {mutant.old[:60]!r}")
        target.write_text(source.replace(mutant.old, mutant.new, 1))
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
             "-rf", "--tb=no", *SUITE, "-k", " or ".join(mutant.cases)],
            cwd=root, capture_output=True, text=True,
            env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"})
        stdout = done.stdout or ""
        lines = (stdout or done.stderr).strip().splitlines()
        summary = lines[-1] if lines else "no output"
        if done.returncode not in (PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED):
            return Result(Outcome.broken_runner, f"pytest exit {done.returncode}: {summary}")
        ran = re.search(r"(\d+) (?:passed|failed|skipped)", summary)
        if not ran or "no tests ran" in summary:
            return Result(Outcome.misdeclared, f"no case matched: {summary}")
        failed, errored = _failing_ids(stdout)
        if errored:
            return Result(Outcome.broken_runner, f"errors outside the named cases: {errored[:3]}")
        if done.returncode == PYTEST_ALL_PASSED or not failed:
            return Result(Outcome.survived, summary)
        stray = [test_id for test_id in failed
                 if not any(test_id.endswith(case) for case in mutant.cases)]
        if stray:
            return Result(Outcome.broken_runner, f"failures outside the named cases: {stray[:3]}")
        return Result(Outcome.killed, summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="run the W1 adapter's mutation list")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
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
