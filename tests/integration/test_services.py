"""Layer 2: real PostgreSQL, Valkey, ClickHouse and object storage.

This file asserts against the stack `run.py` provisions, migrates and seeds. Without that
stack every case here is a reported **skip** naming the command that would make it run - a
skip, never a pass (04: "unavailable external tests are pending, not skipped passes";
run.py reports the same situation as PENDING and exits 3).

    apps/infrx-api/.venv/bin/python tests/integration/run.py

Nothing here writes outside the E2 namespace: database `postgres` inside the
`infrx-e2-postgres` container, Valkey keys under `infrx_e2:`, ClickHouse database
`infrx_e2`, objects under `test/e2/`.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness                                          # noqa: E402
import pgstate                                          # noqa: E402

NO_STACK = (f"no {harness.PROJECT} stack with seeded fixtures: run "
            "`apps/infrx-api/.venv/bin/python tests/integration/run.py`")


def stack_or_skip() -> pgstate.Fixtures:
    """The one place that decides whether layer 2 can run, and says why not."""
    usable, why = harness.docker_available()
    if not usable:
        pytest.skip(f"docker unusable ({why}); {NO_STACK}")
    state = harness.load_state()
    if not state or "fixtures" not in state:
        pytest.skip(NO_STACK)
    if not harness.owned_containers():
        pytest.skip(f"state file exists but no {harness.PREFIX}* container is running; {NO_STACK}")
    return pgstate.Fixtures.from_dict(state["fixtures"])


def connect(**kw):
    import psycopg
    return psycopg.connect(harness.pg_dsn(), autocommit=True, **kw)


def _durable_snapshot() -> dict:
    """Everything the role matrix could commit if a check were not rolled back: the row counts
    it inserts into, the revocations it attempts, the profile name it renames and the exact
    balances. Compared before and after (r2 review B1)."""
    with connect() as conn:
        counts = conn.execute(
            "select (select count(*) from public.api_keys),"
            "       (select count(*) from public.usage_events),"
            "       (select count(*) from public.credit_ledger),"
            "       (select count(*) from public.org_members),"
            "       (select count(*) from public.api_keys where revoked_at is not null),"
            "       (select count(*) from public.profiles where full_name in"
            "               ('Renamed', 'Hijacked')),"
            "       (select count(*) from public.profiles where is_operator)").fetchone()
        return {"api_keys": counts[0], "usage_events": counts[1], "credit_ledger": counts[2],
                "org_members": counts[3], "revoked": counts[4], "renamed": counts[5],
                "operators": counts[6], "balances": pgstate.balances(conn)}


# ------------------------------------------------------------------ F-CONTRACT: schema

def test_the_console_migrations_applied_to_a_supabase_compatible_database():
    """The measurement behind the image choice (08 §10 "D1: four things", item 1):
    `0001_init.sql` needs `auth.users`, `auth.uid()` and the anon/authenticated/service_role
    roles. This asserts they are there AND that our tables are on top of them, so a future
    image swap that quietly drops the auth schema fails here rather than in D1's evidence."""
    stack_or_skip()
    with connect() as conn:
        roles = {row[0] for row in conn.execute(
            "select rolname from pg_roles where rolname in"
            " ('anon','authenticated','service_role')").fetchall()}
        assert roles == {"anon", "authenticated", "service_role"}
        assert conn.execute("select to_regclass('auth.users') is not null").fetchone()[0]
        assert conn.execute(
            "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
            " where n.nspname = 'auth' and p.proname = 'uid'").fetchone()[0] == 1
        tables = {row[0] for row in conn.execute(
            "select tablename from pg_tables where schemaname = 'public'").fetchall()}
        assert {"profiles", "organizations", "org_members", "models", "api_keys",
                "usage_events", "credit_ledger"} <= tables
        unprotected = [row[0] for row in conn.execute(
            "select tablename from pg_tables where schemaname = 'public'"
            " and not rowsecurity").fetchall()]
        assert unprotected == [], f"RLS is off on {unprotected}"


