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

from . import checks, checks_credit, pgharness

SCHEMA = "0003_pilot_durable_schema.sql"
ROLES = "0004_pilot_roles_and_rpcs.sql"
CONSOLE = "0005_console_read_surface.sql"
# D1R
CREDIT = "0006_credit_accounting.sql"
REGISTRY = "0007_provider_registry.sql"
SURFACE = "0008_credit_read_surface.sql"
OPS = "0009_operator_seams.sql"
SEED = migrations.SEED_MARLIN.name           # an operator seed, not a migration

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
    scenario: str            # fresh | upgrade | volume | prodlike | credit | upgrade05 | credit_volume
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
    #: r4: a substring the outcome's detail must contain. Without it, ANY apply error
    #: satisfied the one mutant that declares `apply_error` - including a typo in the
    #: mutant's own replacement text.
    expects_detail: str = ""


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
           expects=APPLY_ERROR, expects_detail="credit_ledger_sign_matches_kind"),
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

    # --- r4: the six the round-3 corpus left alive (F1, F2 and four minor) ----
    Mutant("wallet_insert_may_fund_the_row", ROLES,
           "grant insert (org_id, reserved_total) on infrx.wallets to service_role;",
           "grant insert (org_id, reserved_total, ledger_total) on infrx.wallets "
           "to service_role;",
           "fresh", "legacy_drift",
           "a settlement opens a wallet with a balance of its own choosing, and the "
           "ledger never said so"),
    Mutant("pilot_usage_key_tenant_unchecked", SCHEMA,
           "    if new.api_key_id is not null\n"
           "       and not exists (select 1 from public.api_keys k\n"
           "                       where k.id = new.api_key_id and k.org_id = new.org_id) then",
           "    if false then",
           "fresh", "row_constraints",
           "a metered row is attributed to another tenant's key, which is what per-key "
           "metering and rate limits are counted on"),
    # D1R: 0008 re-creates `console_usage` (columns appended), so the live definition -
    # and the edit that can break it - is 0008's; 0005's text is superseded history.
    Mutant("console_usage_joins_any_key", SURFACE,
           "left join public.api_keys k on k.id = e.api_key_id and k.org_id = e.org_id",
           "left join public.api_keys k on k.id = e.api_key_id",
           "fresh", "console_read_surface",
           "the usage page shows another organization's key NAME beside this tenant's "
           "request"),
    Mutant("outbox_tenant_trigger_on_insert_only", SCHEMA,
           "create trigger outbox_aggregate_tenant before insert or update on infrx.outbox",
           "create trigger outbox_aggregate_tenant before insert on infrx.outbox",
           "fresh", "row_constraints",
           "an event is moved to another organization by UPDATE, unchecked"),
    Mutant("ledger_grant_drops_the_description", ROLES,
           "grant select (id, org_id, delta_usd, kind, reason, ref, created_at)",
           "grant select (id, org_id, delta_usd, kind, ref, created_at)",
           "fresh", "role_matrix",
           "the deployed console's ledger query fails outright: it selects `reason`"),
    Mutant("failed_requests_excludes_the_boundary", CONSOLE,
           "         count(*) filter (where u.http_status >= 400)::bigint,",
           "         count(*) filter (where u.http_status > 400)::bigint,",
           "fresh", "console_read_surface",
           "a 400 - the commonest client error - is not counted as a failure"),

    # --- the database clock --------------------------------------------------
    Mutant("the_clock_offset_works_in_production", SCHEMA,
           "  if current_database() like 'infrx@_%' escape '@' then",
           "  if true then",
           "prodlike", "production_clock",
           "a deployed process can move the store's clock and release "
           "unknown-usage holds early"),
)


def _m(name, file, old, new, scenario, check, why, **kw) -> Mutant:
    return Mutant(name, file, old, new, scenario, check, why, **kw)


