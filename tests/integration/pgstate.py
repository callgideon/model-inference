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

# E2R item 2: E2's private `infrx_e2_test.now()` is GONE. D1's migrations shipped the real
# thing - `infrx.now()`, the function every durable decision reads (R7) - whose test-only
# offset lives in `infrx_test.clock`, installed by the fixture below and by no migration.
# Using E2's own clock after D1 merged would have measured a function nothing in the product
# calls. Two barriers keep it out of a deployed database and `test_harness.py` checks both:
# the fixture is not in `apps/app/supabase/migrations/`, and `infrx.now()` only consults the
# offset when `current_database() like 'infrx\_%'` (production is `postgres`).
CLOCK_SCHEMA = "infrx_test"
CLOCK_FUNCTION = "infrx.now()"
CLOCK_FIXTURE = harness.API_ROOT / "infrx" / "state" / "test_clock.sql"


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
    """Install D1's shared test clock fixture, which is the only thing that can move
    `infrx.now()` - and only in a task-local `infrx_<task>` database.

    R7: one function is "now" for every durable decision. The offset is a TABLE
    (`infrx_test.clock`), not a session GUC, because the conformance harness's hooks run on
    a different connection from the port operations. That has one consequence every caller
    must know and `probe_clock` measures: a move is COMMITTED, so it outlives the statement
    that made it and is undone only by rolling the transaction back or setting it to zero.
    """
    conn.execute(CLOCK_FIXTURE.read_text())
    return f"{CLOCK_FUNCTION} + {CLOCK_SCHEMA}.clock ({CLOCK_FIXTURE.name})"


def set_clock_offset(conn, seconds: float) -> None:
    """The absolute form: database time is wall time + `seconds` from here on."""
    conn.execute(f"select {CLOCK_SCHEMA}.set_offset(%s)", (float(seconds),))


def advance_clock(conn, seconds: float) -> object:
    """The relative form, matching `FakeClock.advance`. Returns what the function returns,
    which is NOT the moved time - see `probe_clock`."""
    return conn.execute(f"select {CLOCK_SCHEMA}.advance(%s)", (float(seconds),)).fetchone()[0]


def clock_delta_s(conn) -> float:
    """`infrx.now() - now()` in seconds: how far the test clock is from wall time."""
    return float(conn.execute(
        f"select extract(epoch from ({CLOCK_FUNCTION} - now()))").fetchone()[0])


def probe_clock(conn) -> dict:
    """Every measured fact about the shared clock, in one place so `run.py` and the suite
    cannot disagree about what it does.

    Three facts, each of which a caller gets wrong if it is not stated:

    * the offset really moves `infrx.now()`, the function the migrations default to;
    * `advance()` RETURNS the MOVED clock. It used to return the pre-move value (a SQL
      function reads `infrx.now()`, which is STABLE, in the UPDATE's own snapshot); D2
      rewrote it as plpgsql, one statement each, so `PgClock.advance` can use the answer.
      E3B phase 2 re-measured it: `advance_return_lag_s` is ~0, not 3600;
    * the offset is a committed row, not a transaction-local GUC: it survives its statement
      and a rolled-back transaction is what undoes it.
    """
    set_clock_offset(conn, 0.0)
    returned = advance_clock(conn, 3600.0)
    read_back, wall = conn.execute(f"select {CLOCK_FUNCTION}, now()").fetchone()
    moved = (read_back - wall).total_seconds()
    lag = (read_back - returned).total_seconds()
    set_clock_offset(conn, 0.0)
    at_rest = clock_delta_s(conn)
    try:
        with conn.transaction():
            advance_clock(conn, 1800.0)
            inside = clock_delta_s(conn)
            raise _Rollback
    except _Rollback:
        pass
    return {"function": CLOCK_FUNCTION, "offset_table": f"{CLOCK_SCHEMA}.clock",
            "fixture": str(CLOCK_FIXTURE.relative_to(harness.REPO_ROOT)),
            "moved_s": round(moved, 1), "advance_return_lag_s": round(lag, 1),
            "at_rest_s": round(at_rest, 1),
            "inside_rolled_back_tx_s": round(inside, 1),
            "after_rollback_s": round(clock_delta_s(conn), 1)}


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
    """Everything a check may name.

    **What is deterministic in `seed`, precisely** (r1 review B4 - the earlier blanket claim
    "the same seed gives the same uuids and rows" was partly false):

    * deterministic, minted here from `Random(seed)` in a fixed order: the four `auth.users`
      ids, the two api-key ids, the key secrets, and the usage-event row ids. `seeded_ids()`
      recomputes them from the seed alone, which is how the claim is checked.
    * **not** deterministic: organization ids and slugs. They come from `0001_init.sql`'s
      signup trigger, which calls `gen_random_uuid()` inside the database, so they differ
      between runs by construction. That is why they are carried in the state file rather
      than recomputed, and why a case names them through `Fixtures`.
    * row *contents* (counts, amounts, timestamps) are fixed constants, not random.
    """

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
TENANTS = (("alpha", "owner_alpha"), ("beta", "owner_beta"))
USAGE_ROWS = {"alpha": 12, "beta": 5}
# A grant, a usage debit and a negative adjustment. 01 requires decimal arithmetic, so every
# assertion on these is on an exact Decimal, never a float.
LEDGER_DELTAS = ((Decimal("25.000000"), "grant"), (Decimal("-1.250000"), "usage"),
                 (Decimal("-0.003125"), "adjustment"))
SEED_EPOCH = datetime(2026, 9, 1, tzinfo=timezone.utc)


def seeded_ids(seed: int = 20260921) -> dict:
    """Every id this harness mints, from the seed alone (r1 review B4).

    This is the ONE place the `Random(seed)` consumption order lives: `seed_fixtures` reads
    it instead of drawing from the generator itself, and `test_services.py` recomputes it to
    check the reproducibility claim. Two copies of a consumption order drift; one cannot.

    Organization ids are deliberately absent: `0001_init.sql`'s signup trigger mints them
    with `gen_random_uuid()` inside the database, so they are NOT a function of this seed.
    """
    rng = Random(seed)
    ids = {"users": {handle: _uuid(rng) for handle, _, _ in PEOPLE},
           "keys": {}, "secrets": {}, "usage": {}, "ledger": {}}
    for org_name, _creator in TENANTS:
        ids["keys"][org_name] = _uuid(rng)
        ids["secrets"][org_name] = f"e2-{org_name}-{rng.getrandbits(64):016x}"
        ids["usage"][org_name] = [_uuid(rng) for _ in range(USAGE_ROWS[org_name])]
        ids["ledger"][org_name] = [_uuid(rng) for _ in LEDGER_DELTAS]
    return ids