def test_the_signup_trigger_and_not_the_harness_created_the_tenants():
    """Seeding through `auth.users` exercises `handle_new_user`; writing profiles and
    organizations directly would have tested nothing but INSERT."""
    fixtures = stack_or_skip()
    with connect() as conn:
        for handle, principal in fixtures.principals.items():
            row = conn.execute(
                "select p.id, p.is_operator, count(m.org_id) from public.profiles p"
                " left join public.org_members m on m.user_id = p.id"
                " where p.id = %s group by p.id, p.is_operator",
                (principal.user_id,)).fetchone()
            assert row is not None, f"no profile for {handle}"
            assert row[1] == principal.is_operator, handle
            assert row[2] >= 1, f"{handle} has no organization"
        owner = conn.execute(
            "select role from public.org_members where org_id = %s and user_id = %s",
            (fixtures.org("alpha"), fixtures.user("owner_alpha"))).fetchone()
        assert owner[0] == "owner"
        member = conn.execute(
            "select role from public.org_members where org_id = %s and user_id = %s",
            (fixtures.org("alpha"), fixtures.user("member_alpha"))).fetchone()
        assert member[0] == "member", "the matrix needs a non-owner member of someone's org"


def test_balances_are_exact_decimals():
    """DUR-RLS: "migrations preserve existing balances". The anchor is an exact Decimal, so
    a float round-trip anywhere in the stack would show up as a mismatch."""
    fixtures = stack_or_skip()
    with connect() as conn:
        totals = pgstate.balances(conn)
    assert totals["alpha-e2"] == Decimal("23.746875") == fixtures.ledger_totals["alpha"]
    assert totals["beta-e2"] == Decimal("23.746875")


def test_exactly_the_seeded_ids_are_in_the_database_recomputed_from_the_seed():
    """r1 review B4: "the same seed gives the same uuids and rows" is now CHECKED, by
    recomputing every minted id from `Random(seed)` and comparing it with what is stored.

    The previous test only compared the saved seed integer with itself, so `Random(seed)` ->
    `Random()` survived as a mutant. It cannot now: an unseeded generator mints different
    uuids and every set below differs.
    """
    fixtures = stack_or_skip()
    expected = pgstate.seeded_ids(fixtures.seed)
    with connect() as conn:
        users = {row[0] for row in conn.execute("select id from public.profiles").fetchall()}
        keys = {row[0] for row in conn.execute("select id from public.api_keys").fetchall()}
        usage = {row[0] for row in conn.execute("select id from public.usage_events").fetchall()}
        ledger = {row[0] for row in conn.execute(
            "select id from public.credit_ledger").fetchall()}
        hashes = dict(conn.execute(
            "select o.slug, k.key_hash from public.api_keys k"
            " join public.organizations o on o.id = k.org_id").fetchall())

    assert users == set(expected["users"].values()), "the four auth.users ids are seed-derived"
    assert keys == set(expected["keys"].values())
    assert usage == {row for rows in expected["usage"].values() for row in rows}
    assert ledger == {row for rows in expected["ledger"].values() for row in rows}
    assert len(usage) == sum(pgstate.USAGE_ROWS.values()) == 17
    # The key SECRET is seed-derived too, which is only demonstrable through its hash.
    import hashlib
    for org_name, slug in (("alpha", "alpha-e2"), ("beta", "beta-e2")):
        assert hashes[slug] == hashlib.sha256(
            expected["secrets"][org_name].encode()).hexdigest(), org_name

    # And the honest other half: organization ids are NOT a function of the seed. They come
    # from the trigger's gen_random_uuid(), so asserting them against a recomputation would be
    # asserting a falsehood - they are carried in the state file instead.
    assert "orgs" not in expected and "slugs" not in expected, \
        "seeded_ids must not pretend to know an id the database mints"
    assert fixtures.org("alpha") not in users, "an org id is not a user id"


# ------------------------------------------------------------------ DUR-RLS

def test_the_role_matrix_holds_for_every_role():
    """DUR-RLS: member/browser/operator/service roles attacking protected columns and RPCs;
    tenant and role enforcement in the database as well as in the route.

    Every check runs in its own rolled-back transaction, so this can be re-run against the
    same stack and leaves nothing behind.
    """
    fixtures = stack_or_skip()
    before = _durable_snapshot()
    with connect() as conn:
        rows = pgstate.run_role_matrix(conn, fixtures)
    after = _durable_snapshot()
    # r2 review B1: this is the assertion that makes "each check runs in its own rolled-back
    # transaction" true rather than stated. Without the rollback the matrix's own writes commit
    # (measured by the reviewer: api_keys 3/2, usage_events 18/17, a revoked key) and the NEXT
    # run of the unmutated matrix fails E2-RLS-14 and E2-RLS-30 on the residue - a failure that
    # looks like a broken policy and is really a dirty fixture.
    assert before == after, f"the matrix left residue: {before} -> {after}"

    failed = [row for row in rows if not row["passed"]]
    assert failed == [], failed
    assert len(rows) >= 30, f"the matrix shrank to {len(rows)} cases"
    # It must contain both directions, or a broken grant would look like a working policy.
    assert any(row["expected"].startswith("error") for row in rows)
    assert any(row["expected"] == "rowcount=1" for row in rows)