#: D1R (0006-0008 and the operator seed). One per claimed invariant; each target text is
#: counted, so a stale target is an error rather than a silent partial mutation.
D1R_MUTANTS: tuple[Mutant, ...] = (
    # --- 0006: wallets --------------------------------------------------------------
    _m("d1r_wallet_owner_not_exclusive", CREDIT,
       "    when 'consumer' then owner_user_id is not null and personal_org_id is not null\n"
       "                         and owner_provider_org_id is null",
       "    when 'consumer' then owner_user_id is not null and personal_org_id is not null",
       "credit", "credit_identity", "a consumer wallet is also owned by a provider"),
    _m("d1r_two_wallets_per_individual", CREDIT,
       "create unique index if not exists credit_wallets_one_per_user\n"
       "  on infrx.credit_wallets (owner_user_id) where kind = 'consumer';", "",
       "credit", "credit_identity", "one individual holds two promotional wallets"),
    _m("d1r_two_wallets_per_personal_org", CREDIT,
       "create unique index if not exists credit_wallets_one_per_personal_org\n"
       "  on infrx.credit_wallets (personal_org_id) where kind = 'consumer';", "",
       "credit", "credit_identity", "two individuals' grants fund one shared organization"),
    _m("d1r_wallet_in_usd", CREDIT,
       "  unit text not null default 'CREDIT' check (unit = 'CREDIT'),\n  owner_user_id",
       "  unit text not null default 'CREDIT',\n  owner_user_id",
       "credit", "credit_identity", "a CREDIT wallet is relabelled USD"),
    _m("d1r_wallet_born_with_money", CREDIT,
       "    if new.ledger_total <> 0 or new.reserved_total <> 0 or new.revision <> 0 then",
       "    if false then", "credit", "credit_identity",
       "a wallet is created holding a balance no ledger row explains"),
    _m("d1r_wallet_rebound_to_another_org", CREDIT,
       "     or new.personal_org_id is distinct from old.personal_org_id\n", "",
       "credit", "credit_identity", "an individual's wallet starts funding another org"),
    _m("d1r_provider_wallet_changes_owner", CREDIT,
       "     or new.owner_provider_org_id is distinct from old.owner_provider_org_id\n", "",
       "credit", "registry", "a provider's dev budget moves to another provider"),
    _m("d1r_total_moved_without_ledger", CREDIT,
       "  if pg_trigger_depth() < 2 and (new.ledger_total", "  if false and (new.ledger_total",
       "credit", "credit_identity", "a writer moves the CREDIT total with no ledger row (R59-7)"),
    # --- 0006: ledger -----------------------------------------------------------------
    _m("d1r_ledger_does_not_move_the_wallet", CREDIT,
       "create or replace trigger credit_ledger_moves_wallet after insert on infrx.credit_ledger\n"
       "  for each row execute function infrx.credit_ledger_moves_wallet();", "",
       "credit", "grant", "the ledger says 10000 and the wallet says 0"),
    _m("d1r_ledger_has_a_transfer", CREDIT,
       "                                     'operator_adjustment', 'inference_debit')),",
       "                                     'operator_adjustment', 'inference_debit', "
       "'transfer')),", "credit", "credit_identity",
       "provider or consumer credit moves between wallets (R67)"),
    _m("d1r_signup_grant_any_amount", CREDIT,
       "    when 'signup_grant' then wallet_kind = 'consumer' and amount = 10000.00000000",
       "    when 'signup_grant' then wallet_kind = 'consumer' and amount > 0",
       "credit", "credit_identity", "the promotional grant is not exactly 10000"),
    _m("d1r_signup_grant_to_provider", CREDIT,
       "    when 'signup_grant' then wallet_kind = 'consumer' and amount = 10000.00000000",
       "    when 'signup_grant' then amount = 10000.00000000",
       "credit", "registry", "a provider dev wallet receives a signup grant"),
    _m("d1r_allocation_mints_consumer_credit", CREDIT,
       "    when 'operator_allocation' then wallet_kind = 'provider_dev' and amount > 0",
       "    when 'operator_allocation' then amount > 0",
       "credit", "credit_identity", "an allocation mints consumer credit"),
    _m("d1r_positive_debit", CREDIT,
       "    when 'inference_debit' then amount < 0 and request_id is not null",
       "    when 'inference_debit' then request_id is not null",
       "credit", "credit_identity", "a settlement credits the wallet"),
    _m("d1r_debit_without_request", CREDIT,
       "    when 'inference_debit' then amount < 0 and request_id is not null",
       "    when 'inference_debit' then amount < 0",
       "credit", "credit_identity", "a debit no request explains"),
    _m("d1r_grant_settles_a_request", CREDIT,
       "  constraint credit_ledger_request_only_on_debits\n"
       "    check (kind = 'inference_debit' or request_id is null)\n",
       "  constraint credit_ledger_request_only_on_debits check (true)\n",
       "credit", "credit_identity", "an adjustment masquerades as a request's settlement"),
    _m("d1r_second_signup_row_per_wallet", CREDIT,
       "create unique index if not exists credit_ledger_one_signup_grant_per_wallet\n"
       "  on infrx.credit_ledger (wallet_id) where kind = 'signup_grant';", "",
       "credit", "credit_identity", "a second signup row without an entitlement"),
    _m("d1r_two_debits_per_request", CREDIT,
       "create unique index if not exists credit_ledger_one_debit_per_request\n"
       "  on infrx.credit_ledger (request_id) where kind = 'inference_debit';", "",
       "credit", "credit_admission", "a request is settled twice"),
    _m("d1r_ledger_is_editable", CREDIT,
       "create or replace trigger credit_ledger_append_only before update or delete\n"
       "  on infrx.credit_ledger for each row execute function infrx.forbid_update_delete();",
       "", "credit", "credit_identity", "CREDIT history is rewritten"),
    _m("d1r_holds_truncatable", CREDIT,
       "                           'infrx.signup_entitlements', 'infrx.credit_wallet_holds']",
       "                           'infrx.signup_entitlements']",
       "credit", "credit_identity", "every active reservation vanishes in one statement"),
    _m("d1r_debit_from_another_wallet", CREDIT,
       "    alter table infrx.credit_ledger add constraint credit_ledger_debit_is_the_jobs\n"
       "      foreign key (request_id, wallet_id) references infrx.jobs (request_id, wallet_id)\n"
       "      on delete restrict;\n", "",
       "credit", "credit_admission", "a request is charged to another wallet"),
    # --- 0006: entitlements and the grant ----------------------------------------------
    _m("d1r_grant_key_includes_campaign", CREDIT,
       "  constraint signup_entitlements_pkey primary key (user_id, entitlement),",
       "  constraint signup_entitlements_pkey primary key (user_id, entitlement, "
       "campaign_version),", "credit", "credit_identity",
       "a campaign bump re-opens eligibility (R71)"),
    _m("d1r_grant_into_someone_elses_wallet", CREDIT,
       "  constraint signup_entitlements_own_wallet foreign key (wallet_id, user_id)\n"
       "    references infrx.credit_wallets (wallet_id, owner_user_id) on delete restrict,\n", "",
       "credit", "credit_identity", "an individual's grant lands in another wallet"),
    _m("d1r_grant_without_its_money_row", CREDIT,
       "  constraint signup_entitlements_is_the_grant_row\n"
       "    foreign key (ledger_operation_id, wallet_id, ledger_kind)\n"
       "    references infrx.credit_ledger (operation_id, wallet_id, kind) on delete restrict\n",
       "  constraint signup_entitlements_is_the_grant_row check (true)\n",
       "credit", "credit_identity", "an entitlement whose money is some other movement"),
    _m("d1r_entitlement_editable", CREDIT,
       "create or replace trigger signup_entitlements_immutable before update or delete\n"
       "  on infrx.signup_entitlements for each row execute function "
       "infrx.forbid_update_delete();", "",
       "credit", "credit_identity", "the grant's evidence or campaign is rewritten"),
    _m("d1r_grant_retry_mints_again", CREDIT,
       "  if not exists (select 1 from infrx.signup_entitlements e\n"
       "                 where e.user_id = p_user_id and e.entitlement = "
       "'initial_signup_grant') then",
       "  if true then", "credit", "grant",
       "an auth-callback retry is a second entitlement (CREDIT-GRANT)"),
    _m("d1r_grant_race_arbitrates_one_index", CREDIT,
       "    values ('consumer', p_user_id, v_org)\n    on conflict do nothing;",
       "    values ('consumer', p_user_id, v_org)\n"
       "    on conflict (owner_user_id) where kind = 'consumer' do nothing;",
       "credit", "grant_race",
       "concurrent first logins fail on the personal-org index instead of replaying "
       "(measured: 1 of 8 callers in a legacy-first run)"),
    _m("d1r_grant_binds_a_shared_org", CREDIT,
       "    if (select count(*) from public.org_members m where m.org_id = v_org) > 1 then",
       "    if false then", "credit", "grant",
       "one individual's grant silently funds a multi-member organization"),
    _m("d1r_grant_ignores_its_flag", CREDIT,
       "  perform infrx.require_feature('signup_grant');\n", "", "credit", "grant",
       "the grant mints before the rollout enables it"),
    _m("d1r_grant_without_evidence_check", CREDIT,
       "  if p_user_id is null or length(btrim(coalesce(p_verification_evidence_ref, ''))) = 0 then",
       "  if p_user_id is null then", "credit", "grant",
       "a blank evidence reference is not an invalid_request"),
    _m("d1r_grant_callable_by_browsers", CREDIT,
       "grant execute on function infrx.grant_signup_credit(uuid, text, text, uuid) to "
       "service_role;",
       "grant execute on function infrx.grant_signup_credit(uuid, text, text, uuid) to "
       "service_role, authenticated;", "fresh", "function_privileges",
       "a browser session mints its own grant (the schema USAGE revoke is the second wall)"),
    _m("d1r_service_writes_money_directly", CREDIT,
       "              infrx.signup_entitlements, infrx.credit_wallet_holds\n"
       "  from public, anon, authenticated, service_role;",
       "              infrx.signup_entitlements, infrx.credit_wallet_holds\n"
       "  from public, anon, authenticated;", "credit", "credit_privileges",
       "the platform role inserts ledger rows and holds outside D's operations"),
    _m("d1r_credit_ledger_without_rls", CREDIT,
       "alter table infrx.credit_ledger enable row level security;\n", "",
       "fresh", "relations_exist", "a future exposure of infrx leaks every CREDIT ledger"),
    _m("d1r_operator_cannot_switch_the_rollout", CREDIT,
       "grant update (enabled, updated_by, reason, updated_at) on infrx.feature_flags\n"
       "  to service_role;", "", "credit", "credit_role_matrix",
       "the cutover can only be done by a superuser session, outside the audited path"),
    _m("d1r_missing_flag_row_is_open", CREDIT,
       "  if not coalesce((select f.enabled from infrx.feature_flags f where f.name = p_name), false)",
       "  if not coalesce((select f.enabled from infrx.feature_flags f where f.name = p_name), true)",
       "credit", "fail_closed", "a deleted flag row enables the feature"),
    _m("d1r_rerun_resets_flags", CREDIT,
       "  ('legacy_usd_admission', true, 'migration 0006', 'pre-cutover default')\n"
       "on conflict (name) do nothing;",
       "  ('legacy_usd_admission', true, 'migration 0006', 'pre-cutover default')\n"
       "on conflict (name) do update set enabled = excluded.enabled;",
       "upgrade05", "rerun", "re-applying the migration silently switches a live flag"),
    _m("d1r_signup_enabled_on_apply", CREDIT,
       "  ('signup_grant', false, 'migration 0006', 'applied; not enabled'),",
       "  ('signup_grant', true, 'migration 0006', 'applied; not enabled'),",
       "upgrade05", "flag_defaults", "applying the migration starts minting"),
    _m("d1r_upgrade_imports_usd_as_credit", CREDIT,
       "-- ======================================================= signup entitlements ===",
       "insert into infrx.credit_wallets (kind, owner_user_id, personal_org_id)\n"
       "select 'consumer', o.created_by, o.id from public.organizations o\n"
       "where o.created_by is not null and exists (select 1 from public.credit_ledger l\n"
       "  where l.org_id = o.id) on conflict do nothing;\n"
       "-- ======================================================= signup entitlements ===",
       "upgrade05", "old_regime_preserved",
       "the upgrade turns USD history into CREDIT wallets (CREDIT-UNITS)"),
    # --- 0006: regimes, pins, admission guards ------------------------------------------
    _m("d1r_usd_job_may_carry_pins", CREDIT,
       "          and num_nulls(wallet_id, model_id, requested_model, deployment_revision_id,\n"
       "                        serving_version_id, rate_card_version, policy_version) = 7",
       "          and num_nulls(wallet_id, model_id, requested_model, deployment_revision_id,\n"
       "                        serving_version_id, rate_card_version, policy_version) >= 0",
       "credit", "credit_admission", "a USD job half-pinned to a CREDIT card"),
    _m("d1r_credit_job_may_carry_usd_price", CREDIT,
       "        else price_version is null and price_snapshot is null\n",
       "        else price_snapshot is null\n",
       "credit", "credit_admission", "a CREDIT job also priced in USD"),
    _m("d1r_regime_defaults_silently", CREDIT,
       "  alter column accounting_regime drop default,\n", "",
       "credit", "credit_admission", "a job that names no regime is admitted as USD"),
    _m("d1r_job_repinned_to_the_next_card", CREDIT,
       "     or new.rate_card_version is distinct from old.rate_card_version\n", "",
       "credit", "credit_admission", "a queued job moves to a newly published rate (R78)"),
    _m("d1r_wallet_not_the_admissions", CREDIT,
       "  if w.kind = 'consumer' and w.personal_org_id is distinct from new.org_id then",
       "  if false then", "credit", "credit_admission",
       "one organization's request spends another individual's wallet (R66)"),
    _m("d1r_dev_budget_pays_for_prod", CREDIT,
       "  if w.kind = 'provider_dev' and not exists (",
       "  if false and not exists (", "credit", "credit_admission",
       "internal preview credit pays for public production traffic"),
    _m("d1r_credit_admission_ignores_flag", CREDIT,
       "  perform infrx.require_feature('credit_admission');\n  select * into w",
       "  select * into w", "credit", "fail_closed",
       "CREDIT jobs are admitted before the rollout enables them"),
    _m("d1r_legacy_admission_ignores_cutover", CREDIT,
       "    perform infrx.require_feature('legacy_usd_admission');\n", "",
       "credit", "fail_closed", "old-regime admission continues after the cutover freeze"),
    # --- 0006: holds -----------------------------------------------------------------------
    _m("d1r_hold_on_another_wallet", CREDIT,
       "  constraint credit_wallet_holds_job_fk\n"
       "    foreign key (request_id, org_id, wallet_id, rate_card_version)\n"
       "    references infrx.jobs (request_id, org_id, wallet_id, rate_card_version)\n"
       "    on delete restrict\n",
       "  constraint credit_wallet_holds_job_fk check (true)\n",
       "credit", "credit_admission", "a job reserves someone else's credit"),
    _m("d1r_hold_reserves_nothing", CREDIT,
       "    update infrx.credit_wallets set reserved_total = reserved_total + new.amount,",
       "    update infrx.credit_wallets set reserved_total = reserved_total + 0,",
       "credit", "credit_admission", "a hold beyond the balance is accepted (no 402)"),
    _m("d1r_hold_never_released", CREDIT,
       "    update infrx.credit_wallets set reserved_total = reserved_total - old.amount,",
       "    update infrx.credit_wallets set reserved_total = reserved_total - 0,",
       "credit", "credit_admission", "settled credit stays reserved for ever"),
    _m("d1r_unknown_usage_debited_later", CREDIT,
       "    or (old.state = 'unknown' and new.state = 'released')) then",
       "    or (old.state = 'unknown' and new.state in ('released', 'settled'))) then",
       "credit", "credit_admission", "unknown usage is charged after the fact (02)"),
    _m("d1r_hold_born_settled", CREDIT,
       "    if new.state <> 'held' then", "    if false then",
       "credit", "credit_admission", "a settled hold that never reserved"),
    _m("d1r_hold_resized", CREDIT,
       "     or new.wallet_id is distinct from old.wallet_id or new.amount is distinct from "
       "old.amount\n",
       "     or new.wallet_id is distinct from old.wallet_id\n",
       "credit", "credit_admission", "a hold shrinks after admission"),
    # --- 0006: usage regime --------------------------------------------------------------
    _m("d1r_legacy_row_carries_credit", CREDIT,
       "                                         serving_version_id, deployment_revision_id) = 4",
       "                                         serving_version_id, deployment_revision_id) >= 0",
       "credit", "regime_on_usage", "a USD row also charges CREDIT"),
    _m("d1r_credit_row_carries_usd_cost", CREDIT,
       "          and price_version is null and cost_usd = 0 and settlement_regime = 'pilot' end),",
       "          and price_version is null and settlement_regime = 'pilot' end),",
       "credit", "credit_admission", "one request costs in two units"),
    _m("d1r_settled_at_another_card", CREDIT,
       "      add constraint usage_events_credit_row_is_the_job\n"
       "        foreign key (id, rate_card_version, serving_version_id, deployment_revision_id)\n"
       "        references infrx.jobs (request_id, rate_card_version, serving_version_id,\n"
       "                               deployment_revision_id);",
       "      add constraint usage_events_credit_row_is_the_job check (true);",
       "credit", "credit_rate", "a queued job settles at the newly published card"),
    _m("d1r_gateway_row_needs_a_regime", CREDIT,
       "-- ================================================================ privileges ===",
       "alter table public.usage_events alter column accounting_regime drop default;\n"
       "-- ================================================================ privileges ===",
       "upgrade05", "legacy_read_path", "the deployed gateway's usage inserts start failing"),
    # --- 0007: registry ---------------------------------------------------------------------
    _m("d1r_public_dev_deployment", REGISTRY,
       "    when 'public' then environment = 'prod'\n                       and state in",
       "    when 'public' then true\n                       and state in",
       "credit", "registry", "a dev deployment is publicly listed (R70)"),
    _m("d1r_public_before_validation", REGISTRY,
       "                       and state in ('proposed_public', 'active', 'draining', 'retired')",
       "", "credit", "registry", "an unvalidated revision is public"),
    _m("d1r_private_in_public_state", REGISTRY,
       "    else state in ('draft', 'validating', 'ready_private', 'retired') end),",
       "    else true end),", "credit", "registry", "a private revision serves as active"),
    _m("d1r_deploy_another_providers_serving", REGISTRY,
       "  constraint deployment_revisions_serving_fk foreign key (serving_version_id, "
       "provider_org_id)\n"
       "    references infrx.serving_versions (serving_version_id, provider_org_id) on delete "
       "restrict,",
       "  constraint deployment_revisions_serving_fk foreign key (serving_version_id)\n"
       "    references infrx.serving_versions (serving_version_id) on delete restrict,",
       "credit", "registry", "a provider deploys another provider's model"),
    _m("d1r_deployment_limits_editable", REGISTRY,
       "     or new.max_output_tokens is distinct from old.max_output_tokens\n", "",
       "credit", "registry", "validated limits widen under admitted jobs"),
    _m("d1r_deployment_state_unconstrained", REGISTRY,
       "  if new.state is distinct from old.state and not (",
       "  if false and not (", "credit", "registry", "a retired revision is resurrected"),
    _m("d1r_rate_card_editable", REGISTRY,
       "                           'infrx.rate_card_versions', 'infrx.data_access_policies',\n"
       "                           'infrx.catalog_listings']\n  loop\n"
       "    execute format('create or replace trigger %I before update or delete",
       "                           'infrx.data_access_policies',\n"
       "                           'infrx.catalog_listings']\n  loop\n"
       "    execute format('create or replace trigger %I before update or delete",
       "credit", "registry", "an admitted card is re-priced (CREDIT-RATE)"),
    _m("d1r_rate_card_any_meter", REGISTRY,
       "  meter text not null default 'tokens-v1' check (meter = 'tokens-v1'),",
       "  meter text not null default 'tokens-v1',", "credit", "registry",
       "an arbitrary billing formula is admissible"),
    _m("d1r_rate_card_unapproved", REGISTRY,
       "  status text not null default 'approved' check (status = 'approved'),",
       "  status text not null default 'approved',", "credit", "registry",
       "a proposed card prices traffic"),
    _m("d1r_negative_rate", REGISTRY,
       "  input_rate_per_million numeric(20,8) not null check (input_rate_per_million >= 0),",
       "  input_rate_per_million numeric(20,8) not null,", "credit", "registry",
       "a debit becomes a credit at settlement"),
    _m("d1r_listing_of_private_deployment", REGISTRY,
       "  constraint catalog_listings_public_fk foreign key (deployment_revision_id, visibility)\n"
       "    references infrx.deployment_revisions (deployment_revision_id, visibility)\n"
       "    on delete restrict\n",
       "  constraint catalog_listings_public_fk check (true)\n",
       "credit", "registry", "a private dev artifact appears in the catalog"),
    _m("d1r_listing_priced_by_another_card", REGISTRY,
       "    foreign key (rate_card_version, deployment_revision_id, serving_version_id, model_id)\n"
       "    references infrx.rate_card_versions\n"
       "      (rate_card_version, deployment_revision_id, serving_version_id, model_id)\n"
       "    on delete restrict,\n  constraint catalog_listings_public_fk",
       "    foreign key (rate_card_version) references infrx.rate_card_versions\n"
       "    on delete restrict,\n  constraint catalog_listings_public_fk",
       "credit", "registry", "an alias is priced by another deployment's card"),
    _m("d1r_listing_under_any_alias", REGISTRY,
       "  constraint catalog_listings_model_fk foreign key (public_model_id, model_id)\n"
       "    references public.models (id, model_uuid) on update restrict on delete restrict,",
       "  constraint catalog_listings_model_fk foreign key (public_model_id)\n"
       "    references public.models (id) on update restrict on delete restrict,",
       "credit", "registry", "one model's deployment is sold under another alias"),
    _m("d1r_shard_digest_unchecked", REGISTRY,
       "    and array_to_string(weight_shard_digests, ',')\n"
       "        ~ '^sha256:[0-9a-f]{64}(,sha256:[0-9a-f]{64})*$'),",
       "    and true),", "credit", "registry", "a malformed weight digest pins nothing"),
    _m("d1r_digest_provenance_free_text", REGISTRY,
       "    check (digest_source in ('served_bytes', 'registry_oid', 'registry_oid_confirmed')),",
       "    ,", "credit", "registry", "provenance is asserted, not recorded (R76)"),
    _m("d1r_revision_label_ambiguous", REGISTRY,
       "  constraint serving_versions_label_key unique (model_id, revision_label),\n", "",
       "credit", "registry", "one R62 pin names two serving versions"),
    _m("d1r_capability_any_meter", REGISTRY,
       "    and capability->>'billing_meter' = 'tokens-v1'\n", "\n",
       "credit", "registry", "a serving version bills by an unknown meter"),
    _m("d1r_serving_version_editable", REGISTRY,
       "  foreach r in array array['infrx.provider_orgs', 'infrx.model_versions',\n"
       "                           'infrx.serving_versions', 'infrx.endpoints',",
       "  foreach r in array array['infrx.provider_orgs', 'infrx.model_versions',\n"
       "                           'infrx.endpoints',", "credit", "registry",
       "a pinned serving version changes under admitted jobs"),
    _m("d1r_two_current_memberships", REGISTRY,
       "create unique index if not exists provider_memberships_one_current\n"
       "  on infrx.provider_memberships (provider_org_id, user_id) where revoked_at is null;",
       "", "credit", "registry", "a member holds two roles at once"),
    _m("d1r_membership_promoted_in_place", REGISTRY,
       "     or new.user_id is distinct from old.user_id or new.role is distinct from old.role\n",
       "     or new.user_id is distinct from old.user_id\n",
       "credit", "registry", "a role change leaves no revocation trail"),
    _m("d1r_membership_unrevoked", REGISTRY,
       "     or (old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at) then",
       "     then", "credit", "registry", "a revoked provider role comes back"),
    _m("d1r_provider_role_is_a_consumer_role", REGISTRY,
       "  role text not null check (role in ('viewer', 'developer', 'administrator')),",
       "  role text not null,", "credit", "registry",
       "an owner role leaks into the provider vocabulary"),
    _m("d1r_two_dev_wallets_per_provider", CREDIT,
       "create unique index if not exists credit_wallets_one_per_provider\n"
       "  on infrx.credit_wallets (owner_provider_org_id) where kind = 'provider_dev';", "",
       "credit", "registry", "a provider doubles its internal budget"),
    _m("d1r_dev_wallet_for_nobody", REGISTRY,
       "    alter table infrx.credit_wallets add constraint credit_wallets_provider_fk\n"
       "      foreign key (owner_provider_org_id) references infrx.provider_orgs on delete "
       "restrict;",
       "    null;", "credit", "registry", "a dev wallet owned by no provider"),
    _m("d1r_job_pins_not_one_card", REGISTRY,
       "      add constraint jobs_pins_are_one_card\n"
       "        foreign key (rate_card_version, deployment_revision_id, serving_version_id, "
       "model_id)\n"
       "        references infrx.rate_card_versions\n"
       "          (rate_card_version, deployment_revision_id, serving_version_id, model_id)\n"
       "        on delete restrict,",
       "      add constraint jobs_pins_are_one_card check (true),",
       "credit", "credit_admission", "a job pins a card that prices another deployment (R69)"),
    _m("d1r_job_pins_unknown_policy", REGISTRY,
       "      add constraint jobs_policy_version_fk foreign key (policy_version)\n"
       "        references infrx.data_access_policies on delete restrict;",
       "      add constraint jobs_policy_version_fk check (true);",
       "credit", "credit_admission", "a job pins a policy nobody recorded"),
    _m("d1r_provider_renamed", REGISTRY,
       "  foreach r in array array['infrx.provider_orgs', 'infrx.model_versions',\n"
       "                           'infrx.serving_versions', 'infrx.endpoints',\n"
       "                           'infrx.rate_card_versions'",
       "  foreach r in array array['infrx.model_versions',\n"
       "                           'infrx.serving_versions', 'infrx.endpoints',\n"
       "                           'infrx.rate_card_versions'",
       "credit", "registry", "an ownership row is edited in place"),
    # --- 0008: resolution and read surface --------------------------------------------------
    _m("d1r_resolve_serves_retired", SURFACE,
       "  if d.state <> 'active' or d.visibility <> 'public' then", "  if false then",
       "credit", "resolve_pins", "a retired or draining deployment admits new work"),
    _m("d1r_resolve_serves_unpriced", SURFACE,
       "  if c.effective_at > v_now then", "  if false then",
       "credit", "resolve_pins", "a card not yet in force prices traffic (R69)"),
    _m("d1r_resolve_ignores_flag", SURFACE,
       "  perform infrx.require_feature('credit_admission');\n  if p_model", "  if p_model",
       "credit", "fail_closed", "pins resolve before CREDIT admission is enabled"),
    _m("d1r_resolve_callable_by_browsers", SURFACE,
       "grant execute on function infrx.resolve_admission_pins(text) to service_role;",
       "grant execute on function infrx.resolve_admission_pins(text) to service_role, "
       "authenticated;", "fresh", "function_privileges",
       "a browser enumerates deployments and rates through the admission resolver"),
    _m("d1r_wallet_page_shows_every_wallet", SURFACE,
       "left join infrx.signup_entitlements e on e.wallet_id = w.wallet_id\n"
       "where (w.kind = 'consumer' and w.owner_user_id = auth.uid())",
       "left join infrx.signup_entitlements e on e.wallet_id = w.wallet_id\n"
       "where (w.kind = 'consumer')", "credit", "credit_read_surface",
       "an individual reads every other individual's balance"),
    _m("d1r_ledger_page_shows_every_ledger", SURFACE,
       "join infrx.credit_wallets w on w.wallet_id = l.wallet_id\n"
       "where (w.kind = 'consumer' and w.owner_user_id = auth.uid())",
       "join infrx.credit_wallets w on w.wallet_id = l.wallet_id\n"
       "where (w.kind = 'consumer')", "credit", "credit_read_surface",
       "an individual reads every other individual's ledger"),
    _m("d1r_ledger_page_without_barrier", SURFACE,
       "create or replace view public.console_credit_ledger with (security_barrier = true) as",
       "create or replace view public.console_credit_ledger as",
       "credit", "credit_leaky_probe", "a cheap caller function reads other ledgers (R59-5)"),
    _m("d1r_ledger_actor_unmasked", SURFACE,
       "       public.visible_principal(w.personal_org_id, l.actor) as actor",
       "       l.actor as actor", "credit", "credit_read_surface",
       "the operator's identity reaches a customer (R59-1)"),
    _m("d1r_money_leaves_as_a_number", SURFACE,
       "       l.amount::text as amount, l.unit,", "       l.amount as amount, l.unit,",
       "credit", "credit_read_surface", "the browser parses CREDIT into a double"),
    _m("d1r_summary_answers_for_anyone", SURFACE,
       "  if not (p_user = auth.uid() or public.is_operator() or public.is_service_client()) then",
       "  if false then", "credit", "credit_read_surface",
       "another user's id answers an empty wallet instead of 42501"),
    _m("d1r_legacy_balance_never_held", SURFACE,
       "         coalesce(sum(l.delta_usd), 0) <> 0\n", "         false\n",
       "credit", "credit_read_surface", "a nonzero USD balance is silently not a rollout hold"),
    _m("d1r_new_views_keep_default_acl", SURFACE,
       "revoke all on public.console_credit_wallets, public.console_credit_ledger\n"
       "  from public, anon, authenticated;\n", "",
       "fresh", "privileges", "anon and members may write through the new views"),
    _m("d1r_summary_callable_by_anon", SURFACE,
       "grant execute on function public.console_wallet_summary(uuid) to authenticated, "
       "service_role;",
       "grant execute on function public.console_wallet_summary(uuid) to anon, authenticated, "
       "service_role;",
       "fresh", "function_privileges", "Supabase's default grant keeps anon executing it"),
    _m("d1r_regrants_a_legacy_view", SURFACE,
       "-- `console_usage` keeps 0005's grants: `create or replace view` keeps the ACL.",
       "grant select on public.console_usage to anon;", "upgrade05", "legacy_schema_unchanged",
       "D1R widens a 0005 object's ACL"),
    _m("d1r_seam_renamed", SURFACE,
       "               reserved_total text, available text, revision bigint,\n"
       "               signup_granted_at timestamptz)",
       "               reserved_total text, available text, revision bigint,\n"
       "               granted_at timestamptz)", "credit", "seams",
       "C0 reads a column the database no longer has"),
    # --- 0009: headless operator seams ---------------------------------------------------------
    _m("d1r_audit_action_open", OPS,
       "                        'admin_adjust'));", "                        'admin_adjust', "
       "'admin_bogus'));", "credit", "operator_seams", "an unaudited action name is recorded"),
    _m("d1r_audit_replays_twice", OPS,
       "create unique index if not exists audit_entries_idempotency_key_idx\n"
       "  on infrx.audit_entries (idempotency_key) where idempotency_key is not null;", "",
       "credit", "operator_seams", "a retried operator action is audited twice"),
    _m("d1r_provider_key_unscoped", OPS,
       "        when 'provider_dev' then provider_org_id is not null and endpoint_id is not null",
       "        when 'provider_dev' then provider_org_id is not null",
       "credit", "operator_seams", "a preview credential reaches every endpoint"),
    _m("d1r_key_scoped_to_foreign_endpoint", OPS,
       "      add constraint api_keys_endpoint_fk foreign key (endpoint_id, provider_org_id)\n"
       "        references infrx.endpoints (endpoint_id, provider_org_id) on delete restrict,",
       "      add constraint api_keys_endpoint_fk foreign key (endpoint_id)\n"
       "        references infrx.endpoints (endpoint_id) on delete restrict,",
       "credit", "operator_seams", "a provider's key is scoped to another provider's endpoint"),
    _m("d1r_consumer_key_for_nobody", OPS,
       "    if new.audience = 'consumer' and new.user_id is null then\n"
       "      raise exception", "    if false then\n      raise exception",
       "credit", "operator_seams", "a consumer key resolves no individual's wallet"),
    _m("d1r_key_unrevoked", OPS,
       "     or (old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at) then\n"
       "    raise exception 'api key", "     then\n    raise exception 'api key",
       "credit", "operator_seams", "a leaked, revoked key comes back"),
    _m("d1r_two_operator_keys", OPS,
       "create unique index if not exists api_keys_one_active_operator\n"
       "  on public.api_keys ((true)) where audience = 'operator' and revoked_at is null;", "",
       "credit", "operator_seams", "a second operator credential exists unaudited"),
    _m("d1r_bootstrap_takes_plaintext", OPS,
       "  if p_key_hash !~ '^[0-9a-f]{64}$' then", "  if false then",
       "credit", "operator_seams", "a plaintext key is stored as its own hash"),
    _m("d1r_suspension_replays_act_twice", OPS,
       "  if exists (select 1 from infrx.audit_entries where idempotency_key = "
       "p_idempotency_key) then", "  if false then",
       "credit", "operator_seams", "a retried suspend/unsuspend flips the organization"),
    _m("d1r_unverified_reads_verified", OPS,
       "         case when to_jsonb(u)->>'email_confirmed_at' is not null\n"
       "              then 'email_confirmed_at/' || (to_jsonb(u)->>'email_confirmed_at') end",
       "         'email_confirmed_at/' || coalesce(to_jsonb(u)->>'email_confirmed_at', 'none')",
       "credit", "operator_seams",
       "an unverified user is handed verification evidence (A1 mints)"),
    _m("d1r_usage_unit_mislabelled", OPS,
       "         case e.accounting_regime when 'credit' then 'CREDIT' else 'USD' end,",
       "         'CREDIT',", "credit", "operator_seams", "a USD row is labelled CREDIT"),
    # --- plans at 10^5 rows per tenant (slow; full run only) -----------------------------------
    _m("d1r_no_ledger_keyset_index", CREDIT,
       "create index if not exists credit_ledger_wallet_created_idx\n"
       "  on infrx.credit_ledger (wallet_id, created_at desc, entry_id desc);", "",
       "credit_volume", "credit_plans", "every ledger page scans and sorts the tenant"),
    # --- the operator seed (not a migration; the runner swaps in a mutated copy) --------------
    _m("d1r_seed_card_not_provisional", SEED,
       "        400.00000000, 1200.00000000, '2026-09-01T00:00:00Z', 'provisional - P-01 pending',\n"
       "        true)",
       "        400.00000000, 1200.00000000, '2026-09-01T00:00:00Z', 'provisional - P-01 pending',\n"
       "        false)", "credit", "seed_is_the_fixtures",
       "an unapproved price is indistinguishable from the launch price (P-01)"),
)

