"""The invariants D1 claims, as functions a test and a mutant runner both call.

Every check raises `AssertionError` on failure and returns a short summary string on
success. They live here rather than inside the pytest module because R32 requires each
claimed invariant to be killable: `test_migration_mutants.py` applies a single edit to
a copy of a migration, rebuilds a database from it, and runs the same function, which
must then fail. A check that only exists as a pytest body cannot be reused that way.
"""
from __future__ import annotations

from decimal import Decimal

import psycopg

def _first_line(error: BaseException) -> str:
    return f"{type(error).__name__}: {str(error).strip().splitlines()[0][:140]}"


# --- fixture identities (fixed so failures name the same row twice) ------------
ORG_A = "0a000000-0000-4000-8000-00000000000a"
ORG_B = "0b000000-0000-4000-8000-00000000000b"
USER_MEMBER = "11111111-1111-4111-8111-111111111111"
USER_OWNER = "22222222-2222-4222-8222-222222222222"
USER_OPERATOR = "33333333-3333-4333-8333-333333333333"
# ORG_B's own owner, so the ORG_A sessions belong to exactly one organization
# and "a session of one org sees another org's rows" is a real question.
USER_OTHER = "77777777-7777-4777-8777-777777777777"
USER_SECOND_OWNER = "88888888-8888-4888-8888-888888888888"
KEY_A = "44444444-4444-4444-8444-444444444444"
JOB_PREPARING = "50000000-0000-4000-8000-000000000000"
# Childless AND keyless: the only thing that can refuse moving it to another
# organization is `jobs_guard` (B9/m16 - the old target was refused by the
# api-key foreign key, so the mutant that dropped the guard survived).
JOB_NOKEY = "50000000-0000-4000-8000-00000000000e"
JOB_QUEUED = "50000000-0000-4000-8000-000000000001"
JOB_RUNNING = "50000000-0000-4000-8000-000000000002"
JOB_TERMINAL = "50000000-0000-4000-8000-000000000003"
JOB_B = "50000000-0000-4000-8000-0000000000bb"
RUN_ID = "60000000-0000-4000-8000-000000000001"
EVENT_ID = "70000000-0000-4000-8000-000000000001"
DEST_ID = "80000000-0000-4000-8000-000000000001"
DIGEST = "sha256:" + "ab" * 32
PERIOD = "2026-09-01T00:00:00Z"

#: Every relation 06 asks D1 to create, as `schema.name`.
EXPECTED_RELATIONS = (
    "infrx.price_versions", "infrx.wallets", "infrx.credit_holds",
    "infrx.capacity_reservations", "infrx.jobs", "infrx.attempts",
    "infrx.staged_media", "infrx.job_media", "infrx.idempotency",
    "infrx.stream_chunks", "infrx.outbox", "infrx.feedback", "infrx.consent_history",
    "infrx.judge_budgets", "infrx.judge_reservations", "infrx.judge_runs",
    "infrx.judge_samples", "infrx.callback_destinations", "infrx.callback_deliveries",
    "infrx.org_entitlements", "infrx.audit_entries",
)

#: The console read surface of 0005 (views in `public`, owner's rights over `infrx`).
EXPECTED_VIEWS = (
    "public.wallets", "public.console_ledger", "public.console_usage",
    "public.org_settings", "public.consent_history", "public.feedback",
    "public.calibration_labels", "public.console_judge_runs",
    "public.console_admin_orgs", "public.operator_audit",
    "infrx.wallet_reconciliation",
)

#: 06 §"Mutation boundaries". `grant` is `grant_credit` (reserved word).
RPC_NAMES = ("admit", "prepare", "claim", "heartbeat", "append", "cancel", "terminalize",
             "grant_credit", "accept_feedback", "reserve_judge", "record_submission")

_JOB_COLUMNS = """
  request_id, job_handle, org_id, key_id, model_revision, execution_mode, state,
  operation, payload_ref, payload_digest, max_input_tokens, max_output_tokens,
  price_version, price_snapshot, maximum_hold, consent_version, trace_mode,
  admitted_at, deadline_at, budget_preparation_s, budget_queue_wait_s,
  budget_generation_s, budget_first_token_s, budget_stall_s, preparation_deadline_at
"""


def _job_values(request_id: str, handle: str, *, org: str = ORG_A, state: str = "queued",
                extra: str = "") -> str:
    return f"""insert into infrx.jobs ({_JOB_COLUMNS}{extra and ', ' + extra})
    values ('{request_id}', '{handle}', '{org}',
      {f"'{KEY_A}'" if org == ORG_A else 'null'},
      'nemostation/marlin-2b', 'stream', '{state}', 'chat.completions',
      'infrx-payload:{request_id}', '{DIGEST}', 4096, 512,
      'pv-1', '{{"price_version":"pv-1"}}'::jsonb, 1.25000000, 1, 'full',
      '2026-09-21T00:00:00Z', '2026-09-21T00:10:00Z', 120, 10, 300, 60, 20,
      '2026-09-21T00:02:00Z'"""


# --- seeding -----------------------------------------------------------------
def seed_legacy(conn) -> dict:
    """The upgrade fixture 06 asks for: organizations, members, keys, positive AND
    negative ledger history, and usage. Seeded against the CURRENT schema (0001/0002),
    before the pilot migrations run."""
    conn.execute(f"""
    insert into auth.users (id, email) values
      ('{USER_OWNER}', 'owner@example.com'), ('{USER_MEMBER}', 'member@example.com'),
      ('{USER_OPERATOR}', 'operator@example.com');
    update public.profiles set is_operator = true where id = '{USER_OPERATOR}';
    -- The signup trigger made a personal org per user; name the first two for the tests.
    """)
    orgs = [row[0] for row in conn.execute(
        "select id from public.organizations order by created_at, id").fetchall()]
    org_a, org_b = str(orgs[0]), str(orgs[1])
    conn.execute(f"""
    insert into public.org_members (org_id, user_id, role)
      values ('{org_a}', '{USER_MEMBER}', 'member') on conflict do nothing;
    insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash)
      values ('{KEY_A}', '{org_a}', '{USER_OWNER}', 'legacy', 'sk-infrx-aaaaaaaa',
              'hash-a');
    insert into public.credit_ledger (org_id, delta_usd, kind, reason) values
      ('{org_a}',  100.500000, 'grant', 'pilot credit'),
      ('{org_a}',   -0.123456, 'usage', 'legacy usage'),
      ('{org_a}',   -0.000001, 'adjustment', 'rounding'),
      ('{org_a}',   25.000000, 'purchase', 'legacy purchase'),
      ('{org_b}',  -12.345678, 'usage', 'negative history');
    insert into public.usage_events
      (id, org_id, api_key_id, model_id, status, stream, prompt_tokens,
       completion_tokens, cost_usd)
    values
      ('90000000-0000-4000-8000-000000000001', '{org_a}', '{KEY_A}',
       'nemostation/marlin-2b', 200, true, 1000, 250, 0.00012345),
      ('90000000-0000-4000-8000-000000000002', '{org_a}', '{KEY_A}',
       'nemostation/marlin-2b', 500, false, 10, 0, 0),
      ('90000000-0000-4000-8000-000000000003', '{org_b}', null,
       'nemostation/marlin-2b', 429, false, null, null, 1.99999999);
    """)
    return capture_legacy(conn, org_a, org_b)


def capture_legacy(conn, org_a: str, org_b: str) -> dict:
    """Every value the upgrade must not change."""
    return {
        "org_a": org_a, "org_b": org_b,
        "ledger": conn.execute("""select id, delta_usd, delta_usd::text, kind, org_id
                                  from public.credit_ledger order by id""").fetchall(),
        "ledger_sums": conn.execute("""select org_id, sum(delta_usd) from public.credit_ledger
                                       group by org_id order by org_id""").fetchall(),
        "usage": conn.execute("""select id, status, cost_usd, cost_usd::text,
                                        prompt_tokens, completion_tokens
                                 from public.usage_events order by id""").fetchall(),
        "usage_sum": conn.execute("select sum(cost_usd) from public.usage_events").fetchone()[0],
        "balances": conn.execute("""select org_id, sum(delta_usd) from public.credit_ledger
                                    group by org_id order by org_id""").fetchall(),
    }


