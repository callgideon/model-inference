"""R32/R40 for D10: single-edit defects of 0019 (and the D10 migrations after it), each the
failure oracle of consumer-v1/01 §D10.d it stands for.

`migration_mutants.py` imports this module on its last line; importing appends these to its
`MUTANTS` and their checks to its `_CHECKS`. Each builds a database from the mutated
migration on the D2 "admission" scenario and runs the named check in process - the D
runner, `assertion_kill` underneath. They are in the default subset (`ALWAYS`).

    uv run --frozen pytest -q tests/d/test_migration_mutants.py -k d10_
"""
from __future__ import annotations

from . import checks_content, checks_ready, pgharness
from . import migration_mutants as _d

READY = _d.READY


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
    _m("d10_protocol_callable_by_browsers", LIFECYCLE,
       "    execute format('grant execute on function %s to service_role', f);",
       "    execute format('grant execute on function %s to service_role, authenticated', f);",
       "content_privileges", "a browser session claims and deletes content"),
)

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
    "content_races": lambda conn: checks_content.check_content_races(pgharness.connect,
                                                                     _d.MUT_DB),
})
_d.MUTANTS = _d.MUTANTS + MIGRATION_MUTANTS
NAMES = tuple(m.name for m in MIGRATION_MUTANTS)