def test_the_role_matrix_has_a_row_for_every_relation_and_security_definer_function():
    """E3B phase 2 item 7 (E2R handback): the matrix is complete against the migrated
    catalog - every relation and every SECURITY DEFINER function of `infrx`/`public` has a
    row per API role, and no row names an object that does not exist. A new migration's
    object fails this until its expected access is written down."""
    stack_or_skip()
    with connect() as conn:
        relations, functions = pgstate.catalog_objects(conn)
    assert sorted(relations - set(pgstate.RELATIONS)) == [], "relations with no matrix row"
    assert sorted(functions - set(pgstate.FUNCTIONS)) == [], \
        "security definer functions with no matrix row"
    assert sorted(set(pgstate.RELATIONS) - relations) == [], "rows for relations that are gone"
    assert sorted(set(pgstate.FUNCTIONS) - functions) == [], "rows for functions that are gone"


def test_a_live_grant_inversion_fails_its_matrix_row():
    """E3B phase 2 item 7: the rows are live. `anon` given the schema and `infrx.jobs`,
    `service_role` stripped of `infrx.admit`, and `authenticated` given table-level INSERT on
    `public.api_keys` (review F2), inside a transaction that is rolled back: each row must come
    back failed, and pass again once it is."""
    fixtures = stack_or_skip()
    rows = {check.case: check for check in pgstate.role_matrix(fixtures)}
    watched = (rows["E3B-RLS-infrx.jobs-anon"], rows["E3B-RLS-infrx.admit(jsonb)-service_role"],
               # Review F2: a table-level write widening fails its write row too.
               rows["E3B-RLS-W-public.api_keys-authenticated"])
    with connect() as conn:
        with conn.transaction(force_rollback=True):
            conn.execute("grant usage on schema infrx to anon; "
                         "grant select on infrx.jobs to anon; "
                         "revoke execute on function infrx.admit(jsonb) from service_role; "
                         "grant insert on public.api_keys to authenticated")
            inverted = [pgstate.run_check(conn, row, fixtures)["passed"] for row in watched]
        restored = [pgstate.run_check(conn, row, fixtures)["passed"] for row in watched]
    assert (inverted, restored) == ([False] * 3, [True] * 3)