def seed_fixtures(conn) -> None:
    """Rows the constraint, role and plan checks mutate against, on a fresh database."""
    conn.execute(f"""
    insert into auth.users (id, email) values
      ('{USER_OWNER}', 'owner@example.com'), ('{USER_MEMBER}', 'member@example.com'),
      ('{USER_OPERATOR}', 'operator@example.com'), ('{USER_OTHER}', 'other@example.com'),
      ('{USER_SECOND_OWNER}', 'second-owner@example.com');
    update public.profiles set is_operator = true where id = '{USER_OPERATOR}';
    insert into public.organizations (id, name, slug, created_by)
      values ('{ORG_A}', 'org a', 'org-a', '{USER_OWNER}'),
             ('{ORG_B}', 'org b', 'org-b', '{USER_OTHER}');
    insert into public.org_members (org_id, user_id, role) values
      ('{ORG_A}', '{USER_OWNER}', 'owner'), ('{ORG_A}', '{USER_MEMBER}', 'member'),
      -- A SECOND owner, because an organization with two of them used to appear twice in
      -- `console_admin_orgs` and C's keyset pagination would skip a page (B1).
      ('{ORG_A}', '{USER_SECOND_OWNER}', 'owner'),
      ('{ORG_B}', '{USER_OTHER}', 'owner');
    insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash)
      values ('{KEY_A}', '{ORG_A}', '{USER_OWNER}', 'k', 'sk-infrx-aaaaaaaa', 'hash-a');
    insert into infrx.price_versions
      (price_version, model_revision, input_rate_per_million, output_rate_per_million,
       token_rules_version, effective_from)
      values ('pv-1', 'nemostation/marlin-2b', 0.10000000, 0.30000000, 'tr-1',
              '2026-01-01T00:00:00Z');
    insert into infrx.consent_history
      (org_id, consent_version, trace_mode, content_retention_days, evaluation_consent,
       actor_principal, effective_at)
      values ('{ORG_A}', 1, 'full', 30, true, '{USER_OWNER}', '2026-01-01T00:00:00Z');
    insert into infrx.org_entitlements (org_id) values ('{ORG_A}');
    """)
    conn.execute(_job_values(JOB_PREPARING, "job_preparing", state="preparing") + ")")
    conn.execute(_job_values(JOB_NOKEY, "job_nokey", state="preparing").replace(
        f"'{KEY_A}'", "null") + ")")
    conn.execute(_job_values(JOB_QUEUED, "job_queued") + ")")
    conn.execute(_job_values(JOB_RUNNING, "job_running", state="running",
                             extra="published") + ", true)")
    conn.execute(_job_values(
        JOB_TERMINAL, "job_terminal", state="succeeded",
        extra="result_ref, outcome_cause, settlement_state, usage_certainty, debit, settled_at")
        + ", 'infrx-result:x', 'completed', 'settled', 'authoritative', 0.00050000,"
          " '2026-09-21T00:05:00Z')")
    conn.execute(_job_values(JOB_B, "job_b", org=ORG_B) + ")")
    conn.execute(f"""
    insert into infrx.credit_holds (request_id, org_id, key_id, amount, state)
      values ('{JOB_QUEUED}', '{ORG_A}', '{KEY_A}', 1.25000000, 'held');
    insert into infrx.capacity_reservations (request_id, kind, org_id, key_id, amount)
      values ('{JOB_QUEUED}', 'inference', '{ORG_A}', '{KEY_A}', 1);
    insert into infrx.attempts
      (job_id, kind, generation, worker_id, acquired_at, expires_at,
       generation_deadline_at, first_token_deadline_at)
      values ('{JOB_RUNNING}', 'inference', 1, 'worker-1', '2026-09-21T00:03:00Z',
              '2026-09-21T00:05:00Z', '2026-09-21T00:08:00Z', '2026-09-21T00:04:00Z');
    insert into infrx.staged_media
      (org_id, handle, kind, state, digest, bytes, mime, storage_ref, finalized_at)
      values ('{ORG_A}', 'upl_a', 'upload', 'finalized', '{DIGEST}', 1024, 'video/mp4',
              'infrx-media:{ORG_A}:upl_a', '2026-09-21T00:00:30Z');
    insert into infrx.job_media (job_id, org_id, handle, role)
      values ('{JOB_QUEUED}', '{ORG_A}', 'upl_a', 'source');
    insert into infrx.outbox (event_id, aggregate_id, org_id, kind, available_at)
      values ('{EVENT_ID}', '{JOB_QUEUED}', '{ORG_A}', 'inference_dispatch',
              '2026-09-21T00:02:00Z');
    insert into infrx.stream_chunks
      (job_id, generation, sequence, event_type, payload, bytes, expires_at)
      values ('{JOB_TERMINAL}', 1, 1, 'delta', '{{"d":"hi"}}'::jsonb, 10,
              '2026-09-21T01:00:00Z'),
             ('{JOB_TERMINAL}', 1, 2, 'terminal', '{{"state":"succeeded"}}'::jsonb, 30,
              '2026-09-21T01:00:00Z');
    insert into infrx.feedback
      (feedback_id, org_id, request_id, author_principal, author_role, channel, name,
       value_bool)
      values ('fb_1', '{ORG_A}', '{JOB_TERMINAL}', '{USER_OWNER}', 'customer', 'console',
              'thumb', true);
    insert into infrx.feedback
      (feedback_id, org_id, request_id, author_principal, author_role, channel, name,
       value_text, calibration_set, rubric_version, by_operator)
      values ('fb_label', '{ORG_A}', '{JOB_TERMINAL}', 'ops@infrx', 'operator', 'console',
              'calibration_label', 'correct', true, 3, true);
    -- An ordinary operator-authored entry: it stays in the feedback list (R49) and its
    -- principal must read `platform` to a customer.
    insert into infrx.feedback
      (feedback_id, org_id, request_id, author_principal, author_role, channel, name,
       value_text, by_operator)
      values ('fb_ops', '{ORG_A}', '{JOB_TERMINAL}', '{USER_OPERATOR}', 'operator',
              'console', 'comment', 'looked into this', true);
    -- A second consent version, authored by an operator and with no dependent rows: the
    -- masking check reads it, and the DELETE violation needs a row no foreign key
    -- protects incidentally (B9/m27).
    insert into infrx.consent_history
      (org_id, consent_version, trace_mode, content_retention_days, evaluation_consent,
       actor_principal, by_operator, effective_at)
      values ('{ORG_A}', 2, 'full', 30, true, '{USER_OPERATOR}', true,
              '2026-02-01T00:00:00Z');
    insert into infrx.judge_budgets (org_id, period_start, period_end, limit_usd)
      values ('{ORG_A}', '{PERIOD}', '2026-10-01T00:00:00Z', 10.00000000);
    insert into infrx.judge_runs
      (run_id, org_id, consent_version, rubric_version, model_revision, state)
      values ('{RUN_ID}', '{ORG_A}', 1, 3, 'claude-opus', 'dry_run');
    insert into infrx.judge_reservations
      (run_id, org_id, period_start, amount, state)
      values ('{RUN_ID}', '{ORG_A}', '{PERIOD}', 2.00000000, 'held');
    -- r3 (N1): four runs with 0, 1, 50 and 75 samples, so `sample_count` is checked
    -- against a real count and the 50-element cap is visible at the same time.
    insert into infrx.judge_runs
      (run_id, org_id, consent_version, rubric_version, model_revision, state)
    select ('61000000-0000-4000-8000-' || lpad(n::text, 12, '0'))::uuid, '{ORG_A}', 1, 3,
           'claude-opus', 'dry_run'
    from (values (0), (1), (50), (75)) as g(n);
    insert into infrx.judge_samples (run_id, org_id, sample_id, rubric_version)
    select ('61000000-0000-4000-8000-' || lpad(n::text, 12, '0'))::uuid, '{ORG_A}',
           's' || lpad(i::text, 4, '0'), 3
    from (values (1), (50), (75)) as g(n), generate_series(1, g.n) as s(i);
    insert into infrx.callback_destinations
      (destination_id, org_id, url, signing_key_ref)
      values ('{DEST_ID}', '{ORG_A}', 'https://hooks.example.com/infrx', 'kms:key/1');
    insert into infrx.callback_deliveries
      (event_id, destination_id, org_id, state, next_attempt_at)
      values ('{EVENT_ID}', '{DEST_ID}', '{ORG_A}', 'pending', '2026-09-21T00:03:00Z');
    insert into infrx.audit_entries (id, actor_principal, action, target_org_id, reason)
      values (gen_random_uuid(), 'ops@infrx', 'admin_grant', '{ORG_A}', 'pilot credit');
    -- ORG_B gets one row in every tenant-scoped relation, so "a session of one
    -- organization cannot see another's" is a real question for every console read view
    -- rather than a query over an empty set.
    insert into infrx.consent_history
      (org_id, consent_version, trace_mode, content_retention_days, evaluation_consent,
       actor_principal, effective_at)
      values ('{ORG_B}', 1, 'minimal', 7, false, '{USER_OTHER}', '2026-01-01T00:00:00Z');
    -- Revoked, so "a revocation cannot be undone" (ruling 9) has something to undo.
    update infrx.consent_history set revoked_at = '2026-03-01T00:00:00Z'
      where org_id = '{ORG_B}' and consent_version = 1;
    insert into public.credit_ledger (org_id, delta_usd, kind, reason)
      values ('{ORG_B}', 5.00000000, 'grant', 'org b credit');
    insert into public.usage_events (id, org_id, model_id, status, cost_usd)
      values ('{JOB_B}', '{ORG_B}', 'nemostation/marlin-2b', 200, 0.00000100);
    insert into infrx.feedback
      (feedback_id, org_id, request_id, author_principal, author_role, channel, name,
       value_bool)
      values ('fb_b', '{ORG_B}', '{JOB_B}', '{USER_OTHER}', 'customer', 'api', 'thumb',
              false);
    insert into infrx.judge_budgets (org_id, period_start, period_end, limit_usd)
      values ('{ORG_B}', '{PERIOD}', '2026-10-01T00:00:00Z', 1.00000000);
    insert into infrx.judge_runs
      (run_id, org_id, consent_version, rubric_version, model_revision, state)
      values ('60000000-0000-4000-8000-0000000000bb', '{ORG_B}', 1, 2, 'claude-opus',
              'dry_run');
    -- One legacy usage row, so the constraints on the columns 0003 added to
    -- usage_events have something to be checked against on a fresh database too.
    insert into public.usage_events
      (id, org_id, api_key_id, model_id, status, prompt_tokens, completion_tokens, cost_usd)
      values ('90000000-0000-4000-8000-000000000001', '{ORG_A}', '{KEY_A}',
              'nemostation/marlin-2b', 200, 1000, 250, 0.00012345);
    -- 450 distinct UTC days, so `console_usage_daily`'s 400-row bound is a bound this
    -- fixture can actually reach (ruling 10).
    insert into public.usage_events
      (id, org_id, api_key_id, model_id, status, cost_usd, created_at)
    select gen_random_uuid(), '{ORG_A}', '{KEY_A}', 'nemostation/marlin-2b', 200, 0.00001,
           now() - make_interval(days => d)
    from generate_series(1, 450) as g(d);
    -- r2: the fixture writes ledger rows only - `infrx.wallets.ledger_total` is moved by
    -- the AFTER INSERT trigger (ruling 8), so a fixture that set it by hand would be
    -- testing a number nothing maintains. `reserved_total` is still set directly,
    -- because holds do not move it until D2/D5.
    --
    -- Three provenance cases for the masking rule (ruling 1):
    --   * an operator-made entry WITH the marker,
    --   * an operator-made entry WITHOUT it (the fail-open case: `by_operator` defaults
    --     false for all history and the deployed console never sets it),
    --   * an entry by an actual member, whose principal a member MAY read.
    insert into public.credit_ledger
      (org_id, delta_usd, kind, reason, created_by, by_operator, operation_id)
      values ('{ORG_A}', 50.00000000, 'grant', 'pilot credit', '{USER_OPERATOR}', true,
              'grant:fixture-1');
    insert into public.credit_ledger
      (org_id, delta_usd, kind, reason, created_by, by_operator)
      values ('{ORG_A}', 7.00000000, 'grant', 'unmarked operator grant',
              '{USER_OPERATOR}', false);
    insert into public.credit_ledger
      (org_id, delta_usd, kind, reason, created_by, by_operator)
      values ('{ORG_A}', 3.00000000, 'adjustment', 'by the owner', '{USER_OWNER}', false);
    update infrx.wallets set reserved_total = 1.25000000 where org_id = '{ORG_A}';
    """)


def seed_volume(conn, rows: int = 3000) -> None:
    """Enough rows that the planner prefers an index on its own merits, so the plan
    check does not have to disable sequential scans and then believe the answer."""
    conn.execute(f"""
    create or replace function infrx_bulk_n(handle text) returns int
      language sql immutable as $$ select replace($1, 'job_bulk_', '')::int $$;

    -- Three keys, not one: with a single key the per-key and per-org partial indexes
    -- cover exactly the same rows, and the planner may answer an org question from the
    -- key index - which would make this check unable to tell the two apart.
    insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash) values
      ('44444444-4444-4444-8444-44444444444b', '{ORG_A}', '{USER_OWNER}', 'k2',
       'sk-infrx-bbbbbbbb', 'hash-b'),
      ('44444444-4444-4444-8444-44444444444c', '{ORG_A}', '{USER_OWNER}', 'k3',
       'sk-infrx-cccccccc', 'hash-c')
    on conflict do nothing;

    -- Times are relative to now() so the reaper queries below are the ones a live
    -- system runs: a fixture where every deadline is already past makes the overdue
    -- set the whole table, and then a sequential scan really is cheaper.
    insert into infrx.jobs ({_JOB_COLUMNS})
    select ('51000000-0000-4000-8000-' || lpad(i::text, 12, '0'))::uuid,
           'job_bulk_' || i, '{ORG_A}',
           (case i % 3 when 0 then '{KEY_A}'
                       when 1 then '44444444-4444-4444-8444-44444444444b'
                       else '44444444-4444-4444-8444-44444444444c' end)::uuid,
           'nemostation/marlin-2b', 'stream',
           'queued', 'chat.completions', 'infrx-payload:' || i, '{DIGEST}', 4096, 512,
           'pv-1', '{{"price_version":"pv-1"}}'::jsonb, 1.25, 1, 'full',
           now() - make_interval(secs => i),
           now() - make_interval(secs => i) + interval '2 hours',
           120, 10, 300, 60, 20,
           now() - make_interval(secs => i) + interval '2 minutes'
    from generate_series(1, {rows}) as g(i);

    -- Most jobs in a live table are terminal; the partial "active" indexes exist
    -- because the live set is small.
    -- Through `running`: 02 allows preparing -> queued -> running -> terminal and the
    -- guard trigger enforces it, so a fixture cannot take a shortcut either.
    update infrx.jobs set state = 'running'
      where job_handle like 'job_bulk_%' and infrx_bulk_n(job_handle) % 10 <> 0;
    update infrx.jobs set state = 'succeeded', result_ref = 'infrx-result:' || request_id,
           outcome_cause = 'completed', settlement_state = 'settled',
           usage_certainty = 'authoritative', debit = 0.00100000,
           settled_at = admitted_at + interval '1 minute'
      where job_handle like 'job_bulk_%' and infrx_bulk_n(job_handle) % 10 <> 0;

    update infrx.jobs
      set queued_at = admitted_at,
          -- A handful are overdue; the rest have queue time left.
          queue_deadline_at = case when infrx_bulk_n(job_handle) % 300 = 0
                                   then admitted_at
                                   else admitted_at + interval '30 minutes' end
      where job_handle like 'job_bulk_%' and state = 'queued';

    -- Deterministic, not random: a plan check that seeds a different shape on every
    -- run is a plan check that fails on somebody else's machine.
    insert into infrx.attempts (job_id, kind, generation, worker_id, acquired_at,
      expires_at, generation_deadline_at, first_token_deadline_at, released_at)
    select request_id, 'inference', 1, 'worker', admitted_at,
           admitted_at + interval '2 minutes', admitted_at + interval '5 minutes',
           admitted_at + interval '1 minute',
           case when infrx_bulk_n(job_handle) % 10 <> 0
                then admitted_at + interval '3 minutes' end
    from infrx.jobs where job_handle like 'job_bulk_%';

    insert into infrx.stream_chunks (job_id, generation, sequence, event_type, payload,
      bytes, committed_at, expires_at)
    select request_id, 1, s, 'delta', '{{"d":"x"}}'::jsonb, 8, admitted_at,
           admitted_at + interval '1 hour'
    from infrx.jobs, generate_series(1, 2) as s where job_handle like 'job_bulk_%';

    insert into infrx.outbox (event_id, aggregate_id, org_id, kind, available_at,
      acknowledged_at)
    select gen_random_uuid(), request_id, '{ORG_A}', 'usage_projection', admitted_at,
           case when infrx_bulk_n(job_handle) % 10 <> 0
                then admitted_at + interval '1 second' end
    from infrx.jobs where job_handle like 'job_bulk_%';

    insert into infrx.credit_holds (request_id, org_id, amount, state)
    select request_id, '{ORG_A}', 0.5, 'released'
    from infrx.jobs
    where job_handle like 'job_bulk_%' and infrx_bulk_n(job_handle) % 20 <> 0
    on conflict do nothing;
    insert into infrx.credit_holds (request_id, org_id, amount, state, reconcile_after)
    select request_id, '{ORG_A}', 0.5, 'unknown',
           case when infrx_bulk_n(job_handle) % 300 = 0
                then admitted_at - interval '1 hour'      -- aged: the sweep must find it
                else admitted_at + interval '24 hours' end
    from infrx.jobs
    where job_handle like 'job_bulk_%' and infrx_bulk_n(job_handle) % 20 = 0
    on conflict do nothing;

    -- A backlog of ready deliveries among many delivered ones.
    insert into infrx.callback_deliveries (event_id, destination_id, org_id, state,
      next_attempt_at, delivered_at)
    select o.event_id, '{DEST_ID}', '{ORG_A}',
           case when o.acknowledged_at is null then 'pending' else 'delivered' end,
           case when o.acknowledged_at is null then o.available_at end,
           o.acknowledged_at
    from infrx.outbox o
    join infrx.jobs j on j.request_id = o.aggregate_id
    where j.job_handle like 'job_bulk_%'
    on conflict do nothing;

    insert into infrx.feedback (feedback_id, org_id, request_id, author_principal,
      author_role, channel, name, value_bool, created_at)
    select 'fb_bulk_' || request_id, '{ORG_A}', request_id, 'u', 'customer', 'api',
           'thumb', true, admitted_at
    from infrx.jobs where job_handle like 'job_bulk_%';

    insert into public.usage_events (id, org_id, api_key_id, model_id, status, cost_usd,
      created_at)
    select request_id, '{ORG_A}', '{KEY_A}', 'nemostation/marlin-2b', 200, 0.001,
           admitted_at
    from infrx.jobs where job_handle like 'job_bulk_%';

    insert into public.credit_ledger (org_id, delta_usd, kind, created_at)
    select '{ORG_A}', -0.001, 'usage', admitted_at
    from infrx.jobs where job_handle like 'job_bulk_%';

    -- Keep the summary equal to the ledger and the holds, so seeding volume does not
    -- create exactly the drift the reconciliation check looks for.
    update infrx.wallets w set
      ledger_total = (select coalesce(sum(delta_usd), 0) from public.credit_ledger l
                      where l.org_id = w.org_id),
      reserved_total = (select coalesce(sum(amount), 0) from infrx.credit_holds h
                        where h.org_id = w.org_id and h.state in ('held', 'unknown'));

    analyze;
    """)


