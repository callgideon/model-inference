"""R32/R40 for D10: single-edit defects of 0019 (and the D10 migrations after it), each the
failure oracle of consumer-v1/01 §D10.d it stands for.

`migration_mutants.py` imports this module on its last line; importing appends these to its
`MUTANTS` and their checks to its `_CHECKS`. Each builds a database from the mutated
migration on the D2 "admission" scenario and runs the named check in process - the D
runner, `assertion_kill` underneath. They are in the default subset (`ALWAYS`).

    uv run --frozen pytest -q tests/d/test_migration_mutants.py -k d10_
"""
from __future__ import annotations

from . import checks_content, checks_followup, checks_reads, checks_ready, pgharness
from . import migration_mutants as _d

READY = _d.READY
#: The D10 follow-up (W5 request 3, G8 V-G8TL-2).
FOLLOWUP = "0022_preparation_refusal_and_flag_writer.sql"


def _m(name, file, old, new, check, why, **kw):
    return _d.Mutant(name, file, old, new, "admission", check, why, **kw)


MIGRATION_MUTANTS = (
    # --- the failure oracles of §D10.d that 0019 carries ----------------------------------
    _m("d10_claim_without_the_marker", READY,
       "     and not exists (select 1 from infrx.job_readiness r where r.job_id = j.request_id) then",
       "     and false then", "ready_marker",
       "remove the ready predicate: a job the previous runtime admitted - no manifest, no "
       "card or capability recheck - is prepared and run (RV-05)"),
    _m("d10_manifest_of_another_tenant", READY,
       "  if not found or c.org_id <> j.org_id or c.kind <> 'source' or c.state = 'deleted' then",
       "  if not found or c.kind <> 'source' or c.state = 'deleted' then", "ready_refusals",
       "bypass the tenant join: a job executes on another organization's object"),
    _m("d10_receipt_rewritable", READY,
       "  if old.received_at is not null\n     and (new.received_at",
       "  if false\n     and (new.received_at", "upload_ticket",
       "omit an upload constraint: one handle comes to name other bytes after its receipt"),
    _m("d10_finalized_without_its_source", READY,
       "        state <> 'finalized' or (source_content_id is not null",
       "        true or (source_content_id is not null", "upload_ticket",
       "omit an upload constraint: a finalized ticket names no content row, so nothing "
       "protects its source from deletion"),
    _m("d10_ticket_writable_by_the_platform", READY,
       "revoke insert, update, delete on infrx.media_uploads from service_role;", "",
       "upload_ticket",
       "the platform role rewrites tickets around the boundary's tenant and window rules"),
    _m("d10_unfinalized_upload_admitted", READY,
       "    if u.state <> 'finalized' then\n      perform infrx.lifecycle_refuse("
       "'upload_not_finalized'",
       "    if false then\n      perform infrx.lifecycle_refuse('upload_not_finalized'",
       "ready_refusals", "a job runs on bytes nobody verified (R99 (a))"),
    _m("d10_expectation_ignored", READY,
       "     and j.rate_card_version is distinct from x->>'rate_card_version' then",
       "     and false then", "ready_refusals",
       "a card this deployment never approved is charged (R69; RV-05's post-commit recheck)"),
    _m("d10_capability_unchecked", READY,
       "  perform infrx.check_pinned_capability(j, p_args->'request');", "  null;",
       "ready_refusals", "an alias that moved admits video to a revision that cannot take it"),
    _m("d10_legacy_capability_unchecked", READY,
       "    if v_cap is null then\n      return;\n    end if;\n  end if;",
       "    return;\n  end if;", "ready_refusals",
       "a legacy USD job runs video on a revision that takes none (capability at ingress only)"),
    _m("d10_retiring_source_admitted", READY,
       "  if c.state = 'tombstoned' then\n    perform infrx.lifecycle_refuse('content_retiring', "
       "'source '",
       "  if false then\n    perform infrx.lifecycle_refuse('content_retiring', 'source '",
       "ready_refusals", "a job is admitted on an object whose delete is already committed"),
    _m("d10_admission_does_not_hold_the_ticket", READY,
       "    select * into u from infrx.media_uploads where org_id = p_org and handle = p_handle\n"
       "      for share;",
       "    select * into u from infrx.media_uploads where org_id = p_org and handle = p_handle;",
       "ready_races", "an abort or completion races an admission on the same ticket"),
    _m("d10_register_outside_the_prefix", READY,
       "  if not exists (select 1 from public.organizations o where o.id::text = "
       "p_identity->>'org_id')\n     or (p_identity->>'location' = 'object_store' and not coalesce(",
       "  if not exists (select 1 from public.organizations o where o.id::text = "
       "p_identity->>'org_id')\n     or (false and not coalesce(", "register_guards",
       "ambiguous ownership is registered (and so becomes deletable) instead of retained"),
    # M6 findings (2026-09-25): a dot segment inside the tenant prefix; the unlocked row.
    _m("d10_register_dot_segment", READY,
       "           or p_identity->>'object_key' ~ '(^|/)\\.\\.?(/|$)') then",
       "           or false) then", "register_guards",
       "a '..' key inside one tenant's prefix is registered and deleted as another's object"),
    _m("d10_content_key_dot_segment_allowed", READY,
       " and object_key !~ '(^|/)\\.\\.?(/|$)'\n", "\n", "register_guards",
       "a writer other than register stores a key that escapes the tenant prefix"),
    _m("d10_content_row_unlocked", _d.LIFECYCLE,
       "  select * into c from infrx.content_objects where content_id = p_id for update;",
       "  select * into c from infrx.content_objects where content_id = p_id;",
       "content_races",
       "a tombstone and an admission read the same row unlocked: a source deleted under a job"),
    _m("d10_upload_expire_early", READY,
       "                where state = 'created' and expires_at <= infrx.now()\n",
       "                where state = 'created' and expires_at <= infrx.now() + interval '1 year'\n",
       "upload_ticket", "the sweep closes tickets still inside their window (review 0-D10-R4)"),
    _m("d10_register_other_bytes", READY,
       "  if (c.digest is not null and p_identity->>'digest' is not null\n"
       "      and c.digest <> p_identity->>'digest')",
       "  if (false)", "register_guards",
       "one key comes to name two sets of bytes, and a delete of one removes the other"),
    _m("d10_tombstoned_key_recreated", READY,
       "  if c.state = 'tombstoned' then\n    perform infrx.lifecycle_refuse('content_retiring',\n"
       "      'the object at this key",
       "  if false then\n    perform infrx.lifecycle_refuse('content_retiring',\n"
       "      'the object at this key", "register_guards",
       "re-create a tombstoned object with the same identity: the pending delete removes it"),
    _m("d10_boundary_callable_by_browsers", READY,
       "    execute format('grant execute on function %s to service_role', f);",
       "    execute format('grant execute on function %s to service_role, authenticated', f);",
       "ready_privileges", "a browser session admits, finalizes or claims directly"),
)