def test_a_check_that_should_fail_does_fail():
    """The matrix runner itself is the thing under test here: if `run_check` could not
    report a failure, every row above would be worthless. One deliberately wrong
    expectation must come back `passed=False` (R32 applied to the harness)."""
    fixtures = stack_or_skip()
    wrong = pgstate.Check("E2-RLS-MUTANT", "anon", None,
                          "select count(*) from public.organizations",
                          ("value", 999), "a deliberately wrong expectation")
    wrong_error = pgstate.Check("E2-RLS-MUTANT2", "authenticated", "member_alpha",
                                "select public.org_balance({alpha})",
                                ("error", pgstate.PERMISSION_DENIED),
                                "a success wrongly expected to be refused")
    # "It errored" is not the assertion: a refusal has to be the RIGHT refusal. This one
    # really does raise 42501, and expecting 42P01 (undefined table) must still fail - or a
    # typo'd table name in any matrix row would read as a passing tenant check.
    wrong_sqlstate = pgstate.Check("E2-RLS-MUTANT3", "authenticated", "member_alpha",
                                   "select public.org_balance({beta})",
                                   ("error", "42P01"),
                                   "the right refusal, checked against the wrong SQLSTATE")
    # r2 review B4: and the RIGHT SQLSTATE with the WRONG message must fail too, or the 12
    # message-qualified cases are decoration - dropping the comparison would keep them green.
    wrong_message = pgstate.Check("E2-RLS-MUTANT4", "authenticated", "member_alpha",
                                  "select public.org_balance({beta})",
                                  ("error", pgstate.PERMISSION_DENIED),
                                  "the right refusal, qualified by a message it never emits",
                                  message_contains="no such text in any postgres message")
    right_message = pgstate.Check("E2-RLS-MUTANT5", "authenticated", "member_alpha",
                                  "select public.org_balance({beta})",
                                  ("error", pgstate.PERMISSION_DENIED),
                                  "and the same case with the fragment it really emits",
                                  message_contains="not a member of organization")
    # E2R review N1: and anon's 42501s are not exempt - anon's refusal checked against the
    # wrong SQLSTATE must fail, or a role-conditional comparison would pass every anon row.
    anon_wrong_sqlstate = pgstate.Check("E2-RLS-MUTANT6", "anon", None,
                                        "select count(*) from public.organizations",
                                        ("error", "42P01"),
                                        "anon's real refusal, checked against the wrong SQLSTATE")
    with connect() as conn:
        assert pgstate.run_check(conn, wrong, fixtures)["passed"] is False
        anon = pgstate.run_check(conn, anon_wrong_sqlstate, fixtures)
        assert anon["observed"] == pgstate.PERMISSION_DENIED, anon
        assert anon["passed"] is False, "an anon row is not exempt from the SQLSTATE check"
        assert pgstate.run_check(conn, wrong_error, fixtures)["passed"] is False
        observed = pgstate.run_check(conn, wrong_sqlstate, fixtures)
        assert observed["observed"] == pgstate.PERMISSION_DENIED, observed
        assert observed["passed"] is False, "a mismatched SQLSTATE must not pass"

        mismatched = pgstate.run_check(conn, wrong_message, fixtures)
        assert mismatched["observed"] == pgstate.PERMISSION_DENIED, mismatched
        assert "not a member of organization" in mismatched["message"], mismatched
        assert mismatched["passed"] is False, \
            "the right SQLSTATE with the wrong message must not pass, or the message " \
            "qualification on the 12 denial cases proves nothing"
        assert pgstate.run_check(conn, right_message, fixtures)["passed"] is True


# ------------------------------------------------------------------ the movable clock

def test_the_shared_clock_moves_the_function_every_durable_decision_reads():
    """E2R item 2: the clock under test is `infrx.now()` - D1's, the one the migrations
    default every timestamp to - and not a private function of E2's that nothing reads.

    Three measured facts, all three of which a caller gets wrong if they are not stated:
    the offset moves `infrx.now()`; `advance()` returns the MOVED clock (D2 made it plpgsql;
    before, it returned the pre-move value, and this case said so until E3B phase 2
    re-measured it); and the offset is a committed row, so it outlives its statement and a
    rollback is what undoes it.
    """
    stack_or_skip()
    with connect() as conn:
        assert conn.execute(
            f"select to_regprocedure('{pgstate.CLOCK_FUNCTION}') is not null").fetchone()[0], \
            "the migrations must have created the clock this probe measures"
        probe = pgstate.probe_clock(conn)
        # And backwards, which is what an expiry test needs.
        pgstate.set_clock_offset(conn, -1800.0)
        behind = pgstate.clock_delta_s(conn)
        pgstate.set_clock_offset(conn, 0.0)
    assert probe["moved_s"] == 3600.0, probe
    assert abs(probe["advance_return_lag_s"]) < 1.0, \
        f"advance() must return the moved clock, which PgClock.advance relies on: {probe}"
    assert abs(probe["at_rest_s"]) < 1.0, probe
    assert probe["inside_rolled_back_tx_s"] == 1800.0, probe
    assert abs(probe["after_rollback_s"]) < 1.0, \
        f"a rolled-back move must leave the clock where it was: {probe}"
    assert -1805.0 <= behind <= -1795.0, behind


