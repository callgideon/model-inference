# C2-RPC — the Lab content-access RPC (lane lab-sql, LW3; WR-C2-1 / WR-C2-SQL-1)

- Base `0bfaa6ed` (lab-sql-lw2 head) · code `dd08e1ec` (C2-RPC) · lane head at handback in `D8-1079c32.md`
  · branch `codex/w5-lab-sql-lw3` · worktree `.claude/worktrees/codex-w5-lab-sql-lw3`.
- Tasklocal key `dlab` only (PG 57500, `infrx-dlab-postgres`, databases `infrx_dlab_*`). Nothing hosted,
  no AWS/SSM/S3/Vercel, no secrets. `0041` is LOCAL-ONLY (R151): never applied hosted.
- Contract source: the content lane's `infrx/content/{__init__,fakes}.py` on `codex/w5-content cf0b1df9`
  (C2 evidence `C2-ae7375e.md`, schema request WR-C2-SQL-1). The fake is the executable contract; every
  answer it gives is asserted on the RPC by a named check below.

## Changed paths (owned)

| Path | What |
|---|---|
| `apps/app/supabase/migrations/0041_lab_content_refs.sql` | `lab_content_refs` (append-only; only the handle's SHA-256), `lab_content_rights` (the fake's `_current` in SQL), `lab_content_ref_issue`/`_redeem`/`_held`, the session door `public.lab_content_ref_issue(p_handle_sha256, p_provider_org_id, p_grant_ref, p_request_id, p_purpose)` behind its own flag `lab_content` (OFF); N3's optional request: `lab_sample_tombstones`, `lab_sample_bounds`, `lab_tombstone_samples`, `lab_bound_samples`, `lab_blocked_samples`, `lab_permitted_samples`; rollback note in the header |
| `apps/infrx-api/infrx/state/lab_content.py` | `PgContentRefs` (= `infrx.content.ContentRefs`, answers typed `RefBinding`), `PgSampleRestrictions` (N3's gate) |
| `apps/infrx-api/tests/d/test_c2rpc_content.py` | 10 PG checks (role matrix: C1 + BOTH consumers, NEMO {DEV, ADMIN, VIEWER} + OTHER {BOTH}, BOTH in both products) |
| `apps/infrx-api/tests/d/test_c2rpc_units.py` | 2 unit cases (no DB), `ensure_ref_binding` (the content lane's `RefBinding`, or a field-for-field stand-in on a tree without `infrx.content`) |
| `apps/infrx-api/tests/d/{code_mutants_c2rpc,test_code_mutants_c2rpc}.py` | 50 SQL + 9 Python mutants, every check/case named |

## Design (the RPC answers the fake)

- **Rights** (`lab_content_rights`, default deny, one row or none): the (grantor, provider) pair's CURRENT grant version
  (R166) effective, not revoked, not expired at `infrx.now()`; naming the **job's** model (`infrx.jobs.model_id` of the
  grantor's own job — never the caller's), every category and the purpose; the user a current developer/administrator
  of the provider; retention bound = `jobs.created_at + retention_days` of the current version.
- **issue** order = the fake's: `invalid_request` (ttl ∉ 1..900, no/unknown category, unknown purpose, malformed digest
  or id) → `not_found` (`lab_grant_of(grant_ref, provider)` NULL — foreign or forged ref — or no job of the grantor with
  that request id) → `forbidden` (rights) → `result_expired` (least(now+ttl, grant expiry, retention) ≤ now) →
  `state_conflict` (digest already issued, checked last so a refused caller learns nothing). Answers the CURRENT
  grant version.
