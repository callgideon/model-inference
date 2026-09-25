#!/usr/bin/env python3
"""R32/R40 for E1B: every invariant this task claims must be killable by ONE edit.

    apps/infrx-api/.venv/bin/python models/marlin2b/tests/mutants.py --list
    apps/infrx-api/.venv/bin/python models/marlin2b/tests/mutants.py

Same shape as `tests/integration/mutants.py` (E2's runner), minus the parts that need a
live stack: nothing is mutated in place — each run copies `models/marlin2b/` into a
temporary directory, edits one line there, and runs the named cases inside the copy.

A kill is an **assertion failure**, never "the process exited non-zero": a collection
error, an import error or a selector that matched nothing says nothing about the
invariant and is reported as such. Two CONTROL mutants make no behavioural change and
must SURVIVE; if a no-op edit comes back killed, every other kill here is worthless.
"""
from __future__ import annotations

import argparse, json, os, re, shutil, subprocess, sys, tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREE = HERE.parent                                   # models/marlin2b
SUITE = "tests"                                      # relative to the copied tree


@dataclass(frozen=True)
class Mutant:
    id: str
    invariant: str
    path: str                     # relative to models/marlin2b
    before: str
    after: str
    select: str                   # pytest -k expression
    cases: tuple[str, ...] = field(default_factory=tuple)
    occurrences: int = 1
    must_survive: bool = False


