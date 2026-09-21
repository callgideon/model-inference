#!/usr/bin/env python3
"""r1 R32 for track G: one single-edit defect per invariant this suite claims.

Same contract as `tests/contracts/mutants.py`, which is where `Mutant`, `Outcome`
and `Result` come from - the list is G's, the vocabulary is shared. The runner is
G's own because it runs G's cases: one mutant at a time, applied to a **copy** of
the package in a temporary directory, with the named cases run there. A mutant that
survives means the case claiming that invariant proves nothing.

    uv run --frozen pytest -q tests/g/test_mutants.py     # the whole list
    uv run --frozen python tests/g/mutants.py --list
    uv run --frozen python tests/g/mutants.py body_cap_removed
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

API_DIR = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = "infrx"
SUITE = "tests/g"


def _shared():
    """`Mutant`/`Outcome`/`Result`/`_failing_ids` from the contracts list.

    Loaded by path rather than imported as `tests.contracts.mutants`: under
    `--import-mode=importlib` the `tests` package is synthesised by pytest and has no
    `__path__`, so a cross-directory import is not reliably available.
    """
    path = API_DIR / "tests" / "contracts" / "mutants.py"
    spec = importlib.util.spec_from_file_location("g_shared_mutants", path)
    module = importlib.util.module_from_spec(spec)
    # `dataclass` resolves annotations through `sys.modules[cls.__module__]`, so the
    # module has to be registered before it is executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_SHARED = _shared()
Mutant, Outcome, Result = _SHARED.Mutant, _SHARED.Outcome, _SHARED.Result

I = "gateway/routes/intake.py"
V = "gateway/routes/validate.py"
N = "gateway/routes/ingress.py"
A = "auth/context.py"
K = "auth/keys.py"                      # F1's caches: G owns the file, and the bounds


def _m(name, invariant, file, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- intake: bounded and deadlined before the parse -----------------------
    _m("body_cap_removed", "the body is bounded before it is parsed",
       I, "                if total > max_bytes:", "                if False:",
       "test_media_sec__an_oversized_body_is_refused_before_it_is_parsed",
       "test_media_sec__a_chunked_body_is_bounded_by_the_running_total"),
    _m("slow_body_deadline_removed", "a slow body cannot outlast INTAKE_TIMEOUT_S",
       I, "                if clock() > deadline:", "                if False:",
       "test_media_sec__a_slow_body_hits_the_intake_deadline"),
    _m("stalled_peer_deadline_removed", "a peer that sends nothing is still deadlined",
       I, "        async with asyncio.timeout(timeout_s):", "        async with asyncio.timeout(None):",
       "test_media_sec__a_peer_that_sends_nothing_hits_the_intake_deadline"),
    _m("non_object_body_accepted", "the body must be a JSON object",
       I, "    if not isinstance(body, dict):", "    if False:",
       "test_media_sec__a_non_object_body_is_refused"),
    # --- the envelope ---------------------------------------------------------
    _m("error_omits_the_request_id_header", "every answer names its request",
       I, "    headers = {wire.HEADER_INFERENCE_ID: request_id}", "    headers = {}",
       "test_f_base__every_answer_carries_a_freshly_minted_inference_id"),
    _m("retry_after_header_dropped", "429/503 carry retry guidance",
       I, "    if error.retry_after_s is not None:", "    if False:",
       "test_f_base__retry_guidance_rides_with_every_429_and_503"),
    _m("unhandled_exception_text_leaks", "an unexpected exception never reaches the client",
       I, "            except Exception:\n"
          "                log.exception(\"%s %s: unhandled error on request %s\", request.method,\n"
          "                              request.url.path, request_id)\n"
          "                return response(errors.InternalError(), request_id)",
       "            except Exception as leaked:\n"
       "                return JSONResponse({\"error\": {\"message\": str(leaked),\n"
       "                                               \"type\": \"server_error\",\n"
       "                                               \"code\": \"internal_error\"}},\n"
       "                                    status_code=500)",
       "test_f_base__an_unexpected_exception_never_reaches_the_client",
       "test_f_base__every_failure_is_a_fixed_envelope_with_no_leak"),
    # --- identity ------------------------------------------------------------
    _m("anonymous_request_accepted", "no accepted request without tenant identity",
       A, '            raise errors.InvalidApiKey("the request carries no per-organization identity")',
       "            pass",
       "test_dur_rls__an_unconfigured_gateway_accepts_no_request",
       "test_dur_rls__a_configuration_with_no_identity_source_accepts_nothing",
       "test_dur_rls__the_shared_legacy_key_is_not_an_identity"),
    _m("unreachable_identity_source_is_a_401", "an unreachable identity source is retryable",
       A, '            raise errors.DependencyUnavailable("the api_keys identity source is unreachable")',
       '            raise errors.InvalidApiKey("the api_keys identity source is unreachable")',
       "test_dur_rls__an_unusable_key_is_refused_with_a_stable_code",
       "test_dur_rls__a_cached_key_survives_an_unreachable_identity_source"),
    _m("pilot_accepts_a_shared_key", "pilot refuses a shared key and a missing identity source",
       A, "        if missing or forbidden:", "        if False:",
       "test_dur_rls__pilot_refuses_to_build_a_resolver_it_cannot_trust"),
    _m("key_cache_unbounded", "the caller-supplied key hash cannot grow a cache without bound",
       K, "    while len(cache) > cap:", "    while False:",
       "test_dur_rls__the_bounded_key_and_miss_caches_are_preserved"),
    _m("miss_cache_evicts_real_keys", "a flood of misses cannot evict a real key",
       K, "        (self.misses if rows else self.keys).pop(h, None)",
       "        self.keys.pop(h, None)",
       "test_dur_rls__a_flood_of_misses_cannot_evict_a_real_key"),
    _m("legacy_comparison_is_not_constant_time", "the legacy key comparison is timing-safe",
       K, "if s.legacy_key and hmac.compare_digest(token.encode(), s.legacy_key.encode()):",
       "if s.legacy_key and token == s.legacy_key:",
       "test_dur_rls__the_legacy_key_comparison_is_still_constant_time"),
    # --- shape and parameters ------------------------------------------------
    _m("unsupported_parameters_ignored", "the parameter set is closed and explicit",
       V, "            if name in UNSUPPORTED or name not in SUPPORTED:", "            if False:",
       "test_f_base__an_unsupported_parameter_is_named_and_refused"),
    _m("n_greater_than_one_accepted", "only n=1 is supported",
       V, "        if count is not None and count != 1:", "        if False:",
       "test_f_base__only_n_equals_one_is_supported"),
    _m("output_ceiling_unchecked", "the output ceiling is 1..MAX_OUTPUT_TOKENS",
       V, "        if not 1 <= output <= ceiling:", "        if False:",
       "test_f_base__the_output_ceiling_is_range_checked"),
    _m("input_ceiling_ignores_the_output", "the input ceiling is the context limit minus output",
       V, "        return self.limits.max_context_tokens - output, output",
       "        return self.limits.max_context_tokens, output",
       "test_dur_admit__admit_accepts_the_ingress_ceilings_and_derives_the_hold",
       "test_f_base__the_derived_ceilings_and_mode_reach_the_acceptor"),
    _m("deadline_beyond_the_budgets", "the deadline is one the store can keep (R29)",
       V, "                                                 + budgets.generation_s)),",
       "                                                 + budgets.generation_s * 2)),",
       "test_dur_admit__the_ingress_deadline_is_one_the_store_can_keep",
       "test_dur_admit__an_async_request_gets_the_async_queue_budget"),
    _m("prefer_async_ignored", "Prefer: respond-async selects the async queue budget",
       V, '    if "respond-async" in prefer:', "    if False:",
       "test_f_base__the_execution_mode_follows_stream_and_prefer",
       "test_dur_admit__an_async_request_gets_the_async_queue_budget"),
    _m("stream_mode_ignored", "a streaming request is not a sync request",
       V, "    return ExecutionMode.stream if stream else ExecutionMode.sync",
       "    return ExecutionMode.sync",
       "test_f_base__the_execution_mode_follows_stream_and_prefer"),
    _m("empty_messages_accepted", "messages is a non-empty array",
       V, "    if not isinstance(messages, list) or not messages:",
       "    if not isinstance(messages, list):",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    # --- r1 R58: the message allow-list ---------------------------------------
    _m("message_keys_not_an_allow_list", "a message is exactly {role, content} (R58)",
       V, "        if not isinstance(message, dict) or set(message) != MESSAGE_KEYS:",
       "        if not isinstance(message, dict):",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("unknown_role_accepted", "a message names a known role",
       V, '        if message["role"] not in ROLES:', "        if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("non_object_part_accepted", "a content part is an object (R58)",
       V, "            if not isinstance(part, dict):", "            if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("text_part_keys_not_an_allow_list", "a text part is exactly {type, text} (R58)",
       V, '                if set(part) != TEXT_PART_KEYS or not isinstance(part["text"], str):',
       '                if not isinstance(part.get("text"), str):',
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("video_part_keys_not_an_allow_list", "a video part is exactly {type, video_url} (R58)",
       V, "                if set(part) != VIDEO_PART_KEYS:", "                if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("part_type_case_insensitive", "part types are matched exactly (R58)",
       V, "            if kind == TEXT_TYPE:", "            if str(kind).lower() == TEXT_TYPE:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("video_ref_keys_not_an_allow_list", "a video part carries exactly {url} (R58)",
       V, "    if not isinstance(ref, dict) or set(ref) != VIDEO_REF_KEYS:",
       "    if not isinstance(ref, dict):",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("foreign_part_type_accepted", "content parts are text or video only",
       V, '                raise errors.UnsupportedMedia(f"content parts are {TEXT_TYPE} and {VIDEO_TYPE}",\n'
          '                                              param="messages")',
       "                pass",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("media_scheme_unchecked", "a media source is http(s), data: or an upload handle",
       V, "    if not source.lower().startswith(MEDIA_SCHEMES):", "    if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("second_video_accepted", "one video per request",
       V, "    if videos > 1:", "    if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("parts_reordered", "normalized parts keep the caller's order (R58)",
       V, "    return tuple(messages)",
       '    return tuple({**m, "content": list(reversed(m["content"]))}\n'
       '                 if isinstance(m.get("content"), list) else m for m in messages)',
       "test_media_sec__accepted_messages_keep_their_parts_in_order"),
    # --- sampling parameters (types and ranges, so no engine 400 is absorbed) --
    _m("stop_bounds_unchecked", "each stop sequence is bounded and non-empty",
       V, "        if not isinstance(item, str) or not item or len(item) > MAX_STOP_CHARS:",
       "        if False:", "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("presence_penalty_unranged", "presence_penalty is ranged at ingress",
       V, '        _number(body, "presence_penalty", -2.0, 2.0)', "        pass",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("frequency_penalty_untyped", "frequency_penalty is typed at ingress",
       V, '        _number(body, "frequency_penalty", -2.0, 2.0)', "        pass",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("number_range_unchecked", "a sampling range is a range",
       V, "    if not low <= value <= high:", "    if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("idempotency_key_unbounded", "the idempotency key is bounded",
       V, "    if key is not None and (not key or len(key) > MAX_IDEMPOTENCY_KEY_CHARS):",
       "    if False:", "test_f_base__an_over_long_idempotency_key_is_refused"),
    # --- startup, readiness and ordering -------------------------------------
    _m("pilot_starts_unreachable", "pilot refuses to start with an unreachable component",
       N, '    if rt.mode == "pilot" and unavailable:', "    if False:",
       "test_f_base__pilot_refuses_to_start_when_a_component_is_unreachable"),
    _m("missing_probe_counts_as_ok", "a missing probe is not a passing probe",
       N, "            state[name] = OK if probe is not None and probe() else UNAVAILABLE",
       "            state[name] = OK if probe is None or probe() else UNAVAILABLE",
       "test_f_base__pilot_refuses_to_start_when_a_component_is_unreachable",
       "test_f_base__dev_starts_with_unreachable_components_and_says_so"),
    _m("readiness_is_public", "readiness needs a tenant",
       N, "        await ingress.auth.context(request)", "        pass",
       "test_dur_rls__readiness_is_protected"),
    _m("health_explains_components", "public health is generic",
       N, '        return JSONResponse({"status": OK})',
       '        return JSONResponse({"status": OK, "components": component_state(deps.checks)})',
       "test_f_base__public_health_is_generic"),
    _m("parse_before_identity", "identity is checked before the body is parsed",
       N, "        auth = await self.auth.context(request)\n"
          "        body = intake.parse_object(raw)",
       "        body = intake.parse_object(raw)\n"
       "        auth = await self.auth.context(request)",
       "test_dur_rls__identity_is_checked_before_the_body_is_parsed"),
    _m("success_omits_the_inference_id", "a success answer names its request too",
       N, "        accepted.headers.setdefault(wire.HEADER_INFERENCE_ID, request_id)", "        pass",
       "test_f_base__every_answer_carries_a_freshly_minted_inference_id"),
    _m("missing_acceptor_is_not_honest", "nothing is accepted before durable acceptance exists",
       N, "        if deps.accept is None:", "        if False:",
       "test_f_base__every_failure_is_a_fixed_envelope_with_no_leak"),
    # --- the cases the first pass left unkillable ------------------------------
    _m("everything_is_too_large", "a body within the cap is read whole",
       I, "                if total > max_bytes:", "                if total >= 0:",
       "test_media_sec__a_body_within_the_cap_is_read_whole"),
    _m("malformed_json_changes_code", "malformed JSON is a stable invalid_request",
       I, '        raise errors.InvalidRequest("the request body is not valid JSON") from None',
       '        raise errors.UnsupportedParameter("the request body is not valid JSON") from None',
       "test_media_sec__malformed_json_is_a_400_with_no_parser_text"),
    _m("envelope_carries_the_operator_detail", "the envelope carries only contract fields",
       I, '    return JSONResponse(envelope.model_dump(mode="json", exclude_none=True),',
       '    return JSONResponse({**envelope.model_dump(mode="json", exclude_none=True),\n'
       '                         "detail": str(error.detail)},',
       "test_f_base__the_envelope_carries_only_the_contract_fields"),
    _m("upload_handles_refused", "an owned upload handle is a valid media reference",
       V, "    if source.startswith(UPLOAD_PREFIX):\n        return source", "    pass",
       "test_media_sec__an_owned_upload_handle_and_a_public_url_are_both_accepted"),
    _m("disagreeing_output_ceilings_accepted", "max_tokens and max_completion_tokens must agree",
       V, "        if requested is not None and alternative is not None and requested != alternative:",
       "        if False:", "test_f_base__max_tokens_and_max_completion_tokens_must_agree"),
    _m("model_revision_replaced", "the requested model revision passes through untouched",
       V, "            model_revision=model or self.rt.settings.model_id,",
       "            model_revision=self.rt.settings.model_id,",
       "test_f_base__the_derived_ceilings_and_mode_reach_the_acceptor"),
    _m("api_key_is_an_operator", "an API key is a service principal, never an operator",
       A, "API_KEY_ROLE = Role.service", "API_KEY_ROLE = Role.operator",
       "test_dur_rls__a_known_key_becomes_an_auth_context"),
    _m("entitlement_version_not_injectable", "the entitlement version source is injectable",
       A, "        self.entitlement_version = entitlement_version or (lambda org_id: 0)",
       "        self.entitlement_version = lambda org_id: 0",
       "test_dur_rls__the_entitlement_version_source_is_injectable"),
    _m("empty_token_looked_up", "a request with no bearer token is refused, not looked up",
       K, "        if not token:\n            return None, 401",
       "        if False:\n            return None, 401",
       "test_dur_rls__no_bearer_token_is_401"),
    _m("admit_skips_revocation", "admission rechecks revocation on the identity G passes",
       "contracts/fakes/state.py",
       'raise errors.InvalidApiKey(f"key {request.key_id} is revoked")', "pass",
       "test_dur_rls__admission_rechecks_revocation_on_the_identity_we_pass"),
    _m("readiness_hides_the_components", "readiness explains component state",
       N, '        return JSONResponse({"status": OK, "mode": rt.mode, "components": state}, headers=headers)',
       '        return JSONResponse({"status": OK}, headers=headers)',
       "test_f_base__readiness_explains_component_state_to_an_authenticated_caller"),
    _m("probe_exception_escapes", "a probe that raises is unavailable, not a 500",
       N, "        except Exception:\n"
          '            intake.log.exception("readiness probe %s failed", name)\n'
          "            state[name] = UNAVAILABLE",
       "        except Exception:\n            raise",
       "test_f_base__a_readiness_probe_that_raises_is_unavailable_not_a_500",
       "test_f_base__pilot_refuses_to_start_when_a_component_is_unreachable"),
    # These two edit files G does not own, in the temporary copy only: they are the
    # cutover itself, and they say exactly which cases pin today's behaviour.
    _m("composition_root_mounts_the_ingress", "G1 mounts nothing until the cutover",
       "gateway/app.py", "ROUTERS = (health, models, chat)",
       "from . import routes as _r\nROUTERS = (health, models, chat, _r.ingress)",
       "test_f_base__the_composition_root_still_mounts_only_the_legacy_routers"),
    _m("unset_mode_refuses", "an unset INFRX_MODE is still legacy behaviour",
       "config.py", '        return "legacy"',
       '        raise RuntimeMisconfigured(mode, detail="INFRX_MODE must be set")',
       "test_f_base__an_unset_mode_is_still_legacy_behaviour",
       "test_f_base__the_composition_root_still_mounts_only_the_legacy_routers",
       "test_f_base__registering_the_ingress_never_replaces_the_legacy_chat_route"),
)


PYTEST_TESTS_FAILED = 1
PYTEST_ALL_PASSED = 0
# Generous for a handful of cases with no sleeps in them: this exists so a mutant that
# makes a case hang cannot hang the suite, not as a performance budget.
NESTED_TIMEOUT_S = 120


def _pytest(root: pathlib.Path, files, selection: str):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
         "--import-mode=importlib", "-rf", "--tb=no", *files, "-k", selection],
        cwd=root, capture_output=True, text=True, timeout=NESTED_TIMEOUT_S,
        env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"})


def run_mutant(mutant) -> Result:
    """Apply one mutant to a throwaway copy and run the cases it names.

    A kill needs all three: pytest exited 1 (not 2-5, which mean the runner broke,
    and not 0, which means nothing noticed), at least one test failed, and every
    failing id names one of the mutant's own cases. The worktree is never written to.
    """
    if not mutant.cases:
        return Result(Outcome.misdeclared, "declares no case")
    with tempfile.TemporaryDirectory(prefix=f"g1-mutant-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(API_DIR / PACKAGE, root / PACKAGE, ignore=ignore)
        shutil.copytree(API_DIR / "tests", root / "tests", ignore=ignore)
        target = root / PACKAGE / mutant.file
        source = target.read_text()
        if mutant.old not in source:
            return Result(Outcome.misdeclared,
                          f"anchor not found in {mutant.file}: {mutant.old[:60]!r}")
        target.write_text(source.replace(mutant.old, mutant.new, 1))
        selection = " or ".join(mutant.cases)
        # Only the files that define the named cases: collecting the whole suite for
        # every mutant imports FastAPI and the contracts fakes 50 times over, which is
        # most of the wall clock of a full run.
        files = sorted(files_for(mutant.cases))
        if not files:
            return Result(Outcome.misdeclared, f"no file defines any of {list(mutant.cases)}")
        try:
            done = _pytest(root, files, selection)
        except subprocess.TimeoutExpired:
            # A defect that makes a case hang is real, but a hang is not the proof the
            # contract asks for, and a runner that waits forever proves nothing at all.
            return Result(Outcome.broken_runner,
                          f"the named cases did not finish within {NESTED_TIMEOUT_S}s")
        stdout = done.stdout or ""
        lines = (stdout or done.stderr).strip().splitlines()
        summary = lines[-1] if lines else "no output"
        if done.returncode not in (PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED):
            return Result(Outcome.broken_runner, f"pytest exit {done.returncode}: {summary}")
        if not re.search(r"(\d+) (?:passed|failed|skipped)", summary) or "no tests ran" in summary:
            return Result(Outcome.misdeclared, f"no case matched {selection!r}: {summary}")
        failed, errored = _SHARED._failing_ids(stdout)
        if errored:
            return Result(Outcome.broken_runner, f"errors outside the named cases: {errored[:3]}")
        if done.returncode == PYTEST_ALL_PASSED or not failed:
            return Result(Outcome.survived, summary)
        stray = [test_id for test_id in failed
                 if not any(case in test_id for case in mutant.cases)]
        if stray:
            return Result(Outcome.broken_runner, f"failures outside the named cases: {stray[:3]}")
        if "skipped" in summary and not failed:
            return Result(Outcome.misdeclared, f"its cases were skipped: {summary}")
        return Result(Outcome.killed, summary)


def _definitions() -> dict[str, str]:
    """case name -> the suite-relative file that defines it."""
    where = {}
    for path in sorted((API_DIR / SUITE).glob("test_*.py")):
        if path.name == "test_mutants.py":
            continue
        for name in re.findall(r"^def (test_\w+)", path.read_text(), re.M):
            where[name] = f"{SUITE}/{path.name}"
    return where


def files_for(cases) -> set[str]:
    where = _definitions()
    return {where[case] for case in cases if case in where}


def case_names() -> set[str]:
    """Every `test_*` function this suite defines about the ingress.

    `test_mutants.py` is excluded: its cases are claims about this list, not about
    the gateway, so requiring a mutant for them would be circular.
    """
    return set(_definitions())


def main() -> int:
    parser = argparse.ArgumentParser(description="run track G's mutation list")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:42s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over "
              f"{len({case for m in MUTANTS for case in m.cases})} named cases")
        return 0
    bad = {}
    for mutant in (m for m in MUTANTS if not args.names or m.name in args.names):
        result = run_mutant(mutant)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}")
        if not result.killed:
            bad[mutant.name] = result.detail
    print(f"\n{len(bad)} not killed" if bad else "\nall killed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
