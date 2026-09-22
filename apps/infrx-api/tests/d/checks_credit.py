"""D1R: the invariants the additive CREDIT / provider schema (0006 onward) claims.

Same contract as `checks.py`: each check raises `AssertionError` on failure and returns a
short summary on success, so `migration_mutants.py` can run it against a single-edit
mutant of a migration (R32/R40). Fixture identities come from `checks.py` where the D1
fixture already has them.
"""
from __future__ import annotations

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
    cols = checks._JOB_COLUMNS
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
}


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
        changed.append(f"{key}: {old!r} -> {new!r}")
    assert not changed, "D1R rewrote 0001-0005 schema:\n  " + "\n  ".join(changed[:30])
    # Named, because these are what the brief lists: the USD wallet trigger and the
    # narrow financial RPC grants still exist exactly as they were.
    for key in (("trigger", "public.credit_ledger", "credit_ledger_moves_wallet"),
                ("function", "infrx", "infrx.grant_credit(jsonb)"),
                ("function", "infrx", "infrx.admit(jsonb)"),
                ("function", "public", "public.org_wallet_summary(uuid)")):
        assert key in before and after.get(key) == before[key], f"{key} changed or vanished"
    return (f"{len(before)} objects of 0001-0005 unchanged "
            f"({len(ALLOWED_LEGACY_CHANGES)} named allowances)")


def check_old_regime_preserved(conn, before: dict) -> str:
    """USD history (ledger rows, sums, usage values), accepted old-regime jobs, their USD
    holds, the USD wallet summaries and the pilot usage rows are identical - to the
    digit - after D1R."""
    legacy = checks.capture_legacy(conn, before["legacy"]["org_a"], before["legacy"]["org_b"])
    assert legacy == before["legacy"], "USD history changed across D1R"
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

