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
       I, "        body = json.loads(raw, parse_constant=_no_constants)",
       "        body = json.loads(raw)",
       "test_media_sec__a_parser_hostile_body_is_a_400_not_a_500",
       "test_media_sec__a_number_json_does_not_have_is_refused"),
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
          '        log.exception("the error envelope could not be rendered for request %s", request_id)\n'
          "        return JSONResponse(LAST_RESORT, status_code=500, headers=headers)",
       "    except KeyboardInterrupt:\n        raise",
       "test_f_base__an_unrenderable_param_is_still_an_envelope"),
    # --- review r1 item 3: structure, liveness and identity-first -------------
    _m("messages_unbounded", "the message count is bounded",
       V, "    if len(messages) > MAX_MESSAGES:", "    if False:",
       "test_media_sec__the_message_count_is_bounded",
       "test_media_sec__a_huge_body_is_refused_without_stalling_the_event_loop"),
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
    _m("non_media_size_unbounded_for_a_model_name", "a megabyte model name is refused",
       V, "        if body_bytes - media_chars > MAX_NON_MEDIA_BYTES:",
       "        if body_bytes - media_chars > 2 ** 40:",
       "test_media_sec__a_megabyte_model_name_never_reaches_the_store"),
    _m("non_media_size_unbounded", "non-media JSON is bounded",
       V, "        if body_bytes - media_chars > MAX_NON_MEDIA_BYTES:", "        if False:",
       "test_media_sec__non_media_json_is_bounded_but_inline_media_is_not"),
    _m("inline_media_counted_as_structure", "an inline video is measured out of that bound",
       V, "        return source, len(source)", "        return source, 0",
       "test_media_sec__non_media_json_is_bounded_but_inline_media_is_not"),
    _m("parse_blocks_the_event_loop", "a large body is parsed off the loop",
       I, "    if len(raw) > offload_over_bytes:\n        return await asyncio.to_thread(parse_object, raw)",
       "    if False:\n        return await asyncio.to_thread(parse_object, raw)",
       "test_media_sec__a_large_body_is_parsed_off_the_event_loop"),
    _m("body_read_before_identity", "an unauthenticated caller never makes us buffer",
       N, "        auth = await self.auth.context(request)\n"
          "        intake.check_content_type(request)\n"
          "        raw = await intake.read_body(",
       "        intake.check_content_type(request)\n"
          "        raw = await intake.read_body(",
       "test_dur_rls__an_unauthenticated_caller_never_makes_us_buffer"),
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
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
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
       V, "        if not DATA_URL.match(source):", "        if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("control_characters_in_urls", "a url carries no control characters",
       V, "    if CONTROL_CHARS.search(source):", "    if False:",
       "test_media_sec__a_malformed_shape_is_refused_with_a_stable_code"),
    _m("content_type_unchecked", "the body is application/json",
       I, "    if declared != JSON_MEDIA_TYPE:", "    if False:",
       "test_f_base__a_body_that_is_not_json_is_refused"),
    _m("digest_over_the_raw_bytes", "the payload digest is canonical",
       V, 'payload_digest="sha256:" + hashlib.sha256(canonical_bytes(body)).hexdigest(),',
       'payload_digest="sha256:" + hashlib.sha256(repr(body).encode()).hexdigest(),',
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