LIFECYCLE = _d.LIFECYCLE
MIGRATION_MUTANTS = MIGRATION_MUTANTS + (
    # --- 0020: the content lifecycle -----------------------------------------------------
    _m("d10_tombstone_skips_the_reference_recheck", LIFECYCLE,
       "  if c.state = 'live' then\n    perform infrx.content_recheck(c, v_now);\n"
       "    update infrx.content_objects set state = 'tombstoned'",
       "  if c.state = 'live' then\n    update infrx.content_objects set state = 'tombstoned'",
       "content_races",
       "skip the live-reference check: a source deleted under a job admitted after the claim"),
    _m("d10_claim_skips_the_reference_check", LIFECYCLE,
       "  if c.state = 'live' then\n    perform infrx.content_recheck(c, v_now);\n  end if;\n"
       "  update infrx.content_objects\n     set claim_generation",
       "  update infrx.content_objects\n     set claim_generation",
       "content_liveness", "a referenced object is claimed for deletion"),
    _m("d10_renewed_claim_deleted", LIFECYCLE,
       "     or c.claim_fence is distinct from (k->>'fence')::int or v_now >= c.claim_expires_at then",
       "     or v_now >= c.claim_expires_at then", "content_protocol",
       "delete a renewed claim: a superseded sweeper tombstones under another's fence"),
    _m("d10_superseded_ack_accepted", LIFECYCLE,
       "  if c.state <> 'tombstoned' or c.claim_generation is distinct from c.generation\n"
       "     or c.claim_fence is distinct from (t->>'fence')::int then",
       "  if c.state <> 'tombstoned' then", "content_protocol",
       "a superseded holder's late acknowledgement finishes a delete it no longer owns"),
    _m("d10_old_generation_ack_retires_the_new", LIFECYCLE,
       "  if c.generation is distinct from (t->>'generation')::int or c.state = 'deleted' then\n"
       "    return infrx.content_doc(c);",
       "  if c.state = 'deleted' then\n    return infrx.content_doc(c);", "content_protocol",
       "a delayed delete acknowledgement retires bytes re-created at the same key"),
    _m("d10_candidates_include_referenced", LIFECYCLE,
       "             and (c.state = 'tombstoned' or infrx.content_referenced(c, v_now) is null)",
       "", "content_liveness", "a collector is offered an object a job still needs"),
    _m("d10_legacy_running_job_unprotected", LIFECYCLE,
       "    when c.location = 'object_store' and exists (\n        select 1 from infrx.jobs j",
       "    when false and exists (\n        select 1 from infrx.jobs j", "content_liveness",
       "restart turns an object the previous runtime's running job uses into a deletable one"),
    _m("d10_open_upload_destination_unprotected", LIFECYCLE,
       "    when c.kind = 'upload_destination' and exists (",
       "    when false and exists (", "content_liveness",
       "a client's bytes are deleted while its upload is still open"),
    _m("d10_retention_recomputed_to_zero", LIFECYCLE,
       "           and (j.settled_at is null\n"
       "                or p_now < j.settled_at + make_interval(secs => coalesce(r.retention_s, 0))))",
       "           and j.settled_at is null)", "content_liveness",
       "a job's sources go the instant it ends, not after the retention its admission captured"),
    _m("d10_scrub_before_the_expiry", LIFECYCLE,
       "                 and (j.settled_at is null or infrx.now() < j.result_expires_at)) then",
       "                 and j.settled_at is null) then", "content_scrub",
       "a result is emptied while the customer is still promised it"),
    _m("d10_running_request_scrubbed", LIFECYCLE,
       "    if not (old.content_scrubbed_at is null and old.settled_at is not null\n",
       "    if not (old.content_scrubbed_at is null\n", "content_scrub",
       "a running job's request text is emptied under the worker that loads it"),
    _m("d10_result_expiry_from_config", LIFECYCLE,
       "  if v_scrubbed is not null or v_expires is null or infrx.now() >= v_expires then",
       "  if v_scrubbed is not null or infrx.now() >= v_settled + interval '1 day' then",
       "content_scrub",
       "infer expiry from current config: an already promised result lifetime moves (RV-11)"),
    _m("d10_scrubbed_result_reads_empty", LIFECYCLE,
       "  if v_scrubbed is not null or v_expires is null or infrx.now() >= v_expires then",
       "  if v_expires is null then", "content_scrub",
       "an expired or scrubbed result answers empty text instead of result_expired"),
    _m("d10_scrub_guard_digest", LIFECYCLE,
       "(new.request_id, new.org_id, new.digest, new.bytes, new.created_at)\n"
       "        is distinct from (old.request_id, old.org_id, old.digest, old.bytes, old.created_at)",
       "(new.request_id, new.org_id, new.bytes, new.created_at)\n"
       "        is distinct from (old.request_id, old.org_id, old.bytes, old.created_at)",
       "content_scrub", "the scrub rewrites a result's digest (review 0-D10-R2)"),
    _m("d10_scrub_guard_bytes", LIFECYCLE,
       "(new.request_id, new.org_id, new.digest, new.bytes, new.created_at)\n"
       "        is distinct from (old.request_id, old.org_id, old.digest, old.bytes, old.created_at)",
       "(new.request_id, new.org_id, new.digest, new.created_at)\n"
       "        is distinct from (old.request_id, old.org_id, old.digest, old.created_at)",
       "content_scrub", "the scrub rewrites a result's size (review 0-D10-R2)"),
    _m("d10_scrub_guard_twice", LIFECYCLE,
       "  if tg_op = 'DELETE' or old.scrubbed_at is not null or new.scrubbed_at is null",
       "  if tg_op = 'DELETE' or new.scrubbed_at is null", "content_scrub",
       "a scrubbed result is stamped again (review 0-D10-R2)"),
    _m("d10_protocol_callable_by_browsers", LIFECYCLE,
       "    execute format('grant execute on function %s to service_role', f);",
       "    execute format('grant execute on function %s to service_role, authenticated', f);",
       "content_privileges", "a browser session claims and deletes content"),
)