def test_only_the_legacy_claim_form_authenticates_on_the_pinned_image():
    """E2R item 2, measured rather than assumed, and the reason `impersonate` sets BOTH forms.

    On `supabase/postgres` 17.6.1.173 `auth.uid()` is
    `nullif(current_setting('request.jwt.claim.sub', true), '')::uuid` - the LEGACY per-claim
    GUC. Setting only the JSON `request.jwt.claims` form (which is what PostgREST 13 sends -
    measured separately, see the evidence) authenticates NOBODY, and then every deny-case in
    the matrix passes for the wrong reason. Pinning the asymmetry here means an image whose
    `auth.uid()` starts reading the JSON form fails this case instead of silently changing
    what the whole matrix means.
    """
    import json
    fixtures = stack_or_skip()
    principal = str(fixtures.user("member_alpha"))
    claims = json.dumps({"sub": principal, "role": "authenticated"})
    measured = {}
    with connect() as conn:
        for label, setters in (
                ("legacy_only", [("request.jwt.claim.sub", principal)]),
                ("json_only", [("request.jwt.claims", claims)]),
                ("both", [("request.jwt.claim.sub", principal),
                          ("request.jwt.claims", claims)])):
            with conn.transaction():
                conn.execute("set local role authenticated")
                for name, value in setters:
                    conn.execute("select set_config(%s, %s, true)", (name, value))
                measured[label] = conn.execute("select auth.uid()::text").fetchone()[0]
            conn.execute("reset role")
        source = conn.execute(
            "select prosrc from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
            " where n.nspname = 'auth' and p.proname = 'uid'").fetchone()[0]
    assert "request.jwt.claim.sub" in source, source
    assert "request.jwt.claims" not in source, \
        f"this image's auth.uid() now reads the JSON form too; the evidence must say so: {source}"
    assert measured["legacy_only"] == principal, measured
    assert measured["json_only"] is None, \
        f"the JSON claim form alone must be measured as authenticating nobody here: {measured}"
    assert measured["both"] == principal, measured


# ------------------------------------------------------------------ the other three stores

def test_valkey_accepts_namespaced_keys_and_the_suite_cleans_up_after_itself():
    stack_or_skip()
    client = harness.valkey_client()
    keys = [f"{harness.VALKEY_PREFIX}smoke:{index}" for index in range(5)]
    try:
        for index, key in enumerate(keys):
            client.set(key, index)
        assert [int(client.get(key)) for key in keys] == list(range(5))
        assert sorted(client.keys(f"{harness.VALKEY_PREFIX}*")) == sorted(
            key.encode() for key in keys), "no key outside the namespace"
    finally:
        client.delete(*keys)
    assert client.keys(f"{harness.VALKEY_PREFIX}*") == []


def test_clickhouse_answers_ddl_and_a_round_trip_in_its_own_database():
    """T owns the real DDL (08 §1). This proves the service is usable and isolated, and
    nothing more - the table is dropped again."""
    stack_or_skip()
    client = harness.clickhouse_client()
    table = f"{harness.CH_DATABASE}.e2_smoke"
    try:
        client.command(f"create table if not exists {table}"
                       " (id UUID, org_id UUID, ts DateTime64(3), bytes UInt32)"
                       " engine = MergeTree order by (org_id, ts)")
        from datetime import datetime, timedelta, timezone
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        rows = [(str(uuid.uuid4()), str(uuid.uuid4()), base + timedelta(seconds=index),
                 index * 11) for index in range(20)]
        client.insert(table, rows, column_names=["id", "org_id", "ts", "bytes"])
        total = client.query(f"select count(), sum(bytes) from {table}").result_rows[0]
        assert total == (20, sum(index * 11 for index in range(20)))
        assert client.query("select currentDatabase()").result_rows[0][0] == harness.CH_DATABASE
    finally:
        client.command(f"drop table if exists {table}")


def test_object_storage_is_tenant_prefixed_and_refuses_an_unsigned_read():
    stack_or_skip()
    s3 = harness.s3_client()
    key = f"{harness.OBJECT_PREFIX}smoke/{uuid.uuid4()}.json"
    try:
        s3.create_bucket(Bucket=harness.S3_BUCKET)
    except Exception:                                    # noqa: BLE001 - already exists
        pass
    try:
        s3.put_object(Bucket=harness.S3_BUCKET, Key=key, Body=b'{"v":1}',
                      ContentType="application/json")
        assert s3.get_object(Bucket=harness.S3_BUCKET, Key=key)["Body"].read() == b'{"v":1}'
        listed = s3.list_objects_v2(Bucket=harness.S3_BUCKET, Prefix=harness.OBJECT_PREFIX)
        assert any(item["Key"] == key for item in listed.get("Contents", []))
        import httpx
        anonymous = httpx.get(f"{harness.s3_endpoint()}/{harness.S3_BUCKET}/{key}", timeout=5.0)
        assert anonymous.status_code in (403, 404), \
            "an unsigned read must not succeed: a trace object is not public"
    finally:
        s3.delete_object(Bucket=harness.S3_BUCKET, Key=key)