def seed_fixtures(conn, seed: int = 20260921) -> Fixtures:
    """Two tenants, one shared member, one platform operator, plus keys, usage and ledger.

    Shapes that exist so a check can fail: two organizations (cross-tenant denial), a
    member who is not an owner (the api_keys owner policies), a user who belongs to two
    organizations (so "my orgs" is not "one org"), an operator who is a member of neither
    (so operator reach is not membership in disguise), and a ledger whose exact total is
    asserted rather than approximated.
    """
    ids = seeded_ids(seed)
    fixtures = Fixtures(seed=seed)
    base = SEED_EPOCH

    # auth.users is GoTrue's table; the 0001 trigger turns each insert into a profile, a
    # personal organization and an owner membership. Seeding through it exercises the
    # trigger instead of writing the rows it is supposed to write.
    for handle, local, is_operator in PEOPLE:
        user_id = ids["users"][handle]
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
    # 0024 (C3A WR-C3A-4): a browser key INSERT needs a verified individual, so owner_alpha
    # is one: E2-RLS-30 stays a positive control and E2-RLS-20/31/32 stay refused by the
    # owner/tenant/authorship checks, not by verification. GoTrue's column is on the
    # template (`harness.GOTRUE_COLUMNS`).
    conn.execute("update auth.users set email_confirmed_at = now() where id = %s",
                 (fixtures.user("owner_alpha"),))

    model_id = conn.execute("select id from public.models order by sort, id limit 1").fetchone()
    if model_id is None:
        raise MigrationError("no models seeded; 0002_seed_models.sql did not run")
    model_id = model_id[0]

    for org_name, creator in TENANTS:
        key_id = ids["keys"][org_name]
        secret = ids["secrets"][org_name]
        conn.execute(
            "insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash)"
            " values (%s, %s, %s, %s, %s, %s)",
            (key_id, fixtures.org(org_name), fixtures.user(creator), f"{org_name} seed key",
             "sk-infrx-" + secret[:8], hashlib.sha256(secret.encode()).hexdigest()))
        fixtures.keys[org_name] = key_id

        for index, row_id in enumerate(ids["usage"][org_name]):
            conn.execute(
                "insert into public.usage_events (id, org_id, api_key_id, model_id, status,"
                " stream, prompt_tokens, completion_tokens, video_seconds, ttft_ms, latency_ms,"
                " cached, cost_usd, created_at)"
                " values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (row_id, fixtures.org(org_name), key_id, model_id,
                 200 if index % 5 else 429, bool(index % 2), 1200 + index * 7, 64 + index,
                 Decimal("12.500"), 800 + index, 4200 + index * 3, index % 3 == 0,
                 Decimal("0.00031250") * (index + 1), base + timedelta(hours=index)))
        fixtures.usage_rows[org_name] = USAGE_ROWS[org_name]

        for row_id, (delta, kind) in zip(ids["ledger"][org_name], LEDGER_DELTAS):
            conn.execute(
                "insert into public.credit_ledger (id, org_id, delta_usd, kind, reason, ref,"
                " created_by, created_at) values (%s,%s,%s,%s,%s,%s,%s,%s)",
                (row_id, fixtures.org(org_name), delta, kind, f"e2 seed {kind}",
                 f"e2:{org_name}:{kind}", fixtures.user(creator), base))
        fixtures.ledger_totals[org_name] = sum(delta for delta, _ in LEDGER_DELTAS)
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
    # For an ("error", "42501") case: a fragment the server's message must contain, so a
    # missing GRANT and an RLS/RPC refusal are not the same observation (r1 review).
    message_contains: str | None = None


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
        # -------- the premises the rest of the matrix rests on (r1 review R-b)
        Check("E2-RLS-05", "postgres", None,
              "select rolbypassrls from pg_roles where rolname = 'service_role'",
              ("value", True),
              "service_role really does bypass RLS - asserted, not assumed, because every "
              "claim about route-side tenant safety depends on it"),
        Check("E2-RLS-06", "postgres", None,
              "select bool_or(rolbypassrls) from pg_roles where rolname in ('anon','authenticated')",
              ("value", False), "and the two browser roles really do not"),
        Check("E2-RLS-07", "authenticated", "member_alpha", "select auth.uid() = {member_alpha}",
              ("value", True),
              "the impersonated principal is who the database thinks it is: a matrix where "
              "auth.uid() is NULL passes every deny-case vacuously"),
        Check("E2-RLS-08", "authenticated", None, "select auth.uid() is null",
              ("value", True), "and with no claim set there is no identity at all"),

        # -------- anon: a browser with no session reaches nothing
        # E2R item 2 (audit carryover 16): these four read 0 rows through RLS while only
        # 0001+0002 were applied. With the merged 0001-0005 they are REFUSED before any
        # policy runs: 0004 ruling R59-4 does `revoke all ... from anon, authenticated` on
        # every existing `public` relation and grants back only what the console needs, and
        # `anon` gets nothing at all. The distinction matters - "0 rows" is also what a
        # broken identity produces, while `42501 permission denied for table X` can only
        # come from the absent grant - so the message is part of the expectation and the
        # relation is named in it. Measured on the pinned image; not inferred from the SQL.
        Check("E2-RLS-01", "anon", None, "select count(*) from public.organizations",
              ("error", PERMISSION_DENIED),
              "an unauthenticated browser cannot even read the organizations table: after "
              "0004 anon holds no privilege on it, so the refusal is the missing grant",
              message_contains="permission denied for table organizations"),
        Check("E2-RLS-02", "anon", None, "select count(*) from public.api_keys",
              ("error", PERMISSION_DENIED), "no key metadata is reachable by anon at all",
              message_contains="permission denied for table api_keys"),
        Check("E2-RLS-03", "anon", None, "select count(*) from public.credit_ledger",
              ("error", PERMISSION_DENIED),
              "no ledger row is reachable by anon - and TRUNCATE went with the grant "
              "(0004: `truncate public.credit_ledger cascade` used to erase it)",
              message_contains="permission denied for table credit_ledger"),
        Check("E2-RLS-04", "anon", None, "select count(*) from public.models",
              ("error", PERMISSION_DENIED),
              "even the catalog is refused: a pre-login page that needs it must add "
              "`grant select on public.models to anon` and say so",
              message_contains="permission denied for table models"),

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
        Check("E2-RLS-15", "authenticated", "member_alpha",
              "select count(*) from public.org_members where org_id = {beta}",
              ("value", 0), "another tenant's membership rows are invisible "
              "(E2R review N2: `org_members_select using (true)` left the matrix green)"),

        # -------- writes a member must not have
        Check("E2-RLS-20", "authenticated", "member_alpha",
              "insert into public.api_keys (org_id, created_by, name, prefix, key_hash)"
              " values ({alpha}, {member_alpha}, 'no', 'sk-infrx-x', 'deadbeef')",
              ("error", PERMISSION_DENIED), "only an owner may mint a key",
              message_contains="violates row-level security policy"),
        Check("E2-RLS-21", "authenticated", "member_alpha",
              "update public.api_keys set revoked_at = now() where org_id = {alpha}",
              ("rowcount", 0), "a member's revoke is filtered to zero rows by the owner policy"),
        Check("E2-RLS-22", "authenticated", "member_alpha",
              "update public.profiles set is_operator = true where id = {member_alpha}",
              ("error", PERMISSION_DENIED),
              "the column grant, not RLS, is what stops self-promotion to operator",
              message_contains="permission denied for table"),
        Check("E2-RLS-23", "authenticated", "member_alpha",
              "update public.profiles set full_name = 'Renamed' where id = {member_alpha}",
              ("rowcount", 1), "a member may still rename themselves"),
        Check("E2-RLS-24", "authenticated", "member_alpha",
              "update public.profiles set full_name = 'Hijacked' where id = {owner_beta}",
              ("rowcount", 0), "another tenant's profile is not writable"),
        Check("E2-RLS-25", "authenticated", "member_alpha",
              "insert into public.credit_ledger (org_id, delta_usd, kind) values ({alpha}, 1, 'grant')",
              ("error", PERMISSION_DENIED), "credit is service-role only: no self-granting",
              message_contains="permission denied for table"),
        Check("E2-RLS-26", "authenticated", "member_alpha",
              "insert into public.usage_events (id, org_id, model_id, status)"
              " values (gen_random_uuid(), {alpha}, 'nemostation/marlin-2b', 200)",
              ("error", PERMISSION_DENIED), "usage is service-role only: no self-metering",
              message_contains="permission denied for table"),
        Check("E2-RLS-27", "authenticated", "member_alpha",
              "delete from public.api_keys where org_id = {alpha}",
              ("error", PERMISSION_DENIED), "keys are revoked, never deleted",
              message_contains="permission denied for table"),
        Check("E2-RLS-28", "authenticated", "member_alpha",
              "insert into public.org_members (org_id, user_id, role)"
              " values ({beta}, {member_alpha}, 'owner')",
              ("error", PERMISSION_DENIED), "nobody joins a tenant by writing the table",
              message_contains="permission denied for table"),

        # -------- owner of alpha
        Check("E2-RLS-30", "authenticated", "owner_alpha",
              "insert into public.api_keys (org_id, created_by, name, prefix, key_hash)"
              " values ({alpha}, {owner_alpha}, 'owner key', 'sk-infrx-o', 'ownerhash')",
              ("rowcount", 1), "an owner mints a key for their own tenant"),
        Check("E2-RLS-31", "authenticated", "owner_alpha",
              "insert into public.api_keys (org_id, created_by, name, prefix, key_hash)"
              " values ({beta}, {owner_alpha}, 'cross', 'sk-infrx-c', 'crosshash')",
              ("error", PERMISSION_DENIED), "an owner of alpha is nobody in beta",
              message_contains="violates row-level security policy"),
        Check("E2-RLS-32", "authenticated", "owner_alpha",
              "insert into public.api_keys (org_id, created_by, name, prefix, key_hash)"
              " values ({alpha}, {member_alpha}, 'forged', 'sk-infrx-f', 'forgedhash')",
              ("error", PERMISSION_DENIED),
              "created_by must be the caller: no forging another member's authorship",
              message_contains="violates row-level security policy"),
        Check("E2-RLS-33", "authenticated", "owner_alpha",
              "update public.api_keys set revoked_at = now() where id = {alpha_key}",
              ("rowcount", 1), "an owner revokes their own key"),
        Check("E2-RLS-34", "authenticated", "owner_alpha",
              "update public.api_keys set revoked_at = now() where id = {beta_key}",
              ("rowcount", 0), "and cannot revoke another tenant's"),

        # -------- RPC guards (the reporting functions are security invoker + explicit check)
        Check("E2-RLS-40", "authenticated", "member_alpha",
              "select public.org_balance({beta})",
              ("error", PERMISSION_DENIED), "the balance RPC refuses a foreign org outright",
              message_contains="not a member of organization"),
        Check("E2-RLS-41", "authenticated", "member_alpha",
              "select public.org_balance({alpha})",
              ("value", Decimal("23.746875")), "and returns the exact seeded total for its own"),
        Check("E2-RLS-42", "authenticated", "member_alpha",
              "select count(*) from public.org_usage_summary({beta}, '2026-01-01Z', '2027-01-01Z')",
              ("error", PERMISSION_DENIED), "the usage RPC refuses a foreign org",
              message_contains="not a member of organization"),
        Check("E2-RLS-43", "authenticated", "member_alpha",
              "select requests from public.org_usage_summary({alpha}, '2026-01-01Z', '2027-01-01Z')",
              ("value", 12), "and counts exactly the seeded rows for its own"),
        Check("E2-RLS-44", "anon", None, "select public.org_balance({alpha})",
              ("error", PERMISSION_DENIED),
              "E2R item 2: anon used to reach the function body and be refused by its "
              "membership check (PUBLIC keeps EXECUTE by default). 0004 revokes EXECUTE "
              "from `public, anon`, so the refusal now happens one layer earlier - at the "
              "function grant. Both are 42501; only the message says which, and a harness "
              "that accepted either would not notice the grant disappearing again",
              message_contains="permission denied for function org_balance"),

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
              "an operator grant is a server action, not a table write (R34 audits it)",
              message_contains="permission denied for table"),
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
    ] + access_rows() + write_rows() + journal_rows() + settlement_rows() + login_rows()


