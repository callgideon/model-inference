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
    * `advance()` RETURNS `infrx.now()`, and `infrx.now()` is STABLE, so within that one
      statement it is still the PRE-move value. Move the clock in its own statement, then
      read (E2 round-3 limit 3, now measured rather than asserted in prose);
    * the offset is a committed row, not a transaction-local GUC: it survives its statement
      and a rolled-back transaction is what undoes it.
    """
    set_clock_offset(conn, 0.0)
    returned = advance_clock(conn, 3600.0)
    read_back, wall = conn.execute(f"select {CLOCK_FUNCTION}, now()").fetchone()
    moved = (read_back - wall).total_seconds()
    stale = (read_back - returned).total_seconds()
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
            "moved_s": round(moved, 1), "advance_returned_pre_move_s": round(stale, 1),
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
