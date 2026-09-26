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

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from infrx.state import migrations

from ..contracts import mutants as shared
from ..contracts.mutants import Outcome
from . import (checks, checks_admission, checks_credit, checks_dispatch, checks_leases,
               checks_media, pgharness)

SCHEMA = "0003_pilot_durable_schema.sql"
ROLES = "0004_pilot_roles_and_rpcs.sql"
CONSOLE = "0005_console_read_surface.sql"
# D1R
CREDIT = "0006_credit_accounting.sql"
REGISTRY = "0007_provider_registry.sql"
SURFACE = "0008_credit_read_surface.sql"
OPS = "0009_operator_seams.sql"
# D2
MEDIA = "0010_media_uploads.sql"
ADMISSION = "0011_admission.sql"
DISPATCH = "0012_dispatch_outbox.sql"
GC = "0013_outbox_gc.sql"
RESULTS = "0014_job_results.sql"
# D10
READY = "0019_upload_readiness.sql"
LIFECYCLE = "0020_content_lifecycle.sql"
READS = "0021_read_authority.sql"
FENCED_RESULT = "0026_fenced_result.sql"     # D10-0026: redefines 0014's put_result (R147)
SEED = migrations.SEED_MARLIN.name           # an operator seed, not a migration

MUT_DB = f"{pgharness.DATABASE}_mut"
MUT_PRODLIKE_DB = "prodlike_d1_mut"       # deliberately not infrx_*