READS = _d.READS
MIGRATION_MUTANTS = MIGRATION_MUTANTS + (
    _m("d10_outcome_without_its_expiry", READS,
       "        'reconcile_after', j.reconcile_after,\n        'result_expires_at', j.result_expires_at) end,",
       "        'reconcile_after', j.reconcile_after) end,", "result_expiry_persisted",
       "status and replay cannot state the persisted expiry, so a reader recomputes it"),
    _m("d10_usd_priced_by_spelling", READS,
       "  v_revision := infrx.resolve_usd_revision(r->>'model_revision');",
       "  v_revision := r->>'model_revision';", "usd_resolution",
       "P-22: the labelled and unlabelled spelling price at different rows"),
    _m("d10_discovery_priced_by_spelling", READS,
       "  where pv.model_revision = infrx.resolve_usd_revision(p_model)\n",
       "  where pv.model_revision = p_model\n", "usd_resolution",
       "G7 WR-3a: /v1/models shows a USD price admission would not charge"),
    _m("d10_requested_model_dropped", READS,
       "    jsonb_build_object('requested_model', r->>'model_revision'), v_hold);",
       "    '{}'::jsonb, v_hold);", "usd_resolution",
       "the caller's model string is lost beside the canonical revision"),
    _m("d10_reconcile_race_untyped", READS,
       "  perform pg_advisory_xact_lock(hashtextextended(v_key, 0));\n", "", "reconcile_race",
       "duplicate reconcile audit: a racing replay dies on a raw 23505"),
    _m("d10_consumer_reads_any_tenant", READS,
       "   where v_org is not null and j.org_id = v_org\n", "   where true\n", "consumer_reads",
       "bypass the tenant join: an individual lists other individuals' jobs"),
    _m("d10_consumer_result_past_expiry", READS,
       "  return infrx.read_result(v_org, 'infrx-result:' || p_request_id);",
       "  return (select x.body from infrx.job_results x where x.request_id = p_request_id);",
       "consumer_reads", "an expired or scrubbed result is shown in the App"),
    _m("d10_browser_writes_key_scope", READS,
       "revoke insert (audience, user_id, provider_org_id, endpoint_id),\n"
       "       update (audience, user_id, provider_org_id, endpoint_id)\n"
       "  on public.api_keys from anon, authenticated;",
       "grant insert (audience, user_id, provider_org_id, endpoint_id),\n"
       "       update (audience, user_id, provider_org_id, endpoint_id)\n"
       "  on public.api_keys to authenticated;", "reads_privileges",
       "let a browser write audience/provider scope"),
    _m("d10_runtime_role_reconciles", READS,
       "      'infrx.read_journal(jsonb)', 'infrx.expire_journal(jsonb)', 'infrx.journal_usage()']",
       "      'infrx.read_journal(jsonb)', 'infrx.expire_journal(jsonb)', 'infrx.journal_usage()',\n"
       "      'infrx.reconcile(jsonb)']", "reads_privileges",
       "the runtime login can run an operator money operation"),
    _m("d10_settled_expiry_rewritable", READS,
       "  if old.settled_at is not null\n"
       "     and new.result_expires_at is distinct from old.result_expires_at then",
       "  if false then", "result_expiry_persisted",
       "a settled job's persisted expiry is rewritten: a promised lifetime moves, or live "
       "content becomes deletable early (review 0-D10-R1 / 2-ACI-3)"),
    _m("d10_success_expiry_not_valid", READS,
       "-- and a LATER migration adds the check, validated, only at zero.\n",
       "alter table infrx.jobs add constraint jobs_success_has_result_expiry\n"
       "  check (state <> 'succeeded' or result_expires_at is not null) not valid;\n",
       "legacy_scrub",
       "R116: a NOT VALID success-has-expiry check re-checks every UPDATE of a pre-0018 "
       "success, so its content never scrubs and the sweep retries forever (review 1-RI-1)"),
    _m("d10_consumer_unit_mislabelled", READS,
       "         case j.accounting_regime when 'credit' then 'CREDIT' else 'USD' end,",
       "         'CREDIT',", "consumer_reads",
       "a USD job is shown in the App labelled CREDIT (units are exact and separate)"),
    _m("d10_consumer_limit_unbounded", READS,
       "   limit greatest(1, least(coalesce(p_limit, 50), 100));",
       "   limit greatest(1, coalesce(p_limit, 50));", "consumer_reads",
       "a browser pages an unbounded history in one call"),
    _m("d10_usd_price_retired_row", READS,
       "    and (pv.effective_to is null or pv.effective_to > infrx.now())\n"
       "  order by pv.effective_from desc, pv.captured_at desc limit 1;",
       "  order by pv.effective_from desc, pv.captured_at desc limit 1;", "usd_resolution",
       "discovery shows a retired price admission would not charge"),
    _m("d10_resolve_ignores_served", READS,
       "  return case when v_served then v_revision else p_model end;",
       "  return coalesce(v_revision, p_model);", "usd_resolution",
       "P-22 prices a listing whose deployment is not served publicly"),
    _m("d10_resolve_future_listing", READS,
       "   where l.public_model_id = v_alias and l.effective_at <= infrx.now()",
       "   where l.public_model_id = v_alias", "usd_resolution",
       "P-22 prices a listing version that is not effective yet"),
    _m("d10_resolve_oldest_listing", READS,
       "   order by l.version desc limit 1;\n  return case",
       "   order by l.version limit 1;\n  return case", "usd_resolution",
       "P-22 prices the oldest listing version instead of the current one"),
    _m("d10_runtime_updates_jobs", READS,
       "grant update (updated_at) on infrx.jobs to infrx_runtime;",
       "grant update on infrx.jobs to infrx_runtime;", "reads_privileges",
       "the runtime login rewrites outcomes, request records and expiries"),
    _m("d10_runtime_writes_results", READS,
       "grant update (updated_at) on infrx.jobs to infrx_runtime;",
       "grant update (updated_at) on infrx.jobs to infrx_runtime;\n"
       "grant insert, update on infrx.job_results to infrx_runtime;", "reads_privileges",
       "the runtime login writes result rows around put_result and the scrub guard"),
    _m("d10_freeze_races_the_admission", READS,
       "                   where f.name = p_name for share), false) then",
       "                   where f.name = p_name), false) then", "flag_freeze_race",
       "a regime freeze measures zero in flight while an admission that passed the flag commits"),
    _m("d10_monitor_reads_customer_content", READS,
       "grant select (request_id, state, admitted_at, queued_at, updated_at, deadline_at, "
       "settled_at,\n              outcome_cause, result_expires_at) on infrx.jobs to infrx_monitor;",
       "grant select on infrx.jobs to infrx_monitor;", "reads_privileges",
       "the read-only monitor login reads request records (customer content)"),

    # --- 0022: fail_preparation (W5 request 3) and the flag writer (V-G8TL-2) ------------
    _m("d10_refetch_not_refreshed", FOLLOWUP,
       "  if p_identity->>'origin' = 'written' and c.state = 'live'\n"
       "     and c.digest = p_identity->>'digest' then",
       "  if false then", "refetch_refresh",
       "M6 WR-7: a clip fetched again is collected before its admission (not_found)"),
    _m("d10_refetch_discovered_refreshes", FOLLOWUP,
       "  if p_identity->>'origin' = 'written' and c.state = 'live'",
       "  if c.state = 'live'", "refetch_refresh",
       "a collector's discovery keeps an orphan alive for ever"),
    _m("d10_refetch_shortens", FOLLOWUP,
       "       set eligible_at = greatest(eligible_at, v_now + make_interval(secs => p_grace_s))",
       "       set eligible_at = v_now + make_interval(secs => p_grace_s)", "refetch_refresh",
       "a registration with a shorter grace makes a protected object deletable early"),
    _m("d10_guard_eligibility_earlier", FOLLOWUP,
       "                  and new.eligible_at > old.eligible_at)) then",
       "                  )) then", "refetch_refresh",
       "any writer moves a live object's eligibility earlier (deletable before its grace)"),
    _m("d10_guard_retiring_eligibility", FOLLOWUP,
       "         and not (old.state = 'live' and new.state = 'live'\n"
       "                  and new.eligible_at > old.eligible_at)) then",
       "         and not (new.eligible_at > old.eligible_at)) then", "refetch_refresh",
       "a tombstoned row's eligibility is rewritten under its delete"),
    _m("d10_fail_prep_any_cause", FOLLOWUP,
       "  if v_cause is null or v_cause not in ('invalid_media', 'preparation_failed') then",
       "  if v_cause is null then", "fail_preparation",
       "a preparation worker ends a job with a cause that bills or blames the client"),
    _m("d10_fail_prep_unfenced", FOLLOWUP,
       "  v_refusal := infrx.fence_lease(v_lease, array['preparation'], v_reconcile_s);\n"
       "  if v_refusal is not null then",
       "  v_refusal := null;\n  if v_refusal is not null then", "fail_preparation",
       "a superseded, foreign or lapsed preparation worker ends a job someone else owns"),
    _m("d10_fail_prep_inference_lease", FOLLOWUP,
       "  v_refusal := infrx.fence_lease(v_lease, array['preparation'], v_reconcile_s);",
       "  v_refusal := infrx.fence_lease(v_lease, array['preparation', 'inference'], "
       "v_reconcile_s);", "fail_preparation",
       "an inference lease ends a running job through the preparation door (R46)"),
    _m("d10_fail_prep_no_replay", FOLLOWUP,
       "  if found and j.settled_at is not null and j.proposal = v_mark then",
       "  if false then", "fail_preparation",
       "the worker's retry of a committed end is refused, so it cannot learn it won"),
    _m("d10_fail_prep_replay_any_lease", FOLLOWUP,
       "  if found and j.settled_at is not null and j.proposal = v_mark then",
       "  if found and j.settled_at is not null and j.outcome_cause = v_cause then",
       "fail_preparation", "another worker (or R29's deadline) is answered as the winner"),
    _m("d10_fail_prep_unmarked", FOLLOWUP,
       "  update infrx.jobs set proposal = v_mark where request_id = j.request_id;\n", "",
       "fail_preparation", "the replay key is never written: an identical retry is refused"),
    _m("d10_fail_prep_platform_absorbed", FOLLOWUP,
       "  perform infrx.terminalize_no_usage(j.request_id, v_cause, 'failed', v_reconcile_s);",
       "  perform infrx.terminalize_no_usage(j.request_id, 'platform_error', 'failed', "
       "v_reconcile_s);", "fail_preparation",
       "a permanent refusal is recorded as the platform's fault, not actionable to the client"),
    _m("d10_fail_prep_not_for_the_runtime", FOLLOWUP,
       "grant execute on function infrx.fail_preparation(jsonb) to service_role, infrx_runtime;",
       "grant execute on function infrx.fail_preparation(jsonb) to service_role;",
       "followup_privileges", "the dedicated runtime login cannot end a refused preparation"),
    _m("d10_fail_prep_for_browsers", FOLLOWUP,
       "revoke all on function infrx.fail_preparation(jsonb) from public, anon, authenticated;",
       "grant execute on function infrx.fail_preparation(jsonb) to public;",
       "followup_privileges", "a browser session ends another tenant's job (the revoke "
       "alone is an equivalent mutant: 0004's default privileges already withhold it)"),
    _m("d10_flag_writer_row_lock_only", FOLLOWUP,
       "  lock table infrx.feature_flags in exclusive mode;\n", "", "flag_writer_queue",
       "V-G8TL-1: overlapping admissions starve the freeze (the row UPDATE joins last)"),
    _m("d10_flag_writer_share_row_exclusive", FOLLOWUP,
       "  lock table infrx.feature_flags in exclusive mode;",
       "  lock table infrx.feature_flags in share row exclusive mode;", "flag_writer_queue",
       "a table lock that does not conflict with ROW SHARE: the freeze still starves"),
    _m("d10_flag_writer_starves_under_load", FOLLOWUP,
       "  lock table infrx.feature_flags in exclusive mode;\n", "", "flag_writer_lands",
       "G8's overlapping-lockers probe: the bounded write never lands"),
    _m("d10_flag_writer_always_changed", FOLLOWUP,
       "  return found;", "  return true;", "followup_privileges",
       "the operator's report says it changed a flag that already had the value"),
    _m("d10_flag_writer_unattributed", FOLLOWUP,
       "     set enabled = p_enabled, updated_by = left(p_actor, 200), reason = left(p_reason, 500),",
       "     set enabled = p_enabled,", "followup_privileges",
       "a flag change is not attributed to the operator who made it"),
    _m("d10_flag_writer_for_the_runtime", FOLLOWUP,
       "grant execute on function infrx.set_feature_flag(text, boolean, text, text) to service_role;",
       "grant execute on function infrx.set_feature_flag(text, boolean, text, text) "
       "to service_role, infrx_runtime;", "followup_privileges",
       "the runtime login can switch a regime off (or signup on)"),
    _m("d10_flag_writer_for_browsers", FOLLOWUP,
       "revoke all on function infrx.set_feature_flag(text, boolean, text, text)\n"
       "  from public, anon, authenticated;",
       "grant execute on function infrx.set_feature_flag(text, boolean, text, text)\n"
       "  to authenticated;", "followup_privileges",
       "a browser session flips a feature flag"),
)