# --- checks ------------------------------------------------------------------
def check_relations_exist(conn) -> str:
    """Every relation of 06 exists, with RLS enabled and no browser-role grant."""
    found = {f"{s}.{t}" for s, t in conn.execute(
        "select schemaname, tablename from pg_tables where schemaname = 'infrx'").fetchall()}
    missing = [name for name in EXPECTED_RELATIONS if name not in found]
    assert not missing, f"06 relations missing from the migration: {missing}"
    no_rls = conn.execute("""
        select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'infrx' and c.relkind = 'r' and not c.relrowsecurity
        order by 1""").fetchall()
    assert not no_rls, f"infrx relations without row level security: {no_rls}"
    leaked = conn.execute("""
        select table_name, grantee, privilege_type
        from information_schema.role_table_grants
        where table_schema = 'infrx' and grantee in ('anon', 'authenticated', 'PUBLIC')
        order by 1, 2, 3""").fetchall()
    assert not leaked, f"browser roles hold grants inside infrx: {leaked}"
    views = {f"{s}.{v}" for s, v in conn.execute(
        "select schemaname, viewname from pg_views where schemaname in ('infrx','public')"
        ).fetchall()}
    missing_views = [name for name in EXPECTED_VIEWS if name not in views]
    assert not missing_views, f"console read views missing: {missing_views}"
    assert conn.execute("select count(*) from infrx.wallet_reconciliation").fetchone()
    return (f"{len(EXPECTED_RELATIONS)} relations, {len(EXPECTED_VIEWS)} views, RLS on "
            f"all, no browser grants")


def check_rpc_boundary(conn) -> str:
    """Each mutation boundary of 06 is SECURITY DEFINER with a fixed search_path,
    executable by service_role and by nobody a browser can reach."""
    rows = {name: (secdef, config, acl) for name, secdef, config, acl in conn.execute("""
        select p.proname, p.prosecdef, p.proconfig, coalesce(p.proacl::text, '')
        from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'infrx'""").fetchall()}
    for name in RPC_NAMES:
        assert name in rows, f"mutation boundary infrx.{name}() is missing"
        secdef, config, acl = rows[name]
        assert secdef, f"infrx.{name}() is not SECURITY DEFINER"
        assert config and any(c.startswith("search_path=") for c in config), \
            f"infrx.{name}() has no fixed search_path: {config}"
        # An aclitem is `grantee=privileges/grantor`; an empty grantee is PUBLIC, which
        # is what `create function` grants by default and what the migration revokes.
        grantees = {item.split("=", 1)[0] for item in acl.strip("{}").split(",") if item}
        assert grantees <= {"postgres", "service_role"}, \
            f"infrx.{name}() is executable beyond the service role: {acl}"
        assert "service_role" in grantees, f"infrx.{name}() is not granted to service_role"
    # The body is absent on purpose: the boundary fails closed until its task fills it.
    for name in RPC_NAMES:
        try:
            conn.execute(f"select infrx.{name}('{{}}'::jsonb)")
        except psycopg.errors.FeatureNotSupported:
            continue
        raise AssertionError(f"infrx.{name}() did not fail closed")
    return f"{len(RPC_NAMES)} boundaries: security definer, fixed search_path, service only"


def check_entitlements_and_limits(conn) -> str:
    """R24: `model_ids` must be nullable AND have no default, so null (platform
    default) is distinguishable from '{}' (nothing entitled). `not null default '{}'`
    would silently deny every organization."""
    notnull, default = conn.execute("""
        select a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
        from pg_attribute a
        left join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum
        where a.attrelid = 'infrx.org_entitlements'::regclass and a.attname = 'model_ids'
        """).fetchone()
    assert not notnull, "org_entitlements.model_ids is NOT NULL: null cannot mean default"
    assert default is None, f"org_entitlements.model_ids has a default: {default}"
    conn.execute(f"""update infrx.org_entitlements set model_ids = '{{}}'
                     where org_id = '{ORG_A}'""")
    empty, = conn.execute(f"""select model_ids from infrx.org_entitlements
                              where org_id = '{ORG_A}'""").fetchone()
    assert empty == [], f"an empty entitlement list did not survive: {empty!r}"
    conn.execute(f"""update infrx.org_entitlements set model_ids = null
                     where org_id = '{ORG_A}'""")
    assert conn.execute(f"""select model_ids is null from infrx.org_entitlements
                            where org_id = '{ORG_A}'""").fetchone()[0]
    # The three typed columns are exactly ENTITLEMENT_LIMIT_NAMES.
    from infrx.contracts.limits import ENTITLEMENT_LIMIT_NAMES
    columns = {name for name, in conn.execute("""
        select column_name from information_schema.columns
        where table_schema = 'infrx' and table_name = 'org_entitlements'""").fetchall()}
    missing = [name for name in ENTITLEMENT_LIMIT_NAMES if name not in columns]
    assert not missing, f"entitlement limits without a column: {missing}"
    # 02: models.limits is extended, not duplicated.
    limit_columns = sorted(name for name, in conn.execute("""
        select column_name from information_schema.columns
        where table_schema = 'public' and table_name = 'models'
          and column_name like '%limit%'""").fetchall())
    assert limit_columns == ["limits"], f"models grew a second limits column: {limit_columns}"
    kind, = conn.execute("""select data_type from information_schema.columns
                            where table_schema='public' and table_name='models'
                              and column_name='limits'""").fetchone()
    assert kind == "jsonb", f"models.limits changed type to {kind}"
    keys = conn.execute("""select jsonb_object_keys(limits) from public.models
                           where id = 'nemostation/marlin-2b'""").fetchall()
    keys = {k for k, in keys}
    assert {"max_video_seconds", "max_output_tokens", "max_context_tokens"} <= keys, \
        f"models.limits was replaced rather than extended: {sorted(keys)}"
    return "model_ids nullable with no default; limits extended in place"


def check_money_domain(conn) -> str:
    """R11: money is numeric(20,8) - scale 1e-8, |value| < 10^12 - everywhere."""
    wrong = conn.execute("""
        select table_schema || '.' || table_name || '.' || column_name,
               numeric_precision, numeric_scale
        from information_schema.columns
        where data_type = 'numeric'
          and (table_schema = 'infrx'
               or (table_schema = 'public' and table_name in ('credit_ledger','usage_events')
                   and column_name in ('delta_usd','cost_usd')))
          and column_name not in ('video_seconds')
          and (numeric_precision, numeric_scale) <> (20, 8)
        order by 1""").fetchall()
    assert not wrong, f"money columns that are not numeric(20,8): {wrong}"
    over, = conn.execute("select count(*) from infrx.wallets").fetchone()
    assert over >= 1
    return "every money column is numeric(20,8)"


def check_wallets_and_reconciliation(conn) -> str:
    """Wallet summaries equal the immutable ledger and the active holds (06), and a
    new organization starts at zero with no grant (02)."""
    drift = conn.execute("""select org_id, ledger_drift, reserved_drift
                            from infrx.wallet_reconciliation
                            where ledger_drift <> 0 or reserved_drift <> 0
                            order by org_id""").fetchall()
    assert not drift, f"wallet summaries disagree with the ledger/holds: {drift}"
    orphans = conn.execute("""select o.id from public.organizations o
                              left join infrx.wallets w on w.org_id = o.id
                              where w.org_id is null""").fetchall()
    assert not orphans, f"organizations without a wallet: {orphans}"
    conn.execute("""insert into public.organizations (id, name, slug)
                    values ('0c000000-0000-4000-8000-00000000000c', 'new', 'new-org')""")
    row = conn.execute("""select ledger_total, reserved_total, available from infrx.wallets
                          where org_id = '0c000000-0000-4000-8000-00000000000c'""").fetchone()
    assert row == (Decimal("0.00000000"), Decimal("0.00000000"), Decimal("0.00000000")), \
        f"a new organization did not start at zero: {row}"
    granted, = conn.execute("""select count(*) from public.credit_ledger
                               where org_id = '0c000000-0000-4000-8000-00000000000c'
                            """).fetchone()
    assert granted == 0, "a new organization received an automatic grant"
    conn.execute("""delete from public.organizations
                    where id = '0c000000-0000-4000-8000-00000000000c'""")
    return "reconciliation clean; new organization at zero with no grant"


def check_upgrade_preserved(conn, before: dict) -> str:
    """Every historical balance and usage value is identical after the upgrade, the
    precision change added only trailing zeros, and the history is marked outside the
    settlement regime so nothing can re-debit it (02)."""
    after = capture_legacy(conn, before["org_a"], before["org_b"])
    assert len(after["ledger"]) == len(before["ledger"]), "ledger rows were added or lost"
    for old, new in zip(before["ledger"], after["ledger"]):
        assert old[0] == new[0], f"ledger row identity changed: {old[0]} -> {new[0]}"
        assert old[1] == new[1], f"ledger delta changed: {old[1]} -> {new[1]}"
        # The stored scale widened, so the text form may gain trailing zeros - and
        # nothing else. A precision change that rounds fails here.
        assert old[2].rstrip("0") == new[2].rstrip("0"), \
            f"ledger delta digits changed: {old[2]} -> {new[2]}"
        assert old[3] == new[3] and old[4] == new[4], "ledger kind or org changed"
    assert before["ledger_sums"] == after["ledger_sums"], \
        f"balances changed: {before['ledger_sums']} -> {after['ledger_sums']}"
    assert before["usage"] == after["usage"], \
        f"usage values changed: {before['usage']} -> {after['usage']}"
    assert before["usage_sum"] == after["usage_sum"], "usage cost total changed"
    legacy, = conn.execute("""select count(*) from public.usage_events
                              where settlement_regime <> 'legacy'""").fetchone()
    assert legacy == 0, f"{legacy} historical usage rows are inside the settlement regime"
    status_type, = conn.execute("""select data_type from information_schema.columns
                                   where table_schema = 'public'
                                     and table_name = 'usage_events'
                                     and column_name = 'status'""").fetchone()
    assert status_type == "integer", f"usage_events.status is no longer numeric: {status_type}"
    outcome, = conn.execute("""select count(*) from information_schema.columns
                               where table_schema='public' and table_name='usage_events'
                                 and column_name='outcome'""").fetchone()
    assert outcome == 1, "usage_events has no separate textual outcome"
    wallets = dict(conn.execute("select org_id, ledger_total from infrx.wallets").fetchall())
    for org_id, total in before["balances"]:
        assert wallets[org_id] == total, \
            f"imported wallet for {org_id} is {wallets[org_id]}, ledger says {total}"
    return (f"{len(after['ledger'])} ledger rows and {len(after['usage'])} usage rows "
            f"value-identical; wallets imported; history marked legacy")


# --- DUR-RLS ------------------------------------------------------------------
class _Allowed(Exception):
    """Raised inside the transaction so an allowed mutation is still rolled back."""


def _jwt(user: str) -> str:
    """Impersonate a PostgREST session, in BOTH claim forms.

    The real `supabase/postgres` image's `auth.uid()` reads the legacy per-claim GUCs
    (`request.jwt.claim.sub`); newer PostgREST sets the JSON `request.jwt.claims`.
    Setting only one makes every principal anonymous on the other's image - and a role
    matrix where nobody is anybody passes by seeing nothing.
    """
    return (f"set local role authenticated; "
            f"select set_config('request.jwt.claims',"
            f"'{{\"sub\":\"{user}\",\"role\":\"authenticated\"}}', true), "
            f"set_config('request.jwt.claim.sub', '{user}', true), "
            f"set_config('request.jwt.claim.role', 'authenticated', true)")


SESSIONS = {
    "anon": "set local role anon",
    "member": _jwt(USER_MEMBER),
    "owner": _jwt(USER_OWNER),
    "operator": _jwt(USER_OPERATOR),
    "other": _jwt(USER_OTHER),
    "service": "set local role service_role",
}

BROWSER_SESSIONS = ("anon", "member", "owner", "operator", "other")