# --------------------------------------------------------------------- access completeness
#
# E3B phase 2, item 7 (E2R handback: "the pilot tables ... and the 0005 console views and RPCs
# have no matrix rows here"). Every relation and every SECURITY DEFINER function in `infrx` and
# `public` - measured on the migrated database, 0001-0017 - with the API roles that may reach
# it; each of anon/authenticated/service_role gets a row, so a grant that appears (or goes)
# fails the row, and an object with no entry fails `test_services`' completeness case. D4's
# 0017 (D4 request 8) adds no relation: its functions are rows below, and its watermark
# columns, their CHECK and the terminal-event trigger on `infrx.jobs` are `journal_rows()`.
# L3-REBASE: D10's 0019-0021 rows below are read off the migration text (each privilege
# block's revoke/grant lists, cited per row), not off the measured catalog; D10's two
# dedicated logins are `login_rows()`.
#
# A relation row: `select 1 from <it> limit 0` as the role - the grant alone (schema usage,
# table/view privilege), independent of the rows and of RLS. A function row:
# `has_function_privilege(<signature>, 'execute')` as the role; for a browser role an `infrx`
# function is refused one layer earlier, at the schema, and the row says so.
API_ROLES = ("anon", "authenticated", "service_role")
NOBODY, SERVICE, BROWSER = (), ("service_role",), ("authenticated", "service_role")