# ------------------------------------------------------------------ fault injection

def test_a_paused_container_hangs_the_client_and_recovers_when_unpaused():
    """A network drop that HANGS is the shape that finds a missing timeout: the kernel
    still accepts the connection and nothing ever answers. A client without a timeout waits
    for ever here, which is the defect this fault exists to expose."""
    stack_or_skip()
    import valkey
    with harness.Faults() as faults:
        faults.pause("valkey")
        client = harness.valkey_client(socket_timeout=2)
        started = time.monotonic()
        with pytest.raises((valkey.exceptions.TimeoutError, valkey.exceptions.ConnectionError)):
            client.ping()
        waited = time.monotonic() - started
        assert waited < 15, f"the client hung for {waited:.1f}s, not the 2s timeout it asked for"
    assert harness.wait_valkey(timeout=30), "unpausing must restore the service"


def test_killing_a_container_is_survivable_and_the_harness_puts_it_back():
    """OPS-RECOVER in miniature: SIGKILL a store, see a failure rather than a silent
    success, then bring it back. Valkey is the victim because it is disposable by
    construction (`--save ""`), so nothing durable is at stake in this drill."""
    stack_or_skip()
    import valkey
    client = harness.valkey_client(socket_timeout=2)
    client.set(f"{harness.VALKEY_PREFIX}kill-drill", "before")
    with harness.Faults() as faults:
        faults.kill_container("valkey", "SIGKILL")
        with pytest.raises((valkey.exceptions.ConnectionError, valkey.exceptions.TimeoutError)):
            harness.valkey_client(socket_timeout=2).ping()
    harness.wait_valkey(timeout=60)
    fresh = harness.valkey_client()
    assert fresh.get(f"{harness.VALKEY_PREFIX}kill-drill") is None, \
        "a save-less Valkey comes back empty, which is what makes the DUR-OUTBOX rebuild " \
        "drill cheap: the index must be rebuildable from PostgreSQL"


def test_a_network_partition_is_distinguishable_from_a_dead_process():
    """`disconnect` removes the endpoint from the project network, which also takes the
    published port with it - a refusal rather than a hang. Both shapes exist because a
    client that handles one often mishandles the other."""
    stack_or_skip()
    import valkey
    with harness.Faults() as faults:
        faults.disconnect("valkey")
        with pytest.raises((valkey.exceptions.ConnectionError, valkey.exceptions.TimeoutError)):
            harness.valkey_client(socket_timeout=3).ping()
    harness.wait_valkey(timeout=60)


def test_cleanup_is_scoped_and_refuses_a_container_it_did_not_create():
    """The namespace guard against the live daemon: whatever else is running on this host,
    only `infrx-e2-*` containers owned by this compose project are touchable."""
    stack_or_skip()
    ours = harness.owned_containers()
    assert ours, "the stack should be up here"
    assert all(name.startswith(harness.PREFIX) for name in ours), ours
    import subprocess
    everything = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"],
                                capture_output=True, text=True, timeout=60).stdout.split()
    strangers = [name for name in everything if name not in ours]
    for stranger in strangers[:5]:
        with pytest.raises(harness.HarnessError):
            harness.assert_ours(stranger)
    assert harness.foreign_containers() == [], "an infrx-e2-* container we do not own exists"


def test_the_prefix_alone_is_not_enough_to_be_touchable():
    """The second gate, which the prefix check hides: a container NAMED like ours but not
    created by this compose project must still be refused, and must be reported rather than
    removed. Proved with a decoy in our own namespace - created here, never started, removed
    in `finally` - because a stranger's name already fails the first gate and so cannot test
    the second."""
    stack_or_skip()
    decoy = f"{harness.PREFIX}decoy"
    image = harness.compose_images()["valkey"]
    harness.run(["docker", "create", "--name", decoy, image, "true"], timeout=120)
    try:
        assert decoy not in harness.owned_containers(), "the decoy must not carry our label"
        assert harness.foreign_containers() == [decoy], \
            "a same-named container this project did not create must be reported"
        with pytest.raises(harness.HarnessError, match="not created by project"):
            harness.assert_ours(decoy)
    finally:
        harness.run(["docker", "rm", "-f", decoy], check=False, timeout=120)
    assert harness.foreign_containers() == []


