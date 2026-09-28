"""R32/R40 for C2-RPC: single-edit defects of `0041_lab_content_refs.sql` (killed by the named
check of `test_c2rpc_content.py` on a database built from the mutated set, needs Docker) and
of `infrx/state/lab_content.py` (killed by the named case of `test_c2rpc_units.py` through the
shared runner, no Docker). The SQL runner is D7's (`code_mutants_d7.kill`).

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_code_mutants_c2rpc.py
"""
from __future__ import annotations

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Runner
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_c2rpc_content as t

FILE = "0041_lab_content_refs.sql"
DB = f"{pgharness.DATABASE}_c2mut"

ROLES = "check_browser_roles_reach_nothing"
BINDS = "check_issue_binds_the_grant_recipient_user_and_expiry"
ORDER = "check_issue_refuses_in_the_contracts_order"
REDEEM = "check_redeem_rechecks_every_rule"
RETAIN = "check_retention_is_rechecked_at_redeem"
HELD = "check_held_is_a_live_redeemable_ref"
DOOR = "check_the_door_is_the_sessions_own_user"
RACE = "check_one_handle_is_issued_once_under_contention"
STONES = "check_tombstones_and_bounds_gate_the_samples"
STORE = "check_the_adapters_compose"


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    # --- DUR-RLS
    _s("c2_service_edits_refs", "    execute format('grant select on infrx.%I to service_role', "
       "t);", "    execute format('grant select, delete on infrx.%I to service_role', t);",
       ROLES, "the platform role deletes refs (or tombstones) around the RPCs"),
    _s("c2_refs_mutable", "    execute format('create or replace trigger %I before update or "
       "delete on infrx.%I '", "    execute format('create or replace trigger %I before "
       "delete on infrx.%I '", ROLES, "an issued ref's expiry is moved after the fact"),
    _s("c2_row_security_off", "    execute format('alter table infrx.%I enable row level "
       "security', t);", "    execute format('alter table infrx.%I disable row level "
       "security', t);", ROLES, "a future browser grant exposes every provider's refs"),
    _s("c2_door_to_anon", "grant execute on function public.lab_content_ref_issue(text, uuid, "
       "text, uuid, text)\n  to authenticated;", "grant execute on function "
       "public.lab_content_ref_issue(text, uuid, text, uuid, text)\n  to authenticated, anon;",
       ROLES, "an anonymous caller probes content refs"),
    # --- the binding
    _s("c2_binds_first_version", "  select g.grant_id, g.version, g.expires_at,",
       "  select g.grant_id, 1, g.expires_at,", BINDS,
       "a ref reports the grant version it was first issued under, not the one in force"),
    _s("c2_ttl_ignored", "  v_expires := least(infrx.now() + make_interval(secs => v_ttl), "
       "r.retained_until,", "  v_expires := least(infrx.now() + interval '1 day', "
       "r.retained_until,", BINDS, "a 300 s ref lives a day"),
    _s("c2_grant_expiry_ignored", "                     r.grant_expires_at);",
       "                     null);", BINDS, "a ref outlives the grant that allowed it"),
    _s("c2_retention_ignored_at_issue", "  v_expires := least(infrx.now() + make_interval(secs "
       "=> v_ttl), r.retained_until,", "  v_expires := least(infrx.now() + make_interval(secs "
       "=> v_ttl), null,", BINDS, "content past its grant's retention is handed out"),
    _s("c2_retention_from_issue", "         j.created_at + make_interval(days => "
       "g.retention_days)", "         infrx.now() + make_interval(days => g.retention_days)",
       BINDS, "retention restarts at every read, so old content never expires"),
    _s("c2_expired_issued", "  if v_expires <= infrx.now() then\n    perform "
       "infrx.refuse('result_expired', 'the content is past its grant''s retention');",
       "  if false then\n    perform infrx.refuse('result_expired', 'the content is past its "
       "grant''s retention');", BINDS, "an already-dead ref is issued (a constraint 500)"),
    _s("c2_categories_not_stored", "    v_request, v_purpose, v_cats, v_expires)",
       "    v_request, v_purpose, array['request_content', 'response_content', 'media'], "
       "v_expires)", BINDS, "a ref carries categories the issuer never asked for"),
    # --- refusal order
    _s("c2_ttl_unbounded", "  if v_ttl is null or v_ttl not between 1 and 900 or "
       "cardinality(v_cats) = 0", "  if v_ttl is null or cardinality(v_cats) = 0", ORDER,
       "a ref is minted for a week"),
    _s("c2_empty_categories", "  if v_ttl is null or v_ttl not between 1 and 900 or "
       "cardinality(v_cats) = 0", "  if v_ttl is null or v_ttl not between 1 and 900",
       ORDER, "a ref with no category is a server error instead of a refusal"),
    _s("c2_any_purpose", "     or v_purpose <> all (array['capture', 'provider_sharing', "
       "'external_judging', 'training'])\n", "", ORDER,
       "an unknown purpose reaches the rights check as a 403 instead of a 400"),
    _s("c2_raw_handle_stored", "     or coalesce(p_args->>'handle_sha256', '') !~ "
       "'^[0-9a-f]{64}$' then", "     then", ORDER,
       "a raw handle or storage path is accepted as a digest (a constraint 500)"),
    _s("c2_ids_uncaught", "  exception when invalid_text_representation or "
       "numeric_value_out_of_range\n                 or invalid_parameter_value then\n    "
       "perform infrx.refuse('invalid_request', 'a content ref names ids, categories and a "
       "ttl');", "  exception when division_by_zero then\n    perform "
       "infrx.refuse('invalid_request', 'a content ref names ids, categories and a ttl');",
       ORDER, "a malformed id is a 500"),
    _s("c2_foreign_grant", "  select * into pinned from infrx.lab_grant_of(p_args->>'grant_ref', "
       "v_provider);", "  select g.* into pinned from infrx.lab_access_grants g where "
       "infrx.lab_grant_ref(g) = p_args->>'grant_ref';", ORDER,
       "a provider mints refs under a grant made to another provider"),
    _s("c2_any_job", "       select 1 from infrx.jobs j where j.org_id = "
       "pinned.grantor_org_id\n          and j.request_id = v_request) then",
       "       select 1 from infrx.jobs j where j.request_id = v_request) then", ORDER,
       "a grant from one organization reads another organization's request"),
    _s("c2_viewer_reads", "                    and m.role in ('developer', 'administrator')\n",
       "", ORDER, "a viewer (aggregate health only) reads customer content"),
    _s("c2_any_member", "     and exists (select 1 from infrx.provider_memberships m\n"
       "                  where m.provider_org_id = p_provider and m.user_id = p_user",
       "     and exists (select 1 from infrx.provider_memberships m\n"
       "                  where m.user_id = p_user", ORDER,
       "a member of another provider reads this provider's content"),
    _s("c2_revoked_member", "                    and (m.revoked_at is null or infrx.now() < "
       "m.revoked_at))", "                    )", ORDER,
       "a removed developer keeps reading content"),
    _s("c2_any_purpose_granted", "     and p_purpose = any(g.purposes)\n", "", ORDER,
       "a training grant is read as a judging permission"),
    _s("c2_any_category_granted", "     and cardinality(p_categories) > 0 and p_categories <@ "
       "g.categories\n", "     and cardinality(p_categories) > 0\n", ORDER,
       "request content reads media or feedback never granted"),
    _s("c2_any_model", "     and j.model_id::text = any(g.model_ids)\n", "", ORDER,
       "a grant for one model reads another model's traffic"),
    _s("c2_reissue_overwrites", "  if c.handle_sha256 is null then\n    perform "
       "infrx.refuse('state_conflict', 'this handle is already issued');",
       "  if false then\n    perform infrx.refuse('state_conflict', 'this handle is already "
       "issued');", ORDER, "a reissued digest answers as if new while the old binding stands"),
    # --- redeem
    _s("c2_redeem_any_user", "     or c.user_id::text is distinct from p_args->>'user_id' then",
       "     then", REDEEM, "a colleague redeems another developer's ref"),
    _s("c2_redeem_any_provider", "  if not found or c.provider_org_id::text is distinct from "
       "p_args->>'provider_org_id'\n", "  if not found\n", REDEEM,
       "another provider redeems a ref it learned"),
    _s("c2_redeem_snapshot", "  select * into r from infrx.lab_content_rights(c.grantor_org_id, "
       "c.provider_org_id,\n                                                c.user_id, "
       "c.request_id, c.purpose,\n                                                "
       "c.categories);\n  if not found then", "  select c.grant_id, 1 as grant_version, null::"
       "timestamptz as grant_expires_at, c.expires_at as retained_until into r;\n  if false "
       "then", REDEEM, "a revoked or narrowed grant keeps serving an issued ref"),
    _s("c2_redeem_after_expiry", "  if infrx.now() >= c.expires_at then\n    perform "
       "infrx.refuse('result_expired', 'the content reference has expired');",
       "  if false then\n    perform infrx.refuse('result_expired', 'the content reference has "
       "expired');", REDEEM, "a ref is redeemed after its TTL"),
    _s("c2_revoked_grant_current", "     and (g.revoked_at is null or infrx.now() < "
       "g.revoked_at)\n", "", REDEEM, "a revoked grant still reads content"),
    _s("c2_first_version_rights", "           order by x.version desc limit 1) g", "           "
       "order by x.version limit 1) g", REDEEM,
       "rights come from the first grant version, so revocation never applies"),
    # --- retention at redeem
    _s("c2_redeem_retention_ignored", "  if infrx.now() >= r.retained_until then\n    perform "
       "infrx.refuse('result_expired', 'the content is past its grant''s retention');\n  end "
       "if;\n  return infrx.lab_content_ref_json(c, least(c.expires_at, r.retained_until),",
       "  if false then\n    perform infrx.refuse('result_expired', 'the content is past its "
       "grant''s retention');\n  end if;\n  return infrx.lab_content_ref_json(c, least("
       "c.expires_at, r.retained_until),", RETAIN,
       "a shortened retention never reaches refs already issued"),
    _s("c2_held_retention_ignored", "       and infrx.now() < c.expires_at and infrx.now() < "
       "r.retained_until))", "       and infrx.now() < c.expires_at))", RETAIN,
       "an out-of-retention ref holds the object from the sweep"),
    # --- held
    _s("c2_held_any_request", "       and c.request_id::text = p_args->>'request_id'\n", "",
       HELD, "one live ref holds every request of the organization"),
    _s("c2_held_any_org", "     where c.grantor_org_id::text = p_args->>'org_id'\n       and",
       "     where", HELD, "another organization's ref holds this one's content"),
    _s("c2_held_after_expiry", "       and infrx.now() < c.expires_at and infrx.now() < "
       "r.retained_until))", "       and infrx.now() < r.retained_until))", HELD,
       "an expired ref keeps the content forever"),
    _s("c2_held_unchecked", "      cross join lateral infrx.lab_content_rights(c.grantor_org_id, "
       "c.provider_org_id,\n                                                  c.user_id, "
       "c.request_id, c.purpose,\n                                                  "
       "c.categories) r", "      cross join lateral (select c.expires_at as retained_until) r",
       HELD, "a revoked grant's ref still holds the object"),
    # --- the door
    _s("c2_door_unflagged", "  perform infrx.require_feature('lab_content');\n", "", DOOR,
       "the launched App's database hands out content refs before the Lab is enabled"),
    _s("c2_door_any_user", "    'handle_sha256', p_handle_sha256, 'user_id', auth.uid(),",
       "    'handle_sha256', p_handle_sha256, 'user_id', (select m.user_id from "
       "infrx.provider_memberships m where m.provider_org_id = p_provider_org_id and m.role = "
       "'administrator' limit 1),", DOOR, "any session issues refs as the provider's admin"),
    _s("c2_door_wide_categories", "    'categories', jsonb_build_array('request_content', "
       "'response_content'), 'ttl_s', 300));", "    'categories', jsonb_build_array("
       "'request_content'), 'ttl_s', 300));", DOOR,
       "a door ref is bound to less than the body it opens (R47)"),
    _s("c2_door_ttl", "'response_content'), 'ttl_s', 300));", "'response_content'), 'ttl_s', "
       "900));", DOOR, "a browser-minted ref lives three times the default"),
    # --- race
    _s("c2_race_duplicates", "handle_sha256 text primary key check (handle_sha256 ~ "
       "'^[0-9a-f]{64}$'),", "handle_sha256 text not null check (handle_sha256 ~ "
       "'^[0-9a-f]{64}$'),", RACE, "two issuers bind one handle twice"),
    # --- tombstones and bounds
    _s("c2_tombstone_resurrects", "    left join infrx.lab_sample_tombstones t\n      on "
       "t.provider_org_id = r.provider_org_id and t.sample_id = s.sample_id",
       "    left join infrx.lab_sample_tombstones t\n      on false", STONES,
       "a revoked or deleted sample is used again"),
    _s("c2_tombstone_other_provider", "      on t.provider_org_id = r.provider_org_id and "
       "t.sample_id = s.sample_id", "      on t.sample_id = s.sample_id", STONES,
       "one provider's tombstone blocks another's dataset"),
    _s("c2_bound_ignored", "     and (t.sample_id is not null or infrx.now() >= "
       "b.content_until)", "     and (t.sample_id is not null)", STONES,
       "trace content past its bound is still selected"),
    _s("c2_bound_rewritable", "  if n > 0 then\n    perform infrx.refuse('idempotency_conflict', "
       "'a sample already has another content bound');", "  if false then\n    perform "
       "infrx.refuse('idempotency_conflict', 'a sample already has another content bound');",
       STONES, "a second bound silently loses, so the caller believes the later one"),
    _s("c2_first_reason_lost", "      on conflict (provider_org_id, sample_id) do nothing\n"
       "      returning sample_id)", "      on conflict (provider_org_id, sample_id) do update "
       "set reason = excluded.reason\n      returning sample_id)", STONES,
       "a later push rewrites why a sample was withdrawn"),
    _s("c2_permitted_ignores_blocked", "   where not b.blocked ? x", "   where b.blocked is not "
       "null", STONES, "the gate reads accessible samples only"),
    _s("c2_bad_reason_500", "  exception when invalid_text_representation or "
       "invalid_parameter_value\n                 or not_null_violation or check_violation or "
       "foreign_key_violation then\n    perform infrx.refuse('invalid_request', 'a tombstone "
       "names the provider, sample ids and '", "  exception when division_by_zero then\n    "
       "perform infrx.refuse('invalid_request', 'a tombstone names the provider, sample ids "
       "and '", STONES, "a malformed tombstone is a 500"),
    # --- the adapters' RPC contract
    _s("c2_json_request_lost", "    'grantor_org_id', c.grantor_org_id, 'request_id', "
       "c.request_id, 'purpose', c.purpose,", "    'grantor_org_id', c.grantor_org_id, "
       "'purpose', c.purpose,", STORE, "C2's RefBinding cannot be read from the answer"),
)
SQL_NAMES = tuple(m.name for m in SQL_MUTANTS)