RELATIONS = {
    "infrx.attempts": SERVICE,
    "infrx.audit_entries": SERVICE,
    "infrx.callback_deliveries": SERVICE,
    "infrx.callback_destinations": SERVICE,
    "infrx.capacity_reservations": SERVICE,
    "infrx.catalog_listings": SERVICE,
    "infrx.consent_history": SERVICE,
    "infrx.content_objects": SERVICE,             # 0019:952-955 revoke all, grant select
    "infrx.credit_holds": SERVICE,
    "infrx.credit_ledger": SERVICE,
    "infrx.credit_wallet_holds": SERVICE,
    "infrx.credit_wallet_reconciliation": SERVICE,
    "infrx.credit_wallets": SERVICE,
    "infrx.data_access_policies": SERVICE,
    "infrx.deployment_revisions": SERVICE,
    "infrx.endpoints": SERVICE,
    "infrx.feature_flags": SERVICE,
    "infrx.feedback": SERVICE,
    "infrx.idempotency": SERVICE,
    "infrx.job_media": SERVICE,
    "infrx.job_readiness": SERVICE,               # 0019:952-955 revoke all, grant select
    "infrx.job_results": NOBODY,
    "infrx.jobs": SERVICE,
    "infrx.judge_budgets": SERVICE,
    "infrx.judge_reservations": SERVICE,
    "infrx.judge_runs": SERVICE,
    "infrx.judge_samples": SERVICE,
    "infrx.media_objects": SERVICE,
    "infrx.media_uploads": SERVICE,
    "infrx.model_versions": SERVICE,
    "infrx.org_entitlements": SERVICE,
    "infrx.outbox": SERVICE,
    "infrx.price_versions": SERVICE,
    "infrx.provider_memberships": SERVICE,
    "infrx.provider_orgs": SERVICE,
    "infrx.rate_card_versions": SERVICE,
    "infrx.retired_individuals": SERVICE,
    "infrx.serving_versions": SERVICE,
    "infrx.signup_denials": SERVICE,
    "infrx.signup_entitlements": SERVICE,
    "infrx.signup_identity_claims": SERVICE,
    "infrx.staged_media": SERVICE,
    "infrx.stream_chunks": SERVICE,
    "infrx.wallet_reconciliation": SERVICE,
    "infrx.wallets": SERVICE,
    "public.api_keys": BROWSER,
    "public.calibration_labels": BROWSER,
    "public.consent_history": BROWSER,
    "public.console_admin_orgs": BROWSER,
    "public.console_credit_ledger": BROWSER,
    "public.console_credit_wallets": BROWSER,
    "public.console_judge_runs": BROWSER,
    "public.console_ledger": BROWSER,
    "public.console_usage": BROWSER,
    "public.credit_ledger": BROWSER,
    "public.feedback": BROWSER,
    "public.models": BROWSER,
    "public.operator_audit": BROWSER,
    "public.org_members": BROWSER,
    "public.org_settings": BROWSER,
    "public.organizations": BROWSER,
    "public.profiles": BROWSER,
    "public.usage_events": BROWSER,
    "public.wallets": BROWSER,
}

VIEWS = frozenset({
    "public.calibration_labels",
    "public.consent_history",
    "public.console_admin_orgs",
    "public.console_credit_ledger",
    "public.console_credit_wallets",
    "public.console_judge_runs",
    "public.console_ledger",
    "public.console_usage",
    "public.feedback",
    "public.operator_audit",
    "public.org_settings",
    "public.wallets",
})