# ------------------------------------------------------------------ canary

def test_a_stack_labelled_for_another_checkout_is_refused_not_destroyed():
    """r1 review B1.1: the compose PROJECT NAME is every checkout's. A container carrying our
    name and our project label but ANOTHER checkout's `ai.infrx.e2.checkout` must be reported
    and must make provisioning refuse - the reviewer's reproduction was a second checkout
    silently destroying and replacing a live stack, exit 0, all PASS.
    """
    stack_or_skip()
    decoy = f"{harness.PREFIX}otherco"
    image = harness.compose_images()["valkey"]
    harness.run(["docker", "create", "--name", decoy,
                 "--label", f"com.docker.compose.project={harness.PROJECT}",
                 "--label", f"{harness.CHECKOUT_LABEL}=/somewhere/else/tests/integration",
                 image, "true"], timeout=120)
    try:
        assert decoy not in harness.owned_containers(), "the decoy is not ours"
        reported = [item for item in harness.foreign("container") if item["name"] == decoy]
        assert reported and "another checkout" in reported[0]["why"], reported
        with pytest.raises(harness.HarnessError, match="another checkout"):
            harness.assert_ours(decoy)
        # And the gate in front of every create and every remove.
        with pytest.raises(harness.HarnessError, match="refusing to provision or tear down"):
            harness.assert_nothing_foreign()
    finally:
        harness.run(["docker", "rm", "-f", decoy], check=False, timeout=120)
    harness.assert_nothing_foreign()


def test_an_unlabelled_volume_with_our_name_is_refused_not_deleted():
    """r1 review B1.2: `down -v` deleted a volume it had not created, because the name was the
    one compose would have picked. A volume without our checkout label is foreign whatever it
    is called, and it must still exist afterwards.

    The gate itself is exercised here against the live daemon; that `up()` and `down()` call
    it is proved in `test_harness.py` with `compose` stubbed, because a test that really ran
    `down()` under a mutant which had removed the gate would destroy the stack every later
    case needs - and then those cases would skip and their mutants would "survive".
    """
    stack_or_skip()
    victim = f"{harness.PROJECT}_reviewer-probe"
    harness.run(["docker", "volume", "create", "--label", "reviewer=e2", victim], timeout=60)
    try:
        reported = [item for item in harness.foreign("volume") if item["name"] == victim]
        assert reported and f"no {harness.PROJECT} checkout label" in reported[0]["why"], reported
        with pytest.raises(harness.HarnessError, match="refusing to provision or tear down"):
            harness.assert_nothing_foreign()
        survived = harness.run(["docker", "volume", "inspect", victim], check=False, timeout=60)
        assert survived.returncode == 0, "a volume this harness did not create must survive"
        assert victim not in harness.owned("volume")
    finally:
        harness.run(["docker", "volume", "rm", "-f", victim], check=False, timeout=60)
    harness.assert_nothing_foreign()


def test_an_unlabelled_network_with_our_name_is_refused_not_removed():
    """r2 review B5: the volume case had a sibling for networks and it was missing, so
    "a same-named unlabelled network is a candidate" survived mutation. Same shape: a network
    named as compose would name ours, created by someone else, must be reported and survive."""
    stack_or_skip()
    victim = f"{harness.PROJECT}_reviewer-probe"
    harness.run(["docker", "network", "create", "--label", "reviewer=e2", victim], timeout=120)
    try:
        reported = [item for item in harness.foreign("network") if item["name"] == victim]
        assert reported and f"no {harness.PROJECT} checkout label" in reported[0]["why"], reported
        assert victim not in harness.owned("network")
        with pytest.raises(harness.HarnessError, match="refusing to provision or tear down"):
            harness.assert_nothing_foreign()
        survived = harness.run(["docker", "network", "inspect", victim], check=False, timeout=60)
        assert survived.returncode == 0, "a network this harness did not create must survive"
        # And the project's own network IS ours, so the check is not simply refusing everything.
        assert harness.NETWORK in harness.owned("network"), harness.owned("network")
    finally:
        harness.run(["docker", "network", "rm", victim], check=False, timeout=120)
    harness.assert_nothing_foreign()


