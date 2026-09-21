"""Migration runner, seeded fixtures, the DUR-RLS role matrix and the test-only clock.

Three things live here because they are one story: the schema the console ships, the rows a
role matrix needs to be meaningful, and the roles themselves. A role matrix over an empty
database passes trivially, which is the failure mode this file exists to avoid.

The matrix is deliberately small and will grow with D1 (`ROLE_MATRIX` is a list of
`Check`s; adding a relation means adding rows, not rewriting a runner).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from random import Random

import harness

# The only SQL roles a check may assume; `set local role` takes a literal, so the set is
# closed here rather than interpolated from a caller.
SQL_ROLES = ("anon", "authenticated", "service_role", "postgres")

# Test-only clock. No migration creates this schema, so a deployed database cannot have
# the function and no deployed process can move time (08 §10 "D1: four things", item 3).
# `test_harness.py` greps the migrations to keep that true.
CLOCK_SCHEMA = "infrx_e2_test"
CLOCK_GUC = "infrx_e2.clock_offset_s"


class MigrationError(RuntimeError):
    pass


# --------------------------------------------------------------------- migrations

def migration_files() -> list[Path]:
    files = sorted(harness.MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise MigrationError(f"no migrations under {harness.MIGRATIONS_DIR}")
    return files


def migration_digests() -> list[tuple[str, str]]:
    """(name, sha256) so evidence records which bytes were applied, not which names."""
    return [(path.name, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in migration_files()]


def is_fresh(conn) -> bool:
    return conn.execute("select to_regclass('public.organizations') is null").fetchone()[0]


def apply_migrations(conn, *, require_fresh: bool = True) -> list[tuple[str, str]]:
    """Apply every migration in filename order, each in its own transaction.

    `require_fresh` is on by default because a migration test that silently ran against an
    already-migrated database proves nothing (08 §10: "a task-local database that does not
    resemble production is worse than none, because it makes the migration test pass").
    """
    if require_fresh and not is_fresh(conn):
        raise MigrationError("database already has public.organizations; expected a fresh one")
    applied = []
    for path in migration_files():
        with conn.transaction():
            conn.execute(path.read_text())
        applied.append((path.name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return applied


def install_test_clock(conn) -> str:
    """A movable database clock, test-only by construction.

    R7/§conformance: an adapter must read the *database* clock inside its transaction, and
    every conformance case drives `harness.clock`. Both hold only if the database's notion
    of now is a settable offset that production cannot have - so it lives in a schema no
    migration creates, keyed off a custom GUC nothing in production sets.
    """
    conn.execute(f"create schema if not exists {CLOCK_SCHEMA}")
    conn.execute(f"""
        create or replace function {CLOCK_SCHEMA}.now() returns timestamptz
        language sql stable as $$
          select now() + (coalesce(nullif(current_setting('{CLOCK_GUC}', true), ''), '0')
                          ::double precision * interval '1 second')
        $$""")
    return f"{CLOCK_SCHEMA}.now()"


def set_clock_offset(conn, seconds: float, *, local: bool = True) -> None:
    """Move database time for this transaction (or this session with local=False)."""
    conn.execute("select set_config(%s, %s, %s)", (CLOCK_GUC, str(float(seconds)), local))


# --------------------------------------------------------------------- fixtures

@dataclass
class Principal:
    handle: str
    user_id: uuid.UUID
    email: str
    is_operator: bool = False
    personal_org: uuid.UUID | None = None


@dataclass
class Fixtures:
    """Everything a check may name. Deterministic in `seed`: the same integer gives the
    same uuids, so a failing case is reproducible from the seed alone."""

    seed: int
    principals: dict[str, Principal] = field(default_factory=dict)
    orgs: dict[str, uuid.UUID] = field(default_factory=dict)
    keys: dict[str, uuid.UUID] = field(default_factory=dict)
    ledger_totals: dict[str, Decimal] = field(default_factory=dict)
    usage_rows: dict[str, int] = field(default_factory=dict)

    def org(self, name: str) -> uuid.UUID:
        return self.orgs[name]

    def user(self, handle: str) -> uuid.UUID:
        return self.principals[handle].user_id

    def to_dict(self) -> dict:
        return {"seed": self.seed,
                "principals": {handle: {"user_id": str(p.user_id), "email": p.email,
                                        "is_operator": p.is_operator,
                                        "personal_org": str(p.personal_org)}
                               for handle, p in self.principals.items()},
                "orgs": {name: str(value) for name, value in self.orgs.items()},
                "keys": {name: str(value) for name, value in self.keys.items()},
                "ledger_totals": {name: str(value) for name, value in self.ledger_totals.items()},
                "usage_rows": dict(self.usage_rows)}

    @classmethod
    def from_dict(cls, raw: dict) -> "Fixtures":
        return cls(
            seed=int(raw["seed"]),
            principals={handle: Principal(handle, uuid.UUID(p["user_id"]), p["email"],
                                          bool(p["is_operator"]), uuid.UUID(p["personal_org"]))
                        for handle, p in raw["principals"].items()},
            orgs={name: uuid.UUID(value) for name, value in raw["orgs"].items()},
            keys={name: uuid.UUID(value) for name, value in raw["keys"].items()},
            ledger_totals={name: Decimal(value) for name, value in raw["ledger_totals"].items()},
            usage_rows=dict(raw["usage_rows"]))


def _uuid(rng: Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128), version=4)


PEOPLE = (
    # handle, email local part, operator
    ("owner_alpha", "owner-alpha", False),
    ("member_alpha", "member-alpha", False),
    ("owner_beta", "owner-beta", False),
    ("operator", "operator", True),
)


def seed_fixtures(conn, seed: int = 20260921) -> Fixtures:
    """Two tenants, one shared member, one platform operator, plus keys, usage and ledger.

    Shapes that exist so a check can fail: two organizations (cross-tenant denial), a
    member who is not an owner (the api_keys owner policies), a user who belongs to two
    organizations (so "my orgs" is not "one org"), an operator who is a member of neither
    (so operator reach is not membership in disguise), and a ledger whose exact total is
    asserted rather than approximated.
    """
    rng = Random(seed)
    fixtures = Fixtures(seed=seed)
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)

    # auth.users is GoTrue's table; the 0001 trigger turns each insert into a profile, a
    # personal organization and an owner membership. Seeding through it exercises the
    # trigger instead of writing the rows it is supposed to write.
    for handle, local, is_operator in PEOPLE:
        user_id = _uuid(rng)
        email = f"{local}@infrx-e2.invalid"
        conn.execute(
            "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
            (user_id, email, json.dumps({"full_name": handle.replace("_", " ").title()})))
        personal = conn.execute(
            "select org_id from public.org_members where user_id = %s", (user_id,)).fetchone()
        if personal is None:
            raise MigrationError(f"the signup trigger created no organization for {handle}")
        if is_operator:
            conn.execute("update public.profiles set is_operator = true where id = %s", (user_id,))
        fixtures.principals[handle] = Principal(handle, user_id, email, is_operator, personal[0])

    fixtures.orgs["alpha"] = fixtures.principals["owner_alpha"].personal_org
    fixtures.orgs["beta"] = fixtures.principals["owner_beta"].personal_org
    fixtures.orgs["operator_personal"] = fixtures.principals["operator"].personal_org
    fixtures.orgs["member_personal"] = fixtures.principals["member_alpha"].personal_org
    conn.execute("update public.organizations set name = %s, slug = %s where id = %s",
                 ("Alpha", "alpha-e2", fixtures.orgs["alpha"]))
    conn.execute("update public.organizations set name = %s, slug = %s where id = %s",
                 ("Beta", "beta-e2", fixtures.orgs["beta"]))

    # member_alpha joins alpha as a plain member: two organizations, one of them not theirs
    # to administer.
    conn.execute("insert into public.org_members (org_id, user_id, role) values (%s, %s, 'member')",
                 (fixtures.orgs["alpha"], fixtures.user("member_alpha")))

    model_id = conn.execute("select id from public.models order by sort, id limit 1").fetchone()
    if model_id is None:
        raise MigrationError("no models seeded; 0002_seed_models.sql did not run")
    model_id = model_id[0]

    for org_name, creator in (("alpha", "owner_alpha"), ("beta", "owner_beta")):
        key_id = _uuid(rng)
        secret = f"e2-{org_name}-{rng.getrandbits(64):016x}"
        conn.execute(
            "insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash)"
            " values (%s, %s, %s, %s, %s, %s)",
            (key_id, fixtures.org(org_name), fixtures.user(creator), f"{org_name} seed key",
             "sk-infrx-" + secret[:8], hashlib.sha256(secret.encode()).hexdigest()))
        fixtures.keys[org_name] = key_id

        rows = 0
        for index in range(12 if org_name == "alpha" else 5):
            conn.execute(
                "insert into public.usage_events (id, org_id, api_key_id, model_id, status,"
                " stream, prompt_tokens, completion_tokens, video_seconds, ttft_ms, latency_ms,"
                " cached, cost_usd, created_at)"
                " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (_uuid(rng), fixtures.org(org_name), key_id, model_id,
                 200 if index % 5 else 429, bool(index % 2), 1200 + index * 7, 64 + index,
                 Decimal("12.500"), 800 + index, 4200 + index * 3, index % 3 == 0,
                 Decimal("0.00031250") * (index + 1), base + timedelta(hours=index)))
            rows += 1
        fixtures.usage_rows[org_name] = rows

        # Exact decimals: a grant, a usage debit and a negative adjustment. 01 requires
        # decimal arithmetic, so the assertion is on an exact Decimal, never a float.
        deltas = [(Decimal("25.000000"), "grant"), (Decimal("-1.250000"), "usage"),
                  (Decimal("-0.003125"), "adjustment")]
        for delta, kind in deltas:
            conn.execute(
                "insert into public.credit_ledger (id, org_id, delta_usd, kind, reason, ref,"
                " created_by, created_at) values (%s,%s,%s,%s,%s,%s,%s,%s)",
                (_uuid(rng), fixtures.org(org_name), delta, kind, f"e2 seed {kind}",
                 f"e2:{org_name}:{kind}", fixtures.user(creator), base))
        fixtures.ledger_totals[org_name] = sum(delta for delta, _ in deltas)
    return fixtures


def balances(conn) -> dict[str, Decimal]:
    """org slug -> exact ledger total, read as the service role would.

    DUR-RLS: "migrations preserve existing balances". This is the snapshot a migration
    upgrade test compares, and it is exact decimals so a float round-trip would show.
    """
    rows = conn.execute(
        "select o.slug, coalesce(sum(l.delta_usd), 0) from public.organizations o"
        " left join public.credit_ledger l on l.org_id = o.id group by o.slug order by o.slug"
    ).fetchall()
    return {slug: Decimal(str(total)) for slug, total in rows}


# --------------------------------------------------------------------- role matrix

@dataclass(frozen=True)
class Check:
    """One row of the DUR-RLS matrix.

    `expect` is one of:
      ("rows", n)          the statement returns exactly n rows
      ("rowcount", n)      the statement affects exactly n rows (a policy filtered update)
      ("error", sqlstate)  the statement is refused with this SQLSTATE
      ("value", x)         a single scalar equals x
    """

    case: str
    role: str
    principal: str | None
    sql: str
    expect: tuple
    why: str
    params: tuple = ()


def _sql(fixtures: Fixtures, template: str) -> tuple[str, tuple]:
    """Substitute fixture ids by name. Ids are uuids the harness generated, never caller
    text, so formatting them is not an injection surface - but they still go in as
    parameters wherever a statement takes them."""
    return template.format(
        alpha=f"'{fixtures.org('alpha')}'::uuid", beta=f"'{fixtures.org('beta')}'::uuid",
        alpha_key=f"'{fixtures.keys['alpha']}'::uuid",
        beta_key=f"'{fixtures.keys['beta']}'::uuid",
        owner_alpha=f"'{fixtures.user('owner_alpha')}'::uuid",
        member_alpha=f"'{fixtures.user('member_alpha')}'::uuid",
        owner_beta=f"'{fixtures.user('owner_beta')}'::uuid",
        operator=f"'{fixtures.user('operator')}'::uuid",
    ), ()


PERMISSION_DENIED = "42501"


def role_matrix(fixtures: Fixtures) -> list[Check]:
    """The DUR-RLS matrix: "member/browser/operator/service roles attack protected
    columns/RPC" with "tenant/role enforcement in DB as well as route"."""
    alpha_keys, beta_keys = 1, 1
    return [
        # -------- anon: a browser with no session reaches nothing
        Check("E2-RLS-01", "anon", None, "select count(*) from public.organizations",
              ("value", 0), "an unauthenticated browser sees no organization"),
        Check("E2-RLS-02", "anon", None, "select count(*) from public.api_keys",
              ("value", 0), "no key metadata leaks to anon"),
        Check("E2-RLS-03", "anon", None, "select count(*) from public.credit_ledger",
              ("value", 0), "no ledger row leaks to anon"),
        Check("E2-RLS-04", "anon", None, "select count(*) from public.models",
              ("value", 0), "even the catalog is authenticated-only"),

        # -------- member of alpha: own tenants only
        Check("E2-RLS-10", "authenticated", "member_alpha",
              "select count(*) from public.organizations where id = {beta}",
              ("value", 0), "a member of alpha cannot see beta's organization row"),
        Check("E2-RLS-11", "authenticated", "member_alpha",
              "select count(*) from public.api_keys where org_id = {beta}",
              ("value", 0), "cross-tenant key metadata is invisible"),
        Check("E2-RLS-12", "authenticated", "member_alpha",
              "select count(*) from public.usage_events where org_id = {beta}",
              ("value", 0), "cross-tenant usage is invisible"),
        Check("E2-RLS-13", "authenticated", "member_alpha",
              "select count(*) from public.credit_ledger where org_id = {beta}",
              ("value", 0), "cross-tenant ledger is invisible"),
        Check("E2-RLS-14", "authenticated", "member_alpha",
              "select count(*) from public.api_keys where org_id = {alpha}",
              ("value", alpha_keys), "a member still reads their own tenant's keys"),

        # -------- writes a member must not have
        Check("E2-RLS-20", "authenticated", "member_alpha",
              "insert into public.api_keys (org_id, created_by, name, prefix, key_hash)"
              " values ({alpha}, {member_alpha}, 'no', 'sk-infrx-x', 'deadbeef')",
              ("error", PERMISSION_DENIED), "only an owner may mint a key"),
        Check("E2-RLS-21", "authenticated", "member_alpha",
              "update public.api_keys set revoked_at = now() where org_id = {alpha}",
              ("rowcount", 0), "a member's revoke is filtered to zero rows by the owner policy"),
        Check("E2-RLS-22", "authenticated", "member_alpha",
              "update public.profiles set is_operator = true where id = {member_alpha}",
              ("error", PERMISSION_DENIED),
              "the column grant, not RLS, is what stops self-promotion to operator"),
        Check("E2-RLS-23", "authenticated", "member_alpha",
              "update public.profiles set full_name = 'Renamed' where id = {member_alpha}",
              ("rowcount", 1), "a member may still rename themselves"),
        Check("E2-RLS-24", "authenticated", "member_alpha",
              "update public.profiles set full_name = 'Hijacked' where id = {owner_beta}",
              ("rowcount", 0), "another tenant's profile is not writable"),
        Check("E2-RLS-25", "authenticated", "member_alpha",
              "insert into public.credit_ledger (org_id, delta_usd, kind) values ({alpha}, 1, 'grant')",
              ("error", PERMISSION_DENIED), "credit is service-role only: no self-granting"),
        Check("E2-RLS-26", "authenticated", "member_alpha",
              "insert into public.usage_events (id, org_id, model_id, status)"
              " values (gen_random_uuid(), {alpha}, 'nemostation/marlin-2b', 200)",
              ("error", PERMISSION_DENIED), "usage is service-role only: no self-metering"),
        Check("E2-RLS-27", "authenticated", "member_alpha",
              "delete from public.api_keys where org_id = {alpha}",
              ("error", PERMISSION_DENIED), "keys are revoked, never deleted"),
        Check("E2-RLS-28", "authenticated", "member_alpha",
              "insert into public.org_members (org_id, user_id, role)"
              " values ({beta}, {member_alpha}, 'owner')",
              ("error", PERMISSION_DENIED), "nobody joins a tenant by writing the table"),

        # -------- owner of alpha
        Check("E2-RLS-30", "authenticated", "owner_alpha",
              "insert into public.api_keys (org_id, created_by, name, prefix, key_hash)"
              " values ({alpha}, {owner_alpha}, 'owner key', 'sk-infrx-o', 'ownerhash')",
              ("rowcount", 1), "an owner mints a key for their own tenant"),
        Check("E2-RLS-31", "authenticated", "owner_alpha",
              "insert into public.api_keys (org_id, created_by, name, prefix, key_hash)"
              " values ({beta}, {owner_alpha}, 'cross', 'sk-infrx-c', 'crosshash')",
              ("error", PERMISSION_DENIED), "an owner of alpha is nobody in beta"),
        Check("E2-RLS-32", "authenticated", "owner_alpha",
              "insert into public.api_keys (org_id, created_by, name, prefix, key_hash)"
              " values ({alpha}, {member_alpha}, 'forged', 'sk-infrx-f', 'forgedhash')",
              ("error", PERMISSION_DENIED),
              "created_by must be the caller: no forging another member's authorship"),
        Check("E2-RLS-33", "authenticated", "owner_alpha",
              "update public.api_keys set revoked_at = now() where id = {alpha_key}",
              ("rowcount", 1), "an owner revokes their own key"),
        Check("E2-RLS-34", "authenticated", "owner_alpha",
              "update public.api_keys set revoked_at = now() where id = {beta_key}",
              ("rowcount", 0), "and cannot revoke another tenant's"),

        # -------- RPC guards (the reporting functions are security invoker + explicit check)
        Check("E2-RLS-40", "authenticated", "member_alpha",
              "select public.org_balance({beta})",
              ("error", PERMISSION_DENIED), "the balance RPC refuses a foreign org outright"),
        Check("E2-RLS-41", "authenticated", "member_alpha",
              "select public.org_balance({alpha})",
              ("value", Decimal("23.746875")), "and returns the exact seeded total for its own"),
        Check("E2-RLS-42", "authenticated", "member_alpha",
              "select count(*) from public.org_usage_summary({beta}, '2026-01-01Z', '2027-01-01Z')",
              ("error", PERMISSION_DENIED), "the usage RPC refuses a foreign org"),
        Check("E2-RLS-43", "authenticated", "member_alpha",
              "select requests from public.org_usage_summary({alpha}, '2026-01-01Z', '2027-01-01Z')",
              ("value", 12), "and counts exactly the seeded rows for its own"),
        Check("E2-RLS-44", "anon", None, "select public.org_balance({alpha})",
              ("error", PERMISSION_DENIED), "anon cannot reach the RPC either"),

        # -------- operator: platform-wide reads, still not a free write
        Check("E2-RLS-50", "authenticated", "operator",
              "select count(*) from public.organizations where id in ({alpha}, {beta})",
              ("value", 2), "an operator reads every tenant (R26: platform-wide in the pilot)"),
        Check("E2-RLS-51", "authenticated", "operator",
              "select count(*) from public.credit_ledger",
              ("value", 6), "including every ledger row"),
        Check("E2-RLS-52", "authenticated", "operator",
              "insert into public.credit_ledger (org_id, delta_usd, kind) values ({alpha}, 5, 'grant')",
              ("error", PERMISSION_DENIED),
              "an operator grant is a server action, not a table write (R34 audits it)"),
        Check("E2-RLS-53", "authenticated", "operator",
              "select public.org_balance({beta})",
              ("value", Decimal("23.746875")), "the RPC lets an operator read any tenant"),

        # -------- service role: RLS is bypassed, so the route is the only boundary
        Check("E2-RLS-60", "service_role", None,
              "select count(*) from public.organizations where id in ({alpha}, {beta})",
              ("value", 2),
              "service_role has BYPASSRLS: tenant safety for the gateway is route-side, "
              "which is why 06 says a service credential never justifies a caller-supplied org"),
        Check("E2-RLS-61", "service_role", None,
              "insert into public.usage_events (id, org_id, model_id, status)"
              " values (gen_random_uuid(), {beta}, 'nemostation/marlin-2b', 200)",
              ("rowcount", 1), "and can write metering for any tenant"),

        # -------- the claim IS the identity
        Check("E2-RLS-70", "authenticated", "owner_beta",
              "select count(*) from public.api_keys where org_id = {beta}",
              ("value", beta_keys),
              "auth.uid() is read from request.jwt.claim.sub, so whoever sets that claim IS "
              "the tenant: it must only ever be set from a verified JWT, never from input"),
    ]


def run_check(conn, check: Check, fixtures: Fixtures) -> dict:
    """Run one check in its own aborted transaction: nothing a check writes survives, so
    the matrix order cannot matter and a failure leaves no residue."""
    import psycopg
    if check.role not in SQL_ROLES:
        raise MigrationError(f"unknown role {check.role!r}")
    statement, params = _sql(fixtures, check.sql)
    observed: object
    outcome = "ok"
    try:
        with conn.transaction() as _tx:
            conn.execute(f"set local role {check.role}")
            if check.principal:
                principal = fixtures.principals[check.principal]
                conn.execute("select set_config('request.jwt.claim.sub', %s, true)",
                             (str(principal.user_id),))
                conn.execute("select set_config('request.jwt.claim.role', %s, true)",
                             (check.role,))
            cursor = conn.execute(statement, params)
            kind = check.expect[0]
            if kind == "value":
                row = cursor.fetchone()
                observed = None if row is None else row[0]
            elif kind == "rows":
                observed = len(cursor.fetchall())
            elif kind == "rowcount":
                observed = cursor.rowcount
            else:
                observed = "no error raised"
                outcome = "unexpected-success"
            raise _Rollback
    except _Rollback:
        pass
    except psycopg.errors.Error as exc:
        observed = exc.sqlstate
        outcome = "error"
    try:
        conn.execute("reset role")
    except psycopg.errors.Error:                 # a failed transaction already reset it
        conn.rollback()

    expected_kind, expected = check.expect
    if expected_kind == "error":
        passed = outcome == "error" and observed == expected
    else:
        passed = outcome == "ok" and _same(observed, expected)
    return {"case": check.case, "role": check.role, "principal": check.principal,
            "expected": f"{expected_kind}={expected}", "observed": observed,
            "outcome": outcome, "passed": passed, "why": check.why}


def _same(observed: object, expected: object) -> bool:
    if isinstance(expected, Decimal):
        return observed is not None and Decimal(str(observed)) == expected
    return observed == expected


class _Rollback(Exception):
    """Ends a check's transaction without committing. Not an error."""


def run_role_matrix(conn, fixtures: Fixtures) -> list[dict]:
    return [run_check(conn, check, fixtures) for check in role_matrix(fixtures)]
