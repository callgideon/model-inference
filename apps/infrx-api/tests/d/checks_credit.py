"""D1R: the invariants the additive CREDIT / provider schema (0006 onward) claims.

Same contract as `checks.py`: each check raises `AssertionError` on failure and returns a
short summary on success, so `migration_mutants.py` can run it against a single-edit
mutant of a migration (R32/R40). Fixture identities come from `checks.py` where the D1
fixture already has them.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

import psycopg
from infrx.state import migrations

from . import checks
from .checks import KEY_A

#: The migrations the hosted-state inventory and the upgrade fixture treat as "before".
BEFORE_D1R = ("supabase_shim.sql", "0001_init.sql", "0002_seed_models.sql",
              "0003_pilot_durable_schema.sql", "0004_pilot_roles_and_rpcs.sql",
              "0005_console_read_surface.sql")

# --- old-regime jobs accepted under 0003-0005 (item 1) -------------------------
OLD_QUEUED = "5a000000-0000-4000-8000-000000000001"     # accepted, USD hold held
OLD_SETTLED = "5a000000-0000-4000-8000-000000000002"    # settled in USD, pilot usage row
OLD_UNKNOWN = "5a000000-0000-4000-8000-000000000003"    # usage unknown, hold reserved


def split(files):
    """(the 0001-0005 schema, everything D1R adds) from `migrations.sql_for(...)`."""
    before = tuple(f for f in files if f[0] in BEFORE_D1R)
    after = tuple(f for f in files if f[0] not in BEFORE_D1R and f[0] != "test_clock.sql")
    clock = tuple(f for f in files if f[0] == "test_clock.sql")
    return before, after + clock


def seed_old_regime(conn, org_a: str) -> None:
    """Accepted pilot-regime (USD) jobs on the exact 0005 schema: one in flight with a
    held USD hold, one settled with its pilot usage row and USD debit, one whose usage is
    unknown and whose hold is still reserved. `seed_legacy` already put users, keys and
    positive/negative/sign-violating USD history in place."""
    cols = checks._JOB_COLUMNS.replace(",\n  accounting_regime", "")   # 0005 has no regime
    common = (f"'{org_a}', '{KEY_A}', 'nemostation/marlin-2b@2026-09-01', 'stream', "
              "@STATE, 'chat.completions', 'infrx-payload:@RID', "
              f"'{checks.DIGEST}', 4096, 512, 'pv-old', "
              "'{\"price_version\":\"pv-old\"}'::jsonb, 1.25000000, 1, 'full', "
              "'2026-09-21T00:00:00Z', '2026-09-21T00:10:00Z', 120, 10, 300, 60, 20, "
              "'2026-09-21T00:02:00Z'")
    conn.execute("""
    insert into infrx.price_versions (price_version, model_revision, input_rate_per_million,
      output_rate_per_million, token_rules_version, effective_from)
    values ('pv-old', 'nemostation/marlin-2b@2026-09-01', 0.20000000, 0.60000000, 'tr-1',
            '2026-01-01T00:00:00Z')""")

    def job(rid, handle, state, extra_cols="", extra_vals=""):
        conn.execute(f"insert into infrx.jobs ({cols}{extra_cols}) values ('{rid}', "
                     f"'{handle}', " + common.replace("@STATE", f"'{state}'").replace("@RID", rid)
                     + f"{extra_vals})")

    job(OLD_QUEUED, "job_old_queued", "queued")
    job(OLD_SETTLED, "job_old_settled", "succeeded",
        ", result_ref, outcome_cause, settlement_state, usage_certainty, debit, settled_at",
        ", 'infrx-result:old', 'completed', 'settled', 'authoritative', 0.50000000, "
        "'2026-09-21T00:05:00Z'")
    job(OLD_UNKNOWN, "job_old_unknown", "failed",
        ", outcome_cause, settlement_state, usage_certainty, settled_at, reconcile_after",
        ", 'engine_error', 'held_unknown', 'unknown', '2026-09-21T00:05:00Z', "
        "'2026-09-22T00:05:00Z'")
    conn.execute(f"""
    insert into infrx.credit_holds (request_id, org_id, key_id, amount, state, reconcile_after)
    values ('{OLD_QUEUED}', '{org_a}', '{KEY_A}', 1.25000000, 'held', null),
           ('{OLD_SETTLED}', '{org_a}', '{KEY_A}', 1.25000000, 'settled', null),
           ('{OLD_UNKNOWN}', '{org_a}', '{KEY_A}', 1.25000000, 'unknown',
            '2026-09-22T00:05:00Z');
    update infrx.wallets set reserved_total = 2.50000000 where org_id = '{org_a}';
    insert into public.usage_events (id, org_id, api_key_id, model_id, status, stream,
      prompt_tokens, completion_tokens, cost_usd, settlement_regime, outcome, job_state,
      settlement_state, usage_certainty, price_version, settlement_version)
    values ('{OLD_SETTLED}', '{org_a}', '{KEY_A}', 'nemostation/marlin-2b', 200, true,
            2000, 500, 0.50000000, 'pilot', 'completed', 'succeeded', 'settled',
            'authoritative', 'pv-old', 1);
    insert into public.credit_ledger (org_id, delta_usd, kind, reason, request_id,
      operation_id)
    values ('{org_a}', -0.50000000, 'usage', 'pilot usage', '{OLD_SETTLED}',
            'op-old-settled');
    """)


def capture_old_regime(conn) -> dict:
    """Every accepted-job, hold and USD wallet value the D1R upgrade must not change."""
    return {
        "jobs": conn.execute("""
            select request_id, org_id, state, price_version, price_snapshot::text,
                   maximum_hold::text, debit::text, settlement_state, outcome_cause,
                   settled_at, reconcile_after, model_revision
            from infrx.jobs order by request_id""").fetchall(),
        "holds": conn.execute("""select request_id, org_id, amount::text, state
                                 from infrx.credit_holds order by request_id""").fetchall(),
        "usd_wallets": conn.execute("""
            select org_id, ledger_total::text, reserved_total::text, available::text
            from infrx.wallets order by org_id""").fetchall(),
        "pilot_usage": conn.execute("""
            select id, cost_usd::text, settlement_regime, price_version
            from public.usage_events order by id""").fetchall(),
    }


# --- the 0001-0005 inventory (item 1) -----------------------------------------
#: Changes D1R makes to a 0001-0005 object, each named, each justified in the migration.
#: Anything else that differs is a rewrite of legacy schema.
ALLOWED_LEGACY_CHANGES = {
    # A CREDIT-regime job carries a rate card, not a USD price version (0006).
    ("column", "infrx.jobs", "price_version"): "NOT NULL relaxed; regime CHECK replaces it",
    ("column", "infrx.jobs", "price_snapshot"): "NOT NULL relaxed; regime CHECK replaces it",
    # 0008 appends the CREDIT columns to the legacy usage view; every old column keeps its
    # position and type, which the per-column entries (with attnum) still check.
    ("view", "public.console_usage", "definition"): "columns appended, none changed",
    # Coordinator ruling (G6B handback): the closed AuditAction list is extended in 0009,
    # its four 0003 values kept.
    ("constraint", "infrx.audit_entries", "audit_entries_action_check"):
        "six headless operator actions appended",
}

#: D2 fills the bodies of the 0004 boundaries it owns. The BODY may change; SECURITY
#: DEFINER, the fixed search_path and the ACL (service_role only) may not - the inventory
#: value is `md5(definition) secdef acl`, and only the md5 is allowed to move.
FILLED_BOUNDARIES = frozenset({("function", "infrx", "infrx.admit(jsonb)"),
                               ("function", "infrx", "infrx.prepare(jsonb)"),
                               # D3 (0016): claim, heartbeat, cancel; terminalize's fence.
                               ("function", "infrx", "infrx.claim(jsonb)"),
                               ("function", "infrx", "infrx.heartbeat(jsonb)"),
                               ("function", "infrx", "infrx.cancel(jsonb)"),
                               ("function", "infrx", "infrx.terminalize(jsonb)"),
                               # D4 (0017): the journal append.
                               ("function", "infrx", "infrx.append(jsonb)"),
                               # D5 (0018): the operator grant's body.
                               ("function", "infrx", "infrx.grant_credit(jsonb)")})


def _same_boundary(old: str, new: str | None) -> bool:
    return new is not None and old.split(" ", 1)[1] == new.split(" ", 1)[1]


def inventory(conn) -> dict:
    """Constraints, triggers, function bodies and ACLs, relation/column ACLs, RLS,
    policies, column shapes and view definitions of `public` and `infrx`. Read with an
    empty search_path so every name is schema-qualified the same way on both images."""
    rows = {}
    with conn.transaction():
        conn.execute("set local search_path = pg_catalog")
        for kind, sql in _INVENTORY:
            for a, b, value in conn.execute(sql).fetchall():
                rows[(kind, a, b)] = value
    return rows


_INVENTORY = (
        ("constraint", """select conrelid::regclass::text, conname, pg_get_constraintdef(oid)
                          from pg_constraint
                          where connamespace in ('public'::regnamespace,
                                                 'infrx'::regnamespace)"""),
        ("trigger", """select tgrelid::regclass::text, tgname, pg_get_triggerdef(oid)
                       from pg_trigger where not tgisinternal"""),
        ("function", """select p.pronamespace::regnamespace::text,
                               p.oid::regprocedure::text,
                               md5(pg_get_functiondef(p.oid)) || ' ' || p.prosecdef
                               || ' ' || coalesce(p.proacl::text, '<default>')
                        from pg_proc p
                        where p.pronamespace in ('public'::regnamespace,
                                                 'infrx'::regnamespace)
                          and p.prokind = 'f'
                          and not exists (select 1 from pg_depend d
                                          where d.objid = p.oid and d.deptype = 'e')"""),
        ("relation", """select c.relkind::text, c.oid::regclass::text,
                               coalesce(c.relacl::text, '<default>') || ' rls='
                               || c.relrowsecurity
                        from pg_class c
                        where c.relnamespace in ('public'::regnamespace,
                                                 'infrx'::regnamespace)
                          and c.relkind in ('r', 'v', 'S')"""),
        ("column", """select a.attrelid::regclass::text, a.attname,
                             a.attnum || ' ' || format_type(a.atttypid, a.atttypmod)
                             || ' notnull='
                             || a.attnotnull || ' acl=' || coalesce(a.attacl::text, '')
                      from pg_attribute a join pg_class c on c.oid = a.attrelid
                      where c.relnamespace in ('public'::regnamespace,
                                               'infrx'::regnamespace)
                        and c.relkind in ('r', 'v') and a.attnum > 0
                        and not a.attisdropped"""),
        ("policy", """select schemaname || '.' || tablename, policyname,
                             cmd || ' ' || roles::text || ' ' || coalesce(qual, '')
                             || ' ' || coalesce(with_check, '')
                      from pg_policies where schemaname in ('public', 'infrx')"""),
        ("view", """select schemaname || '.' || viewname, 'definition', md5(definition)
                    from pg_views where schemaname in ('public', 'infrx')"""),
)


def check_legacy_schema_unchanged(conn, before: dict) -> str:
    """Item 1: nothing 0001-0005 created is rewritten, dropped or re-granted by D1R -
    the wallet trigger, the narrow financial RPC privileges, every constraint, trigger,
    policy, function body and ACL are identical - except the named allowances."""
    after = inventory(conn)
    changed = []
    for key, old in sorted(before.items()):
        new = after.get(key)
        if new == old or (new is not None and key in ALLOWED_LEGACY_CHANGES):
            continue
        if key in FILLED_BOUNDARIES and _same_boundary(old, new):
            continue
        changed.append(f"{key}: {old!r} -> {new!r}")
    assert not changed, "D1R rewrote 0001-0005 schema:\n  " + "\n  ".join(changed[:30])
    # Named, because these are what the brief lists: the USD wallet trigger and the
    # narrow financial RPC grants still exist exactly as they were.
    for key in (("trigger", "public.credit_ledger", "credit_ledger_moves_wallet"),
                ("function", "infrx", "infrx.grant_credit(jsonb)"),
                ("function", "public", "public.org_wallet_summary(uuid)")):
        # D5 fills grant_credit's body (0018): its md5 may move, its security and ACL not.
        assert key in before and (after.get(key) == before[key] or (
            key in FILLED_BOUNDARIES and _same_boundary(before[key], after.get(key)))), \
            f"{key} changed or vanished"
    for key in FILLED_BOUNDARIES:
        assert key in before and _same_boundary(before[key], after.get(key)), \
            f"{key}: its grants or SECURITY DEFINER changed ({before[key]!r} -> {after.get(key)!r})"
    return (f"{len(before)} objects of 0001-0005 unchanged "
            f"({len(ALLOWED_LEGACY_CHANGES)} named allowances)")


def check_old_regime_preserved(conn, before: dict) -> str:
    """USD history (ledger rows, sums, usage values), accepted old-regime jobs, their USD
    holds, the USD wallet summaries and the pilot usage rows are identical - to the
    digit - after D1R."""
    legacy = checks.capture_legacy(conn, before["legacy"]["org_a"], before["legacy"]["org_b"])
    assert legacy == before["legacy"], "USD history changed across D1R"
    # CREDIT-UNITS: the upgrade imports nothing - no wallet, no ledger row, no grant from
    # USD history (a nonzero USD balance is a rollout hold, not a conversion).
    made = conn.execute("select (select count(*) from infrx.credit_wallets), "
                        "(select count(*) from infrx.credit_ledger), "
                        "(select count(*) from infrx.signup_entitlements)").fetchone()
    assert made == (0, 0, 0), f"the upgrade created CREDIT from USD history: {made}"
    before, after = before["old"], capture_old_regime(conn)
    for part in before:
        assert after[part] == before[part], \
            f"old-regime {part} changed:\n  {before[part]}\n  -> {after[part]}"
    return (f"{len(after['jobs'])} accepted jobs, {len(after['holds'])} USD holds and "
            f"{len(after['usd_wallets'])} USD wallets value-identical")


def upgrade05(pgharness, database: str, files) -> tuple:
    """Build 0001-0002, seed legacy history, apply 0003-0005, accept old-regime jobs,
    take the inventory, then apply everything D1R adds. Returns the connection and the
    captured `before` state."""
    first = tuple(f for f in files if f[0] in ("supabase_shim.sql", "0001_init.sql",
                                               "0002_seed_models.sql"))
    pilot = tuple(f for f in files if f[0] in BEFORE_D1R and f not in first)
    _, d1r = split(files)
    pgharness.recreate(database)
    pgharness.apply(database, first)
    conn = pgharness.connect(database)
    legacy = checks.seed_legacy(conn)
    pgharness.apply(database, pilot)
    seed_old_regime(conn, legacy["org_a"])
    before = {"legacy": checks.capture_legacy(conn, legacy["org_a"], legacy["org_b"]),
              "inventory": inventory(conn), "old": capture_old_regime(conn)}
    pgharness.apply(database, d1r)
    return conn, before



# =============================================================================
# item 2: CREDIT wallets, ledger, entitlements, the initial grant
# =============================================================================
FIXTURES = Path(__file__).resolve().parents[2] / "infrx" / "contracts" / "fixtures" / "v2"

# Individuals created for the CREDIT fixture, one auth user (and so one personal
# organization, made by 0001's signup trigger) each, in separate statements so each
# personal organization is unambiguous.
CONSUMER_1 = "c1000000-0000-4000-8000-000000000001"
CONSUMER_2 = "c1000000-0000-4000-8000-000000000002"
UNGRANTED = "c1000000-0000-4000-8000-000000000003"      # a verified-later individual
SHARED = "c1000000-0000-4000-8000-000000000004"         # personal org with a second member
RACER = "c1000000-0000-4000-8000-000000000005"          # granted only by the race check
PROVIDER_DEV_USER = "c1000000-0000-4000-8000-000000000006"
PROVIDER_ADMIN_USER = "c1000000-0000-4000-8000-000000000007"
EVIDENCE = "email_verification/2026-09-22/test"


def _load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def personal_org(conn, user: str) -> str:
    return str(conn.execute("select id from public.organizations where created_by = %s "
                            "order by created_at, id limit 1", (user,)).fetchone()[0])


def wallet_of(conn, user: str) -> str | None:
    row = conn.execute("select wallet_id from infrx.credit_wallets "
                       "where owner_user_id = %s and kind = 'consumer'", (user,)).fetchone()
    return str(row[0]) if row else None


def set_flag(conn, name: str, enabled: bool) -> None:
    conn.execute("update infrx.feature_flags set enabled = %s, updated_by = 'd1r-test', "
                 "reason = 'test' where name = %s", (enabled, name))


def grant(conn, user: str, *, campaign: str = "launch_2026_09", op: str | None = None,
          evidence: str = EVIDENCE) -> tuple:
    return conn.execute("select * from infrx.grant_signup_credit(%s, %s, %s, %s)",
                        (user, evidence, campaign, op)).fetchone()


def seed_credit(conn) -> None:
    """On top of `checks.seed_fixtures`: the individuals, the flags on, two grants."""
    for user in (CONSUMER_1, CONSUMER_2, UNGRANTED, SHARED, RACER, PROVIDER_DEV_USER,
                 PROVIDER_ADMIN_USER):
        conn.execute("insert into auth.users (id, email) values (%s, %s)",
                     (user, f"{user[:8]}-{user[-2:]}@example.com"))
    conn.execute("insert into public.org_members (org_id, user_id, role) values (%s, %s, "
                 "'member')", (personal_org(conn, SHARED), CONSUMER_2))
    set_flag(conn, "signup_grant", True)
    set_flag(conn, "credit_admission", True)
    grant(conn, CONSUMER_1)
    grant(conn, CONSUMER_2)
    seed_registry(conn)


# --- the operator seed and the provider fixture (item 3) -------------------------
NEMO = "b0000001-0000-4000-8000-000000000001"
OTHER_PROVIDER = "b0000009-0000-4000-8000-000000000009"
OTHER_ENDPOINT = "c0000009-0000-4000-8000-000000000009"
MODEL = "d0000001-0000-4000-8000-000000000001"
SERVING = "d0000003-0000-4000-8000-000000000003"
DEV_DEPLOYMENT = "c0000003-0000-4000-8000-000000000003"
PUBLIC_DEPLOYMENT = "c0000004-0000-4000-8000-000000000004"
PROD_ENDPOINT = "c0000002-0000-4000-8000-000000000002"
DEV_ENDPOINT = "c0000001-0000-4000-8000-000000000001"
CARD = "rc_marlin2b_2026_09_provisional"
POLICY = "dap_2026_09_01"
DEV_CARD = "rc_marlin2b_dev_internal"
PROVIDER_WALLET = "b0000003-0000-4000-8000-000000000003"
SERVING_2 = "d0000005-0000-4000-8000-000000000005"        # D1R review (c): a real second
DEPLOYMENT_2 = "c0000005-0000-4000-8000-000000000005"     # serving version and deployment


def apply_seed(conn) -> None:
    conn.execute(migrations.SEED_MARLIN.read_text())


def seed_registry(conn) -> None:
    """The operator seed exactly as an operator runs it, plus what the checks need
    beside it: a second provider, provider members, the provider's dev wallet funded
    by an audited allocation, and an internal card for the private dev deployment."""
    apply_seed(conn)
    conn.execute(f"""
    insert into infrx.provider_orgs (provider_org_id, slug, display_name, created_by)
      values ('{OTHER_PROVIDER}', 'other', 'NemoStation', 'ops');   -- same display text
    insert into infrx.endpoints (endpoint_id, provider_org_id, name, environment, created_by)
      values ('{OTHER_ENDPOINT}', '{OTHER_PROVIDER}', 'other', 'prod', 'ops');
    insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by) values
      ('{NEMO}', '{PROVIDER_DEV_USER}', 'developer', 'ops@infrx'),
      ('{NEMO}', '{PROVIDER_ADMIN_USER}', 'administrator', 'ops@infrx');
    insert into infrx.credit_wallets (wallet_id, kind, owner_provider_org_id)
      values ('{PROVIDER_WALLET}', 'provider_dev', '{NEMO}');
    insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id,
      actor, reason)
      values ('{PROVIDER_WALLET}', 'provider_dev', 'operator_allocation', 500,
              gen_random_uuid(), 'ops@infrx', 'preview budget');
    insert into infrx.rate_card_versions (rate_card_version, model_id, deployment_revision_id,
      serving_version_id, input_rate_per_million, output_rate_per_million, effective_at,
      approved_by, provisional)
      values ('{DEV_CARD}', '{MODEL}', '{DEV_DEPLOYMENT}', '{SERVING}', 400, 1200,
              '2026-09-01T00:00:00Z', 'ops@infrx', true);
    """)


class _Allowed(Exception):
    pass


def attempt(conn, sql: str, params=None, *, session: str | None = None) -> str | None:
    """None when the statement succeeded (it is rolled back either way), else the
    SQLSTATE, the constraint name if any, and the first line of the message."""
    try:
        with conn.transaction():
            if session:
                conn.execute(checks.SESSIONS[session])
            conn.execute(sql, params)
            raise _Allowed()
    except _Allowed:
        return None
    except psycopg.Error as refused:
        name = getattr(refused.diag, "constraint_name", None) or ""
        return f"{refused.sqlstate} {name} {str(refused).splitlines()[0][:120]}".strip()


def _all_refused(conn, cases, what: str) -> int:
    survived = [label for label, sql in cases if attempt(conn, sql) is None]
    assert not survived, f"the schema accepted ({what}):\n  " + "\n  ".join(survived)
    return len(cases)


def _all_accepted(conn, cases, what: str) -> int:
    refused = [f"{label}: {why}" for label, sql in cases
               if (why := attempt(conn, sql)) is not None]
    assert not refused, f"the schema refused ({what}):\n  " + "\n  ".join(refused)
    return len(cases)


def _identity_cases(conn) -> tuple[tuple, tuple]:
    w1, w2 = wallet_of(conn, CONSUMER_1), wallet_of(conn, CONSUMER_2)
    o1, o2 = personal_org(conn, CONSUMER_1), personal_org(conn, CONSUMER_2)
    adj = "00000000-0000-4000-8000-00000000ad01"
    # A consumer wallet for UNGRANTED with no money yet (as the grant would create it), for
    # cases that must satisfy every rule but the one under test.
    bare_wallet = (f"with w as (insert into infrx.credit_wallets (kind, owner_user_id, "
                   f"personal_org_id) values ('consumer', '{UNGRANTED}', "
                   f"'{personal_org(conn, UNGRANTED)}') returning wallet_id) ")
    job1 = credit_job(JOB1, "job_ledger_1", o1, w1) + "; "
    ledger = ("insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
              "operation_id, request_id, actor) values ")
    refused = (
        ("a consumer wallet without its personal org",
         f"insert into infrx.credit_wallets (kind, owner_user_id) values "
         f"('consumer', '{UNGRANTED}')"),
        ("a consumer wallet that also names a provider owner",
         f"insert into infrx.credit_wallets (kind, owner_user_id, personal_org_id, "
         f"owner_provider_org_id) values ('consumer', '{UNGRANTED}', "
         f"'{personal_org(conn, UNGRANTED)}', '{NEMO}')"),
        ("a provider_dev wallet owned by an individual",
         f"insert into infrx.credit_wallets (kind, owner_user_id) values "
         f"('provider_dev', '{UNGRANTED}')"),
        ("a second consumer wallet for one individual",
         f"insert into infrx.credit_wallets (kind, owner_user_id, personal_org_id) values "
         f"('consumer', '{CONSUMER_1}', '{personal_org(conn, UNGRANTED)}')"),
        ("a second wallet funded through one personal org",
         f"insert into infrx.credit_wallets (kind, owner_user_id, personal_org_id) values "
         f"('consumer', '{UNGRANTED}', '{o1}')"),
        ("a wallet denominated in USD",
         f"insert into infrx.credit_wallets (kind, unit, owner_user_id, personal_org_id) "
         f"values ('consumer', 'USD', '{UNGRANTED}', '{personal_org(conn, UNGRANTED)}')"),
        ("a wallet created with a balance",
         f"insert into infrx.credit_wallets (kind, owner_user_id, personal_org_id, "
         f"ledger_total) values ('consumer', '{UNGRANTED}', "
         f"'{personal_org(conn, UNGRANTED)}', 5)"),
        ("changing a wallet's kind",
         f"update infrx.credit_wallets set kind = 'provider_dev' where wallet_id = '{w1}'"),
        ("changing a wallet's owner",
         f"update infrx.credit_wallets set owner_user_id = '{CONSUMER_2}' "
         f"where wallet_id = '{w1}'"),
        ("rebinding a wallet to another org",
         f"update infrx.credit_wallets set personal_org_id = "
         f"'{personal_org(conn, UNGRANTED)}' where wallet_id = '{w1}'"),
        ("relabelling a wallet's unit",
         f"update infrx.credit_wallets set unit = 'USD' where wallet_id = '{w1}'"),
        ("moving a total without a ledger row (even as the owner)",
         f"update infrx.credit_wallets set ledger_total = 20000 where wallet_id = '{w1}'"),
        ("moving a reservation without a hold",
         f"update infrx.credit_wallets set reserved_total = 1 where wallet_id = '{w1}'"),
        ("deleting a wallet", f"delete from infrx.credit_wallets where wallet_id = '{w1}'"),
        ("a transfer kind",
         ledger + f"('{w1}', 'consumer', 'transfer', -5, gen_random_uuid(), null, 'x')"),
        ("a signup grant of another amount",
         bare_wallet + ledger.replace("values ", "select ") + "wallet_id, 'consumer', "
         "'signup_grant', 5000, gen_random_uuid(), null, 'platform' from w"),
        ("a second signup grant into a wallet, without an entitlement",
         ledger + f"('{w1}', 'consumer', 'signup_grant', 10000, gen_random_uuid(), null, "
                  f"'platform')"),
        ("a ledger row that lies about its wallet's kind",
         ledger + f"('{w1}', 'provider_dev', 'operator_adjustment', 5, gen_random_uuid(), "
                  f"null, 'ops')"),
        ("an operator allocation minting consumer credit",
         ledger + f"('{w1}', 'consumer', 'operator_allocation', 5, gen_random_uuid(), "
                  f"null, 'ops')"),
        ("a positive inference debit",
         job1 + ledger + f"('{w1}', 'consumer', 'inference_debit', 5, gen_random_uuid(), "
                         f"'{JOB1}', 'svc')"),
        ("an inference debit that names no request",
         ledger + f"('{w1}', 'consumer', 'inference_debit', -5, gen_random_uuid(), null, "
                  f"'svc')"),
        ("a grant that claims to settle a request",
         job1 + ledger + f"('{w1}', 'consumer', 'operator_adjustment', 5, gen_random_uuid(), "
                         f"'{JOB1}', 'ops')"),
        ("a zero movement",
         ledger + f"('{w1}', 'consumer', 'operator_adjustment', 0, gen_random_uuid(), null, "
                  f"'ops')"),
        ("an anonymous movement",
         ledger + f"('{w1}', 'consumer', 'operator_adjustment', 5, gen_random_uuid(), null, "
                  f"'  ')"),
        ("a debit past zero",
         ledger + f"('{w1}', 'consumer', 'operator_adjustment', -10000.00000001, "
                  f"gen_random_uuid(), null, 'ops')"),
        ("a reused operation id",
         ledger + f"('{w2}', 'consumer', 'operator_adjustment', 5, "
                  f"(select operation_id from infrx.credit_ledger where wallet_id = '{w1}' "
                  f"limit 1), null, 'ops')"),
        ("editing ledger history", f"update infrx.credit_ledger set reason = 'rewritten' "
                                   f"where wallet_id = '{w1}'"),
        ("deleting ledger history", f"delete from infrx.credit_ledger where wallet_id = '{w1}'"),
        ("truncating the CREDIT ledger", "truncate infrx.credit_ledger cascade"),
        ("truncating CREDIT wallets", "truncate infrx.credit_wallets cascade"),
        ("truncating CREDIT holds", "truncate infrx.credit_wallet_holds"),
        ("a second initial entitlement under another campaign",
         f"insert into infrx.signup_entitlements (user_id, entitlement, wallet_id, amount, "
         f"verification_evidence_ref, ledger_operation_id, campaign_version) "
         f"select user_id, entitlement, wallet_id, amount, 'again', gen_random_uuid(), "
         f"'relaunch' from infrx.signup_entitlements where user_id = '{CONSUMER_1}'"),
        ("an entitlement paid into another individual's wallet",
         bare_wallet + ", g as (insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, "
         "amount, operation_id, actor) select wallet_id, 'consumer', 'signup_grant', 10000, "
         "gen_random_uuid(), 'platform' from w returning wallet_id, operation_id) "
         "insert into infrx.signup_entitlements (user_id, entitlement, wallet_id, amount, "
         f"verification_evidence_ref, ledger_operation_id) select '{SHARED}', "
         "'initial_signup_grant', wallet_id, 10000, 'x', operation_id from g"),
        ("an entitlement whose money is not a signup row",
         bare_wallet + ", g as (insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, "
         f"amount, operation_id, actor) select wallet_id, 'consumer', 'operator_adjustment', "
         f"1, '{adj}', 'ops' from w returning wallet_id) "
         "insert into infrx.signup_entitlements (user_id, entitlement, wallet_id, amount, "
         f"verification_evidence_ref, ledger_operation_id) select '{UNGRANTED}', "
         f"'initial_signup_grant', wallet_id, 10000, 'x', '{adj}' from g"),
        ("an entitlement for another amount",
         f"update infrx.signup_entitlements set amount = 20000 where user_id = '{CONSUMER_1}'"),
        ("re-dating or re-attributing an entitlement",
         f"update infrx.signup_entitlements set campaign_version = 'x' "
         f"where user_id = '{CONSUMER_1}'"),
        ("an entitlement with no verification evidence",
         f"insert into infrx.signup_entitlements (user_id, entitlement, wallet_id, amount, "
         f"verification_evidence_ref, ledger_operation_id) values ('{UNGRANTED}', "
         f"'initial_signup_grant', '{w1}', 10000, ' ', gen_random_uuid())"),
    )
    accepted = (
        ("an audited operator adjustment, both signs",
         ledger + f"('{w1}', 'consumer', 'operator_adjustment', 2.5, gen_random_uuid(), "
                  f"null, 'ops@infrx'), ('{w1}', 'consumer', 'operator_adjustment', "
                  f"-2.5, gen_random_uuid(), null, 'ops@infrx')"),
        ("spending exactly to zero",
         ledger + f"('{w1}', 'consumer', 'operator_adjustment', -10000, gen_random_uuid(), "
                  f"null, 'ops@infrx')"),
    )
    return refused, accepted


JOB1 = "5c000000-0000-4000-8000-0000000000d1"


def check_credit_identity(conn) -> str:
    """CREDIT-IDENTITY / R59-7 / R67 / R71: ownership is an exclusive-or by kind, one
    wallet per individual and per personal org, kind/unit/owner immutable, totals move
    only by the ledger, the ledger vocabulary is closed with sign and wallet-kind rules,
    and the entitlement is one per individual, paid into their own wallet by its own
    signup row."""
    refused, accepted = _identity_cases(conn)
    # R71 as a shape, because three constraints defend "one grant per individual" and no
    # row can isolate the key: the key IS (user_id, entitlement) and nothing else.
    key = conn.execute("""select array_agg(a.attname order by k.n) from pg_constraint c,
        unnest(c.conkey) with ordinality k(attnum, n)
        join pg_attribute a on a.attnum = k.attnum
        where c.conname = 'signup_entitlements_pkey'
          and a.attrelid = 'infrx.signup_entitlements'::regclass""").fetchone()[0]
    assert key == ["user_id", "entitlement"], f"the grant key is {key}, not (user, entitlement)"
    n = _all_refused(conn, refused, "identity")
    m = _all_accepted(conn, accepted, "identity controls")
    return f"{n} identity/ledger violations refused, {m} controls accepted"


def check_credit_reconciles(conn) -> str:
    """R59-7: every CREDIT summary equals its ledger and its active holds."""
    drift = conn.execute("select wallet_id, ledger_drift, reserved_drift from "
                         "infrx.credit_wallet_reconciliation where ledger_drift <> 0 "
                         "or reserved_drift <> 0").fetchall()
    assert not drift, f"CREDIT wallets disagree with their ledger/holds: {drift}"
    n, = conn.execute("select count(*) from infrx.credit_wallets").fetchone()
    assert n >= 2, "the reconciliation saw no wallet"
    return f"{n} CREDIT wallets reconcile"


def check_grant(conn) -> str:
    """CREDIT-GRANT / A1 seam: one transaction creates the wallet, the entitlement and
    exactly one +10000.00000000 row; every retry (another operation id, campaign or
    organization) returns the same grant; a missing flag is a maintenance refusal; blank
    evidence, a shared organization and an unknown user are refused."""
    with conn.transaction():
        user, wallet, op, amount, _at, replayed = grant(conn, UNGRANTED)
        assert str(user) == UNGRANTED and not replayed and amount == "10000.00000000", \
            f"first grant: {(user, amount, replayed)}"
        row = conn.execute("select kind, unit, personal_org_id, ledger_total::text, "
                           "reserved_total::text, revision from infrx.credit_wallets "
                           "where wallet_id = %s", (wallet,)).fetchone()
        assert row == ("consumer", "CREDIT", uuid.UUID(personal_org(conn, UNGRANTED)),
                       "10000.00000000", "0.00000000", 1), \
            f"granted wallet: {row}"
        # Another organization, another campaign, another operation id: same grant.
        conn.execute("insert into public.organizations (name, slug, created_by) values "
                     "('second', 'second-org-x', %s)", (UNGRANTED,))
        try:
            with conn.transaction():
                again = grant(conn, UNGRANTED, campaign="relaunch_2027",
                              op="00000000-0000-4000-8000-00000000aa01", evidence="other")
        except psycopg.Error as raised:
            raise AssertionError(f"a retry raised instead of replaying: {raised}") from None
        assert again[5] is True and again[2] == op and again[1] == wallet, \
            f"a retry minted or moved the grant: {again}"
        n, = conn.execute("select count(*) from infrx.credit_ledger where wallet_id = %s",
                          (wallet,)).fetchone()
        assert n == 1, f"{n} ledger rows for one grant"
        raise psycopg.Rollback()
    for label, sql, code in (
        ("blank evidence", f"select infrx.grant_signup_credit('{UNGRANTED}', ' ')", "22023"),
        ("a shared personal organization",
         f"select infrx.grant_signup_credit('{SHARED}', 'e')", "55000"),
        ("an unknown user", "select infrx.grant_signup_credit(gen_random_uuid(), 'e')",
         "P0002"),
    ):
        why = attempt(conn, sql)
        assert why is not None and why.startswith(code), f"{label}: {why!r}"
    with conn.transaction():
        set_flag(conn, "signup_grant", False)
        why = attempt(conn, f"select infrx.grant_signup_credit('{UNGRANTED}', 'e')")
        raise psycopg.Rollback()
    assert why is not None and why.startswith("55000"), f"grant with the flag off: {why!r}"
    return "grant: one row, idempotent across op/campaign/org, 4 refusals"


class _Rollback(Exception):
    pass


def check_grant_race(connect, database: str, attempts: int = 8, rounds: int = 10) -> str:
    """CREDIT-GRANT under a race: in each round, N concurrent callers for one fresh
    individual produce exactly one ledger row, and every caller is handed that same row.
    Several rounds, because a race that loses one time in five is still a race.

    The rounds are probabilistic: the interleaving that matters - a caller passing the
    per-user check and then colliding on the per-personal-org index - happens only
    sometimes (the mutant `d1r_grant_race_arbitrates_one_index` died 4 runs in 5). So it is
    first produced DETERMINISTICALLY (D3, for D2's request 8): an individual whose personal
    organization already funds another individual's wallet collides on the personal-org
    index ALONE. `on conflict do nothing` absorbs that collision and the grant answers the
    typed rollout hold; a conflict target naming only the per-user index surfaces a raw
    unique violation instead, every time."""
    squatter = "c2000000-0000-4000-8000-00000000dead"
    victim = "c2000000-0000-4000-8000-00000000beef"
    with connect(database) as c:
        try:
            with c.transaction():
                for user in (squatter, victim):
                    c.execute("insert into auth.users (id, email) values (%s, %s)",
                              (user, f"{user[-4:]}@example.com"))
                c.execute("insert into infrx.credit_wallets (kind, owner_user_id, "
                          "personal_org_id) values ('consumer', %s, %s)",
                          (squatter, personal_org(c, victim)))
                try:
                    with c.transaction():
                        grant(c, victim, op=None)
                except psycopg.Error as refused:
                    state = refused.sqlstate
                else:
                    state = None
                assert state == "55000", (
                    f"a personal-org-only collision answered {state!r}, not the typed "
                    f"rollout hold (55000): the insert does not absorb a conflict on the "
                    f"per-personal-org index")
                raise _Rollback()
        except _Rollback:
            pass
    for n in range(rounds):
        user = RACER if n == 0 else f"c2000000-0000-4000-8000-{n:012d}"
        if n:
            with connect(database) as c:
                c.execute("insert into auth.users (id, email) values (%s, %s) "
                          "on conflict do nothing", (user, f"race{n}@example.com"))
        barrier = threading.Barrier(attempts)
        out: list = []
        errors: list = []

        def call() -> None:
            try:
                with connect(database) as c:
                    barrier.wait()
                    out.append(grant(c, user, op=None))
            except Exception as failed:               # reported below, never swallowed
                errors.append(f"{type(failed).__name__}: {str(failed)[:200]}")

        threads = [threading.Thread(target=call) for _ in range(attempts)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with connect(database) as c:
            rows = c.execute("select l.operation_id from infrx.credit_ledger l "
                             "join infrx.credit_wallets w using (wallet_id) "
                             "where w.owner_user_id = %s", (user,)).fetchall()
        assert len(out) == attempts, \
            f"round {n}: only {len(out)} of {attempts} callers returned: {errors}"
        assert len(rows) == 1, f"round {n}: {len(rows)} ledger rows after a race"
        assert {r[2] for r in out} == {rows[0][0]}, f"round {n}: callers got different grants"
        assert sum(1 for r in out if not r[5]) == 1, f"round {n}: not exactly one issuer"
    return (f"{rounds} rounds x {attempts} concurrent grants -> 1 ledger row and 1 issuer "
            f"per individual")


def check_no_unit_conversion(conn) -> str:
    """R64/R65/R73: no function or view touches a USD amount and a CREDIT amount
    together (so none can sum or convert across units), and nothing is named like a
    converter. `public.console_usage` shows the two columns side by side, never combined."""
    usd = ("delta_usd", "cost_usd", "infrx.wallets", "infrx.credit_holds", "usd_per_m",
           "price_versions", "public.credit_ledger",
           # D1R review (b): the legacy statement and the regime's own name.
           "console_legacy_usd_statement", "legacy_usd")
    credit = ("credit_wallets", "infrx.credit_ledger", "credit_wallet_holds",
              "charged_credits", "signup_entitlements", "rate_card_versions")
    # Row-per-request projections that label each amount with its own unit.
    side_by_side = {"public.console_usage", "infrx.usage_records", "infrx.active_holds"}
    found = []
    objects = conn.execute("""
        select n.nspname || '.' || p.proname, lower(p.prosrc) from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname in ('public', 'infrx')
          and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')
        union all
        select schemaname || '.' || viewname, lower(definition) from pg_views
        where schemaname in ('public', 'infrx')""").fetchall()
    for name, src in objects:
        if any(w in name for w in ("convert", "exchange", "to_credit", "to_usd", "usd_to",
                                   "credit_to")):
            found.append(f"{name}: named like a converter")
        both = any(t in src for t in usd) and any(t in src for t in credit)
        if both and name not in side_by_side:
            found.append(f"{name}: reads USD and CREDIT amounts together")
        if both and name in side_by_side and "sum(" in src:
            found.append(f"{name}: aggregates across units")
    assert not found, "cross-unit code exists:\n  " + "\n  ".join(found)
    return f"{len(objects)} functions/views: none converts or combines units"


#: Every CREDIT amount column (D1R review (b): the fixture is executed against the COLUMN
#: types, so a migration that narrows one is caught, not a literal cast nobody stores).
CREDIT_AMOUNT_COLUMNS = (("infrx.credit_ledger", "amount"),
                         ("infrx.credit_wallets", "ledger_total"),
                         ("infrx.credit_wallets", "reserved_total"),
                         ("infrx.credit_wallet_holds", "amount"),
                         ("public.usage_events", "charged_credits"),
                         ("infrx.rate_card_versions", "input_rate_per_million"),
                         ("infrx.rate_card_versions", "output_rate_per_million"))


def check_money_unit_cases(conn) -> str:
    """The F2P `money_unit_cases` fixture as SQL, through the type of every CREDIT amount
    column: each valid amount round-trips to its canonical eight-digit text and 10^12 is
    refused. (Rounding refusals - `0.000000001`, `1e3` - are the service parser's:
    PostgreSQL's numeric input rounds, so the trust boundary is money_units.)"""
    n = 0
    for table, column in CREDIT_AMOUNT_COLUMNS:
        kind, = conn.execute("select format_type(atttypid, atttypmod) from pg_attribute "
                             "where attrelid = %s::regclass and attname = %s",
                             (table, column)).fetchone()
        for case in _load("money_unit_cases"):
            if case["valid"]:
                text, = conn.execute(f"select %s::{kind}::text", (case["input"],)).fetchone()
                assert text == case["canonical"], \
                    f"{table}.{column} ({kind}) spells {case['input']} as {text}"
                n += 1
            elif case["input"] == "1000000000000.00000000":
                assert attempt(conn, f"select %s::{kind}", (case["input"],)) is not None, \
                    f"{table}.{column} ({kind}) accepted 10^12"
                n += 1
    return f"{n} money_unit_cases executed through {len(CREDIT_AMOUNT_COLUMNS)} CREDIT columns"


def check_regime_on_usage(conn) -> str:
    """CREDIT-UNITS on usage: a row states its regime; a legacy_usd row carries no
    CREDIT field, and the regime is one of the two."""
    base = ("insert into public.usage_events (id, org_id, model_id, status, "
            "accounting_regime, charged_credits) values (gen_random_uuid(), "
            f"'{checks.ORG_A}', 'nemostation/marlin-2b', 200, ")
    n = _all_refused(conn, (
        ("a legacy row with a CREDIT charge", base + "'legacy_usd', 1)"),
        ("an unknown regime", base + "'usd', null)"),
        ("a CREDIT row with no card or serving revision", base + "'credit', 1)"),
    ), "usage regime")
    # The deployed gateway's own insert (no regime named) is still the legacy writer.
    m = _all_accepted(conn, (
        ("the deployed gateway's usage row",
         "insert into public.usage_events (id, org_id, api_key_id, model_id, status, stream, "
         "prompt_tokens, completion_tokens, video_seconds, ttft_ms, latency_ms, cached, "
         f"cost_usd) values (gen_random_uuid(), '{checks.ORG_A}', '{checks.KEY_A}', "
         "'nemostation/marlin-2b', 200, true, 10, 5, 1.5, 100, 900, false, 0.00000350)"),
    ), "legacy writer")
    regime, = conn.execute("select count(*) from public.usage_events "
                           "where accounting_regime <> 'legacy_usd'").fetchone()
    return f"{n} regime violations refused, {m} legacy writer accepted ({regime} CREDIT rows)"


# =============================================================================
# item 3: the registry
# =============================================================================
def registry_counts(conn) -> tuple:
    return tuple(conn.execute(f"select count(*) from infrx.{t}").fetchone()[0] for t in (
        "provider_orgs", "model_versions", "serving_versions", "endpoints",
        "deployment_revisions", "rate_card_versions", "data_access_policies",
        "catalog_listings"))


def _as_fixture(value):
    """A database value spelled the way the fixture JSON spells it."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return value


def check_seed_is_the_fixtures(conn) -> str:
    """The operator seed IS the F2P fixture records (02: operators seed the identical
    records): serving revision, both deployment revisions and the rate card match their
    fixture JSON field for field; the card is labelled provisional (P-01); a second run
    of the seed changes nothing."""
    before = registry_counts(conn)
    apply_seed(conn)
    assert registry_counts(conn) == before, "re-running the seed changed the registry"
    compared = 0
    card = _load("rate_card_marlin")
    row = conn.execute("select rate_card_version, unit, meter, model_id, deployment_revision_id, "
                       "serving_version_id, input_rate_per_million::text, "
                       "output_rate_per_million::text, effective_at, approved_by, status, "
                       "hold_rounding, debit_rounding, provisional from infrx.rate_card_versions "
                       "where rate_card_version = %s", (card["rate_card_version"],)).fetchone()
    keys = ("rate_card_version", "unit", "meter", "model_id", "deployment_revision_id",
            "serving_version_id", "input_rate_per_million", "output_rate_per_million",
            "effective_at", "approved_by", "status", "hold_rounding", "debit_rounding")
    got = dict(zip(keys, map(_as_fixture, row)))
    for k in keys:
        assert got[k] == card[k], f"rate card {k}: database {got[k]!r}, fixture {card[k]!r}"
    assert row[-1] is True and "P-01" in got["approved_by"], "the card is not labelled provisional"
    compared += len(keys)
    for name in ("deployment_revision_public", "deployment_revision_private_dev"):
        fx = _load(name)
        keys = ("deployment_revision_id", "endpoint_id", "environment", "max_input_tokens",
                "max_output_tokens", "provider_org_id", "serving_version_id", "state",
                "visibility", "created_at")
        row = conn.execute(f"select {', '.join(keys)} from infrx.deployment_revisions "
                           "where deployment_revision_id = %s",
                           (fx["deployment_revision_id"],)).fetchone()
        got = dict(zip(keys, map(_as_fixture, row)))
        for k in keys:
            assert got[k] == fx[k], f"{name}.{k}: database {got[k]!r}, fixture {fx[k]!r}"
        compared += len(keys)
    fx = _load("serving_revision")
    keys = ("serving_version_id", "model_id", "model_version_id", "provider_org_id",
            "public_model_id", "revision_label", "model_repo", "model_commit",
            "weight_shard_digests", "tokenizer_digest", "chat_template_digest",
            "digest_source", "prompt_harness_ref", "preprocessor_profile_version",
            "runtime_image_ref", "engine_options_digest", "precision", "capability",
            "created_at")
    row = conn.execute("""
        select s.serving_version_id, s.model_id, s.model_version_id, s.provider_org_id, m.id,
               s.revision_label, v.model_repo, v.model_commit, v.weight_shard_digests,
               v.tokenizer_digest, v.chat_template_digest, v.digest_source,
               s.prompt_harness_ref, s.preprocessor_profile_version, s.runtime_image_ref,
               s.engine_options_digest, s.precision, s.capability, s.created_at
        from infrx.serving_versions s
        join infrx.model_versions v on v.model_version_id = s.model_version_id
        join public.models m on m.model_uuid = s.model_id
        where s.serving_version_id = %s""", (fx["serving_version_id"],)).fetchone()
    got = dict(zip(keys, map(_as_fixture, row)))
    for k in keys:
        assert got[k] == fx[k], f"serving_revision.{k}: database {got[k]!r}, fixture {fx[k]!r}"
    assert "runtime_image_digest" not in fx and conn.execute(
        "select runtime_image_digest is null from infrx.serving_versions "
        "where serving_version_id = %s", (fx["serving_version_id"],)).fetchone()[0], \
        "a moving-tag runtime gained a digest (R76)"
    compared += len(keys)
    return f"seed == F2P fixtures on {compared} fields; provisional; re-run is a no-op"


def _registry_cases() -> tuple[tuple, tuple]:
    dep = ("insert into infrx.deployment_revisions (endpoint_id, provider_org_id, environment, "
           "serving_version_id, visibility, state, max_input_tokens, max_output_tokens, "
           "created_by) values ")
    card = ("insert into infrx.rate_card_versions (rate_card_version, model_id, "
            "deployment_revision_id, serving_version_id, input_rate_per_million, "
            "output_rate_per_million, effective_at, approved_by, provisional")
    mv = ("insert into infrx.model_versions (model_id, provider_org_id, model_repo, "
          "model_commit, weight_shard_digests, tokenizer_digest, chat_template_digest, "
          "digest_source, created_by) values ")
    sha = "'sha256:" + "a" * 64 + "'"
    good_mv = (f"('{MODEL}', '{NEMO}', 'r', '{'f' * 40}', array[{sha}], {sha}, {sha}, "
               f"'served_bytes', 'ops')")
    listing = ("insert into infrx.catalog_listings (public_model_id, version, model_id, "
               "deployment_revision_id, serving_version_id, rate_card_version, effective_at, "
               "approved_by) values ")
    refused = (
        ("a public deployment on a dev endpoint",
         dep + f"('{DEV_ENDPOINT}', '{NEMO}', 'dev', '{SERVING}', 'public', 'active', 1, 1, 'o')"),
        ("a public deployment that never passed validation",
         dep + f"('{PROD_ENDPOINT}', '{NEMO}', 'prod', '{SERVING}', 'public', 'draft', 1, 1, 'o')"),
        ("a private deployment in a public state",
         dep + f"('{PROD_ENDPOINT}', '{NEMO}', 'prod', '{SERVING}', 'private', 'active', 1, 1, 'o')"),
        ("a deployment whose environment disagrees with its endpoint",
         dep + f"('{DEV_ENDPOINT}', '{NEMO}', 'prod', '{SERVING}', 'private', 'draft', 1, 1, 'o')"),
        ("deploying another provider's serving version",
         dep + f"('{OTHER_ENDPOINT}', '{OTHER_PROVIDER}', 'prod', '{SERVING}', 'private', "
               f"'draft', 1, 1, 'o')"),
        ("deploying onto another provider's endpoint",
         dep + f"('{OTHER_ENDPOINT}', '{NEMO}', 'prod', '{SERVING}', 'private', 'draft', 1, 1, 'o')"),
        ("re-pointing a deployment at another serving version",
         f"update infrx.deployment_revisions set serving_version_id = gen_random_uuid() "
         f"where deployment_revision_id = '{PUBLIC_DEPLOYMENT}'"),
        ("making a private deployment public by update",
         f"update infrx.deployment_revisions set visibility = 'public' "
         f"where deployment_revision_id = '{DEV_DEPLOYMENT}'"),
        ("widening a deployment's validated limits",
         f"update infrx.deployment_revisions set max_output_tokens = 100000 "
         f"where deployment_revision_id = '{PUBLIC_DEPLOYMENT}'"),
        ("an active deployment moved back to proposed_public",
         f"update infrx.deployment_revisions set state = 'proposed_public' "
         f"where deployment_revision_id = '{PUBLIC_DEPLOYMENT}'"),
        ("an active deployment going back to draft",
         f"update infrx.deployment_revisions set state = 'validating' "
         f"where deployment_revision_id = '{PUBLIC_DEPLOYMENT}'"),
        ("resurrecting a retired deployment",
         f"with r as (update infrx.deployment_revisions set state = 'retired' "
         f"where deployment_revision_id = '{DEV_DEPLOYMENT}' returning 1) "
         f"select 1; update infrx.deployment_revisions set state = 'ready_private' "
         f"where deployment_revision_id = '{DEV_DEPLOYMENT}'"),
        ("deleting a deployment", f"delete from infrx.deployment_revisions "
                                  f"where deployment_revision_id = '{DEV_DEPLOYMENT}'"),
        ("re-pricing an admitted card", f"update infrx.rate_card_versions set "
         f"output_rate_per_million = 1 where rate_card_version = '{CARD}'"),
        ("deleting a card", f"delete from infrx.rate_card_versions where rate_card_version = '{CARD}'"),
        ("a USD card", card + f", unit) values ('rc_x', '{MODEL}', '{PUBLIC_DEPLOYMENT}', "
                              f"'{SERVING}', 1, 1, now(), 'o', false, 'USD')"),
        ("an unknown meter", card + f", meter) values ('rc_x', '{MODEL}', '{PUBLIC_DEPLOYMENT}', "
                                    f"'{SERVING}', 1, 1, now(), 'o', false, 'seconds-v1')"),
        ("a negative rate", card + f") values ('rc_x', '{MODEL}', '{PUBLIC_DEPLOYMENT}', "
                                   f"'{SERVING}', -1, 1, now(), 'o', false)"),
        ("an unapproved card", card + f", status) values ('rc_x', '{MODEL}', "
                                      f"'{PUBLIC_DEPLOYMENT}', '{SERVING}', 1, 1, now(), 'o', "
                                      f"false, 'proposed')"),
        ("a card with no approver", card + f") values ('rc_x', '{MODEL}', '{PUBLIC_DEPLOYMENT}', "
                                           f"'{SERVING}', 1, 1, now(), ' ', false)"),
        ("a card pricing a serving version its deployment does not run",
         card + f") values ('rc_x', '{MODEL}', '{PUBLIC_DEPLOYMENT}', gen_random_uuid(), 1, 1, "
                f"now(), 'o', false)"),
        ("a listing of a private dev deployment",
         listing + f"('nemostation/marlin-2b', 9, '{MODEL}', '{DEV_DEPLOYMENT}', '{SERVING}', "
                   f"'{DEV_CARD}', now(), 'o')"),
        ("a listing priced by another deployment's card",
         listing + f"('nemostation/marlin-2b', 9, '{MODEL}', '{PUBLIC_DEPLOYMENT}', '{SERVING}', "
                   f"'{DEV_CARD}', now(), 'o')"),
        ("moving an alias by editing its listing",
         "update infrx.catalog_listings set deployment_revision_id = deployment_revision_id, "
         "version = 2"),
        ("a listing under another model's alias",
         f"insert into public.models (id, name, provider, description, status, base_url, "
         f"served_model, input_usd_per_m, output_usd_per_m, context_tokens, input_modalities, "
         f"output_modalities) values ('x/y', 'x', 'x', 'x', 'live', 'u', 'x', 0, 0, 1, "
         f"'{{text}}', '{{text}}'); " + listing + f"('x/y', 1, '{MODEL}', '{PUBLIC_DEPLOYMENT}', "
         f"'{SERVING}', '{CARD}', now(), 'o')"),
        ("re-owning a model that has versions",
         f"update public.models set provider_org_id = '{OTHER_PROVIDER}' "
         f"where model_uuid = '{MODEL}'"),
        ("a model version owned by a provider that does not own the model",
         mv + good_mv.replace(f"'{NEMO}'", f"'{OTHER_PROVIDER}'")),
        ("a commit that is not a sha", mv + good_mv.replace("f" * 40, "main")),
        ("no weight shard", mv + good_mv.replace(f"array[{sha}]", "'{}'::text[]")),
        ("a null weight shard", mv + good_mv.replace(f"array[{sha}]", f"array[{sha}, null]")),
        ("a malformed shard digest",
         mv + good_mv.replace(f"array[{sha}]", f"array[{sha}, 'md5:abc']")),
        ("an unrecorded digest provenance", mv + good_mv.replace("'served_bytes'", "'guess'")),
        ("editing a model version's weights",
         "update infrx.model_versions set weight_shard_digests = array[]::text[]"),
        ("a second serving version with the same revision label",
         f"insert into infrx.serving_versions (model_version_id, model_id, provider_org_id, "
         f"revision_label, prompt_harness_ref, preprocessor_profile_version, runtime_image_ref, "
         f"engine_options_digest, precision, capability, created_by) select model_version_id, "
         f"model_id, provider_org_id, revision_label, prompt_harness_ref, "
         f"preprocessor_profile_version, runtime_image_ref, engine_options_digest, precision, "
         f"capability, 'o' from infrx.serving_versions where serving_version_id = '{SERVING}'"),
        ("a serving version with another billing meter",
         f"insert into infrx.serving_versions (model_version_id, model_id, provider_org_id, "
         f"revision_label, prompt_harness_ref, preprocessor_profile_version, runtime_image_ref, "
         f"engine_options_digest, precision, capability, created_by) select model_version_id, "
         f"model_id, provider_org_id, 'v2', prompt_harness_ref, preprocessor_profile_version, "
         f"runtime_image_ref, engine_options_digest, precision, capability || "
         f"'{{\"billing_meter\": \"seconds-v1\"}}', 'o' from infrx.serving_versions "
         f"where serving_version_id = '{SERVING}'"),
        ("pinning a runtime digest onto an existing serving version",
         f"update infrx.serving_versions set runtime_image_digest = {sha}"),
        ("a provider role that is a consumer role",
         f"insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by) "
         f"values ('{NEMO}', '{CONSUMER_1}', 'owner', 'ops')"),
        ("two current memberships of one provider",
         f"insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by) "
         f"values ('{NEMO}', '{PROVIDER_DEV_USER}', 'viewer', 'ops')"),
        ("promoting a membership in place",
         f"update infrx.provider_memberships set role = 'administrator' "
         f"where user_id = '{PROVIDER_DEV_USER}'"),
        ("an ungranted membership",
         f"insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by) "
         f"values ('{NEMO}', '{CONSUMER_1}', 'viewer', '')"),
        ("un-revoking a membership",
         f"update infrx.provider_memberships set revoked_at = now() "
         f"where user_id = '{PROVIDER_DEV_USER}'; update infrx.provider_memberships "
         f"set revoked_at = null where user_id = '{PROVIDER_DEV_USER}'"),
        ("deleting a membership",
         f"delete from infrx.provider_memberships where user_id = '{PROVIDER_DEV_USER}'"),
        ("a second dev wallet for one provider",
         f"insert into infrx.credit_wallets (kind, owner_provider_org_id) "
         f"values ('provider_dev', '{NEMO}')"),
        ("a dev wallet for a provider that does not exist",
         "insert into infrx.credit_wallets (kind, owner_provider_org_id) "
         "values ('provider_dev', gen_random_uuid())"),
        ("a signup grant into a provider's dev wallet",
         f"insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id, "
         f"actor) values ('{PROVIDER_WALLET}', 'provider_dev', 'signup_grant', 10000, "
         f"gen_random_uuid(), 'platform')"),
        ("moving a provider's dev wallet to another provider",
         f"update infrx.credit_wallets set owner_provider_org_id = '{OTHER_PROVIDER}' "
         f"where wallet_id = '{PROVIDER_WALLET}'"),
        ("renaming a provider (ownership rows are immutable)",
         f"update infrx.provider_orgs set slug = 'x' where provider_org_id = '{NEMO}'"),
    )
    accepted = (
        ("a well-formed model version of the owner's model", mv + good_mv),
        ("two providers may share display text (it is not a key)",
         "select 1 from infrx.provider_orgs where display_name = 'NemoStation' having count(*) = 2"),
        ("a dev deployment moves forward to retired",
         f"update infrx.deployment_revisions set state = 'retired' "
         f"where deployment_revision_id = '{DEV_DEPLOYMENT}'"),
        ("a membership is revoked once",
         f"update infrx.provider_memberships set revoked_at = now() "
         f"where user_id = '{PROVIDER_DEV_USER}'"),
        ("a new rate is a new card; the alias moves by a new listing version",
         card + f") values ('rc_marlin2b_v2', '{MODEL}', '{PUBLIC_DEPLOYMENT}', '{SERVING}', "
                f"500, 1500, now(), 'ops@infrx', false); " + listing +
         f"('nemostation/marlin-2b', 2, '{MODEL}', '{PUBLIC_DEPLOYMENT}', '{SERVING}', "
         f"'rc_marlin2b_v2', now(), 'ops@infrx')"),
        ("an operator allocation funds the provider's dev wallet",
         f"insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id, "
         f"actor) values ('{PROVIDER_WALLET}', 'provider_dev', 'operator_allocation', 1, "
         f"gen_random_uuid(), 'ops@infrx')"),
    )
    return refused, accepted


def check_registry(conn) -> str:
    """Item 3: provider-owned, immutable registry. Visibility/state/environment rules
    (R70), provider-coherent deployments, immutable cards/listings (CREDIT-RATE), digest
    provenance (R76), provider roles distinct from consumer roles, one dev wallet per
    provider and no signup money for it."""
    refused, accepted = _registry_cases()
    n = _all_refused(conn, refused, "registry")
    m = _all_accepted(conn, accepted, "registry controls")
    return f"{n} registry violations refused, {m} controls accepted"


# =============================================================================
# item 4: pin resolution, CREDIT admission rows, the read surface, privileges
# =============================================================================
def credit_job(rid: str, handle: str, org: str, wallet: str, *, card: str = CARD,
               deployment: str = PUBLIC_DEPLOYMENT, serving: str = SERVING,
               model: str = MODEL, policy: str = POLICY, hold: str = "10.01440000",
               key: str | None = None) -> str:
    """A CREDIT-regime admission row, the shape D2 writes: every pin, no USD price."""
    return f"""insert into infrx.jobs (request_id, job_handle, org_id, key_id, model_revision,
      execution_mode, state, operation, payload_ref, payload_digest, max_input_tokens,
      max_output_tokens, maximum_hold, consent_version, trace_mode, admitted_at, deadline_at,
      budget_preparation_s, budget_queue_wait_s, budget_generation_s, budget_first_token_s,
      budget_stall_s, preparation_deadline_at, accounting_regime, wallet_id, model_id,
      requested_model, deployment_revision_id, serving_version_id, rate_card_version,
      policy_version)
    values ('{rid}', '{handle}', '{org}', {f"'{key}'" if key else 'null'},
      'nemostation/marlin-2b@2026-09-01', 'sync',
      'queued', 'chat.completions', 'infrx-payload:{rid}', '{checks.DIGEST}', 23500, 512,
      {hold}, 1, 'off', infrx.now(), infrx.now() + interval '10 minutes', 120, 10, 300, 60,
      20, infrx.now() + interval '2 minutes', 'credit', '{wallet}', '{model}',
      'nemostation/marlin-2b@2026-09-01', '{deployment}', '{serving}', '{card}',
      '{policy}')"""


def hold(rid: str, org: str, wallet: str, amount: str = "10.01440000", card: str = CARD) -> str:
    return (f"insert into infrx.credit_wallet_holds (request_id, org_id, wallet_id, "
            f"rate_card_version, amount, state) values ('{rid}', '{org}', '{wallet}', "
            f"'{card}', {amount}, 'held')")


def resolve(conn, model: str) -> dict:
    cur = conn.execute("select * from infrx.resolve_admission_pins(%s)", (model,))
    names = [d.name for d in cur.description]
    return dict(zip(names, map(_as_fixture, cur.fetchone())))


def check_resolve_pins(conn) -> str:
    """The D2 seam: the alias and the R62 pin resolve to exactly the F2P `admission_pins`
    fixture; unknown, private-dev, retired and draining are `not_found` (R70); a card
    not yet effective is `invalid_request` (R69); CREDIT admission off is maintenance."""
    fx = _load("admission_pins")
    for model in (fx["requested_model"], "nemostation/marlin-2b"):
        got = resolve(conn, model)
        for k in ("model_id", "deployment_revision_id", "serving_version_id",
                  "rate_card_version", "policy_version", "accounting_regime"):
            assert got[k] == fx[k], f"resolve({model}).{k}: {got[k]!r}, fixture {fx[k]!r}"
        assert got["requested_model"] == model and got["provisional"] is True
        assert got["model_revision"] == "nemostation/marlin-2b@2026-09-01", got
        # D1R review (c): the rates are the fixture card's, to the digit, as text.
        card = _load("rate_card_marlin")
        for k in ("input_rate_per_million", "output_rate_per_million"):
            assert got[k] == card[k], f"resolve({model}).{k}: {got[k]!r}, card {card[k]!r}"
    refusals = (
        ("an unknown alias", "nobody/nothing", "P0002"),
        ("an unknown revision label", "nemostation/marlin-2b@2031-01-01", "P0002"),
        ("the private dev deployment's id", DEV_DEPLOYMENT, "P0002"),
        ("a malformed pin", "nemostation/marlin-2b@a@b", "P0002"),
    )
    for label, model, code in refusals:
        why = attempt(conn, "select * from infrx.resolve_admission_pins(%s)", (model,))
        assert why is not None and why.startswith(code), f"{label}: {why!r}"
    staged = (
        ("a retired listed deployment", "P0002",
         f"update infrx.deployment_revisions set state = 'retired' "
         f"where deployment_revision_id = '{PUBLIC_DEPLOYMENT}'"),
        ("a draining listed deployment", "P0002",
         f"update infrx.deployment_revisions set state = 'draining' "
         f"where deployment_revision_id = '{PUBLIC_DEPLOYMENT}'"),
        ("a listing whose card is not yet effective", "22023",
         f"insert into infrx.rate_card_versions (rate_card_version, model_id, "
         f"deployment_revision_id, serving_version_id, input_rate_per_million, "
         f"output_rate_per_million, effective_at, approved_by, provisional) values "
         f"('rc_future', '{MODEL}', '{PUBLIC_DEPLOYMENT}', '{SERVING}', 1, 1, "
         f"infrx.now() + interval '1 day', 'ops', false); insert into infrx.catalog_listings "
         f"(public_model_id, version, model_id, deployment_revision_id, serving_version_id, "
         f"rate_card_version, effective_at, approved_by) values ('nemostation/marlin-2b', 7, "
         f"'{MODEL}', '{PUBLIC_DEPLOYMENT}', '{SERVING}', 'rc_future', infrx.now(), 'ops')"),
        ("CREDIT admission switched off", "55000",
         "update infrx.feature_flags set enabled = false where name = 'credit_admission'"),
    )
    for label, code, setup in staged:
        with conn.transaction():
            conn.execute(setup)
            why = attempt(conn, "select * from infrx.resolve_admission_pins(%s)",
                          ("nemostation/marlin-2b",))
            raise psycopg.Rollback()
        assert why is not None and why.startswith(code), f"{label}: {why!r}"
    return f"alias and pin == admission_pins fixture; {len(refusals) + len(staged)} refusals"


PDEV_KEY = "c6000000-0000-4000-8000-000000000001"


def _provider_dev_key(org: str, audience: str = "provider_dev") -> str:
    """NEMO's provider_dev key on its dev endpoint, filed in `org` (inside the case).
    `audience="other_provider"`: a provider_dev key of ANOTHER provider (review M5)."""
    if audience == "other_provider":
        return (f"insert into public.api_keys (id, org_id, name, prefix, key_hash, audience, "
                f"provider_org_id, endpoint_id) values ('{PDEV_KEY}', '{org}', 'k', "
                f"'sk-infrx-pdev0000', 'hash-pdev', 'provider_dev', '{OTHER_PROVIDER}', "
                f"'{OTHER_ENDPOINT}')")
    if audience == "consumer":
        return (f"insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash) "
                f"values ('{PDEV_KEY}', '{org}', '{PROVIDER_DEV_USER}', 'k', 'sk-infrx-pdev0000', "
                f"'hash-pdev')")
    return (f"insert into public.api_keys (id, org_id, name, prefix, key_hash, audience, "
            f"provider_org_id, endpoint_id) values ('{PDEV_KEY}', '{org}', 'k', "
            f"'sk-infrx-pdev0000', 'hash-pdev', 'provider_dev', '{NEMO}', '{DEV_ENDPOINT}')")


def _admission_cases(conn) -> tuple[tuple, tuple]:
    w1, w2 = wallet_of(conn, CONSUMER_1), wallet_of(conn, CONSUMER_2)
    o1, o2 = personal_org(conn, CONSUMER_1), personal_org(conn, CONSUMER_2)
    op = personal_org(conn, PROVIDER_DEV_USER)
    j = "5c000000-0000-4000-8000-0000000000"
    usage = ("insert into public.usage_events (id, org_id, model_id, status, "
             "settlement_regime, outcome, settlement_state, usage_certainty, "
             "settlement_version, accounting_regime, charged_credits, rate_card_version, "
             "serving_version_id, deployment_revision_id, prompt_tokens, completion_tokens) "
             "values ")
    admitted = credit_job(j + "01", "job_c_01", o1, w1) + "; " + hold(j + "01", o1, w1)
    refused = (
        ("a CREDIT job pinned to a card of another deployment",
         credit_job(j + "02", "job_c_02", o1, w1, card=DEV_CARD)),
        ("a CREDIT job whose serving pin is not its deployment's",
         credit_job(j + "02", "job_c_02", o1, w1, serving=MODEL)),
        ("a CREDIT job with no policy pin", credit_job(j + "02", "job_c_02", o1, w1)
         .replace(f"'{POLICY}')", "null)")),
        ("a CREDIT job pinned to an unrecorded policy",
         credit_job(j + "02", "job_c_02", o1, w1, policy="dap_unknown")),
        ("a CREDIT job that also carries a USD price",
         credit_job(j + "02", "job_c_02", o1, w1).replace(
             "policy_version)", "policy_version, price_version)").replace(
             f"'{POLICY}')", f"'{POLICY}', 'pv-1')")),
        ("a USD job that also carries CREDIT pins",
         checks._job_values(j + "03", "job_c_03").replace(
             "accounting_regime\n)", "accounting_regime, rate_card_version)") + f", '{CARD}')"),
        ("a new job that does not name its regime",
         checks._job_values(j + "03", "job_c_03").replace(",\n  accounting_regime", "")
         .replace(", 'legacy_usd'", "") + ")"),
        ("spending another individual's wallet through one's own organization",
         credit_job(j + "04", "job_c_04", o1, w2)),
        # Through the provider's own provider_dev key, so only the dev-deployment rule
        # can refuse it (the key rule is its own case below).
        ("a provider dev wallet paying for public production",
         _provider_dev_key(op) + "; "
         + credit_job(j + "05", "job_c_05", op, PROVIDER_WALLET, key=PDEV_KEY)),
        # D2 (D1R review (a)): R70 as a database invariant, and the provider's own key.
        ("a consumer wallet spending on the private dev deployment",
         credit_job(j + "0c", "job_c_0c", o1, w1, card=DEV_CARD, deployment=DEV_DEPLOYMENT)),
        ("a consumer wallet spending on a draining public deployment",
         f"update infrx.deployment_revisions set state = 'draining' where "
         f"deployment_revision_id = '{PUBLIC_DEPLOYMENT}'; "
         + credit_job(j + "0d", "job_c_0d", o1, w1)),
        ("a provider dev job admitted without a key",
         credit_job(j + "0e", "job_c_0e", op, PROVIDER_WALLET, card=DEV_CARD,
                    deployment=DEV_DEPLOYMENT)),
        ("a provider dev job admitted through another provider's provider_dev key",
         _provider_dev_key(op, "other_provider") + "; "
         + credit_job(j + "0e", "job_c_0e", op, PROVIDER_WALLET, card=DEV_CARD,
                      deployment=DEV_DEPLOYMENT, key=PDEV_KEY)),
        ("a provider dev job admitted through a consumer key",
         _provider_dev_key(op, "consumer") + "; "
         + credit_job(j + "0e", "job_c_0e", op, PROVIDER_WALLET, card=DEV_CARD,
                      deployment=DEV_DEPLOYMENT, key=PDEV_KEY)),
        ("a hold beyond the available credit",
         credit_job(j + "06", "job_c_06", o1, w1) + "; "
         + hold(j + "06", o1, w1, amount="10000.00000001")),
        ("a hold on another wallet than the job's",
         credit_job(j + "07", "job_c_07", o1, w1) + "; " + hold(j + "07", o1, w2)),
        ("a hold at another card than the job's",
         credit_job(j + "07", "job_c_07", o1, w1) + "; " + hold(j + "07", o1, w1, card=DEV_CARD)),
        ("a second hold for one request", admitted + "; " + hold(j + "01", o1, w1)),
        ("a hold born settled", credit_job(j + "08", "job_c_08", o1, w1) + "; "
         + hold(j + "08", o1, w1).replace("'held')", "'settled')")),
        ("re-pinning an admitted job to the next card of its deployment",
         f"insert into infrx.rate_card_versions (rate_card_version, model_id, "
         f"deployment_revision_id, serving_version_id, input_rate_per_million, "
         f"output_rate_per_million, effective_at, approved_by, provisional) values "
         f"('rc_next', '{MODEL}', '{PUBLIC_DEPLOYMENT}', '{SERVING}', 1, 1, now(), 'ops', "
         f"false); " + credit_job(j + "0b", "job_c_0b", o1, w1)
         + f"; update infrx.jobs set rate_card_version = 'rc_next' where request_id = '{j}0b'"),
        ("re-pointing an admitted job at a real second serving version and its deployment",
         f"insert into infrx.serving_versions (serving_version_id, model_version_id, model_id, "
         f"provider_org_id, revision_label, prompt_harness_ref, preprocessor_profile_version, "
         f"runtime_image_ref, engine_options_digest, precision, capability, created_by) "
         f"select '{SERVING_2}', model_version_id, model_id, provider_org_id, '2026-09-02', "
         f"prompt_harness_ref, preprocessor_profile_version, runtime_image_ref, "
         f"engine_options_digest, precision, capability, 'ops' from infrx.serving_versions "
         f"where serving_version_id = '{SERVING}'; "
         f"insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, "
         f"provider_org_id, environment, serving_version_id, visibility, state, "
         f"max_input_tokens, max_output_tokens, created_by) values ('{DEPLOYMENT_2}', "
         f"'{PROD_ENDPOINT}', '{NEMO}', 'prod', '{SERVING_2}', 'public', 'proposed_public', "
         f"30720, 2048, 'ops'); insert into infrx.rate_card_versions (rate_card_version, "
         f"model_id, deployment_revision_id, serving_version_id, input_rate_per_million, "
         f"output_rate_per_million, effective_at, approved_by, provisional) values "
         f"('rc_sv2', '{MODEL}', '{DEPLOYMENT_2}', '{SERVING_2}', 1, 1, now(), 'ops', false); "
         + admitted + f"; update infrx.jobs set serving_version_id = '{SERVING_2}', "
         f"deployment_revision_id = '{DEPLOYMENT_2}', rate_card_version = 'rc_sv2' "
         f"where request_id = '{j}01'"),
        ("switching an admitted job's regime",
         admitted + f"; update infrx.jobs set accounting_regime = 'legacy_usd' "
                    f"where request_id = '{j}01'"),
        ("resizing a hold", admitted + f"; update infrx.credit_wallet_holds set amount = 1 "
                                       f"where request_id = '{j}01'"),
        ("debiting unknown usage later",
         admitted + f"; update infrx.credit_wallet_holds set state = 'unknown', "
                    f"reconcile_after = now() where request_id = '{j}01'; "
                    f"update infrx.credit_wallet_holds set state = 'settled', "
                    f"reconcile_after = null where request_id = '{j}01'"),
        ("re-holding a settled hold",
         admitted + f"; update infrx.credit_wallet_holds set state = 'settled' "
                    f"where request_id = '{j}01'; update infrx.credit_wallet_holds "
                    f"set state = 'held' where request_id = '{j}01'"),
        ("deleting a hold", admitted + f"; delete from infrx.credit_wallet_holds "
                                       f"where request_id = '{j}01'"),
        ("a settlement at a card the job was not admitted at",
         admitted + "; " + usage + f"('{j}01', '{o1}', 'nemostation/marlin-2b', 200, 'pilot', "
         f"'completed', 'settled', 'authoritative', 1, 'credit', 9.976, '{DEV_CARD}', "
         f"'{SERVING}', '{DEV_DEPLOYMENT}', 23500, 480)"),
        ("a CREDIT settlement that also states a USD cost",
         admitted + "; " + usage.replace("prompt_tokens,", "cost_usd, prompt_tokens,")
         + f"('{j}01', '{o1}', 'nemostation/marlin-2b', 200, 'pilot', 'completed', "
         f"'settled', 'authoritative', 1, 'credit', 9.976, '{CARD}', '{SERVING}', "
         f"'{PUBLIC_DEPLOYMENT}', 0.01, 23500, 480)"),
        ("a debit from another wallet than the job's",
         admitted + f"; insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
         f"operation_id, request_id, actor) values ('{w2}', 'consumer', 'inference_debit', "
         f"-9.976, gen_random_uuid(), '{j}01', 'svc')"),
        ("a second debit for one request",
         admitted + f"; insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
         f"operation_id, request_id, actor) values ('{w1}', 'consumer', 'inference_debit', "
         f"-1, gen_random_uuid(), '{j}01', 'svc'), ('{w1}', 'consumer', 'inference_debit', "
         f"-1, gen_random_uuid(), '{j}01', 'svc')"),
    )
    settle = (f"; update infrx.credit_wallet_holds set state = 'settled' where request_id = "
              f"'{j}01'; insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
              f"operation_id, request_id, actor) values ('{w1}', 'consumer', 'inference_debit', "
              f"-9.976, gen_random_uuid(), '{j}01', 'svc'); " + usage
              + f"('{j}01', '{o1}', 'nemostation/marlin-2b', 200, 'pilot', 'completed', "
              f"'settled', 'authoritative', 1, 'credit', 9.976, '{CARD}', '{SERVING}', "
              f"'{PUBLIC_DEPLOYMENT}', 23500, 480)")
    accepted = (
        ("admission, hold and settlement at the admitted card (usage_credit fixture)",
         admitted + settle),
        ("a hold of exactly the available credit",
         credit_job(j + "09", "job_c_09", o1, w1, hold="10000") + "; "
         + hold(j + "09", o1, w1, amount="10000")),
        ("a provider dev wallet on its own dev deployment, through its provider_dev key",
         _provider_dev_key(op) + "; "
         + credit_job(j + "0a", "job_c_0a", op, PROVIDER_WALLET, card=DEV_CARD,
                      deployment=DEV_DEPLOYMENT, key=PDEV_KEY)),
        ("unknown usage keeps its hold, then releases it",
         admitted + f"; update infrx.credit_wallet_holds set state = 'unknown', "
                    f"reconcile_after = now() where request_id = '{j}01'; "
                    f"update infrx.credit_wallet_holds set state = 'released', "
                    f"reconcile_after = null where request_id = '{j}01'"),
    )
    return refused, accepted


def check_credit_admission_rows(conn) -> str:
    """D2's atomic admission schema: a CREDIT job carries every pin and its wallet and no
    USD price; the pins are one card's own and never change (R69/R78); the wallet is
    reachable from the admission (R66); a hold reserves in CREDIT and 402 is the named
    `credit_wallets_reserved_within_total`; unknown usage is released, never debited;
    a settlement is at the admitted card, once, from the job's wallet."""
    refused, accepted = _admission_cases(conn)
    n = _all_refused(conn, refused, "CREDIT admission")
    m = _all_accepted(conn, accepted, "CREDIT admission controls")
    j = "5c000000-0000-4000-8000-0000000000"
    why = attempt(conn, credit_job(j + "06", "job_c_06", personal_org(conn, CONSUMER_1),
                                   wallet_of(conn, CONSUMER_1)) + "; "
                  + hold(j + "06", personal_org(conn, CONSUMER_1), wallet_of(conn, CONSUMER_1),
                         amount="10000.00000001"))
    assert why is not None and "credit_wallets_reserved_within_total" in why, \
        f"insufficient credit is not the named 402 constraint: {why!r}"
    # The whole lifecycle leaves the summary equal to ledger and holds.
    with conn.transaction():
        conn.execute(accepted[0][1])
        w1 = wallet_of(conn, CONSUMER_1)
        row = conn.execute("select ledger_total::text, reserved_total::text from "
                           "infrx.credit_wallets where wallet_id = %s", (w1,)).fetchone()
        assert row == ("9990.02400000", "0.00000000"), f"after one settlement: {row}"
        check_credit_reconciles(conn)
        raise psycopg.Rollback()
    # D1R review (c): reconciliation holds while an UNKNOWN hold is outstanding too.
    with conn.transaction():
        conn.execute(accepted[0][1].split("; update infrx.credit_wallet_holds")[0])
        conn.execute("update infrx.credit_wallet_holds set state = 'unknown', reconcile_after "
                     "= infrx.now() + interval '1 day' where request_id = %s", (j + "01",))
        reserved = conn.execute("select reserved_total::text from infrx.credit_wallets "
                                "where wallet_id = %s", (wallet_of(conn, CONSUMER_1),)).fetchone()
        assert reserved == ("10.01440000",), f"an unknown hold left the reservation: {reserved}"
        check_credit_reconciles(conn)
        raise psycopg.Rollback()
    return f"{n} admission/hold/settlement violations refused, {m} controls accepted"


def check_credit_rate(conn) -> str:
    """CREDIT-RATE: a job admitted at card v1 keeps v1 after a new card and a new listing
    are published (admission now resolves v2), and settles only at v1."""
    o1, w1 = personal_org(conn, CONSUMER_1), wallet_of(conn, CONSUMER_1)
    rid = "5c000000-0000-4000-8000-0000000000f1"
    with conn.transaction():
        pins = resolve(conn, "nemostation/marlin-2b")
        conn.execute(credit_job(rid, "job_rate", o1, w1, card=pins["rate_card_version"]))
        conn.execute(hold(rid, o1, w1, card=pins["rate_card_version"]))
        conn.execute(f"""
          insert into infrx.rate_card_versions (rate_card_version, model_id,
            deployment_revision_id, serving_version_id, input_rate_per_million,
            output_rate_per_million, effective_at, approved_by, provisional)
          values ('rc_marlin2b_v2', '{MODEL}', '{PUBLIC_DEPLOYMENT}', '{SERVING}', 800, 2400,
                  infrx.now(), 'ops@infrx', false);
          insert into infrx.catalog_listings (public_model_id, version, model_id,
            deployment_revision_id, serving_version_id, rate_card_version, effective_at,
            approved_by)
          values ('nemostation/marlin-2b', 2, '{MODEL}', '{PUBLIC_DEPLOYMENT}', '{SERVING}',
                  'rc_marlin2b_v2', infrx.now(), 'ops@infrx')""")
        now = resolve(conn, "nemostation/marlin-2b")
        assert now["rate_card_version"] == "rc_marlin2b_v2", f"new admission: {now}"
        kept, = conn.execute("select rate_card_version from infrx.jobs where request_id = %s",
                             (rid,)).fetchone()
        assert kept == pins["rate_card_version"] == CARD, f"the queued job moved to {kept}"
        settle_at = ("insert into public.usage_events (id, org_id, model_id, status, "
                     "settlement_regime, outcome, settlement_state, usage_certainty, "
                     "settlement_version, accounting_regime, charged_credits, "
                     "rate_card_version, serving_version_id, deployment_revision_id) values "
                     f"('{rid}', '{o1}', 'nemostation/marlin-2b', 200, 'pilot', 'completed', "
                     f"'settled', 'authoritative', 1, 'credit', 1, %s, '{SERVING}', "
                     f"'{PUBLIC_DEPLOYMENT}')")
        assert attempt(conn, settle_at, ("rc_marlin2b_v2",)) is not None, \
            "the queued job settled at the newly published card"
        assert attempt(conn, settle_at, (CARD,)) is None, "the admitted card was refused"
        raise psycopg.Rollback()
    return "admitted at v1, v2 published, v1 kept and settled; v2 refused for that job"


def check_fail_closed(conn) -> str:
    """Item 5: application is not enablement. With a flag off, the write it guards is a
    maintenance refusal (55000) - never an unmetered or unregimed success."""
    o1, w1 = personal_org(conn, CONSUMER_1), wallet_of(conn, CONSUMER_1)
    cases = (
        ("credit_admission", credit_job("5c000000-0000-4000-8000-0000000000e1", "job_off",
                                        o1, w1)),
        ("legacy_usd_admission", checks._job_values("5c000000-0000-4000-8000-0000000000e2",
                                                    "job_off2") + ")"),
        ("signup_grant", f"select infrx.grant_signup_credit('{UNGRANTED}', 'e')"),
        ("credit_admission", "select * from infrx.resolve_admission_pins('nemostation/marlin-2b')"),
    )
    for flag, sql in cases:
        assert attempt(conn, sql) is None, f"{flag}: the control was refused while enabled"
        with conn.transaction():
            set_flag(conn, flag, False)
            why = attempt(conn, sql)
            raise psycopg.Rollback()
        assert why is not None and why.startswith("55000"), f"{flag} off: {why!r}"
    with conn.transaction():
        conn.execute("delete from infrx.feature_flags where name = 'credit_admission'")
        why = attempt(conn, cases[0][1])
        raise psycopg.Rollback()
    assert why is not None and why.startswith("55000"), f"a missing flag row: {why!r}"
    return (f"{len(cases)} guarded writes refuse with 55000 when off, and when the flag row "
            f"is missing")


def check_flag_defaults(conn) -> str:
    """Applying the migrations enables nothing new: CREDIT admission and the signup grant
    start OFF; the pilot's own USD admission keeps its pre-cutover behaviour."""
    got = dict(conn.execute("select name, enabled from infrx.feature_flags").fetchall())
    want = {"signup_grant": False, "credit_admission": False, "legacy_usd_admission": True}
    assert got == want, f"feature flags after apply: {got}, expected {want}"
    return f"flags after apply: {got}"



SESSIONS = {
    "anon": checks.SESSIONS["anon"],
    "consumer": checks._jwt(CONSUMER_1),
    "consumer_2": checks._jwt(CONSUMER_2),
    "ungranted": checks._jwt(UNGRANTED),
    "provider_member": checks._jwt(PROVIDER_DEV_USER),
    "provider_admin": checks._jwt(PROVIDER_ADMIN_USER),
    "operator": checks._jwt(checks.USER_OPERATOR),
    "service": checks.SESSIONS["service"],
}
BROWSER = ("anon", "consumer", "provider_member", "provider_admin", "operator")


def rows_as(conn, session: str, sql: str, params=None) -> list:
    with conn.transaction():
        conn.execute(SESSIONS[session])
        out = conn.execute(sql, params).fetchall()
        raise psycopg.Rollback()
    return out


def refused_as(conn, session: str, sql: str) -> str | None:
    try:
        with conn.transaction():
            conn.execute(SESSIONS[session])
            conn.execute(sql)
            raise _Allowed()
    except _Allowed:
        return None
    except psycopg.Error as refused:
        return f"{refused.sqlstate} {str(refused).splitlines()[0][:100]}"


def check_credit_read_surface(conn) -> str:
    """Item 4: exact text amounts and explicit units; an individual reads only their own
    wallet and ledger (not even co-members of their org, not a provider's dev wallet);
    operator principals read `platform` to a customer; the wallet summary refuses
    another user's id; the legacy USD statement is separate, labelled, and carries the
    rollout hold."""
    w1 = wallet_of(conn, CONSUMER_1)
    c1 = rows_as(conn, "consumer", "select * from public.console_wallet_summary(%s)",
                 (CONSUMER_1,))
    assert len(c1) == 1, c1
    user, wallet, kind, unit, total, reserved, available, revision, granted = c1[0]
    assert (str(user), str(wallet), kind, unit, total, reserved, available) == (
        CONSUMER_1, w1, "consumer", "CREDIT", "10000.00000000", "0.00000000",
        "10000.00000000"), f"consumer summary: {c1}"
    assert granted is not None and revision == 1, c1
    none = rows_as(conn, "ungranted", "select wallet_id, ledger_total, available "
                   "from public.console_wallet_summary(%s)", (UNGRANTED,))
    assert none == [(None, "0.00000000", "0.00000000")], f"no wallet yet: {none}"
    for session, target in (("consumer_2", CONSUMER_1), ("provider_admin", CONSUMER_1)):
        why = refused_as(conn, session,
                         f"select * from public.console_wallet_summary('{target}')")
        assert why is not None and why.startswith("42501"), f"{session} read {target}: {why}"
    assert refused_as(conn, "anon", f"select * from public.console_wallet_summary("
                                    f"'{CONSUMER_1}')") is not None, "anon read a wallet"
    for session in ("operator", "service"):
        got = rows_as(conn, session, "select ledger_total from public.console_wallet_summary(%s)",
                      (CONSUMER_1,))
        assert got == [("10000.00000000",)], f"{session}: {got}"
    # Visibility of the pages themselves.
    seen = {s: {str(r[0]) for r in rows_as(conn, s, "select wallet_id from "
                                                   "public.console_credit_wallets")}
            for s in ("consumer", "consumer_2", "provider_member", "provider_admin",
                      "operator")}
    assert seen["consumer"] == {w1}, f"consumer sees wallets {seen['consumer']}"
    assert PROVIDER_WALLET not in seen["provider_admin"] | seen["provider_member"], \
        "a provider member reads the provider dev wallet through the consumer surface"
    assert refused_as(conn, "anon", "select * from public.console_credit_wallets"), \
        "anon reads the wallet page"
    assert {w1, PROVIDER_WALLET} <= seen["operator"], seen["operator"]
    with conn.transaction():
        conn.execute("insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
                     "operation_id, actor, reason) values (%s, 'consumer', "
                     "'operator_adjustment', 1, gen_random_uuid(), %s, 'goodwill')",
                     (w1, checks.USER_OPERATOR))
        mine = rows_as(conn, "consumer", "select wallet_id, kind, amount, unit, actor "
                       "from public.console_credit_ledger order by created_at, kind")
        ops = rows_as(conn, "operator", "select actor from public.console_credit_ledger "
                      "where wallet_id = %s and kind = 'operator_adjustment'", (w1,))
        raise psycopg.Rollback()
    assert {str(r[0]) for r in mine} == {w1}, f"consumer ledger rows: {mine}"
    assert ("signup_grant", "10000.00000000", "CREDIT", "platform") in \
        {r[1:] for r in mine}, mine
    assert {r[4] for r in mine} == {"platform"}, f"an operator principal leaked: {mine}"
    assert ops == [(checks.USER_OPERATOR,)], f"operator view of the principal: {ops}"
    assert rows_as(conn, "consumer_2", "select count(*) from public.console_credit_ledger "
                   "where wallet_id = %s", (w1,)) == [(0,)], "a consumer read another ledger"
    # Money is text on every new surface.
    numeric = conn.execute("""
        select table_name, column_name from information_schema.columns
        where table_schema = 'public'
          and table_name in ('console_credit_wallets', 'console_credit_ledger', 'console_usage')
          and column_name in ('ledger_total', 'reserved_total', 'available', 'amount',
                              'charged_credits', 'credit_hold', 'cost', 'max_hold')
          and data_type <> 'text'""").fetchall()
    assert not numeric, f"money leaves SQL as a number: {numeric}"
    # The legacy USD statement: its own unit, the hold when nonzero, guarded.
    stmt = rows_as(conn, "operator", "select accounting_regime, unit, balance, entry_count, "
                   "rollout_hold from public.console_legacy_usd_statement(%s)", (checks.ORG_A,))
    bal, n = conn.execute("select coalesce(sum(delta_usd), 0)::numeric(20,8)::text, count(*) "
                          "from public.credit_ledger where org_id = %s",
                          (checks.ORG_A,)).fetchone()
    assert stmt == [("legacy_usd", "USD", bal, n, bal != "0.00000000")], \
        f"legacy statement {stmt} vs ledger {bal}/{n}"
    zero = rows_as(conn, "consumer", "select balance, entry_count, rollout_hold from "
                   "public.console_legacy_usd_statement(%s)", (personal_org(conn, CONSUMER_1),))
    assert zero == [("0.00000000", 0, False)], f"an org with no USD history: {zero}"
    why = refused_as(conn, "consumer", f"select * from public.console_legacy_usd_statement("
                                       f"'{checks.ORG_A}')")
    assert why is not None and why.startswith("42501"), f"cross-org legacy statement: {why}"
    return ("wallet summary, wallet and ledger pages scoped to the individual; operator "
            "masked; money as text; legacy USD statement separate with rollout_hold")


def check_credit_privileges(conn) -> str:
    """Item 4 / R59-4: the platform role reads the CREDIT money relations and writes them
    only through D's definer functions; it cannot delete or truncate registry history;
    no browser role holds anything in `infrx`; the only new browser-callable functions
    are the two console RPCs (checked by `checks.check_function_privileges`)."""
    problems = []
    no_write = ("credit_wallets", "credit_ledger", "signup_entitlements",
                "credit_wallet_holds", "credit_wallet_reconciliation")
    for table in no_write:
        for verb in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            if conn.execute("select has_table_privilege('service_role', %s, %s)",
                            (f"infrx.{table}", verb)).fetchone()[0]:
                problems.append(f"service_role may {verb} infrx.{table}")
        if not conn.execute("select has_table_privilege('service_role', %s, 'SELECT')",
                            (f"infrx.{table}",)).fetchone()[0]:
            problems.append(f"service_role cannot read infrx.{table}")
    for table in ("provider_orgs", "provider_memberships", "model_versions", "serving_versions",
                  "endpoints", "deployment_revisions", "rate_card_versions",
                  "data_access_policies", "catalog_listings", "feature_flags"):
        for verb in ("DELETE", "TRUNCATE"):
            if conn.execute("select has_table_privilege('service_role', %s, %s)",
                            (f"infrx.{table}", verb)).fetchone()[0]:
                problems.append(f"service_role may {verb} infrx.{table}")
    # D1R review (c): the immutable registry relations are not even UPDATE-able by the
    # platform role (the triggers are the second wall, not the only one).
    for table in ("provider_orgs", "model_versions", "serving_versions", "endpoints",
                  "rate_card_versions", "data_access_policies", "catalog_listings"):
        if conn.execute("select has_table_privilege('service_role', %s, 'UPDATE')",
                        (f"infrx.{table}",)).fetchone()[0]:
            problems.append(f"service_role may UPDATE immutable infrx.{table}")
    for column in ("enabled", "updated_by", "reason"):
        if not conn.execute("select has_column_privilege('service_role', "
                            "'infrx.feature_flags', %s, 'UPDATE')", (column,)).fetchone()[0]:
            problems.append(f"the operator cannot switch feature_flags.{column}")
    if conn.execute("select has_table_privilege('service_role', 'infrx.feature_flags', "
                    "'INSERT')").fetchone()[0]:
        problems.append("service_role may invent a feature flag")
    assert not problems, "platform-role privileges:\n  " + "\n  ".join(problems)
    return (f"{len(no_write)} CREDIT relations read-only to service_role; registry and flags "
            f"never deleted")


#: (what is protected, statement). Every browser session must be refused every one.
ATTACKS = (
    ("mint: a CREDIT ledger row",
     f"insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, operation_id, "
     f"actor) select wallet_id, 'consumer', 'operator_adjustment', 1000, gen_random_uuid(), "
     f"'me' from infrx.credit_wallets limit 1"),
    ("mint: the signup grant RPC", f"select infrx.grant_signup_credit('{UNGRANTED}', 'me')"),
    ("mint: an entitlement", f"insert into infrx.signup_entitlements (user_id, entitlement, "
                             f"wallet_id, amount, verification_evidence_ref, ledger_operation_id)"
                             f" values ('{UNGRANTED}', 'initial_signup_grant', "
                             f"gen_random_uuid(), 10000, 'me', gen_random_uuid())"),
    ("balances: a wallet total", "update infrx.credit_wallets set ledger_total = 1e6"),
    ("balances: read wallets directly", "select ledger_total from infrx.credit_wallets"),
    ("balances: through the console view",
     "update public.console_credit_wallets set ledger_total = '1000000'"),
    ("ledger: through the console view", "delete from public.console_credit_ledger"),
    ("hold", "insert into infrx.credit_wallet_holds (request_id, org_id, wallet_id, "
             "rate_card_version, amount, state) values (gen_random_uuid(), gen_random_uuid(), "
             "gen_random_uuid(), 'x', 0, 'held')"),
    ("settle", "update infrx.credit_wallet_holds set state = 'settled'"),
    ("admission pins", "select * from infrx.resolve_admission_pins('nemostation/marlin-2b')"),
    ("provider role: self-assign administrator",
     f"insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by) "
     f"values ('{NEMO}', '{CONSUMER_1}', 'administrator', 'me')"),
    ("provider role: revoke someone", "update infrx.provider_memberships set revoked_at = now()"),
    ("provider role: read memberships", "select * from infrx.provider_memberships"),
    ("rates: publish a card", f"insert into infrx.rate_card_versions (rate_card_version, "
                              f"model_id, deployment_revision_id, serving_version_id, "
                              f"input_rate_per_million, output_rate_per_million, effective_at, "
                              f"approved_by, provisional) values ('rc_free', '{MODEL}', "
                              f"'{PUBLIC_DEPLOYMENT}', '{SERVING}', 0, 0, now(), 'me', false)"),
    ("rates: move the alias", f"insert into infrx.catalog_listings (public_model_id, version, "
                              f"model_id, deployment_revision_id, serving_version_id, "
                              f"rate_card_version, effective_at, approved_by) values "
                              f"('nemostation/marlin-2b', 99, '{MODEL}', "
                              f"'{PUBLIC_DEPLOYMENT}', '{SERVING}', '{CARD}', now(), 'me')"),
    ("deployment: promote", f"update infrx.deployment_revisions set state = 'retired' "
                            f"where deployment_revision_id = '{DEV_DEPLOYMENT}'"),
    ("model ownership", f"update public.models set provider_org_id = '{OTHER_PROVIDER}'"),
    ("model identity", f"update public.models set model_uuid = gen_random_uuid()"),
    ("rollout switch", "update infrx.feature_flags set enabled = true"),
    ("the flag gate", "select infrx.require_feature('credit_admission')"),
    ("usage: write a CREDIT settlement",
     "update public.usage_events set accounting_regime = 'credit'"),
)

#: The platform role's side of the same boundary: what it does through D's functions
#: (allowed) and what it can never do directly (refused).
SERVICE_ALLOWED = (
    ("grants through the A1 seam", f"select infrx.grant_signup_credit('{UNGRANTED}', 'e')"),
    ("resolves pins for D2", "select * from infrx.resolve_admission_pins('nemostation/marlin-2b')"),
    ("reads wallets", "select ledger_total from infrx.credit_wallets"),
    ("grants a provider role (Lab/G6B server path)",
     f"insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by) "
     f"values ('{NEMO}', '{CONSUMER_1}', 'viewer', 'ops@infrx')"),
    ("switches a rollout flag", "update infrx.feature_flags set enabled = true, "
                                "updated_by = 'ops@infrx', reason = 'cutover' "
                                "where name = 'credit_admission'"),
)
SERVICE_REFUSED = (
    ("mints directly", ATTACKS[0][1]),
    ("moves a total directly", "update infrx.credit_wallets set ledger_total = 1e6"),
    ("holds directly", ATTACKS[7][1]),
    ("writes an entitlement directly", ATTACKS[2][1]),
    ("deletes a rate card", "delete from infrx.rate_card_versions"),
    ("truncates the registry", "truncate infrx.catalog_listings"),
)


def check_credit_role_matrix(conn) -> str:
    """DUR-RLS extended: anon, consumer-only, provider member, provider administrator and
    platform operator sessions are refused every mint, hold, settle, provider-role,
    rate, deployment and rollout write, and every direct `infrx` read; the service role
    does what D's seams give it and nothing directly on the money relations."""
    reached = [f"{s} may {label}" for label, sql in ATTACKS for s in BROWSER
               if refused_as(conn, s, sql) is None]
    assert not reached, "browser sessions reached protected state:\n  " + "\n  ".join(reached)
    denied = [f"{label}: {why}" for label, sql in SERVICE_ALLOWED
              if (why := refused_as(conn, "service", sql)) is not None]
    assert not denied, "the platform role lost a seam:\n  " + "\n  ".join(denied)
    direct = [label for label, sql in SERVICE_REFUSED if refused_as(conn, "service", sql) is None]
    assert not direct, "the platform role wrote money directly:\n  " + "\n  ".join(direct)
    for session, user in (("consumer", CONSUMER_1), ("provider_member", PROVIDER_DEV_USER),
                          ("provider_admin", PROVIDER_ADMIN_USER)):
        uid, = rows_as(conn, session, "select auth.uid()")[0]
        assert str(uid) == user, f"the {session} session is nobody: {uid}"
    return (f"{len(ATTACKS)} attacks x {len(BROWSER)} browser sessions refused; service: "
            f"{len(SERVICE_ALLOWED)} seams allowed, {len(SERVICE_REFUSED)} direct writes refused")


# =============================================================================
# item 5: re-run, legacy read path
# =============================================================================
def snapshot(conn) -> dict:
    """The schema inventory plus every row D1R's relations and flags hold."""
    data = {}
    for table in ("feature_flags", "credit_wallets", "credit_ledger", "signup_entitlements",
                  "credit_wallet_holds", "provider_orgs", "provider_memberships",
                  "model_versions", "serving_versions", "endpoints", "deployment_revisions",
                  "rate_card_versions", "data_access_policies", "catalog_listings"):
        data[table] = conn.execute(f"select to_jsonb(t)::text from infrx.{table} t "
                                   f"order by 1").fetchall()
    data["models"] = conn.execute("select id, model_uuid, provider_org_id from public.models "
                                  "order by id").fetchall()
    data["jobs"] = conn.execute("select to_jsonb(j)::text from infrx.jobs j order by 1").fetchall()
    return {"inventory": inventory(conn), "data": data}


def check_rerun_is_noop(conn, apply_again) -> str:
    """Item 5: applying D1R's migrations a second time - after an operator has enabled a
    flag and granted credit - changes no schema object, no row and no flag."""
    set_flag(conn, "signup_grant", True)
    fresh = "c1000000-0000-4000-8000-0000000000ff"       # one personal org, one member
    conn.execute("insert into auth.users (id, email) values (%s, 'rerun@example.com')",
                 (fresh,))
    grant(conn, fresh)
    before = snapshot(conn)
    apply_again()
    after = snapshot(conn)
    changed = sorted(k for k in before["inventory"]
                     if after["inventory"].get(k) != before["inventory"][k])
    added = sorted(set(after["inventory"]) - set(before["inventory"]))
    assert not changed and not added, f"a re-run changed the schema: {(changed + added)[:10]}"
    moved = [t for t in before["data"] if after["data"][t] != before["data"][t]]
    assert not moved, f"a re-run changed rows of {moved}"
    return (f"re-run: {len(before['inventory'])} objects and "
            f"{sum(map(len, before['data'].values()))} rows unchanged, flags kept")


#: The deployed console's reads (apps/app/lib, apps/app/app) and README reporting calls,
#: as the SQL PostgREST sends, for a member of the organization.
def _legacy_member_reads(org: str) -> tuple:
    return (
        ("session: own profile", "select email, is_operator from public.profiles "
                                 "where id = auth.uid()"),
        ("session: memberships with org names",
         "select m.org_id, m.role, o.name from public.org_members m "
         "join public.organizations o on o.id = m.org_id"),
        ("teams: members' emails", "select m.role, p.email from public.org_members m "
                                   "join public.profiles p on p.id = m.user_id"),
        ("credits.ts: org_wallet_summary", f"select * from public.org_wallet_summary('{org}')"),
        ("credits.ts: ledger select list", f"select id, delta_usd, kind, reason, ref, created_at "
                                           f"from public.credit_ledger where org_id = '{org}'"),
        ("api-keys page", "select id, name, prefix, created_at, last_used_at, revoked_at "
                          "from public.api_keys"),
        ("models page", "select * from public.models where status <> 'retired' order by sort"),
        ("README: org_usage_summary",
         f"select * from public.org_usage_summary('{org}', now() - interval '365 days', now())"),
        ("README: org_usage_daily",
         f"select * from public.org_usage_daily('{org}', now() - interval '365 days', now())"),
        ("README: org_balance", f"select public.org_balance('{org}')"),
        ("0005: console_usage", "select * from public.console_usage"),
        ("0005: console_ledger", "select * from public.console_ledger"),
        ("0005: wallets", "select * from public.wallets"),
        ("0005: console_usage_summary", f"select * from public.console_usage_summary('{org}', "
                                        f"now() - interval '365 days', now())"),
        ("0005: console_usage_daily", f"select * from public.console_usage_daily('{org}', "
                                      f"now() - interval '365 days', now())"),
    )


def check_legacy_read_path(conn, before: dict) -> str:
    """Item 5: after D1R the deployed console's reads and the admin page's service reads
    still execute, the USD figures they return are the historical ones, and the admin
    `addCredit` USD insert and the deployed gateway's usage insert still work and stay in
    the legacy regime."""
    org = before["legacy"]["org_a"]
    failed = []
    for label, sql in _legacy_member_reads(org):
        why = refused_as_user(conn, checks.USER_MEMBER, sql)
        if why is not None:
            failed.append(f"member {label}: {why}")
    for label, sql in (
        ("admin: organizations", "select id, name, slug, created_at from public.organizations"),
        ("admin: org_members", "select org_id from public.org_members"),
        ("admin: api_keys", "select org_id, revoked_at from public.api_keys"),
        ("admin: usage_events", "select org_id, cost_usd from public.usage_events"),
        ("admin: credit_ledger", "select org_id, delta_usd from public.credit_ledger"),
    ):
        if (why := refused_as(conn, "service", sql)) is not None:
            failed.append(f"service {label}: {why}")
    assert not failed, "the legacy read path broke:\n  " + "\n  ".join(failed)
    usd = rows_as_user(conn, checks.USER_MEMBER,
                       f"select ledger_total from public.org_wallet_summary('{org}')")
    want = dict((str(o), t) for o, t in before["legacy"]["balances"])[org]
    assert usd == [(f"{want:.8f}",)], f"USD summary {usd} vs history {want}"
    try:
        regimes = _legacy_writes(conn, org)
    except psycopg.Error as refused:
        raise AssertionError(f"a legacy USD writer was refused: {refused}") from None
    assert regimes == [("legacy_usd",)], f"a legacy writer's row changed regime: {regimes}"
    return (f"{len(_legacy_member_reads(org))} member reads and 5 admin reads execute; USD "
            f"summary = history; addCredit and gateway writes stay legacy_usd")


def _legacy_writes(conn, org: str) -> list:
    """The admin page's addCredit and the deployed gateway's usage row, as service_role."""
    with conn.transaction():
        conn.execute(checks.SESSIONS["service"])
        conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, reason, "
                     "created_by) values (%s, 5, 'grant', 'admin addCredit', %s)",
                     (org, checks.USER_OPERATOR))
        conn.execute("insert into public.usage_events (id, org_id, model_id, status, "
                     "prompt_tokens, completion_tokens, cost_usd) values (gen_random_uuid(), "
                     "%s, 'nemostation/marlin-2b', 200, 10, 5, 0.00000350)", (org,))
        regimes = conn.execute("select distinct accounting_regime from public.usage_events"
                               ).fetchall()
        raise psycopg.Rollback()
    return regimes


def rows_as_user(conn, user: str, sql: str) -> list:
    with conn.transaction():
        conn.execute(checks._jwt(user))
        out = conn.execute(sql).fetchall()
        raise psycopg.Rollback()
    return out


def refused_as_user(conn, user: str, sql: str) -> str | None:
    try:
        rows_as_user(conn, user, sql)
    except psycopg.Error as refused:
        return f"{refused.sqlstate} {str(refused).splitlines()[0][:100]}"
    return None


# =============================================================================
# item 6: the seams handed to A1, D2 and C0
# =============================================================================
def check_seams(conn) -> str:
    """`infrx/state/credit_schema.py` is the live catalog: every seam's signature, result
    columns (names, types, order) and callers; every page view's columns; every CREDIT
    pin column exists on `infrx.jobs`."""
    from infrx.state import credit_schema
    problems = []
    for signature, (callers, columns) in credit_schema.SEAMS.items():
        row = conn.execute("""
            select p.oid, array(select a.name || ':' || format_type(a.typ, null)
                                from unnest(p.proargnames, p.proallargtypes, p.proargmodes)
                                  with ordinality as a(name, typ, mode, n)
                                where a.mode = 't' order by a.n)
            from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname || '.' || p.proname
                  || regexp_replace(p.oid::regprocedure::text, '^[^(]*', '') = %s""",
            (signature.replace(" ", ""),)).fetchone()
        if row is None:
            problems.append(f"{signature} does not exist")
            continue
        declared = [f"{n}:{t}" for n, t in columns]
        if list(row[1]) != declared:
            problems.append(f"{signature} returns {row[1]}, the map says {declared}")
        for role in ("anon", "authenticated", "service_role"):
            may = conn.execute("select has_function_privilege(%s, %s, 'execute')",
                               (role, row[0])).fetchone()[0]
            if may != (role in callers):
                problems.append(f"{signature}: {role} execute={may}")
    for view, columns in credit_schema.VIEWS.items():
        got = [c for c, in conn.execute(
            "select attname from pg_attribute where attrelid = %s::regclass and attnum > 0 "
            "order by attnum", (view,)).fetchall()]
        if got != list(columns):
            problems.append(f"{view} columns {got}, the map says {list(columns)}")
    jobs = {c for c, in conn.execute("select attname from pg_attribute where attrelid = "
                                     "'infrx.jobs'::regclass and attnum > 0").fetchall()}
    missing = [c for c in credit_schema.CREDIT_JOB_PINS if c not in jobs]
    if missing:
        problems.append(f"infrx.jobs lacks the pins {missing}")
    assert not problems, "the seam map and the database disagree:\n  " + "\n  ".join(problems)
    return (f"{len(credit_schema.SEAMS)} seams and {len(credit_schema.VIEWS)} pages match "
            f"credit_schema.py")


# =============================================================================
# query plans at realistic tenant size
# =============================================================================
TENANT_ROWS = 100_000


def seed_credit_volume(conn, rows: int = TENANT_ROWS) -> None:
    """`rows` CREDIT ledger rows in each of two individuals' wallets, one second apart,
    through the real ledger trigger (so the totals stay reconciled). Committed in batches,
    as a wallet's history is: one transaction updating one wallet row 10^5 times builds a
    version chain nothing can prune, which is quadratic and is not how a ledger grows."""
    batch = 500
    for user in (CONSUMER_1, CONSUMER_2):
        wallet = wallet_of(conn, user)
        for start in range(1, rows + 1, batch):
            conn.execute("""
                insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount,
                                                 operation_id, actor, created_at)
                select %s, 'consumer', 'operator_adjustment',
                       case when i %% 2 = 1 then 1 else -1 end, gen_random_uuid(), 'ops',
                       now() - make_interval(secs => i)
                from generate_series(%s::int, %s::int) as g(i)""",
                (wallet, start, min(start + batch - 1, rows)))
    conn.execute("analyze")


def _plan(conn, sql: str, params=None, analyze: bool = False) -> str:
    opts = "analyze, timing off, summary on" if analyze else "costs off"
    return "\n".join(r for r, in conn.execute(f"explain ({opts}) {sql}", params).fetchall())


def check_credit_plans(conn) -> str:
    """The new keyset reads at TENANT_ROWS rows per tenant: the ledger page and its next
    page are one ordered index range (no sequential scan, no sort); through the barrier
    view the wallet qual reaches the base relation (D1's limit - the page sorts the one
    tenant's rows - and no worse)."""
    w1 = wallet_of(conn, CONSUMER_1)
    n, = conn.execute("select count(*) from infrx.credit_ledger where wallet_id = %s",
                      (w1,)).fetchone()
    assert n >= TENANT_ROWS, f"only {n} rows in the tenant"
    last = conn.execute("select created_at, entry_id from infrx.credit_ledger "
                        "where wallet_id = %s order by created_at desc, entry_id desc "
                        "offset 99 limit 1", (w1,)).fetchone()
    base = ("select entry_id, created_at, amount from infrx.credit_ledger where wallet_id = %s "
            "{extra} order by created_at desc, entry_id desc limit 100")
    problems, timings = [], []
    for label, sql, params in (
        ("first page", base.format(extra=""), (w1,)),
        ("keyset next page", base.format(extra="and (created_at, entry_id) < (%s, %s)"),
         (w1, *last)),
    ):
        plan = _plan(conn, sql, params)
        if "credit_ledger_wallet_created_idx" not in plan or "Seq Scan" in plan \
                or "Sort" in plan:
            problems.append(f"{label}:\n{plan}")
        timings.append(f"{label} {_plan(conn, sql, params, True).splitlines()[-1].strip()}")
    view = ("select entry_id, created_at, amount from public.console_credit_ledger "
            "where wallet_id = %s order by created_at desc, entry_id desc limit 100")
    plan = _plan(conn, view, (w1,))
    scan = [line for line in plan.splitlines() if "credit_ledger" in line and "Scan" in line]
    if not scan or "Seq Scan" in scan[0] or "wallet_id" not in plan.split(scan[0], 1)[1][:300]:
        problems.append(f"barrier view did not push the wallet qual down:\n{plan}")
    timings.append(f"view page {_plan(conn, view, (w1,), True).splitlines()[-1].strip()}")
    assert not problems, "CREDIT keyset reads are not index-served:\n" + "\n".join(problems)
    return f"{n} rows/tenant: " + "; ".join(timings)


def check_credit_leaky_probe(conn) -> str:
    """R59-5 for the CREDIT pages: a caller's cheap stable function in the WHERE clause
    sees only the caller's own wallet rows (the probe that broke D1's first views), and
    both views are declared `security_barrier`."""
    seen: list[str] = []
    conn.execute("""
        create or replace function public._d1r_probe(p_value text) returns boolean
        language plpgsql stable cost 0.0000001 as $$
        begin raise notice 'D1RPROBE %', coalesce(p_value, '-'); return true; end $$;
        grant execute on function public._d1r_probe(text) to authenticated;""")

    def collect(diagnostic) -> None:
        message = getattr(diagnostic, "message_primary", "") or ""
        if message.startswith("D1RPROBE "):
            seen.append(message.removeprefix("D1RPROBE "))

    conn.add_notice_handler(collect)
    try:
        for view in ("public.console_credit_wallets", "public.console_credit_ledger"):
            rows_as(conn, "consumer_2", f"select count(*) from {view} "
                                        f"where public._d1r_probe(wallet_id::text)")
    finally:
        conn.remove_notice_handler(collect)
        conn.execute("drop function if exists public._d1r_probe(text)")
    mine = {wallet_of(conn, CONSUMER_2), "-"}
    assert seen, "the probe was never evaluated, so this check proves nothing"
    leaked = sorted(set(seen) - mine)
    assert not leaked, f"a leaky function saw other individuals' wallets: {leaked[:5]}"
    for view in ("public.console_credit_wallets", "public.console_credit_ledger"):
        assert conn.execute("select coalesce(array_to_string(reloptions, ','), '') like "
                            "'%%security_barrier=true%%' from pg_class where oid = %s::regclass",
                            (view,)).fetchone()[0], f"{view} is not security_barrier"
    return f"2 CREDIT views probed ({len(seen)} evaluations); nothing foreign seen"


# =============================================================================
# 0009: the headless operator seams (coordinator ruling on the G6B handback)
# =============================================================================
def check_operator_seams(conn) -> str:
    """AuditAction extended and idempotent; key audience/scope/revocation rules; one
    audited operator key from a hash; verified_user; audited, replayable suspension;
    UsageRecordV2-shaped usage and per-regime holds - all as the platform role."""
    o1, w1 = personal_org(conn, CONSUMER_1), wallet_of(conn, CONSUMER_1)
    key = ("insert into public.api_keys (org_id, created_by, name, prefix, key_hash, audience, "
           "user_id, provider_org_id, endpoint_id) values ")
    audit = ("insert into infrx.audit_entries (id, actor_principal, action, reason, "
             "idempotency_key) values ")
    n = _all_refused(conn, (
        ("an audit action outside the closed list",
         audit + "(gen_random_uuid(), 'ops', 'admin_bogus', 'r', null)"),
        ("one idempotency key on two audit rows",
         audit + "(gen_random_uuid(), 'ops', 'admin_adjust', 'r', 'k1'), "
                 "(gen_random_uuid(), 'ops', 'admin_reconcile', 'r', 'k1')"),
        ("a provider_dev key with no endpoint scope",
         key + f"('{o1}', null, 'p', 'sk-infrx-p0000001', 'hash-p1', 'provider_dev', null, "
               f"'{NEMO}', null)"),
        ("a provider_dev key scoped to another provider's endpoint",
         key + f"('{o1}', null, 'p', 'sk-infrx-p0000002', 'hash-p2', 'provider_dev', null, "
               f"'{NEMO}', '{OTHER_ENDPOINT}')"),
        ("a consumer key scoped to an endpoint",
         key + f"('{o1}', '{CONSUMER_1}', 'c', 'sk-infrx-c0000001', 'hash-c1', 'consumer', "
               f"null, '{NEMO}', '{DEV_ENDPOINT}')"),
        ("a consumer key that names no individual",
         key + f"('{o1}', null, 'c', 'sk-infrx-c0000002', 'hash-c2', 'consumer', null, null, "
               f"null)"),
        ("an unknown audience",
         key + f"('{o1}', '{CONSUMER_1}', 'c', 'sk-infrx-c0000003', 'hash-c3', 'admin', null, "
               f"null, null)"),
        ("changing a key's audience",
         f"update public.api_keys set audience = 'operator' where key_hash = 'hash-a'"),
        ("un-revoking a key",
         f"update public.api_keys set revoked_at = now() where key_hash = 'hash-a'; "
         f"update public.api_keys set revoked_at = null where key_hash = 'hash-a'"),
        ("a second active operator key",
         f"select infrx.bootstrap_operator_key('{o1}', 'ops', 'sk-infrx-o1', '{'1' * 64}', "
         f"'ops', 'bootstrap'); select infrx.bootstrap_operator_key('{o1}', 'ops', "
         f"'sk-infrx-o2', '{'2' * 64}', 'ops', 'bootstrap')"),
        ("bootstrapping the operator key onto a consumer key's hash",
         key + f"('{o1}', '{CONSUMER_1}', 'c', 'sk-infrx-c0000009', '{'3' * 64}', 'consumer', "
               f"null, null, null); select infrx.bootstrap_operator_key('{o1}', 'ops', "
               f"'sk-infrx-o3', '{'3' * 64}', 'ops', 'bootstrap')"),
        ("a plaintext key given to the bootstrap",
         f"select infrx.bootstrap_operator_key('{o1}', 'ops', 'sk-infrx-o1', "
         f"'sk-infrx-secret', 'ops', 'bootstrap')"),
        ("a suspension with a free-text reason code",
         f"select infrx.set_suspension('{o1}', true, 'because', 'ops', 'r', 'sus-x')"),
    ), "operator seams")
    m = _all_accepted(conn, (
        ("an extended audit action", audit + "(gen_random_uuid(), 'ops', 'admin_adjust', 'r', "
                                             "'k2')"),
        ("a provider_dev key scoped to its provider's dev endpoint",
         key + f"('{o1}', null, 'p', 'sk-infrx-p0000003', 'hash-p3', 'provider_dev', null, "
               f"'{NEMO}', '{DEV_ENDPOINT}')"),
    ), "operator seam controls")
    with conn.transaction():
        # The deployed console's own insert: the individual is its creator.
        conn.execute(checks._jwt(CONSUMER_1))
        conn.execute("insert into public.api_keys (org_id, created_by, name, prefix, key_hash) "
                     "values (%s, %s, 'mine', 'sk-infrx-mine0001', 'hash-mine')",
                     (o1, CONSUMER_1))
        conn.execute("reset role")
        row = conn.execute("select audience, user_id from public.api_keys "
                           "where key_hash = 'hash-mine'").fetchone()
        assert row == ("consumer", uuid.UUID(CONSUMER_1)), f"console key identity: {row}"
        conn.execute(checks.SESSIONS["service"])
        first = conn.execute("select infrx.bootstrap_operator_key(%s, 'ops', 'sk-infrx-op', "
                             "%s, 'ops@infrx', 'bootstrap')", (o1, "a" * 64)).fetchone()[0]
        again = conn.execute("select infrx.bootstrap_operator_key(%s, 'ops', 'sk-infrx-op', "
                             "%s, 'ops@infrx', 'bootstrap')", (o1, "a" * 64)).fetchone()[0]
        assert first == again, "the operator bootstrap is not idempotent"
        found = conn.execute("select audience, org_suspended from infrx.key_by_hash(%s)",
                             ("a" * 64,)).fetchall()
        assert found == [("operator", False)], found
        kid = conn.execute("select id from public.api_keys where key_hash = 'hash-mine'"
                           ).fetchone()[0]
        t1 = conn.execute("select infrx.revoke_key(%s, 'ops', 'leak', 'rev-1')", (kid,)).fetchone()
        t2 = conn.execute("select infrx.revoke_key(%s, 'ops', 'leak', 'rev-2')", (kid,)).fetchone()
        assert t1 == t2 and t1[0] is not None, f"revocation is not one-way/idempotent: {t1} {t2}"
        s1 = conn.execute("select infrx.set_suspension(%s, true, 'abuse', 'ops', 'r', 'sus-1')",
                          (o1,)).fetchone()
        try:
            with conn.transaction():
                s2 = conn.execute("select infrx.set_suspension(%s, false, 'abuse', 'ops', 'r', "
                                  "'sus-1')", (o1,)).fetchone()
        except psycopg.Error as raised:
            raise AssertionError(f"a replayed suspension raised: {raised}") from None
        assert s1 == s2 == (True,), f"a replayed suspension acted twice: {s1} {s2}"
        audits = conn.execute("select action, count(*) from infrx.audit_entries group by 1 "
                              "order by 1").fetchall()
        assert ("admin_key_issue", 1) in audits and ("admin_key_revoke", 1) in audits \
            and ("admin_set_suspension", 1) in audits, f"audit trail: {audits}"
        replay = conn.execute("select action from infrx.audit_by_idempotency_key('sus-1')"
                              ).fetchall()
        assert replay == [("admin_set_suspension",)], replay
        raise psycopg.Rollback()
    unverified = conn.execute("select * from infrx.verified_user(%s)", (UNGRANTED,)).fetchone()
    assert unverified[2] is None and str(unverified[1]) == personal_org(conn, UNGRANTED), \
        f"an unconfirmed user reads as verified: {unverified}"
    # GoTrue's column: the shim has it; the bare supabase/postgres image's auth.users does
    # not (GoTrue adds it on a hosted project), so the verified path runs where it exists.
    verified_path = "not run (auth.users has no email_confirmed_at on this image)"
    if conn.execute("select count(*) from information_schema.columns where table_schema = "
                    "'auth' and table_name = 'users' and column_name = 'email_confirmed_at'"
                    ).fetchone()[0]:
        with conn.transaction():
            conn.execute("update auth.users set email_confirmed_at = '2026-09-22T12:00:00Z' "
                         "where id = %s", (UNGRANTED,))
            verified = conn.execute("select * from infrx.verified_user(%s)", (UNGRANTED,)
                                    ).fetchone()
            raise psycopg.Rollback()
        assert verified[2] and verified[2].startswith("email_confirmed_at/"), verified
        verified_path = "verified path checked"
    with conn.transaction():
        conn.execute(_admission_cases(conn)[1][0][1])       # a settled CREDIT request
        conn.execute(credit_job("5c000000-0000-4000-8000-0000000000c2", "job_h", o1, w1) + "; "
                     + hold("5c000000-0000-4000-8000-0000000000c2", o1, w1))
        usage = conn.execute("select accounting_regime, unit, charged_amount, rate_card_version "
                             "from infrx.usage_records(%s)", (o1,)).fetchall()
        holds = conn.execute("select accounting_regime, unit, amount, state "
                             "from infrx.active_holds(%s)", (o1,)).fetchall()
        raise psycopg.Rollback()
    assert usage == [("credit", "CREDIT", "9.97600000", CARD)], f"usage records: {usage}"
    assert holds == [("credit", "CREDIT", "10.01440000", "held")], f"holds: {holds}"
    legacy = conn.execute("select accounting_regime, unit, charged_amount from "
                          "infrx.usage_records(%s, null, null, 1)", (checks.ORG_A,)).fetchall()
    assert len(legacy) == 1 and legacy[0][:2] == ("legacy_usd", "USD"), legacy
    return (f"{n} refusals, {m} controls; bootstrap/revoke/suspension idempotent and audited; "
            f"verified_user unverified path, {verified_path}; usage and holds per regime")
