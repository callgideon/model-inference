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
       "test_media_sec__a_chunked_body_is_bounded_by_the_running_total",
       "test_media_sec__the_cap_counts_the_whole_stream_not_one_chunk",
       "test_media_sec__the_cap_is_an_upper_bound_not_an_off_by_one"),
    _m("cap_counts_one_chunk", "the cap counts the whole stream, not one chunk",
       I, "                total += len(chunk)", "                total = len(chunk)",
       "test_media_sec__the_cap_counts_the_whole_stream_not_one_chunk"),
    _m("cap_is_off_by_one", "the cap is an upper bound",
       I, "                if total > max_bytes:", "                if total >= max_bytes:",
       "test_media_sec__the_cap_is_an_upper_bound_not_an_off_by_one"),
    # --- review r1 item 1: the parser's failures are ours to classify ----------
    _m("recursion_error_escapes", "deep nesting is a 400, not a 500",
       I, "    except (ValueError, RecursionError):", "    except ValueError:",
       "test_media_sec__a_parser_hostile_body_is_a_400_not_a_500"),
    _m("json_constants_accepted", "NaN and Infinity are not numbers JSON has",
       I, "        body = json.loads(text, parse_constant=_no_constants)",
       "        body = json.loads(text)",
       "test_media_sec__the_parser_itself_refuses_json_that_is_not_json"),
    # --- review r1 item 2: the error path cannot raise -------------------------
    _m("param_echoed_unfiltered", "only a parameter-shaped param is echoed",
       I, "    return param if isinstance(param, str) and SAFE_PARAM.fullmatch(param) else None",
       "    return param if isinstance(param, str) else None",
       "test_f_base__an_unrenderable_param_is_still_an_envelope",
       "test_f_base__a_caller_string_is_never_reflected_or_logged"),
    _m("param_never_echoed", "a parameter name that is one is still named",
       I, "    return param if isinstance(param, str) and SAFE_PARAM.fullmatch(param) else None",
       "    return None", "test_f_base__a_parameter_name_that_is_a_name_is_still_echoed"),
    _m("internal_code_reaches_http_status", "an internal-only code is answered as internal_error",
       I, "        public = error if error.code in errors.HTTP_ERRORS else errors.InternalError()",
       "        public = error",
       "test_f_base__an_internal_only_code_escaping_a_route_is_a_500_envelope"),
    _m("envelope_render_unprotected", "rendering the envelope cannot fail",
       I, "    except Exception:\n"
          '        log.exception("the error envelope could not be rendered for request %s", request_id)',
       "    except KeyboardInterrupt:\n        raise",
       "test_f_base__the_envelope_of_last_resort"),
    # --- review r1 item 3: structure, liveness and identity-first -------------
    _m("messages_unbounded", "the message count is bounded",
       V, "    if len(messages) > MAX_MESSAGES:", "    if False:",
       "test_media_sec__the_message_count_is_bounded"),
    _m("parts_unbounded", "the parts per message are bounded",
       V, "        if len(content) > MAX_PARTS_PER_MESSAGE:", "        if False:",
       "test_media_sec__the_parts_per_message_are_bounded"),
    _m("text_unbounded", "the total text is bounded",
       V, "            if text_chars > MAX_TEXT_CODEPOINTS:\n"
          "                raise errors.InvalidRequest(f\"at most {MAX_TEXT_CODEPOINTS} characters of text\",\n"
          "                                            param=\"messages\")\n"
          "            continue",
       "            continue", "test_media_sec__the_total_text_is_bounded"),
    _m("url_unbounded", "a public url is bounded",
       V, "    if len(source) > MAX_URL_CHARS:", "    if False:",
       "test_media_sec__a_public_url_is_bounded"),
    _m("identity_after_the_body", "identity is resolved before the body is read or parsed",
       N, "        auth = await self.auth.context(request)\n        intake.check_content_type(request)",
       "        intake.check_content_type(request)",
       "test_dur_rls__an_unauthenticated_caller_never_makes_us_buffer",
       "test_dur_rls__identity_is_checked_before_the_body_is_parsed"),
    _m("unstorable_text_accepted", "text the database cannot store is refused",
       V, "    try:\n        text.encode()\n    except UnicodeEncodeError:",
       "    try:\n        text.encode(errors=\"replace\")\n    except UnicodeEncodeError:",
       "test_media_sec__text_the_database_cannot_store_is_refused"),
    _m("nul_accepted", "a NUL cannot be stored and is refused",
       V, '    if "\\x00" in text:', "    if False:",
       "test_media_sec__text_the_database_cannot_store_is_refused"),
    _m("unhashable_role_crashes", "a non-string role is refused before the set lookup",
       V, "        if not isinstance(role, str) or role not in ROLES:", "        if role not in ROLES:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    # --- review r1 item 5: clock skew -----------------------------------------
    _m("no_skew_margin", "the deadline leaves room for store-clock skew (R7)",
       V, "DEADLINE_SKEW_MARGIN_S = 2.0", "DEADLINE_SKEW_MARGIN_S = 0.0",
       "test_dur_admit__the_deadline_survives_clock_skew"),
    _m("skew_margin_added", "the margin is subtracted, not added",
       V, "                                                 - DEADLINE_SKEW_MARGIN_S)),",
       "                                                 + DEADLINE_SKEW_MARGIN_S)),",
       "test_dur_admit__the_deadline_survives_clock_skew",
       "test_f_base__there_is_no_second_clock"),
    # --- review r1 item 6 and the nonblocking list ----------------------------
    _m("null_parameter_is_absent", "a null parameter is refused, not treated as absent",
       V, "            if body[name] is None:", "            if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("seed_unranged", "seed is 0..2**63-1",
       V, "        if seed is not None and not 0 <= seed <= MAX_SEED:", "        if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("model_copied_into_the_revision", "a public model id is mapped, never copied",
       V, "        revision = self.served_models.get(model)",
       "        revision = self.served_models.get(model, model)",
       "test_f_base__an_unserved_model_is_refused_without_naming_what_is_served"),
    _m("model_length_unbounded", "a model name is bounded",
       V, "        if len(model) > MAX_MODEL_CHARS:", "        if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code",
       "test_media_sec__a_megabyte_model_name_never_reaches_the_store"),
    _m("prefer_matched_as_a_substring", "Prefer is parsed as tokens",
       V, '    tokens = {token.strip().lower().split("=")[0]\n'
          '              for token in (headers.get("prefer") or "").split(",")}',
       '    tokens = (headers.get("prefer") or "").lower()',
       "test_f_base__prefer_is_parsed_as_tokens_not_as_a_substring"),
    _m("stream_and_async_both_accepted", "one response shape per request",
       V, "        if stream:\n            # One response shape per request", "        if False:\n            # One response shape per request",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("upload_form_unchecked", "an upload reference is infrx-upload:upl_…",
       V, "        if not ids.UPLOAD_HANDLE_RE.fullmatch(handle):", "        if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("data_url_form_unchecked", "an inline video is data:<mime>;base64,",
       V, "    if match is None:", "    if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("control_characters_in_urls", "a url carries no control characters",
       V, "    if UNSAFE_IN_URL.search(source):", "    if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("content_type_unchecked", "the body is application/json",
       I, "    if declared != JSON_MEDIA_TYPE:", "    if False:",
       "test_f_base__a_body_that_is_not_json_is_refused"),
    _m("digest_over_the_raw_bytes", "the payload digest is canonical",
       V, "    if not media:\n        return \"sha256:\" + hashlib.sha256(canonical_bytes(body)).hexdigest()",
       "    if not media:\n        return \"sha256:\" + hashlib.sha256(repr(body).encode()).hexdigest()",
       "test_dur_admit__the_payload_digest_is_canonical"),
    _m("trace_default_is_full", "the trace policy defaults to off",
       V, "    return ConsentSnapshot(org_id=org_id, consent_version=0, trace_mode=TraceMode.off,",
       "    return ConsentSnapshot(org_id=org_id, consent_version=0, trace_mode=TraceMode.full,",
       "test_trace_tenant__the_trace_policy_defaults_to_off"),
    _m("consent_source_ignored", "an injected consent source is used",
       V, "        self.consent_for = consent_for or off_mode_policy",
       "        self.consent_for = off_mode_policy",
       "test_trace_tenant__an_injected_consent_source_is_used_as_given"),
    _m("a_second_clock", "there is one clock, and it is injected",
       V, "        return datetime.fromtimestamp(self.rt.clock(), timezone.utc)",
       "        return datetime.now(timezone.utc)",
       "test_f_base__there_is_no_second_clock"),
    _m("key_ttl_ignored", "a cached key expires and is looked up again",
       K, "        if hit is None or hit[0] < now():", "        if hit is None:",
       "test_dur_rls__a_cached_key_expires_and_is_looked_up_again",
       "test_dur_rls__a_miss_expires_on_its_own_shorter_ttl",
       "test_dur_rls__a_revocation_takes_effect_when_the_cache_expires"),
    _m("key_id_not_passed_to_the_store", "the key the store rechecks is the caller's key",
       A, "            return AuthContext(org_id=org_id, key_id=key_id, principal=key_id, role=API_KEY_ROLE,",
       "            return AuthContext(org_id=org_id, key_id=org_id, principal=key_id, role=API_KEY_ROLE,",
       "test_dur_rls__admission_rechecks_revocation_on_the_identity_we_pass",
       "test_dur_rls__a_known_key_becomes_an_auth_context"),
    _m("orgless_row_becomes_an_identity", "an identity row names a tenant",
       A, "        if not org_id or not key_id:", "        if False:",
       "test_dur_rls__an_identity_row_without_a_tenant_is_not_an_identity"),
    _m("malformed_row_raises_a_validation_error", "a malformed identity row is a typed failure",
       A, "        except ValueError:", "        except KeyboardInterrupt:",
       "test_dur_rls__a_malformed_identity_row_fails_closed_without_a_trace"),
    # --- review r1 item 7: FastAPI's own answers -------------------------------
    _m("http_exceptions_unwrapped", "404 and a wrong method are envelopes",
       N, "    app.add_exception_handler(StarletteHTTPException, http_exception)", "    pass",
       "test_f_base__an_unknown_path_and_a_wrong_method_are_envelopes"),
    _m("unhandled_exceptions_unwrapped", "an error outside a route is an envelope",
       N, "    app.add_exception_handler(Exception, unhandled)", "    pass",
       "test_f_base__an_unhandled_error_outside_a_route_is_still_an_envelope"),
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
       I, "        if public.retry_after_s is not None:", "        if False:",
       "test_f_base__retry_guidance_rides_with_every_429_and_503"),
    _m("unhandled_exception_text_leaks", "an unexpected exception never reaches the client",
       I, "            except Exception:\n"
          "                log.exception(\"%s: unhandled error on request %s\", request.url.path, request_id)\n"
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
    # Misses live in their own cache with its own cap; sharing one cache is exactly how
    # a flood of random keys evicts the real ones. (An earlier mutant here - swapping the
    # `pop` target - was equivalent: for a miss the original already pops `keys`.)
    _m("one_cache_for_hits_and_misses", "a flood of misses cannot evict a real key",
       K, "        put(*((self.keys, h, hit, s.key_cache_max) if rows else (self.misses, h, hit, s.miss_cache_max)))",
       "        put(self.keys, h, hit, s.key_cache_max)",
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
       V, "                                                 - DEADLINE_SKEW_MARGIN_S)),",
       "                                                 + budgets.generation_s)),",
       "test_dur_admit__the_ingress_deadline_is_one_the_store_can_keep",
       "test_dur_admit__an_async_request_gets_the_async_queue_budget"),
    _m("prefer_async_ignored", "Prefer: respond-async selects the async queue budget",
       V, "    if wire.PREFER_RESPOND_ASYNC in tokens:", "    if False:",
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
       V, "        if not isinstance(role, str) or role not in ROLES:", "        if False:",
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
       V, "    if not source.lower().startswith(HTTP_SCHEMES):", "    if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("second_video_accepted", "one video per request",
       V, "                if videos > MAX_VIDEO_PARTS:", "                if False:",
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
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code",
       "test_media_sec__a_number_json_does_not_have_is_refused"),
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
    _m("success_omits_the_inference_id", "a success answer names its request too",
       N, "        accepted.headers.setdefault(wire.HEADER_INFERENCE_ID, request_id)", "        pass",
       "test_f_base__every_answer_carries_a_freshly_minted_inference_id"),
    _m("missing_acceptor_is_not_honest", "nothing is accepted before durable acceptance exists",
       N, "        if deps.accept is None:", "        if False:",
       "test_f_base__every_failure_is_a_fixed_envelope_with_no_leak"),
    # --- review r2 B1: refuse before parsing, bound concurrency, spare the payload ---
    _m("structure_not_counted_before_the_parse", "hostile structure is refused before parsing",
       I, "    if sum(text.count(opener) for opener in OPENERS) > max_openers:", "    if False:",
       "test_media_sec__hostile_structure_is_refused_without_parsing_it"),
    _m("opener_cap_counts_only_braces", "both openers are counted",
       I, 'OPENERS = ("{", "[")', 'OPENERS = ("{",)',
       "test_media_sec__hostile_structure_is_refused_without_parsing_it"),
    _m("opener_cap_excludes_text", "braces inside text are text",
       V, "MAX_OPENERS = STRUCTURE_OPENERS + MAX_TEXT_CODEPOINTS", "MAX_OPENERS = STRUCTURE_OPENERS",
       "test_media_sec__a_legitimate_body_at_the_opener_cap_is_still_parsed"),
    _m("large_bodies_unbounded", "at most two large bodies are in flight",
       I, "        if self.slots.in_flight >= self.slots.limit:", "        if False:",
       "test_media_sec__at_most_two_large_bodies_are_in_flight"),
    _m("large_body_slot_never_claimed", "a large body claims a slot",
       I, "        if self.held or total <= self.slots.threshold:",
       "        if self.held or total <= 2 ** 40:",
       "test_media_sec__at_most_two_large_bodies_are_in_flight"),
    _m("large_body_slot_never_released", "a slot is released on every path",
       I, "            self.slots.in_flight -= 1\n            self.held = False",
       "            self.held = False",
       "test_media_sec__a_large_body_slot_is_released_on_every_path",
       "test_media_sec__at_most_two_large_bodies_are_in_flight"),
    _m("declared_length_ignored_for_slots", "a declared large body claims its slot early",
       I, "        if declared.isascii() and declared.isdigit() and len(declared) <= 19:\n            large.account(int(declared))",
       "        pass",
       "test_media_sec__a_declared_large_body_claims_its_slot_before_it_is_read"),
    _m("payload_scanned_byte_by_byte", "no per-byte Python work touches a media payload",
       V, "    if source[:len(DATA_PREFIX)].lower() == DATA_PREFIX:\n"
          "        check_data_url(source, allowed_mime)\n"
          "        return source, True",
       "    if source[:len(DATA_PREFIX)].lower() == DATA_PREFIX:\n"
          "        storable(source, \"messages\")\n"
          "        check_data_url(source, allowed_mime)\n"
          "        return source, True",
       "test_media_sec__no_per_byte_python_work_touches_a_media_payload"),
    _m("digest_reserialises_the_payload", "the digest covers media by hash, not by value",
       V, "    if not media:\n        return \"sha256:\" + hashlib.sha256(canonical_bytes(body)).hexdigest()",
       "    return \"sha256:\" + hashlib.sha256(canonical_bytes(body)).hexdigest()\n"
       "    if not media:\n        pass",
       "test_media_sec__no_per_byte_python_work_touches_a_media_payload"),
    _m("media_token_ignores_the_payload", "one byte of the payload changes the digest",
       V, "                    media[source] = MEDIA_TOKEN + hashlib.sha256(source.encode()).hexdigest()",
       "                    media[source] = MEDIA_TOKEN",
       "test_dur_admit__the_payload_digest_covers_the_media_payload_by_hash"),
    # --- review r2 B2: the cap is code points, in every form -----------------------
    _m("text_cap_in_bytes", "the text cap counts code points, not bytes",
       V, "            text_chars += len(storable(content, \"messages\"))",
       "            text_chars += len(storable(content, \"messages\").encode())",
       "test_media_sec__text_within_the_code_point_cap_is_accepted_in_every_form"),
    _m("text_cap_off_by_one", "the text cap is exact",
       V, "            if text_chars > MAX_TEXT_CODEPOINTS:", "            if text_chars > MAX_TEXT_CODEPOINTS + 1:",
       "test_media_sec__one_code_point_past_the_cap_is_refused"),
    _m("text_cap_off_for_text_parts", "the cap counts text parts too",
       V, "                text_chars += len(storable(part[\"text\"], \"messages\"))",
       "                storable(part[\"text\"], \"messages\")",
       "test_media_sec__the_text_cap_counts_across_parts_and_messages"),
    _m("url_cap_off_by_one", "the url cap is exact",
       V, "    if len(source) > MAX_URL_CHARS:", "    if len(source) > MAX_URL_CHARS + 1:",
       "test_media_sec__the_url_cap_is_exact"),
    # --- review r2 B3 and the same-pass list ---------------------------------------
    _m("max_completion_tokens_ignored", "max_completion_tokens alone is the ceiling",
       V, "        output = requested if requested is not None else alternative",
       "        output = requested",
       "test_f_base__max_completion_tokens_alone_is_the_output_ceiling"),
    _m("omitted_model_not_mapped", "an omitted model goes through the served map",
       V, "            return self.served_models.get(default, default)", "            return default",
       "test_f_base__an_omitted_model_goes_through_the_served_map"),
    _m("deps_not_read_from_rt", "register reads its deps from the runtime",
       N, 'ingress = Ingress(rt, deps if deps is not None else getattr(rt, "ingress", None))',
       "ingress = Ingress(rt, deps)",
       "test_f_base__register_reads_its_deps_from_the_runtime"),
    _m("default_model_not_served", "the served map contains MODEL_ID",
       N, "        if default not in self.validator.served_models:", "        if False:",
       "test_f_base__the_default_model_must_be_in_the_served_map"),
    _m("disconnect_not_caught", "a client disconnect is not a server error",
       I, "    except ClientDisconnect:", "    except KeyboardInterrupt:",
       "test_dur_rls__a_client_that_disconnects_mid_body_is_not_a_server_error"),
    _m("refusal_keeps_the_socket", "a refusal before the body closes the connection",
       I, '        if public.code in CLOSE_CODES:\n            headers["Connection"] = "close"',
       "        pass",
       "test_f_base__a_refusal_before_the_body_closes_the_connection"),
    _m("safe_param_length_6400", "a param name is bounded at 64 characters",
       I, 'SAFE_PARAM = re.compile(r"[A-Za-z0-9_.-]{1,64}")',
       'SAFE_PARAM = re.compile(r"[A-Za-z0-9_.-]{1,6400}")',
       "test_f_base__a_parameter_name_of_65_characters_is_not_echoed"),
    _m("request_id_source_trusted", "a minted id can never reach a header unchecked",
       I, "        if isinstance(minted, str) and REQUEST_ID_RE.fullmatch(minted):\n            return minted",
       "        return minted",
       "test_f_base__a_request_id_source_that_misbehaves_never_reaches_a_header"),
    _m("last_resort_without_the_request_id", "the last resort names the request",
       I, '        body = {"error": {**LAST_RESORT["error"], "request_id": request_id}}',
       "        body = LAST_RESORT",
       "test_f_base__the_envelope_of_last_resort"),
    _m("idempotency_key_not_storable", "an idempotency key is storable text",
       V, '        storable(key, "Idempotency-Key")', "        pass",
       "test_media_sec__an_idempotency_key_is_storable_text"),
    _m("data_url_mime_unchecked", "an inline video is a supported video type",
       V, '    if match.group("mime").lower() not in allowed_mime:', "    if False:",
       "test_media_sec__an_inline_video_must_be_a_supported_video_type"),
    _m("data_url_payload_may_be_empty", "an inline video carries a payload",
       V, "    if len(source) <= match.end():", "    if False:",
       "test_media_sec__an_inline_video_must_be_a_supported_video_type"),
    _m("url_control_only_crlf", "url hygiene covers more than CRLF",
       V, r'UNSAFE_IN_URL = re.compile(r"[\x00-\x20\x7f-\x9f\u2028\u2029@\\]")',
       r'UNSAFE_IN_URL = re.compile(r"[\r\n]")',
       "test_media_sec__a_reference_url_carries_no_smuggling_characters"),
    _m("content_type_prefix_match", "the content type is compared exactly",
       I, '    if declared != JSON_MEDIA_TYPE:', "    if not declared.startswith(JSON_MEDIA_TYPE):",
       "test_f_base__the_content_type_and_prefer_checks_are_case_insensitive"),
    _m("content_type_case_sensitive", "the content type is compared case-insensitively",
       I, '    declared = (request.headers.get("content-type") or "").split(";")[0].strip().lower()',
       '    declared = (request.headers.get("content-type") or "").split(";")[0].strip()',
       "test_f_base__the_content_type_and_prefer_checks_are_case_insensitive"),
    _m("content_type_checked_after_the_body", "the content type is checked before the read",
       N, "        intake.check_content_type(request)\n        large = self.slots.slot()",
       "        large = self.slots.slot()",
       "test_f_base__the_content_type_is_checked_before_the_body_is_read"),
    _m("prefer_case_sensitive", "Prefer is compared case-insensitively",
       V, '    tokens = {token.strip().lower().split("=")[0]',
       '    tokens = {token.strip().split("=")[0]',
       "test_f_base__the_content_type_and_prefer_checks_are_case_insensitive"),
    _m("upload_handle_not_anchored", "an upload handle is matched whole",
       V, "        if not ids.UPLOAD_HANDLE_RE.fullmatch(handle):",
       "        if not ids.UPLOAD_HANDLE_RE.match(handle):",
       "test_media_sec__an_upload_handle_is_anchored_and_exact"),
    _m("retry_hint_not_defaulted", "a 429/503 always carries retry guidance",
       I, "    elif error.code in errors.RETRY_AFTER_CODES:", "    elif False:",
       "test_f_base__retry_guidance_rides_with_every_429_and_503"),
    _m("empty_idempotency_key_accepted", "an empty idempotency key is not a key",
       V, "    if key is not None and (not key or len(key) > MAX_IDEMPOTENCY_KEY_CHARS):",
       "    if key is not None and len(key) > MAX_IDEMPOTENCY_KEY_CHARS:",
       "test_f_base__an_empty_idempotency_key_is_refused_and_absence_is_not"),
    _m("falsy_probe_is_ok", "a probe's answer has to be true",
       N, "            state[name] = OK if probe is not None and probe() else UNAVAILABLE",
       "            state[name] = OK if probe is not None and probe() is not None else UNAVAILABLE",
       "test_f_base__a_probe_that_answers_falsely_is_unavailable"),
    _m("top_p_upper_bound_open", "top_p is bounded above",
       V, '        _number(body, "top_p", 0.0, 1.0)', '        _number(body, "top_p", 0.0, 2.0)',
       "test_f_base__top_p_and_the_closed_set_are_exact_at_the_edges"),
    # --- review r3: separators, UTF-8, the payload's own checks --------------------
    _m("separators_not_counted", "one opener is not one element (r3 B1)",
       I, "    if text.count(SEPARATOR) > max_separators:", "    if False:",
       "test_media_sec__an_opener_free_body_is_refused_without_parsing_it"),
    _m("separator_cap_excludes_text", "commas inside text are text",
       V, "MAX_SEPARATORS = STRUCTURE_SEPARATORS + MAX_TEXT_CODEPOINTS",
       "MAX_SEPARATORS = STRUCTURE_SEPARATORS",
       "test_media_sec__commas_inside_text_are_text"),
    _m("separator_cap_off_by_one", "the separator cap is exact",
       I, "    if text.count(SEPARATOR) > max_separators:",
       "    if text.count(SEPARATOR) > max_separators + 1:",
       "test_media_sec__the_separator_cap_is_exact"),
    _m("separator_counts_payload_bytes", "the count is of separators, not of payload bytes",
       I, 'SEPARATOR = ","', 'SEPARATOR = "A"',
       "test_media_sec__an_inline_video_costs_one_separator"),
    _m("body_encoding_sniffed", "the body is UTF-8 (RFC 8259)",
       I, "    try:\n        text = raw.decode()\n    except UnicodeDecodeError:",
       "    try:\n        text = raw.decode(errors=\"replace\")\n    except UnicodeDecodeError:",
       "test_media_sec__a_body_that_is_not_utf8_is_refused",
       "test_media_sec__utf16_text_is_not_a_413_it_is_a_400"),
    _m("bom_accepted", "a BOM is not part of a JSON document",
       I, "    if text.startswith(BOM):", "    if False:",
       "test_media_sec__a_body_that_is_not_utf8_is_refused"),
    _m("payload_not_ascii_checked", "an inline payload is base64 ASCII (r3 B2)",
       V, "    if not source.isascii():", "    if False:",
       "test_media_sec__an_inline_payload_that_is_not_base64_ascii_is_refused"),
    _m("payload_nul_admitted", "an inline payload carries no NUL",
       V, '    if "\\x00" in source:', "    if False:",
       "test_media_sec__an_inline_payload_that_is_not_base64_ascii_is_refused"),
    _m("digest_ignores_other_params_with_media", "the digest covers the whole body (r3 B3)",
       V, '    document = {**body, "messages": [_tokenised(message, media) for message in messages]}',
       '    document = {"messages": [_tokenised(message, media) for message in messages]}',
       "test_dur_admit__the_payload_digest_covers_the_media_payload_by_hash"),
    # --- review r3 same pass -------------------------------------------------------
    _m("content_length_trusted_blindly", "a declared length is parsed only when it is one",
       I, "        if declared.isascii() and declared.isdigit() and len(declared) <= 19:",
       "        if declared:",
       "test_media_sec__a_declared_length_that_is_not_a_number_is_ignored"),
    _m("opener_cap_off_by_one", "the opener cap is exact at its boundary",
       I, "    if sum(text.count(opener) for opener in OPENERS) > max_openers:",
       "    if sum(text.count(opener) for opener in OPENERS) > max_openers + 1:",
       "test_media_sec__the_opener_cap_is_exact_at_its_real_boundary"),
    _m("slot_threshold_inclusive", "a body at the threshold is not a large body",
       I, "        if self.held or total <= self.slots.threshold:",
       "        if self.held or total < self.slots.threshold:",
       "test_media_sec__the_large_body_threshold_is_exclusive"),
    _m("slot_released_twice", "a slot is released once, however often release is called",
       I, "        if self.held:\n            self.slots.in_flight -= 1",
       "        if True:\n            self.slots.in_flight -= 1",
       "test_media_sec__a_slot_is_released_once_however_often_release_is_called"),
    _m("close_only_on_401", "every pre-body refusal closes the connection",
       I, 'CLOSE_CODES = frozenset({"invalid_api_key", "request_too_large", "capacity_exhausted",\n'
          '                         "deadline_exceeded"})',
       'CLOSE_CODES = frozenset({"invalid_api_key"})',
       "test_f_base__a_429_and_a_504_also_close_the_connection"),
    _m("last_resort_keeps_stale_headers", "the last resort drops the failed envelope's headers",
       I, "        return JSONResponse(body, status_code=500,\n"
          "                            headers={wire.HEADER_INFERENCE_ID: request_id})",
       "        return JSONResponse(body, status_code=500, headers=headers)",
       "test_f_base__the_last_resort_carries_no_header_from_the_envelope_it_replaced"),
    _m("handlers_mint_unchecked", "the app-level handlers mint a checked id",
       N, "        return intake.response(errors.DomainError(code=code), intake.mint(mint_request_id))",
       "        return intake.response(errors.DomainError(code=code), mint_request_id())",
       "test_f_base__the_app_level_handlers_mint_a_checked_request_id"),
    _m("unhandled_mints_unchecked", "the unhandled handler mints a checked id",
       N, "        request_id = intake.mint(mint_request_id)", "        request_id = mint_request_id()",
       "test_f_base__the_app_level_handlers_mint_a_checked_request_id"),
    _m("backslash_allowed_in_urls", "a backslash alone is enough to refuse a url",
       V, r'UNSAFE_IN_URL = re.compile(r"[\x00-\x20\x7f-\x9f\u2028\u2029@\\]")',
       r'UNSAFE_IN_URL = re.compile(r"[\x00-\x20\x7f-\x9f\u2028\u2029@]")',
       "test_media_sec__a_backslash_alone_is_enough_to_refuse_a_url"),
    _m("slow_deadline_doubled", "the intake deadline is the configured one",
       I, "    deadline = clock() + timeout_s", "    deadline = clock() + timeout_s * 2",
       "test_media_sec__a_slow_body_hits_the_intake_deadline"),
    _m("parameters_not_closed", "parameters carry the closed set and nothing else",
       V, '                        if name in SUPPORTED - {"model", "messages"}},',
       "                        if True},",
       "test_f_base__parameters_carry_the_closed_set_and_nothing_else"),
    # --- review r4: the parser's per-literal work, and the allowances --------------
    _m("parse_int_unbounded", "an integer literal is bounded before it is converted",
       I, "        body = json.loads(text, parse_constant=_no_constants,\n"
          "                          parse_int=_bounded_int, parse_float=_bounded_float)",
       "        body = json.loads(text, parse_constant=_no_constants,\n"
          "                          parse_float=_bounded_float)",
       "test_media_sec__a_body_of_huge_integers_is_refused_by_the_parser",
       "test_media_sec__a_number_literal_is_bounded_by_its_length"),
    _m("parse_float_unbounded", "a float literal is bounded too",
       I, "        body = json.loads(text, parse_constant=_no_constants,\n"
          "                          parse_int=_bounded_int, parse_float=_bounded_float)",
       "        body = json.loads(text, parse_constant=_no_constants,\n"
          "                          parse_int=_bounded_int)",
       "test_media_sec__a_number_literal_is_bounded_by_its_length"),
    _m("number_digits_off_by_one", "the digit bound is exact",
       I, "    if len(literal.lstrip(\"-\")) > MAX_NUMBER_DIGITS:\n"
          "        raise ValueError(f\"a number of more than {MAX_NUMBER_DIGITS} digits\")\n"
          "    return int(literal)",
       "    if len(literal.lstrip(\"-\")) > MAX_NUMBER_DIGITS + 1:\n"
          "        raise ValueError(f\"a number of more than {MAX_NUMBER_DIGITS} digits\")\n"
          "    return int(literal)",
       "test_media_sec__a_number_literal_is_bounded_by_its_length"),
    _m("number_sign_counted_as_a_digit", "a sign is not a digit",
       I, '    if len(literal.lstrip("-")) > MAX_NUMBER_DIGITS:', "    if len(literal) > MAX_NUMBER_DIGITS:",
       "test_media_sec__a_number_literal_is_bounded_by_its_length"),
    _m("content_length_non_ascii_digits", "a non-ASCII digit never reaches int()",
       I, "        if declared.isascii() and declared.isdigit() and len(declared) <= 19:",
       "        if declared.isdigit() and len(declared) <= 19:",
       "test_media_sec__a_declared_length_that_is_not_a_number_is_ignored"),
    _m("structure_separators_halved", "the structural allowance fits a maximal body",
       V, "STRUCTURE_SEPARATORS = (len(SUPPORTED) + MAX_MESSAGES * (2 + MAX_PARTS_PER_MESSAGE * 2)\n"
          "                        + MAX_STOP_SEQUENCES)",
       "STRUCTURE_SEPARATORS = (len(SUPPORTED) + MAX_MESSAGES * (2 + MAX_PARTS_PER_MESSAGE * 2)\n"
          "                        + MAX_STOP_SEQUENCES) // 2",
       "test_media_sec__a_maximal_structure_body_is_accepted"),
    _m("text_beside_messages_ignored", "stop sequences and the model name are text too",
       V, "MAX_SEPARATORS = STRUCTURE_SEPARATORS + MAX_TEXT_CODEPOINTS + TEXT_BESIDE_MESSAGES",
       "MAX_SEPARATORS = STRUCTURE_SEPARATORS + MAX_TEXT_CODEPOINTS",
       "test_media_sec__a_maximal_structure_body_is_accepted"),
    # --- the cases the first pass left unkillable ------------------------------
    _m("everything_is_too_large", "a body within the cap is read whole",
       I, "                if total > max_bytes:", "                if total >= 0:",
       "test_media_sec__a_body_within_the_cap_is_read_whole"),
    _m("malformed_json_changes_code", "malformed JSON is a stable invalid_request",
       I, '        raise errors.InvalidRequest("the request body is not valid JSON") from None',
       '        raise errors.UnsupportedParameter("the request body is not valid JSON") from None',
       "test_media_sec__malformed_json_is_a_400_with_no_parser_text"),
    _m("envelope_carries_the_operator_detail", "the envelope carries only contract fields",
       I, '        return JSONResponse(body.model_dump(mode="json", exclude_none=True),',
       '        return JSONResponse({**body.model_dump(mode="json", exclude_none=True),\n'
       '                             "detail": str(error.detail)},',
       "test_f_base__the_envelope_carries_only_the_contract_fields"),
    _m("upload_handles_refused", "an owned upload handle is a valid media reference",
       V, "    if source.startswith(UPLOAD_SCHEME):", "    if False:",
       "test_media_sec__an_owned_upload_handle_and_a_public_url_are_both_accepted"),
    _m("disagreeing_output_ceilings_accepted", "max_tokens and max_completion_tokens must agree",
       V, "        if requested is not None and alternative is not None and requested != alternative:",
       "        if False:", "test_f_base__max_tokens_and_max_completion_tokens_must_agree"),
    _m("model_revision_replaced", "the requested model revision passes through untouched",
       V, "            model_revision=revision,",
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
       "from .routes import ingress as _ingress\nROUTERS = (health, models, chat, _ingress)",
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