MUTANTS = MUTANTS + D1R_MUTANTS


def _mutate(directory: Path, mutant: Mutant) -> None:
    for path in (*migrations.migrations(), migrations.SEED_MARLIN):
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
    # D1R, scenario "credit" (fresh + D1 fixture + CREDIT fixture).
    "credit_identity": checks_credit.check_credit_identity,
    "grant": checks_credit.check_grant,
    "registry": checks_credit.check_registry,
    "credit_admission": checks_credit.check_credit_admission_rows,
    "credit_rate": checks_credit.check_credit_rate,
    "regime_on_usage": checks_credit.check_regime_on_usage,
    "fail_closed": checks_credit.check_fail_closed,
    "credit_role_matrix": checks_credit.check_credit_role_matrix,
    "credit_privileges": checks_credit.check_credit_privileges,
    "resolve_pins": checks_credit.check_resolve_pins,
    "credit_read_surface": checks_credit.check_credit_read_surface,
    "credit_leaky_probe": checks_credit.check_credit_leaky_probe,
    "seams": checks_credit.check_seams,
    "grant_race": lambda conn: checks_credit.check_grant_race(pgharness.connect, MUT_DB),
    "operator_seams": checks_credit.check_operator_seams,
    "seed_is_the_fixtures": checks_credit.check_seed_is_the_fixtures,
    "credit_plans": checks_credit.check_credit_plans,       # scenario "credit_volume"
    # D1R, scenario "upgrade05": need the captured state (see `_upgrade05_check`).
    "legacy_schema_unchanged": None,
    "old_regime_preserved": None,
    "legacy_read_path": None,
    "flag_defaults": None,
    "rerun": None,
}