_d._CHECKS.update({
    "fail_preparation": checks_followup.check_fail_preparation,
    "refetch_refresh": checks_followup.check_written_reregistration_refreshes,
    "followup_privileges": checks_followup.check_followup_privileges,
    "flag_writer_queue": lambda conn: checks_followup.check_flag_writer_queues_new_readers(
        pgharness.connect, _d.MUT_DB),
    "flag_writer_lands": lambda conn: (
        checks_followup.check_flag_writer_lands_under_overlapping_lockers(
            pgharness.connect, _d.MUT_DB)),
})
_d._CHECKS.update({
    "ready_marker": checks_ready.check_ready_marker,
    "ready_refusals": checks_ready.check_ready_refusals,
    "upload_ticket": checks_ready.check_upload_ticket,
    "ready_privileges": checks_ready.check_ready_privileges,
    "register_guards": checks_ready.check_register_guards,
    "ready_races": lambda conn: checks_ready.check_ready_races(pgharness.connect, _d.MUT_DB),
})
_d._CHECKS.update({
    "content_liveness": checks_content.check_content_liveness,
    "content_protocol": checks_content.check_content_protocol,
    "content_scrub": checks_content.check_content_scrub,
    "content_privileges": checks_content.check_content_privileges,
    "legacy_scrub": checks_content.check_legacy_success_scrub,
    "content_races": lambda conn: checks_content.check_content_races(pgharness.connect,
                                                                     _d.MUT_DB),
})
_d._CHECKS.update({
    "result_expiry_persisted": checks_reads.check_result_expiry_persisted,
    "consumer_reads": checks_reads.check_consumer_reads,
    "usd_resolution": checks_reads.check_usd_resolution,
    "reads_privileges": checks_reads.check_reads_privileges,
    "reconcile_race": lambda conn: checks_reads.check_reconcile_race(pgharness.connect,
                                                                     _d.MUT_DB),
    "flag_freeze_race": lambda conn: checks_reads.check_flag_freeze_race(pgharness.connect,
                                                                         _d.MUT_DB),
})
_d.MUTANTS = _d.MUTANTS + MIGRATION_MUTANTS
NAMES = tuple(m.name for m in MIGRATION_MUTANTS)