#: (what is protected, statement). Every browser session must be refused every one.
ATTACKS = (
    ("balances: wallet summary", f"update infrx.wallets set ledger_total = 1000000 "
                                 f"where org_id = '{ORG_A}'"),
    ("balances: wallet read", "select ledger_total from infrx.wallets"),
    ("holds", f"insert into infrx.credit_holds (request_id, org_id, amount, state) "
              f"values ('{JOB_B}', '{ORG_A}', 0, 'held')"),
    ("holds: release", "update infrx.credit_holds set state = 'released'"),
    ("entitlements", f"update infrx.org_entitlements set model_ids = null "
                     f"where org_id = '{ORG_A}'"),
    ("suspension: set", f"update public.organizations set suspended = true "
                        f"where id = '{ORG_A}'"),
    ("suspension: clear", "update public.organizations set suspended = false, "
                          "suspension_reason = null"),
    ("job ownership", f"update infrx.jobs set org_id = '{ORG_B}' "
                      f"where request_id = '{JOB_QUEUED}'"),
    ("job state", f"update infrx.jobs set state = 'succeeded' "
                  f"where request_id = '{JOB_QUEUED}'"),
    ("author provenance: forge operator label",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_text, calibration_set, rubric_version, "
     f"by_operator) values ('fb_forged', '{ORG_A}', '{JOB_TERMINAL}', 'me', 'operator', "
     f"'console', 'calibration_label', 'correct', true, 1, true)"),
    ("author provenance: by_operator", "update infrx.feedback set by_operator = true"),
    ("author provenance: read labels", "select value_text from infrx.feedback "
                                       "where calibration_set"),
    ("platform role", f"update public.profiles set is_operator = true "
                      f"where id = '{USER_MEMBER}'"),
    ("ledger: insert", f"insert into public.credit_ledger (org_id, delta_usd, kind) "
                       f"values ('{ORG_A}', 1000, 'grant')"),
    ("ledger: edit history", "update public.credit_ledger set delta_usd = 0"),
    ("ledger: delete history", "delete from public.credit_ledger"),
    ("metering: insert usage", f"insert into public.usage_events "
                               f"(id, org_id, model_id, status) values "
                               f"('90000000-0000-4000-8000-0000000000ff', '{ORG_A}', "
                               f"'nemostation/marlin-2b', 200)"),
    ("metering: rewrite usage", "update public.usage_events set cost_usd = 0, "
                                "settlement_regime = 'legacy'"),
    ("prices", "update public.models set input_usd_per_m = 0"),
    ("price versions", "insert into infrx.price_versions (price_version, model_revision, "
                       "input_rate_per_million, output_rate_per_million, "
                       "token_rules_version, effective_from) values "
                       "('pv-free', 'nemostation/marlin-2b', 0, 0, 'tr', now())"),
    ("membership", f"insert into public.org_members (org_id, user_id, role) values "
                   f"('{ORG_B}', '{USER_OWNER}', 'owner')"),
    # Trace consent is an audited decision recorded in consent_history, not a column an
    # owner writes through the key-management policy - by UPDATE **or** by INSERT
    # (ruling 5): a table-level INSERT grant covers every column of the new row.
    ("per-key trace consent", "update public.api_keys set trace_mode = 'full'"),
    ("per-key trace consent by insert",
     f"insert into public.api_keys (org_id, created_by, name, prefix, key_hash, "
     f"trace_mode) values ('{ORG_A}', '{USER_OWNER}', 'k', 'sk-infrx-eeeeeeee', "
     f"'hash-e', 'full')"),
    ("choosing an api key's identity",
     f"insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash) "
     f"values ('44444444-4444-4444-8444-4444444444ff', '{ORG_A}', '{USER_OWNER}', 'k', "
     f"'sk-infrx-ffffffff', 'hash-f')"),
    ("backdating an api key",
     f"insert into public.api_keys (org_id, created_by, name, prefix, key_hash, "
     f"created_at) values ('{ORG_A}', '{USER_OWNER}', 'k', 'sk-infrx-99999999', "
     f"'hash-9', '2020-01-01T00:00:00Z')"),
    ("forging a key's last use",
     f"insert into public.api_keys (org_id, created_by, name, prefix, key_hash, "
     f"last_used_at) values ('{ORG_A}', '{USER_OWNER}', 'k', 'sk-infrx-88888888', "
     f"'hash-8', now())"),
    # B9/m44: the console views are owner's-rights views over `infrx`. A simple view is
    # updatable, so without the revoke an owner writes the ledger total through one.
    ("balances through the console view",
     "update public.wallets set ledger_total = '1000000'"),
    ("the ledger through the console view",
     "update public.console_ledger set delta = '1000000'"),
    ("feedback provenance through the console view",
     "update public.feedback set author_principal = 'someone else'"),
    ("deleting through a console view", "delete from public.console_usage"),
    ("another tenant's output", "select payload from infrx.stream_chunks"),
    ("audit trail", "delete from infrx.audit_entries"),
    ("the database clock", "select infrx.now()"),
    *tuple((f"RPC infrx.{name}", f"select infrx.{name}('{{}}'::jsonb)") for name in RPC_NAMES),
)

#: Positive controls. Without them a matrix where SET ROLE silently did nothing, or
#: where the tables were unreachable for an unrelated reason, would look perfect.
ALLOWED = (
    ("owner renames the organization",
     "owner", f"update public.organizations set name = 'renamed' where id = '{ORG_A}'"),
    ("member updates their own profile name",
     "member", f"update public.profiles set full_name = 'x' where id = '{USER_MEMBER}'"),
    ("member reads their organization's usage",
     "member", "select count(*) from public.usage_events"),
    ("member reads their organization's ledger",
     "member", "select count(*) from public.credit_ledger"),
    # The console's own key actions must keep working (R33: a leaked key is always
    # revocable), which is what the narrowed column grant is for.
    ("owner renames a key",
     "owner", f"update public.api_keys set name = 'renamed' where org_id = '{ORG_A}'"),
    ("owner revokes a key",
     "owner", f"update public.api_keys set revoked_at = now() where org_id = '{ORG_A}'"),
    ("owner creates a key",
     "owner", f"insert into public.api_keys (org_id, created_by, name, prefix, key_hash) "
              f"values ('{ORG_A}', '{USER_OWNER}', 'new', 'sk-infrx-dddddddd', 'hash-d')"),
    ("service role writes a wallet",
     "service", f"update infrx.wallets set reserved_total = 2 where org_id = '{ORG_A}'"),
    ("service role calls the clock", "service", "select infrx.now()"),
)


def _attempt(conn, session: str, sql: str) -> str | None:
    """None when the statement was allowed, otherwise the SQLSTATE and message."""
    try:
        with conn.transaction():
            conn.execute(SESSIONS[session])
            conn.execute(sql)
            raise _Allowed()
    except _Allowed:
        return None
    except psycopg.Error as refused:
        return f"{refused.sqlstate} {str(refused).splitlines()[0]}"


def check_sessions_are_somebody(conn) -> str:
    """Every impersonated session IS the principal it claims to be.

    Without this the whole matrix can pass by accident: if `auth.uid()` reads a claim
    form the harness does not set (the real Supabase image reads the legacy per-claim
    GUCs), every session is anonymous, every policy denies, and every attack "fails"
    for the wrong reason.
    """
    expected = {"member": USER_MEMBER, "owner": USER_OWNER, "operator": USER_OPERATOR,
                "other": USER_OTHER}
    for session, user in expected.items():
        uid, role, operator = read_rows(
            conn, session, "select auth.uid(), auth.role(), public.is_operator()")[0]
        assert uid is not None and str(uid) == user, \
            f"the {session} session is nobody: auth.uid() = {uid!r}"
        assert role == "authenticated", f"the {session} session has role {role!r}"
        assert operator == (session == "operator"), \
            f"is_operator() is {operator} for the {session} session"
    assert read_rows(conn, "anon", "select auth.uid()")[0][0] is None
    assert read_rows(conn, "owner",
                     f"select public.is_org_owner('{ORG_A}')")[0][0] is True
    assert read_rows(conn, "member",
                     f"select public.is_org_owner('{ORG_A}')")[0][0] is False
    return f"{len(expected)} impersonated principals resolve, in both claim forms"


def check_role_matrix(conn) -> str:
    """DUR-RLS: no browser role can write balances, holds, entitlements, suspension,
    job ownership, author provenance, `by_operator` or platform roles, nor read another
    tenant's durable state, nor call a mutation boundary."""
    allowed_when_it_should_not_be = []
    for label, sql in ATTACKS:
        for session in BROWSER_SESSIONS:
            refusal = _attempt(conn, session, sql)
            if refusal is None:
                allowed_when_it_should_not_be.append(f"{session} may {label}")
    assert not allowed_when_it_should_not_be, \
        "browser roles reached protected state:\n  " + \
        "\n  ".join(allowed_when_it_should_not_be)
    refused_but_needed = []
    for label, session, sql in ALLOWED:
        refusal = _attempt(conn, session, sql)
        if refusal is not None:
            refused_but_needed.append(f"{label} ({session}): {refusal}")
    assert not refused_but_needed, \
        "the matrix denied something it must allow:\n  " + "\n  ".join(refused_but_needed)
    return (f"{len(ATTACKS)} protected operations x {len(BROWSER_SESSIONS)} browser "
            f"sessions denied; {len(ALLOWED)} controls allowed")