FUNCTIONS = {
    "infrx.accept_feedback(jsonb)": SERVICE,
    "infrx.acknowledge_dispatch(jsonb)": SERVICE,
    "infrx.active_holds(uuid)": SERVICE,
    "infrx.admission_checks(jsonb,jsonb,jsonb,text,timestamp with time zone)": NOBODY,
    ("infrx.admission_insert_job(jsonb,jsonb,jsonb,jsonb,timestamp with time zone,"
     "timestamp with time zone,text,text,text,jsonb,jsonb,numeric)"): NOBODY,
    "infrx.admission_replay(jsonb,double precision,timestamp with time zone,text)": NOBODY,
    "infrx.admission_rows(jsonb,jsonb,jsonb,text,timestamp with time zone)": NOBODY,
    "infrx.admit_credit(jsonb)": NOBODY,
    "infrx.admit_legacy_usd(jsonb)": NOBODY,
    "infrx.admit(jsonb)": SERVICE,
    "infrx.admit_ready(jsonb)": SERVICE,                          # 0019:976-986 (D10)
    "infrx.api_keys_identity_guard()": SERVICE,
    "infrx.append(jsonb)": SERVICE,
    "infrx.audit_by_idempotency_key(text)": SERVICE,
    "infrx.bind_source(infrx.jobs,jsonb,integer)": NOBODY,        # 0019:964-975 (D10)
    "infrx.bootstrap_operator_key(uuid,text,text,text,text,text)": SERVICE,
    "infrx.cancel(jsonb)": SERVICE,
    "infrx.check_pinned_capability(infrx.jobs,jsonb)": NOBODY,    # 0019:964-975 (D10)
    "infrx.claim_preparation(jsonb)": SERVICE,
    "infrx.claim_preparation_ready(jsonb)": SERVICE,              # 0019:976-986 (D10)
    "infrx.claim(jsonb)": SERVICE,
    "infrx.consent_guard()": NOBODY,
    "infrx.content_acknowledge_delete(jsonb)": SERVICE,           # 0020:482-489 (D10)
    "infrx.content_candidates(jsonb)": SERVICE,                   # 0020:482-489 (D10)
    "infrx.content_claim(jsonb)": SERVICE,                        # 0020:482-489 (D10)
    "infrx.content_objects_guard()": NOBODY,                      # 0019:964-975 (D10)
    "infrx.content_recheck(infrx.content_objects,timestamp with time zone)": NOBODY,  # 0020
    "infrx.content_referenced(infrx.content_objects,timestamp with time zone)": NOBODY,
    "infrx.content_references(jsonb)": SERVICE,                   # 0019:976-986 (D10)
    "infrx.content_register(jsonb)": SERVICE,                     # 0019:976-986 (D10)
    "infrx.content_row(uuid)": NOBODY,                            # 0020:470-481 (D10)
    "infrx.content_tombstone(jsonb)": SERVICE,                    # 0020:482-489 (D10)
    "infrx.credit_ledger_moves_wallet()": SERVICE,
    "infrx.credit_wallet_holds_moves_wallet()": SERVICE,
    "infrx.credit_wallets_guard()": SERVICE,
    "infrx.delete_media_object_if_idle(text,timestamp with time zone)": SERVICE,
    "infrx.deployment_revisions_guard()": SERVICE,
    "infrx.dispatch_pending(jsonb)": SERVICE,
    "infrx.debit_credit(text,integer,integer)": NOBODY,           # 0018 (D5)
    "infrx.dispatch_snapshot()": SERVICE,
    "infrx.ensure_wallet()": NOBODY,
    "infrx.expire_journal(jsonb)": SERVICE,                       # 0017 (D4)
    "infrx.extend_model_limits()": SERVICE,
    "infrx.fail_dispatch(jsonb)": SERVICE,
    "infrx.fail_preparation(jsonb)": SERVICE,                     # 0022 (D10 follow-up)
    "infrx.fence_lease(jsonb,text[],double precision)": NOBODY,
    "infrx.forbid_truncate()": NOBODY,
    "infrx.forbid_update_delete()": NOBODY,
    "infrx.gc_outbox(jsonb)": SERVICE,
    "infrx.grant_credit(jsonb)": SERVICE,
    "infrx.grant_signup_credit(uuid,text,text,uuid)": SERVICE,
    "infrx.heartbeat(jsonb)": SERVICE,
    "infrx.idempotency_lookup(jsonb)": SERVICE,                   # 0018 (D5)
    "infrx.individual_usd_hold(uuid)": NOBODY,
    "infrx.is_entitled(uuid,text)": NOBODY,
    "infrx.job_admission(uuid)": SERVICE,
    "infrx.jobs_admission_guard()": SERVICE,
    "infrx.jobs_admission_record_guard()": NOBODY,
    "infrx.jobs_credit_admission_guard(infrx.jobs)": NOBODY,
    "infrx.jobs_guard()": NOBODY,
    "infrx.jobs_no_delete_when_terminal()": NOBODY,
    "infrx.jobs_pins_guard()": SERVICE,
    # 0022 (D10 follow-up, L3-REBASE F2) revokes it from everyone, like its sibling guards
    "infrx.jobs_result_expiry_guard()": NOBODY,
    "infrx.jobs_settlement_record_guard()": NOBODY,               # 0018 (D5)
    "infrx.journal_terminal_event()": NOBODY,                     # 0017 (D4)
    "infrx.journal_usage()": SERVICE,                             # 0017 (D4)
    "infrx.journal_bytes_charged()": NOBODY,
    "infrx.job_results_guard()": NOBODY,                          # 0020:470-481 (D10)
    "infrx.key_by_hash(text)": SERVICE,
    "infrx.ledger_moves_wallet()": NOBODY,
    "infrx.legacy_usd_rollout_hold(uuid)": NOBODY,
    "infrx.load_work(jsonb)": SERVICE,
    "infrx.load_work_credit(jsonb)": SERVICE,                     # 0018 (D5)
    "infrx.media_uploads_guard()": NOBODY,
    "infrx.now()": SERVICE,
    "infrx.outbox_aggregate_tenant()": NOBODY,
    "infrx.personal_org_binding_guard()": SERVICE,
    "infrx.prepare(jsonb)": SERVICE,
    "infrx.provider_memberships_guard()": SERVICE,
    "infrx.put_result(jsonb)": SERVICE,
    "infrx.quarantine_hold_credit(uuid,timestamp with time zone)": NOBODY,
    "infrx.quarantine_hold_legacy_usd(uuid,timestamp with time zone)": NOBODY,
    "infrx.read_journal(jsonb)": SERVICE,                         # 0017 (D4)
    "infrx.read_result(uuid,text)": SERVICE,
    "infrx.readiness_cutover_check()": SERVICE,                   # 0019:976-986 (D10)
    "infrx.readiness_doc(uuid)": SERVICE,                         # 0019:976-986 (D10)
    "infrx.record_signup_denial(uuid,text)": NOBODY,
    "infrx.record_submission(jsonb)": SERVICE,
    "infrx.recover_job(uuid,timestamp with time zone,integer,double precision)": NOBODY,
    "infrx.recover(jsonb)": SERVICE,
    "infrx.register_content(jsonb,double precision)": NOBODY,     # 0019:964-975 (D10)
    "infrx.register_database_content()": NOBODY,                  # 0020:470-481 (D10)
    "infrx.register_existing_database_content(jsonb)": SERVICE,   # 0020:482-489 (D10)
    "infrx.reconcile(jsonb)": SERVICE,                            # 0018 (D5)
    "infrx.release_aged_unknown(uuid,timestamp with time zone)": NOBODY,
    "infrx.release_dispatch(jsonb)": SERVICE,
    "infrx.release_hold_credit(uuid)": NOBODY,
    "infrx.release_hold_legacy_usd(uuid)": NOBODY,
    "infrx.reopen_dispatch(jsonb)": SERVICE,
    "infrx.require_feature(text)": SERVICE,
    "infrx.reserve_judge(jsonb)": SERVICE,
    "infrx.resolve_admission_pins(text)": SERVICE,
    "infrx.resolve_usd_revision(text)": NOBODY,                   # 0021:429-430 (D10)
    "infrx.retire_individual(uuid,text,text,text)": SERVICE,
    "infrx.retired_wallet_guard()": SERVICE,
    "infrx.revoke_key(uuid,text,text,text)": SERVICE,
    "infrx.scrub_content(infrx.content_objects,timestamp with time zone)": NOBODY,  # 0020
    "infrx.set_feature_flag(text,boolean,text,text)": SERVICE,    # 0022 (V-G8TL-2)
    "infrx.set_suspension(uuid,boolean,text,text,text,text)": SERVICE,
    "infrx.settle_credit(uuid,numeric)": NOBODY,                  # 0018 (D5)
    "infrx.settle_legacy_usd(uuid,numeric)": NOBODY,              # 0018 (D5)
    "infrx.staged_media_guard()": NOBODY,
    "infrx.terminalize_no_usage(uuid,text,text,double precision)": NOBODY,
    "infrx.terminalize_unstarted(uuid,text)": NOBODY,
    "infrx.terminalize(jsonb)": SERVICE,
    "infrx.touch_media_object(text,uuid)": SERVICE,
    "infrx.upload_abort(jsonb)": SERVICE,                         # 0019:976-986 (D10)
    "infrx.upload_acknowledge_put(jsonb)": SERVICE,               # 0019:976-986 (D10)
    "infrx.upload_complete(jsonb)": SERVICE,                      # 0019:976-986 (D10)
    "infrx.upload_create(jsonb)": SERVICE,                        # 0019:976-986 (D10)
    "infrx.upload_expire(jsonb)": SERVICE,                        # 0019:976-986 (D10)
    "infrx.upload_resolve(jsonb)": SERVICE,                       # 0019:976-986 (D10)
    "infrx.upload_row(uuid,text,text)": NOBODY,                   # 0019:964-975 (D10)
    "infrx.usage_pilot_row_matches_job()": NOBODY,
    "infrx.usage_records(uuid,timestamp with time zone,uuid,integer)": SERVICE,
    "infrx.usd_price(text)": SERVICE,                             # 0021:447-448 (D10)
    "infrx.verified_user(uuid)": SERVICE,
    "public.claim_signup_grant(uuid,text,uuid)": SERVICE,
    # 0021:420-428 (D10): the signed-in consumer reads; the org resolver is nobody's
    "public.consumer_job_result(uuid)": BROWSER,
    # 0024 (D10-APP-SQL): consumer_jobs gains four defaulted filters (0021's signature is
    # dropped and recreated), and C0 WR-5's own-ledger page
    "public.consumer_credit_ledger(text,integer)": BROWSER,
    "public.consumer_may_create_key()": BROWSER,   # 0024: the api_keys INSERT predicate
    "public.consumer_jobs(text,integer,uuid,text,uuid,timestamp with time zone,"
    "timestamp with time zone)": BROWSER,
    "public.consumer_org()": NOBODY,
    "public.handle_new_user()": SERVICE,
    "public.is_operator()": BROWSER,
    "public.is_org_member(uuid)": BROWSER,
    "public.is_org_owner(uuid)": BROWSER,
}

# SECURITY INVOKER functions a row pins although the completeness case does not list them:
# 0017's `chunk_doc` and 0018's three pure helpers, callable only from the SECURITY DEFINER
# bodies (revoked from everyone; D5 request 8, measured on both images).
INVOKER_FUNCTIONS = {"infrx.chunk_doc(infrx.stream_chunks)": NOBODY,
                     "infrx.cause_carries_state(text,text)": NOBODY,
                     "infrx.debit_legacy_usd(jsonb,integer,integer)": NOBODY,
                     "infrx.usage_doc(integer,integer)": NOBODY,
                     # D10's pure helpers, revoked from everyone (0019:964-975, 0020:470-481)
                     "infrx.lifecycle_code(text)": NOBODY,
                     "infrx.lifecycle_refuse(text,text)": NOBODY,
                     "infrx.lifecycle_refusal(text,text)": NOBODY,
                     "infrx.content_doc(infrx.content_objects)": NOBODY,
                     "infrx.upload_doc(infrx.media_uploads)": NOBODY,
                     "infrx.claim_doc(infrx.content_objects)": NOBODY,
                     "infrx.scrubbed_request(jsonb)": NOBODY}

