#!/usr/bin/env python3
"""r1 R32 for the W adapter: every invariant `tests/w` claims must be killable.

Each entry is one single edit to `infrx/worker/` that breaks one named invariant,
with the cases that must fail because of it. The runner copies the package and the
tests into a temporary directory, applies one mutant there, runs only the named
cases, and fails if the mutant survives. The worktree is never written to.

    uv run --frozen pytest -q tests/w/test_mutants.py            # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_mutants.py
    uv run --frozen python tests/w/mutants.py --list

**A kill must be an assertion.** pytest exits 1, at least one named case fails, every
failure is one of the mutant's own cases, *and* every failure is an `AssertionError` or a
pytest `Failed` (`DID NOT RAISE`). A `NameError` or a `TypeError` from the mutant text is
a broken copy, not a proof - so a mutant whose honest kill is a *different typed failure*
(the adapter raising `EngineIncomplete` where the case expected a stall, say) declares
that exception in `allowed_errors`, and nothing else may die that way. This is the rule
the console runners use, and it is what stops a syntax error from reading as evidence.

ponytail: `tests/contracts/mutants.py` has the same machinery but hardcodes its own
pytest target, and it is not this track's file to parameterise. If a third track needs
it, ask the coordinator to move `Mutant`/`run_mutant` into the contracts package with the
target and the kill rule as arguments, and delete this copy.
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
# The only ways a case may legitimately notice a mutant: its own assertion, or the
# absence of an exception it demanded. A rewritten assert reports as the bare expression
# (`path:12: assert 3 == 4`) rather than as `AssertionError`, so both spellings are the
# same outcome; `Failed` is `pytest.raises` reporting DID NOT RAISE.
KILL_ERRORS = ("AssertionError", "assert", "Failed")

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
    # Exception names, beyond `KILL_ERRORS`, this mutant may die by. Each use is a
    # documented kill mode, never a convenience.
    allowed_errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def path(self) -> pathlib.Path:
        return pathlib.Path(PACKAGE) / self.file


def _m(name, invariant, file, old, new, *cases, allowed_errors=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  allowed_errors=tuple(allowed_errors))


# Case names as constants, so a typo is a `NameError` here rather than a mutant that
# cannot die.
BODY = "test_api_stream__the_body_carries_the_served_name_the_ceiling_and_the_salt"
ALLOW = "test_api_stream__messages_are_rebuilt_from_an_allow_list"
NO_URL = "test_api_stream__no_outbound_body_ever_carries_a_foreign_url"
MEDIA = "test_api_stream__prepared_media_replaces_the_customers_url"
TENANT = "test_api_stream__media_belongs_to_the_requests_tenant"
BUDGET = "test_api_stream__the_video_token_budget_is_f1s_own_function"
SALT = "test_api_stream__the_cache_salt_is_per_tenant_and_per_profile"
OPTIONS = "test_api_stream__unsupported_options_are_refused_explicitly"
CEILING = "test_api_stream__the_output_ceiling_is_validated_and_enforced"
TEXTS = "test_api_stream__deltas_carry_the_visible_and_raw_text"
TAIL = "test_api_stream__the_filters_final_tail_reaches_the_event_stream"
ADAPTER_SPLITS = "test_api_stream__every_chunk_split_reaches_the_customer_through_the_adapter"
EMPTY = "test_api_stream__an_empty_first_delta_is_not_a_token"
USAGE = "test_api_stream__usage_is_authoritative_only_when_the_stream_agrees"
BOUNDS = "test_api_stream__one_event_and_the_whole_output_are_bounded"
JOURNAL = "test_api_stream__an_event_always_fits_the_journal_in_any_script"
FLOOD = "test_api_stream__a_line_that_never_ends_is_bounded_and_still_checked"
BYTES = "test_api_stream__the_splitter_handles_bytes_not_lines"
MIDLINE = "test_api_stream__a_cancel_lands_mid_line"
FINISH = "test_api_stream__a_finish_reason_outside_the_set_is_not_a_success"
DROPPED = "test_api_stream__a_dropped_line_is_never_a_billable_success"
SECOND = "test_api_stream__a_second_choice_is_a_protocol_violation"
REF = "test_api_stream__a_prepared_reference_must_be_one_the_store_could_have_made"
MEASURED = "test_api_stream__an_unmeasured_prompt_or_duration_is_refused"
PINNED = "test_api_stream__the_roles_and_the_timer_boundaries_are_pinned"
JUNK = "test_api_stream__junk_and_stray_payloads_are_survived_not_relayed"
FAILURES = "test_api_stream__transport_engine_and_incomplete_failures_are_distinct"
TYPED = "test_api_stream__every_engine_failure_is_typed"
STALL = "test_api_stream__a_stall_ends_the_stream_and_says_which_bound_it_passed"
DEADLINE = "test_api_stream__the_generation_deadline_is_its_own_outcome"
SLOW = "test_api_stream__a_slow_but_steady_stream_is_not_a_stall"
CANCEL = "test_api_stream__cancellation_closes_the_upstream_stream"
CANCEL_EARLY = "test_api_stream__a_request_cancelled_before_it_starts_is_never_sent"
CANCEL_SCOPE = "test_api_stream__a_cancellation_is_scoped_to_its_generation"
LIFECYCLE = ("test_api_stream__a_cancel_intent_is_never_immortal_and_never_"
             "refuses_a_running_lease")
HEALTH = "test_f_contract__health_drain_and_the_capability_probe"
SPLITS = "test_api_stream__every_chunk_split_filters_to_the_same_text"
LEADING = "test_api_stream__only_a_leading_block_is_a_delimiter"
UNCLOSED = "test_api_stream__an_unclosed_block_never_becomes_visible"
PARTIAL = "test_api_stream__a_partial_delimiter_at_the_end_is_released"
STRADDLE = "test_api_stream__the_close_delimiter_may_straddle_any_boundary"

MUTANTS: tuple[Mutant, ...] = (
    # --- the wire body --------------------------------------------------------
    _m("body_sends_the_model_revision", "vLLM is asked for its served name",
       E, '"model": self.served_model,          # the engine\'s served name, not the revision',
       '"model": prepared.model_revision,', BODY),
    _m("ceiling_not_sent", "the engine is given the request's ceiling",
       E, '"max_tokens": ceiling,', '"max_tokens": self.limits.max_output_tokens,',
       BODY, CEILING),
    _m("usage_not_requested", "usage is requested, or the settlement is unknown",
       E, '"stream_options": {"include_usage": True},', '"stream_options": {},', BODY),
    # --- r1 R58: the message allow-list ---------------------------------------
    _m("unknown_parts_forwarded", "a content part is rebuilt, never forwarded (R58)",
       E, '        raise errors.UnsupportedParameter(\n'
          '            f"{kind!r} content parts are not supported; the pilot is text and video",\n'
          '            param="messages")',
       "        return dict(part), consumed", ALLOW, NO_URL),
    _m("role_allowlist_dropped", "only system, user and assistant exist (R58)",
       E, "            if role not in ALLOWED_ROLES:", "            if False:", ALLOW,
       allowed_errors=("EngineFailure",)),
    _m("message_extra_keys_allowed", "a message is exactly {role, content} (R58)",
       E, '            extra = sorted(set(message) - {"role", "content"})',
       "            extra = []", ALLOW),
    _m("text_part_extra_keys_allowed", "a text part is exactly {type, text} (R58)",
       E, '            if set(part) != {"type", "text"} or not isinstance(part["text"], str):',
       '            if not isinstance(part.get("text"), str):', ALLOW),
    _m("video_part_extra_keys_allowed", "a video part is exactly {type, video_url} (R58)",
       E, '            if set(part) != {"type", "video_url"}:', "            if False:", ALLOW),
    _m("content_shape_unchecked", "content is text or a list of parts (R58)",
       E, "            if not isinstance(content, list):", "            if False:", ALLOW,
       allowed_errors=("EngineFailure",)),
    _m("non_object_part_accepted", "a content part is an object (R58)",
       E, "        if not isinstance(part, dict):", "        if False:", ALLOW,
       allowed_errors=("EngineFailure",)),
    _m("media_parts_not_counted", "prepared refs and media parts must agree",
       E, "        if consumed != len(refs):", "        if False:", MEDIA),
    _m("two_videos_accepted", "one video per request",
       E, "        if len(videos) > 1:", "        if False:", MEDIA),
    _m("non_video_media_accepted", "the pilot accepts video media only (R58)",
       E, "        if len(videos) != len(prepared.media):", "        if False:", MEDIA),
    _m("part_kind_not_matched", "a part's kind matches its ref's MIME class (R58)",
       E, "            if not ref.mime.startswith(VIDEO_MIME_PREFIX):", "            if False:",
       MEDIA),
    _m("refs_org_not_checked", "the refs a worker executes are the request's own (R10)",
       E, "        if ref.org_id != request.org_id:", "        if False:", TENANT),
    _m("mixed_org_media_accepted", "one request, one tenant (R10)",
       E, "        if len({ref.org_id for ref in prepared.media}) > 1:", "        if False:",
       TENANT),
    _m("frame_budget_rewritten", "the video budget is F1's own function",
       E, '            body["mm_processor_kwargs"] = self._media.budget_kwargs(videos[0].duration_s)',
       '            body["mm_processor_kwargs"] = {"fps": 2.0, "min_frames": 4,\n'
       '                                          "max_frames": 240,\n'
       '                                          "size": {"shortest_edge": 4096,\n'
       '                                                   "longest_edge": 200704}}',
       BUDGET),
    # --- tenant namespacing ---------------------------------------------------
    _m("salt_ignores_the_tenant", "the cache salt is per tenant",
       E, "    parts = [tenant, prepared.profile_version, prepared.model_revision,",
       "    parts = [prepared.profile_version, prepared.model_revision,", SALT),
    _m("salt_shared_when_the_tenant_is_unknown", "an unknown tenant shares with nobody",
       E, '        tenant = "|".join(orgs) if orgs else prepared.request_id',
       '        tenant = "|".join(orgs) if orgs else "shared"', SALT),
    _m("salt_ignores_the_profile_version", "the profile version namespaces the cache",
       E, "    parts = [tenant, prepared.profile_version, prepared.model_revision,",
       "    parts = [tenant, prepared.model_revision,", SALT),
    _m("salt_ignores_the_media_digest", "the tenant's source digest namespaces the cache",
       E, '             *sorted(f"{ref.digest}@{ref.profile_version}" for ref in prepared.media)]',
       "             ]", SALT),
    _m("customer_salt_honoured", "a client cannot name another tenant's namespace",
       E, '    parameters["tenant_salt"] = request.org_id',
       '    parameters.setdefault("tenant_salt", request.org_id)', SALT),
    _m("media_uuid_unsalted", "multimodal uuids share the prefix cache's namespace",
       E, '    return hashlib.sha256(f"{salt}\\x1f{ref.digest}".encode()).hexdigest()[:32]',
       "    return hashlib.sha256(ref.digest.encode()).hexdigest()[:32]", SALT),
    _m("media_uuid_ignores_the_digest", "two objects in one tenant are two cache entries",
       E, '    return hashlib.sha256(f"{salt}\\x1f{ref.digest}".encode()).hexdigest()[:32]',
       '    return hashlib.sha256(f"{salt}\\x1f{ref.profile_version}".encode()).hexdigest()[:32]',
       SALT),
    # --- refused options ------------------------------------------------------
    _m("unknown_parameters_forwarded", "an unsupported option is refused explicitly",
       E, "            if name in REFUSED_PARAMETERS or name not in PASSTHROUGH_PARAMETERS:\n"
          '                raise errors.UnsupportedParameter(f"{name} is not supported", param=name)',
       "            if False:\n"
          '                raise errors.UnsupportedParameter(f"{name} is not supported", param=name)',
       OPTIONS),
    _m("n_above_one_accepted", "n>1 is refused",
       E, "                if value != 1:", "                if False:", OPTIONS),
    # --- ceilings and the context --------------------------------------------
    _m("ceiling_lower_bound_dropped", "a zero ceiling is not a valid request",
       E, "        if not 1 <= ceiling <= self.limits.max_output_tokens:",
       "        if ceiling > self.limits.max_output_tokens:", CEILING),
    _m("ceiling_upper_bound_dropped", "the ceiling has an upper bound",
       E, "        if not 1 <= ceiling <= self.limits.max_output_tokens:",
       "        if not 1 <= ceiling:", CEILING),
    _m("context_unchecked", "the prompt plus the ceiling must fit the context",
       E, "        if total > self.limits.max_context_tokens:", "        if False:", CEILING),
    _m("context_boundary_ge", "a request that exactly fits the context is accepted",
       E, "        if total > self.limits.max_context_tokens:",
       "        if total >= self.limits.max_context_tokens:", CEILING),
    _m("deltas_past_the_ceiling_accepted", "more deltas than the ceiling is a violation",
       E, "        if stream.deltas > ceiling:", "        if False:", CEILING),
    _m("usage_past_the_ceiling_accepted", "usage beyond the envelope is a platform failure",
       E, "        if stream.usage_candidate.completion_tokens > ceiling:", "        if False:",
       CEILING),
    _m("usage_ceiling_boundary", "an answer exactly at the ceiling is legitimate",
       E, "        if stream.usage_candidate.completion_tokens > ceiling:",
       "        if stream.usage_candidate.completion_tokens >= ceiling:", CEILING),
    # --- deltas ---------------------------------------------------------------
    _m("empty_deltas_counted", "an empty delta is liveness, not a token",
       E, '        if content is None or content == "":', "        if content is None:", EMPTY),
    _m("surrogate_emitted", "every emitted event must serialise",
       E, "        if not _encodable(content):", "        if False:", TYPED),
    _m("event_bytes_unbounded", "one event fits the journal (R58)",
       E, "        for raw_piece, visible_piece in zip_longest(_split_encoded(raw, budget),\n"
          "                                                    _split_encoded(visible, budget),\n"
          "                                                    fillvalue=\"\"):",
       "        for raw_piece, visible_piece in ((raw, visible),):", BOUNDS, JOURNAL),
    _m("event_bytes_sized_for_ascii", "an event is sized in bytes, not code points (R58)",
       E, "PAYLOAD_COPIES_DIVISOR = 4", "PAYLOAD_COPIES_DIVISOR = 2", JOURNAL),
    _m("json_escape_cost_ignored", "a control character costs six bytes in JSON",
       E, '        cost = len(json.dumps(char, ensure_ascii=False).encode()) - 2     # minus the quotes',
       "        cost = 1", JOURNAL),
    _m("visible_never_split", "visible is split on its own account (R58)",
       E, "        for raw_piece, visible_piece in zip_longest(_split_encoded(raw, budget),\n"
          "                                                    _split_encoded(visible, budget),\n"
          "                                                    fillvalue=\"\"):",
       "        for raw_piece, visible_piece in zip_longest(_split_encoded(raw, budget),\n"
          "                                                    [visible],\n"
          "                                                    fillvalue=\"\"):",
       JOURNAL),
    _m("held_tail_never_split", "the held tail is split like any other delta (R58)",
       E, "            for event in self._delta_events(stream, \"\", stream.held_tail):\n"
          "                yield event",
       "            stream.events += 1\n"
          "            yield EngineEvent(type=ChunkEventType.delta,\n"
          '                              payload=_delta_payload("", stream.held_tail))',
       JOURNAL),
    _m("pending_line_unbounded", "an unterminated SSE line is bounded (R58/B7)",
       E, "            if len(pending) > self.pending_cap():", "            if False:", FLOOD),
    _m("pending_cap_is_a_whole_stream", "the pending cap is one journal event, not a stream",
       E, "        return max(4096, self.limits.journal_event_max_bytes)",
       "        return 512 * 1024 * 1024", FLOOD),
    _m("chunk_checks_skipped", "the deadline is checked once per chunk, not per line (B7)",
       E, "            now = self.clock.now()\n"
          "            stall = self._overdue(stream, now)\n"
          "            if stall is not None:\n"
          "                stream.stall = stall\n                return",
       "            now = self.clock.now()\n"
          "            if False:\n                stream.stall = None\n                return",
       FLOOD, STALL),
    _m("chunk_cancel_check_skipped", "cancellation is checked once per chunk (B7)",
       E, "            if key in self.cancelled:\n"
          "                stream.cancelled = True\n                return",
       "            if False:\n"
          "                stream.cancelled = True\n                return",
       CANCEL),
    _m("decoder_not_incremental", "a code point split across chunks survives (B7)",
       E, "            pending += decoder.decode(chunk)",
       '            pending += chunk.decode("utf-8", "replace")', BYTES),
    _m("trailing_line_dropped", "a last line with no newline is still an event",
       E, "        if pending.strip():", "        if False:", BYTES),
    _m("pending_cap_always_4096", "the cap is one journal event, not one buffer",
       E, "        return max(4096, self.limits.journal_event_max_bytes)", "        return 4096",
       BYTES),
    _m("lines_split_per_line", "one split pass per chunk, not one per line (B7)",
       E, "                stream.split_passes += 1\n"
          '                lines = pending.split("\\n")\n'
          "                pending = lines.pop()\n"
          "                for line in lines:\n"
          '                    yield line.rstrip("\\r"), now',
       "                while True:\n"
          "                    stream.split_passes += 1\n"
          '                    line, separator, rest = pending.partition("\\n")\n'
          "                    if not separator:\n                        break\n"
          "                    pending = rest\n"
          '                    yield line.rstrip("\\r"), now',
       BYTES),
    _m("cancel_checked_only_with_newline", "the cancel check runs per chunk, not per line",
       E, "            if key in self.cancelled:\n"
          "                stream.cancelled = True\n                return",
       '            if key in self.cancelled and "\\n" in pending:\n'
          "                stream.cancelled = True\n                return",
       MIDLINE),
    _m("unplaceable_line_not_counted", "a line we cannot place is counted (BOM)",
       E, "            if line and not line.startswith(\":\") and not line.startswith(SSE_FIELDS):\n"
          "                # A line we cannot place - a BOM before `data:`, a truncated field name - is\n"
          "                # content we may be dropping, so it is counted and can no longer end the\n"
          "                # stream `completed` (R21). A comment (`:`) and a blank separator are not.\n"
          "                stream.malformed_lines += 1",
       "            if False:\n                stream.malformed_lines += 1", JUNK),
    _m("output_bytes_unbounded", "the accumulated output is bounded (R58)",
       E, "        if len(stream.raw_text) + len(content) > budget:", "        if False:", BOUNDS),
    _m("visible_and_raw_collapsed", "visible is filtered, raw is not (R58)",
       E, '    return {"visible": visible, "raw": raw, "content": raw}',
       '    return {"visible": raw, "raw": raw, "content": raw}', TEXTS, ADAPTER_SPLITS),
    _m("held_tail_never_emitted", "the filter's final tail reaches the events (R58)",
       E, "        if stream.held_tail:", "        if False:", TAIL, ADAPTER_SPLITS),
    # --- usage (r1 R58) -------------------------------------------------------
    _m("first_usage_wins", "the authoritative usage is the last one (R58)",
       E, '        if obj.get("usage") is not None:\n'
          '            self._note_usage(stream, obj["usage"])',
       '        if obj.get("usage") is not None and stream.usage_objects == 0:\n'
          '            self._note_usage(stream, obj["usage"])',
       USAGE),
    _m("conflicting_usage_trusted", "conflicting usage objects are not a count (R58)",
       E, '        elif len(stream.usage_candidates) > 1:\n            reason = "conflicting"',
       '        elif False:\n            reason = "conflicting"', USAGE),
    _m("usage_after_delta_trusted", "a count arriving before the last delta is not the count",
       E, "        elif stream.deltas > stream.usage_at_deltas:\n"
          '            reason = "delta_after_usage"',
       '        elif False:\n            reason = "delta_after_usage"', USAGE),
    _m("usage_below_the_delta_count_trusted", "a count below the deltas is not the count",
       E, "        elif stream.usage_candidate.completion_tokens < stream.deltas:\n"
          '            reason = "below_delta_count"',
       '        elif False:\n            reason = "below_delta_count"', USAGE),
    _m("unknown_usage_estimated_from_deltas", "output chunks never become a token count",
       E, "        if reason is not None:\n            return [stream.usage_event(None, reason)]",
       "        if reason is not None:\n"
       "            return [stream.usage_event(\n"
       "                Usage.of(stream.prepared.prompt_tokens, stream.deltas), reason)]",
       USAGE),
    # `malformed_usage_becomes_a_zero_count` was removed rather than forced: the flag and
    # the absent candidate are the same fact (`_final_usage` treats either as malformed),
    # so no single edit to it changes the outcome. "A usage we cannot read is unknown"
    # stays covered by `usage_booleans_trusted`, `usage_nonints_trusted`,
    # `usage_nondict_trusted` and `usage_totals_not_checked`.
    _m("malformed_flag_ignored", "any malformed usage object makes it unknown (R58)",
       E, "        if stream.malformed_usage or stream.usage_candidate is None:",
       "        if stream.usage_candidate is None:", USAGE),
    _m("usage_booleans_trusted", "True is not a token count",
       E, "        if isinstance(value, bool) or not isinstance(value, int) or value < 0:",
       "        if not isinstance(value, int) or value < 0:", USAGE),
    _m("usage_nonints_trusted", "a count that is not a nonnegative integer is not a count",
       E, "        if isinstance(value, bool) or not isinstance(value, int) or value < 0:",
       "        if value is None:", USAGE, allowed_errors=("EngineFailure",)),
    _m("usage_nondict_trusted", "a usage object is an object",
       E, "    if not isinstance(raw, dict):\n        return None",
       "    if False:\n        return None", USAGE, allowed_errors=("EngineFailure",)),
    _m("usage_totals_not_checked", "a usage object that does not add up is unknown",
       E, "    if total is not None and (isinstance(total, bool) or not isinstance(total, int)\n"
          "                             or total != prompt + completion):",
       "    if total is not None and (isinstance(total, bool) or not isinstance(total, int)):",
       USAGE),
    _m("completed_without_a_count", "a finished stream with unknown usage is not a success",
       E, "        if self.finish_reason in FINISHED_REASONS and self.usage is not None \\\n"
          "                and self.malformed_lines == 0:",
       "        if self.finish_reason in FINISHED_REASONS and self.malformed_lines == 0:",
       USAGE),
    _m("completed_without_finish", "completed needs a finish reason as well (R21)",
       E, "        if self.finish_reason in FINISHED_REASONS and self.usage is not None \\\n"
          "                and self.malformed_lines == 0:",
       "        if self.usage is not None and self.malformed_lines == 0:", FINISH),
    _m("abort_is_finished", "abort is not a finish reason we accept",
       E, 'FINISHED_REASONS = ("stop", "length")', 'FINISHED_REASONS = ("stop", "length", "abort")',
       FINISH),
    # --- failure classes ------------------------------------------------------
    _m("pre_header_status_ignored", "a non-200 is an engine error, not a stream",
       E, "                if response.status_code != 200:", "                if False:",
       FAILURES),
    _m("in_stream_error_swallowed", "an error object in the stream is an engine error",
       E, '        if obj.get("error") is not None:', "        if False:", TYPED),
    _m("detail_not_coerced", "an engine's detail may be any JSON value",
       E, "        self.detail = str(detail)[:DETAIL_MAX_CHARS]",
       "        self.detail = detail[:DETAIL_MAX_CHARS]", TYPED),
    _m("detail_unbounded", "operator detail is bounded",
       E, "        self.detail = str(detail)[:DETAIL_MAX_CHARS]",
       "        self.detail = str(detail)", FAILURES),
    _m("error_body_read_whole", "an engine's error body is read bounded",
       E, "            if len(body) >= ERROR_BODY_MAX_BYTES:\n                break",
       "            if False:\n                break", FAILURES),
    _m("choices_shape_unchecked", "choices carries the answer, so its shape is checked",
       E, "            if not isinstance(choices, list):", "            if False:", TYPED),
    _m("choice_shape_unchecked", "a choice is an object",
       E, "        if not isinstance(choice, dict):", "        if False:", TYPED),
    _m("delta_shape_unchecked", "a delta is an object",
       E, "        if not isinstance(delta, dict):", "        if False:", TYPED),
    _m("content_shape_trusted", "delta content is text",
       E, "        if not isinstance(content, str):", "        if False:", TYPED),
    _m("unexpected_exception_escapes", "nothing untyped leaves generate (R58)",
       E, "        except Exception as failure:\n"
          "            # r1 R58: nothing untyped leaves the port. W2 has to settle a cause, and an\n"
          "            # unknown exception is not one.\n"
          '            raise EngineFailure(f"{type(failure).__name__}: {failure}", stage="adapter") from None',
       "        except EngineFailure:\n            raise",
       TYPED, allowed_errors=("OSError",)),
    _m("incomplete_looks_complete", "output that just stopped is not a clean answer",
       E, "        if not stream.complete and stream.stall is None and stream.finish_reason is None \\\n"
          "                and not stream.cancelled:",
       "        if False:", FAILURES),
    _m("transport_failure_hidden", "a dead connection is a transport failure",
       E, "        except httpx.HTTPError as failure:\n"
          '            raise EngineTransportError(f"{type(failure).__name__}: {failure}",\n'
          '                                       stage="stream" if stream.started else "pre_headers") from None',
       "        except httpx.HTTPError as failure:\n"
          '            stream.stall = "inter_event"',
       FAILURES),
    _m("pre_header_timeout_is_stall", "a timeout before the headers is a transport failure",
       E, "            if not stream.started:", "            if False:", FAILURES),
    _m("post_header_timeout_is_a_failure", "silence after the headers is a stall",
       E, "            if not stream.started:", "            if True:", STALL),
    _m("junk_relayed_as_content", "a line that is not JSON is never relayed",
       E, "        except ValueError:\n            stream.malformed_lines += 1\n            return []",
       "        except ValueError:\n            stream.malformed_lines += 1\n"
       "            return [EngineEvent(type=ChunkEventType.delta,\n"
       "                                payload=_delta_payload(payload, payload))]",
       JUNK),
    _m("non_object_payload_not_counted", "junk is counted, so a dashboard can see it",
       E, "        if not isinstance(obj, dict):\n            stream.malformed_lines += 1\n"
          "            return []",
       "        if not isinstance(obj, dict):\n            return []", JUNK),
    # --- timers ---------------------------------------------------------------
    # `stall_never_stops_the_stream` was retired in the B7 pass: the check moved into
    # `_lines`, where `chunk_checks_skipped` kills it, and losing only the *reason* there is
    # unrepresentable because the post-loop `_overdue` re-derives it (measured: the mutant
    # survives). Keeping it would be an unkillable mutant, and weakening the post-loop
    # derivation to kill it would be the false kill r1 R40 forbids.
    _m("first_token_deadline_ignored", "the store's first-token instant binds the worker",
       E, "        if stream.deltas == 0 and lease.first_token_deadline_at is not None \\\n"
          "                and now >= lease.first_token_deadline_at:",
       "        if False:", STALL),
    _m("inter_event_budget_ignored", "the inter-event budget binds the worker",
       E, "        if stream.last_event_at is not None and stream.deltas > 0 \\\n"
          "                and now >= stream.last_event_at + timedelta(seconds=self.limits.tpot_stall_s):",
       "        if False:", STALL),
    _m("last_event_not_updated", "the inter-event budget is measured from the last event",
       E, "        stream.last_event_at = now", "        pass", SLOW),
    _m("keepalives_count_as_progress", "a keepalive is not an event",
       E, '        if not line.startswith("data:"):\n'
          "            return []                                    # blank separator, or a `:` keepalive",
       '        if not line.startswith("data:"):\n'
          "            stream.last_event_at = now\n            return []",
       STALL),
    _m("generation_deadline_ignored", "the attempt's absolute deadline ends it (R20)",
       E, '        if now >= lease.generation_deadline_at:\n            return "generation"',
       '        if False:\n            return "generation"', DEADLINE),
    _m("generation_stall_cause", "the platform's own deadline is deadline_exceeded (R21)",
       E, '        if self.stall == "generation":\n'
          "            return TerminalCause.deadline_exceeded",
       "        if False:\n            return TerminalCause.deadline_exceeded", DEADLINE),
    # --- cancellation ---------------------------------------------------------
    # `cancellation_ignored_mid_stream` moved with B7: the check lives in `_lines` now and
    # `chunk_cancel_check_skipped` is the same defect at its new site, so keeping both would
    # be one mutant that cannot find its anchor.

    _m("cancelled_request_still_sent", "a cancelled request is never sent",
       E, "        if key in self.cancelled:\n"
          "            # Cancelled before the request was sent: nothing ran, so zero tokens is the",
       "        if False:\n"
          "            # Cancelled before the request was sent: nothing ran, so zero tokens is the",
       CANCEL_EARLY),
    _m("cancelled_usage_estimated", "a truncated stream leaves the usage unknown",
       E, '                return [stream.usage_event(None, "cancelled")]',
       "                return [stream.usage_event(\n"
       "                    Usage.of(stream.prepared.prompt_tokens, stream.deltas), None)]",
       CANCEL),
    _m("cancel_key_ignores_the_generation", "an intent is scoped to its generation (R58)",
       E, "        key = (lease.job_id, lease.generation)", "        key = (lease.job_id, 1)",
       CANCEL_SCOPE),
    _m("cancel_intents_unbounded", "intents for work that never runs are bounded (R58)",
       E, "        if len(self.cancelled) >= MAX_CANCEL_INTENTS:\n"
          "            self._evict_spent()",
       "        if False:\n            self._evict_spent()", LIFECYCLE),
    _m("finished_cancel_is_remembered", "a cancel for a finished generation stores nothing",
       E, "        if key in self.finished:\n"
          "            # (1) The generation is over: there is nothing to stop, and remembering the\n"
          "            # intent for ever is what made an ordinary late cancel immortal.\n"
          "            return True",
       "        if False:\n            return True", LIFECYCLE),
    _m("running_lease_refused", "a running generation is never refused a cancel",
       E, "        if key in self.running:", "        if False:", LIFECYCLE),
    _m("running_not_registered", "a generation in flight is known to be running",
       E, "        self.running.add(key)", "        pass", LIFECYCLE),
    _m("pre_start_intents_immortal", "a pre-start intent expires with its lease",
       E, "            if held in self.finished or now >= expires_at:",
       "            if held in self.finished:", LIFECYCLE),
    _m("expiry_ignores_the_clock", "the expiry is the lease's own deadline",
       E, "            if held in self.finished or now >= expires_at:",
       "            if held in self.finished or True:", LIFECYCLE),
    _m("eviction_takes_a_running_intent", "eviction never touches a running generation",
       E, "            if held in self.running:\n                continue",
       "            if False:\n                continue", LIFECYCLE),
    _m("aclose_does_not_retire", "closing retires the generation (clause 2)",
       E, "        try:\n            await self._iterator.aclose()\n        finally:\n"
          "            self._engine._retire(self._key)",
       "        await self._iterator.aclose()", LIFECYCLE),
    _m("finished_never_remembered", "a finished generation is remembered",
       E, "        self.finished[key] = True", "        pass", LIFECYCLE),
    _m("finished_unbounded", "the finished map is a bounded FIFO",
       E, "        while len(self.finished) > MAX_CANCEL_INTENTS:", "        while False:",
       LIFECYCLE),
    _m("intent_outlives_the_stream", "an intent is cleared on every exit path (R58)",
       E, "            self.cancelled.pop(key, None)\n            self._remember_finished(key)",
       "            self._remember_finished(key)", CANCEL),
    # --- readiness ------------------------------------------------------------
    _m("drain_is_not_observable", "drain stops reporting ready",
       E, '        if self.drained:\n            return {"ready": False, "drained": True,',
       '        if False:\n            return {"ready": False, "drained": True,', HEALTH),
    _m("health_ignores_the_engine", "an engine that answers badly is not ready",
       E, "            ready = response.status_code == 200", "            ready = True", HEALTH),
    _m("health_unreachable_ready", "an engine that does not answer is not ready",
       E, '            return {"ready": False, "drained": False, "served_model": self.served_model,\n'
          '                    "detail": type(failure).__name__}',
       '            return {"ready": True, "drained": False, "served_model": self.served_model,\n'
          '                    "detail": type(failure).__name__}', HEALTH),
    _m("capability_probe_accepts_any_model", "the pinned engine must serve the model",
       E, "        if self.served_model not in models:", "        if False:", HEALTH),
    _m("capability_probe_accepts_any_version", "the version pin is checked",
       E, "        if self.require_version is not None and version != self.require_version:",
       "        if False:", HEALTH),
    # --- the round-2 same-pass rulings ----------------------------------------
    _m("dropped_line_still_completes", "a dropped line is not a billable success (R21)",
       E, "        if self.finish_reason in FINISHED_REASONS and self.usage is not None \\\n"
          "                and self.malformed_lines == 0:",
       "        if self.finish_reason in FINISHED_REASONS and self.usage is not None:",
       DROPPED),
    _m("second_choice_merged", "n=1 is forced, so index 1 cannot exist",
       E, "        if index != 0:", "        if False:", SECOND),
    _m("storage_ref_shape_trusted", "a prepared reference has the store's shape",
       E, "    if matched is None or matched.group(\"org\") != ref.org_id:",
       "    if False:", REF),
    _m("storage_ref_tenant_trusted", "a prepared reference sits under its own tenant",
       E, "    if matched is None or matched.group(\"org\") != ref.org_id:",
       "    if matched is None:", REF),
    _m("storage_ref_unchecked", "every prepared ref is checked before the body is built",
       E, "        for ref in prepared.media:\n            check_storage_ref(ref)",
       "        for ref in ():\n            check_storage_ref(ref)", REF),
    _m("prompt_tokens_unbounded", "a prompt count nothing measured is refused",
       E, "    if not 0 <= prompt_tokens <= limits.max_context_tokens:", "    if False:",
       MEASURED),
    # Declared kill mode: without the guard the adapter *crashes* on a missing duration
    # (`budget_kwargs(None)`), which is the defect - `or 0.0` silently asked for a four-frame
    # budget instead, and either way the case's refusal never happens.
    _m("missing_duration_is_zero", "a prepared video carries its duration",
       E, "            if videos[0].duration_s is None:", "            if False:", MEASURED,
       allowed_errors=("TypeError",)),
    _m("roles_widened", "the role vocabulary is closed (R58)",
       E, 'ALLOWED_ROLES = ("system", "user", "assistant")',
       'ALLOWED_ROLES = ("system", "user", "assistant", "tool")', PINNED, ALLOW),
    _m("first_token_boundary_exclusive", "a deadline is reached at its instant",
       E, "        if stream.deltas == 0 and lease.first_token_deadline_at is not None \\\n"
          "                and now >= lease.first_token_deadline_at:",
       "        if stream.deltas == 0 and lease.first_token_deadline_at is not None \\\n"
          "                and now > lease.first_token_deadline_at:", PINNED),
    _m("stall_boundary_exclusive", "the inter-event budget is reached at its instant",
       E, "                and now >= stream.last_event_at + timedelta(seconds=self.limits.tpot_stall_s):",
       "                and now > stream.last_event_at + timedelta(seconds=self.limits.tpot_stall_s):",
       PINNED),
    _m("generation_boundary_exclusive", "the generation deadline is reached at its instant",
       E, "        if now >= lease.generation_deadline_at:",
       "        if now > lease.generation_deadline_at:", PINNED),
    # --- the reasoning filter -------------------------------------------------
    _m("close_delimiter_tail_forgotten", "a split `</think>` is still recognised",
       R, "            self._tail = buf[-(len(CLOSE) - 1):]", '            self._tail = ""',
       SPLITS, STRADDLE),
    _m("open_delimiter_prefix_forgotten", "a split `<think>` is still recognised",
       R, "        if OPEN.startswith(stripped):          # a proper prefix: it may still complete\n"
          '            self._held = buf\n            return ""',
       '        if False:\n            self._held = buf\n            return ""',
       SPLITS, UNCLOSED),
    _m("reasoning_text_leaks_before_the_close", "reasoning is dropped, not relayed",
       R, '            self._tail = buf[-(len(CLOSE) - 1):]\n            return ""',
       "            self._tail = buf[-(len(CLOSE) - 1):]\n            return buf", SPLITS),
    _m("unclosed_reasoning_leaks", "an unclosed block never becomes visible",
       R, '        held, self._held, self._tail = self._held, "", ""\n'
          "        if self._reasoning:\n"
          '            return ""                          # unclosed reasoning stays reasoning',
       '        held, self._held, tail, self._tail = self._held, "", self._tail, ""\n'
          "        if self._reasoning:\n            return tail",
       UNCLOSED),
    _m("any_occurrence_stripped", "only a leading block is a delimiter",
       R, '        self._held, self._decided = "", True   # it was never a delimiter: emit it whole\n'
          "        return buf",
       '        self._held, self._decided = "", True\n        return buf.replace(OPEN, "")',
       LEADING, SPLITS),
    _m("held_prefix_never_released", "a partial delimiter that never completes is text",
       R, '        held, self._held, self._tail = self._held, "", ""',
       '        held, self._held, self._tail = "", "", ""', PARTIAL, SPLITS),
    _m("whitespace_after_the_close_kept", "the whitespace around the block goes with it",
       R, "        if self._eat_ws:\n            text = text.lstrip()",
       "        if False:\n            text = text.lstrip()", LEADING, SPLITS),
    _m("delimiter_case_insensitive", "`<THINK>` is text, as F1's regex had it",
       R, "        if stripped.startswith(OPEN):", "        if stripped.lower().startswith(OPEN):",
       LEADING, SPLITS),
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
# `--tb=line` prints one line per failure: `path:lineno: ExceptionName: message`.
_REASON_LINE = re.compile(r"^.*?:\d+: ([A-Za-z_][\w.]*)")


def _failing_ids(stdout: str) -> tuple[list[str], list[str]]:
    failed, errored = [], []
    for line in stdout.splitlines():
        match = _FAILED_LINE.match(line.strip())
        if match:
            (errored if line.strip().startswith("ERROR") else failed).append(match.group(1))
    return failed, errored


def _reasons(stdout: str) -> list[str]:
    """The exception name of every reported failure, last dotted component only."""
    names, inside = [], False
    for line in stdout.splitlines():
        if line.startswith("=") and "FAILURES" in line:
            inside = True
            continue
        if line.startswith("=") and "short test summary" in line:
            inside = False
        if inside:
            match = _REASON_LINE.match(line.strip())
            if match:
                names.append(match.group(1).rsplit(".", 1)[-1])
    return names


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
             "-rf", "--tb=line", *SUITE, "-k", " or ".join(mutant.cases)],
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
        reasons = _reasons(stdout)
        if not reasons:
            return Result(Outcome.broken_runner, f"no failure reason reported: {summary}")
        allowed = set(KILL_ERRORS) | set(mutant.allowed_errors)
        undeclared = sorted({name for name in reasons if name not in allowed})
        if undeclared:
            # A runtime error the mutant did not declare is a broken copy, not a proof: the
            # case noticed *something*, but not the invariant it claims.
            return Result(Outcome.broken_runner,
                          f"undeclared failure mode {undeclared}: {summary}")
        return Result(Outcome.killed, f"{summary} via {sorted(set(reasons))}")


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
