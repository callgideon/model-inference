"""R32 for a migration: single-edit defects the D1 checks must catch.

Each mutant is one edit to a copy of one migration file in a temporary directory. The
runner builds a database from the mutated set and runs the check that claims the
invariant; a mutant that survives means that check proves nothing.

The four categories the task brief names are all here - a dropped REVOKE, a widened
grant, a missing row-check clause and a precision change that alters history - plus
the ones that would quietly break accounting: the wallet import, the new-organization
wallet, the settlement regime of historical usage, the publication marker, the append-
only ledger and the test clock's production barrier.

    uv run --frozen pytest -q tests/d/test_migration_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/d/test_migration_mutants.py
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from infrx.state import migrations

from . import checks, pgharness

SCHEMA = "0003_pilot_durable_schema.sql"
ROLES = "0004_pilot_roles_and_rpcs.sql"
CONSOLE = "0005_console_read_surface.sql"

MUT_DB = f"{pgharness.DATABASE}_mut"
MUT_PRODLIKE_DB = "prodlike_d1_mut"       # deliberately not infrx_*


KILLED = "killed"
SURVIVED = "survived"
APPLY_ERROR = "apply_error"
SETUP_ERROR = "setup_error"


@dataclass(frozen=True)
class Mutant:
    name: str
    file: str
    old: str
    new: str
    scenario: str            # fresh | upgrade | volume | prodlike
    check: str               # the check that must fail
    why: str                 # what would be wrong in production
    #: How many places this ONE conceptual edit touches (the three console RPCs share a
    #: tenant guard). Still a single edit; the count is stated so a stale target is
    #: still an error rather than a silent partial mutation.
    occurrences: int = 1
    #: What this mutant must produce. Almost always a kill by the named check. One mutant
    #: declares `APPLY_ERROR` instead: making the legacy ledger's sign rules VALID means
    #: the migration cannot be applied to a database holding the history the deployed
    #: console can write, and that refusal IS the observable consequence. Declaring it
    #: keeps R40 intact - the runner still never SCORES an apply error as a kill.
    expects: str = "killed"


MUTANTS: tuple[Mutant, ...] = (
    # --- a dropped REVOKE / a widened grant ---------------------------------
    Mutant("organizations_update_not_narrowed", ROLES,
           "grant update (name, slug) on public.organizations to authenticated;",
           "grant update on public.organizations to authenticated;",
           "fresh", "role_matrix",
           "an owner clears their own suspension through the existing update policy"),
    Mutant("rpc_granted_to_authenticated", ROLES,
           "grant execute on function infrx.%I(jsonb) to service_role;",
           "grant execute on function infrx.%I(jsonb) to service_role, authenticated;",
           "fresh", "rpc_boundary",
           "a browser session can call admit, terminalize or grant_credit directly"),
    Mutant("infrx_tables_readable_by_authenticated", ROLES,
           "grant usage on schema infrx to service_role;",
           "grant usage on schema infrx to service_role;\n"
           "grant select on all tables in schema infrx to authenticated;",
           "fresh", "relations_exist",
           "every tenant's jobs, holds and feedback become browser-readable"),
    Mutant("jobs_without_row_level_security", ROLES,
           "alter table infrx.jobs                   enable row level security;",
           "-- mutant: no RLS on jobs",
           "fresh", "relations_exist",
           "a future PostgREST exposure of infrx leaks every tenant's jobs"),
    Mutant("rpc_without_fixed_search_path", ROLES,
           "language plpgsql security definer set search_path = infrx, public, pg_temp "
           "as $body$",
           "language plpgsql security definer as $body$",
           "fresh", "rpc_boundary",
           "a SECURITY DEFINER body resolves names through the caller's search_path"),
    # The platform-role denial is INHERITED from 0001 (which grants only
    # `full_name`/`avatar_url`), so 0004's `revoke update (is_operator)` is a
    # restatement and no edit to a D1 file can break it - dropping that line leaves a
    # surviving mutant, which is why it is not in this list. The invariant is still
    # claimed by the matrix, so the mutant edits the file that actually holds it. The
    # runner works on a temporary copy, so 0001 is never modified in the checkout.
    # r2: 0004 now revokes and re-grants `public.profiles` itself, so the killable edit
    # is 0004's (0001's grant no longer decides anything - which is the point of
    # enumerating the whole surface in one file).
    Mutant("profiles_operator_column_grant_widened", ROLES,
           "grant update (full_name, avatar_url) on public.profiles to authenticated;",
           "grant update on public.profiles to authenticated;",
           "fresh", "role_matrix",
           "a member promotes themselves to platform operator through "
           "profiles_update_self"),

    # --- precision that alters history --------------------------------------
    Mutant("ledger_precision_rounds_history", SCHEMA,
           "  alter column delta_usd type numeric(20,8);",
           "  alter column delta_usd type numeric(20,2);",
           "upgrade", "upgrade_preserved",
           "every historical ledger delta is silently rounded to cents"),
    Mutant("usage_cost_precision_rounds_history", SCHEMA,
           "  alter column cost_usd type numeric(20,8);",
           "  alter column cost_usd type numeric(20,4);",
           "upgrade", "upgrade_preserved",
           "historical usage costs lose their last four digits"),
    Mutant("wallet_money_is_not_the_domain", SCHEMA,
           "  ledger_total numeric(20,8) not null default 0,",
           "  ledger_total numeric(20,2) not null default 0,",
           "fresh", "money_domain",
           "a wallet cannot hold the scale the ledger settles in"),

    # --- the promotional-credit migration itself -----------------------------
    Mutant("wallet_import_starts_everyone_at_zero", SCHEMA,
           "select o.id, coalesce(sum(l.delta_usd), 0)",
           "select o.id, 0",
           "upgrade", "upgrade_preserved",
           "every existing organization loses its promotional balance"),
    Mutant("new_organizations_get_no_wallet", SCHEMA,
           "create trigger organizations_ensure_wallet after insert on public.organizations\n"
           "  for each row execute function infrx.ensure_wallet();",
           "-- mutant: no wallet for a new organization",
           "fresh", "wallets",
           "a new organization has no wallet row, so its first admission cannot lock one"),
    # B10: the previous form of this mutant made 0003 fail to apply (the pilot-regime
    # check refuses a row with no outcome), and the runner scored that as a kill. The
    # reviewer's form applies cleanly and is killed by the check that claims it.
    Mutant("historical_usage_enters_the_settlement_regime", SCHEMA,
           "-- r2: `usage_events_pilot_settlement_idx (id, settlement_version)` was",
           "update public.usage_events set settlement_regime = 'pilot', "
           "outcome = 'completed', settlement_state = 'settled', settlement_version = 1;\n"
           "-- r2: `usage_events_pilot_settlement_idx (id, settlement_version)` was",
           "upgrade", "upgrade_preserved",
           "reconciliation replays historical usage into new debits"),
    Mutant("ledger_history_is_editable", SCHEMA,
           "create trigger credit_ledger_append_only before update or delete "
           "on public.credit_ledger\n  for each row execute function "
           "infrx.forbid_update_delete();",
           "-- mutant: the ledger is not append-only",
           "fresh", "row_constraints",
           "a correction becomes an edit and the audit trail stops being one"),

    # --- missing row-check clauses -------------------------------------------
    Mutant("calibration_fields_may_disagree", SCHEMA,
           "  constraint feedback_calibration_is_one_fact\n"
           "    check ((name = 'calibration_label') = calibration_set\n"
           "           and (name = 'calibration_label') = (rubric_version is not null)),",
           "",
           "fresh", "row_constraints",
           "a customer signal can be smuggled into the calibration set (R43)"),
    Mutant("operator_entry_without_the_marker", SCHEMA,
           "  constraint feedback_operator_is_marked\n"
           "    check (author_role <> 'operator' or by_operator)",
           "  constraint feedback_operator_is_marked check (true)",
           "fresh", "row_constraints",
           "an operator-authored row escapes R41 masking and leaks the principal"),
    Mutant("cause_and_state_may_disagree", SCHEMA,
           "      when 'completed' then state = 'succeeded'",
           "      when 'completed' then true",
           "fresh", "row_constraints",
           "`failed` + `completed` loses a settled debit, and `succeeded` + "
           "`engine_error` is a free success"),
    Mutant("a_debit_on_any_outcome", SCHEMA,
           "  constraint jobs_debit_only_when_settled\n"
           "    check (debit = 0 or (settlement_state is not distinct from 'settled'\n"
           "                         and coalesce(outcome_cause in ('completed','client_cancelled',\n"
           "                                                        'client_disconnected'), false))),",
           "  constraint jobs_debit_only_when_settled check (debit >= 0),",
           "fresh", "row_constraints",
           "our own deadline can charge the customer (R21)"),
    Mutant("zero_day_retention_is_a_policy", SCHEMA,
           "  content_retention_days int not null check (content_retention_days between 1 and 90),",
           "  content_retention_days int not null check (content_retention_days between 0 and 90),",
           "fresh", "row_constraints",
           "\"keep nothing\" is recorded as a retention policy instead of off mode (R43)"),
    Mutant("evaluation_consent_without_full_capture", SCHEMA,
           "  check (not evaluation_consent or trace_mode = 'full')",
           "  check (true)",
           "fresh", "row_constraints",
           "evaluation consent is inferred for an organization that never gave it"),
    Mutant("the_publication_marker_can_be_cleared", SCHEMA,
           "  if old.published and not new.published then",
           "  if false then",
           "fresh", "row_constraints",
           "a published request can be regenerated after loss (02 §6)"),
    Mutant("two_active_attempts_per_phase", SCHEMA,
           "create unique index attempts_one_active_per_kind_idx on infrx.attempts (job_id, kind)\n"
           "  where released_at is null;",
           "create index attempts_one_active_per_kind_idx on infrx.attempts (job_id, kind)\n"
           "  where released_at is null;",
           "fresh", "row_constraints",
           "two workers hold a live lease on one phase of one job"),
    Mutant("two_terminal_journal_events", SCHEMA,
           "create unique index stream_chunks_one_terminal_idx on infrx.stream_chunks (job_id)\n"
           "  where event_type = 'terminal';",
           "create index stream_chunks_one_terminal_idx on infrx.stream_chunks (job_id)\n"
           "  where event_type = 'terminal';",
           "fresh", "row_constraints",
           "a replayed settlement can write a second terminal event (R30)"),
    Mutant("entitlements_deny_everyone_by_default", SCHEMA,
           "  model_ids text[],",
           "  model_ids text[] not null default '{}',",
           "fresh", "entitlements",
           "every organization is entitled to nothing, because null became '{}' (R24)"),

    # --- the console read surface (0005) -------------------------------------
    Mutant("wallet_view_without_a_tenant_predicate", CONSOLE,
           "from infrx.wallets w\n"
           "where public.is_org_member(w.org_id) or public.is_operator() "
           "or public.is_service_client();",
           "from infrx.wallets w;",
           "fresh", "console_read_surface",
           "every tenant's balance is readable by any signed-in user, because the view "
           "reads infrx with its owner's rights"),
    Mutant("operator_audit_readable_by_a_member", CONSOLE,
           "from infrx.audit_entries a\nwhere public.is_operator() "
           "or public.is_service_client();",
           "from infrx.audit_entries a;",
           "fresh", "console_read_surface",
           "a customer reads the operator audit trail, principals and all (R34)"),
    Mutant("calibration_labels_leak_into_feedback", CONSOLE,
           "where not f.calibration_set\n  and (public.is_org_member(f.org_id)",
           "where (public.is_org_member(f.org_id)",
           "fresh", "console_read_surface",
           "operator calibration verdicts appear in a customer's feedback list (R49)"),
    Mutant("judge_runs_readable_by_any_member", CONSOLE,
           "where public.is_org_owner(r.org_id) or public.is_operator() "
           "or public.is_service_client();",
           "where public.is_org_member(r.org_id) or public.is_operator() "
           "or public.is_service_client();",
           "fresh", "console_read_surface",
           "judge runs and their costs are owner and operator only (R13)"),
    Mutant("console_rpcs_answer_for_any_organization", CONSOLE,
           "  if not (public.is_org_member(p_org) or public.is_operator()\n"
           "          or public.is_service_client()) then",
           "  if false then",
           "fresh", "console_read_surface",
           "the wallet summary and both usage aggregates report another tenant's "
           "figures to any signed-in user",
           occurrences=3),
    Mutant("api_keys_update_not_narrowed", ROLES,
           "grant update (name, revoked_at) on public.api_keys to authenticated;",
           "grant update on public.api_keys to authenticated;",
           "fresh", "role_matrix",
           "an owner writes `trace_mode` directly, so a consent change leaves no "
           "consent_history row"),

    # --- bounded access paths ------------------------------------------------
    Mutant("no_pending_outbox_index", SCHEMA,
           "create index outbox_pending_idx on infrx.outbox (available_at, event_id)\n"
           "  where acknowledged_at is null;",
           "-- mutant: no pending index",
           "volume", "index_plans",
           "dispatch scans the whole outbox on every poll"),
    Mutant("no_expiring_lease_index", SCHEMA,
           "create index attempts_expiring_idx on infrx.attempts (expires_at)\n"
           "  where released_at is null;",
           "-- mutant: no expiring-lease index",
           "volume", "index_plans",
           "the lease reaper scans every attempt ever made"),

    # --- the ten the reviewer's corpus left alive at 9d9857b (B9) -------------
    Mutant("views_writable_by_browser_roles", CONSOLE,
           "revoke all on public.wallets, public.console_ledger, public.console_usage,\n"
           "               public.org_settings, public.consent_history, public.feedback,\n"
           "               public.calibration_labels, public.console_judge_runs,\n"
           "               public.console_admin_orgs, public.operator_audit\n"
           "  from public, anon, authenticated;\n",
           "",
           "fresh", "privileges",
           "an owner runs `update public.wallets set ledger_total = 1000000` through a "
           "simple updatable view over infrx - the whole money invariant"),
    Mutant("consent_view_actor_unmasked", CONSOLE,
           "       public.visible_principal(c.org_id, c.actor_principal) as changed_by,",
           "       c.actor_principal as changed_by,",
           "fresh", "console_read_surface",
           "a customer reads the operator who changed their trace consent (R41)"),
    Mutant("feedback_view_principal_unmasked", CONSOLE,
           "       public.visible_principal(f.org_id, f.author_principal) as author_principal,",
           "       f.author_principal,",
           "fresh", "console_read_surface",
           "a customer reads the operator principal off a feedback entry (R41)"),
    Mutant("ledger_actor_masking_keys_on_the_marker", CONSOLE,
           "       public.visible_principal(l.org_id, l.created_by::text,\n"
           "                                coalesce(p.email, l.created_by::text)) as actor,",
           "       case when l.by_operator then 'platform'\n"
           "            else coalesce(p.email, l.created_by::text) end as actor,",
           "fresh", "console_read_surface",
           "the masking fails open again: `by_operator` defaults false for all history "
           "and the deployed console never sets it"),
    Mutant("job_org_mutable", SCHEMA,
           "  if new.request_id is distinct from old.request_id\n"
           "     or new.org_id is distinct from old.org_id\n",
           "  if new.request_id is distinct from old.request_id\n",
           "fresh", "row_constraints",
           "a job can be moved to another tenant after admission"),
    Mutant("consent_deletable", SCHEMA,
           "create trigger consent_history_guard before update or delete on infrx.consent_history",
           "create trigger consent_history_guard before update on infrx.consent_history",
           "fresh", "row_constraints",
           "consent history stops being an audit trail"),
    Mutant("attempts_not_keyed_by_kind", SCHEMA,
           "  primary key (job_id, kind, generation),",
           "  primary key (job_id, generation),",
           "fresh", "row_constraints",
           "R46's two attempt sequences collide: a preparation lease at generation 1 "
           "blocks the inference attempt at generation 1"),
    Mutant("idempotency_key_ignores_operation", SCHEMA,
           "  primary key (org_id, operation, key),",
           "  primary key (org_id, key),",
           "fresh", "row_constraints",
           "one idempotency key cannot be reused across operations, so a feedback "
           "submit collides with a chat completion"),
    Mutant("chunks_not_keyed_by_generation", SCHEMA,
           "  primary key (job_id, generation, sequence),",
           "  primary key (job_id, sequence),",
           "fresh", "row_constraints",
           "a requeued attempt cannot write sequence 1 again, so a replay loses events"),
    Mutant("no_active_holds_by_org_index", SCHEMA,
           "create index credit_holds_org_active_idx on infrx.credit_holds (org_id)\n"
           "  where state in ('held','unknown');",
           "-- mutant: no active-holds index",
           "volume", "index_plans",
           "06's org/active hold path scans every hold ever taken"),
    Mutant("infrx_usage_to_authenticated", ROLES,
           "grant usage on schema infrx to service_role;",
           "grant usage on schema infrx to service_role, authenticated;",
           "fresh", "privileges",
           "the pilot schema becomes reachable from a browser session"),
    Mutant("clock_granted_to_authenticated", ROLES,
           "revoke all on function infrx.now() from public, anon, authenticated;\n"
           "grant execute on function infrx.now() to service_role;",
           "grant execute on function infrx.now() to service_role, authenticated;",
           "fresh", "privileges",
           "a browser session can read the store's clock"),

    # --- r2: the routes the review found ---------------------------------------
    # r3: re-adding the column no longer leaks anything, because SELECT on this table is
    # column-scoped since N2 - so the killable edit is the GRANT, which is what decides
    # whether `created_by` (the operator's uuid, written by the deployed console) is
    # readable at all.
    Mutant("ledger_select_is_table_wide_again", ROLES,
           "grant select (id, org_id, delta_usd, kind, reason, ref, created_at)\n"
           "  on public.credit_ledger to authenticated;",
           "grant select on public.credit_ledger to authenticated;",
           "fresh", "no_operator_identity",
           "`select created_by from public.credit_ledger` returns the operator's user id "
           "to any member"),
    Mutant("truncate_guard_dropped", SCHEMA,
           "    execute format('create trigger %I before truncate on %s for each statement '",
           "    continue; execute format('create trigger %I before truncate on %s for each statement '",
           "fresh", "truncate_refused",
           "`truncate public.credit_ledger cascade` erases the ledger for anyone who "
           "still holds the privilege"),
    Mutant("api_keys_insert_is_table_wide", ROLES,
           "grant insert (org_id, created_by, name, prefix, key_hash) on public.api_keys\n"
           "  to authenticated;",
           "grant insert on public.api_keys to authenticated;",
           "fresh", "role_matrix",
           "an owner sets `trace_mode` - a consent decision - by inserting a key"),
    Mutant("views_without_security_barrier", CONSOLE,
           "create or replace view public.wallets with (security_barrier = true) as",
           "create or replace view public.wallets as",
           "fresh", "leaky_function",
           "a cheap user function in the WHERE clause reads every tenant's balance"),
    Mutant("wallet_total_not_moved_by_the_ledger", SCHEMA,
           "create trigger credit_ledger_moves_wallet after insert on public.credit_ledger\n"
           "  for each row execute function infrx.ledger_moves_wallet();",
           "-- mutant: nothing moves the wallet",
           "fresh", "legacy_drift",
           "the deployed console's addCredit leaves the summary disagreeing with the "
           "ledger, silently"),
    Mutant("consent_revocation_can_be_undone", SCHEMA,
           "  if old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at then",
           "  if old.revoked_at is not null and new.revoked_at <> old.revoked_at then",
           "fresh", "row_constraints",
           "`set revoked_at = null` compares NULL, passes, and un-revokes consent"),
    Mutant("holds_need_not_belong_to_the_job", SCHEMA,
           "  add constraint credit_holds_job_belongs_to_org\n"
           "    foreign key (request_id, org_id) references infrx.jobs (request_id, org_id)\n"
           "    on delete restrict,",
           "",
           "fresh", "row_constraints",
           "one tenant's credit is reserved against another tenant's job"),
    Mutant("ledger_signs_unconstrained", SCHEMA,
           "    check (case kind when 'grant' then delta_usd > 0",
           "    check (true or case kind when 'grant' then delta_usd > 0",
           "fresh", "row_constraints",
           "a `grant` of minus five hundred, or a `usage` that pays the customer"),
    Mutant("usage_pilot_rows_need_no_job", SCHEMA,
           "    if not exists (select 1 from infrx.jobs j\n"
           "                   where j.request_id = new.id and j.org_id = new.org_id) then",
           "    if false then",
           "fresh", "row_constraints",
           "a settled usage row can be written under an organization that never ran it"),
    Mutant("terminal_settlement_is_rewritable", SCHEMA,
           "      raise exception 'job % is terminal: its settlement is immutable', "
           "old.request_id\n        using errcode = '23514';",
           "      null;",
           "fresh", "row_constraints",
           "a settled job's debit and result can be rewritten afterwards (R53)"),
    Mutant("admin_orgs_duplicates_an_org_with_two_owners", CONSOLE,
           "  where m.org_id = o.id and m.role = 'owner'\n"
           "  order by m.created_at, owner_profile.email\n"
           "  limit 1) owner on true",
           "  where m.org_id = o.id and m.role = 'owner') owner on true",
           "fresh", "console_read_surface",
           "an organization with two owners appears twice and C's keyset pagination "
           "skips a page"),
    Mutant("money_leaves_the_views_as_a_number", CONSOLE,
           "select w.org_id, w.ledger_total::text as ledger_total,",
           "select w.org_id, w.ledger_total as ledger_total,",
           "fresh", "console_read_surface",
           "PostgREST renders numeric unquoted and the browser parses it into a double "
           "- money stops being exact (ruling 10)"),
    Mutant("aggregates_hide_their_tenant", CONSOLE,
           "  select u.org_id,\n"
           "         (u.created_at at time zone 'utc')::date as day,",
           "  select null::uuid,\n"
           "         (u.created_at at time zone 'utc')::date as day,",
           "fresh", "console_read_surface",
           "C's tenant check has nothing to check: a row with no org_id passes through "
           "to a DTO unverified"),
    Mutant("usage_daily_is_unbounded", CONSOLE,
           "  order by 2 desc\n  limit 400;",
           "  order by 2 desc;",
           "fresh", "console_read_surface",
           "one call can ask for every day since the epoch"),

    # --- r3: the eighteen the reviewer's round-2 corpus left alive (N5) --------
    Mutant("org_settings_without_a_tenant_predicate", CONSOLE,
           "from infrx.consent_history c\nwhere c.revoked_at is null\n"
           "  and (public.is_org_member(c.org_id) or public.is_operator() "
           "or public.is_service_client())\n",
           "from infrx.consent_history c\nwhere c.revoked_at is null\n",
           "fresh", "console_read_surface",
           "every organization's trace settings are readable by any signed-in user"),
    Mutant("consent_history_view_without_barrier", CONSOLE,
           "create or replace view public.consent_history with (security_barrier = true) as",
           "create or replace view public.consent_history as",
           "fresh", "leaky_function",
           "a cheap function in the WHERE clause reads another tenant's consent trail"),
    Mutant("judge_runs_view_without_barrier", CONSOLE,
           "create or replace view public.console_judge_runs with (security_barrier = true) as",
           "create or replace view public.console_judge_runs as",
           "fresh", "leaky_function",
           "a cheap function in the WHERE clause reads another tenant's judge costs"),
    Mutant("revocation_can_be_re_dated", SCHEMA,
           "  if old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at then",
           "  if old.revoked_at is not null and new.revoked_at is null then",
           "fresh", "row_constraints",
           "a revocation can be moved later, which is a revocation that did not happen "
           "when the customer says it did"),
    Mutant("maximum_hold_is_mutable", SCHEMA,
           "     or new.maximum_hold is distinct from old.maximum_hold\n",
           "",
           "fresh", "row_constraints",
           "the reserved envelope can be raised after admission, so a settlement can "
           "exceed what the customer's balance was checked against (R53)"),
    Mutant("job_key_is_mutable", SCHEMA,
           "     or new.key_id is distinct from old.key_id\n",
           "",
           "fresh", "row_constraints",
           "an admitted job can be re-attributed to another of the tenant's keys, which "
           "is what per-key metering and rate limits are counted on"),
    Mutant("idempotency_feedback_not_composite", SCHEMA,
           "  add constraint idempotency_feedback_belongs_to_org\n"
           "    foreign key (feedback_id, org_id) references infrx.feedback "
           "(feedback_id, org_id)\n    on delete restrict;",
           "  add constraint idempotency_feedback_belongs_to_org\n"
           "    foreign key (feedback_id) references infrx.feedback (feedback_id)\n"
           "    on delete restrict;",
           "fresh", "row_constraints",
           "one tenant's idempotency key replays another tenant's feedback"),
    Mutant("delivery_destination_not_composite", SCHEMA,
           "  foreign key (destination_id, org_id)\n"
           "    references infrx.callback_destinations (destination_id, org_id) "
           "on delete cascade,",
           "  foreign key (destination_id)\n"
           "    references infrx.callback_destinations (destination_id) on delete cascade,",
           "fresh", "row_constraints",
           "one tenant's event is delivered to another tenant's registered endpoint - "
           "cross-tenant egress, not a bookkeeping error"),
    Mutant("ledger_sign_rules_made_valid", SCHEMA,
           "    check (case kind when 'grant' then delta_usd > 0\n"
           "                     when 'purchase' then delta_usd > 0\n"
           "                     when 'usage' then delta_usd < 0\n"
           "                     else true end) not valid,",
           "    check (case kind when 'grant' then delta_usd > 0\n"
           "                     when 'purchase' then delta_usd > 0\n"
           "                     when 'usage' then delta_usd < 0\n"
           "                     else true end),",
           "upgrade", "upgrade_preserved",
           "the migration refuses to apply to a database whose history the deployed "
           "console wrote - or, worse, somebody 'fixes' the history",
           expects=APPLY_ERROR),
    Mutant("pilot_usage_trigger_on_insert_only", SCHEMA,
           "create trigger usage_events_pilot_tenant before insert or update on public.usage_events",
           "create trigger usage_events_pilot_tenant before insert on public.usage_events",
           "fresh", "row_constraints",
           "a legacy row is promoted into the settlement regime by UPDATE, unchecked"),
    Mutant("judge_money_is_a_number", CONSOLE,
           "       r.reserved_cost::text as budget_reserved, r.actual_cost::text as budget_settled,",
           "       r.reserved_cost as budget_reserved, r.actual_cost as budget_settled,",
           "fresh", "console_read_surface",
           "judge budgets reach the browser as JSON numbers and lose precision"),
    Mutant("admin_org_money_is_a_number", CONSOLE,
           "       o.suspension_reason, w.ledger_total::text as ledger_total,\n"
           "       w.reserved_total::text as reserved_total,",
           "       o.suspension_reason, w.ledger_total, w.reserved_total,",
           "fresh", "console_read_surface",
           "the operator's organization list shows balances as JSON numbers"),
    Mutant("pending_reconciliation_counts_every_hold", CONSOLE,
           "         coalesce(sum(u.max_hold::numeric(20,8))\n"
           "                  filter (where u.usage_certainty = 'unknown'\n"
           "                          and u.max_hold is not null), 0)::numeric(20,8)::text,",
           "         coalesce(sum(u.max_hold::numeric(20,8)), 0)::numeric(20,8)::text,",
           "fresh", "console_read_surface",
           "a customer is told credit is pending reconciliation when it is simply held "
           "for a request that is still running"),
    Mutant("platform_absorbed_counts_everything", CONSOLE,
           "         count(*) filter (where u.settlement_state = 'released_platform_absorbed')::bigint",
           "         count(*)::bigint",
           "fresh", "console_read_surface",
           "every request looks like one the platform paid for"),
    Mutant("failed_requests_counts_everything", CONSOLE,
           "         count(*) filter (where u.http_status >= 400)::bigint,",
           "         count(*)::bigint,",
           "fresh", "console_read_surface",
           "the usage page reports a 100% error rate"),
    Mutant("spent_keeps_its_negative_sign", CONSOLE,
           "                    -sum(delta_usd) filter (where delta_usd < 0) as spent",
           "                    sum(delta_usd) filter (where delta_usd < 0) as spent",
           "fresh", "console_read_surface",
           "the credits card renders a negative 'spent'"),
    Mutant("infrx_default_privileges_to_authenticated", ROLES,
           "alter default privileges in schema infrx\n"
           "  grant select, insert, update, delete on tables to service_role;",
           "alter default privileges in schema infrx\n"
           "  grant select, insert, update, delete on tables to service_role, authenticated;",
           "fresh", "function_privileges",
           "every relation D2-D6 adds to the pilot schema is born readable by a browser "
           "session"),
    Mutant("purchase_may_be_negative", SCHEMA,
           "                     when 'purchase' then delta_usd > 0\n",
           "",
           "fresh", "row_constraints",
           "a `purchase` that takes credit away"),

    # --- the database clock --------------------------------------------------
    Mutant("the_clock_offset_works_in_production", SCHEMA,
           "  if current_database() like 'infrx@_%' escape '@' then",
           "  if true then",
           "prodlike", "production_clock",
           "a deployed process can move the store's clock and release "
           "unknown-usage holds early"),
)


def _mutate(directory: Path, mutant: Mutant) -> None:
    for path in migrations.migrations():
        shutil.copy(path, directory / path.name)
    target = directory / mutant.file
    text = target.read_text()
    found = text.count(mutant.old)
    assert found == mutant.occurrences, (
        f"mutant {mutant.name}: its target appears {found} times in {mutant.file}, "
        f"expected {mutant.occurrences}")
    target.write_text(text.replace(mutant.old, mutant.new))


#: The runner self-test (R40/B10): a mutant that deliberately breaks the SQL must be
#: reported as an apply error, never as a kill.
SELF_TEST = Mutant("self_test_broken_sql", SCHEMA,
                   "create table infrx.price_versions (",
                   "create tabl infrx.price_versions (",
                   "fresh", "relations_exist",
                   "(not a defect: the runner must classify this as an apply error)")

_CHECKS = {
    "relations_exist": checks.check_relations_exist,
    "privileges": checks.check_privileges,
    "truncate_refused": checks.check_truncate_refused,
    "leaky_function": checks.check_leaky_function_probe,
    "function_privileges": checks.check_function_privileges,
    "no_operator_identity": checks.check_no_operator_identity_in_public,
    "legacy_drift": checks.check_legacy_writer_does_not_drift,
    "rpc_boundary": checks.check_rpc_boundary,
    "entitlements": checks.check_entitlements_and_limits,
    "money_domain": checks.check_money_domain,
    "wallets": checks.check_wallets_and_reconciliation,
    "role_matrix": checks.check_role_matrix,
    "row_constraints": checks.check_row_constraints,
    "index_plans": checks.check_index_plans,
    "production_clock": checks.check_production_clock,
    "console_read_surface": checks.check_console_read_surface,
    "upgrade_preserved": None,        # needs the captured "before" state
}


#: R40 / B10: a kill is an ASSERTION FAILURE RAISED BY THE NAMED CHECK. Anything else
#: is its own class and is never counted as a kill - the previous runner counted
#: `psycopg.Error` and setup/apply failures, and reported
#: `historical_usage_enters_the_settlement_regime` as "killed by setup: 0003 failed to
#: apply", which is a migration that does not build, not an invariant that is defended.
def kill(mutant: Mutant) -> tuple[str, str]:
    """Build a database from the mutated migrations and run the check that claims the
    invariant. Returns `(outcome, detail)` with `outcome` in KILLED / SURVIVED /
    APPLY_ERROR / SETUP_ERROR."""
    pgharness.ensure()
    with TemporaryDirectory(prefix=f"infrx-d1-{mutant.name}-") as tmp:
        directory = Path(tmp)
        _mutate(directory, mutant)
        files = migrations.sql_for(shim=pgharness.NEEDS_SHIM, directory=directory)
        database = MUT_PRODLIKE_DB if mutant.scenario == "prodlike" else MUT_DB
        current = tuple(f for f in files if f[0] in (
            "supabase_shim.sql", "0001_init.sql", "0002_seed_models.sql"))
        try:
            pgharness.recreate(database)
            if mutant.scenario == "upgrade":
                pgharness.apply(database, current)
            else:
                pgharness.apply(database, files)
        except (AssertionError, psycopg.Error) as broken:
            return APPLY_ERROR, _first_line(broken)
        try:
            with pgharness.connect(database) as conn:
                if mutant.scenario == "upgrade":
                    before = checks.seed_legacy(conn)
                    try:
                        pgharness.apply(database, tuple(f for f in files if f not in current))
                    except (AssertionError, psycopg.Error) as broken:
                        return APPLY_ERROR, _first_line(broken)
                    return _run(checks.check_upgrade_preserved, conn, before)
                if mutant.scenario in ("fresh", "volume"):
                    checks.seed_fixtures(conn)
                if mutant.scenario == "volume":
                    checks.seed_volume(conn)
                return _run(_CHECKS[mutant.check], conn)
        except (AssertionError, psycopg.Error) as during_setup:
            return SETUP_ERROR, _first_line(during_setup)


def _run(check, *args) -> tuple[str, str]:
    """Only an AssertionError from the named check is a kill."""
    try:
        check(*args)
    except AssertionError as failure:
        return KILLED, _first_line(failure)
    except psycopg.Error as wrong_class:
        # The check blew up instead of asserting: that is a broken check, not a
        # defended invariant, and it must be visible as such.
        return SETUP_ERROR, f"the check raised instead of asserting: {_first_line(wrong_class)}"
    return SURVIVED, ""

def _first_line(error: BaseException) -> str:
    return f"{type(error).__name__}: {str(error).strip().splitlines()[0][:160]}"