# 0017's two watermark columns on `infrx.jobs`, as `pg_get_*def` renders them (measured at
# the D4 merge): one cursor or none, each part >= 1; and the trigger that writes the terminal
# journal event in the settling transaction, enabled.
JOURNAL_CHECK = ("CHECK ((((journal_pruned_generation IS NULL) = (journal_pruned_sequence IS "
                 "NULL)) AND COALESCE(((journal_pruned_generation >= 1) AND "
                 "(journal_pruned_sequence >= 1)), true)))")
JOURNAL_TRIGGER = ("CREATE TRIGGER jobs_terminal_journal_event AFTER UPDATE OF settled_at ON "
                   "infrx.jobs FOR EACH ROW WHEN (((old.settled_at IS NULL) AND "
                   "(new.settled_at IS NOT NULL) AND (new.journal_reserved_bytes > 0))) "
                   "EXECUTE FUNCTION infrx.journal_terminal_event()")


def journal_rows() -> list[Check]:
    """D4's 0017 objects on `infrx.jobs` (D4 request 8): the watermark columns per API role
    (service_role reads them through 0004's table grant; a browser role never reaches the
    schema), their CHECK and the terminal-event trigger, each pinned to its definition."""
    rows = [
        Check("E3B-RLS-0017-watermark-check", "postgres", None,
              "select pg_get_constraintdef(oid) from pg_constraint where conrelid = "
              "'infrx.jobs'::regclass and conname = 'jobs_journal_watermark_is_one_cursor'",
              ("value", JOURNAL_CHECK), "the prune watermark is one cursor or none (0017)"),
        Check("E3B-RLS-0017-terminal-trigger", "postgres", None,
              "select pg_get_triggerdef(oid) from pg_trigger where tgrelid = "
              "'infrx.jobs'::regclass and tgname = 'jobs_terminal_journal_event' "
              "and tgenabled = 'O'",
              ("value", JOURNAL_TRIGGER), "the settling UPDATE writes the terminal event (0017)")]
    for role in API_ROLES:
        sql = "select journal_pruned_generation, journal_pruned_sequence from infrx.jobs limit 0"
        rows.append(Check(
            f"E3B-RLS-0017-watermark-columns-{role}", role, None, sql,
            ("rows", 0) if role == "service_role" else ("error", PERMISSION_DENIED),
            f"{role} {'reads' if role == 'service_role' else 'never reaches'} the watermark",
            message_contains=None if role == "service_role"
            else "permission denied for schema infrx"))
    return rows


# D5's 0018 objects on `infrx.jobs` (D5 request 8, measured on a fresh clone, both images): the
# settled usage is one authoritative fact, and the guard freezes it and the proposal once settled.
SETTLED_USAGE_CHECK = ("CHECK ((((usage_prompt_tokens IS NULL) = (usage_completion_tokens IS "
                       "NULL)) AND ((usage_prompt_tokens IS NULL) OR ((usage_prompt_tokens >= 0) "
                       "AND (usage_completion_tokens >= 0) AND (NOT (usage_certainty IS DISTINCT "
                       "FROM 'authoritative'::text))))))")
SETTLEMENT_GUARD = ("CREATE TRIGGER jobs_settlement_record_guard BEFORE UPDATE ON infrx.jobs FOR "
                    "EACH ROW EXECUTE FUNCTION infrx.jobs_settlement_record_guard()")


def settlement_rows() -> list[Check]:
    """D5's 0018 objects on `infrx.jobs`: the settled usage/proposal columns per API role,
    their CHECK and the guard that freezes them once settled."""
    rows = [
        Check("E3B-RLS-0018-settled-usage-check", "postgres", None,
              "select pg_get_constraintdef(oid) from pg_constraint where conrelid = "
              "'infrx.jobs'::regclass and conname = 'jobs_settled_usage_is_one_fact'",
              ("value", SETTLED_USAGE_CHECK), "settled usage is one authoritative fact (0018)"),
        Check("E3B-RLS-0018-settlement-guard", "postgres", None,
              "select pg_get_triggerdef(oid) from pg_trigger where tgenabled = 'O' and "
              "tgrelid = 'infrx.jobs'::regclass and tgname = 'jobs_settlement_record_guard'",
              ("value", SETTLEMENT_GUARD), "the settled usage and proposal are frozen (0018)")]
    for role in API_ROLES:
        sql = ("select proposal, usage_prompt_tokens, usage_completion_tokens "
               "from infrx.jobs limit 0")
        rows.append(Check(
            f"E3B-RLS-0018-settlement-columns-{role}", role, None, sql,
            ("rows", 0) if role == "service_role" else ("error", PERMISSION_DENIED),
            f"{role} {'reads' if role == 'service_role' else 'never reaches'} the settled usage",
            message_contains=None if role == "service_role"
            else "permission denied for schema infrx"))
    return rows


# Review F2 (refuted as a gate hole - D's check_privileges enforces write grants verb by
# verb - and folded in): the TABLE-level write verbs each API role holds, measured at 0016.
# anon and authenticated hold none on any relation (authenticated's api_keys/organizations/
# profiles writes are COLUMN grants, which `has_table_privilege` does not count); service_role
# holds its schema's default except the immutable registry rows (insert only) and the money
# and signup relations only SECURITY DEFINER functions write. L3-REBASE: D10's 0019 adds the
# upload tickets to the definer-only set (0019:959 `revoke insert, update, delete on
# infrx.media_uploads from service_role`: the `upload_*` boundary is the writer, R128) and its
# two new relations are read-only to service_role (0019:952-955).
WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
SERVICE_WRITES = {
    "infrx": "INSERT,UPDATE,DELETE", "public": "INSERT,UPDATE,DELETE,TRUNCATE",
    **dict.fromkeys(("infrx.catalog_listings", "infrx.data_access_policies",
                     "infrx.deployment_revisions", "infrx.endpoints", "infrx.model_versions",
                     "infrx.provider_memberships", "infrx.provider_orgs",
                     "infrx.rate_card_versions", "infrx.serving_versions"), "INSERT"),
    **dict.fromkeys(("infrx.credit_ledger", "infrx.credit_wallet_holds",
                     "infrx.credit_wallet_reconciliation", "infrx.credit_wallets",
                     "infrx.feature_flags", "infrx.job_results", "infrx.retired_individuals",
                     "infrx.media_uploads", "infrx.content_objects", "infrx.job_readiness",
                     "infrx.signup_denials", "infrx.signup_entitlements",
                     "infrx.signup_identity_claims", "infrx.wallets"), ""),
}


def write_rows() -> list[Check]:
    """Per relation and API role, the table-level write verbs it holds, read as `postgres`
    (the three-argument form needs no schema usage). A grant that widens or narrows fails."""
    rows = []
    for relation in RELATIONS:
        for role in API_ROLES:
            expected = (SERVICE_WRITES.get(relation, SERVICE_WRITES[relation.split(".")[0]])
                        if role == "service_role" else "")
            verbs = ", ".join(f"'{verb}'" for verb in WRITE_VERBS)
            rows.append(Check(
                f"E3B-RLS-W-{relation}-{role}", "postgres", None,
                f"select array_to_string(array(select v from unnest(array[{verbs}]) v "
                f"where has_table_privilege('{role}', '{relation}', v)), ',')",
                ("value", expected),
                f"{role} holds exactly {expected or 'no'} table-level write on {relation}"))
    return rows