# --- row checks ---------------------------------------------------------------
#: (invariant, statement that must be refused).
VIOLATIONS = (
    ("a phase deadline past the absolute deadline",
     _job_values("52000000-0000-4000-8000-000000000001", "job_v1",
                 extra="queue_deadline_at") + ", '2026-09-21T09:00:00Z')"),
    ("a terminal state with no settlement",
     _job_values("52000000-0000-4000-8000-000000000002", "job_v2", state="failed") + ")"),
    ("a settled row with no cause",
     _job_values("52000000-0000-4000-8000-000000000003", "job_v3", state="failed",
                 extra="settled_at") + ", '2026-09-21T00:05:00Z')"),
    ("cause `completed` on a failed job",
     _job_values("52000000-0000-4000-8000-000000000004", "job_v4", state="failed",
                 extra="settled_at, outcome_cause, settlement_state")
     + ", '2026-09-21T00:05:00Z', 'completed', 'released_free')"),
    ("cause `engine_error` on a succeeded job",
     _job_values("52000000-0000-4000-8000-000000000005", "job_v5", state="succeeded",
                 extra="settled_at, outcome_cause, settlement_state, result_ref")
     + ", '2026-09-21T00:05:00Z', 'engine_error', 'released_free', 'r')"),
    ("a debit on a released outcome",
     _job_values("52000000-0000-4000-8000-000000000006", "job_v6", state="succeeded",
                 extra="settled_at, outcome_cause, settlement_state, result_ref, debit")
     + ", '2026-09-21T00:05:00Z', 'completed', 'released_free', 'r', 0.001)"),
    ("a debit on a non-billable cause (R21)",
     _job_values("52000000-0000-4000-8000-000000000007", "job_v7", state="failed",
                 extra="settled_at, outcome_cause, settlement_state, debit")
     + ", '2026-09-21T00:05:00Z', 'sync_deadline', 'settled', 0.001)"),
    ("held_unknown with no reconciliation window",
     _job_values("52000000-0000-4000-8000-000000000008", "job_v8", state="failed",
                 extra="settled_at, outcome_cause, settlement_state")
     + ", '2026-09-21T00:05:00Z', 'engine_error', 'held_unknown')"),
    ("a reconciliation window without held_unknown",
     _job_values("52000000-0000-4000-8000-000000000009", "job_v9", state="failed",
                 extra="settled_at, outcome_cause, settlement_state, reconcile_after")
     + ", '2026-09-21T00:05:00Z', 'engine_error', 'released_free',"
       " '2026-09-22T00:00:00Z')"),
    ("a success with no result reference (R30)",
     _job_values("52000000-0000-4000-8000-00000000000a", "job_va", state="succeeded",
                 extra="settled_at, outcome_cause, settlement_state")
     + ", '2026-09-21T00:05:00Z', 'completed', 'released_free')"),
    ("a zero output-token ceiling (R55)",
     _job_values("52000000-0000-4000-8000-00000000000b", "job_vb").replace(
         "4096, 512", "4096, 0") + ")"),
    ("a payload digest that is not sha256",
     _job_values("52000000-0000-4000-8000-00000000000c", "job_vc").replace(
         DIGEST, "md5:deadbeef") + ")"),
    ("an unknown job state",
     _job_values("52000000-0000-4000-8000-00000000000d", "job_vd", state="paused") + ")"),
    # r2/B9(m16): JOB_PREPARING has no job_media, hold or reservation, so only
    # `jobs_guard` can refuse this - the old target was refused by a child foreign key
    # and the mutant that removed `org_id` from the guard survived.
    ("changing a job's organization",
     f"update infrx.jobs set org_id = '{ORG_B}' where request_id = '{JOB_NOKEY}'"),
    ("changing a job's admitted budget",
     f"update infrx.jobs set budget_generation_s = 9999 where request_id = '{JOB_QUEUED}'"),
    ("changing a job's payload reference",
     f"update infrx.jobs set payload_ref = 'other' where request_id = '{JOB_QUEUED}'"),
    ("clearing the publication marker",
     f"update infrx.jobs set published = false where request_id = '{JOB_RUNNING}'"),
    ("resurrecting a terminal job",
     f"update infrx.jobs set state = 'running' where request_id = '{JOB_TERMINAL}'"),
    ("skipping preparation straight to running",
     f"update infrx.jobs set state = 'running' where request_id = '{JOB_PREPARING}'"),
    ("a preparation lease with a first-token deadline (R46)",
     f"insert into infrx.attempts (job_id, kind, generation, worker_id, acquired_at, "
     f"expires_at, generation_deadline_at, first_token_deadline_at) values "
     f"('{JOB_QUEUED}', 'preparation', 1, 'w', '2026-09-21T00:00:10Z', "
     f"'2026-09-21T00:00:40Z', '2026-09-21T00:02:00Z', '2026-09-21T00:00:30Z')"),
    ("an inference lease without a first-token deadline",
     f"insert into infrx.attempts (job_id, kind, generation, worker_id, acquired_at, "
     f"expires_at, generation_deadline_at) values "
     f"('{JOB_QUEUED}', 'inference', 1, 'w', '2026-09-21T00:03:00Z', "
     f"'2026-09-21T00:05:00Z', '2026-09-21T00:08:00Z')"),
    ("a first-token deadline past the generation deadline",
     f"insert into infrx.attempts (job_id, kind, generation, worker_id, acquired_at, "
     f"expires_at, generation_deadline_at, first_token_deadline_at) values "
     f"('{JOB_QUEUED}', 'inference', 1, 'w', '2026-09-21T00:03:00Z', "
     f"'2026-09-21T00:05:00Z', '2026-09-21T00:08:00Z', '2026-09-21T00:09:00Z')"),
    ("a second active attempt of the same kind",
     f"insert into infrx.attempts (job_id, kind, generation, worker_id, acquired_at, "
     f"expires_at, generation_deadline_at, first_token_deadline_at) values "
     f"('{JOB_RUNNING}', 'inference', 2, 'w2', '2026-09-21T00:03:00Z', "
     f"'2026-09-21T00:05:00Z', '2026-09-21T00:08:00Z', '2026-09-21T00:04:00Z')"),
    ("a hold in `held` state carrying a reconciliation window",
     f"insert into infrx.credit_holds (request_id, org_id, amount, state, "
     f"reconcile_after) values ('{JOB_RUNNING}', '{ORG_A}', 1, 'held', now())"),
    ("an `unknown` hold with no reconciliation window",
     f"insert into infrx.credit_holds (request_id, org_id, amount, state) "
     f"values ('{JOB_RUNNING}', '{ORG_A}', 1, 'unknown')"),
    ("a negative hold",
     f"insert into infrx.credit_holds (request_id, org_id, amount, state) "
     f"values ('{JOB_RUNNING}', '{ORG_A}', -1, 'held')"),
    ("a second hold for one request",
     f"insert into infrx.credit_holds (request_id, org_id, amount, state) "
     f"values ('{JOB_QUEUED}', '{ORG_A}', 1, 'held')"),
    ("a negative reserved total",
     f"update infrx.wallets set reserved_total = -1 where org_id = '{ORG_A}'"),
    ("writing the derived available balance",
     f"update infrx.wallets set available = 999 where org_id = '{ORG_A}'"),
    ("an active reservation that is also released",
     f"insert into infrx.capacity_reservations (request_id, kind, org_id, amount, "
     f"active, released_at) values ('{JOB_RUNNING}', 'inference', '{ORG_A}', 1, true, "
     f"now())"),
    ("an unknown reservation kind",
     f"insert into infrx.capacity_reservations (request_id, kind, org_id, amount) "
     f"values ('{JOB_RUNNING}', 'gpu_minutes', '{ORG_A}', 1)"),
    ("a duplicate reservation of one kind",
     f"insert into infrx.capacity_reservations (request_id, kind, org_id, amount) "
     f"values ('{JOB_QUEUED}', 'inference', '{ORG_A}', 1)"),
    ("a second terminal journal event (R30)",
     f"insert into infrx.stream_chunks (job_id, generation, sequence, event_type, "
     f"payload, bytes, expires_at) values ('{JOB_TERMINAL}', 1, 3, 'terminal', "
     f"'{{}}'::jsonb, 2, '2026-09-21T01:00:00Z')"),
    ("a zero chunk sequence",
     f"insert into infrx.stream_chunks (job_id, generation, sequence, event_type, "
     f"payload, bytes, expires_at) values ('{JOB_RUNNING}', 1, 0, 'delta', "
     f"'{{}}'::jsonb, 2, '2026-09-21T01:00:00Z')"),
    ("an unknown chunk event type",
     f"insert into infrx.stream_chunks (job_id, generation, sequence, event_type, "
     f"payload, bytes, expires_at) values ('{JOB_RUNNING}', 1, 1, 'heartbeat', "
     f"'{{}}'::jsonb, 2, '2026-09-21T01:00:00Z')"),
    ("an unknown outbox kind",
     f"insert into infrx.outbox (event_id, aggregate_id, org_id, kind, available_at) "
     f"values ('70000000-0000-4000-8000-0000000000ff', '{JOB_QUEUED}', '{ORG_A}', "
     f"'email', now())"),
    ("an unbounded outbox payload",
     f"insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, "
     f"available_at) values ('70000000-0000-4000-8000-0000000000fe', '{JOB_QUEUED}', "
     f"'{ORG_A}', 'usage_projection', jsonb_build_object('blob', repeat('x', 5000)), "
     f"now())"),
    ("a thumb whose value is a number (R3)",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_int) values ('fb_x1', '{ORG_A}', "
     f"'{JOB_TERMINAL}', 'u', 'customer', 'api', 'thumb', 1)"),
    ("a rating of 6",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_int) values ('fb_x2', '{ORG_A}', "
     f"'{JOB_TERMINAL}', 'u', 'customer', 'api', 'rating', 6)"),
    ("two value variants at once",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_bool, value_text) values ('fb_x3', '{ORG_A}', "
     f"'{JOB_TERMINAL}', 'u', 'customer', 'api', 'thumb', true, 'also text')"),
    ("a calibration label that is not in the calibration set (R43)",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_text, calibration_set, rubric_version, "
     f"by_operator) values ('fb_x4', '{ORG_A}', '{JOB_TERMINAL}', 'ops', 'operator', "
     f"'console', 'calibration_label', 'correct', false, 1, true)"),
    ("a calibration label with no rubric version",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_text, calibration_set, by_operator) values "
     f"('fb_x5', '{ORG_A}', '{JOB_TERMINAL}', 'ops', 'operator', 'console', "
     f"'calibration_label', 'correct', true, true)"),
    ("a rubric version on an ordinary entry",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_bool, rubric_version) values ('fb_x6', "
     f"'{ORG_A}', '{JOB_TERMINAL}', 'u', 'customer', 'api', 'thumb', true, 1)"),
    ("a customer-authored calibration label (R54)",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_text, calibration_set, rubric_version) values "
     f"('fb_x7', '{ORG_A}', '{JOB_TERMINAL}', 'u', 'customer', 'console', "
     f"'calibration_label', 'correct', true, 1)"),
    ("an operator-authored entry without the by_operator marker (R55)",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_text) values ('fb_x8', '{ORG_A}', "
     f"'{JOB_TERMINAL}', 'ops', 'operator', 'console', 'comment', 'seen')"),
    ("a rubric version of 0",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_text, calibration_set, rubric_version, "
     f"by_operator) values ('fb_x9', '{ORG_A}', '{JOB_TERMINAL}', 'ops', 'operator', "
     f"'console', 'calibration_label', 'correct', true, 0, true)"),
    ("a rubric version of 1001",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_text, calibration_set, rubric_version, "
     f"by_operator) values ('fb_xa', '{ORG_A}', '{JOB_TERMINAL}', 'ops', 'operator', "
     f"'console', 'calibration_label', 'correct', true, 1001, true)"),
    ("a calibration verdict outside the vocabulary",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_text, calibration_set, rubric_version, "
     f"by_operator) values ('fb_xb', '{ORG_A}', '{JOB_TERMINAL}', 'ops', 'operator', "
     f"'console', 'calibration_label', 'excellent', true, 1, true)"),
    ("feedback text past 4000 characters (R43)",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_text) values ('fb_xc', '{ORG_A}', "
     f"'{JOB_TERMINAL}', 'u', 'customer', 'api', 'correction', repeat('x', 4001))"),
    ("a comment past 4000 characters",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_bool, comment) values ('fb_xd', '{ORG_A}', "
     f"'{JOB_TERMINAL}', 'u', 'customer', 'api', 'thumb', true, repeat('x', 4001))"),
    ("feedback on another tenant's request (R55)",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_bool) values ('fb_xe', '{ORG_B}', "
     f"'{JOB_TERMINAL}', 'u', 'customer', 'api', 'thumb', true)"),
    ("a second label for one request, rubric and operator",
     f"insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
     f"author_role, channel, name, value_text, calibration_set, rubric_version, "
     f"by_operator) values ('fb_xf', '{ORG_A}', '{JOB_TERMINAL}', 'ops@infrx', "
     f"'operator', 'console', 'calibration_label', 'incorrect', true, 3, true)"),
    ("media attached from another tenant (R55)",
     f"insert into infrx.job_media (job_id, org_id, handle, role) values "
     f"('{JOB_B}', '{ORG_A}', 'upl_a', 'source')"),
    ("a zero-day retention policy (R43)",
     f"insert into infrx.consent_history (org_id, consent_version, trace_mode, "
     f"content_retention_days, evaluation_consent, actor_principal, effective_at) "
     f"values ('{ORG_A}', 5, 'full', 0, false, 'u', now())"),
    ("a retention policy past 90 days",
     f"insert into infrx.consent_history (org_id, consent_version, trace_mode, "
     f"content_retention_days, evaluation_consent, actor_principal, effective_at) "
     f"values ('{ORG_A}', 6, 'full', 91, false, 'u', now())"),
    ("evaluation consent without full capture",
     f"insert into infrx.consent_history (org_id, consent_version, trace_mode, "
     f"content_retention_days, evaluation_consent, actor_principal, effective_at) "
     f"values ('{ORG_A}', 7, 'minimal', 30, true, 'u', now())"),
    ("rewriting consent history",
     f"update infrx.consent_history set evaluation_consent = false "
     f"where org_id = '{ORG_A}'"),
    # r2/B9(m27): version 2 has no judge run pointing at it, so the guard is the only
    # thing that can refuse the delete.
    ("deleting consent history",
     f"delete from infrx.consent_history where org_id = '{ORG_A}' and consent_version = 2"),
    ("undoing a consent revocation (ruling 9)",
     f"update infrx.consent_history set revoked_at = null where org_id = '{ORG_B}'"),
    ("rewriting a consent row's created_at",
     f"update infrx.consent_history set created_at = now() where org_id = '{ORG_A}'"),
    ("an idempotency row that refers to nothing",
     f"insert into infrx.idempotency (org_id, operation, key, payload_digest) values "
     f"('{ORG_A}', 'chat.completions', 'k1', '{DIGEST}')"),
    ("an idempotency row with two results",
     f"insert into infrx.idempotency (org_id, operation, key, payload_digest, "
     f"request_id, feedback_id) values ('{ORG_A}', 'chat.completions', 'k2', "
     f"'{DIGEST}', '{JOB_QUEUED}', 'fb_1')"),
    ("a negative entitlement limit",
     f"update infrx.org_entitlements set max_concurrent_requests = -1 "
     f"where org_id = '{ORG_A}'"),
    ("a judge budget period that ends before it starts",
     f"insert into infrx.judge_budgets (org_id, period_start, period_end, limit_usd) "
     f"values ('{ORG_B}', '2026-10-01T00:00:00Z', '2026-09-01T00:00:00Z', 1)"),
    ("a submitted run with no provider id",
     f"update infrx.judge_runs set state = 'submitted', submit_intent = "
     f"gen_random_uuid() where run_id = '{RUN_ID}'"),
    ("a submitting run with no submission intent",
     f"update infrx.judge_runs set state = 'submitting' where run_id = '{RUN_ID}'"),
    ("a settlement larger than its reservation",
     f"update infrx.judge_reservations set settled_amount = 3, state = 'settled' "
     f"where run_id = '{RUN_ID}'"),
    ("a judge run for consent that does not exist",
     f"insert into infrx.judge_runs (run_id, org_id, consent_version, rubric_version, "
     f"model_revision, state) values ('60000000-0000-4000-8000-0000000000ff', "
     f"'{ORG_A}', 99, 1, 'claude-opus', 'dry_run')"),
    ("a duplicate sample score for one rubric version",
     f"insert into infrx.judge_samples (run_id, org_id, sample_id, rubric_version) values "
     f"('{RUN_ID}', '{ORG_A}', 's1', 3), ('{RUN_ID}', '{ORG_A}', 's1', 3)"),
    ("a plaintext callback destination",
     f"insert into infrx.callback_destinations (destination_id, org_id, url, "
     f"signing_key_ref) values ('80000000-0000-4000-8000-0000000000ff', '{ORG_A}', "
     f"'http://hooks.example.com', 'kms:key/1')"),
    ("a delivered callback with no delivery time",
     f"update infrx.callback_deliveries set state = 'delivered' "
     f"where event_id = '{EVENT_ID}'"),
    ("a dead-lettered callback with no reason",
     f"update infrx.callback_deliveries set state = 'dead_letter' "
     f"where event_id = '{EVENT_ID}'"),
    ("an audit entry with an empty reason (R34)",
     "insert into infrx.audit_entries (id, actor_principal, action, reason) values "
     "(gen_random_uuid(), 'ops', 'admin_grant', '')"),
    ("an unknown audited action",
     "insert into infrx.audit_entries (id, actor_principal, action, reason) values "
     "(gen_random_uuid(), 'ops', 'delete_everything', 'because')"),
    ("rewriting an audit entry",
     "update infrx.audit_entries set reason = 'other'"),
    ("deleting an audit entry", "delete from infrx.audit_entries"),
    ("a negative price",
     "insert into infrx.price_versions (price_version, model_revision, "
     "input_rate_per_million, output_rate_per_million, token_rules_version, "
     "effective_from) values ('pv-neg', 'm', -1, 0, 'tr', now())"),
    ("a price window that ends before it starts",
     "insert into infrx.price_versions (price_version, model_revision, "
     "input_rate_per_million, output_rate_per_million, token_rules_version, "
     "effective_from, effective_to) values ('pv-back', 'm', 1, 1, 'tr', now(), "
     "now() - interval '1 day')"),
    ("rewriting a price version (R45)",
     "update infrx.price_versions set input_rate_per_million = 0"),
    ("a job priced by a version that does not exist",
     _job_values("52000000-0000-4000-8000-0000000000fe", "job_vprice").replace(
         "'pv-1', ", "'pv-nope', ") + ")"),
    ("finalized media with no digest",
     f"insert into infrx.staged_media (org_id, handle, kind, state, storage_ref, "
     f"finalized_at) values ('{ORG_A}', 'upl_bad', 'upload', 'finalized', 'k', now())"),
    ("rewriting finalized media",
     f"update infrx.staged_media set digest = '{'sha256:' + 'cd' * 32}' "
     f"where org_id = '{ORG_A}' and handle = 'upl_a'"),
    ("editing ledger history",
     f"update public.credit_ledger set delta_usd = 0 where org_id = '{ORG_A}'"),
    ("deleting ledger history", f"delete from public.credit_ledger"),
    ("a pilot usage row with no outcome",
     "update public.usage_events set settlement_regime = 'pilot' "
     "where id = '90000000-0000-4000-8000-000000000001'"),
    ("an unknown usage outcome",
     "update public.usage_events set outcome = 'vanished' "
     "where id = '90000000-0000-4000-8000-000000000001'"),
    ("an unknown settlement state",
     "update public.usage_events set settlement_state = 'maybe' "
     "where id = '90000000-0000-4000-8000-000000000001'"),
    # --- r2: tenant coherence by composite key (ruling 7) ---
    ("a hold on one tenant's job against another's credit",
     f"insert into infrx.credit_holds (request_id, org_id, amount, state) "
     f"values ('{JOB_B}', '{ORG_A}', 1, 'held')"),
    ("a hold naming another tenant's key",
     f"insert into infrx.credit_holds (request_id, org_id, key_id, amount, state) "
     f"values ('{JOB_RUNNING}', '{ORG_A}', "
     f"'44444444-4444-4444-8444-4444444444bb', 1, 'held')"),
    ("a reservation on another tenant's job",
     f"insert into infrx.capacity_reservations (request_id, kind, org_id, amount) "
     f"values ('{JOB_B}', 'inference', '{ORG_A}', 1)"),
    ("a job keyed by another tenant's api key",
     _job_values("52000000-0000-4000-8000-0000000000fd", "job_vkey").replace(
         f"'{KEY_A}'", "'44444444-4444-4444-8444-4444444444bb'") + ")"),
    ("an idempotency row pointing at another tenant's job",
     f"insert into infrx.idempotency (org_id, operation, key, payload_digest, request_id) "
     f"values ('{ORG_B}', 'chat.completions', 'k3', '{DIGEST}', '{JOB_QUEUED}')"),
    ("a judge reservation against another tenant's budget",
     f"insert into infrx.judge_reservations (run_id, org_id, period_start, amount, state) "
     f"values ('60000000-0000-4000-8000-0000000000bb', '{ORG_A}', '{PERIOD}', 1, 'held')"),
    ("a judge sample naming another tenant's request",
     f"insert into infrx.judge_samples (run_id, org_id, sample_id, rubric_version, "
     f"request_id) values ('{RUN_ID}', '{ORG_A}', 's-foreign', 3, '{JOB_B}')"),
    ("a callback delivery of one tenant's event to another's destination",
     f"insert into infrx.callback_deliveries (event_id, destination_id, org_id, state) "
     f"values ('{EVENT_ID}', '{DEST_ID}', '{ORG_B}', 'failed')"),
    ("an outbox event with no organization",
     f"insert into infrx.outbox (event_id, aggregate_id, kind, available_at) values "
     f"('70000000-0000-4000-8000-0000000000fd', '{JOB_QUEUED}', 'usage_projection', "
     f"now())"),
    ("a pilot usage row under another tenant",
     f"insert into public.usage_events (id, org_id, model_id, status, "
     f"settlement_regime, outcome, settlement_state, settlement_version) values "
     f"('{JOB_TERMINAL}', '{ORG_B}', 'nemostation/marlin-2b', 200, 'pilot', "
     f"'completed', 'settled', 1)"),
    ("a ledger row against another tenant's request",
     f"insert into public.credit_ledger (org_id, delta_usd, kind, request_id) values "
     f"('{ORG_B}', -1, 'usage', '{JOB_TERMINAL}')"),
    # --- r2: money signs and one debit per request (ruling 7) ---
    ("a negative grant",
     f"insert into public.credit_ledger (org_id, delta_usd, kind) values "
     f"('{ORG_A}', -5, 'grant')"),
    ("a positive usage debit",
     f"insert into public.credit_ledger (org_id, delta_usd, kind) values "
     f"('{ORG_A}', 5, 'usage')"),
    ("a zero-delta ledger row",
     f"insert into public.credit_ledger (org_id, delta_usd, kind) values "
     f"('{ORG_A}', 0, 'adjustment')"),
    ("an operator-made ledger row with nothing to audit it",
     f"insert into public.credit_ledger (org_id, delta_usd, kind, by_operator) values "
     f"('{ORG_A}', 5, 'grant', true)"),
    ("a second usage debit for one request",
     f"insert into public.credit_ledger (org_id, delta_usd, kind, request_id) values "
     f"('{ORG_A}', -1, 'usage', '{JOB_TERMINAL}'), "
     f"('{ORG_A}', -1, 'usage', '{JOB_TERMINAL}')"),
    # --- r2: the nonblocking checks that were one line each ---
    ("a debit past the reserved envelope",
     _job_values("52000000-0000-4000-8000-0000000000fc", "job_vdebit", state="succeeded",
                 extra="settled_at, outcome_cause, settlement_state, usage_certainty, "
                       "result_ref, debit")
     + ", '2026-09-21T00:05:00Z', 'completed', 'settled', 'authoritative', 'r', 99)"),
    ("a settlement on usage that was never authoritative",
     _job_values("52000000-0000-4000-8000-0000000000fb", "job_vcert", state="succeeded",
                 extra="settled_at, outcome_cause, settlement_state, usage_certainty, "
                       "result_ref")
     + ", '2026-09-21T00:05:00Z', 'completed', 'settled', 'unknown', 'r')"),
    ("a held_unknown outcome with authoritative usage",
     _job_values("52000000-0000-4000-8000-0000000000fa", "job_vheld", state="failed",
                 extra="settled_at, outcome_cause, settlement_state, usage_certainty, "
                       "reconcile_after")
     + ", '2026-09-21T00:05:00Z', 'engine_error', 'held_unknown', 'authoritative',"
       " '2026-09-22T00:00:00Z')"),
    ("a deadline before the admission",
     _job_values("52000000-0000-4000-8000-0000000000f9", "job_vdead").replace(
         "'2026-09-21T00:10:00Z'", "'2026-09-20T00:00:00Z'") + ")"),
    ("queue time spent beyond the queue budget",
     f"update infrx.jobs set queue_wait_used_s = 9999 "
     f"where request_id = '{JOB_QUEUED}'"),
    ("rewriting an admitted price snapshot (R53)",
     f"update infrx.jobs set price_snapshot = '{{}}'::jsonb "
     f"where request_id = '{JOB_QUEUED}'"),
    ("rewriting an admitted token ceiling",
     f"update infrx.jobs set max_output_tokens = 4096 "
     f"where request_id = '{JOB_QUEUED}'"),
    ("rewriting a settled debit",
     f"update infrx.jobs set debit = 0 where request_id = '{JOB_TERMINAL}'"),
    ("deleting a settled job",
     f"delete from infrx.jobs where request_id = '{JOB_TERMINAL}'"),
    ("a suspension with no reason or date",
     f"update public.organizations set suspended = true where id = '{ORG_A}'"),
    ("a free-text suspension reason",
     f"update public.organizations set suspended = true, suspended_at = now(), "
     f"suspension_reason = 'called the CEO names' where id = '{ORG_A}'"),
    ("a journal chunk past the event byte limit",
     f"insert into infrx.stream_chunks (job_id, generation, sequence, event_type, "
     f"payload, bytes, expires_at) values ('{JOB_RUNNING}', 1, 9, 'delta', "
     f"jsonb_build_object('d', repeat('x', 1100000)), 1100000, now() + interval '1h')"),
    ("a chunk whose byte count understates its payload",
     f"insert into infrx.stream_chunks (job_id, generation, sequence, event_type, "
     f"payload, bytes, expires_at) values ('{JOB_RUNNING}', 1, 8, 'delta', "
     f"jsonb_build_object('d', repeat('x', 4000)), 1, now() + interval '1h')"),
)