RUNNER = Runner(name="c2rpc", targets=("tests/d/test_c2rpc_units.py",))
F = "state/lab_content.py"
REFS = "test_refs__send_the_binding_inputs_and_hand_back_a_typed_binding"
GATE = "test_restrictions__send_the_provider_ids_reason_and_bounds"


def _p(name, invariant, old, new, *cases, **kw) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=F, old=old, new=new, cases=cases, **kw)


CODE_MUTANTS = (
    _p("c2_py_user_dropped", "a ref is the server-derived user's",
       '"handle_sha256": handle_sha256, "user_id": user_id,\n            "provider_org_id": '
       'provider_org_id, "grant_ref": grant_ref,', '"handle_sha256": handle_sha256,\n'
       '            "provider_org_id": provider_org_id, "grant_ref": grant_ref,', REFS),
    _p("c2_py_ttl_fixed", "the caller's TTL is sent", '"ttl_s": ttl_s}))', '"ttl_s": 900}))',
       REFS),
    _p("c2_py_categories_dropped", "every category is sent",
       '"categories": [str(c) for c in categories]', '"categories": [str(c) for c in '
       'categories][:1]', REFS),
    _p("c2_py_untyped_binding", "a binding is C2's typed model",
       "    return RefBinding.model_validate(doc)", "    return doc", REFS),
    _p("c2_py_redeem_unscoped", "a redeem names its provider and user",
       '"handle_sha256": handle_sha256, "user_id": user_id,\n            "provider_org_id": '
       'provider_org_id}))', '"handle_sha256": handle_sha256}))', REFS),
    _p("c2_py_held_truthy", "held is the RPC's boolean",
       '"org_id": org_id, "request_id": request_id}))["held"]',
       '"org_id": org_id, "request_id": request_id})) is not None', REFS),
    _p("c2_py_reason_dropped", "a tombstone carries its reason",
       '"sample_ids": list(sample_ids),\n            "reason": reason}', '"sample_ids": '
       'list(sample_ids),\n            "reason": "deleted"}', GATE),
    _p("c2_py_bound_naive", "a bound is sent as an ISO instant",
       '"content_until": at.isoformat()', '"content_until": str(at.date())', GATE),
    _p("c2_py_purpose_fixed", "the gate asks for the caller's purpose",
       '"dataset_ref": dataset_ref,\n            "purpose": purpose})', '"dataset_ref": '
       'dataset_ref,\n            "purpose": "provider_sharing"})', GATE),
)


def kill(mutant) -> tuple[str, str]:
    return d7.kill(mutant, DB, t)


def run_code_mutant(mutant):
    return shared.run_mutant(mutant, RUNNER)
