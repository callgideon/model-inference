#!/usr/bin/env python3
"""C3F (FEEDBACK-ACK, LAB-ACCESS, DUR-RLS): both audiences' feedback doors on a REAL task-local stack.

The pinned Supabase PostgreSQL with every migration (D harness, task `app-c3f`: container
`infrx-app-c3f-postgres-supabase` on 127.0.0.1:57509), plus `proposed_doors.sql` (WR-C3F-1) when
no migration defines the doors yet. The world is lab-sql's L2-SQL one (`test_l2sql_access.seed`:
two consumers, NEMO with a developer, an administrator and a viewer, OTHER with CONSUMER_2 as a
developer) with the `feedback` flag on, one CREDIT job per consumer on NEMO's model, and grants:
C1 -> NEMO (the job's model, `feedback`, `provider_sharing`), C2 -> NEMO granted then REVOKED,
C1 -> OTHER (OTHER's own model only). JOB_1 already carries an API signal and an operator label.

    cd apps/infrx-api && INFRX_D_TASK=app-c3f INFRX_D1_IMAGE=supabase \\
        uv run --frozen python ../app/tests/c/feedback/stack.py [--mutants]

Order: the `check_*` below as the browser roles (psycopg, `checks._jwt`), then the two adapters
through PostgREST v13.0.4 + supabase-js (`apps/app/tests/c/feedback/feedback-postgrest.test.ts`,
`apps/lab/tests/c/review/review-postgrest.test.ts`), then the committed revocation. `--mutants`
applies each SQL mutant of the doors to a fresh world and requires a named check to fail.
Everything is labelled with this checkout and removed at exit; nothing hosted is touched.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

os.environ.setdefault("INFRX_D_TASK", "app-c3f")
os.environ.setdefault("INFRX_D1_IMAGE", "supabase")
APP = Path(__file__).resolve().parents[3]
API, LAB = APP.parent / "infrx-api", APP.parent / "lab"
sys.path.insert(0, str(API))

import httpx  # noqa: E402
import psycopg  # noqa: E402
from infrx.contracts import errors  # noqa: E402
from infrx.contracts.conformance import builders as b  # noqa: E402
from infrx.lab.access import LabAccess  # noqa: E402
from infrx.state import migrations  # noqa: E402
from infrx.state.jobstore import connector  # noqa: E402
from infrx.state.lab_access import PgAccessStore  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402
from tests.d import checks, pgharness  # noqa: E402
from tests.d import checks_admission as ca  # noqa: E402
from tests.d import checks_credit as cc  # noqa: E402
from tests.d import test_l2sql_access as t  # noqa: E402

assert pgharness.SERVICE.host_port == 57509 and pgharness.ON_SUPABASE, \
    "run with INFRX_D_TASK=app-c3f INFRX_D1_IMAGE=supabase (this lane's port, the real image)"

POSTGREST = ("postgrest/postgrest@sha256:"
             "a312f4b2e48530a01fc26f5310d547d6c26d087858360e164522e415723a7732")  # v13.0.4
NAME, NETWORK, DB_ALIAS = "infrx-app-c3f-postgrest", "infrx-app-c3f-net", "infrx-app-c3f-db"
LABEL = "ai.infrx.app-c3f.checkout"
JWT_SECRET = "infrx-app-c3f-local-jwt-secret-not-a-real-one"
AUTHN_PASSWORD = "infrx-app-c3f-authenticator-local"
DB = f"{pgharness.DATABASE}_c3f"
# WR-LSQ-7: once a 0038_* migration exists the doors are the migration's (body byte-identical to
# proposed_doors.sql), so build() applies nothing and --mutants mutates the real migration.
DOORS = next(migrations.DIR.glob("0038_*.sql"), Path(__file__).with_name("proposed_doors.sql"))
C1, C2, DEV, ADMIN, VIEWER, BOTH = t.C1, t.BOTH, t.DEV, t.ADMIN, t.VIEWER, t.BOTH
NEMO, OTHER, MODEL = t.NEMO, t.OTHER, t.MODEL
UNKNOWN = "9f000000-0000-4000-8000-00000000009f"
LEAVER = "5e000000-0000-4000-8000-000000000001"     # a NEMO developer revoked a day ago
REVIEW_KEYS = {"feedback_id", "request_id", "name", "value", "comment", "author_role", "channel",
               "created_at"}


# ------------------------------------------------------------------------------ world
def grant(conn, owner: str, provider: str, model: str, **over) -> None:
    conn.execute("select infrx.lab_put_access_grant(%s)", (Jsonb({
        "actor_user_id": owner, "grantor_org_id": cc.personal_org(conn, owner),
        "recipient_provider_org_id": provider, "model_ids": [model], "categories": ["feedback"],
        "purposes": ["provider_sharing"], "retention_days": 30, **over}),))


def revoke(conn, owner: str, provider: str) -> None:
    conn.execute("select infrx.lab_revoke_access_grant(%s)", (Jsonb({
        "actor_user_id": owner, "grantor_org_id": cc.personal_org(conn, owner),
        "recipient_provider_org_id": provider}),))


def seed(conn) -> dict:
    t.seed(conn)
    conn.execute("insert into auth.users (id, email) values (%s, 'leaver@example.com')", (LEAVER,))
    conn.execute("insert into infrx.provider_memberships (provider_org_id, user_id, role, "
                 "granted_by, granted_at, revoked_at) values (%s, %s, 'developer', 'ops', "
                 "infrx.now() - interval '2 days', infrx.now() - interval '1 day')", (NEMO, LEAVER))
    conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) values "
                 "('feedback', true, 'rig', 'C3F') on conflict (name) do update set enabled = true")
    world, jobs = ca.World(conn), {}
    for name, user, key in (("job_1", C1, ca.C1_KEY), ("job_2", C2, ca.C2_KEY)):
        request = ca.credit_request(world, key, cc.personal_org(conn, user))
        ca.admit(conn, request, b.idem(request, f"c3f-{name}"), regime="credit")
        jobs[name] = request.request_id
    grant(conn, C1, NEMO, MODEL)
    grant(conn, C2, NEMO, MODEL)
    revoke(conn, C2, NEMO)                                          # revoked sharing
    grant(conn, C1, OTHER, t.OTHER_MODEL)                           # another provider's model
    org1 = cc.personal_org(conn, C1)
    conn.execute("select infrx.accept_feedback(%s)", (Jsonb({
        "org_id": org1, "principal": ca.C1_KEY, "by_operator": False, "channel": "api",
        "request_id": jobs["job_1"], "feedback_id": "fb_" + "a" * 43,
        "body": {"name": "thumb", "value": True},
        "idem": {"org_id": org1, "operation": "feedback", "key": "c3f-api",
                 "payload_hash": "sha256:" + "ab" * 32}}),))
    conn.execute("select infrx.label_calibration(%s)", (Jsonb({
        "principal": "ops@infrx", "is_operator": True, "request_id": jobs["job_1"],
        "feedback_id": "fb_" + "b" * 43, "label": "incorrect", "rubric_version": 2,
        "idem": {"org_id": org1, "operation": "calibration.label", "key": "c3f-label",
                 "payload_hash": "sha256:" + "cd" * 32}}),))
    return {"jobs": jobs, "orgs": {"c1": org1, "c2": cc.personal_org(conn, C2)},
            "users": {"c1": C1, "c2": C2, "dev": DEV, "admin": ADMIN, "viewer": VIEWER,
                      "both": BOTH}, "providers": {"nemo": NEMO, "other": OTHER},
            "unknown": UNKNOWN}


def doors_sql() -> str:
    return DOORS.read_text()


def build(sql: str | None = None) -> dict:
    """A fresh database: every migration, the doors (unless a migration has them), the world."""
    pgharness.recreate(DB)
    # A mutant of the migration's doors REPLACES the migration (a re-run over it would keep its
    # grants), so the mutated file is what the world is built from.
    real = DOORS.parent == migrations.DIR
    pgharness.apply(DB, tuple((name, sql if real and sql is not None and name == DOORS.name
                                     else body)
                              for name, body in migrations.sql_for(shim=pgharness.NEEDS_SHIM)))
    with pgharness.connect(DB) as conn:
        have = conn.execute("select to_regprocedure('public.submit_feedback(jsonb)') is not null "
                            "and to_regprocedure('public.lab_review_feedback(jsonb)') is not null"
                            ).fetchone()[0]
        if (sql is not None and not real) or not have:
            conn.execute(sql if sql is not None else doors_sql())
        return seed(conn)


# ----------------------------------------------------------------------------- sessions
def as_user(conn, user: str | None, door: str, args, *, keep: bool = False):
    """(answer, None) or (None, refusal) of one door call as `user`'s browser session (None =
    anon, "service" = the platform role): the domain code of a P0001 refusal, else the
    SQLSTATE. Committed only with `keep`."""
    role = {None: "set local role anon", "service": "set local role service_role"}
    try:
        with conn.transaction():
            conn.execute(role.get(user) or checks._jwt(user))
            answer = conn.execute(f"select public.{door}(%s)", (Jsonb(args),)).fetchone()[0]
            if not keep:
                raise psycopg.Rollback()
    except psycopg.Error as refused:
        code = re.match(r"(\w+): ", str(refused)) if refused.sqlstate == "P0001" else None
        return None, code.group(1) if code else refused.sqlstate
    return answer, None


def submit(conn, user, job, key, *, keep=True, **signal):
    return as_user(conn, user, "submit_feedback", {"request_id": job, "idempotency_key": key,
                                                   **(signal or {"name": "rating", "value": 4})},
                   keep=keep)


def review(conn, user, provider, job):
    return as_user(conn, user, "lab_review_feedback", {"provider_org_id": provider,
                                                       "request_id": job})


def count(conn, sql, *params) -> int:
    return conn.execute(sql, params).fetchone()[0]


def port_allows(user: str, provider: str, grantor_org: str) -> bool:
    access = LabAccess(PgAccessStore(connector(pgharness.dsn(DB))))
    try:
        asyncio.run(access.authorize_content(
            user_id=user, provider_org_id=provider, grantor_org_id=grantor_org, model_id=MODEL,
            category="feedback", purpose="provider_sharing"))
    except (errors.Forbidden, errors.NotFound):
        return False
    return True


# ------------------------------------------------------------------------------- checks
def check_only_signed_in_sessions_reach_the_doors(conn, w) -> str:
    """DUR-RLS: anon is refused (42501) on both doors; `authenticated` is the only browser
    grantee; the platform role, with no auth.uid(), is `not_found` on both."""
    for door, args in (("submit_feedback", {"request_id": w["jobs"]["job_1"],
                                            "idempotency_key": "x", "name": "thumb",
                                            "value": True}),
                       ("lab_review_feedback", {"provider_org_id": NEMO,
                                                "request_id": w["jobs"]["job_1"]})):
        assert as_user(conn, None, door, args)[1] == "42501", f"anon reached {door}"
        acl = conn.execute("select coalesce(proacl::text, '') from pg_proc where oid = "
                           "%s::regprocedure", (f"public.{door}(jsonb)",)).fetchone()[0]
        grantees = {item.split("=", 1)[0] for item in acl.strip("{}").split(",") if item}
        assert grantees == {"postgres", "authenticated", "service_role"}, f"{door}: {acl}"
        assert as_user(conn, "service", door, args)[1] == "not_found", door
    return "anon 42501 on both; authenticated only; platform role not_found"


def check_own_feedback_is_acknowledged_once_durably(conn, w) -> str:
    """FEEDBACK-ACK: the individual's signal on their own request is ONE committed row with the
    server's provenance (author = auth.uid(), customer, console, no operator marker, no
    calibration), its idempotency record under `feedback.submit` and ONE outbox event; the same
    key and signal replays that row (a lost ack is safe); another signal under it conflicts."""
    job, key = w["jobs"]["job_1"], f"c3f-ack-{uuid.uuid4()}"
    row, why = submit(conn, C1, job, key, name="rating", value=4, comment="useful")
    assert why is None, f"refused: {why}"
    assert (row["author_principal"], row["author_role"], row["channel"], row["by_operator"],
            row["calibration_set"], row["org_id"]) == (C1, "customer", "console", False, False,
                                                       w["orgs"]["c1"]), row
    again, why = submit(conn, C1, job, key, name="rating", value=4, comment="useful")
    assert why is None and again["feedback_id"] == row["feedback_id"], (again, why)
    fid = row["feedback_id"]
    assert count(conn, "select count(*) from infrx.feedback where feedback_id = %s", fid) == 1
    assert count(conn, "select count(*) from infrx.idempotency where feedback_id = %s and "
                       "operation = 'feedback.submit' and key = %s", fid, key) == 1
    assert count(conn, "select count(*) from infrx.outbox where kind = 'feedback_projection' "
                       "and payload->>'feedback_id' = %s", fid) == 1
    _, why = submit(conn, C1, job, key, name="rating", value=5, comment="useful")
    assert why == "idempotency_conflict", why
    return "one row + key + event; replay = same row; changed signal = conflict"


def check_nothing_but_the_signal_is_the_callers(conn, w) -> str:
    """A forged request id (another org's), an unknown or malformed one, a smuggled
    provenance field and a label name write nothing; a member of the job's org who is an
    operator is still a customer, with R41's marker set by the server."""
    before = count(conn, "select count(*) from infrx.feedback")
    for job in (w["jobs"]["job_2"], UNKNOWN, "5c000000"):
        assert submit(conn, C1, job, f"c3f-{uuid.uuid4()}")[1] == "not_found", job
    for smuggled in ({"author_role": "operator"}, {"channel": "api"}, {"org_id": w["orgs"]["c1"]},
                     {"by_operator": True}, {"calibration_set": True}):
        _, why = as_user(conn, C1, "submit_feedback", {
            "request_id": w["jobs"]["job_1"], "idempotency_key": "s", "name": "thumb",
            "value": True, **smuggled})
        assert why == "invalid_request", (smuggled, why)
    assert submit(conn, C1, w["jobs"]["job_1"], "lbl", name="calibration_label",
                  value="correct")[1] == "invalid_request"
    assert count(conn, "select count(*) from infrx.feedback") == before, "a refusal wrote"
    with conn.transaction():
        conn.execute("update public.profiles set is_operator = true where id = %s", (C2,))
        row, why = submit(conn, C2, w["jobs"]["job_2"], "op", keep=True)
        assert why is None and (row["author_role"], row["by_operator"]) == ("customer", True), \
            (row, why)
        raise psycopg.Rollback()
    return "forged/unknown/malformed not_found; 5 provenance fields + a label refused; op marker"


def check_review_is_the_l2_port_answer(conn, w) -> str:
    """LAB-ACCESS, one answer: for every (user, provider, request) the door returns rows exactly
    when L2's port (`LabAccess.authorize_content`, feedback, provider_sharing, the job's model)
    allows it: the developer and administrator of NEMO on C1's shared request, nobody else - not
    the viewer (forbidden), not NEMO on C2's REVOKED grant, not OTHER (its grant names only its
    own model), not a consumer or a non-member (not_found, a forged workspace), not an unknown
    request. Rows are the customer signals only (no label), with no identity column."""
    allowed = []
    for user in (DEV, ADMIN, VIEWER, BOTH, C1, LEAVER):
        for provider in (NEMO, OTHER):
            for name, job in (*w["jobs"].items(), ("unknown", UNKNOWN)):
                rows, why = review(conn, user, provider, job)
                org = {"job_1": w["orgs"]["c1"], "job_2": w["orgs"]["c2"]}.get(name)
                port = org is not None and port_allows(user, provider, org)
                assert (rows is not None) == port, (user, provider, name, rows, why, port)
                if rows is None:
                    expect = "forbidden" if (user, provider) == (VIEWER, NEMO) else "not_found"
                    assert why == expect, (user, provider, name, why)
                else:
                    allowed.append((user, provider, name))
                    assert rows and all(set(r) == REVIEW_KEYS and r["author_role"] == "customer"
                                        and r["name"] != "calibration_label" for r in rows), rows
    assert sorted(allowed) == sorted([(DEV, NEMO, "job_1"), (ADMIN, NEMO, "job_1")]), allowed
    return f"36 cases, door == port; allowed {len(allowed)}: NEMO dev+admin on job_1"


def check_a_narrower_or_expiring_grant_shares_nothing(conn, w) -> str:
    """The CURRENT version decides: a new version without `provider_sharing`, without
    `feedback`, or past its expiry (on the database clock) denies the next review."""
    job = w["jobs"]["job_1"]
    for over in ({"purposes": ["training"]}, {"categories": ["request_content"]},
                 {"expires_at": None}):
        with conn.transaction():
            if over.get("expires_at", 0) is None:
                expiry = conn.execute("select infrx.now() + interval '1 hour'").fetchone()[0]
                grant(conn, C1, NEMO, MODEL, expires_at=expiry.isoformat())
                assert review(conn, DEV, NEMO, job)[0] is not None, "unexpired grant denied"
                conn.execute("select infrx_test.advance(3600)")
            else:
                grant(conn, C1, NEMO, MODEL, **over)
            assert review(conn, DEV, NEMO, job)[1] == "not_found", over
            raise psycopg.Rollback()
    return "no provider_sharing / no feedback / expired: not_found"


def check_a_revocation_denies_the_very_next_review(conn, w) -> str:
    """Revoked sharing, committed: allowed before, `not_found` on the next call, and the L2
    port agrees. Commits: runs last."""
    job = w["jobs"]["job_1"]
    assert review(conn, DEV, NEMO, job)[0] is not None
    revoke(conn, C1, NEMO)
    assert review(conn, DEV, NEMO, job)[1] == "not_found", "a revoked grant still shares"
    assert not port_allows(DEV, NEMO, w["orgs"]["c1"]), "the L2 port disagrees"
    return "allowed, revoked, next call not_found; port agrees"


CHECKS = (check_only_signed_in_sessions_reach_the_doors,
          check_own_feedback_is_acknowledged_once_durably,
          check_nothing_but_the_signal_is_the_callers,
          check_review_is_the_l2_port_answer,
          check_a_narrower_or_expiring_grant_shares_nothing)
LAST = check_a_revocation_denies_the_very_next_review

# Each decision of the doors, broken once; a named check must fail on it (R32).
MUTANTS = (
    ("no signed-in session may send feedback",
     "grant execute on function public.submit_feedback(jsonb) to authenticated;", "",
     [check_only_signed_in_sessions_reach_the_doors, check_own_feedback_is_acknowledged_once_durably]),
    ("no signed-in session may review",
     "grant execute on function public.lab_review_feedback(jsonb) to authenticated;", "",
     [check_only_signed_in_sessions_reach_the_doors, check_review_is_the_l2_port_answer]),
    ("anon may send feedback", "grant execute on function public.submit_feedback(jsonb) to authenticated;",
     "grant execute on function public.submit_feedback(jsonb) to authenticated, anon;",
     [check_only_signed_in_sessions_reach_the_doors]),
    ("feedback on any org's request", "where j.request_id = v_job and public.is_org_member(j.org_id);",
     "where j.request_id = v_job;", [check_nothing_but_the_signal_is_the_callers,
                                     check_only_signed_in_sessions_reach_the_doors]),
    ("a smuggled provenance key is dropped, not refused",
     "where k not in ('request_id', 'name', 'value', 'comment', 'idempotency_key'))",
     "where false)", [check_nothing_but_the_signal_is_the_callers]),
    ("the author is not the session", "'principal', auth.uid()::text,", "'principal', 'platform',",
     [check_own_feedback_is_acknowledged_once_durably]),
    ("the operator marker is never set", "'by_operator', public.is_operator(),",
     "'by_operator', false,", [check_nothing_but_the_signal_is_the_callers]),
    ("console feedback claims the api channel", "'channel', 'console', 'request_id', v_job,",
     "'channel', 'api', 'request_id', v_job,", [check_own_feedback_is_acknowledged_once_durably]),
    ("console and api share one key scope", "'operation', 'feedback.submit',",
     "'operation', 'feedback',", [check_own_feedback_is_acknowledged_once_durably]),
    ("the digest ignores the signal", "(v_body || jsonb_build_object('request_id', v_job))::text",
     "jsonb_build_object('request_id', v_job)::text",
     [check_own_feedback_is_acknowledged_once_durably]),
    ("a revoked membership still reviews",
     "and (m.revoked_at is null or infrx.now() < m.revoked_at)) then\n    perform infrx.refuse('not_found'",
     ") then\n    perform infrx.refuse('not_found'", [check_review_is_the_l2_port_answer]),
    ("a viewer reviews", "and m.role in ('developer', 'administrator')) then",
     ") then", [check_review_is_the_l2_port_answer]),
    ("a non-member is told forbidden, not not_found (an oracle)",
     "perform infrx.refuse('not_found', 'no such provider workspace');",
     "perform infrx.refuse('forbidden', 'no such provider workspace');",
     [check_review_is_the_l2_port_answer]),
    ("a revoked grant still shares", "and (g.revoked_at is null or infrx.now() < g.revoked_at)",
     "", [check_review_is_the_l2_port_answer, check_a_revocation_denies_the_very_next_review]),
    ("an expired grant still shares", "and (g.expires_at is null or infrx.now() < g.expires_at)",
     "", [check_a_narrower_or_expiring_grant_shares_nothing]),
    ("any model of the grant covers the job", "and j.model_id::text = any(g.model_ids)", "",
     [check_review_is_the_l2_port_answer]),
    ("any category covers feedback", "and 'feedback' = any(g.categories)", "",
     [check_a_narrower_or_expiring_grant_shares_nothing]),
    ("any purpose covers review", "and 'provider_sharing' = any(g.purposes)", "",
     [check_a_narrower_or_expiring_grant_shares_nothing]),
    ("an old grant version still decides", "order by g.version desc limit 1", "order by g.version limit 1",
     [check_review_is_the_l2_port_answer, check_a_narrower_or_expiring_grant_shares_nothing]),
    ("the grant may be another provider's", "and g.recipient_provider_org_id = v_provider", "",
     [check_review_is_the_l2_port_answer]),
    ("labels are reviewed instead", "'request_id', v_job, 'org_id', v_org)))",
     "'request_id', v_job, 'org_id', v_org, 'calibration', true)))",
     [check_review_is_the_l2_port_answer]),
    ("the customer's principal is disclosed", "'created_at', f->'created_at') order by n)",
     "'created_at', f->'created_at', 'author_principal', f->'author_principal') order by n)",
     [check_review_is_the_l2_port_answer]),
)


def run_checks(conn, world, checks_=CHECKS) -> list[str]:
    failed = []
    for check in checks_:
        try:
            print(f"ok   {check.__name__}: {check(conn, world)}")
        except AssertionError as broken:
            failed.append(check.__name__)
            print(f"FAIL {check.__name__}: {str(broken)[:300]}")
    return failed


# ----------------------------------------------------------------------------- PostgREST
def _docker(*args, check=True):
    return subprocess.run(("docker", *args), capture_output=True, text=True, check=check)


def _ours(name: str, kind: str) -> bool | None:
    probe = _docker(kind, "inspect", "-f", "{{json .Config.Labels}}" if kind == "container"
                    else "{{json .Labels}}", name, check=False)
    if probe.returncode != 0:
        return None
    return (json.loads(probe.stdout.strip() or "null") or {}).get(LABEL) == pgharness.checkout()


def up() -> str:
    for name, kind in ((NAME, "container"), (NETWORK, "network")):
        if _ours(name, kind) is False:
            raise SystemExit(f"refusing to touch {kind} {name}: not created by this checkout")
    _docker("rm", "-f", NAME, check=False)
    if _ours(NETWORK, "network") is None:
        _docker("network", "create", "--label", f"{LABEL}={pgharness.checkout()}", NETWORK)
    _docker("network", "connect", "--alias", DB_ALIAS, NETWORK, pgharness.CONTAINER, check=False)
    pgharness._sb("postgres", f"alter role authenticator with login password '{AUTHN_PASSWORD}'")
    # As in C3A's stack: the hosted `auth.uid()` reads `request.jwt.claims`.
    pgharness._sb(DB, "create or replace function auth.uid() returns uuid language sql stable "
                      "as $f$ select coalesce(nullif(current_setting('request.jwt.claim.sub', "
                      "true), ''), nullif(nullif(current_setting('request.jwt.claims', true), "
                      "'')::jsonb ->> 'sub', ''))::uuid $f$")
    _docker("run", "-d", "--name", NAME, "--network", NETWORK,
            "--label", f"{LABEL}={pgharness.checkout()}",
            "-e", f"PGRST_DB_URI=postgres://authenticator:{AUTHN_PASSWORD}@{DB_ALIAS}:5432/{DB}",
            "-e", "PGRST_DB_SCHEMAS=public", "-e", "PGRST_DB_ANON_ROLE=anon",
            "-e", f"PGRST_JWT_SECRET={JWT_SECRET}", POSTGREST)
    address = _docker("inspect", "-f", "{{(index .NetworkSettings.Networks \"" + NETWORK
                      + "\").IPAddress}}", NAME).stdout.strip()
    base = f"http://{address}:3000"
    for _ in range(60):
        try:
            if httpx.get(base + "/", timeout=2).status_code < 500:
                return base
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"{NAME} never answered")