#: Statements that must be ACCEPTED. Without these, a primary key that drops a column
#: (B9: m19 `kind`, m21 `operation`, m23 `generation`) looks exactly like a stricter
#: schema, and every "must be refused" case still passes.
ACCEPTED = (
    ("R46: a preparation and an inference attempt share generation 1",
     f"insert into infrx.attempts (job_id, kind, generation, worker_id, acquired_at, "
     f"expires_at, generation_deadline_at, first_token_deadline_at) values "
     f"('{JOB_QUEUED}', 'preparation', 1, 'w', '2026-09-21T00:00:10Z', "
     f"'2026-09-21T00:00:40Z', '2026-09-21T00:02:00Z', null), "
     f"('{JOB_QUEUED}', 'inference', 1, 'w', '2026-09-21T00:03:00Z', "
     f"'2026-09-21T00:05:00Z', '2026-09-21T00:08:00Z', '2026-09-21T00:04:00Z')"),
    ("one idempotency key in two operations",
     f"insert into infrx.idempotency "
     f"(org_id, operation, key, payload_digest, request_id, feedback_id) values "
     f"('{ORG_A}', 'chat.completions', 'shared', '{DIGEST}', '{JOB_QUEUED}', null), "
     f"('{ORG_A}', 'feedback.submit', 'shared', '{DIGEST}', null, 'fb_1')"),
    ("the same sequence in two generations of one journal",
     f"insert into infrx.stream_chunks (job_id, generation, sequence, event_type, "
     f"payload, bytes, expires_at) values "
     f"('{JOB_RUNNING}', 1, 1, 'delta', '{{}}'::jsonb, 2, now() + interval '1h'), "
     f"('{JOB_RUNNING}', 2, 1, 'delta', '{{}}'::jsonb, 2, now() + interval '1h')"),
)


def check_row_constraints(conn) -> str:
    """Every row check, exclusion and immutability trigger refuses its violation - and
    every shape the schema must still accept is accepted, so a key that lost a column
    cannot pass as a stricter schema."""
    survived = []
    for invariant, sql in VIOLATIONS:
        try:
            with conn.transaction():
                conn.execute(sql)
                raise _Allowed()
        except _Allowed:
            survived.append(invariant)
        except psycopg.Error:
            continue
    assert not survived, "the schema accepted:\n  " + "\n  ".join(survived)
    refused = []
    for invariant, sql in ACCEPTED:
        try:
            with conn.transaction():
                conn.execute(sql)
                raise _Allowed()
        except _Allowed:
            continue
        except psycopg.Error as wrongly:
            refused.append(f"{invariant}: {_first_line(wrongly)}")
    assert not refused, "the schema refused what it must accept:\n  " + "\n  ".join(refused)
    return (f"{len(VIOLATIONS)} row-level violations refused, "
            f"{len(ACCEPTED)} required shapes accepted")


# --- bounded access paths -----------------------------------------------------
#: (path, index that must appear in the plan, query).
PLANS = (
    ("job pagination by org, time and id", "jobs_org_created_idx",
     f"select request_id from infrx.jobs where org_id = '{ORG_A}' "
     f"and created_at < now() order by created_at desc, request_id desc limit 100"),
    ("active jobs per organization", "jobs_org_active_idx",
     f"select count(*) from infrx.jobs where org_id = '{ORG_A}' "
     f"and state in ('preparing','queued','running')"),
    ("queued jobs past their queue deadline", "jobs_queue_deadline_idx",
     "select request_id from infrx.jobs where state = 'queued' "
     "and queue_deadline_at < now() limit 100"),
    ("pending outbox events", "outbox_pending_idx",
     "select event_id from infrx.outbox where acknowledged_at is null "
     "and available_at <= now() order by available_at, event_id limit 50"),
    ("expiring leases", "attempts_expiring_idx",
     "select job_id from infrx.attempts where released_at is null "
     "and expires_at < now() limit 100"),
    ("aged unknown-usage holds", "credit_holds_reconcile_idx",
     "select request_id from infrx.credit_holds where state = 'unknown' "
     "and reconcile_after < now() limit 100"),
    ("journal replay by cursor", "stream_chunks_pkey",
     f"select sequence from infrx.stream_chunks where job_id = '{JOB_TERMINAL}' "
     f"and (generation, sequence) > (1, 0) order by generation, sequence limit 100"),
    ("feedback pagination by org and time", "feedback_org_created_idx",
     f"select feedback_id from infrx.feedback where org_id = '{ORG_A}' "
     f"order by created_at desc, feedback_id desc limit 100"),
    ("ready callback deliveries", "callback_deliveries_ready_idx",
     "select event_id from infrx.callback_deliveries where state = 'pending' "
     "and next_attempt_at <= now() limit 100"),
    ("active holds by organization", "credit_holds_org_active_idx",
     f"select request_id from infrx.credit_holds where org_id = '{ORG_A}' "
     f"and state in ('held','unknown') limit 100"),
    ("usage pagination by org and time", "usage_events_org_created_idx",
     f"select id from public.usage_events where org_id = '{ORG_A}' "
     f"order by created_at desc limit 100"),
    ("ledger pagination by org and time", "credit_ledger_org_created_idx",
     f"select id from public.credit_ledger where org_id = '{ORG_A}' "
     f"order by created_at desc limit 100"),
)

#: Ruling 6: a `security_barrier` view must still push the caller's tenant qual DOWN to
#: the base relation. If it did not, every row of every tenant would be materialised
#: before the filter ran - which is both the leak B5 found and an unbounded scan.
PUSHDOWN = (
    ("console_usage by org", "usage_events",
     f"select request_id from public.console_usage where org_id = '{ORG_A}' "
     f"order by created_at desc limit 100"),
    ("console_usage by org and time", "usage_events",
     f"select request_id from public.console_usage where org_id = '{ORG_A}' "
     f"and created_at < now() limit 100"),
    ("console_ledger by org", "credit_ledger",
     f"select id from public.console_ledger where org_id = '{ORG_A}' limit 100"),
    ("wallets by org", "wallets",
     f"select ledger_total from public.wallets where org_id = '{ORG_A}'"),
)