# F2R item 9: the outcome vocabulary and the kill rule are the shared ones
# (`tests/contracts/mutants.py`). This list cannot use the shared *pytest* runner - a
# mutant here is an edit to SQL, proved by building a database and calling one check in
# process - but what a result MEANS is the same contract, so `Outcome` and
# `shared.assertion_kill` come from there rather than from a second copy of the rules.
# `apply_error` and `setup_error` are this track's two refinements of
# `Outcome.broken_runner`: they name which half of the build failed, which a pytest
# target has no analogue for. Neither is ever scored as a kill.
KILLED = Outcome.killed.value
SURVIVED = Outcome.survived.value
APPLY_ERROR = "apply_error"
SETUP_ERROR = "setup_error"
#: Everything that is not a kill and not a survivor: `Outcome.broken_runner`, refined.
BROKEN = (APPLY_ERROR, SETUP_ERROR)


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
    # D10: 0021 redefines `require_feature` (the flag read FOR SHARE, G8's request).
    _m("d1r_missing_flag_row_is_open", READS,
       "                   where f.name = p_name for share), false) then",
       "                   where f.name = p_name for share), true) then",
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
    # D10: 0021 replaces 0006's constraint with `jobs_regime_fixes_provenance_v2`
    # (requested_model may be carried verbatim by a USD job, P-22); the pins stay refused.
    _m("d1r_usd_job_may_carry_pins", READS,
       "          and num_nulls(wallet_id, model_id, deployment_revision_id, serving_version_id,\n"
       "                        rate_card_version, policy_version) = 6",
       "          and num_nulls(wallet_id, model_id, deployment_revision_id, serving_version_id,\n"
       "                        rate_card_version, policy_version) >= 0",
       "credit", "credit_admission", "a USD job half-pinned to a CREDIT card"),
    _m("d1r_credit_job_may_carry_usd_price", READS,
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
       "    if w.personal_org_id is distinct from j.org_id then",
       "    if false then", "credit", "credit_admission",
       "one organization's request spends another individual's wallet (R66)"),
    _m("d1r_dev_budget_pays_for_prod", CREDIT,
       "                     and d.environment = 'dev') then",
       "                     and d.environment = 'dev') and false then", "credit",
       "credit_admission", "internal preview credit pays for public production traffic"),
    # D2 (D1R review (a)): R70 and the provider_dev key are database invariants.
    # D1R review (b): a unit-mixing body and a narrowed CREDIT column.
    _m("d2_usd_statement_reads_credit", SURFACE,
       "  from public.credit_ledger l where l.org_id = p_org;",
       "  from public.credit_ledger l where l.org_id = p_org\n"
       "    and not exists (select 1 from infrx.credit_wallets);",
       "credit", "no_unit_conversion",
       "a USD statement consults CREDIT wallets - the first step to a combined total"),
    _m("d2_credit_ledger_rounds_to_cents", CREDIT,
       "  amount numeric(20,8) not null check (amount <> 0),",
       "  amount numeric(20,2) not null check (amount <> 0),",
       "credit", "money_unit_cases", "every CREDIT ledger amount is rounded to 1e-2"),
    _m("d2_consumer_spends_on_a_private_deployment", CREDIT,
       "                     and d.visibility = 'public' and d.state = 'active') then",
       "                     and true) then", "credit", "credit_admission",
       "a consumer wallet pays for a private dev or retired deployment (R70)"),
    # (Equivalent, so not listed: dropping `k.audience = 'provider_dev'` alone - 0009's
    # `api_keys_audience_identity` lets only a provider_dev key carry a provider_org_id,
    # so the provider match below already refuses a consumer or operator key.)
    _m("d2_provider_job_through_another_providers_key", CREDIT,
       "\n                     and k.provider_org_id = w.owner_provider_org_id) then",
       ") then", "credit", "credit_admission",
       "one provider's preview key spends another provider's budget (M5)"),
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


#: D2 (0010-0014, scenario "admission": every migration, the frozen clock and
#: `checks_admission.seed_admission`). One per claimed invariant.
_DEADLINE_BLOCK = ("  if v_now >= j.preparation_deadline_at then\n"
                   "    perform infrx.terminalize_unstarted(j.request_id, 'preparation_failed');\n"
                   "    return jsonb_build_object('refusal', jsonb_build_object('code', "
                   "'already_terminal',\n"
                   "      'detail', 'job ' || j.request_id || ' passed its preparation deadline'));\n"
                   "  end if;\n")
D2_MUTANTS: tuple[Mutant, ...] = (
    # --- 0011: admission --------------------------------------------------------------
    _m("d2_replay_ignores_the_payload", ADMISSION,
       "  if i.payload_digest <> p_idem->>'payload_hash' then", "  if false then",
       "admission", "admission_idempotency", "a changed body replays another request's job"),
    _m("d2_tombstone_expires_a_tick_late", ADMISSION,
       "  if v_expires is not null and p_now >= v_expires then",
       "  if v_expires is not null and p_now > v_expires then",
       "admission", "admission_idempotency", "a key replays past its 24 h tombstone"),
    _m("d2_tombstone_runs_from_admission", ADMISSION,
       "  select coalesce(i.expires_at, j.settled_at + make_interval(secs => p_ttl_s))",
       "  select coalesce(i.expires_at, j.admitted_at + make_interval(secs => p_ttl_s))",
       "admission", "admission_idempotency",
       "a long job's retry mints a second billable job (01: active mappings never expire)"),
    # D10: 0021 redefines the legacy body; the CREDIT body's copy stays in 0011.
    _m("d2_readmits_a_request_uuid", READS,
       "  if exists (select 1 from infrx.jobs where request_id = (r->>'request_id')::uuid) then",
       "  if false then", "admission", "admission_idempotency",
       "R6: a retry without its key is a 500, not a typed 409"),
    _m("d2_readmits_a_request_uuid_credit", ADMISSION,
       "'credit');\n  if v_replay is not null then\n    return v_replay;\n  end if;\n"
       "  if exists (select 1 from infrx.jobs where request_id = (r->>'request_id')::uuid) then",
       "'credit');\n  if v_replay is not null then\n    return v_replay;\n  end if;\n"
       "  if false then", "admission", "admission_idempotency",
       "R6: a CREDIT retry without its key is a 500, not a typed 409"),
    _m("d2_revoked_key_admitted", ADMISSION,
       "   where k.id = v_key and k.org_id = v_org and k.revoked_at is null",
       "   where k.id = v_key and k.org_id = v_org",
       "admission", "admission_refusals", "a revoked key buys one more billable job"),
    _m("d2_foreign_key_admitted", ADMISSION,
       "   where k.id = v_key and k.org_id = v_org and k.revoked_at is null",
       "   where k.id = v_key and k.revoked_at is null",
       "admission", "admission_refusals", "another organization's key admits into this one"),
    _m("d2_suspension_ignored", ADMISSION, "  if v_suspended then", "  if false then",
       "admission", "admission_refusals", "a suspended organization keeps admitting (R33)"),
    _m("d2_entitlement_ignored", ADMISSION,
       "  if not infrx.is_entitled(v_org, p_model) then", "  if false then",
       "admission", "admission_refusals", "a withdrawn model keeps being served (R10)"),
    _m("d2_deadline_at_the_store_clock_admitted", ADMISSION,
       "  if v_deadline is null or v_deadline <= p_now then",
       "  if v_deadline is null or v_deadline < p_now then",
       "admission", "admission_refusals", "a job nothing may ever run is accepted (R29)"),
    _m("d2_r29_ceiling_on_gateway_clock", ADMISSION,
       "  v_ceiling := p_now + make_interval(",
       "  v_ceiling := (p_request->>'created_at')::timestamptz + make_interval(",
       "admission", "admission_accepts",
       "a gateway clock running ahead buys a horizon the store cannot keep (R29, R-3)"),
    _m("d2_r29_refusal_on_gateway_clock", ADMISSION,
       "  if v_deadline is null or v_deadline <= p_now then",
       "  if v_deadline is null or v_deadline <= (p_request->>'created_at')::timestamptz then",
       "admission", "admission_refusals",
       "a deadline already past on the store clock is admitted because the gateway says not"),
    _m("d2_deadline_not_clamped", ADMISSION,
       "  v_deadline := least(v_deadline, v_ceiling);", "  v_deadline := v_deadline;",
       "admission", "admission_accepts",
       "a caller's far horizon pins a hold and capacity for as long as it likes (R79)"),
    _m("d2_output_ceiling_unbounded", ADMISSION,
       "  if v_out is null or v_out < 1 or v_out > (p_limits->>'max_output_tokens')::int then",
       "  if v_out is null or v_out < 1 then",
       "admission", "admission_refusals", "a request past MAX_OUTPUT_TOKENS is admitted (R55)"),
    _m("d2_zero_input_ceiling_admitted", ADMISSION,
       "  if v_in is null or v_in < 1 then", "  if v_in is null then",
       "admission", "admission_refusals", "a zero input ceiling reaches the table as a 500"),
    _m("d2_context_unbounded", ADMISSION,
       "  if v_in + v_out > (p_limits->>'max_context_tokens')::int then", "  if false then",
       "admission", "admission_refusals", "a reserved envelope the model cannot keep (R55)"),
    _m("d2_total_capacity_unbounded", ADMISSION,
       "  if v_count >= (p_limits->>'max_active_jobs')::int then", "  if false then",
       "admission", "admission_capacity", "the platform-wide cap is oversubscribed"),
    _m("d2_org_capacity_unbounded", ADMISSION,
       "  if v_count >= (p_limits->>'max_active_jobs_per_org')::int then", "  if false then",
       "admission", "admission_capacity", "one organization takes every slot"),
    _m("d2_key_capacity_unbounded", ADMISSION,
       "  if v_count >= (p_limits->>'max_active_jobs_per_key')::int then", "  if false then",
       "admission", "admission_capacity", "one key takes every slot"),
    _m("d2_preparation_capacity_unbounded", ADMISSION,
       "  if v_count >= (p_limits->>'max_preparing_jobs')::int then", "  if false then",
       "admission", "admission_capacity", "preparation is oversubscribed (R1)"),
    _m("d2_journal_budget_unbounded", ADMISSION,
       "  if infrx.journal_bytes_charged() + v_reserve > (p_limits->>'journal_total_bytes')"
       "::bigint then", "  if false then",
       "admission", "admission_capacity", "the journal disk is oversubscribed"),
    _m("d2_admission_takes_another_tenants_media", ADMISSION,
       "             where (m->>'org_id')::uuid is distinct from v_org) then",
       "             where false) then",
       "admission", "admission_refusals", "a request carries another tenant's media (R55)"),
    _m("d2_withdrawn_price_still_charged", READS,  # D10: 0021 redefines `admit_legacy_usd` (P-22)
      
       "     and (pv.effective_to is null or pv.effective_to > v_now)", "",
       "admission", "admission_refusals", "a withdrawn price keeps admitting (R45)"),
    _m("d2_hold_rounds_to_nearest", ADMISSION,
       "  select (ceil((p_in::numeric * p_in_rate + p_out::numeric * p_out_rate) * 100)",
       "  select (round((p_in::numeric * p_in_rate + p_out::numeric * p_out_rate) * 100)",
       "admission", "admission_accepts", "a hold smaller than the reserved envelope (R53, §4)"),
    _m("d2_usd_balance_unchecked", READS,  # D10: 0021 redefines `admit_legacy_usd` (P-22)
      
       "  if v_hold > coalesce(v_available, 0) then", "  if false then",
       "admission", "admission_refusals", "a hold past the available balance (DUR-CAP)"),
    _m("d2_usd_hold_reserves_nothing", READS,  # D10: 0021 redefines `admit_legacy_usd` (P-22)
      
       "  update infrx.wallets set reserved_total = reserved_total + v_hold, "
       "revision = revision + 1,",
       "  update infrx.wallets set reserved_total = reserved_total + 0, "
       "revision = revision + 1,",
       "admission", "admission_accepts", "two jobs reserve the same credit"),
    _m("d2_usd_hold_row_missing", READS,  # D10: 0021 redefines `admit_legacy_usd` (P-22)
      
       "  values (j.request_id, j.org_id, j.key_id, v_hold, 'held', v_now, v_now);",
       "  select j.request_id, j.org_id, j.key_id, v_hold, 'held', v_now, v_now where false;",
       "admission", "admission_accepts", "an acceptance without its hold (DUR-ADMIT)"),
    _m("d2_admission_without_prepare_dispatch", ADMISSION,
       "  values (gen_random_uuid(), v_id, v_org, 'prepare_dispatch',",
       "  values (gen_random_uuid(), v_id, v_org, 'inference_dispatch',",
       "admission", "admission_accepts", "an accepted job nothing ever dispatches"),
    _m("d2_admission_without_mapping", ADMISSION,
       "  if p_idem->>'key' is not null then\n    insert into infrx.idempotency",
       "  if false then\n    insert into infrx.idempotency",
       "admission", "admission_accepts", "a retried request becomes a second billable job"),
    _m("d2_credit_wallet_reached_through_any_org", ADMISSION,
       "  if w.personal_org_id is distinct from (r->>'org_id')::uuid then", "  if false then",
       "admission", "admission_refusals",
       "an individual's wallet is spent through another organization (R66)"),
    _m("d2_zero_credit_hold_written", ADMISSION,
       "  if v_hold <= 0 then\n    perform infrx.refuse('invalid_request', 'a zero hold would",
       "  if false then\n    perform infrx.refuse('invalid_request', 'a zero hold would",
       "admission", "admission_refusals", "a zero hold meters nothing (D1R review (d))"),
    # --- review SEC-3: nobody executes a helper -------------------------------------------
    _m("helpers_executable_by_service_role", ADMISSION,
       "    execute format('revoke all on function %s from public, anon, authenticated, "
       "service_role',", "    execute format('revoke all on function %s from public, anon, "
       "authenticated',", "admission", "d2_function_privileges",
       "the platform role can call admit_credit with a caller-chosen regime or skip admit's "
       "checks by calling a helper"),
    _m("dispatch_helpers_executable_by_service_role", DISPATCH,
       "    execute format('revoke all on function %s from public, anon, authenticated, "
       "service_role',", "    execute format('revoke all on function %s from public, anon, "
       "authenticated',", "admission", "d2_function_privileges",
       "the platform role can terminalize or release holds outside any fence"),
    _m("credit_guard_executable_by_service_role", CREDIT,
       "revoke all on function infrx.jobs_credit_admission_guard(infrx.jobs)\n"
       "  from public, anon, authenticated, service_role;",
       "revoke all on function infrx.jobs_credit_admission_guard(infrx.jobs)\n"
       "  from public, anon, authenticated;", "admission", "d2_function_privileges",
       "the platform role probes wallets through the admission guard"),
    # --- the review's fold-ins (M2-M7) ------------------------------------------------
    _m("d2_usd_hold_checked_against_the_ledger", READS,  # D10: 0021 redefines `admit_legacy_usd` (P-22)
      
       "  select w.available into v_available from infrx.wallets w",
       "  select w.ledger_total into v_available from infrx.wallets w",
       "admission", "admission_refusals",
       "two holds reserve the same USD credit (DUR-CAP: available, not the ledger)"),
    _m("d2_zero_usd_hold_written", READS,  # D10: 0021 redefines `admit_legacy_usd` (P-22)
      
       "  if v_hold <= 0 then\n    perform infrx.refuse('invalid_request', 'a zero USD hold",
       "  if false then\n    perform infrx.refuse('invalid_request', 'a zero USD hold",
       "admission", "admission_refusals", "a zero USD hold meters nothing (M3)"),
    _m("d2_usd_operator_key_admitted", READS,  # D10: 0021 redefines `admit_legacy_usd` (P-22)
       "  if v_audience = 'operator' then",
       "  if false then", "admission", "admission_refusals",
       "an operator credential buys USD inference (M4)"),
    _m("d2_usd_provider_key_admitted", READS,  # D10: 0021 redefines `admit_legacy_usd` (P-22)
       "  if v_audience <> 'consumer' then",
       "  if false then", "admission", "admission_refusals",
       "a provider preview key buys public USD inference (M4)"),
    _m("d2_wallet_of_the_keys_creator", ADMISSION,
       "  select a.audience, coalesce(a.user_id, a.created_by) as user_id into k",
       "  select a.audience, coalesce(a.created_by, a.user_id) as user_id into k",
       "admission", "admission_accepts", "a key's creator pays for its individual (M6, R66)"),
    _m("d2_replay_across_regimes", ADMISSION,
       "  if v_regime is distinct from p_regime then", "  if false then",
       "admission", "admission_idempotency", "a CREDIT caller is answered a USD job (M7)"),
    _m("d2_usd_hold_equal_to_available_refused", READS,  # D10: 0021 redefines `admit_legacy_usd` (P-22)
      
       "  if v_hold > coalesce(v_available, 0) then", "  if v_hold >= coalesce(v_available, 0) then",
       "admission", "admission_accepts", "the last affordable USD request is refused (MC-3)"),
    _m("d2_usd_hold_loosened_one_unit", READS,  # D10: 0021 redefines `admit_legacy_usd` (P-22)
      
       "  if v_hold > coalesce(v_available, 0) then",
       "  if v_hold > coalesce(v_available, 0) + 0.00000001 then",
       "admission", "admission_refusals",
       "a USD hold one unit past the available balance is admitted (MC-3b)"),
    _m("d2_credit_hold_equal_to_available_refused", ADMISSION,
       "  if v_hold > w.available then", "  if v_hold >= w.available then",
       "admission", "admission_accepts", "the last affordable CREDIT request is refused (MC-3)"),
    _m("d2_credit_provider_key_admitted", ADMISSION, "  if k.audience <> 'consumer' then",
       "  if false then", "admission", "admission_refusals",
       "a provider_dev key spends its creator's consumer wallet on public inference (MC-1)"),
    _m("d2_operator_key_spends_a_wallet", ADMISSION,
       "  if k.audience = 'operator' then", "  if false then",
       "admission", "admission_refusals", "an operator credential spends a wallet (R66)"),
    _m("d2_credit_ceilings_past_the_deployment", ADMISSION,
       "  if (r->>'max_input_tokens')::int > pin.max_input_tokens\n"
       "     or (r->>'max_output_tokens')::int > pin.max_output_tokens then",
       "  if false then", "admission", "admission_refusals",
       "a request past the deployment's validated limits is admitted"),
    _m("d2_idempotency_scope_of_another_org", ADMISSION,
       "  if p_args->'idem'->>'org_id' is distinct from p_args->'request'->>'org_id' then",
       "  if false then", "admission", "admission_refusals",
       "one tenant's scope answers another's request (R10)"),
    # D10: 0020 redefines the guard (the expiry scrub is its one permitted change).
    _m("d2_admitted_request_rewritable", LIFECYCLE,
       "  elsif new.request_record is distinct from old.request_record then",
       "  elsif false then", "admission", "admission_accepts",
       "the request a worker loads is not the one that was priced (R53)"),
    _m("d2_admission_lock_dropped", ADMISSION,
       "  perform pg_advisory_xact_lock(infrx.admission_lock_key());\n", "",
       "admission", "admission_concurrency",
       "concurrent admissions count capacity from stale snapshots and oversubscribe"),
    # --- 0012: preparation and the relay --------------------------------------------
    _m("d2_claim_preparation_unlocked", DISPATCH,
       "  select * into j from infrx.jobs where request_id = (p_args->>'job_id')::uuid for update;",
       "  select * into j from infrx.jobs where request_id = (p_args->>'job_id')::uuid;",
       "admission", "preparation_claim_race",
       "two workers race one preparing job to a duplicate-key error (OB-6b)"),
    _m("d2_prepare_any_generation", DISPATCH,
       "  if a.generation is distinct from (l->>'generation')::int then", "  if false then",
       "admission", "prepare_transition", "a superseded preparation worker queues the job"),
    _m("d2_prepare_any_worker", DISPATCH,
       "  if a.worker_id is distinct from l->>'worker_id' then", "  if false then",
       "admission", "prepare_transition", "a foreign worker queues the job"),
    _m("d2_prepare_any_kind", DISPATCH,
       "  if l->>'kind' is distinct from 'preparation' then", "  if false then",
       "admission", "prepare_transition", "an inference token fences preparation (R46)"),
    _m("d2_prepare_on_an_expired_lease", DISPATCH,
       "  if v_now >= a.expires_at then\n    perform infrx.refuse('stale_lease', "
       "'preparation lease expired at '",
       "  if false then\n    perform infrx.refuse('stale_lease', 'preparation lease expired at '",
       "admission", "prepare_transition", "a lost preparation worker still queues the job"),
    _m("d2_prepare_stores_another_tenants_media", DISPATCH,
       "             where (m->>'org_id')::uuid is distinct from j.org_id) then",
       "             where false) then", "admission", "prepare_transition",
       "another tenant's prepared content is filed under this job (R10)"),
    _m("d2_late_prepared_not_terminalized", DISPATCH,
       _DEADLINE_BLOCK + "  if v_now >= a.expires_at then",
       _DEADLINE_BLOCK.replace("  if v_now >= j", "  if false and v_now >= j", 1)
       + "  if v_now >= a.expires_at then", "admission", "preparation_deadline",
       "a late preparation worker leaves the job preparing with its hold (R29/R55)"),
    _m("d2_late_claim_not_terminalized", DISPATCH,
       _DEADLINE_BLOCK + "  select * into a from infrx.attempts",
       _DEADLINE_BLOCK.replace("  if v_now >= j", "  if false and v_now >= j", 1)
       + "  select * into a from infrx.attempts", "admission", "preparation_deadline",
       "a job past its preparation phase is claimed again (R29)"),
    _m("d2_two_live_preparation_leases", DISPATCH,
       "  if found and v_now < a.expires_at then", "  if false then",
       "admission", "prepare_transition", "two preparation workers own one job"),
    _m("d2_unbounded_preparation_retries", DISPATCH,
       "  if j.preparation_attempts > (p_args->'limits'->>'max_prepublication_retries')::int "
       "then", "  if false then", "admission", "preparation_deadline",
       "a job is retried for ever below its deadline (R46)"),
    _m("d2_terminalize_keeps_the_usd_reservation", DISPATCH,
       "    update infrx.wallets set reserved_total = reserved_total - h.amount,",
       "    update infrx.wallets set reserved_total = reserved_total - 0,",
       "admission", "preparation_deadline", "a failed job's credit stays reserved for ever"),
    _m("d2_terminalize_keeps_the_credit_hold", DISPATCH,
       "   where request_id = p_request_id and state in ('held', 'unknown');",
       "   where request_id = p_request_id and false;",
       "admission", "credit_terminalization", "a failed CREDIT job's hold is never released"),
    _m("d2_terminalize_keeps_reservations", DISPATCH,
       "   where request_id = p_request_id and active;", "   where request_id = p_request_id "
       "and false;", "admission", "preparation_deadline", "a dead job pins capacity"),
    _m("d2_prepare_keeps_the_preparation_unit", DISPATCH,
       "   where request_id = j.request_id and kind = 'preparation' and active;",
       "   where request_id = j.request_id and false;",
       "admission", "prepare_transition", "a queued job still blocks preparation (R1)"),
    _m("d2_prepare_without_inference_dispatch", DISPATCH,
       "  values (gen_random_uuid(), j.request_id, j.org_id, 'inference_dispatch',",
       "  values (gen_random_uuid(), j.request_id, j.org_id, 'usage_projection',",
       "admission", "prepare_transition", "a queued job no worker ever hears of"),
    _m("d2_queue_deadline_past_the_budget", DISPATCH,
       "queue_deadline_at = least(v_now + make_interval(secs => v_remaining),",
       "queue_deadline_at = greatest(v_now + make_interval(secs => v_remaining),",
       "admission", "prepare_transition", "a job waits past its queue budget (R38)"),
    _m("d2_redelivered_inside_the_window", DISPATCH,
       "       and (o.claimed_at is null or o.claimed_at <= v_now - make_interval(",
       "       and (true or o.claimed_at <= v_now - make_interval(",
       "admission", "dispatch_relay", "every pump re-sends what it just sent"),
    _m("d2_superseded_rows_dispatched", DISPATCH,
       "    if infrx.dispatch_wanted((r.ev).kind, (r.job).state) then", "    if true then",
       "admission", "dispatch_relay", "a stale prepare_dispatch reaches a preparation pool"),
    _m("d2_acknowledgment_not_recorded", DISPATCH,
       "    update infrx.outbox set acknowledged_at = infrx.now()\n     where event_id in",
       "    update infrx.outbox set acknowledged_at = null\n     where event_id in",
       "admission", "dispatch_relay", "an acknowledged row is delivered for ever"),
    _m("d2_pending_for_no_worker", DISPATCH,
       "  if p_args->>'worker_id' is null or length(btrim(p_args->>'worker_id')) = 0 then\n"
       "    perform infrx.refuse('invalid_request', 'a worker id is required');\n"
       "  end if;\n  for r in",
       "  if false then\n"
       "    perform infrx.refuse('invalid_request', 'a worker id is required');\n"
       "  end if;\n  for r in",
       "admission", "dispatch_details",
       "a row claimed by no worker is never acknowledged and redelivered for ever (OB-8)"),
    _m("d2_ack_after_reopen_lands", DISPATCH,
       "       and claimed_at is not null\n       and claimed_by = p_args->>'worker_id'\n", "",
       "admission", "dispatch_relay",
       "a relay's late ack after a rebuild's reopen strands the job (OB-1b)"),
    _m("d2_ack_by_another_relay", DISPATCH,
       "       and claimed_by = p_args->>'worker_id'\n", "",
       "admission", "dispatch_relay", "a relay acknowledges a row another relay holds"),
    _m("d2_reopen_keeps_claimed_at", DISPATCH,
       "    update infrx.outbox o set acknowledged_at = null, claimed_at = null, claimed_by = null",
       "    update infrx.outbox o set acknowledged_at = null, claimed_by = null",
       "admission", "dispatch_relay", "a reopened row keeps a stale claim (OB-6b)"),
    _m("d2_reopen_ignores_since", DISPATCH,
       "       and (o.acknowledged_at >= (p_args->>'since')::timestamptz\n"
       "            or o.claimed_at >= (p_args->>'since')::timestamptz)",
       "       and true", "admission", "dispatch_relay",
       "every rebuild re-sends the whole delivered history"),
    _m("d2_reopen_revives_superseded_rows", DISPATCH,
       "       and infrx.dispatch_wanted(o.kind, j.state)\n"
       "       and (o.acknowledged_at >= (p_args->>'since')::timestamptz",
       "       and (o.acknowledged_at >= (p_args->>'since')::timestamptz",
       "admission", "dispatch_relay", "a stale prepare_dispatch is re-sent after a rebuild"),
    # --- review OB-4..OB-6 ------------------------------------------------------------
    _m("d2r_pending_unbounded", DISPATCH,
       "     limit greatest(1, least(coalesce((p_args->>'limit')::int, 100), 1000))\n", "",
       "admission", "dispatch_details", "one relay read locks every pending row"),
    _m("d2r_pending_no_order", DISPATCH,
       "     order by o.available_at, o.event_id\n     limit greatest(",
       "     limit greatest(", "admission", "dispatch_details",
       "a backlog is dispatched in no particular order (the oldest can starve)"),
    _m("d2r_pending_delivers_future_rows", DISPATCH,
       "     where o.acknowledged_at is null and o.available_at <= v_now",
       "     where o.acknowledged_at is null", "admission", "dispatch_details",
       "a delayed dispatch runs before its time"),
    _m("d2r_ack_any_kind", DISPATCH,
       "       and kind in ('prepare_dispatch', 'inference_dispatch')\n"
       "       and acknowledged_at is null\n       and claimed_at is not null\n",
       "       and acknowledged_at is null\n       and claimed_at is not null\n",
       "admission", "dispatch_details", "the relay acknowledges a projection it never sent"),
    _m("d2r_release_releases_nothing", DISPATCH,
       "    update infrx.outbox set claimed_at = null, claimed_by = null\n     where event_id in",
       "    update infrx.outbox set claimed_by = null\n     where event_id in",
       "admission", "dispatch_details", "rows deferred by a full index wait a whole window"),
    _m("d2r_fail_acknowledges", DISPATCH,
       "    update infrx.outbox set last_error = left(coalesce(p_args->>'error', 'unknown'), 500)",
       "    update infrx.outbox set last_error = left(coalesce(p_args->>'error', 'unknown'), "
       "500), acknowledged_at = infrx.now()",
       "admission", "dispatch_details", "a row the index refused is marked delivered"),
    _m("d2r_snapshot_oldest_event", DISPATCH,
       "                 order by x.created_at desc, x.event_id desc limit 1) o on true",
       "                 order by x.created_at, x.event_id limit 1) o on true",
       "admission", "dispatch_details", "a rebuild indexes a superseded event id"),
    _m("d2r_index_event_attempt_swapped", DISPATCH,
       "    'attempt', case o.kind when 'prepare_dispatch' then j.preparation_attempts\n"
       "                           else j.attempts end);",
       "    'attempt', case o.kind when 'prepare_dispatch' then j.attempts\n"
       "                           else j.preparation_attempts end);",
       "admission", "dispatch_details", "a candidate carries the other phase's retry count"),
    _m("d2r_snapshot_indexes_an_inference_leased_job", DISPATCH,
       "                     where a.job_id = j.request_id\n"
       "                       and a.released_at is null and a.expires_at > infrx.now());",
       "                     where a.job_id = j.request_id and a.kind = 'preparation'\n"
       "                       and a.released_at is null and a.expires_at > infrx.now());",
       "admission", "dispatch_details", "a running job is handed to a second worker (OB-5)"),
    _m("d2_snapshot_indexes_a_leased_job", DISPATCH,
       "                       and a.released_at is null and a.expires_at > infrx.now());",
       "                       and false);", "admission", "dispatch_relay",
       "a rebuild hands a job being prepared to a second pool member"),
    # --- 0013: GC ---------------------------------------------------------------------
    _m("d2_gc_deletes_a_live_jobs_rows", GC,
       "                       and (j.settled_at is null\n", "                       and (false\n",
       "admission", "outbox_gc", "a live job loses the event id its rebuild relies on"),
    _m("d2_gc_deletes_inside_the_retention", GC,
       "       and o.acknowledged_at < v_now - make_interval(secs => v_retention)",
       "       and o.acknowledged_at <= v_now - make_interval(secs => v_retention)",
       "admission", "outbox_gc", "rows are deleted at the retention instant, not after it"),
    _m("d2_gc_expires_a_live_job", GC,
       "       and j.settled_at is not null\n", "       and true\n",
       "admission", "outbox_gc", "a live job's undelivered dispatch is acknowledged away"),
    _m("d2_gc_deletes_inside_the_tombstone", GC,
       "or j.settled_at > v_now - make_interval(secs => v_tombstone)", "or false",
       "admission", "outbox_gc",
       "a replay inside the tombstone answers an admission whose events are gone (OB-3)"),
    _m("d2r_gc_delete_unbounded", GC,
       "     order by o.acknowledged_at, o.event_id\n     limit v_limit\n",
       "     order by o.acknowledged_at, o.event_id\n",
       "admission", "outbox_gc", "one GC call deletes the whole acknowledged history"),
    _m("d2_gc_deletes_callbacks", GC, "       and o.kind <> 'callback_delivery'",
       "       and true", "admission", "outbox_gc", "callback deliveries cascade away"),
    _m("d2_gc_unbounded", GC,
       "     limit v_limit\n     for update of o skip locked),\n  acked as",
       "     for update of o skip locked),\n  acked as",
       "admission", "outbox_gc", "one GC call locks the whole outbox"),
    _m("d2_gc_expires_projections", GC,
       "       and o.kind in ('prepare_dispatch', 'inference_dispatch')\n"
       "       and j.settled_at is not null",
       "       and true\n       and j.settled_at is not null",
       "admission", "outbox_gc", "an unconsumed usage projection is acknowledged away"),
    # --- 0014 and the prompt count --------------------------------------------------
    # D10: 0026 redefines `put_result` (the fence first, R147); 0014's body is dead SQL.
    _m("d2_result_rewritable", FENCED_RESULT, "  if r.digest <> v_digest then", "  if false then",
       "admission", "results_and_prompt_tokens",
       "a second writer's answer silently stands for the stored one"),
    # D10: 0020 redefines `read_result` (the persisted expiry is its authority).
    _m("d2_result_read_across_tenants", LIFECYCLE,
       "     and r.request_id = substr(p_ref, 14)::uuid and r.org_id = p_org;",
       "     and r.request_id = substr(p_ref, 14)::uuid;",
       "admission", "results_and_prompt_tokens", "a tenant reads another tenant's answer"),
    _m("d2_prompt_past_the_ceiling", DISPATCH,
       "     or (p_args->>'prompt_tokens')::int > j.max_input_tokens then",
       "     or false then", "admission", "results_and_prompt_tokens",
       "a prompt past the hold's input envelope reaches the table as a 500"),
    # --- 0010: M3's tables --------------------------------------------------------------
    _m("d2_upload_handle_any_shape", MEDIA,
       "  handle text primary key check (handle ~ '^upl_[A-Za-z0-9_-]{22,64}$'),",
       "  handle text primary key,", "admission", "media_uploads",
       "a guessable or foreign-kind handle names an upload"),
    _m("d2_upload_facts_before_finalize", MEDIA,
       "    (state = 'finalized' or num_nonnulls(digest, bytes, mime, duration_s, storage_ref,\n"
       "                                         profile_version) = 0)),",
       "    true),", "admission", "media_uploads",
       "an unfinalized upload already names content"),
    # D10: 0019 redefines `media_uploads_guard` (the receipt is written once, too).
    _m("d2_finalized_upload_rewritable", READY,
       "  if old.state <> 'created' and row(new.*) is distinct from row(old.*) then",
       "  if false then", "admission", "media_uploads",
       "a finalized upload's content changes under the refs that name it"),
    _m("d2_finalized_upload_deleted_early", READY,
       "    if old.state = 'finalized' and infrx.now() < old.expires_at then",
       "    if false then", "admission", "media_uploads",
       "a live upload record vanishes and its handle stops resolving (R82)"),
    _m("d2_media_touch_any_tenant", MEDIA,
       "   where storage_ref = p_storage_ref\n     and org_id = p_org\n",
       "   where storage_ref = p_storage_ref\n",
       "admission", "media_objects", "another tenant keeps an object alive"),
    _m("d2_media_touch_answers_like_a_miss", MEDIA,
       "  if v_at is null then\n    raise exception 'not_found: object %'",
       "  if false then\n    raise exception 'not_found: object %'",
       "admission", "media_objects", "a touch of a missing object reports success (SEC-4)"),
    _m("d2_media_touch_reveals_existence", MEDIA,
       "    raise exception 'not_found: object %', p_storage_ref using errcode = 'P0002';",
       "    raise exception 'not_found: object %', p_storage_ref using errcode = 'P0002', "
       "hint = (select case when count(*) > 0 then 'stored' else '' end "
       "from infrx.media_objects where storage_ref = p_storage_ref);",
       "admission", "media_objects",
       "a foreign touch tells a tenant another tenant's object exists (MC-2)"),
    _m("d2_media_touch_raises_from_another_line", MEDIA,
       "    raise exception 'not_found: object %', p_storage_ref using errcode = 'P0002';",
       "    if exists (select 1 from infrx.media_objects where storage_ref = p_storage_ref) "
       "then\n      raise exception 'not_found: object %', p_storage_ref using errcode = "
       "'P0002';\n    end if;\n"
       "    raise exception 'not_found: object %', p_storage_ref using errcode = 'P0002';",
       "admission", "media_objects",
       "a foreign touch is told apart from a miss by the RAISE's line (MC-2b)"),
    _m("d2_result_ref_shape_loose", LIFECYCLE,
       "   where p_ref ~ '^infrx-result:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
       "[0-9a-f]{12}$'",
       "   where p_ref ~ '^infrx-result:[0-9a-f-]{36}$'",
       "admission", "results_and_prompt_tokens",
       "a malformed reference is a 500 instead of not_found (SEC-5)"),
    _m("d2_media_delete_ignores_last_use", MEDIA,
       "                 where storage_ref = p_storage_ref and last_used_at = p_observed",
       "                 where storage_ref = p_storage_ref",
       "admission", "media_objects", "the collector deletes an object a stage just re-used"),
    _m("d2_media_objects_truncatable", MEDIA,
       "grant select, insert, update, delete on infrx.media_objects to service_role;",
       "grant all on infrx.media_objects to service_role;",
       "admission", "media_privileges", "the platform role can TRUNCATE the object index"),
    _m("d2_media_uploads_without_rls", MEDIA,
       "alter table infrx.media_uploads enable row level security;\n", "",
       "admission", "media_privileges", "a future exposure of infrx leaks every upload"),
)
MUTANTS = MUTANTS + D2_MUTANTS


#: D3 (0016 and the 0003 `jobs_guard` amendment, scenario "admission"). One per claimed
#: invariant, each killed by its named `checks_leases` check.
LEASES = "0016_fenced_leases.sql"
#: D5 item 10b: 0018 redefines `infrx.cancel`, `infrx.claim` and `release_aged_unknown`
#: (each body verbatim but for D5's one change), so the D3 mutants anchored in those bodies
#: live on 0018's copies - same name, check and defect. A mutant left on a superseded body
#: would edit dead SQL (`test_no_mutant_anchors_in_a_superseded_function_body`).
SETTLE = "0018_terminal_settlement.sql"
_REFUSAL_RETURN = ("  if v_refusal is not null then\n"
                   "    return jsonb_build_object('refusal', v_refusal);\n  end if;\n")
#: FE-3: recover_job decides publication BEFORE the retry counter.
_PUBLISHED_CHECK = (
    "  if j.published then\n"
    "    return jsonb_build_array(jsonb_build_object('outcome', infrx.terminalize_no_usage(\n"
    "      p_id, 'lost_after_publication', 'failed', p_reconcile_s)));\n  end if;\n")
_RETRY_CHECK = (
    "  if j.attempts >= p_retries then\n"
    "    return jsonb_build_array(jsonb_build_object('outcome', infrx.terminalize_no_usage(\n"
    "      p_id, 'retries_exhausted', 'failed', p_reconcile_s)));\n  end if;\n")
D3_MUTANTS: tuple[Mutant, ...] = (
    # --- the fence ---------------------------------------------------------------------
    _m("d3_fence_accepts_any_kind", LEASES,
       "  if v_kind is null or not (v_kind = any(p_kinds)) then", "  if v_kind is null then",
       "admission", "preparation_fence", "a preparation lease reaches the settlement (R46)"),
    _m("d3_fence_without_a_live_attempt", LEASES,
       "  -- A live attempt of this kind exists exactly while the job is in the phase it fences.\n"
       "  if not found then",
       "  -- A live attempt of this kind exists exactly while the job is in the phase it fences.\n"
       "  if false then",
       "admission", "lease_fence", "a token with no attempt behind it loads the work"),
    _m("d3_fence_serves_a_released_attempt", LEASES,
       "   where job_id = j.request_id and kind = v_kind and released_at is null;",
       "   where job_id = j.request_id and kind = v_kind;",
       "admission", "lease_fence",
       "a preparation worker that already handed its job over still loads its work (H-2)"),
    _m("d3_fence_ignores_the_generation", LEASES,
       "  if a.generation is distinct from (p_lease->>'generation')::int then", "  if false then",
       "admission", "lease_fence", "a superseded generation mutates the new holder's job"),
    _m("d3_fence_ignores_the_worker", LEASES,
       "  if a.worker_id is distinct from p_lease->>'worker_id' then", "  if false then",
       "admission", "lease_fence", "two processes holding generation N both mutate"),
    _m("d3_fence_expiry_before_the_deadline", LEASES,
       "  if v_now >= j.deadline_at or v_now >= a.generation_deadline_at then",
       "  if v_now < a.expires_at and (v_now >= j.deadline_at "
       "or v_now >= a.generation_deadline_at) then",
       "admission", "lease_fence", "an overdue job waits for the reaper with its hold (R55)"),
    _m("d3_fence_ignores_the_generation_deadline", LEASES,
       "  if v_now >= j.deadline_at or v_now >= a.generation_deadline_at then",
       "  if v_now >= j.deadline_at then",
       "admission", "lease_fence", "a live lease keeps generating past its phase (R29)"),
    _m("d3_heartbeat_deadline_from_created_at", LEASES,
       "  if v_now >= j.deadline_at or v_now >= a.generation_deadline_at then",
       "  if v_now >= j.deadline_at or v_now >= (j.request_record->>'created_at')::timestamptz\n"
       "       + make_interval(secs => j.budget_generation_s) then",
       "admission", "lease_fence", "a fenced call judges the phase on the gateway's clock"),
    _m("d3_fence_clock_is_the_gateways", LEASES,
       "  v_now timestamptz := infrx.now();\n  v_kind text := p_lease->>'kind';",
       "  v_now timestamptz := (select (r.request_record->>'created_at')::timestamptz\n"
       "    from infrx.jobs r where r.request_id = (p_lease->>'job_id')::uuid);\n"
       "  v_kind text := p_lease->>'kind';",
       "admission", "lease_fence", "the fence's now is the request's created_at (R7/R29)"),
    _m("d3_fence_ignores_the_preparation_deadline", LEASES,
       "  if v_kind = 'preparation' and v_now >= j.preparation_deadline_at then",
       "  if false then", "admission", "preparation_fence",
       "a late preparation settles with the wrong cause (R29)"),
    _m("d3_fence_ignores_expiry", LEASES,
       "  if v_now >= a.expires_at then\n    perform infrx.refuse('stale_lease', v_kind || "
       "' lease expired at '",
       "  if false then\n    perform infrx.refuse('stale_lease', v_kind || "
       "' lease expired at '", "admission", "lease_fence", "a lost worker keeps mutating"),
    _m("d3_fence_expiry_off_by_one", LEASES,
       "  if v_now >= a.expires_at then", "  if v_now > a.expires_at then",
       "admission", "lease_fence",
       "a lease still renews at the instant the reaper may requeue it (FE-2)"),
    _m("d3_fence_expires_a_microsecond_early", LEASES,
       "  if v_now >= a.expires_at then",
       "  if v_now >= a.expires_at - interval '1 microsecond' then",
       "admission", "lease_fence",
       "a live lease is refused before its expiry instant (confirmation FC-1)"),
    _m("d3_fence_serves_a_terminal_job", LEASES,
       "  if j.settled_at is not null then\n    perform infrx.refuse('already_terminal', 'job ' "
       "|| j.request_id || ' is already '",
       "  if false then\n    perform infrx.refuse('already_terminal', 'job ' "
       "|| j.request_id || ' is already '",
       "admission", "lease_fence", "a worker cannot tell its job was cancelled"),
    _m("d3_fence_without_the_row_lock", LEASES,
       "  select * into j from infrx.jobs where request_id = (p_lease->>'job_id')::uuid "
       "for update;",
       "  select * into j from infrx.jobs where request_id = (p_lease->>'job_id')::uuid;",
       "admission", "lease_races", "a fenced mutation does not serialize with the reaper"),
    # --- claim -------------------------------------------------------------------------
    _m("d3_claim_without_the_row_lock", SETTLE,
       "  select * into j from infrx.jobs where request_id = (p_args->>'job_id')::uuid "
       "for update;",
       "  select * into j from infrx.jobs where request_id = (p_args->>'job_id')::uuid;",
       "admission", "lease_races", "two claimers race to a unique violation"),
    _m("d3_claim_serves_a_terminal_job", SETTLE,
       "  if j.settled_at is not null then\n    perform infrx.refuse('already_terminal', 'job ' "
       "|| j.request_id || ' is ' || j.state);",
       "  if false then\n    perform infrx.refuse('already_terminal', 'job ' "
       "|| j.request_id || ' is ' || j.state);",
       "admission", "claim_generation", "a dispatcher cannot tell a finished job"),
    _m("d3_claim_any_state", SETTLE, "  if j.state <> 'queued' then", "  if false then",
       "admission", "claim_generation", "a running job is claimed twice"),
    _m("d3_claim_beside_a_live_attempt", SETTLE,
       "  if exists (select 1 from infrx.attempts where job_id = j.request_id\n"
       "                and released_at is null) then",
       "  if false then",
       "admission", "claim_generation",
       "a job ends with two live attempts of different kinds (FE-4)"),
    _m("d3_claim_beside_a_live_preparation_only", SETTLE,
       "  if exists (select 1 from infrx.attempts where job_id = j.request_id\n"
       "                and released_at is null) then",
       "  if exists (select 1 from infrx.attempts where job_id = j.request_id\n"
       "                and released_at is null and kind = 'preparation') then",
       "admission", "claim_generation",
       "a claim beside a live inference attempt dies on the unique index, untyped (FC-3)"),
    # D5 item 2 retired `d3_claim_leases_a_credit_job` with the MY-3 refusal it guarded:
    # 0018's `claim` leases a CREDIT job (`d5_claim_refuses_credit_again` is the control).
    _m("d3_claim_past_the_absolute_deadline", SETTLE,
       "  if v_now >= j.deadline_at then\n    perform infrx.refuse('not_claimable'",
       "  if false then\n    perform infrx.refuse('not_claimable'",
       "admission", "claim_generation", "a job nobody waits for starts running"),
    _m("d3_claim_past_the_queue_deadline", SETTLE,
       "  if v_now >= j.queue_deadline_at then\n    perform infrx.refuse('not_claimable'",
       "  if false then\n    perform infrx.refuse('not_claimable'",
       "admission", "claim_generation", "a job told to give up starts running (R20)"),
    _m("d3_claim_queue_time_not_charged", SETTLE,
       "                          + coalesce(extract(epoch from v_now - j.queued_at)::float8, 0)",
       "                          + 0",
       "admission", "claim_generation", "a requeue buys back queue time (R38)"),
    _m("d3_claim_generation_not_incremented", SETTLE,
       "          (select coalesce(max(generation), 0) + 1 from infrx.attempts",
       "          (select coalesce(max(generation), 0) + 2 from infrx.attempts",
       "admission", "claim_generation", "the generation is not the next one"),
    _m("d3_claim_generation_past_the_deadline", SETTLE,
       "  v_generation_deadline := least(v_now + make_interval(secs => j.budget_generation_s),\n"
       "                                 j.deadline_at);",
       "  v_generation_deadline := v_now + make_interval(secs => j.budget_generation_s);",
       "admission", "claim_generation", "a phase outlives the accepted deadline (R20)"),
    _m("d3_claim_first_token_past_the_generation", SETTLE,
       "          least(v_now + make_interval(secs => j.budget_first_token_s), "
       "v_generation_deadline))",
       "          v_now + make_interval(secs => j.budget_first_token_s))",
       "admission", "claim_generation",
       "a job with less time left than the first-token budget is unclaimable: the attempts "
       "CHECK (first token <= generation, R20) refuses the unclamped instant",
       expects_detail="attempts_check1"),
    _m("d3_claim_leases_without_running", SETTLE,
       "  update infrx.jobs set state = 'running', queued_at = null,",
       "  update infrx.jobs set queued_at = null,",
       "admission", "lease_races", "a leased job stays queued: a rebuild re-dispatches it"),
    _m("d3_claim_instants_from_created_at", SETTLE,
       "  v_generation_deadline := least(v_now + make_interval(secs => j.budget_generation_s),",
       "  v_generation_deadline := least((j.request_record->>'created_at')::timestamptz"
       " + make_interval(secs => j.budget_generation_s),",
       "admission", "claim_generation", "the phase instant comes from the gateway's clock (R29)"),
    _m("d3_claim_lease_ttl_ignored", SETTLE,
       "v_now + make_interval(secs => v_ttl),", "v_now + interval '1 day',",
       "admission", "claim_generation", "a lost worker is not reaped for a day"),
    # --- heartbeat / load_work ------------------------------------------------------------
    _m("d3_heartbeat_renews_the_callers_record", LEASES,
       "           else v_now + make_interval(secs => infrx.lease_limit(p_args, 'lease_ttl_s')) "
       "end",
       "           else (p_args->'lease'->>'expires_at')::timestamptz end",
       "admission", "lease_fence", "a worker grants itself any lease (R29)"),
    _m("d3_preparation_renewal_past_the_phase", LEASES,
       "                  t.generation_deadline_at)          -- = preparation_deadline_at (R52)",
       "                  v_now + interval '1 day')",
       "admission", "preparation_fence", "a renewal buys preparation time (R52)"),
    _m("d3_heartbeat_ignores_a_terminalization", LEASES,
       _REFUSAL_RETURN + "  update infrx.attempts t", "  update infrx.attempts t",
       "admission", "lease_fence", "the worker of a terminalized job gets a lease (R39)"),
    _m("d3_load_work_after_a_terminalization", LEASES,
       _REFUSAL_RETURN + "  select * into j from infrx.jobs where request_id = "
       "(p_args->'lease'->>'job_id')::uuid;",
       "  select * into j from infrx.jobs where request_id = "
       "(p_args->'lease'->>'job_id')::uuid;",
       "admission", "lease_fence", "a terminalized job's work is still handed out (R39)"),
    _m("d3_load_work_the_callers_deadline", LEASES,
       "    'request', j.request_record || jsonb_build_object('deadline_at', j.deadline_at),",
       "    'request', j.request_record,",
       "admission", "preparation_fence", "a worker is told a deadline the store never kept"),
    _m("d3_load_work_without_prepared_refs", LEASES,
       "    'prepared_refs', coalesce(j.prepared_refs, '[]'),",
       "    'prepared_refs', '[]'::jsonb,",
       "admission", "lease_fence", "the engine never sees preparation's media"),
    # --- cancel and the terminalization -------------------------------------------------
    _m("d3_cancel_any_tenant", SETTLE,
       "   where job_handle = p_args->>'job_handle' and org_id = (p_args->>'org_id')::uuid",
       "   where job_handle = p_args->>'job_handle'",
       "admission", "cancel", "a tenant cancels another tenant's job"),
    _m("d3_cancel_rewrites_the_committed_outcome", SETTLE,
       "  if j.settled_at is not null then\n    return infrx.job_admission(j.request_id)"
       "->'outcome';",
       "  if false then\n    return infrx.job_admission(j.request_id)->'outcome';",
       "admission", "cancel", "a late cancel fails instead of answering the outcome"),
    _m("d3_cancel_without_the_row_lock", SETTLE,
       "   where job_handle = p_args->>'job_handle' and org_id = (p_args->>'org_id')::uuid\n"
       "   for update;",
       "   where job_handle = p_args->>'job_handle' and org_id = (p_args->>'org_id')::uuid;",
       "admission", "lease_races", "cancel and complete race to a second terminal write"),
    _m("d3_terminalization_of_a_terminal_job", LEASES,
       "  if j.settled_at is not null then\n    perform infrx.refuse('already_terminal', 'job ' "
       "|| p_request_id",
       "  if false then\n    perform infrx.refuse('already_terminal', 'job ' || p_request_id",
       "admission", "cancel",
       "a caller that forgot the terminal check writes a second terminalization (H-4)"),
    _m("d3_terminal_check_only_cancelled", LEASES,
       "  if j.settled_at is not null then\n    perform infrx.refuse('already_terminal', 'job ' "
       "|| p_request_id",
       "  if j.state = 'cancelled' then\n    perform infrx.refuse('already_terminal', 'job ' "
       "|| p_request_id",
       "admission", "cancel",
       "an expired or failed job is terminalized a second time (confirmation CM-1)"),
    _m("d3_published_output_released", LEASES,
       "  if j.published then\n    -- Output was committed",
       "  if false then\n    -- Output was committed",
       "admission", "cancel", "uncounted output drops out of reconciliation (02)"),
    _m("d3_a_cancellation_is_platform_absorbed", LEASES,
       "                    'client_cancelled', 'client_disconnected') then",
       "                    'client_disconnected') then",
       "admission", "cancel", "a free cancellation is booked as platform cost (R21)"),
    _m("d3_no_reconciliation_window", LEASES,
       "    v_reconcile := v_now + make_interval(secs => p_reconcile_s);",
       "    v_reconcile := v_now;",
       "admission", "cancel", "unknown usage is released at once, before reconciliation"),
    _m("d3_terminalization_keeps_the_hold", LEASES,
       "  else\n    perform infrx.release_hold_legacy_usd(p_request_id);\n  end if;",
       "  else\n    null;\n  end if;",
       "admission", "cancel", "a cancelled job keeps the customer's money reserved"),
    _m("d3_terminalization_keeps_the_credit_hold", LEASES,
       "  elsif j.accounting_regime = 'credit' then\n"
       "    perform infrx.release_hold_credit(p_request_id);",
       "  elsif j.accounting_regime = 'credit' then\n    null;",
       "admission", "cancel", "a cancelled CREDIT job keeps the wallet reserved"),
    _m("d3_terminalization_keeps_the_reservations", LEASES,
       "  update infrx.capacity_reservations set active = false, released_at = v_now\n"
       "   where request_id = p_request_id and active;",
       "  update infrx.capacity_reservations set active = false, released_at = v_now\n"
       "   where false;",
       "admission", "cancel", "a cancelled job holds capacity for ever"),
    _m("d3_terminalization_keeps_the_attempts", LEASES,
       "  update infrx.attempts set released_at = v_now, finished_at = coalesce(finished_at, "
       "v_now)\n   where job_id = p_request_id and released_at is null;",
       "  update infrx.attempts set released_at = v_now, finished_at = coalesce(finished_at, "
       "v_now)\n   where false;",
       "admission", "cancel", "a cancelled job's worker still holds a live attempt"),
    _m("d3_terminalization_without_its_usage_projection", LEASES,
       "  values (gen_random_uuid(), p_request_id, j.org_id, 'usage_projection',",
       "  values (gen_random_uuid(), p_request_id, j.org_id, 'trace_projection',",
       "admission", "cancel", "usage never learns the job ended"),
    _m("d3_quarantine_releases_the_hold", LEASES,
       "  update infrx.credit_holds set state = 'unknown', reconcile_after = p_reconcile_after,",
       "  update infrx.credit_holds set state = 'released', reconcile_after = null,",
       "admission", "cancel", "the hold says released while the wallet stays reserved"),
    # FE-1/MY-1: the CREDIT regime's unknown-usage path (a published CREDIT job)
    _m("d3_quarantine_releases_the_credit_hold", LEASES,
       "  update infrx.credit_wallet_holds set state = 'unknown', reconcile_after = "
       "p_reconcile_after",
       "  update infrx.credit_wallet_holds set state = 'released', reconcile_after = null",
       "admission", "cancel",
       "a published CREDIT job's reservation is released while the job says held_unknown"),
    _m("d3_published_credit_released_not_quarantined", LEASES,
       "    perform infrx.quarantine_hold_credit(p_request_id, v_reconcile);",
       "    perform infrx.release_hold_credit(p_request_id);",
       "admission", "cancel", "uncounted CREDIT output drops out of reconciliation (02)"),
    _m("d3_published_credit_quarantined_as_legacy", LEASES,
       "  if v_settlement = 'held_unknown' and j.accounting_regime = 'credit' then",
       "  if false then",
       "admission", "cancel",
       "a published CREDIT job's hold stays 'held' with no window: never reconciled"),
    _m("d3_unknown_release_keeps_the_credit_hold", SETTLE,
       "  if j.accounting_regime = 'credit' then\n    perform infrx.release_hold_credit(p_id);",
       "  if j.accounting_regime = 'credit' then\n    null;",
       "admission", "recover_unknown_release",
       "a consumer's CREDIT stays reserved for ever after the 24 h window"),
    # --- the reaper ---------------------------------------------------------------------
    _m("d3_requeue_after_publication", LEASES,
       "  if j.published then\n    return jsonb_build_array(",
       "  if false then\n    return jsonb_build_array(",
       "admission", "recover_requeue", "published output is regenerated (02 §6)"),
    _m("d3_retries_checked_before_publication", LEASES,
       _PUBLISHED_CHECK + _RETRY_CHECK, _RETRY_CHECK + _PUBLISHED_CHECK,
       "admission", "recover_requeue",
       "a publication lost with its retries spent is booked retries_exhausted (FE-3)"),
    _m("d3_retries_unbounded", LEASES, "  if j.attempts >= p_retries then", "  if false then",
       "admission", "recover_requeue", "a failing job is retried for ever"),
    _m("d3_one_retry_too_many", LEASES, "  if j.attempts >= p_retries then",
       "  if j.attempts > p_retries then",
       "admission", "recover_requeue", "MAX_PREPUBLICATION_RETRIES + 1 requeues"),
    _m("d3_a_live_lease_reaped", LEASES,
       "  if p_now < a.expires_at then\n    return '[]';\n  end if;\n  -- 02 §6",
       "  if false then\n    return '[]';\n  end if;\n  -- 02 §6",
       "admission", "recover_requeue", "a renewal committed after the scan is requeued anyway"),
    _m("d3_generation_deadline_not_reaped", LEASES,
       "  if p_now >= a.generation_deadline_at then", "  if false then",
       "admission", "recover_requeue", "a run past its phase keeps its hold (R20)"),
    _m("d3_scan_misses_the_generation_deadline", LEASES,
       "                       and (a.expires_at <= v_now or a.generation_deadline_at <= v_now)))",
       "                       and (a.expires_at <= v_now)))",
       "admission", "recover_requeue", "the sweep never looks at an overdue live run"),
    _m("d3_requeue_restores_the_whole_queue_budget", LEASES,
       "                          secs => greatest(0, budget_queue_wait_s - queue_wait_used_s)),",
       "                          secs => budget_queue_wait_s),",
       "admission", "recover_requeue", "a requeue buys a fresh queue budget (R38)"),
    _m("d3_requeue_without_an_inference_dispatch", LEASES,
       "  values (gen_random_uuid(), p_id, j.org_id, 'inference_dispatch',",
       "  values (gen_random_uuid(), p_id, j.org_id, 'prepare_dispatch',",
       "admission", "recover_requeue", "a requeued job is handed to the preparation pool"),
    _m("d3_requeue_reopens_the_old_row", LEASES,
       "  insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, available_at)\n"
       "  values (gen_random_uuid(), p_id, j.org_id, 'inference_dispatch',\n"
       "          jsonb_build_object('job_handle', j.job_handle, 'request_id', p_id,\n"
       "                             'attempt', j.attempts), p_now)\n",
       "  update infrx.outbox set acknowledged_at = null, claimed_at = null, claimed_by = null\n"
       "   where event_id = (select event_id from infrx.outbox where aggregate_id = p_id\n"
       "                        and kind = 'inference_dispatch' order by created_at desc limit 1)\n",
       "admission", "recover_requeue",
       "a requeue re-sends an old event id a replay-safe index drops (D2 OB-5b)"),
    _m("d3_requeue_keeps_the_lost_attempt", LEASES,
       "   where job_id = p_id and kind = 'inference' and generation = a.generation;",
       "   where false;",
       "admission", "recover_requeue", "the lost attempt still fences the next claim"),
    _m("d3_requeue_uncounted", LEASES,
       "  update infrx.jobs set state = 'queued', attempts = attempts + 1, queued_at = p_now,",
       "  update infrx.jobs set state = 'queued', attempts = attempts, queued_at = p_now,",
       "admission", "recover_requeue", "the retry counter never moves"),
    _m("d3_queued_job_reaped_early", LEASES, "    if p_now >= j.queue_deadline_at then",
       "    if true then", "admission", "recover_requeue",
       "a queued job is expired before its queue instant"),
    _m("d3_queue_expiry_ignored", LEASES, "    if p_now >= j.queue_deadline_at then",
       "    if false then", "admission", "recover_requeue",
       "an abandoned queued job keeps its hold"),
    _m("d3_reaper_deadline_from_created_at", LEASES,
       "  if p_now >= a.generation_deadline_at then",
       "  if p_now >= (j.request_record->>'created_at')::timestamptz"
       " + make_interval(secs => j.budget_generation_s) then",
       "admission", "recover_requeue", "the reaper judges a run on the gateway's clock"),
    _m("d3_reaper_takes_the_callers_clock", LEASES,
       "  v_now timestamptz := infrx.now();\n  v_retries int :=",
       "  v_now timestamptz := coalesce((p_args->>'now')::timestamptz, infrx.now());\n"
       "  v_retries int :=",
       "admission", "recover_unknown_release", "a caller releases an unknown hold early (R7)"),
    _m("d3_prep_reaper_ignores_the_deadline", LEASES,
       "    if p_now >= j.preparation_deadline_at then", "    if false then",
       "admission", "recover_preparation", "a dead preparation holds until deadline_at"),
    _m("d3_prep_live_lease_reaped", LEASES,
       "    if not found or p_now < a.expires_at then", "    if not found then",
       "admission", "recover_preparation", "a live preparation is redispatched"),
    _m("d3_prep_retries_unbounded", LEASES,
       "    if j.preparation_attempts > p_retries then", "    if false then",
       "admission", "recover_preparation", "a preparation is retried for ever (R46)"),
    _m("d3_prep_reap_without_redispatch", LEASES,
       "    values (gen_random_uuid(), p_id, j.org_id, 'prepare_dispatch',",
       "    values (gen_random_uuid(), p_id, j.org_id, 'inference_dispatch',",
       "admission", "recover_preparation", "a reaped preparation is never tried again"),
    _m("d3_prep_redispatch_reopens_the_old_row", LEASES,
       "    insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, available_at)\n"
       "    values (gen_random_uuid(), p_id, j.org_id, 'prepare_dispatch',\n"
       "            jsonb_build_object('job_handle', j.job_handle, 'request_id', p_id,\n"
       "                               'attempt', j.preparation_attempts), p_now);",
       "    update infrx.outbox set acknowledged_at = null, claimed_at = null, claimed_by = null\n"
       "     where aggregate_id = p_id and kind = 'prepare_dispatch';",
       "admission", "recover_preparation",
       "a lost preparation is re-sent under an old event id a replay-safe index drops (OB-5b)"),
    _m("d3_prep_reap_reads_a_released_attempt", LEASES,
       "     where job_id = p_id and kind = 'preparation' and released_at is null;\n"
       "    if not found or p_now < a.expires_at then",
       "     where job_id = p_id and kind = 'preparation' order by generation desc limit 1;\n"
       "    if not found or p_now < a.expires_at then",
       "admission", "recover_preparation",
       "two reaper ticks on one lapsed preparation lease write two fresh rows (confirmation FC-2)"),
    _m("d3_prep_reap_keeps_the_lease", LEASES,
       "     where job_id = p_id and kind = 'preparation' and generation = a.generation;",
       "     where false;",
       "admission", "recover_preparation", "the dead worker's lease blocks the next claim"),
    _m("d3_unknown_released_early", LEASES,
       "     where settlement_state = 'held_unknown' and reconcile_after <= v_now",
       "     where settlement_state = 'held_unknown'",
       "admission", "recover_unknown_release", "reconciliation is skipped (02)"),
    _m("d3_unknown_released_twice", SETTLE,
       "   where request_id = p_id and settlement_state = 'held_unknown'\n   for update skip locked;",
       "   where request_id = p_id\n   for update skip locked;",
       "admission", "recover_unknown_release", "a second sweep releases the hold again"),
    _m("d3_unknown_release_keeps_the_hold", SETTLE,
       "  else\n    perform infrx.release_hold_legacy_usd(p_id);\n  end if;",
       "  else\n    null;\n  end if;",
       "admission", "recover_unknown_release", "platform-absorbed, and still reserved"),
    _m("d3_one_stuck_job_stops_the_sweep", LEASES,
       "      v_out := v_out || infrx.recover_job(v_id, v_now, v_retries, v_reconcile);\n"
       "    exception when others then",
       "      v_out := v_out || infrx.recover_job(v_id, v_now, v_retries, v_reconcile);\n"
       "    exception when no_data_found then",
       "admission", "recover_isolation", "one poisoned job stops every release"),
    # --- privileges ---------------------------------------------------------------------
    _m("d3_service_operations_callable_by_browsers", LEASES,
       "    execute format('grant execute on function %s to service_role', f);",
       "    execute format('grant execute on function %s to service_role, authenticated', f);",
       "admission", "lease_privileges", "a browser session runs the reaper"),
    _m("d3_the_fence_callable_by_the_service", LEASES,
       "      'infrx.fence_lease(jsonb, text[], double precision)',\n", "",
       "admission", "lease_privileges", "a client can fence-and-terminalize any job"),
    # --- the 0003 amendment -----------------------------------------------------------
    _m("d3_any_terminal_settlement_may_change", SCHEMA,
       "           and not (old.settlement_state = 'held_unknown'\n"
       "                    and new.settlement_state = 'released_platform_absorbed'))",
       "           and false)",
       "admission", "recover_unknown_release", "a settled job's settlement is rewritable"),
    _m("d3_held_unknown_may_be_rewritten", SCHEMA,
       "                    and new.settlement_state = 'released_platform_absorbed'))",
       "                    and true))",
       "admission", "recover_unknown_release", "an unknown-usage outcome is rewritten as never charged"),
    _m("d3_settled_usage_may_become_absorbed", SCHEMA,
       "           and not (old.settlement_state = 'held_unknown'",
       "           and not (old.settlement_state in ('held_unknown', 'settled')",
       "admission", "recover_unknown_release",
       "known, settled usage is rewritten as platform-absorbed (MY-2)"),
    _m("d3_unknown_release_refused", SCHEMA,
       "           and not (old.settlement_state = 'held_unknown'",
       "           and not (false",
       "admission", "recover_unknown_release", "unknown-usage holds are reserved for ever"),
)
MUTANTS = MUTANTS + D3_MUTANTS


#: D4 (0017, plus one 0011 accounting edit; scenario "admission"). One per claimed invariant,
#: each killed by its named `checks_journal` check.
from . import checks_journal  # noqa: E402
JOURNAL = "0017_stream_journal.sql"
_J_FENCE = ("  v_refusal := infrx.fence_lease(v_lease, array['inference'],\n"
            "                                 infrx.lease_limit(p_args, "
            "'unknown_usage_reconcile_s'));\n"
            "  if v_refusal is not null then\n"
            "    return jsonb_build_object('refusal', v_refusal);\n  end if;\n")
_J_SEQUENCE = (
    "  select * into j from infrx.jobs where request_id = (v_lease->>'job_id')::uuid;\n"
    "  -- Under the row lock the fence holds: the generation's next sequence, never at or below\n"
    "  -- the prune watermark (a fully pruned journal must not reissue a pruned cursor).\n"
    "  select greatest(coalesce(max(c.sequence), 0),\n"
    "                  case when j.journal_pruned_generation = (v_lease->>'generation')::int\n"
    "                       then j.journal_pruned_sequence else 0 end)\n"
    "    into v_next from infrx.stream_chunks c\n"
    "   where c.job_id = j.request_id and c.generation = (v_lease->>'generation')::int;\n")
_J_CEILING = ("       > j.journal_reserved_bytes - least(1024, j.journal_reserved_bytes / 2) then")
_J_TERMINAL_ROOM = ("  if new.journal_stored_bytes + v_widest > new.journal_reserved_bytes then")
_J_WHEN = "                    and new.journal_reserved_bytes > 0)"
_J_PRUNE_BYTES = "journal_stored_bytes = journal_stored_bytes - v_bytes,"
D4_MUTANTS: tuple[Mutant, ...] = (
    # --- item 1: the fenced append -------------------------------------------------------
    _m("d4_append_without_the_fence", JOURNAL,
       "  v_refusal := infrx.fence_lease(v_lease, array['inference'],\n"
       "                                 infrx.lease_limit(p_args, "
       "'unknown_usage_reconcile_s'));",
       "  v_refusal := null;",
       "admission", "append_fenced", "a stale, foreign or expired worker appends output"),
    _m("d4_append_accepts_a_preparation_lease", JOURNAL,
       "infrx.fence_lease(v_lease, array['inference'],",
       "infrx.fence_lease(v_lease, array['preparation', 'inference'],",
       "admission", "append_fenced", "a preparation worker publishes output (R46)"),
    _m("d4_r29_refusal_raised_rolls_back", JOURNAL,
       "    return jsonb_build_object('refusal', v_refusal);",
       "    perform infrx.refuse(v_refusal->>'code', v_refusal->>'detail');",
       "admission", "append_past_the_instant",
       "an overdue append's terminalization is rolled back with its refusal (R39)"),
    _m("d4_append_without_the_row_lock", JOURNAL, _J_FENCE + _J_SEQUENCE,
       _J_SEQUENCE + _J_FENCE,
       "admission", "journal_races", "two appends on one lease mint the same cursor"),
    _m("d4_terminal_event_accepted_when_last", JOURNAL,
       "              where not coalesce(e.event->>'type'",
       "              where e.n < jsonb_array_length(v_events) and not coalesce(e.event->>'type'",
       "admission", "append_terminal_refused", "a worker forges the settlement's event (R30)"),
    _m("d4_event_limit_off_by_one", JOURNAL, "  if v_largest > v_max_event then",
       "  if v_largest >= v_max_event then",
       "admission", "append_oversize", "an event of exactly the limit is refused (R25)"),
    _m("d4_oversize_event_stored", JOURNAL, "  if v_largest > v_max_event then", "  if false then",
       "admission", "append_oversize", "an event over the limit is journalled (R25)"),
    _m("d4_event_limit_on_characters", JOURNAL,
       "         coalesce(max(octet_length((e->'payload')::text)), 0)\n",
       "         coalesce(max(length((e->'payload')::text)), 0)\n",
       "admission", "append_oversize",
       "a 3-byte-per-character event of ~3 MiB passes the 1 MiB event limit (A3)"),
    _m("d4_event_limit_on_the_batch_sum", JOURNAL, "  if v_largest > v_max_event then",
       "  if v_bytes > v_max_event then",
       "admission", "append_oversize", "a batch of legitimate events is refused whole (A3)"),
    _m("d4_only_the_first_event_is_measured", JOURNAL,
       "         coalesce(max(octet_length((e->'payload')::text)), 0)\n",
       "         coalesce(octet_length((v_events->0->'payload')::text), 0)\n",
       "admission", "append_oversize", "an oversize event behind a small one is stored"),
    _m("d4_job_ceiling_ignores_the_terminal_reserve", JOURNAL, _J_CEILING,
       "       > j.journal_reserved_bytes then",
       "admission", "append_job_ceiling", "output leaves no room for the terminal event (R39)"),
    _m("d4_terminal_event_waved_through", JOURNAL, _J_TERMINAL_ROOM, "  if false then",
       "admission", "append_job_ceiling", "a terminal event past the reservation is stored"),
    _m("d4_terminal_reserve_sized_to_this_outcome", JOURNAL, _J_TERMINAL_ROOM,
       "  if new.journal_stored_bytes + v_bytes > new.journal_reserved_bytes then",
       "admission", "append_job_ceiling",
       "the settlement's room depends on its outcome, not the widest one (R39)"),
    _m("d4_empty_batch_publishes", JOURNAL, "  if v_count = 0 then", "  if false then",
       "admission", "append_empty", "an empty batch forbids a prepublication requeue"),
    _m("d4_sequence_restarts_per_batch", JOURNAL, "v_next + e.n", "e.n",
       "admission", "append", "the second batch collides with the first's cursors"),
    _m("d4_published_not_set", JOURNAL, "                        published = true\n",
       "                        published = published\n",
       "admission", "append", "published output is regenerated after a loss (02 §6)"),
    _m("d4_stored_bytes_not_counted", JOURNAL,
       "  update infrx.jobs set journal_stored_bytes = journal_stored_bytes + v_bytes,\n"
       "                        published = true",
       "  update infrx.jobs set journal_stored_bytes = journal_stored_bytes,\n"
       "                        published = true",
       "admission", "append", "stored output is never charged to the journal budget"),
    _m("d4_chunk_ttl_from_the_callers_clock", JOURNAL,
       "           v_now + make_interval(secs => v_ttl)",
       "           (v_lease->>'acquired_at')::timestamptz + make_interval(secs => v_ttl)",
       "admission", "append", "a chunk's retention runs from the worker's lease record (R7)"),
    # --- item 2: the global budget --------------------------------------------------------
    _m("d4_job_ceiling_is_the_whole_reservation_plus_one", JOURNAL, _J_CEILING,
       "       > j.journal_reserved_bytes + 1 then",
       "admission", "global_charge", "an append raises the global journal charge (DUR-CAP)"),
    _m("d4_stored_counted_beside_the_reservation", ADMISSION,
       "greatest(coalesce(r.amount, 0), j.journal_stored_bytes)",
       "coalesce(r.amount, 0) + j.journal_stored_bytes",
       "admission", "global_charge", "reserved and stored bytes are counted twice (02)"),
    # --- item 3: the terminal event -------------------------------------------------------
    _m("d4_terminal_trigger_on_any_update", JOURNAL,
       "after update of settled_at on infrx.jobs\n"
       "  for each row when (old.settled_at is null and new.settled_at is not null\n" + _J_WHEN,
       "after update on infrx.jobs\n  for each row when (new.journal_reserved_bytes > 0)",
       "admission", "terminal_every_path",
       "every update re-fires the trigger: killed via 0003's one-terminal index (review H4)"),
    _m("d4_terminal_payload_from_the_old_row", JOURNAL,
       "jsonb_build_object('state', new.state, 'cause', new.outcome_cause,",
       "jsonb_build_object('state', old.state, 'cause', old.outcome_cause,",
       "admission", "terminal_every_path", "the terminal event is not the stored outcome (R30)"),
    _m("d4_terminal_event_in_generation_one_always", JOURNAL,
       "  select c.generation, c.sequence + 1, c.expires_at - c.committed_at",
       "  select 1, c.sequence + 1, c.expires_at - c.committed_at",
       "admission", "terminal_every_path", "a replay ends before a second generation's output"),
    _m("d4_terminal_event_on_every_transition", JOURNAL,
       "after update of settled_at on infrx.jobs\n"
       "  for each row when (old.settled_at is null and new.settled_at is not null\n",
       "after update on infrx.jobs\n  for each row when (old.state is distinct from new.state\n",
       "admission", "terminal_every_path",
       "a queued job's journal shows a terminal event (review H4, a direct assertion)"),
    _m("d4_terminal_trigger_refires_on_the_same_instant", JOURNAL,
       "  for each row when (old.settled_at is null and new.settled_at is not null\n",
       "  for each row when (new.settled_at is not null\n",
       "admission", "terminal_every_path",
       "an idempotent rewrite of settled_at fails on a second terminal event (review J3)",
       expects_detail="stream_chunks_one_terminal_idx"),
    _m("d4_terminal_event_without_a_reservation", JOURNAL,
       "new.settled_at is not null\n" + _J_WHEN, "new.settled_at is not null)",
       "admission", "terminal_every_path",
       "a row with no journal reservation cannot terminalize (review M3)"),
    _m("d4_empty_journal_terminal_ttl_is_not_the_default", JOURNAL,
       "    v_ttl := interval '3600 seconds';", "    v_ttl := interval '30 seconds';",
       "admission", "terminal_every_path",
       "an early-cancelled stream answers journal_expired 30 s later (review J2)"),
    _m("d4_chunkless_terminal_event_at_the_first_generation", JOURNAL,
       "    select greatest(coalesce(max(a.generation), 1),",
       "    select greatest(coalesce(min(a.generation), 1),",
       "admission", "terminal_every_path",
       "a requeued job's end lands behind its last attempt (review H3a)"),
    _m("d4_no_terminal_event_on_cancel", JOURNAL, _J_WHEN,
       "                    and new.journal_reserved_bytes > 0 and new.state <> 'cancelled')",
       "admission", "terminal_every_path", "a cancelled stream never ends for its client"),
    _m("d4_terminal_event_legacy_regime_only", JOURNAL, _J_WHEN,
       "                    and new.journal_reserved_bytes > 0\n"
       "                    and new.accounting_regime = 'legacy_usd')",
       "admission", "credit_journal", "a CREDIT job's stream never ends (the journal is shared)"),
    _m("d4_terminal_event_bytes_uncounted", JOURNAL,
       "  update infrx.jobs set journal_stored_bytes = journal_stored_bytes + v_bytes\n"
       "   where request_id = new.request_id;\n", "",
       "admission", "expire_bytes", "pruning the terminal event frees bytes never charged"),
    _m("d4_terminal_event_below_the_watermark", JOURNAL,
       "    v_sequence := case when v_generation = new.journal_pruned_generation\n"
       "                       then new.journal_pruned_sequence + 1 else 1 end;",
       "    v_sequence := 1;",
       "admission", "expire_prefix", "the end of an expired journal is written unreadable"),
    # --- item 4: replay -------------------------------------------------------------------
    _m("d4_read_any_tenant", JOURNAL,
       "   where org_id = (p_args->>'org_id')::uuid and job_handle = p_args->>'job_handle';",
       "   where job_handle = p_args->>'job_handle';",
       "admission", "read_tenant", "another organization replays this job's output (R10)"),
    _m("d4_read_unbounded", JOURNAL, "           limit least(v_limit, 1000)) p;",
       "           limit v_limit) p;",
       "admission", "read_bounded", "one replay page reads a whole 16 MiB journal"),
    _m("d4_read_zero_limit_is_an_empty_page", JOURNAL,
       "  if v_limit is null or v_limit <= 0 then", "  if v_limit is null or v_limit < 0 then",
       "admission", "read_bounded", "a zero limit polls an empty page for ever"),
    _m("d4_order_by_sequence_only", JOURNAL,
       "           order by c.generation, c.sequence\n           limit",
       "           order by c.sequence\n           limit",
       "admission", "read_replay", "a replay splices two generations"),
    _m("d4_gap_is_an_empty_page", JOURNAL,
       "  if (v_position_generation, v_position_sequence)\n"
       "       < (j.journal_pruned_generation, j.journal_pruned_sequence) then",
       "  if false then",
       "admission", "read_typed", "a pruned prefix is skipped silently"),
    _m("d4_past_head_is_an_empty_page", JOURNAL,
       "  if (v_position_generation, v_position_sequence)\n"
       "       > (coalesce(v_head_generation, 0), coalesce(v_head_sequence, 0)) then",
       "  if false then",
       "admission", "read_typed", "a client polls a cursor the journal never issued"),
    _m("d4_cursor_overflows_as_an_integer", JOURNAL,
       "  v_position_generation numeric := coalesce((p_args->'cursor'->>'generation')::numeric, 0);",
       "  v_position_generation int := coalesce((p_args->'cursor'->>'generation')::int, 0);",
       "admission", "read_typed", "a client's Last-Event-ID becomes a 500, not a 400"),
    _m("d4_expired_journal_reads_empty", JOURNAL,
       "  if not found and j.journal_pruned_generation is not null then", "  if false then",
       "admission", "read_typed", "an expired journal reads as something other than 410"),
    # --- item 5: pruning and usage --------------------------------------------------------
    _m("d4_expire_on_the_callers_clock", JOURNAL,
       "least(coalesce((p_args->>'now')::timestamptz, v_db_now), v_db_now)",
       "coalesce((p_args->>'now')::timestamptz, v_db_now)",
       "admission", "expire_clock", "a caller destroys a live replay window (R7)"),
    _m("d4_prune_without_a_watermark", JOURNAL,
       "  update infrx.jobs set journal_stored_bytes = journal_stored_bytes - v_bytes,\n"
       "                          journal_pruned_generation = v_generation,\n"
       "                          journal_pruned_sequence = v_sequence\n",
       "  update infrx.jobs set journal_stored_bytes = journal_stored_bytes - v_bytes\n",
       "admission", "expire_prefix", "a pruned prefix replays as if nothing were missing"),
    _m("d4_prune_not_a_prefix", JOURNAL,
       "                   where job_id = v_job and (generation, sequence) <= "
       "(v_generation, v_sequence)",
       "                   where job_id = v_job and expires_at <= v_bound",
       "admission", "expire_prefix", "the watermark moves backwards over a missing chunk"),
    _m("d4_sequence_reissues_a_pruned_cursor", JOURNAL,
       "                       then j.journal_pruned_sequence else 0 end)",
       "                       then 0 else 0 end)",
       "admission", "expire_prefix", "new output is written below the watermark, unreadable"),
    _m("d4_prune_keeps_bytes_charged", JOURNAL, _J_PRUNE_BYTES,
       "journal_stored_bytes = journal_stored_bytes,",
       "admission", "expire_bytes", "pruned bytes are charged for ever (DUR-CAP)"),
    _m("d4_prune_frees_twice", JOURNAL, _J_PRUNE_BYTES,
       "journal_stored_bytes = journal_stored_bytes - v_bytes - v_bytes,",
       "admission", "expire_bytes", "pruning frees bytes still stored (DUR-CAP)"),
    _m("d4_usage_misreports_the_charge", JOURNAL,
       "    'charged_bytes', infrx.journal_bytes_charged(),", "    'charged_bytes', 0,",
       "admission", "usage", "the readiness probe reports a free journal that is full"),
    # --- item 6 ---------------------------------------------------------------------------
    _m("d4_expire_unbounded_per_pass", JOURNAL,
       "order by min(c.expires_at), c.job_id limit v_limit", "order by min(c.expires_at), c.job_id",
       "admission", "expire_clock", "one pruning transaction locks every expired job (H1)"),
    _m("d4_expire_empty_inner_resets_watermark", JOURNAL,
       "    if not found then\n      continue;\n    end if;\n    -- A prefix",
       "    -- A prefix",
       "admission", "journal_races",
       "a pruner with a stale candidate list resets a pruned job's watermark (review J1)"),
    _m("d4_expire_waits_on_a_locked_job", JOURNAL,
       "    perform 1 from infrx.jobs where request_id = v_job for update skip locked;",
       "    perform 1 from infrx.jobs where request_id = v_job for update;",
       "admission", "journal_races", "the pruner stalls behind every in-flight append"),
    # --- item 9b: privileges --------------------------------------------------------------
    _m("d4_append_granted_to_authenticated", JOURNAL,
       "-- `append` keeps 0004's grant (service_role only): `create or replace` preserves it.",
       "grant execute on function infrx.append(jsonb) to authenticated;",
       "admission", "journal_privileges", "a browser session writes a job's output"),
    _m("d4_journal_usage_granted_to_anon", JOURNAL,
       "grant execute on function infrx.journal_usage() to service_role;",
       "grant execute on function infrx.journal_usage() to service_role, anon;",
       "admission", "journal_privileges", "an anonymous caller reads the platform's load"),
    _m("d4_chunk_doc_callable_by_the_service", JOURNAL,
       "revoke all on function infrx.chunk_doc(infrx.stream_chunks)\n"
       "  from public, anon, authenticated, service_role;",
       "revoke all on function infrx.chunk_doc(infrx.stream_chunks)\n"
       "  from public, anon, authenticated;",
       "admission", "journal_privileges", "an internal helper widens the service surface"),
)
MUTANTS = MUTANTS + D4_MUTANTS


#: D5 (0018, plus one 0016 edit; scenario "admission"). One per claimed invariant, each
#: killed by its named `checks_settle` check.
from . import checks_operations, checks_settle  # noqa: E402
_S_REPLAY = (
    "  if found then\n"
    "    if j.accounting_regime <> v_regime then\n"
    "      perform infrx.refuse('not_found', 'job ' || j.request_id || ' is not settled in '\n"
    "                           || v_regime);\n"
    "    end if;\n"
    "    if j.settled_at is not null and (v_proposal = j.proposal or v_proposal =\n"
    "        jsonb_build_object('cause', j.outcome_cause, 'usage',\n"
    "                           infrx.usage_doc(j.usage_prompt_tokens, j.usage_completion_tokens),\n"
    "                           'result_ref', j.result_ref)) then\n"
    "      return infrx.job_admission(j.request_id);\n"
    "    end if;\n"
    "  end if;\n")
_S_FENCE = (
    "  -- 2. D3's fence: stale, foreign, expired, wrong-kind refused; R29 terminalizes, commits\n"
    "  -- and answers the refusal as data (R39). The job row is locked from here on.\n"
    "  v_refusal := infrx.fence_lease(p_args->'lease', array['inference'], v_reconcile_s);\n"
    "  if v_refusal is not null then\n"
    "    return jsonb_build_object('refusal', v_refusal);\n"
    "  end if;\n")
_S_DEBIT = ("  select round((p_prompt::numeric * (p_snapshot->>'input_rate_per_million')::numeric\n"
            "                + p_completion::numeric * (p_snapshot->>'output_rate_per_million')"
            "::numeric)\n"
            "               * 0.000001, 8)::numeric(20,8);")
_S_USD_LEDGER = (
    "    insert into public.credit_ledger (org_id, delta_usd, kind, reason, ref, request_id,\n"
    "                                      created_at)\n"
    "    values (j.org_id, -p_debit, 'usage', 'inference usage', j.job_handle, p_id,\n"
    "            j.settled_at);")
_S_CAUSE_CHECK = (
    "  if v_cause not in ('client_cancelled', 'client_disconnected', 'sync_deadline') then\n"
    "    perform infrx.refuse('invalid_request', v_cause || ' is not a cancellation cause');\n"
    "  end if;\n")
_S_CANCEL_LOOKUP = (
    "  select * into j from infrx.jobs\n"
    "   where job_handle = p_args->>'job_handle' and org_id = (p_args->>'org_id')::uuid\n"
    "   for update;\n"
    "  if not found then\n"
    "    perform infrx.refuse('not_found', 'no job ' || (p_args->>'job_handle') || ' owned by org '\n"
    "                         || (p_args->>'org_id'));\n"
    "  end if;\n")
_S_CREDIT_HOLD = (
    "    update infrx.credit_wallet_holds set state = 'settled'\n"
    "     where request_id = p_id and state = 'held';\n"
    "    if not found then\n"
    "      raise exception 'job %: its CREDIT hold is not held', p_id using errcode = '23514';\n"
    "    end if;\n")
_S_CREDIT_DEBIT = (
    "    insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id,\n"
    "                                     request_id, actor, reason, created_at)\n"
    "    select j.wallet_id, w.kind, 'inference_debit', -p_charged, p_id, p_id, 'platform',\n"
    "           'inference', j.settled_at\n"
    "      from infrx.credit_wallets w where w.wallet_id = j.wallet_id;\n")
D5_MUTANTS: tuple[Mutant, ...] = (
    # --- item 1: the legacy USD settling transaction --------------------------------------
    _m("d5_replay_after_the_fence", SETTLE, _S_REPLAY + _S_FENCE, _S_FENCE + _S_REPLAY,
       "admission", "settle_exact",
       "the winner's retry after a lost answer is `already_terminal`: a crash after the "
       "settling commit cannot be resolved"),
    _m("d5_settle_before_the_fence", SETTLE,
       "  v_refusal := infrx.fence_lease(p_args->'lease', array['inference'], v_reconcile_s);",
       "  v_refusal := null;", "admission", "settle_late_data",
       "a superseded or foreign worker settles a live job (DUR-FENCE): the check's first "
       "assertion, a foreign worker's completion answered instead of stale_lease"),
    _m("d5_result_ref_unchecked", SETTLE,
       "  if v_ref is not null and (v_ref <> 'infrx-result:' || j.request_id",
       "  if false and (v_ref <> 'infrx-result:' || j.request_id",
       "admission", "settle_result_ref", "a success settles for a result nobody stored (R30)"),
    _m("d5_foreign_result_ref_accepted", SETTLE,
       "  if v_ref is not null and (v_ref <> 'infrx-result:' || j.request_id\n      or not exists",
       "  if v_ref is not null and (not exists", "admission", "settle_result_ref",
       "job A is delivered with job B's result (R10/R30)"),
    _m("d5_disconnected_not_billable", SETTLE,
       "        or v_cause not in ('completed', 'client_cancelled', 'client_disconnected') then",
       "        or v_cause not in ('completed', 'client_cancelled') then",
       "admission", "settle_causes", "a client that disconnected after its usage is not charged"),
    _m("d5_sync_deadline_billed", SETTLE,
       "        or v_cause not in ('completed', 'client_cancelled', 'client_disconnected') then",
       "        or v_cause not in ('completed', 'client_cancelled', 'client_disconnected',\n"
       "                           'sync_deadline') then",
       "admission", "settle_causes",
       "a synchronous-deadline cancel with usage is billed; 0003's settled-cause CHECK then "
       "refuses the settlement (untyped 23514, a 500) instead of releasing it (R21)"),
    _m("d5_engine_incomplete_is_a_success", SETTLE,
       "  if v_in is null and v_cause = 'completed' and not j.published then",
       "  if false then", "admission", "settle_causes",
       "a delivered success nobody metered is a free success with a fetchable result"),
    _m("d5_debit_rounds_down", SETTLE, _S_DEBIT, _S_DEBIT.replace("round(", "trunc("),
       "admission", "settle_exact", "every half-unit tie is lost to the customer's favour"),
    _m("d5_debit_rounded_twice", SETTLE, _S_DEBIT,
       "  select (round(p_prompt::numeric * (p_snapshot->>'input_rate_per_million')::numeric"
       " * 0.000001, 8)\n"
       "          + round(p_completion::numeric * (p_snapshot->>'output_rate_per_million')"
       "::numeric * 0.000001, 8))::numeric(20,8);",
       "admission", "settle_exact", "each token line rounded on its own: two roundings (01 §4)"),
    _m("d5_debit_above_hold", SETTLE,
       "    if v_in > j.max_input_tokens or v_out > j.max_output_tokens\n"
       "       or v_charge > j.maximum_hold then",
       "    if v_in > j.max_input_tokens or v_out > j.max_output_tokens then",
       "admission", "settle_envelope",
       "a debit past the reserved hold is attempted and refused by the hold CHECK (untyped "
       "23514, a 500) instead of settling as a free platform error"),
    _m("d5_over_envelope_charged", SETTLE,
       "    if v_in > j.max_input_tokens or v_out > j.max_output_tokens\n",
       "    if false\n", "admission", "settle_envelope",
       "tokens nobody reserved are charged while the debit fits the hold"),
    _m("d5_wallet_total_written_directly", SETTLE,
       "    -- The one writer of ledger_total is the ledger row's trigger (0003).\n",
       "    update infrx.wallets set ledger_total = ledger_total - p_debit "
       "where org_id = j.org_id;\n", "admission", "settle_exact",
       "the total moves twice: the summary drifts from the ledger (D1 limit)"),
    _m("d5_usage_debit_without_ledger_row", SETTLE, _S_USD_LEDGER,
       "    update infrx.wallets set ledger_total = ledger_total - p_debit "
       "where org_id = j.org_id;", "admission", "settle_exact",
       "a debit with no ledger row: the summary is not the immutable ledger (5a)"),
    _m("d5_hold_not_moved_on_settle", SETTLE,
       "    update infrx.credit_holds set state = 'settled', updated_at = j.settled_at",
       "    update infrx.credit_holds set state = 'held', updated_at = j.settled_at",
       "admission", "settle_exact",
       "the reservation is released while the hold stays held: reserved drift (5a)"),
    _m("d5_reservation_kept", SETTLE,
       "  update infrx.capacity_reservations set active = false, released_at = v_now\n"
       "   where request_id = j.request_id and active;",
       "  update infrx.capacity_reservations set active = false, released_at = v_now\n"
       "   where false;", "admission", "settle_releases", "a settled job pins capacity for ever"),
    _m("d5_attempt_kept", SETTLE,
       "   where job_id = j.request_id and released_at is null;", "   where false;",
       "admission", "settle_releases", "a settled job's worker still holds a live attempt"),
    _m("d5_tombstone_not_started", SETTLE,
       "  update infrx.idempotency set expires_at = v_now + make_interval(secs => v_idem_ttl_s)",
       "  update infrx.idempotency set expires_at = null",
       "admission", "settle_releases", "a finished job's key is never released (01)"),
    _m("d5_projection_missing", SETTLE,
       "  values (gen_random_uuid(), j.request_id, j.org_id, 'usage_projection',",
       "  values (gen_random_uuid(), j.request_id, j.org_id, 'trace_projection',",
       "admission", "settle_releases", "usage never learns the job settled"),
    _m("d5_cost_is_not_the_debit", SETTLE,
       "    j.execution_mode = 'stream', j.usage_prompt_tokens, j.usage_completion_tokens, "
       "p_debit,", "    j.execution_mode = 'stream', j.usage_prompt_tokens, "
       "j.usage_completion_tokens, 0,", "admission", "settle_releases",
       "the usage page shows a free request the ledger charged"),
    _m("d5_result_retention_not_set", SETTLE,
       "                                  then v_now + make_interval(secs => v_result_ttl_s) end,",
       "                                  then v_now end,",
       "admission", "settle_result_ref", "a delivered result expires the instant it settles "
       "(D2 limit 9)"),
    _m("d5_usage_created_at_now", SETTLE,
       "    j.settled_at, 'pilot', j.outcome_cause, j.state, j.settlement_state, "
       "j.usage_certainty,\n    j.price_version,",
       "    now(), 'pilot', j.outcome_cause, j.state, j.settlement_state, j.usage_certainty,\n"
       "    j.price_version,", "admission", "settle_clock",
       "the usage row is dated by the wall clock, not the settlement (D1 limit)"),
    _m("d5_ledger_created_at_now", SETTLE,
       "    values (j.org_id, -p_debit, 'usage', 'inference usage', j.job_handle, p_id,\n"
       "            j.settled_at);",
       "    values (j.org_id, -p_debit, 'usage', 'inference usage', j.job_handle, p_id,\n"
       "            now());", "admission", "settle_clock",
       "the debit is dated by the wall clock, not the settlement (D1 limit)"),
    _m("d5_unknown_usage_released", SETTLE,
       "  if v_in is null and j.published then", "  if false then",
       "admission", "settle_unknown", "uncounted output drops out of reconciliation (02)"),
    _m("d5_window_from_the_callers_clock", SETTLE,
       "    v_reconcile := v_now + make_interval(secs => v_reconcile_s);",
       "    v_reconcile := (o->>'settled_at')::timestamptz + make_interval(secs => v_reconcile_s);",
       "admission", "settle_unknown", "a worker chooses when its unknown hold is released (R7)"),
    _m("d5_second_terminal_event", SETTLE,
       "  -- The money, last, in the job's own unit: hold, then wallet (no body reads both, R64).\n",
       "  insert into infrx.stream_chunks (job_id, generation, sequence, event_type, payload,\n"
       "    bytes, expires_at) values (j.request_id, 99, 1, 'terminal', '{}', 2, v_now);\n",
       "admission", "settle_terminal_event",
       "a second terminal event is written; the journal's unique terminal row refuses the "
       "settlement (untyped 23505, a 500)"),
    _m("d5_settled_usage_rewritable", SETTLE,
       "  if old.settled_at is not null\n     and (new.proposal is distinct from old.proposal",
       "  if false\n     and (new.proposal is distinct from old.proposal",
       "admission", "settle_late_data", "a settled outcome's usage is rewritten after the fact"),
    # --- item 3: the cancel cause ---------------------------------------------------------
    _m("d5_cancel_cause_ignored", SETTLE,
       "  return infrx.terminalize_no_usage(j.request_id, v_cause, 'cancelled',",
       "  return infrx.terminalize_no_usage(j.request_id, 'client_cancelled', 'cancelled',",
       "admission", "cancel_cause", "every cancellation is booked as the client's own (R21)"),
    _m("d5_cancel_accepts_any_cause", SETTLE,
       "  if v_cause not in ('client_cancelled', 'client_disconnected', 'sync_deadline') then",
       "  if false then", "admission", "cancel_refuses_a_cause",
       "a canceller records `completed` or a platform cause (R21)"),
    _m("d5_cancel_cause_checked_after_the_lookup", SETTLE,
       _S_CAUSE_CHECK + _S_CANCEL_LOOKUP, _S_CANCEL_LOOKUP + _S_CAUSE_CHECK,
       "admission", "cancel_refuses_a_cause",
       "a refused cause answers `not_found` for another tenant's job: it probes ownership"),
    _m("d5_sync_deadline_released_free", LEASES,
       "                    'client_cancelled', 'client_disconnected') then",
       "                    'client_cancelled', 'client_disconnected', 'sync_deadline') then",
       "admission", "cancel_cause", "our own synchronous timeout is booked as never charged"),
    # --- item 2: the CREDIT settlement and WorkV2 (D half) -----------------------------
    _m("d5_credit_debit_before_hold", SETTLE, _S_CREDIT_HOLD + _S_CREDIT_DEBIT,
       _S_CREDIT_DEBIT + _S_CREDIT_HOLD, "admission", "credit_settle",
       "the last affordable request fails its own settlement (D2's hard rule, D1R request 4)"),
    _m("d5_credit_settles_at_the_active_card", SETTLE,
       "  from infrx.rate_card_versions c where c.rate_card_version = p_card;",
       "  from infrx.rate_card_versions c where c.deployment_revision_id = (select\n"
       "    x.deployment_revision_id from infrx.rate_card_versions x where x.rate_card_version\n"
       "    = p_card) order by c.effective_at desc, c.created_at desc limit 1;",
       "admission", "credit_admitted_card", "a job is charged a card published while it ran (R68)"),
    _m("d5_credit_debits_the_usd_wallet", SETTLE, _S_CREDIT_DEBIT,
       "    insert into public.credit_ledger (org_id, delta_usd, kind, request_id, created_at)\n"
       "    values (j.org_id, -p_charged, 'usage', p_id, j.settled_at);\n",
       "admission", "credit_usd_untouched",
       "a CREDIT charge lands on the organization's USD ledger (R64/R65)"),
    _m("d5_credit_debit_in_float", SETTLE,
       "  select round((p_prompt::numeric * c.input_rate_per_million\n"
       "                + p_completion::numeric * c.output_rate_per_million)\n"
       "               * 0.000001, 8)::numeric(20,8)",
       "  select round(((p_prompt::float8 * c.input_rate_per_million::float8\n"
       "                + p_completion::float8 * c.output_rate_per_million::float8)\n"
       "               * 0.000001)::numeric, 8)::numeric(20,8)",
       "admission", "credit_grid",
       "a CREDIT charge computed in binary floating point is off in the 8th place "
       "(review N1, the reviewer's rv_credit_debit_in_float)"),
    _m("d5_legacy_debit_in_float", SETTLE,
       "  select round((p_prompt::numeric * (p_snapshot->>'input_rate_per_million')::numeric\n"
       "                + p_completion::numeric * (p_snapshot->>'output_rate_per_million')::numeric)\n"
       "               * 0.000001, 8)::numeric(20,8);",
       "  select round(((p_prompt::float8 * (p_snapshot->>'input_rate_per_million')::float8\n"
       "                + p_completion::float8 * (p_snapshot->>'output_rate_per_million')::float8)\n"
       "               * 0.000001)::numeric, 8)::numeric(20,8);",
       "admission", "settle_exact",
       "a USD debit computed in binary floating point is off in the 8th place (review N1)"),
    _m("d5_credit_debit_rounds_down", SETTLE,
       "  select round((p_prompt::numeric * c.input_rate_per_million",
       "  select trunc((p_prompt::numeric * c.input_rate_per_million",
       "admission", "credit_grid", "every half-unit CREDIT tie is lost (02-credits half_up_8)"),
    _m("d5_frozen_wallet_refuses_its_own_debit", "0015_signup_eligibility.sql",
       "  for each row when (new.kind = 'signup_grant')",
       "  for each row when (new.kind in ('signup_grant', 'inference_debit'))",
       "admission", "credit_retired",
       "a retired individual's in-flight job can never settle (A1 request 7, R85)"),
    _m("d5_charged_credits_not_recorded", SETTLE,
       "    j.execution_mode, j.trace_mode, 'credit', p_charged, j.rate_card_version,",
       "    j.execution_mode, j.trace_mode, 'credit', 0, j.rate_card_version,",
       "admission", "credit_settle", "the usage page shows a free request the ledger charged"),
    _m("d5_credit_v1_debit_nonzero", SETTLE,
       "         debit = case when j.accounting_regime = 'credit' then 0 else v_charge end,",
       "         debit = v_charge,", "admission", "credit_settle",
       "a CREDIT charge is read from the USD debit field (R64)"),
    _m("d5_regimes_cross", SETTLE,
       "    if j.accounting_regime <> v_regime then", "    if false then",
       "admission", "credit_regimes",
       "a v1 caller settles a CREDIT job and reads a zero USD debit (R64)"),
    _m("d5_load_work_credit_reads_current_pins", SETTLE,
       "    'admission', infrx.job_admission(j.request_id),\n    'policy',",
       "    'admission', infrx.job_admission(j.request_id) || jsonb_build_object('rate_card',\n"
       "      (select to_jsonb(c) from infrx.rate_card_versions c\n"
       "        where c.deployment_revision_id = j.deployment_revision_id\n"
       "        order by c.effective_at desc, c.created_at desc limit 1)),\n    'policy',",
       "admission", "credit_admitted_card", "the worker is handed the card published now (R68/R78)"),
    _m("d5_load_work_credit_current_policy", SETTLE,
       "                 from infrx.data_access_policies p where p.policy_version = "
       "j.policy_version));",
       "                 from infrx.data_access_policies p order by p.effective_at desc "
       "limit 1));", "admission", "credit_admitted_card",
       "a later policy widens what an accepted request allowed"),
    _m("d5_claim_refuses_credit_again", SETTLE,
       "  if j.state <> 'queued' then\n",
       "  if j.accounting_regime = 'credit' then\n"
       "    perform infrx.refuse('not_claimable', 'no v2 work loader');\n  end if;\n"
       "  if j.state <> 'queued' then\n",
       "admission", "credit_settle", "the lift's control: a CREDIT job is never leased (MY-3)"),
    # --- item 6: races -------------------------------------------------------------------
    _m("d5_settle_without_the_row_lock", SETTLE,
       "  select * into j from infrx.jobs where request_id = (o->>'job_id')::uuid for update;",
       "  select * into j from infrx.jobs where request_id = (o->>'job_id')::uuid;",
       "admission", "settle_races",
       "a duplicate completion behind the winner is refused instead of replayed: a worker "
       "that lost the answer cannot resolve its own settlement"),
    _m("d5_takes_the_scope_lock", SETTLE,
       "  v_proposal := jsonb_build_object('cause', v_cause, 'usage',",
       "  perform pg_advisory_xact_lock(infrx.admission_lock_key());\n"
       "  v_proposal := jsonb_build_object('cause', v_cause, 'usage',",
       "admission", "settle_races",
       "every settlement queues behind admission's global lock (0011's order inverted)"),
    # review B2 / N5: grant_credit under real transactions
    _m("d5_grant_without_wallet_lock", SETTLE,
       "   where wallet_id = (p_args->>'wallet_id')::uuid for update;",
       "   where wallet_id = (p_args->>'wallet_id')::uuid;", "admission", "settle_races",
       "an operator's racing retry of one operation is refused instead of replayed "
       "(the reviewer's rv_grant_without_wallet_lock)"),
    _m("d5_grant_concurrent_reuse_untyped", SETTLE,
       "  exception when unique_violation then\n    -- The wallet lock serializes",
       "  exception when division_by_zero then\n    -- The wallet lock serializes",
       "admission", "settle_races",
       "one operation id racing on two wallets surfaces as an untyped unique violation "
       "(a 500) instead of idempotency_conflict"),
    # --- item 4: operator money ----------------------------------------------------------
    _m("d5_adjust_replay_appends", SETTLE,
       "  select * into l from infrx.credit_ledger where operation_id = v_op;\n  if found then",
       "  select * into l from infrx.credit_ledger where operation_id = v_op;\n  if false then",
       "admission", "adjust",
       "an operator's retry of one operation is refused (idempotency_conflict via the unique "
       "operation id) instead of answered replayed - the id stops a double move"),
    # review B1: each part of "another movement" is its own defect
    _m("d5_grant_replay_ignores_wallet", SETTLE,
       "    if l.wallet_id <> w.wallet_id or l.kind <> v_kind or", "    if l.kind <> v_kind or",
       "admission", "adjust",
       "an operation id reused on ANOTHER wallet answers `replayed` and moves nothing there "
       "(the reviewer's rv_replay_ignores_wallet)"),
    # verifier V-N4: the CREDIT detector's hold half (0006's view; assert_no_drift reads it)
    _m("d5_credit_reserved_drift_blind", CREDIT,
       "       w.reserved_total - coalesce((select sum(h.amount) from infrx.credit_wallet_holds h\n"
       "                                    where h.wallet_id = w.wallet_id\n"
       "                                      and h.state in ('held', 'unknown')), 0) as reserved_drift",
       "       0::numeric as reserved_drift", "admission", "drift_detected",
       "a CREDIT wallet reserving more than its active holds is never reported: "
       "assert_no_drift goes blind to hold drift (the verifier's om8)"),
    # verifier V-N2: the actor and reason are not part of the movement
    _m("d5_grant_actor_is_part_of_the_movement", SETTLE,
       "    if l.wallet_id <> w.wallet_id or l.kind <> v_kind or l.amount <> v_amount then",
       "    if l.wallet_id <> w.wallet_id or l.kind <> v_kind or l.amount <> v_amount\n"
       "       or l.actor <> btrim(p_args->>'actor') then", "admission", "adjust",
       "an operator's retry of one movement under another actor is refused "
       "idempotency_conflict instead of replayed (the verifier's om1)"),
    _m("d5_grant_replay_ignores_kind", SETTLE,
       "l.wallet_id <> w.wallet_id or l.kind <> v_kind or l.amount",
       "l.wallet_id <> w.wallet_id or l.amount", "admission", "adjust",
       "an operation id reused for another KIND of movement answers `replayed` "
       "(the reviewer's rv_replay_ignores_kind)"),
    _m("d5_adjust_without_audit", SETTLE, "         'grant_credit:' || v_op;",
       "         'grant_credit:' || v_op where false;", "admission", "adjust",
       "an operator moved credit and nothing records who or why (R34)"),
    _m("d5_adjust_below_reserved", SETTLE,
       "  if w.ledger_total + v_amount < w.reserved_total then", "  if false then",
       "admission", "adjust", "an adjustment below the holds surfaces as a spend refusal (402)"),
    _m("d5_allocation_to_consumer_wallet", SETTLE,
       "  if v_kind = 'operator_allocation' and w.kind <> 'provider_dev' then", "  if false then",
       "admission", "allocation", "consumer credit minted by allocation (0006 kind rule, 500)"),
    _m("d5_amount_through_float", SETTLE, "  v_amount := v_text::numeric;",
       "  v_amount := v_text::float8::numeric;", "admission", "allocation",
       "a full-precision operator amount is rounded through binary floating point "
       "(review N4)"),
    _m("d5_amount_not_bounded", SETTLE,
       "     or v_text !~ '^-?[0-9]{1,12}(\\.[0-9]{1,8})?$' then", "     or false then",
       "admission", "adjust", "an over-scale or exponent amount reaches the ledger (R11)"),
    _m("d5_reconcile_on_callers_clock", READS,  # D10: 0021 redefines `reconcile`
      
       "  v_now timestamptz := infrx.now();\n  v_key text := 'reconcile:'",
       "  v_now timestamptz := coalesce((p_args->>'at')::timestamptz, infrx.now());\n"
       "  v_key text := 'reconcile:'", "admission", "reconcile_clock",
       "an operator releases an unknown hold before its window (R7)"),
    _m("d5_reconcile_debits", READS,  # D10: 0021 redefines `reconcile`
      
       "    perform infrx.release_aged_unknown(j.request_id, v_now);\n",
       "    perform infrx.release_aged_unknown(j.request_id, v_now);\n"
       "    insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id,\n"
       "      request_id, actor) select j.wallet_id, w.kind, 'inference_debit', -1,\n"
       "      gen_random_uuid(), j.request_id, 'late' from infrx.credit_wallets w\n"
       "      where w.wallet_id = j.wallet_id;\n", "admission", "reconcile_clock",
       "late evidence becomes a delayed customer debit (02)"),
    _m("d5_reconcile_any_tenant", READS,  # D10: 0021 redefines `reconcile`
      
       "   where request_id = (p_args->>'request_id')::uuid and org_id = (p_args->>'org_id')"
       "::uuid\n   for update;",
       "   where request_id = (p_args->>'request_id')::uuid\n   for update;",
       "admission", "reconcile_tenant", "an operator path reconciles another tenant's request"),
    _m("d5_reconcile_replay_audits_again", READS,  # D10: 0021 redefines `reconcile`
      
       "  select * into a from infrx.audit_entries where idempotency_key = v_key;\n"
       "  if found then",
       "  select * into a from infrx.audit_entries where idempotency_key = v_key;\n"
       "  if false then", "admission", "reconcile_clock",
       "a retried reconcile fails on its own audit row (untyped 23505, a 500)"),
    # review B3 / H-B2 / CF-8: the replay is THIS request's operation
    _m("d5_reconcile_replay_any_request", READS,  # D10: 0021 redefines `reconcile`
      
       "    if a.after->>'request_id' is distinct from j.request_id::text then",
       "    if false then", "admission", "reconcile_clock",
       "an operation id reused for ANOTHER request answers `replayed` and that request's "
       "unknown hold stays reserved"),
    # --- item 10a: the privilege surface --------------------------------------------------
    _m("d5_reconcile_granted_to_anon", SETTLE,
       "    execute format('grant execute on function %s to service_role', f);",
       "    execute format('grant execute on function %s to service_role, anon', f);",
       "admission", "d5_privileges", "an anonymous browser releases unknown-usage holds"),
    _m("d5_grant_credit_granted_to_authenticated", SETTLE,
       "comment on function infrx.grant_credit(jsonb) is",
       "grant execute on function infrx.grant_credit(jsonb) to authenticated;\n"
       "comment on function infrx.grant_credit(jsonb) is", "admission", "d5_privileges",
       "a signed-in browser adjusts a wallet"),
    _m("d5_settle_helper_callable_by_the_service", SETTLE,
       "      'infrx.settle_legacy_usd(uuid, numeric)', 'infrx.settle_credit(uuid, numeric)']",
       "      'infrx.settle_legacy_usd(uuid, numeric)']", "admission", "d5_privileges",
       "the platform role debits a CREDIT wallet outside any settlement"),
    # --- R91: the read-only lookup -------------------------------------------------------
    _m("d5_lookup_any_payload", SETTLE,
       "  if i.payload_digest <> v_idem->>'payload_hash' then\n"
       "    perform infrx.refuse('idempotency_conflict',\n"
       "                         'same idempotency key, different canonical payload');\n"
       "  end if;\n  return infrx.job_admission(i.request_id) || '{\"replayed\": true}';",
       "  return infrx.job_admission(i.request_id) || '{\"replayed\": true}';",
       "admission", "lookup", "a changed body is answered another request's job (R6)"),
    _m("d5_lookup_ignores_operation", SETTLE,
       "   where org_id = (v_idem->>'org_id')::uuid and operation = v_idem->>'operation'\n"
       "     and key = v_idem->>'key';",
       "   where org_id = (v_idem->>'org_id')::uuid\n     and key = v_idem->>'key';",
       "admission", "lookup",
       "a key reused under another operation answers that operation's job, or a false "
       "idempotency_conflict (review N2, the reviewer's rv_lookup_ignores_operation)"),
    _m("d5_lookup_not_stable", SETTLE,
       "create or replace function infrx.idempotency_lookup(p_args jsonb) returns jsonb\n"
       "language plpgsql stable security definer",
       "create or replace function infrx.idempotency_lookup(p_args jsonb) returns jsonb\n"
       "language plpgsql volatile security definer", "admission", "d5_privileges",
       "the read-only lookup may write (nothing but the footprint check stops it; review N3)"),
    _m("d5_lookup_expired_answers", SETTLE,
       "  if v_expires is not null and infrx.now() >= v_expires then\n    return null;",
       "  if false then\n    return null;", "admission", "lookup",
       "an expired mapping keeps answering its old job instead of none"),
    _m("d5_lookup_active_mapping_expires", SETTLE,
       "  select coalesce(i.expires_at, j.settled_at + make_interval(",
       "  select coalesce(i.expires_at, j.admitted_at + make_interval(",
       "admission", "lookup", "an active job's mapping expires under it (01)"),
    _m("d5_lookup_any_org_scope", SETTLE,
       "  if v_idem->>'org_id' is distinct from p_args->>'org_id' then\n"
       "    perform infrx.refuse('forbidden', 'the idempotency scope must name the caller''s "
       "org');",
       "  if false then\n"
       "    perform infrx.refuse('forbidden', 'the idempotency scope must name the caller''s "
       "org');", "admission", "lookup", "one organization reads another's jobs by key (R10)"),
    # --- item 5b: the released record -----------------------------------------------------
    _m("d5_release_reported_as_outcome", SETTLE,
       "  return jsonb_build_array(jsonb_build_object('released',",
       "  return jsonb_build_array(jsonb_build_object('outcome',",
       "admission", "settle_released", "a 24 h release is counted as a second terminalization"),
)
MUTANTS = MUTANTS + D5_MUTANTS


#: R83: an anchor that appears zero or twice is `misdeclared` - an edit landing on
#: whichever line came first is not the declared defect (D2 review H4).
MISDECLARED = "misdeclared"


_FUNCTION = re.compile(r"create or replace function ([\w.]+)\s*\(", re.IGNORECASE)


def superseded(mutants) -> list[str]:
    """D5 item 10b (D4 design decision 4, now structural): the mutants whose anchor lies
    inside a `create or replace function X` body (`$$ ... $$`) that a LATER-numbered
    migration redefines - an edit there changes SQL nothing runs, so its "kill" would be
    the redefinition's, not the check's."""
    files = sorted(migrations.migrations(), key=lambda path: path.name)
    found = []
    for mutant in mutants:
        if mutant.file == SEED:
            continue
        text = (migrations.DIR / mutant.file).read_text()
        at = text.find(mutant.old)
        later = [path for path in files if path.name > mutant.file]
        for match in _FUNCTION.finditer(text):
            start = text.find("$$", match.end())
            end = text.find("$$", start + 2)
            if not start <= at < end:
                continue
            again = re.compile(rf"create or replace function {re.escape(match.group(1))}\s*\(",
                               re.IGNORECASE)
            redefined = [path.name for path in later if again.search(path.read_text())]
            if redefined:
                found.append(f"{mutant.name}: anchored in {match.group(1)} of {mutant.file}, "
                             f"redefined by {redefined}")
    return found


def anchor_count(mutant: Mutant) -> int:
    source = migrations.DIR / mutant.file if mutant.file != SEED else migrations.SEED_MARLIN
    return source.read_text().count(mutant.old)


def _mutate(directory: Path, mutant: Mutant) -> str | None:
    """Apply the edit to a copy; the reason it cannot be applied, or None."""
    for path in (*migrations.migrations(), migrations.SEED_MARLIN):
        shutil.copy(path, directory / path.name)
    target = directory / mutant.file
    text = target.read_text()
    found = text.count(mutant.old)
    if found != mutant.occurrences:
        return (f"its anchor appears {found} times in {mutant.file}, expected "
                f"{mutant.occurrences}")
    target.write_text(text.replace(mutant.old, mutant.new))
    return None


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
    # D1R review (b): the two unit checks, each with its own mutant.
    "no_unit_conversion": checks_credit.check_no_unit_conversion,
    "money_unit_cases": checks_credit.check_money_unit_cases,
    # D1R, scenario "upgrade05": need the captured state (see `_upgrade05_check`).
    "legacy_schema_unchanged": None,
    "old_regime_preserved": None,
    "legacy_read_path": None,
    "flag_defaults": None,
    "rerun": None,
    # D2, scenario "admission" (all migrations + frozen clock + seed_admission).
    "admission_accepts": checks_admission.check_admission_accepts,
    "admission_refusals": checks_admission.check_admission_refusals,
    "admission_capacity": checks_admission.check_admission_capacity,
    "admission_idempotency": checks_admission.check_admission_idempotency,
    "admission_concurrency": lambda conn: checks_admission.check_admission_concurrency(
        pgharness.connect, MUT_DB),
    "prepare_transition": checks_dispatch.check_prepare_transition,
    "preparation_deadline": checks_dispatch.check_preparation_deadline_terminalizes,
    "credit_terminalization": checks_dispatch.check_credit_job_terminalization_releases_credit,
    "dispatch_relay": checks_dispatch.check_dispatch_relay,
    "dispatch_details": checks_dispatch.check_dispatch_details,
    "preparation_claim_race": lambda conn: checks_dispatch.check_preparation_claim_race(
        pgharness.connect, MUT_DB),
    "d2_function_privileges": checks_admission.check_d2_function_privileges,
    "outbox_gc": checks_dispatch.check_outbox_gc,
    "results_and_prompt_tokens": checks_dispatch.check_results_and_prompt_tokens,
    "media_uploads": checks_media.check_media_uploads,
    "media_objects": checks_media.check_media_objects,
    "media_privileges": checks_media.check_media_privileges,
    # D3, scenario "admission".
    "claim_generation": checks_leases.check_claim_generation,
    "lease_fence": checks_leases.check_fence,
    "preparation_fence": checks_leases.check_preparation_fence,
    "cancel": checks_leases.check_cancel,
    "recover_requeue": checks_leases.check_recover_requeue,
    "recover_preparation": checks_leases.check_recover_preparation,
    "recover_unknown_release": checks_leases.check_recover_unknown_release,
    "recover_isolation": checks_leases.check_recover_isolation,
    "lease_privileges": checks_leases.check_lease_privileges,
    "lease_races": lambda conn: checks_leases.check_lease_races(pgharness.connect, MUT_DB),
    # D4, scenario "admission".
    "append": checks_journal.check_append,
    "append_oversize": checks_journal.check_append_oversize,
    "append_terminal_refused": checks_journal.check_append_terminal_refused,
    "append_job_ceiling": checks_journal.check_append_job_ceiling,
    "append_empty": checks_journal.check_append_empty,
    "append_fenced": checks_journal.check_append_fenced,
    "append_past_the_instant": checks_journal.check_append_past_the_instant,
    "global_charge": checks_journal.check_global_charge,
    "terminal_every_path": checks_journal.check_terminal_every_path,
    "read_replay": checks_journal.check_read_replay,
    "read_tenant": checks_journal.check_read_tenant,
    "read_bounded": checks_journal.check_read_bounded,
    "read_typed": checks_journal.check_read_typed,
    "expire_clock": checks_journal.check_expire_clock,
    "expire_prefix": checks_journal.check_expire_prefix,
    "expire_bytes": checks_journal.check_expire_bytes,
    "usage": checks_journal.check_usage,
    "credit_journal": checks_journal.check_credit_journal,
    "journal_privileges": checks_journal.check_journal_privileges,
    "journal_races": lambda conn: checks_journal.check_journal_races(pgharness.connect, MUT_DB),
    # D5, scenario "admission".
    "settle_exact": checks_settle.check_settle_exact,
    "settle_late_data": checks_settle.check_settle_late_data,
    "settle_result_ref": checks_settle.check_settle_result_ref,
    "settle_causes": checks_settle.check_settle_causes,
    "settle_envelope": checks_settle.check_settle_envelope,
    "settle_unknown": checks_settle.check_settle_unknown,
    "settle_releases": checks_settle.check_settle_releases,
    "settle_clock": checks_settle.check_settle_clock,
    "settle_terminal_event": checks_settle.check_settle_terminal_event,
    "cancel_cause": checks_settle.check_cancel_cause,
    "cancel_refuses_a_cause": checks_settle.check_cancel_refuses_a_cause,
    "settle_released": checks_settle.check_settle_released,
    "lookup": checks_settle.check_lookup,
    "credit_settle": checks_settle.check_credit_settle,
    "credit_grid": checks_settle.check_credit_grid,
    "credit_usd_untouched": checks_settle.check_credit_usd_untouched,
    "credit_regimes": checks_settle.check_credit_regimes,
    "credit_admitted_card": checks_settle.check_credit_rate,
    "credit_retired": checks_settle.check_credit_retired,
    "adjust": checks_operations.check_adjust,
    "drift_detected": checks_settle.check_drift_detected,
    "allocation": checks_operations.check_allocation,
    "reconcile_clock": checks_operations.check_reconcile_clock,
    "reconcile_tenant": checks_operations.check_reconcile_tenant,
    "d5_privileges": checks_operations.check_d5_privileges,
    "settle_races": lambda conn: checks_settle.check_settle_races(pgharness.connect, MUT_DB),
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
        refused = _mutate(directory, mutant)
        if refused is not None:
            return MISDECLARED, refused
        files = migrations.sql_for(shim=pgharness.NEEDS_SHIM, directory=directory)
        if mutant.scenario in ("credit", "upgrade05", "credit_volume", "admission"):
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
    if mutant.scenario == "admission":
        try:
            with pgharness.connect(MUT_DB) as conn:
                checks_admission.seed_admission(conn)
                return _run(_CHECKS[mutant.check], conn)
        except (AssertionError, psycopg.Error) as during_setup:
            return SETUP_ERROR, _first_line(during_setup)
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
    """Only an AssertionError from the named check is a kill (the shared rule).

    A check that raises anything else - `psycopg.Error` above all - blew up instead of
    asserting: a broken check, not a defended invariant, and it must be visible as such.
    """
    result = shared.assertion_kill(check, *args)
    if result.outcome is Outcome.broken_runner:
        return SETUP_ERROR, result.detail
    return (KILLED if result.killed else SURVIVED), result.detail

def _first_line(error: BaseException) -> str:
    # An `AssertionError()` with no message is still a failure to report, not a crash of
    # the runner reading it (D2 review H3).
    lines = str(error).strip().splitlines()
    return f"{type(error).__name__}: {(lines[0] if lines else '(no message)')[:160]}"
from . import signup_mutants  # noqa: E402,F401  A1 (0015): appends its mutants and checks
from . import d10_mutants  # noqa: E402,F401  D10 (0019+): appends its mutants and checks