MUTANTS: tuple[Mutant, ...] = (
    # ---------------- controls (must survive)
    Mutant("e1bc01", "CONTROL: a comment-only edit in bench.py changes nothing",
           "bench.py", 'IDEMPOTENCY_PREFIX = "sop1."',
           'IDEMPOTENCY_PREFIX = "sop1."  # control: no behaviour change',
           "item_key or resume or tenant", must_survive=True),
    Mutant("e1bc03", "CONTROL: the copy layout does not by itself fail the CLI default-path case",
           "bench.py", 'DEFAULT_OUT = os.path.join(HERE, "results", "bench.jsonl")',
           'DEFAULT_OUT = os.path.join(HERE, "results", "bench.jsonl")  # control',
           "historical_cli", must_survive=True),
    Mutant("e1bc02", "CONTROL: a comment-only edit in synth.py changes nothing",
           "corpus-synth/synth.py", 'MEDIA_SUBDIR = "sop-synth-v1"',
           'MEDIA_SUBDIR = "sop-synth-v1"  # control: no behaviour change',
           "manifest or validator or bit_exact", must_survive=True),

    # ---------------- E1B.a: the reference form and the item identity
    Mutant("e1bm01", "the upload reference is infrx-upload:upl_… and nothing else (R61)",
           "bench.py", 'UPLOAD_REF_SCHEME = "infrx-upload:"', 'UPLOAD_REF_SCHEME = "upload://"',
           "request_forms",
           cases=("test_request_forms_and_upload_flow",)),
    Mutant("e1bm02", "a handle that is not a contract handle is never sent back as a reference",
           "bench.py", 'HANDLE_OK = re.compile(r"upl_[A-Za-z0-9_-]{22,64}")',
           'HANDLE_OK = re.compile(r"[A-Za-z0-9_-]{1,72}")',
           "contract_handle",
           cases=("test_an_upload_handle_that_is_not_a_contract_handle_is_never_sent_back",)),
    Mutant("e1bm03", "every field of the SOP recipe changes the item key",
           "bench.py",
           "    parts = (dataset_version, source_id, episode_id, str(segment_index),\n"
           "             f\"{start_s}-{end_s}\", prompt_version, profile_version)",
           "    parts = (dataset_version, source_id, episode_id, str(segment_index),\n"
           "             f\"{start_s}-{end_s}\", prompt_version)",
           "item_key",
           cases=("test_item_key_is_the_sop_recipe_and_a_function_of_the_payload_only",)),
    Mutant("e1bm04", "the unit separator keeps neighbouring fields from colliding",
           "bench.py", 'return sha256("\\x1f".join(parts).encode("utf-8"))',
           'return sha256("".join(parts).encode("utf-8"))',
           "item_key",
           cases=("test_item_key_is_the_sop_recipe_and_a_function_of_the_payload_only",)),
    Mutant("e1bm05", "two scheduled copies of one clip are two items, not one replayed",
           "bench.py", "        segment = occurrences.get(source_id, 0)",
           "        segment = 0",
           "distinct_idempotency",
           cases=("test_every_scheduled_item_carries_a_distinct_idempotency_key",)),

    # ---------------- E1B.a: MARLIN-SOP resume
    Mutant("e1bm06", "every request carries its Idempotency-Key, so a resume cannot duplicate",
           "bench.py",
           '    headers = {**cfg["headers_for"](item["tenant"]),\n'
           '               "idempotency-key": item["idempotency_key"]}',
           '    headers = {**cfg["headers_for"](item["tenant"])}',
           "second_accepted_item or reuses_its_handle",
           cases=("test_resume_after_a_lost_ack_creates_no_second_accepted_item",
                  "test_a_resumed_upload_item_reuses_its_handle_instead_of_conflicting")),
    Mutant("e1bm07", "a failed item is not terminal: a resume must re-send it",
           "bench.py",
           '    if row.get("outcome") in ("accepted", CANCELLED_REPLAY):\n        return True',
           '    if row.get("outcome") in ("accepted", CANCELLED_REPLAY, "failed"):\n        return True',
           "second_accepted_item",
           cases=("test_resume_after_a_lost_ack_creates_no_second_accepted_item",)),
    Mutant("e1bm08", "a resumed upload item re-uses its staged handle instead of re-staging",
           "bench.py",
           '        if row is not None and row.get("upload_handle"):\n'
           '            item["upload_handle"] = row["upload_handle"]',
           '        if False:\n'
           '            item["upload_handle"] = row["upload_handle"]',
           "reuses_its_handle",
           cases=("test_a_resumed_upload_item_reuses_its_handle_instead_of_conflicting",)),
    Mutant("e1bm09", "each tenant sends its own key, so the idempotency scope is per org",
           "bench.py", "        value = keys[tenant % len(keys)] if keys else \"\"",
           "        value = keys[0] if keys else \"\"",
           "two_tenants",
           cases=("test_two_tenants_may_hold_identical_item_keys",)),

    # ---------------- E1B.b: the driver
    Mutant("e1bm10", "a burst is several arrivals at one instant, at the same mean rate",
           "bench.py", "            if i % burst == 0:", "            if True:",
           "bursty",
           cases=("test_bursty_arrivals_are_bursts_and_keep_the_mean_rate",)),
    Mutant("e1bm11", "a cancelled request is neither accepted nor failed",
           "bench.py",
           '                row["outcome"], row["error_class"] = "cancelled", "client_cancelled"',
           '                row["outcome"], row["error_class"] = "accepted", "client_cancelled"',
           "cancellation",
           cases=("test_cancellation_is_its_own_outcome_and_stays_in_the_denominators",)),
    Mutant("e1bm12", "a phase the target does not publish is named, not inferred",
           "bench.py", '"declared_missing": [p for p in PHASES if p not in seen]',
           '"declared_missing": []',
           "phase_timings",
           cases=("test_phase_timings_come_from_server_timing_and_absent_phases_are_named",)),
    Mutant("e1bm13", "a soak always has a last resource sample to compare with its first",
           "bench.py",
           '        row = {"kind": "resource_sample", "t_s": round(CLOCK() - t0, 6), '
           '"final": True, **sampler()}\n        samples.append(row)\n        write_row(cfg, row)\n'
           '        raise',
           '        raise',
           "resource_samples",
           cases=("test_resource_samples_are_written_to_the_raw_file_and_summarised",)),
    Mutant("e1bm14", "successful work is counted in video-seconds that really went out",
           "bench.py",
           '    video_seconds = sum(r["duration_s"] or 0 for r in fresh if r["media_sent"])',
           '    video_seconds = sum(r["duration_s"] or 0 for r in fresh)',
           "declared_profile",
           cases=("test_the_declared_profile_carries_every_axis_a_throughput_number_needs",)),
    Mutant("e1bm15", "the report refuses a tail the sample count cannot support",
           "bench.py",
           '        cell = lambda block, q: ("—" if (pct.get(block) or {}).get(f"p{q}") is None',
           '        cell = lambda block, q: ((pct.get(block) or {}).get(f"p{q}") if False',
           "report_refuses",
           cases=("test_the_report_refuses_unsupported_tails_and_names_every_cell_limit",)),
    Mutant("e1bm16", "the sample-sufficiency rule is p50>=6 / p95>=60 / p99>=300",
           "bench.py",
           "MIN_TAIL = 3            # a reported quantile needs this many samples strictly beyond it",
           "MIN_TAIL = 1            # a reported quantile needs this many samples strictly beyond it",
           "suppressed or predeclared_protocol",
           cases=("test_percentiles_are_suppressed_when_samples_cannot_support_them",
                  "test_the_predeclared_protocol_matches_the_client_that_implements_it")),

    Mutant("e1bm24", "a pre-E1B summary row is listed apart, not read as an empty cell",
           "bench.py", '    return "denominators" not in cell or "profile" not in cell',
           "    return False",
           "report_refuses",
           cases=("test_the_report_refuses_unsupported_tails_and_names_every_cell_limit",)),

    # ---------------- the review's non-blocking items, each with its own case
    Mutant("n05", "only an attributable failure feeds the platform-caused criterion",
           "bench.py",
           '    return row.get("outcome") == "failed" and (\n'
           '        (isinstance(status, int) and status >= 500)\n'
           '        or row.get("error_class") in PLATFORM_ERROR_CLASSES)',
           '    return row.get("outcome") == "failed"',
           "unattributable",
           cases=("test_an_unattributable_failure_is_not_counted_as_platform_caused",)),
    Mutant("n07", "a resumed run makes no cold/warm claim",
           "bench.py", '    for item in schedule:\n        item["cold"] = None',
           '    for item in []:\n        item["cold"] = None',
           "no_cold_warm_claim",
           cases=("test_a_resumed_run_makes_no_cold_warm_claim",)),
    Mutant("n08", "a resume across a changed identity knob is refused",
           "bench.py",
           "    differing = [name for name in FINGERPRINT_FIELDS "
           "if previous.get(name) != current[name]]",
           "    differing = []",
           "run_profile",
           cases=("test_the_raw_file_declares_its_run_profile_and_a_mismatched_resume_is_refused",)),

    # ---------------- the reviewer's surviving mutants (review of 2cf7a81), now killable
    Mutant("r05", "a target that publishes no Server-Timing gets no invented phase",
           "bench.py", "    return out or None", '    return out or {"queue": 0.0}',
           "no_phase_timings",
           cases=("test_a_target_that_publishes_no_phase_timings_says_so",)),
    Mutant("r06", "the handle minimum is 22 characters, one short is refused",
           "bench.py", 'HANDLE_OK = re.compile(r"upl_[A-Za-z0-9_-]{22,64}")',
           'HANDLE_OK = re.compile(r"upl_[A-Za-z0-9_-]{21,64}")',
           "handle_grammar",
           cases=("test_the_handle_grammar_is_enforced_at_both_edges",)),
    Mutant("r19", "a handle may not carry a dot or a slash, whatever its length",
           "bench.py", 'HANDLE_OK = re.compile(r"upl_[A-Za-z0-9_-]{22,64}")',
           r'HANDLE_OK = re.compile(r"upl_[\S]{22,64}")',
           "handle_grammar",
           cases=("test_the_handle_grammar_is_enforced_at_both_edges",)),
    Mutant("r08", "EVERY tenant's key is checked, not just the first",
           "bench.py",
           "    if isinstance(key, (list, tuple, set, frozenset)):\n"
           "        return any(carries_key(value, one) for one in key)",
           "    if False:\n"
           "        return any(carries_key(value, one) for one in key)",
           "two_tenants",
           cases=("test_two_tenants_may_hold_identical_item_keys",)),
    Mutant("r13", "a 429 is resumable, not terminal",
           "bench.py",
           "TERMINAL_REJECT_STATUS = {400, 401, 403, 404, 409, 410, 413, 415, 422}",
           "TERMINAL_REJECT_STATUS = {400, 401, 403, 404, 409, 410, 413, 415, 422, 429}",
           "rejected_429",
           cases=("test_a_rejected_429_item_is_resumed_with_the_same_key",)),
    Mutant("r11", "the concurrency sweep stays inside the engine's own --max-num-seqs",
           "results/E1B-protocol.md", "`--target direct -c 1,2,4,8,16,32`",
           "`--target direct -c 1,2,4,8,16,64`",
           "predeclared_protocol",
           cases=("test_the_predeclared_protocol_matches_the_client_that_implements_it",)),

    # ---------------- E1B.c: the pre-registration cannot drift
    Mutant("e1bm17", "the protocol's frozen seed is the seed the runs use",
           "results/E1B-protocol.md", "`--seed 20260922`", "`--seed 7`",
           "predeclared_protocol",
           cases=("test_the_predeclared_protocol_matches_the_client_that_implements_it",)),
    Mutant("e1bm18", "the absent latency criterion stays absent",
           "results/E1B-protocol.md", "| **explicitly absent** |", "| **provisional (P-18)** |",
           "predeclared_protocol",
           cases=("test_the_predeclared_protocol_matches_the_client_that_implements_it",)),

    # ---------------- E1B.a: sop-synth-v1
    Mutant("e1bm19", "a step declared absent is never drawn",
           "corpus-synth/synth.py",
           '        steps = [s for s in steps if s["canonical_index"] != ABSENT_CANONICAL_INDEX]',
           '        steps = list(steps)',
           "bit_exact or committed_manifest or each_case",
           cases=("test_the_render_command_is_bit_exact_and_derives_only_from_the_script",)),
    Mutant("e1bm20", "the render is bit-exact and single-threaded, or the sha256 is not a pin",
           "corpus-synth/synth.py",
           '            "-fflags", "+bitexact", "-flags:v", "+bitexact", "-movflags", "+faststart",',
           '            "-movflags", "+faststart",',
           "bit_exact",
           cases=("test_the_render_command_is_bit_exact_and_derives_only_from_the_script",)),
    Mutant("e1bm21", "only the declared clip may put two steps inside one frame period",
           "corpus-synth/synth.py", "        inside = [g for g in gaps if g < period]",
           "        inside = []",
           "validator",
           cases=("test_the_validator_rejects_every_way_the_script_can_go_wrong",)),
    Mutant("e1bm22", "a boundary crossing is recorded whether or not it is the declared case",
           "corpus-synth/synth.py",
           '        if c.get("boundary_spanning_step_ids") != spanning:',
           '        if False:',
           "validator",
           cases=("test_the_validator_rejects_every_way_the_script_can_go_wrong",)),
    Mutant("e1bm23", "a missing prerequisite is named exactly, and nothing reaches the network",
           "corpus-synth/synth.py",
           "    if not (ffmpeg.exists() and ffprobe.exists()) and not allow_download:",
           "    if False:",
           "missing_prerequisite",
           cases=("test_a_missing_prerequisite_names_the_exact_tool_and_never_reaches_the_network",)),
    # ---------------- E1C.1: the upload client speaks the MOUNTED contract (RV-07)
    Mutant("e1cm01", "create sends the route's constraint fields, not the legacy shape",
           "bench.py",
           '                          json={"max_bytes": len(body), "bytes": len(body),\n'
           '                                "accepted_mime": [mime], "digest": digest})',
           '                          json={"purpose": "video", "bytes": len(body),\n'
           '                                "sha256": hexdigest, "content_type": mime})',
           "real_router",
           cases=("test_upload_speaks_the_mounted_contract_through_the_real_router",)),
    Mutant("e1cm02", "no origin an answer names ever receives the bytes or the bearer",
           "bench.py",
           '    put = await client.put(f"{cfg[\'base\']}/uploads/{handle}", content=body,',
           '    put = await client.put(ticket.get("url") or f"{cfg[\'base\']}/uploads/{handle}", '
           'content=body,',
           "returned_origin",
           cases=("test_no_returned_origin_ever_receives_the_bytes_or_the_bearer",)),
    Mutant("e1cm03", "a ticket whose destination_ref is not infrx-upload:<handle> is refused",
           "bench.py",
           '    if handle is None or ticket.get("destination_ref") != UPLOAD_REF_SCHEME + handle:',
           '    if handle is None:',
           "outside_the_contract",
           cases=("test_an_answer_outside_the_contract_is_refused_before_any_byte_moves",)),
    Mutant("e1cm04", "the completion must name our handle, bytes and digest",
           "bench.py",
           '    if (completed.get("upload_handle"), media.get("digest"), media.get("bytes")) != (\n'
           '            handle, digest, len(body)):',
           '    if completed.get("upload_handle") is None:',
           "outside_the_contract",
           cases=("test_an_answer_outside_the_contract_is_refused_before_any_byte_moves",)),
    Mutant("e1cm05", "the fake refuses what the real router refuses (no drift)",
           "tests/fake_gateway.py",
           "        if not isinstance(body, dict) or set(body) - UPLOAD_CONSTRAINTS:",
           "        if not isinstance(body, dict):",
           "fake_gateway_answers",
           cases=("test_the_fake_gateway_answers_the_upload_probes_like_the_real_router",)),
    Mutant("e1cm06", "completion takes no fields, at the fake as at the router",
           "tests/fake_gateway.py",
           "        if request.content and json.loads(request.content) != {}:",
           "        if False:",
           "fake_gateway_answers",
           cases=("test_the_fake_gateway_answers_the_upload_probes_like_the_real_router",)),

    # ---------------- E1C.2: the resumable dataset recipe (dataset.py)
    Mutant("e1cd01", "the key is a function of the item, never of the run or attempt",
           "dataset.py", "    key = bench.item_key(a.dataset_version, item[\"id\"],",
           "    key = bench.item_key(a.dataset_version + str(time.time()), item[\"id\"],",
           "exactly_one_logical_result",
           cases=("test_interrupt_then_resume_leaves_exactly_one_logical_result_per_item",)),
    Mutant("e1cd02", "a resumed upload item keeps its staged handle (same payload)",
           "dataset.py",
           '    if form == "upload" and (handle is None or (not row["sends"]',
           '    if form == "upload" and (True or (not row["sends"]',
           "exactly_one_logical_result",
           cases=("test_interrupt_then_resume_leaves_exactly_one_logical_result_per_item",)),
    Mutant("e1cd03", "the producer is bounded by the queue",
           "dataset.py", "    queue = asyncio.Queue(maxsize=a.queue)",
           "    queue = asyncio.Queue()",
           "bounded_queue",
           cases=("test_the_producer_never_reads_further_ahead_than_the_bounded_queue",)),
    Mutant("e1cd04", "a failure export retries exactly that subset",
           "dataset.py", "    only = read_ids(a.only) if a.only else None",
           "    only = None",
           "identified_subset",
           cases=("test_a_failure_export_retries_exactly_the_identified_subset",)),
    Mutant("e1cd05", "result_expired is terminal: never re-billed automatically",
           "dataset.py",
           '    if row["http_status"] == 410 and row["error_code"] == "result_expired":\n'
           '        return "expired"',
           '    if False:\n        return "expired"',
           "never_rebilled",
           cases=("test_an_expired_result_is_exported_and_never_rebilled",)),
    Mutant("e1cd06", "an item changed after its key was used is refused, not re-keyed",
           "dataset.py", '        if row["sends"]:\n            stats["changed"] += 1',
           '        if False:\n            stats["changed"] += 1',
           "refused_not_rekeyed",
           cases=("test_a_changed_item_or_a_changed_run_identity_is_refused_not_rekeyed",)),
    Mutant("e1cd07", "a resume across a changed run identity is refused",
           "dataset.py",
           "        differing = [k for k in FINGERPRINT if stored.get(k) != current[k]]",
           "        differing = []",
           "refused_not_rekeyed",
           cases=("test_a_changed_item_or_a_changed_run_identity_is_refused_not_rekeyed",)),
    Mutant("e1cd08", "digest retention keeps no output text",
           "dataset.py", '                      output=text if a.retain_output == "text" else None,',
           "                      output=text,",
           "digest_retention",
           cases=("test_digest_retention_keeps_no_output_text",)),

    # ---------------- E1C.3: validity (RV-08, BENCH-VALIDITY)
    Mutant("e1cv01", "capacity numbers are over fresh answers only, never replays",
           "bench.py", '    fresh = [r for r in accepted if served_as(r) == "fresh"]',
           "    fresh = accepted",
           "unexpected_replay",
           cases=("test_an_unexpected_replay_invalidates_the_cell_and_never_counts_as_capacity",)),
    Mutant("e1cv02", "an unexpected replay makes the cell INVALID",
           "bench.py",
           "    if unexpected:\n        reasons.append(f\"unexpected replay",
           "    if False:\n        reasons.append(f\"unexpected replay",
           "unexpected_replay",
           cases=("test_an_unexpected_replay_invalidates_the_cell_and_never_counts_as_capacity",)),
    Mutant("e1cv03", "a resume excuses only replays of the keys it resumed",
           "bench.py",
           '                  if not (intentional_resume and r.get("resend", "resume") == "resume")]',
           "                  if not intentional_resume]",
           "resume_labels",
           cases=("test_a_resume_labels_its_replays_and_they_do_not_invalidate_it",)),
    Mutant("e1cv04", "a scheduled item with no attempt makes the cell INVALID",
           "bench.py", "    if missing:\n        reasons.append(f\"missing attempts",
           "    if False:\n        reasons.append(f\"missing attempts",
           "missing_attempts",
           cases=("test_missing_attempts_and_an_interrupted_schedule_invalidate",)),
    Mutant("e1cv05", "driver lag above the declared bound makes the cell INVALID",
           "bench.py",
           "    if open_loop and max_lag_s is not None and lag_max is not None and lag_max > max_lag_s:",
           "    if open_loop and max_lag_s is not None and lag_max is not None and lag_max > 10 * max_lag_s:",
           "driver_lag",
           cases=("test_driver_lag_above_the_declared_bound_invalidates_but_retry_waits_do_not",)),
    Mutant("e1cv06", "a served model other than the declared one makes the cell INVALID",
           "bench.py", "        if any(m != expect_model for m in observed):",
           "        if False:",
           "served_model",
           cases=("test_a_served_model_other_than_the_declared_one_invalidates",)),
    Mutant("e1cv07", "the served model is read from the stream",
           "bench.py",
           '                row["served_model"] = allow(chunk["model"], MODEL_OK, cfg["key"])',
           "                pass",
           "served_model",
           cases=("test_a_served_model_other_than_the_declared_one_invalidates",)),
    Mutant("e1cv08", "a lost attempt row fails reconciliation",
           "bench.py",
           '            per_seq[r["seq"]] == (r.get("retries") or 0) + 1 for r in finals),',
           "            True for r in finals),",
           "reconcile",
           cases=("test_counters_that_do_not_reconcile_invalidate",)),
    Mutant("e1cv09", "old raw rows classify exactly as new ones (no reclassification)",
           "bench.py", '    if row.get("idempotency_replayed"):\n        return "replay"',
           '    if row.get("served") == "replay":\n        return "replay"',
           "never_reclassified",
           cases=("test_old_e1b_raw_files_stay_readable_and_are_never_reclassified",)),
    Mutant("e1cv10", "upload stage timestamps are taken",
           "bench.py", '            row["upload_start_s"] = now() if now else None\n',
           "", "monotonic",
           cases=("test_stage_timestamps_are_monotonic_and_output_lengths_are_reported",)),

    # ---------------- E1C.4: the run profile and --validate-only (consumer-v1/05 §1, §6)
    Mutant("e1cp01", "every required profile field is required",
           "runprofile.py",
           '        out += [f"{path}.{k}: required" for k in schema.get("required", ()) if k not in value]\n',
           "", "missing_group",
           cases=("test_a_missing_group_or_field_refuses_the_run_before_any_request",)),
    Mutant("e1cp02", "the run refuses to start when its profile does not validate",
           "bench.py", '        if not verdict["runnable"]:\n            print("refusing to start',
           '        if False:\n            print("refusing to start',
           "missing_group",
           cases=("test_a_missing_group_or_field_refuses_the_run_before_any_request",)),
    Mutant("e1cp03", "the target host must be on the allowlist",
           "runprofile.py",
           '    if host not in target["allowlist"] and f"{host}:{url.port}" not in target["allowlist"]:',
           "    if False:", "every_flag",
           cases=("test_every_flag_must_sit_inside_the_declared_target_workload_and_bounds",)),
    Mutant("e1cp04", "the workload is pinned by its manifest digest",
           "runprofile.py", '    elif file_sha256(source) != w["manifest_sha256"]:',
           "    elif False:", "every_flag",
           cases=("test_every_flag_must_sit_inside_the_declared_target_workload_and_bounds",)),
    Mutant("e1cp05", "the budget check includes outstanding holds",
           "runprofile.py",
           '        out["projected"] = n * per_request + (v["outstanding_holds"] or 0)',
           '        out["projected"] = n * per_request', "outstanding_holds",
           cases=("test_the_budget_counts_outstanding_holds_and_a_missing_rate_blocks_only_paid_runs",)),
    # ---------------- P-24 amendment: the spend cap in the profile's own unit (USD | CREDIT)
    Mutant("e1cp20", "the spend unit is the profile's currency, not an assumed USD",
           "runprofile.py", '    spend, cur = bounds["spend"], bounds["spend"]["currency"]',
           '    spend, cur = bounds["spend"], "USD"', "credit_cap or usd_profile or mixed_spend",
           cases=("test_a_credit_cap_projects_in_credit_with_exact_arithmetic",
                  "test_a_usd_profile_reads_both_namings_and_never_as_credit")),
    Mutant("e1cp21", "USD amounts are never read as CREDIT (no 1:1 conversion)",
           "runprofile.py", '    if legacy and cur != "USD":', "    if False:",
           "credit_cap or usd_profile or mixed_spend",
           cases=("test_a_usd_profile_reads_both_namings_and_never_as_credit",
                  "test_mixed_spend_units_or_an_unknown_currency_refuse_the_run")),
    Mutant("e1cp22", "the cap comparison is exact decimal, never binary float",
           "runprofile.py", "    return None if v is None else Decimal(str(v))",
           "    return None if v is None else float(v)", "credit_cap or usd_profile or mixed_spend",
           cases=("test_a_credit_cap_projects_in_credit_with_exact_arithmetic",)),
    Mutant("e1cp23", "one profile never mixes the legacy USD keys with the unit-neutral ones",
           "runprofile.py", "    if legacy and any(new in node for node, keys, _ in groups for new in keys):",
           "    if False:", "credit_cap or usd_profile or mixed_spend",
           cases=("test_mixed_spend_units_or_an_unknown_currency_refuse_the_run",)),
    Mutant("e1cp24", "a non-finite cap, hold or rate is a refusal, never an open cap (PCC-V1)",
           "runprofile.py", "                      if d is not None and not d.is_finite()]",
           "                      if False]", "non_finite",
           cases=("test_a_non_finite_spend_amount_is_a_refusal_not_an_open_cap",)),
    Mutant("e1cp25", "an integer spend literal of any size validates; only a float is non-finite "
           "(PCC-V5)", "runprofile.py",
           "        if isinstance(value, float) and not math.isfinite(value):",
           "        if not math.isfinite(value):", "huge_integer",
           cases=("test_a_huge_integer_spend_validates_and_only_float_inf_or_nan_is_refused",)),
    Mutant("e1cp26", "no FILL placeholder reaches a run, whatever the schema allows (E4P-V3)",
           "runprofile.py", "for p in fill_paths(profile)]", "for p in ()]", "e4c_base",
           cases=("test_the_e4c_base_profile_refuses_until_every_fill_is_frozen_then_bounds_the_soak",)),
    Mutant("e1cp27", "a profiled loopback gateway is metered: its blocks refuse (E4P)",
           "runprofile.py",
           '    return profile is None or isinstance(target, dict) and target.get("path") == '
           '"direct-engine"', "    return True", "loopback_gateway",
           cases=("test_a_profiled_loopback_gateway_is_metered_and_its_blocks_refuse_the_run",)),
    Mutant("e1cp06", "a missing rate/budget blocks a paid run",
           "runprofile.py",
           '    res["runnable"] = res["valid"] and (local or not res["blocks"])',
           '    res["runnable"] = res["valid"]', "outstanding_holds",
           cases=("test_the_budget_counts_outstanding_holds_and_a_missing_rate_blocks_only_paid_runs",)),
    Mutant("e1cp07", "no secret may sit in a profile",
           "runprofile.py",
           "    if isinstance(value, str) and (SECRET_SHAPE.search(value) or carries_key(value, keys)):",
           "    if False:", "no_secret",
           cases=("test_no_secret_may_sit_in_a_profile_and_none_is_echoed",)),
    Mutant("e1cp08", "an active key outside the test keys refuses the run (P-24)",
           "runprofile.py",
           "    foreign = [p for p in prefixes if not any(match(p, a) for a in allowed)]",
           "    foreign = []", "key_inventory",
           cases=("test_a_key_inventory_outside_the_test_keys_refuses_the_run",)),
    Mutant("e1cp09", "P4 must enter through the public edge (S3 F5)",
           "runprofile.py",
           '    if m["profile_class"] == "P4" and target["path"] != "public-edge":',
           "    if False:", "public_edge",
           cases=("test_p4_enters_through_the_public_edge_and_must_observe_refusals",)),
    Mutant("e1cp10", "a P4 cell that saw no 429/503 is INVALID (S3 F5)",
           "bench.py",
           '    if profile["measurement"]["profile_class"] == "P4" and not any(',
           '    if False and not any(', "public_edge",
           cases=("test_p4_enters_through_the_public_edge_and_must_observe_refusals",)),
    Mutant("e1cp11", "no new request after bounds.max_duration_s",
           "bench.py", "    if limit is not None and CLOCK() - t0 > limit:",
           "    if False:", "duration_bound",
           cases=("test_the_declared_duration_bound_stops_new_requests_and_invalidates",)),
    Mutant("e1cp12", "no price evidence is 'unavailable', never zero",
           "bench.py",
           '        return {"status": "unavailable", "usd_per_successful_video_hour": None,',
           '        return {"status": "unavailable", "usd_per_successful_video_hour": 0.0,',
           "section6",
           cases=("test_a_profiled_run_emits_the_section6_fields_and_labels_its_cost",)),
    Mutant("e1cp13", "an over-cap clip is never counted as successful video capacity",
           "bench.py",
           '                   if r["media_sent"] and r["duration_s"] is not None and r["duration_s"] <= cap}',
           '                   if r["media_sent"] and r["duration_s"] is not None}',
           "section6",
           cases=("test_a_profiled_run_emits_the_section6_fields_and_labels_its_cost",)),

    # ---------------- E1C fix round (review findings 0-B1..0-M4, 2-E1C-ACC-01..04)
    Mutant("e1cf01", "open loop: what is in flight after max_drain_s is cancelled (p03)",
           "bench.py", '        await drain(cfg, tasks, cfg.get("max_drain_s"))',
           "        await drain(cfg, tasks, None)", "drain_bound",
           cases=("test_the_drain_bound_cuts_what_is_still_in_flight_in_either_loop",)),
    Mutant("e1cf02", "closed loop: in flight at max_duration_s gets max_drain_s, no more (0-B1)",
           "bench.py",
           '    await drain(cfg, [asyncio.create_task(worker()) for _ in range(cfg["concurrency"])], timeout)',
           '    await drain(cfg, [asyncio.create_task(worker()) for _ in range(cfg["concurrency"])], None)',
           "drain_bound",
           cases=("test_the_drain_bound_cuts_what_is_still_in_flight_in_either_loop",)),
    Mutant("e1cf03", "open loop: no arrival is sent after max_duration_s at run time (p05)",
           "bench.py", "        if past_deadline(cfg, t0):\n            break",
           "        if False:\n            break", "poisson",
           cases=("test_the_duration_bound_holds_at_run_time_when_poisson_arrivals_overshoot_it",)),
    Mutant("e1cf04", "the profile's lag bound is the run's (p10)",
           "bench.py", '        a.max_driver_lag = profile["measurement"]["max_driver_lag_s"]\n', "",
           "identity_and_lag",
           cases=("test_the_profile_identity_and_lag_bound_are_applied_to_the_run",)),
    Mutant("e1cf05", "the profile's model_revision is the run's declared identity (p11)",
           "bench.py", '        a.expect_model = profile["identity"]["model_revision"]\n', "",
           "identity_and_lag",
           cases=("test_the_profile_identity_and_lag_bound_are_applied_to_the_run",)),
    Mutant("e1cf06", "target.path is one of the three hops (p02)",
           "profiles/run-profile.v1.schema.json",
           '"path": {"enum": ["public-edge", "direct-gateway", "direct-engine"],',
           '"path": {"type": "string",', "target_path_must",
           cases=("test_the_declared_target_path_must_be_the_path_the_run_takes",)),
    Mutant("e1cf07", "target.path names the --target the run uses (2-E1C-ACC-03)",
           "runprofile.py", "    if a.target != wants:", "    if False:", "target_path_must",
           cases=("test_the_declared_target_path_must_be_the_path_the_run_takes",)),
    Mutant("e1cf08", "public-edge is TLS on the default port of a non-loopback host (S3 F5)",
           "runprofile.py",
           '    if target["path"] == "public-edge" and (url.scheme != "https" or host in LOOPBACK',
           '    if False and (url.scheme != "https" or host in LOOPBACK', "target_path_must",
           cases=("test_the_declared_target_path_must_be_the_path_the_run_takes",)),
    Mutant("e1cf09", "the schedule's output-token ceiling is bounded (p14)",
           "runprofile.py", '("max_output_tokens", out_tokens, "scheduled output-token ceiling")',
           '("max_output_tokens", 0, "scheduled output-token ceiling")', "every_flag",
           cases=("test_every_flag_must_sit_inside_the_declared_target_workload_and_bounds",)),
    Mutant("e1cf10", "faults on customer traffic need a maintenance window (p15)",
           "runprofile.py",
           '    if m["fault_schedule"] and target["customer_traffic"] and not target["maintenance_window"]:',
           "    if False:", "every_flag",
           cases=("test_every_flag_must_sit_inside_the_declared_target_workload_and_bounds",)),
    Mutant("e1cf11", "the run's key env is declared in target.tenant_key_env (p16)",
           "runprofile.py", '    if not set(used) <= set(target["tenant_key_env"]):',
           "    if False:", "every_flag",
           cases=("test_every_flag_must_sit_inside_the_declared_target_workload_and_bounds",)),
    Mutant("e1cf12", "a paid target without --profile or an opt-out does not start (0-B2)",
           "bench.py", "    if not a.unprofiled:\n        return (\"--base-url is not a local",
           "    if False:\n        return (\"--base-url is not a local", "unprofiled_run",
           cases=("test_an_unprofiled_run_against_a_paid_target_refuses_to_start",)),
    Mutant("e1cf13", "the smoke opt-out is itself bounded",
           "bench.py", "    if (sent > cap[0] or max(a.max_tokens_mix) > cap[1]):",
           "    if False:", "unprofiled_run",
           cases=("test_an_unprofiled_run_against_a_paid_target_refuses_to_start",)),
    Mutant("e1cf14", "§6 counts: fresh_accepted is fresh only (v17)",
           "bench.py", '"fresh_accepted": len(fresh), "replayed": valid["replayed"],',
           '"fresh_accepted": res["accepted"], "replayed": valid["replayed"],',
           "unexpected_replay",
           cases=("test_an_unexpected_replay_invalidates_the_cell_and_never_counts_as_capacity",)),
    Mutant("e1cf15", "output tokens (out_tok_per_s, totals) are over fresh answers (v19)",
           "bench.py", '    out_tokens = sum(r["completion_tokens"] or 0 for r in fresh)',
           '    out_tokens = sum(r["completion_tokens"] or 0 for r in accepted)',
           "unexpected_replay or resume_labels",
           cases=("test_an_unexpected_replay_invalidates_the_cell_and_never_counts_as_capacity",
                  "test_a_resume_labels_its_replays_and_they_do_not_invalidate_it")),
    Mutant("e1cf16", "successful clip-seconds are over fresh answers (v20)",
           "bench.py", '    in_contract = {r["item_key"]: r for r in fresh\n',
           '    in_contract = {r["item_key"]: r for r in finals if r["outcome"] == "accepted"\n',
           "unexpected_replay or resume_labels",
           cases=("test_an_unexpected_replay_invalidates_the_cell_and_never_counts_as_capacity",
                  "test_a_resume_labels_its_replays_and_they_do_not_invalidate_it")),
    *(Mutant(f"e1cf{17 + i}", f"a resume under another {field} is refused (d02/d03)",
             "dataset.py",
             'FINGERPRINT = ("dataset_version", "profile_version", "model", "base_url", "form")',
             "FINGERPRINT = " + repr(tuple(f for f in ("dataset_version", "profile_version", "model",
                                                       "base_url", "form") if f != field)),
             "any_other_run_identity",
             cases=("test_a_resume_under_any_other_run_identity_is_refused_before_any_request",))
      for i, field in enumerate(("base_url", "dataset_version", "form", "profile_version"))),
    Mutant("e1cf21", "output retention has no default (d04)",
           "dataset.py", '    r.add_argument("--retain-output", choices=["text", "digest"], required=True,',
           '    r.add_argument("--retain-output", choices=["text", "digest"], default="text",',
           "retention_must", cases=("test_output_retention_must_be_declared",)),
    Mutant("e1cf22", "the media digest is part of the item key (d09)",
           "dataset.py", '(media_digest or "nomedia")[:16]', '"nomedia"', "edited_video",
           cases=("test_an_edited_video_after_a_lost_ack_is_refused_not_resent_under_its_old_key",)),
    Mutant("e1cf23", "a paid dataset run without a profile does not start (2-E1C-ACC-01)",
           "dataset.py", "        return None if local else [", "        return None if True else [",
           "bounded_by_its_profile",
           cases=("test_a_dataset_run_is_bounded_by_its_profile_and_a_paid_one_needs_it",)),
    Mutant("e1cf24", "a dataset profile that does not bound the manifest refuses the run",
           "dataset.py", '    return verdict["errors"] + ([] if local else verdict["blocks"])',
           "    return None", "bounded_by_its_profile",
           cases=("test_a_dataset_run_is_bounded_by_its_profile_and_a_paid_one_needs_it",)),
    Mutant("e1cf25", "a dataset run takes no item after max_duration_s",
           "dataset.py", "            if limit is not None and time.monotonic() - t0 > limit:",
           "            if False:", "stops_taking_items",
           cases=("test_a_dataset_run_stops_taking_items_at_its_declared_duration",)),
    Mutant("e1cf26", "a dataset run cancels what is in flight after max_drain_s",
           "dataset.py", '                                   else limit + bounds["max_drain_s"])',
           "                                   else None)", "stops_taking_items",
           cases=("test_a_dataset_run_stops_taking_items_at_its_declared_duration",)),
    Mutant("e1cf27", "the certify opt-out is gone: a paid unprofiled certify run does not start",
           "bench.py", 'UNPROFILED_OPT_OUT = {"smoke": (4, 512)}',
           'UNPROFILED_OPT_OUT = {"smoke": (4, 512), "certify": (10**9, 10**9)}',
           "certify_opt_out",
           cases=("test_a_paid_run_needs_a_profile_and_the_certify_opt_out_is_gone",)),
    Mutant("e1cf28", "only a local target (injected transport, loopback) runs without a profile",
           "bench.py", "    if runprofile.is_local(a):\n        return None",
           "    if True:\n        return None", "certify_opt_out or unprofiled_run",
           cases=("test_a_paid_run_needs_a_profile_and_the_certify_opt_out_is_gone",
                  "test_an_unprofiled_run_against_a_paid_target_refuses_to_start")),

    # ---------------- CERTIFY-TREE item 5: the cancelled-replay rule (R106)
    Mutant("e1bm25", "a replay answered state_conflict is terminal: its key is spent",
           "bench.py", '    if row.get("outcome") in ("accepted", CANCELLED_REPLAY):',
           '    if row.get("outcome") in ("accepted",):',
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
    Mutant("e1bm26", "a replay answered state_conflict is classified, not left a failure",
           "bench.py", "        row[\"outcome\"] = CANCELLED_REPLAY\n", "        pass\n",
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
    Mutant("e1bm27", "only a replay's state_conflict is the cancelled replay, never a fresh one's",
           "bench.py", '            "failed", "stream_error_event", "state_conflict", True):',
           '            "failed", "stream_error_event", "state_conflict", row["idempotency_replayed"]):',
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
    Mutant("e1bm28", "only state_conflict is the cancelled replay, never a replay's other error",
           "bench.py", '            "failed", "stream_error_event", "state_conflict", True):',
           '            "failed", "stream_error_event", row["error_code"], True):',
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
    Mutant("e1bm29", "the summary counts the cancelled replays",
           "bench.py", '"cancelled": len(cancelled), CANCELLED_REPLAY: len(cancelled_replays),',
           '"cancelled": len(cancelled), CANCELLED_REPLAY: 0,',
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
    # ---------------- CERTIFY-POLISH N9: the denominators sum to the schedule
    Mutant("e1bm31", "a cancelled replay has its own denominator: the buckets sum to scheduled",
           "bench.py", '                         "cancelled_replay_excluded": len(cancelled_replays),',
           '                         "cancelled_replay_excluded": 0,',
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
    # ---------------- CERTIFY-POLISH N7: only a stream event is the cancelled replay
    Mutant("e1bm30", "only a replay whose STREAM answered state_conflict is the cancelled replay",
           "bench.py", '            "failed", "stream_error_event", "state_conflict", True):',
           '            "failed", row["error_class"], "state_conflict", True):',
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
)


SUMMARY_LINE = re.compile(r"^(?:(?:\d+ [a-z]+(?:, )?)+|no tests ran) in [\d.]+s.*$", re.M)


def run_one(mutant: Mutant) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"infrx-e1b-{mutant.id}-") as tmp:
        # models/marlin2b, not marlin2b: bench.DEFAULT_OUT is derived from the file's own
        # path, and test_historical_cli_still_parses… asserts it ends with
        # models/marlin2b/results/bench.jsonl. A copy one level shallower failed that case in
        # EVERY run, so any mutant whose selector reached it was killed by the copy layout
        # rather than by the edit. Control e1bc03 guards exactly that.
        root = Path(tmp) / "models" / "marlin2b"
        shutil.copytree(TREE, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # E1C: the upload conformance cases mount the REAL router from apps/infrx-api,
        # which is read, never mutated; link it where the copy expects the checkout.
        (Path(tmp) / "apps").symlink_to(TREE.parents[1] / "apps")
        target = root / mutant.path
        source = target.read_text()
        found = source.count(mutant.before)
        if found != mutant.occurrences:
            return {"id": mutant.id, "status": "stale", "invariant": mutant.invariant,
                    "why": f"the mutated text occurs {found} times, expected "
                           f"{mutant.occurrences}: the mutant no longer describes the code"}
        target.write_text(source.replace(mutant.before, mutant.after, mutant.occurrences))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(root / SUITE), "-k", mutant.select,
             "-p", "no:cacheprovider", "--no-header", "-x"],
            cwd=str(root), capture_output=True, text=True, timeout=600,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return verdict(mutant, result.returncode, result.stdout + result.stderr)


def verdict(mutant: Mutant, code: int, output: str) -> dict:
    matches = SUMMARY_LINE.findall(output)
    summary = matches[-1] if matches else ""
    failed = re.search(r"(\d+) failed", summary)
    errors = re.search(r"(\d+) errors?\b", summary)
    nothing_ran = ("no tests ran" in summary
                   or (re.search(r"\d+ deselected", summary)
                       and not re.search(r"\d+ (passed|failed)", summary)))
    detail = {"id": mutant.id, "invariant": mutant.invariant, "exit": code,
              "selected_cases": mutant.cases, "must_survive": mutant.must_survive,
              "summary": summary, "failed": int(failed.group(1)) if failed else 0,
              "errors": int(errors.group(1)) if errors else 0,
              "tail": "\n".join(output.strip().splitlines()[-4:])}
    if nothing_ran:
        return {**detail, "status": "no-cases",
                "why": f"the selector {mutant.select!r} matched nothing: not a kill"}
    if detail["errors"] or (code != 0 and not summary):
        return {**detail, "status": "setup-error",
                "why": "pytest reported an ERROR rather than a failure: the suite could not "
                       "run, so this says nothing about the invariant"}
    killed = code != 0 and detail["failed"] > 0
    if mutant.must_survive:
        return {**detail, "status": "SURVIVED" if not killed else "CONTROL-KILLED",
                "why": None if not killed else
                       "a no-op edit was reported killed: the runner is measuring its own "
                       "setup, so every other kill it reports is worthless"}
    return {**detail, "status": "killed" if killed else "SURVIVED"}


def summarise(results: list[dict]) -> dict:
    bad = [r for r in results
           if (r["status"] != "killed" and not r.get("must_survive"))
           or r["status"] in ("CONTROL-KILLED", "stale", "setup-error", "no-cases")]
    return {"mutants": len(results),
            "killed": sum(1 for r in results if r["status"] == "killed"),
            "controls_survived": sum(1 for r in results
                                     if r.get("must_survive") and r["status"] == "SURVIVED"),
            "not_killed": len(bad), "problems": [r["id"] for r in bad] or None,
            "results": results}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", help="a single mutant id")
    ap.add_argument("--report", type=Path)
    a = ap.parse_args(argv)
    if a.list:
        for m in MUTANTS:
            print(f"{m.id}  {'CONTROL ' if m.must_survive else ''}{m.path}\n        {m.invariant}")
        print(f"\n{len(MUTANTS)} mutants")
        return 0
    wanted = [m for m in MUTANTS if a.only is None or m.id == a.only]
    results = [run_one(m) for m in wanted]
    for r in results:
        print(f"[{r['status']:>13}] {r['id']}  {r['invariant']}", flush=True)
    summary = summarise(results)
    print(json.dumps(summary, indent=2))
    if a.report:
        a.report.write_text(json.dumps(summary, indent=2))
    return 1 if summary["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