def check_view_pushdown(conn) -> str:
    """The tenant qual reaches the base relation inside every barrier view.

    An ordered index scan is NOT available through a barrier (the view's own quals sit
    between the ordering and the index), so the console's bounded pages sort what the
    pushed-down qual selected; what must never happen is the qual being applied ABOVE
    the barrier, which would scan every tenant's rows first.
    """
    problems = []
    for path, relation, query in PUSHDOWN:
        plan = "\n".join(line for line, in conn.execute(f"explain (costs off) {query}"))
        scan = [line for line in plan.splitlines()
                if f"Scan on {relation}" in line or f"Scan using" in line and relation in line]
        if not scan:
            problems.append(f"{path}: no scan of {relation}\n{plan}")
            continue
        index = plan.split(scan[0], 1)[1].splitlines()[:3]
        if not any("org_id = " in line for line in index):
            problems.append(f"{path}: the tenant qual did not reach {relation}\n{plan}")
    assert not problems, "a barrier view did not push the tenant qual down:\n" + \
        "\n".join(problems)
    return f"{len(PUSHDOWN)} view queries push their tenant qual to the base relation"


def check_index_plans(conn) -> str:
    """Every bounded access path of 06 is served by an index, with no sequential scan.

    The tables are seeded with enough rows that the planner chooses on cost, so this
    is not `enable_seqscan = off` being asked a leading question.
    """
    problems = []
    for path, index, query in PLANS:
        plan = "\n".join(line for line, in conn.execute(f"explain (costs off) {query}"))
        if index not in plan:
            problems.append(f"{path}: {index} unused\n{plan}")
        elif "Seq Scan" in plan:
            problems.append(f"{path}: sequential scan\n{plan}")
    assert not problems, "bounded access paths without an index:\n" + "\n".join(problems)
    return f"{len(PLANS)} access paths index-served"



# --- privileges, statement by statement (r2, ruling 4) -------------------------
#: The WHOLE browser-reachable privilege surface: relation -> {verb: columns or None}.
#: `None` means the table-level privilege. Anything not listed here must not be held by
#: `anon` or `authenticated`, which is what makes TRUNCATE (B3) visible: it was never
#: enumerated, so nobody noticed Supabase's default ALL grant still carried it.
EXPECTED_PRIVILEGES = {
    ("authenticated", "profiles"): {"SELECT": None,
                                    "UPDATE": {"full_name", "avatar_url"}},
    ("authenticated", "organizations"): {"SELECT": None, "UPDATE": {"name", "slug"}},
    ("authenticated", "org_members"): {"SELECT": None},
    ("authenticated", "models"): {"SELECT": None},
    ("authenticated", "api_keys"): {
        "SELECT": None,
        "INSERT": {"org_id", "created_by", "name", "prefix", "key_hash"},
        "UPDATE": {"name", "revoked_at"}},
    ("authenticated", "usage_events"): {"SELECT": None},
    ("authenticated", "credit_ledger"): {"SELECT": None},
    # The console read surface: read-only, by name.
    **{("authenticated", view.split(".", 1)[1]): {"SELECT": None}
       for view, _kind in (
           ("public.wallets", ""), ("public.console_ledger", ""),
           ("public.console_usage", ""), ("public.org_settings", ""),
           ("public.consent_history", ""), ("public.feedback", ""),
           ("public.calibration_labels", ""), ("public.console_judge_runs", ""),
           ("public.console_admin_orgs", ""), ("public.operator_audit", ""))},
}

#: Relations whose whole point is that nothing is ever removed (0003's trigger list).
TRUNCATE_GUARDED = (
    "public.credit_ledger", "public.usage_events", "infrx.jobs", "infrx.attempts",
    "infrx.credit_holds", "infrx.capacity_reservations", "infrx.stream_chunks",
    "infrx.outbox", "infrx.idempotency", "infrx.feedback", "infrx.consent_history",
    "infrx.price_versions", "infrx.audit_entries", "infrx.wallets",
    "infrx.judge_runs", "infrx.judge_reservations", "infrx.judge_samples",
    "infrx.callback_deliveries",
)


def check_privileges(conn) -> str:
    """Ruling 4: the browser-reachable privilege surface is exactly the enumerated one.

    Table-level grants are compared verb by verb (so TRUNCATE, REFERENCES and TRIGGER
    cannot hide in an ALL grant) and column grants column by column.
    """
    problems = []
    table_grants: dict[tuple, dict] = {}
    for role, table, verb in conn.execute("""
            select grantee, table_name, privilege_type
            from information_schema.role_table_grants
            where table_schema = 'public' and grantee in ('anon','authenticated','PUBLIC')
            """).fetchall():
        table_grants.setdefault((role, table), {})[verb] = None
    column_grants: dict[tuple, dict] = {}
    for role, table, verb, column in conn.execute("""
            select grantee, table_name, privilege_type, column_name
            from information_schema.column_privileges
            where table_schema = 'public' and grantee in ('anon','authenticated','PUBLIC')
            """).fetchall():
        column_grants.setdefault((role, table), {}).setdefault(verb, set()).add(column)

    for key, verbs in sorted(table_grants.items()):
        role, table = key
        expected = EXPECTED_PRIVILEGES.get(key, {})
        extra = sorted(v for v in verbs if expected.get(v, "missing") is not None)
        if extra:
            problems.append(f"{role} holds table-level {', '.join(extra)} on {table}")
    for key, verbs in sorted(column_grants.items()):
        role, table = key
        expected = EXPECTED_PRIVILEGES.get(key)
        if expected is None:
            problems.append(f"{role} holds {sorted(verbs)} on {table}, which is not "
                            f"in the enumerated surface")
            continue
        for verb, columns in sorted(verbs.items()):
            allowed = expected.get(verb, "absent")
            if allowed == "absent":
                problems.append(f"{role} holds {verb} on {table} ({sorted(columns)})")
            elif allowed is not None and not columns <= allowed:
                problems.append(f"{role} holds {verb} on {table} columns "
                                f"{sorted(columns - allowed)} beyond {sorted(allowed)}")
    for key in EXPECTED_PRIVILEGES:
        if key not in table_grants and key not in column_grants:
            problems.append(f"{key[0]} lost every grant on {key[1]}: the console breaks")
    assert not problems, "the privilege surface is not the enumerated one:\n  " + \
        "\n  ".join(problems)
    # And the pilot schema itself is unreachable, which is what m09 and m08 claim.
    for role in ("anon", "authenticated"):
        assert not conn.execute("select has_schema_privilege(%s, 'infrx', 'usage')",
                                (role,)).fetchone()[0], \
            f"{role} holds USAGE on schema infrx"
        assert not conn.execute(
            "select has_function_privilege(%s, 'infrx.now()', 'execute')",
            (role,)).fetchone()[0], f"{role} may call the store's clock"
    return (f"{len(EXPECTED_PRIVILEGES)} enumerated grants and nothing else; "
            f"infrx unreachable")


#: Ruling 2: an operator's identity or free text must not live in a relation a customer
#: can SELECT. These are the column names that carry one.
OPERATOR_IDENTITY_COLUMNS = ("operator_principal", "suspended_by", "actor_principal",
                             "author_principal", "created_by", "updated_by",
                             "changed_by", "owner_email")


def check_no_operator_identity_in_public(conn) -> str:
    """Ruling 2 / B2(b,c): the operator principal is stored only where customers cannot
    read it.

    A column grant is not enough on a legacy table - the deployed console may
    `select *`, and 0001's policies already give members SELECT on `credit_ledger`,
    `organizations` and `profiles`. So the rule is structural: no base table in `public`
    that `authenticated` may read carries an operator identity column, and the masked
    views are the only place a principal appears at all.
    """
    # pg_catalog, not information_schema: the oid form of `has_table_privilege` cannot
    # be handed the name of something that no longer exists.
    readable = [(table, column) for table, column in conn.execute("""
        select c.relname, a.attname
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
        where n.nspname = 'public' and c.relkind = 'r'
          and has_table_privilege('authenticated', c.oid, 'select')
        order by 1, 2""").fetchall()]
    offending = [f"public.{table}.{column}" for table, column in readable
                 if column in OPERATOR_IDENTITY_COLUMNS
                 and not (table, column) in (("profiles", "created_by"),)]
    # `created_by` is 0001's own column on api_keys/organizations/credit_ledger: it names
    # a *member* for tenant rows, and the masked views are what decide whether a viewer
    # may resolve it to a person. What must not exist is a column carrying the platform
    # side's identity or prose.
    offending = [name for name in offending
                 if name.endswith(("operator_principal", "suspended_by"))]
    assert not offending, \
        f"an operator identity is stored where customers can read it: {offending}"
    # And the customer-safe suspension reason is a code, not prose.
    codes = conn.execute("""
        select pg_get_constraintdef(oid) from pg_constraint
        where conrelid = 'public.organizations'::regclass
          and conname = 'organizations_suspension_reason_check'""").fetchone()
    assert codes, "public.organizations.suspension_reason is unconstrained free text"
    return (f"{len(readable)} customer-readable columns hold no operator identity; "
            f"the suspension reason is a closed code")


def check_truncate_refused(conn) -> str:
    """B3: TRUNCATE is refused for every role, including the one that owns the data.

    RLS does not apply to TRUNCATE and a row trigger never sees it, so a privilege was
    the only thing standing between `anon` and `truncate public.credit_ledger cascade`.
    Now the privilege is gone AND a statement trigger refuses it - a privilege can be
    re-granted by accident, a trigger cannot.
    """
    survived = []
    for relation in TRUNCATE_GUARDED:
        for session in ("anon", "member", "owner", "operator", "service"):
            refusal = _attempt(conn, session, f"truncate {relation} cascade")
            if refusal is None:
                survived.append(f"{session} truncated {relation}")
    # And as the migration owner (superuser), where only the trigger can refuse.
    for relation in TRUNCATE_GUARDED:
        try:
            with conn.transaction():
                conn.execute(f"truncate {relation} cascade")
                raise _Allowed()
        except _Allowed:
            survived.append(f"the table owner truncated {relation}")
        except psycopg.Error:
            continue
    assert not survived, "TRUNCATE was accepted:\n  " + "\n  ".join(survived)
    return (f"{len(TRUNCATE_GUARDED)} append-only relations refuse TRUNCATE for "
            f"5 browser/service sessions and for the owner")


def check_leaky_function_probe(conn) -> str:
    """Ruling 6 / B5: a caller's own cheap function in the WHERE clause must not see
    rows the tenant predicate excludes.

    Without `security_barrier` the view is flattened into the caller's query and the
    planner orders the quals by cost, so a `cost 1e-7` function runs before
    `is_org_member(org_id)` and sees every row - which is how the reviewer read every
    organization's wallet as ORG_B's owner.

    The probe must be **stable**, not volatile: PostgreSQL evaluates a volatile qual
    last whatever its cost, so a probe that writes to a table cannot see the leak it is
    looking for (the first version of this check passed with the barrier removed, which
    is exactly the false negative R32 exists to catch). It signals through a notice
    instead, which a stable function may raise.
    """
    seen: list[str] = []
    conn.execute("""
        create or replace function public._d1_probe(p_relation text, p_value text)
        returns boolean language plpgsql stable cost 0.0000001 as $$
        begin
          raise notice 'D1PROBE %|%', p_relation, coalesce(p_value, '-');
          return true;
        end $$;""")

    def collect(diagnostic) -> None:
        message = getattr(diagnostic, "message_primary", "") or ""
        if message.startswith("D1PROBE "):
            seen.append(message.removeprefix("D1PROBE "))

    conn.add_notice_handler(collect)
    # Every probe reports the row's ORGANIZATION, so "did it see a row it must not" is
    # an exact question: the only ids it may report are the ones this session is a
    # member of (which includes the personal organization the signup trigger made).
    probes = (("public.wallets", "coalesce(org_id::text, '-')"),
              ("public.feedback", "coalesce(org_id::text, '-')"),
              ("public.console_ledger", "coalesce(org_id::text, '-')"),
              ("public.console_usage", "coalesce(org_id::text, '-')"),
              ("public.operator_audit", "coalesce(target_org_id::text, '-')"))
    try:
        for relation, expression in probes:
            # As ORG_B's owner: everything the probe sees must belong to ORG_B.
            read_or_denied(conn, "other",
                           f"select count(*) from {relation} "
                           f"where public._d1_probe('{relation}', {expression})")
    finally:
        conn.remove_notice_handler(collect)
        conn.execute("drop function if exists public._d1_probe(text, text)")
    own = {str(org) for org, in conn.execute(
        "select org_id from public.org_members where user_id = %s", (USER_OTHER,)).fetchall()}
    own.add("-")
    leaked = [entry for entry in seen if entry.split("|", 1)[1] not in own]
    assert seen, ("the probe was never evaluated, so this check proves nothing "
                  "(did the notice handler or the function cost change?)")
    assert not leaked, ("a leaky function in the WHERE clause saw another tenant's "
                        f"rows: {leaked[:8]}")
    return (f"{len(probes)} views probed with a cheap stable leaky function "
            f"({len(seen)} evaluations); nothing foreign seen")


def check_legacy_writer_does_not_drift(conn) -> str:
    """B8 / ruling 8: the deployed console's `addCredit` path cannot make the wallet
    summary disagree with the ledger.

    This is exactly `app/(console)/admin/actions.ts`: an insert into `credit_ledger`
    through the service role, with no knowledge of `infrx.wallets`.
    """
    before = conn.execute("select ledger_total from infrx.wallets where org_id = %s",
                          (ORG_A,)).fetchone()[0]
    with conn.transaction():
        conn.execute("set local role service_role")
        conn.execute(f"""
            insert into public.credit_ledger (org_id, delta_usd, kind, reason, created_by)
            values ('{ORG_A}', 5.00000000, 'grant', 'legacy console top-up',
                    '{USER_OPERATOR}')""")
    after = conn.execute("select ledger_total from infrx.wallets where org_id = %s",
                         (ORG_A,)).fetchone()[0]
    assert after == before + Decimal("5.00000000"), \
        f"a legacy ledger insert did not move the wallet: {before} -> {after}"
    drift = conn.execute("""select org_id, ledger_drift from infrx.wallet_reconciliation
                            where ledger_drift <> 0 or reserved_drift <> 0""").fetchall()
    assert not drift, f"the legacy writer left the summary drifting: {drift}"
    # Both figures a customer can see must agree, and both are read as a member: the
    # 0001 reporting functions are SECURITY INVOKER and refuse a caller who is nobody.
    summary = read_rows(conn, "member",
                        f"select ledger_total from public.org_wallet_summary('{ORG_A}')")
    balance = read_rows(conn, "member", f"select public.org_balance('{ORG_A}')")
    assert Decimal(summary[0][0]) == balance[0][0], \
        f"org_wallet_summary {summary[0][0]} disagrees with org_balance {balance[0][0]}"
    return f"a legacy service-role grant moved the wallet to {after}; reconciliation clean"