def test_the_target_database_is_a_template_copy_owned_by_postgres():
    """r1 review R-a: `infrx_e2` created with TEMPLATE postgres OWNER postgres.

    Three things at once, because all three are what make it usable: the name matches D1's
    `current_database() like 'infrx\\_%'` clock gate, the Supabase `auth` schema came across
    with the template, and `postgres` owns it (otherwise `public` - owned by
    `pg_database_owner` - refuses CREATE and the migration cannot run at all).
    """
    stack_or_skip()
    with connect() as conn:
        name, owner = conn.execute(
            "select d.datname, pg_get_userbyid(d.datdba) from pg_database d"
            " where d.datname = current_database()").fetchone()
        assert name == harness.PG_DATABASE == f"infrx_{harness.NAMESPACE}"
        assert conn.execute("select current_database() like 'infrx\\_%'").fetchone()[0] is True, \
            "the name must match D1's clock gate"
        assert owner == harness.PG_USER, \
            f"owned by {owner}: `postgres` could not create in public and the migration fails"
        assert conn.execute("select to_regclass('auth.users') is not null").fetchone()[0], \
            "the template copy carries the image's auth schema"
        assert conn.execute(
            "select count(*) from pg_namespace where nspname = 'auth'").fetchone()[0] == 1
        # And production is NOT this name, which is the whole reason the gate works.
        assert harness.PG_TEMPLATE_SOURCE == "postgres" != harness.PG_DATABASE


def test_both_jwt_claim_forms_are_set_for_an_impersonated_principal():
    """r1 review R-b: the pinned image's `auth.uid()` reads the legacy per-claim GUC; hosted
    Supabase / PostgREST >= 10 set the JSON `request.jwt.claims`. A harness that sets one is
    silently nobody against the other, and then every deny-case passes for the wrong reason."""
    fixtures = stack_or_skip()
    principal = fixtures.user("member_alpha")
    import json
    with connect() as conn:
        with conn.transaction():
            conn.execute("set local role authenticated")
            pgstate.impersonate(conn, principal, "authenticated")
            legacy_sub, legacy_role, claims, uid = conn.execute(
                "select current_setting('request.jwt.claim.sub', true),"
                "       current_setting('request.jwt.claim.role', true),"
                "       current_setting('request.jwt.claims', true),"
                "       auth.uid()").fetchone()
        conn.execute("reset role")
    assert legacy_sub == str(principal), "the legacy per-claim GUC the pinned image reads"
    assert legacy_role == "authenticated"
    assert json.loads(claims) == {"sub": str(principal), "role": "authenticated"}, \
        "the JSON form hosted Supabase and PostgREST >= 10 read"
    assert str(uid) == str(principal), "auth.uid() is the principal, not NULL"


def test_a_matrix_that_authenticates_nobody_fails():
    """r1 review R-b: with `auth.uid()` NULL every policy that reads it denies everybody, so
    every "0 rows" case passes for the wrong reason. The gate that stops that must itself be
    provable, so this replaces `impersonate` with one that authenticates nobody and asserts the
    case is reported `no-identity` and `passed=False` - while the same case passes normally.
    """
    fixtures = stack_or_skip()
    case = pgstate.Check("E2-RLS-VACUOUS", "authenticated", "member_alpha",
                         "select count(*) from public.organizations where id = {beta}",
                         ("value", 0),
                         "cross-tenant denial - which is also what a NULL identity produces")
    with connect() as conn:
        honest = pgstate.run_check(conn, case, fixtures)
        assert honest["passed"] is True and honest["auth_uid"] == str(fixtures.user("member_alpha"))

        real = pgstate.impersonate
        pgstate.impersonate = lambda conn, user_id, role: None   # authenticate nobody
        try:
            vacuous = pgstate.run_check(conn, case, fixtures)
        finally:
            pgstate.impersonate = real
    assert vacuous["outcome"] == "no-identity", vacuous
    assert vacuous["passed"] is False, \
        "the statement still answers 0 rows; only the identity gate can tell the difference"
    assert "auth.uid() is None" in str(vacuous["observed"]), vacuous["observed"]


def test_canary_intentional_failure_is_detected_in_the_service_suite():
    if os.environ.get("INFRX_E2_CANARY") == "fail":
        raise AssertionError("E2 canary: this failure is intentional (INFRX_E2_CANARY=fail)")
    assert os.environ.get("INFRX_E2_CANARY") in (None, "", "off")