def _upgrade05_check(name: str, conn, before: dict, database: str, d1r):
    """The upgrade-from-0005 checks, bound to their captured state."""
    return {
        "legacy_schema_unchanged": lambda: checks_credit.check_legacy_schema_unchanged(
            conn, before["inventory"]),
        "old_regime_preserved": lambda: checks_credit.check_old_regime_preserved(conn, before),
        "legacy_read_path": lambda: checks_credit.check_legacy_read_path(conn, before),
        "flag_defaults": lambda: checks_credit.check_flag_defaults(conn),
        "rerun": lambda: checks_credit.check_rerun_is_noop(
            conn, lambda: pgharness.apply(database, d1r)),
    }[name]


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
        if mutant.scenario in ("credit", "upgrade05", "credit_volume"):
            seed, migrations.SEED_MARLIN = migrations.SEED_MARLIN, directory / SEED
            try:
                return _kill_d1r(mutant, files)
            finally:
                migrations.SEED_MARLIN = seed
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


def _kill_d1r(mutant: Mutant, files) -> tuple[str, str]:
    """D1R's scenarios. Same classification as `kill`: an apply failure is APPLY_ERROR, a
    fixture that cannot be built is SETUP_ERROR, only the named check's assertion kills."""
    if mutant.scenario == "upgrade05":
        try:
            conn, before = checks_credit.upgrade05(pgharness, MUT_DB, files)
        except AssertionError as broken:
            return (APPLY_ERROR if "failed to apply" in str(broken) else SETUP_ERROR,
                    _first_line(broken))
        except psycopg.Error as broken:
            return SETUP_ERROR, _first_line(broken)
        with conn:
            _, d1r = checks_credit.split(files)
            return _run(_upgrade05_check(mutant.check, conn, before, MUT_DB, d1r))
    try:
        pgharness.recreate(MUT_DB)
        pgharness.apply(MUT_DB, files)
    except (AssertionError, psycopg.Error) as broken:
        return APPLY_ERROR, _first_line(broken)
    try:
        with pgharness.connect(MUT_DB) as conn:
            checks.seed_fixtures(conn)
            checks_credit.seed_credit(conn)
            if mutant.scenario == "credit_volume":
                checks_credit.seed_credit_volume(conn)
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