# --- the console read surface (0005) ------------------------------------------
#: (view, "tenant" | "owner" | "operator"). A tenant view must show a member only their
#: own organization's rows; an operator view must show a non-operator nothing.
READ_VIEWS = (
    ("public.wallets", "tenant"),
    ("public.console_ledger", "tenant"),
    ("public.console_usage", "tenant"),
    ("public.org_settings", "tenant"),
    ("public.consent_history", "tenant"),
    ("public.feedback", "tenant"),
    ("public.console_judge_runs", "owner"),
    ("public.calibration_labels", "operator"),
    ("public.console_admin_orgs", "operator"),
    ("public.operator_audit", "operator"),
)


class _Rollback(Exception):
    """Carries the rows out of the transaction that is then rolled back."""

    def __init__(self, rows: list) -> None:
        super().__init__("rollback")
        self.rows = rows


def read_rows(conn, session: str, sql: str) -> list:
    """Read under that session's role, then roll the transaction back (`set local`).

    Named `read_rows` because it returns ROWS: a check that asks a view for `count(*)`
    lets the planner drop the joins whose columns nobody selected, which is how B1's
    operator view passed a test while raising `42501` in production.
    """
    try:
        with conn.transaction():
            conn.execute(SESSIONS[session])
            raise _Rollback(conn.execute(sql).fetchall())
    except _Rollback as done:
        return done.rows


def check_console_read_surface(conn) -> str:
    """The console's read surface (0005) is owner's-rights over `infrx`, so a missing
    predicate leaks every tenant. Each view is checked for what it shows whom - by
    selecting its COMPUTED columns, never `count(*)`: the planner drops a join whose
    columns nobody selects, and B1's operator view was "passing" that way while it in
    fact raised 42501 for every caller but the platform key."""
    problems = []
    for view, kind in READ_VIEWS:
        columns = _view_columns(conn, view)
        # C wraps every port in a tenant check that refuses a row without `org_id` or
        # with a foreign one, because these views hand an operator or the platform key
        # every organization's rows. So the column has to be on every relation.
        if "org_id" not in columns:
            problems.append(f"{view}: no org_id column for C's tenant check")
        projection = ", ".join(columns)
        # `anon` holds no SELECT at all after ruling 4, so the read is refused rather
        # than empty. Both are "sees nothing"; refused is the stronger one.
        if read_or_denied(conn, "anon", f"select {projection} from {view}"):
            problems.append(f"{view}: anon sees rows")
        if kind == "operator":
            for session in ("member", "owner"):
                if read_or_denied(conn, session, f"select {projection} from {view}"):
                    problems.append(f"{view}: a {session} session sees operator rows")
            if not read_rows(conn, "operator", f"select {projection} from {view}"):
                problems.append(f"{view}: an operator sees nothing, so the denials "
                                f"above prove nothing")
            continue
        session = "owner" if kind == "owner" else "member"
        if not read_rows(conn, session, f"select {projection} from {view}"):
            problems.append(f"{view}: a {session} of the seeded org sees nothing, so the "
                            f"cross-tenant check below proves nothing")
        if kind == "owner" and read_or_denied(conn, "member",
                                              f"select {projection} from {view}"):
            problems.append(f"{view}: a plain member sees owner-only rows")
        # Not "nothing but ORG_A": a user may belong to several organizations (the
        # signup trigger gives everyone a personal one). The invariant is that ORG_B's
        # row - a tenant this session is not in - is invisible, which is why the fixture
        # seeds one in every relation.
        if read_or_denied(conn, session,
                          f"select {projection} from {view} where org_id = '{ORG_B}'"):
            problems.append(f"{view}: a session of one org sees another org's rows")
    assert not problems, "the console read surface leaks:\n  " + "\n  ".join(problems)

    # R41/R50 by PROVEN MEMBERSHIP (ruling 1), not by a marker that defaults false.
    ledger = "select reason, actor, by_operator from public.console_ledger order by reason"
    customer = dict((reason, actor) for reason, actor, _ in read_rows(conn, "member", ledger))
    assert customer["pilot credit"] == "platform", \
        f"a customer read a marked operator principal: {customer}"
    assert customer["unmarked operator grant"] == "platform", \
        ("a customer read an UNMARKED operator principal - the masking still keys on "
         f"`by_operator`, which fails open for history: {customer}")
    assert customer["by the owner"] == "owner@example.com", \
        f"a customer cannot see their own member's principal: {customer}"
    operator = dict((reason, actor) for reason, actor, _ in read_rows(conn, "operator", ledger))
    assert operator["pilot credit"] == "operator@example.com", \
        f"an operator did not read the real ledger principal: {operator}"

    consent = read_rows(conn, "member", "select version, changed_by from "
                                        f"public.consent_history where org_id = '{ORG_A}'")
    by_version = dict(consent)
    assert by_version[2] == "platform", \
        f"a customer read the operator who changed their consent: {by_version}"
    feedback = dict(read_rows(conn, "member", "select name, author_principal from "
                                              "public.feedback where org_id = "
                                              f"'{ORG_A}'"))
    assert feedback["comment"] == "platform", \
        f"a customer read an operator principal off a feedback entry: {feedback}"
    assert feedback["thumb"] == "owner@example.com" or \
        feedback["thumb"] == USER_OWNER, f"their own member's principal was masked: {feedback}"

    labels = read_rows(conn, "operator", "select count(*) from public.feedback "
                                         "where calibration_set")
    assert labels[0][0] == 0, "a calibration label appeared in a feedback list (R49)"
    assert read_rows(conn, "operator",
                     "select id from public.calibration_labels")

    # r3 (N1): the sample count is the real count, and the array is capped at 50.
    counts = {n: (c, len(s)) for n, c, s in read_rows(
        conn, "owner",
        "select id, sample_count, samples from public.console_judge_runs "
        "where id::text like '61000000-%' order by id")}
    expected = {"61000000-0000-4000-8000-000000000000": (0, 0),
                "61000000-0000-4000-8000-000000000001": (1, 1),
                "61000000-0000-4000-8000-000000000050": (50, 50),
                "61000000-0000-4000-8000-000000000075": (75, 50)}
    observed = {str(run): value for run, value in counts.items()}
    assert observed == expected, \
        f"sample_count/len(samples) is {observed}, expected {expected}"

    # One row per organization, however many owners it has (B1).
    orgs = read_rows(conn, "operator",
                     "select org_id, owner_email from public.console_admin_orgs")
    assert len(orgs) == len({org for org, _ in orgs}), \
        f"console_admin_orgs duplicates an organization: {sorted(orgs)}"

    # Ruling 10: money crosses as TEXT with eight fractional digits, never as a JSON
    # number, and timestamps stay timestamptz (PostgREST renders `…+00:00`).
    money = read_rows(conn, "member", "select ledger_total, reserved_total, available "
                                      f"from public.wallets where org_id = '{ORG_A}'")[0]
    assert all(isinstance(value, str) for value in money), \
        f"money left a view as something other than text: {money}"
    assert money[0].endswith(".00000000") or "." in money[0], money
    kinds = dict(conn.execute("""
        select column_name, data_type from information_schema.columns
        where table_schema = 'public' and table_name = 'console_usage'""").fetchall())
    assert kinds["cost"] == "text" and kinds["max_hold"] == "text", kinds
    assert kinds["created_at"] == "timestamp with time zone", kinds
    # `key_id` is null exactly when `key_name` is (a deleted key keeps its usage row).
    assert kinds["key_id"] == "uuid", kinds
    pairs = read_rows(conn, "member", "select key_id, key_name from public.console_usage")
    assert all((key_id is None) == (name is None) for key_id, name in pairs), pairs

    # The two aggregates C cannot express over a view (ruling 10).
    summary = read_rows(conn, "member",
                        f"select org_id, requests, cost, pending_reconciliation from "
                        f"public.console_usage_summary('{ORG_A}', "
                        f"'2000-01-01T00:00:00Z', '2100-01-01T00:00:00Z')")[0]
    assert str(summary[0]) == ORG_A, f"the summary row carries no tenant: {summary}"
    assert summary[1] >= 1, f"the usage summary counted nothing: {summary}"
    assert isinstance(summary[2], str) and isinstance(summary[3], str), \
        f"the summary returned money as a number: {summary}"
    # An empty window still answers zero rather than "no rows": a grouped aggregate
    # would hand C nothing to render.
    empty = read_rows(conn, "member",
                      f"select org_id, requests from public.console_usage_summary("
                      f"'{ORG_A}', '1999-01-01T00:00:00Z', '1999-01-02T00:00:00Z')")
    assert len(empty) == 1 and empty[0][1] == 0, f"an empty window returned {empty}"
    daily = read_rows(conn, "member",
                      f"select org_id, day, requests, cost from "
                      f"public.console_usage_daily('{ORG_A}', "
                      f"'2000-01-01T00:00:00Z', '2100-01-01T00:00:00Z')")
    # The fixture seeds 450 distinct days, so the bound is reachable and exact.
    assert len(daily) == 400, f"the daily bucket bound is not held: {len(daily)} rows"
    assert all(str(row[0]) == ORG_A for row in daily), \
        f"a daily bucket carries no tenant: {daily[0]}"
    assert isinstance(daily[0][3], str), f"daily cost is not text: {daily[0]}"
    for rpc in (f"select * from public.console_usage_summary('{ORG_B}', "
                f"'2000-01-01T00:00:00Z', '2100-01-01T00:00:00Z')",
                f"select * from public.console_usage_daily('{ORG_B}', "
                f"'2000-01-01T00:00:00Z', '2100-01-01T00:00:00Z')",
                f"select * from public.org_wallet_summary('{ORG_B}')"):
        try:
            read_rows(conn, "member", rpc)
        except psycopg.errors.InsufficientPrivilege:
            continue
        raise AssertionError(f"a console RPC answered for another organization: {rpc}")

    summary = read_rows(conn, "member",
                        f"select * from public.org_wallet_summary('{ORG_A}')")[0]
    assert str(summary[0]) == ORG_A, f"the wallet summary carries no tenant: {summary}"
    assert all(isinstance(value, str) for value in summary[1:]), \
        f"org_wallet_summary returned numbers, not Money strings: {summary}"
    assert Decimal(summary[1]) == conn.execute(
        "select ledger_total from infrx.wallets where org_id = %s", (ORG_A,)).fetchone()[0]
    return (f"{len(READ_VIEWS)} views tenant- and role-scoped on their computed columns; "
            f"principals masked by membership; money is text; 3 RPCs guarded")


def read_or_denied(conn, session: str, sql: str) -> list:
    """Rows, or `[]` when the read is refused outright - both are "sees nothing"."""
    try:
        return read_rows(conn, session, sql)
    except psycopg.errors.InsufficientPrivilege:
        return []


def _view_columns(conn, view: str) -> list[str]:
    schema, name = view.split(".", 1)
    return [c for c, in conn.execute("""
        select column_name from information_schema.columns
        where table_schema = %s and table_name = %s order by ordinal_position""",
        (schema, name)).fetchall()]


# --- the database clock -------------------------------------------------------
def check_test_clock(conn, second_session=None) -> str:
    """R7/S1 note 3: the store's now is movable in a task-local database...

    `second_session` is a callable returning another connection to the same database,
    because the offset has to be visible to every one of them.
    """
    conn.execute("select infrx_test.set_offset(0)")
    at_rest, = conn.execute("select infrx.now() - now()").fetchone()
    assert at_rest.total_seconds() == 0, f"the clock is offset at rest: {at_rest}"
    conn.execute("select infrx_test.advance(3600)")
    moved, = conn.execute("select infrx.now() - now()").fetchone()
    assert 3599 <= moved.total_seconds() <= 3601, f"advance(3600) moved {moved}"
    # Every session sees it: the conformance harness's hooks are synchronous on one
    # connection while the port runs on a pool (S1 note 2), so a session GUC would
    # move the clock for the hook alone.
    if second_session is not None:
        with second_session() as other:
            elsewhere, = other.execute("select infrx.now() - now()").fetchone()
        assert 3599 <= elsewhere.total_seconds() <= 3601, \
            f"another session did not see the offset: {elsewhere}"
    conn.execute("select infrx_test.set_offset(0)")
    back, = conn.execute("select infrx.now() - now()").fetchone()
    assert back.total_seconds() == 0
    return "movable in a task-local database, visible to every session"


def check_production_clock(conn) -> str:
    """...and immovable anywhere else, even with the fixture installed."""
    database, = conn.execute("select current_database()").fetchone()
    assert not database.startswith("infrx_"), \
        f"this check needs a production-like database name, not {database}"
    at_rest, = conn.execute("select infrx.now() - now()").fetchone()
    assert at_rest.total_seconds() == 0, f"a production clock is already offset: {at_rest}"
    installed, = conn.execute("""select count(*) from information_schema.tables
                                 where table_schema = 'infrx_test'""").fetchone()
    assert installed, "this check needs the test clock fixture installed to mean anything"
    conn.execute("select infrx_test.set_offset(86400)")
    after, = conn.execute("select infrx.now() - now()").fetchone()
    assert after.total_seconds() == 0, \
        f"a non-task-local database honoured a test clock offset: {after}"
    return f"{database}: offset ignored although infrx_test.clock exists and is set"