- **redeem**: `not_found` (unknown, or another provider or user) → `result_expired` (own expiry) → `forbidden` (rights with
  the ref's purpose + categories) → `result_expired` (current retention); expiry answered = least(ref expiry, retention).
- **held**: a ref of (org, request), unexpired, whose rights and retention still hold (T3's `Retention(holds=...)`).
- **Door**: `auth.uid()` only, categories {request_content, response_content} (R47), ttl 300; flag `lab_content` (55000
  when off or absent: the launched App's database answers nothing); anon has no EXECUTE.
- **N3 tombstones/bounds** (optional request): N3 asked for columns on `lab_dataset_samples`; that table is immutable
  (0029, DATA-IMMUTABLE) and a sample id is shared by derived versions, so they are two append-only sibling tables keyed
  (provider, sample id): tombstones permanent, first reason stands; bounds write-once (another = `idempotency_conflict`);
  `lab_permitted_samples` = D7's accessible − tombstoned − past `content_until` (N3's `permitted`); `lab_blocked_samples`
  = N3's `blocked` map.

## Commands (from `apps/infrx-api`, `INFRX_D_TASK=dlab`)

The dlab port was held by the coordinator's merge-batch #8 runs for ~75 min of this lane; every PG run below went
through a retry wrapper (`flock -w` on `/tmp/infrx-dlab-postgres-57500.lock`, rerun on `HarnessBusy`), attempts noted.

| Command | Head | Exit | Result |
|---|---|---|---|
| seam first: 0041–0043 moved aside, `pytest -q tests/d/test_c2rpc_content.py tests/d/test_d8_ledgers.py tests/d/test_d8_requests.py` | pre-commit | 1 | C2: **10 failed** (`42883 function infrx.lab_content_ref_issue(jsonb) does not exist` …); D8: 12 failed; requests: 13 setup errors (see D8 evidence) |
| `pytest -q tests/d/test_c2rpc_content.py` (first run) | pre-commit | 1 | 8 passed, 2 failed: a test helper passed `purposes` twice; `ResultExpired` is C2's `Gone` subclass (assertion corrected) |
| same, fixed | pre-commit | 0 | **10 passed** |
| `pytest -q tests/d/test_c2rpc_units.py` | dd08e1ec | 0 | 2 passed |
| `INFRX_MUTANTS=all pytest -q tests/d/test_code_mutants_c2rpc.py` (first) | pre-commit | 1 | 60 passed, 2 failed: `c2_service_edits_refs` survived (the check probes `delete`; mutant now grants delete), `c2_json_request_lost` setup_error (the adapter check now turns a broken answer into its assertion, R40) |
| same | dd08e1ec | 0 | **62 passed = 50 SQL + 9 Python mutants killed**, lists well-formed, no anchor in a superseded body, every case named |
| `ruff check infrx/state/lab_content.py tests/d/*c2rpc*` | dd08e1ec | 0 | clean |
| `INFRX_D_TASK=dlab make api-test` | 1079c328 | 2 | 5719 passed; the 12 failures are shared-lock valkey (7) and WR-LSQ3-3/4 wiring (5), none in C2 files - `D8-1079c32.md` |

## Oracles → checks (each named by ≥ 1 mutant)

LAB-ACCESS/DUR-RLS `check_browser_roles_reach_nothing` (5 browser sessions × 10 probes; service reads, never writes;
append-only; door ACL = authenticated) · `check_issue_binds_the_grant_recipient_user_and_expiry` (current version,
TTL 300, grant expiry 100 s, retention bound, then gone) · `check_issue_refuses_in_the_contracts_order` (8 invalid,
5 not_found, 7 forbidden incl. model/revoked member, retention, reissue) · `check_redeem_rechecks_every_rule` ·
`check_retention_is_rechecked_at_redeem` · `check_held_is_a_live_redeemable_ref` · `check_the_door_is_the_sessions_own_user`
(flag absent/off 55000; DEV via door; viewer/consumer/other member forbidden; anon 42501) ·
`check_one_handle_is_issued_once_under_contention` (race, commits) · `check_tombstones_and_bounds_gate_the_samples` ·
`check_the_adapters_compose` (typed `RefBinding`; `StateConflict`, `InvalidRequest`, `Forbidden`, `NotFound`, `ResultExpired`).

## Wiring requests

- **WR-LSQ3-1** `tests/integration/test_harness.py` migration pin: `0041_lab_content_refs.sql` (with 0042/0043) after 0040.
- **WR-LSQ3-2** `Makefile` api-mutants own-process PG line: + `tests/d/test_code_mutants_c2rpc.py`.
- **WR-LSQ3-3** `tests/d/checks.py` `EXPECTED_FUNCTION_CALLERS`: `public.lab_content_ref_issue(text,uuid,text,uuid,text)` =
  {authenticated, service_role} (diff `D8-1079c32-wiring.patch`, verified).
- **WR-C2-5 (content lane, at merge)** `tests/content/world.py`: a `pg` world over `PgContentRefs(connector(dsn))` +
  `PgAccessStore` (the lane's own requested proof, WR-C2-SQL-1); the content lane owns that world. `RefBinding` comes
  from `infrx.content` (lazy import in the adapter).
- **WR-C2-2 (unchanged, composition)** `Retention(..., holds=ContentAccess(PgContentRefs(connect), retention).holds)`.
- **WR-N3-4 (datasets lane, optional)** `lineage.blocked/permitted` may read `PgSampleRestrictions.blocked/permitted`
  and push `tombstone`/`bound` instead of the per-read object listing (its ponytail ceiling).
- Flag row `lab_content` is never inserted by a migration; enabling it is an operator act (Lab activation, P-17).

## Proposed ruling (unnumbered)

Lab content refs: the database holds only the SHA-256 of a `tc_` handle; issue and every redeem read the CURRENT grant
version, the grantor's own job's model, a current developer+ membership, all categories and the purpose on `infrx.now()`;
the answer's expiry is least(TTL ≤ 900 s, grant expiry, job age + current retention); refusal order invalid_request →
not_found → forbidden → result_expired → state_conflict; the browser door is `auth.uid()`-only, request+response
content, 300 s, behind its own flag `lab_content`. Sample tombstones are permanent (first reason stands) and content bounds
write-once, keyed by (provider, sample id).

## Open issues

- The door has no rate limit (the content service and the flag gate it); a Lab route may add one.
- `lab_held` scans the (grantor, request) index; fine at request granularity.

## Estimate (remaining for C2-RPC)

optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence medium. Basis: one review round (D10-0025 1/2/5
analogue) plus the content lane's merge-time `pg` world (WR-C2-5).

## Audit log

- 2026-09-28: written at 1079c328 (code dd08e1ec), lane lab-sql-lw3.
