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


@dataclass(frozen=True)
class Mutant:
    name: str
    file: str
    old: str
    new: str
    scenario: str            # fresh | upgrade | volume | prodlike
    check: str               # the check that must fail
    why: str                 # what would be wrong in production


MUTANTS: tuple[Mutant, ...] = (
    # --- a dropped REVOKE / a widened grant ---------------------------------
    Mutant("organizations_update_not_revoked", ROLES,
           "revoke update on public.organizations from anon, authenticated;",
           "-- mutant: the table-level UPDATE grant stays",
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
    Mutant("profiles_operator_column_grant_widened", "0001_init.sql",
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
    Mutant("historical_usage_enters_the_settlement_regime", SCHEMA,
           "add column if not exists settlement_regime text not null default 'legacy',",
           "add column if not exists settlement_regime text not null default 'pilot',",
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
           "    check (debit = 0 or (settlement_state = 'settled'\n"
           "                         and outcome_cause in ('completed','client_cancelled',\n"
           "                                               'client_disconnected'))),",
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
    Mutant("ledger_actor_is_never_masked", CONSOLE,
           "       case when l.by_operator and not (public.is_operator() "
           "or public.is_service_client())\n"
           "            then 'platform'\n"
           "            else coalesce(l.operator_principal, p.email, l.created_by::text) "
           "end as actor,",
           "       coalesce(l.operator_principal, p.email, l.created_by::text) as actor,",
           "fresh", "console_read_surface",
           "a customer session reads the operator's identity off a grant (R41/R50)"),
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
    Mutant("wallet_summary_answers_for_any_organization", CONSOLE,
           "  if not (public.is_org_member(p_org) or public.is_operator()\n"
           "          or public.is_service_client()) then",
           "  if false then",
           "fresh", "console_read_surface",
           "org_wallet_summary reports another tenant's balance to any signed-in user"),
    Mutant("api_keys_update_not_narrowed", CONSOLE,
           "revoke update on public.api_keys from anon, authenticated;",
           "-- mutant: the broad owner UPDATE grant stays",
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
    assert found == 1, (f"mutant {mutant.name}: its target appears {found} times in "
                        f"{mutant.file}; a mutant must be one edit in one place")
    target.write_text(text.replace(mutant.old, mutant.new))


_CHECKS = {
    "relations_exist": checks.check_relations_exist,
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


def kill(mutant: Mutant) -> str | None:
    """Build a database from the mutated migrations and run the check that claims the
    invariant. Returns the failure that killed the mutant, or None if it survived."""
    pgharness.ensure()
    with TemporaryDirectory(prefix=f"infrx-d1-{mutant.name}-") as tmp:
        directory = Path(tmp)
        _mutate(directory, mutant)
        files = migrations.sql_for(directory=directory)
        database = MUT_PRODLIKE_DB if mutant.scenario == "prodlike" else MUT_DB
        try:
            if mutant.scenario == "upgrade":
                pgharness.recreate(database)
                current = tuple(f for f in files if f[0] in (
                    "supabase_shim.sql", "0001_init.sql", "0002_seed_models.sql"))
                pgharness.apply(database, current)
                with pgharness.connect(database) as conn:
                    before = checks.seed_legacy(conn)
                    pgharness.apply(database, tuple(f for f in files if f not in current))
                    return _report(checks.check_upgrade_preserved, conn, before)
            pgharness.recreate(database)
            pgharness.apply(database, files)
            with pgharness.connect(database) as conn:
                if mutant.scenario in ("fresh", "volume"):
                    checks.seed_fixtures(conn)
                if mutant.scenario == "volume":
                    checks.seed_volume(conn)
                return _report(_CHECKS[mutant.check], conn)
        except (AssertionError, psycopg.Error) as during_setup:
            # A mutant the schema itself refuses (a constraint the migration can no
            # longer satisfy, a fixture it can no longer store) is killed too - by the
            # migration rather than by the check. Say which.
            return f"setup: {_first_line(during_setup)}"


def _report(check, *args) -> str | None:
    try:
        check(*args)
    except (AssertionError, psycopg.Error) as failure:
        return _first_line(failure)
    return None


def _first_line(error: BaseException) -> str:
    return f"{type(error).__name__}: {str(error).strip().splitlines()[0][:160]}"