# D10's two dedicated logins (0021:458-560, R127), read off the migration text: NOLOGIN here
# (the operator grants LOGIN out of band), NOINHERIT, no attribute that widens, a member of
# nothing, and exactly the surface 0021 grants, plus 0022's `fail_preparation`, minus the
# two unmarked doors `admit`/`claim_preparation` 0023 revokes (R123; the W5 runtime admits
# and claims through the ready doors). Read as `postgres`, catalog-wide over
# `infrx`/`public`, so a grant that appears anywhere fails the row.
RUNTIME_FUNCTIONS = (                                             # 0021:490-515, 0022, 0023
    "infrx.acknowledge_dispatch(jsonb)", "infrx.admit_ready(jsonb)",
    "infrx.append(jsonb)", "infrx.cancel(jsonb)", "infrx.claim(jsonb)",
    "infrx.claim_preparation_ready(jsonb)",
    "infrx.content_acknowledge_delete(jsonb)", "infrx.content_candidates(jsonb)",
    "infrx.content_claim(jsonb)", "infrx.content_references(jsonb)",
    "infrx.content_register(jsonb)", "infrx.content_tombstone(jsonb)",
    "infrx.dispatch_pending(jsonb)", "infrx.dispatch_snapshot()", "infrx.expire_journal(jsonb)",
    "infrx.fail_dispatch(jsonb)", "infrx.fail_preparation(jsonb)",  # 0022
    "infrx.gc_outbox(jsonb)", "infrx.heartbeat(jsonb)",
    "infrx.idempotency_lookup(jsonb)", "infrx.job_admission(uuid)", "infrx.journal_usage()",
    "infrx.load_work(jsonb)", "infrx.load_work_credit(jsonb)", "infrx.now()",
    "infrx.prepare(jsonb)", "infrx.put_result(jsonb)", "infrx.read_journal(jsonb)",
    "infrx.read_result(uuid,text)", "infrx.readiness_doc(uuid)", "infrx.recover(jsonb)",
    "infrx.release_dispatch(jsonb)", "infrx.reopen_dispatch(jsonb)", "infrx.terminalize(jsonb)",
    "infrx.upload_abort(jsonb)", "infrx.upload_acknowledge_put(jsonb)",
    "infrx.upload_complete(jsonb)", "infrx.upload_create(jsonb)", "infrx.upload_expire(jsonb)",
    "infrx.upload_resolve(jsonb)", "infrx.usd_price(text)")
LOGINS = {
    "infrx_runtime": {
        "config": "idle_in_transaction_session_timeout=30s,statement_timeout=15s",  # 0021:480-481
        "functions": RUNTIME_FUNCTIONS,
        "tables": tuple(sorted(                                   # 0021:518-535
            [f"{t}:SELECT" for t in (
                "infrx.catalog_listings", "infrx.content_objects", "infrx.data_access_policies",
                "infrx.deployment_revisions", "infrx.endpoints", "infrx.job_readiness",
                "infrx.jobs", "infrx.model_versions", "infrx.provider_orgs",
                "infrx.rate_card_versions", "infrx.serving_versions", "infrx.stream_chunks",
                "public.models")]
            + ["infrx.job_media:SELECT,INSERT", "infrx.staged_media:SELECT,INSERT"])),
        "columns": ("infrx.jobs.updated_at:UPDATE",),             # 0021:537
    },
    "infrx_monitor": {
        "config": "default_transaction_read_only=on,statement_timeout=10s",  # 0021:482-483
        "functions": (),
        "tables": ("infrx.credit_wallet_reconciliation:SELECT",   # 0021:550-551
                   "infrx.wallet_reconciliation:SELECT"),
        "columns": tuple(sorted(                                  # 0021:543-549
            [f"infrx.jobs.{c}:SELECT" for c in (
                "request_id", "state", "admitted_at", "queued_at", "updated_at", "deadline_at",
                "settled_at", "outcome_cause", "result_expires_at")]
            + [f"infrx.outbox.{c}:SELECT" for c in (
                "kind", "acknowledged_at", "claimed_at", "available_at")]
            + [f"infrx.credit_holds.{c}:SELECT" for c in (
                "request_id", "state", "reconcile_after")]
            + ["infrx.stream_chunks.expires_at:SELECT", "infrx.job_results.request_id:SELECT",
               # 0024 (W5-F5 WR-W5F5-1): the CREDIT holds' state, with a monitor policy
               "infrx.credit_wallet_holds.state:SELECT"])),
    },
}
_LOGIN_ATTRIBUTES = ("rolsuper", "rolinherit", "rolcreaterole", "rolcreatedb", "rolcanlogin",
                     "rolreplication", "rolbypassrls")


def _joined(sql: str, sep: str = ",") -> str:
    """One text value: the single column of `sql`, byte-ordered (Python's `sorted`)."""
    return (f"select array_to_string(array(select v from ({sql}) q(v) "
            f"order by v collate \"C\"), '{sep}')")


def login_rows() -> list[Check]:
    """Per dedicated login: attributes, memberships, role defaults, and the exact function,
    table and column surface, each one sorted joined value."""
    rows = []
    for role, surface in LOGINS.items():
        attributes = " || ".join(f"case when {a} then '{a},' else '' end"
                                 for a in _LOGIN_ATTRIBUTES)
        rows += [
            Check(f"L3-LOGIN-{role}-attributes", "postgres", None,
                  f"select {attributes} from pg_roles where rolname = '{role}'", ("value", ""),
                  f"{role} is NOLOGIN, NOINHERIT, not superuser/createrole/createdb/"
                  "replication/bypassrls (0021:473-478, R127)"),
            Check(f"L3-LOGIN-{role}-member-of", "postgres", None,
                  f"select count(*) from pg_auth_members where member = '{role}'::regrole",
                  ("value", 0), f"{role} is a member of no role (R127)"),
            # PostgreSQL 16+ gives a role's creator ADMIN on it (no INHERIT, no SET): that is
            # the one member, and it confers nothing on the login itself.
            Check(f"L3-LOGIN-{role}-members", "postgres", None,
                  _joined("select m.member::regrole::text || ':' || m.admin_option::text || "
                          "':' || m.inherit_option::text || ':' || m.set_option::text from "
                          f"pg_auth_members m where m.roleid = '{role}'::regrole"),
                  ("value", "postgres:true:false:false"),
                  f"only the creator's implicit ADMIN (PG16+) is a member of {role}"),
            Check(f"L3-LOGIN-{role}-config", "postgres", None,
                  _joined(f"select unnest(rolconfig) from pg_roles where rolname = '{role}'"),
                  ("value", surface["config"]),
                  f"{role}'s bounds are role defaults (0021:480-483, R127)"),
            Check(f"L3-LOGIN-{role}-functions", "postgres", None,
                  _joined("select p.oid::regprocedure::text from pg_proc p join pg_namespace n "
                          "on n.oid = p.pronamespace where n.nspname in ('infrx', 'public') "
                          f"and has_function_privilege('{role}', p.oid, 'execute')"),
                  ("value", ",".join(sorted(surface["functions"]))),
                  f"{role} executes exactly 0021's list and nothing else"),
            Check(f"L3-LOGIN-{role}-tables", "postgres", None,
                  _joined("select n.nspname || '.' || c.relname || ':' || array_to_string("
                          "array(select v from unnest(array['SELECT', 'INSERT', 'UPDATE', "
                          f"'DELETE', 'TRUNCATE']) v where has_table_privilege('{role}', "
                          "c.oid, v)), ',') from pg_class c join pg_namespace n on n.oid = "
                          "c.relnamespace where n.nspname in ('infrx', 'public') and "
                          "c.relkind in ('r', 'v', 'm', 'p', 'f') and has_table_privilege("
                          f"'{role}', c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')", ";"),
                  ("value", ";".join(surface["tables"])),
                  f"{role} holds exactly 0021's table-level grants"),
            Check(f"L3-LOGIN-{role}-columns", "postgres", None,
                  _joined("select n.nspname || '.' || c.relname || '.' || a.attname || ':' || "
                          "x.privilege_type from pg_attribute a join pg_class c on c.oid = "
                          "a.attrelid join pg_namespace n on n.oid = c.relnamespace, "
                          "aclexplode(a.attacl) x where n.nspname in ('infrx', 'public') and "
                          f"x.grantee = '{role}'::regrole"),
                  ("value", ",".join(surface["columns"])),
                  f"{role} holds exactly 0021's column grants (R127: the runtime UPDATEs only "
                  "jobs.updated_at, no job_results DML)"),
        ]
    return rows