def down() -> None:
    if _ours(NAME, "container"):
        _docker("rm", "-f", NAME, check=False)
    _docker("network", "disconnect", NETWORK, pgharness.CONTAINER, check=False)
    if _ours(NETWORK, "network"):
        _docker("network", "rm", NETWORK, check=False)


def adapters(world: dict) -> int:
    base = up()
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({**world, "url": base, "jwt_secret": JWT_SECRET}, handle)
        env = {**os.environ, "INFRX_C3F_STACK": handle.name}
        codes = [subprocess.run(("node", "--test", test), cwd=root, env=env).returncode
                 for root, test in ((APP, "tests/c/feedback/feedback-postgrest.test.ts"),
                                    (LAB, "tests/c/review/review-postgrest.test.ts"),
                                    (LAB, "tests/v/detail/feedback-postgrest.test.ts"))]
        os.unlink(handle.name)
        return max(codes)
    finally:
        down()


# ---------------------------------------------------------------------------------- main
def mutants() -> int:
    pristine, survivors = doors_sql(), 0
    for what, find, replace, named in MUTANTS:
        if pristine.count(find) != 1:
            print(f"STALE {what}: find matches {pristine.count(find)} times")
            survivors += 1
            continue
        world = build(pristine.replace(find, replace))
        with pgharness.connect(DB) as conn:
            failed = run_checks(conn, world, (*CHECKS, LAST))
        hit = [c.__name__ for c in named if c.__name__ in failed]
        survivors += not hit
        print(f"{'killed    ' if hit else 'NOT KILLED'} {what} ({', '.join(hit) or failed})")
    names = {c.__name__ for m in MUTANTS for c in m[3]}
    unnamed = [c.__name__ for c in (*CHECKS, LAST) if c.__name__ not in names]
    for name in unnamed:
        print(f"FAIL no mutant names {name}")
    print(f"\n{len(MUTANTS)} SQL mutants, {len(MUTANTS) - survivors} killed; every check named: "
          f"{not unnamed}")
    return 1 if survivors or unnamed else 0


def main() -> int:
    pgharness.ensure()
    if "--mutants" in sys.argv:
        return mutants()
    world = build()
    with pgharness.connect(DB) as conn:
        failed = run_checks(conn, world)
    code = adapters(world)
    with pgharness.connect(DB) as conn:
        failed += run_checks(conn, world, (LAST,))
    print(f"\n{len(CHECKS) + 1} checks, {len(failed)} failed; adapters exit {code}")
    return 1 if failed or code else 0


if __name__ == "__main__":
    sys.exit(main())