def access_rows() -> list[Check]:
    rows = []
    for relation, allowed in RELATIONS.items():
        schema, name = relation.split(".")
        for role in API_ROLES:
            if role in allowed:
                rows.append(Check(f"E3B-RLS-{relation}-{role}", role, None,
                                  f"select 1 from {relation} limit 0", ("rows", 0),
                                  f"{role} holds the grant on {relation} (measured at 0016)"))
                continue
            cause = ("schema infrx" if schema == "infrx" and role != "service_role" else
                     f"{'view' if relation in VIEWS else 'table'} {name}")
            rows.append(Check(f"E3B-RLS-{relation}-{role}", role, None,
                              f"select 1 from {relation} limit 0",
                              ("error", PERMISSION_DENIED),
                              f"{role} holds no privilege on {relation}",
                              message_contains=f"permission denied for {cause}"))
    for function, allowed in {**FUNCTIONS, **INVOKER_FUNCTIONS}.items():
        for role in API_ROLES:
            barrier = function.startswith("infrx.") and role != "service_role"
            rows.append(Check(
                f"E3B-RLS-{function}-{role}", role, None,
                f"select has_function_privilege('{function}', 'execute')",
                ("error", PERMISSION_DENIED) if barrier else ("value", role in allowed),
                f"{role} {'may' if role in allowed else 'may not'} execute {function}"
                + (" (the infrx schema refuses first)" if barrier else ""),
                message_contains="permission denied for schema infrx" if barrier else None))
    return rows


CATALOG_RELATIONS = ("select n.nspname || '.' || c.relname from pg_class c join pg_namespace n "
                     "on n.oid = c.relnamespace where n.nspname in ('infrx', 'public') "
                     "and c.relkind in ('r', 'v', 'm', 'p', 'f')")
CATALOG_FUNCTIONS = ("select p.oid::regprocedure::text from pg_proc p join pg_namespace n "
                     "on n.oid = p.pronamespace where n.nspname in ('infrx', 'public') "
                     "and p.prosecdef")


def catalog_objects(conn) -> tuple[set[str], set[str]]:
    """(relations, SECURITY DEFINER functions) of `infrx`/`public` on this database, named
    exactly as RELATIONS/FUNCTIONS name them (schema-qualified: empty search_path)."""
    conn.execute("set search_path = ''")
    try:
        return ({name for name, in conn.execute(CATALOG_RELATIONS).fetchall()},
                {name for name, in conn.execute(CATALOG_FUNCTIONS).fetchall()})
    finally:
        conn.execute("reset search_path")


def run_check(conn, check: Check, fixtures: Fixtures) -> dict:
    """Run one check in its own aborted transaction: nothing a check writes survives, so
    the matrix order cannot matter and a failure leaves no residue."""
    import psycopg
    if check.role not in SQL_ROLES:
        raise MigrationError(f"unknown role {check.role!r}")
    statement, params = _sql(fixtures, check.sql)
    observed: object
    outcome = "ok"
    identity = None
    message = ""
    try:
        with conn.transaction() as _tx:
            conn.execute(f"set local role {check.role}")
            if check.principal:
                principal = fixtures.principals[check.principal]
                impersonate(conn, principal.user_id, check.role)
                # r1 review R-b: a matrix in which `auth.uid()` is NULL proves nothing - every
                # policy that reads it denies everybody, so every "0 rows" case passes
                # vacuously. The identity is therefore checked on EVERY principal-bearing
                # case, not once, and a mismatch fails the case whatever the statement said.
                identity = conn.execute("select auth.uid()").fetchone()[0]
                if str(identity) != str(principal.user_id):
                    raise _NoIdentity(f"auth.uid() is {identity!r}, expected "
                                      f"{principal.user_id} - the JWT claim did not take")
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
    except _NoIdentity as lost:
        observed, outcome, message = str(lost), "no-identity", str(lost)
    except psycopg.errors.Error as exc:
        observed = exc.sqlstate
        outcome = "error"
        message = (exc.diag.message_primary or "")[:200]
    try:
        conn.execute("reset role")
    except psycopg.errors.Error:                 # a failed transaction already reset it
        conn.rollback()

    expected_kind, expected = check.expect
    if expected_kind == "error":
        passed = (outcome == "error" and observed == expected
                  # r1 review, same pass: 42501 is both "no grant" and "RLS/RPC refused you".
                  # Where a case distinguishes them, the message fragment is the distinction.
                  and (check.message_contains is None
                       or check.message_contains.lower() in message.lower()))
    else:
        passed = outcome == "ok" and _same(observed, expected)
    return {"case": check.case, "role": check.role, "principal": check.principal,
            "expected": f"{expected_kind}={expected}"
                        + (f" message~{check.message_contains!r}" if check.message_contains else ""),
            "observed": observed, "message": message, "auth_uid": str(identity) if identity else None,
            "outcome": outcome, "passed": passed, "why": check.why}


def _same(observed: object, expected: object) -> bool:
    if isinstance(expected, Decimal):
        return observed is not None and Decimal(str(observed)) == expected
    return observed == expected


def impersonate(conn, user_id, role: str) -> None:
    """Set BOTH JWT claim forms (r1 review R-b).

    The pinned image's `auth.uid()` reads the legacy per-claim GUC `request.jwt.claim.sub`;
    hosted Supabase / PostgREST >= 10 set the JSON `request.jwt.claims` instead. A harness
    that sets only one is correct against exactly one of them, and silently authenticates
    nobody against the other - which makes every deny-case pass for the wrong reason.
    """
    import json
    conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(user_id),))
    conn.execute("select set_config('request.jwt.claim.role', %s, true)", (role,))
    conn.execute("select set_config('request.jwt.claims', %s, true)",
                 (json.dumps({"sub": str(user_id), "role": role}),))


class _Rollback(Exception):
    """Ends a check's transaction without committing. Not an error."""


class _NoIdentity(Exception):
    """`auth.uid()` did not come back as the principal the case impersonated."""


def run_role_matrix(conn, fixtures: Fixtures) -> list[dict]:
    return [run_check(conn, check, fixtures) for check in role_matrix(fixtures)]
