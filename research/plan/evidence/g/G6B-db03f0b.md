# G6B — Headless endpoint provisioning and operations

## Task and status

| Field | Value |
|---|---|
| Task | **G6B** (track G), slices a/b/c of `research/plan/18-marlin-backend-first.md` §G6B |
| Owner / session | implementation agent (Claude Opus 5.5, 1M context), wave 3 |
| Status | **implemented against fakes; not integrated.** Every port the adapter writes through (keys, identity, A1 grant, D5 adjustment/reconciliation, audit, registry, account view) is a Protocol with an in-memory fake in `tests/g/ops/fakes.py`. Real adapters are D1R/D5/A1 (pending); ingress audience derivation is G1R. `infrx.operations.cli` deliberately refuses to run until those adapters exist |
| Oracles | API-OPS, API-AUTH, CREDIT-IDENTITY, CREDIT-RATE — **fake-level only**. No live, staged or database evidence is claimed |

## Source

| Field | Value |
|---|---|
| Base SHA | `9c87f621d7ea4e9d8a830d5a2d1b309c4a350668` (`claude/backend-impl`) |
| Implementation SHA | `db03f0bb1b200b75ef36400f3815303c4dc4cfcf` |
| Branch / worktree | `codex/g6b-headless-ops` / `.claude/worktrees/codex-g6b` |
| Integrated SHA | none (coordinator) |
| Commits | `b3b1051` G6B.a · `7ae46d3` G6B.a case coverage · `72fd254` G6B.b · `04dc441` G6B.c · `db03f0b` typed amount refusal + client body checked against G1's allow-list |

## What was built

- `infrx/operations/ports.py` — the write-side shapes the contracts do not name yet:
  `IdentityDirectory` (verified users only), `TenantStore` (key rows by hash/by org,
  one-way revoke, suspension code), `Ledger` (A1 `grant_initial`, D5 `adjust`,
  `reconcile`), `AuditLog` (`infrx.audit_entries`, lookup by idempotency key),
  `Registry` (immutable serving/deployment/rate-card rows + alias), `AccountView`
  (own usage/holds). `AUDIT_ACTIONS` and `SUSPENSION_REASONS` are D1's CHECK lists verbatim.
- `infrx/operations/service.py` — `Operations.operator(secret)` / `.tenant(secret)`
  derive the session from the key row (sha256 lookup, unrevoked, audience); no API
  accepts a caller-built `AuthContextV2`. Operator writes go through `_once`
  (idempotency key 1..255, reason 1..500, deterministic operation id
  `uuid5(action, key)` shaped as v4, one audit row; replay returns the recorded
  result; different request under the key is `IdempotencyConflict`). Wallets are
  resolved with the contract `resolve_wallet` from the identity, never named.
  `marlin_release()` builds the pinned serving/deployment/card from the committed
  S2M values in `contracts/v2/fixtures.py`.
- `infrx/operations/cli.py` — operator CLI; secret from `$INFRX_OPERATOR_KEY` or
  `getpass`; argv carrying `sk-` refused; issued secret written once to `--secret-file`
  (O_EXCL, 0600), never printed.
- `client_example.py` — **replaces** the legacy laptop client (it accepted `--api-key`
  on argv, pointed at the shared legacy SSM key and sent `stream_options`, which the
  pilot ingress refuses). Quickstart on sync JSON; per-item sweep with
  `sop1.<item_key>` (bench `item_key`), resume from an append-only state file,
  ≤8 in flight, `Retry-After`, explicit classes (§3.6). Upload form and
  `Prefer: respond-async` implemented but **specified, not served**. Reuses
  `models/marlin2b/bench.py` (`item_key`, `IDEMPOTENCY_PREFIX`, `api_key`,
  `refuse_embedded_key`, `refuse_key_in_args`, `allow`/`CODE_OK`/`ID_OK`, `data_url`,
  `messages_for`, `upload`); no second client.
- `README.md` — one section, "Headless provisioning and the dataset client (G6B)".

## Requirement coverage (test → invariant)

All in `apps/infrx-api/tests/g/ops/`.

| Test | Invariant |
|---|---|
| `test_api_ops__a_verified_individual_is_granted_keyed_and_reads_its_balance` | grant → key → tenant session with no frontend; balance = 10,000 CREDIT; two audit rows, operator principal |
| `test_api_ops__the_secret_is_stored_only_as_its_hash_and_never_audited` | row holds sha256 only; secret body absent from audit, row and `repr` |
| `test_api_ops__the_secret_is_revealed_once` | replay and crash-replay return id/prefix, no secret, no second row |
| `test_api_auth__a_forged_operator_is_refused` | consumer key / unknown key / empty → refused; operator key cannot open a tenant session |
| `test_api_auth__a_revoked_operator_key_is_refused` | revoked operator row authenticates nothing |
| `test_api_auth__a_revoked_key_reads_nothing` | revoked consumer key refused; revocation one-way |
| `test_api_auth__rotation_issues_before_it_revokes` | new key works, old refused; audit order issue→revoke |
| `test_api_auth__a_foreign_tenant_key_is_not_found_through_another_org` | revoke/rotate through another org is NotFound; rotation never mints into a non-personal org |
| `test_credit_identity__a_wallet_bound_to_another_org_is_refused` | key, adjustment and grant refused for a wallet bound elsewhere; nothing written |
| `test_credit_identity__an_unverified_user_gets_no_grant_and_no_key` | unverified = NotFound; ledger and keys untouched |
| `test_credit_identity__no_key_without_a_metered_wallet` | no key before a wallet exists (no unmetered key) |
| `test_credit_identity__a_replayed_grant_is_deduplicated` | same or new idempotency key → one 10,000 entry per user |
| `test_api_ops__a_replayed_adjustment_is_deduplicated` | same key → one entry; different amount → conflict; crash replay → no second entry |
| `test_api_ops__an_adjustment_moves_only_the_individuals_wallet` | only the resolved wallet moves; actor/reason recorded; zero and malformed amounts refused typed |
| `test_api_ops__suspension_refuses_new_keys_and_keeps_prose_in_the_audit` | closed code on org, prose only in audit; suspended org gets no key |
| `test_api_ops__every_write_needs_a_reason_and_an_idempotency_key` | bounds enforced before any write |
| `test_api_ops__operations_never_touch_a_balance` | package source names no wallet total / USD column |
| `test_api_ops__the_cli_*` (2), `test_api_auth__the_cli_reports_a_refusal_without_the_secret` | secret file 0600 once, never printed; argv key and existing file refused; errors carry no credential |
| `test_api_ops__the_pinned_marlin_release_is_published_and_quoted` | serving (`served_bytes`, no image digest) + deployment + provisional P-01 card published, quoted, replay-idempotent |
| `test_api_auth__a_private_deployment_is_never_published_and_is_not_found` | private deployment refused at publish; not_found to a consumer (R70) |
| `test_credit_rate__an_unpriced_or_mispriced_deployment_is_unserveable` | mispriced card refused; unpriced model is invalid_request (R69) |
| `test_credit_rate__a_stale_card_is_refused` | a card not strictly newer than the active one is refused |
| `test_credit_rate__an_admitted_request_keeps_its_admitted_rate` | publication changes future quotes; admitted settle at the admitted card (9.2 not 19.2 CREDIT) |
| `test_api_ops__a_tenant_reads_only_its_own_usage_holds_and_jobs` | usage/holds/jobs keyed by the authenticated org; foreign job NotFound; empty totals `{}` |
| `test_api_ops__operator_cancellation_is_tenant_scoped_and_audited` | foreign cancel NotFound and unaudited; cancel audited, idempotent |
| `test_api_ops__reconciliation_waits_for_its_interval_and_is_audited` | before `reconcile_after` refused; foreign NotFound; then released and audited |
| `test_api_ops__the_cli_publishes_the_provisional_marlin_release` | CLI publishes; naive time refused |
| `test_api_ops__the_quickstart_runs_on_the_sync_path` | body ⊆ `validate.SUPPORTED`, passes `check_messages`, mode sync; bearer from env |
| `test_api_ops__a_resumed_sweep_resends_the_same_key_and_payload_and_skips_done_items` | resume sends only the unfinished item, same key, byte-identical body |
| `test_api_ops__failures_are_explicit_and_never_retried_blindly` | 400/409 quarantined, 410 re-run, 200-without-usage failed, 429 honours Retry-After |
| `test_api_ops__an_exhausted_wallet_pauses_the_sweep` | 402 stops further sends |
| `test_api_ops__concurrency_is_bounded_per_key` | peak in-flight = `--concurrency`; >8 refused |
| `test_api_ops__an_async_item_is_persisted_then_polled_to_its_result` | 202 handle persisted before polling (specified-not-served path) |
| `test_api_auth__the_client_key_never_reaches_argv_or_state` | key on argv refused; echoed key never recorded |

### Failure proof

| Failure | Result | Named test | Killing mutant(s) |
|---|---|---|---|
| Foreign tenant | NotFound / Forbidden, nothing written | `…a_foreign_tenant_key_is_not_found_through_another_org`, `…a_wallet_bound_to_another_org_is_refused`, `…a_tenant_reads_only_its_own_usage_holds_and_jobs`, `…operator_cancellation_is_tenant_scoped_and_audited` | `rotation_ignores_the_personal_org`, `wallet_binding_unchecked`, `grant_skips_an_existing_binding`, `key_without_wallet`, `usage_/holds_/job_not_keyed_by_org` |
| Forged operator | Forbidden / InvalidApiKey | `…a_forged_operator_is_refused` | `operator_audience_unchecked`, `tenant_audience_unchecked`, `issued_key_is_operator` |
| Revoked key | InvalidApiKey | `…a_revoked_key_reads_nothing`, `…a_revoked_operator_key_is_refused`, `…rotation_issues_before_it_revokes` | `revoked_key_accepted`, `rotation_skips_the_revoke` |
| Replayed adjustment/grant | deduplicated (one ledger entry) | `…a_replayed_adjustment_is_deduplicated`, `…a_replayed_grant_is_deduplicated` | `replay_is_not_looked_up`, `replay_ignores_the_request`, `operation_id_not_deterministic` |
| Unpriced / private deployment | invalid_request (R69) / not_found (R70) | `…unpriced_or_mispriced…`, `…a_private_deployment_is_never_published_and_is_not_found` | `mispriced_card_published`, `private_deployment_published` |
| Stale rate | refused; admitted rate kept | `…a_stale_card_is_refused`, `…an_admitted_request_keeps_its_admitted_rate` | `equal_effective_card_accepted`, `card_not_written` |

Note: the task table groups "unpriced/private (not_found)"; per R69 unpriced is
`invalid_request`, private is `not_found`. The code follows the rulings.

## Environment

Local dev host, Linux 7.0.0-1010-aws; Python 3.12.3 (`apps/infrx-api/.venv`, `uv sync
--frozen`, uv 0.11.8), pydantic 2.13.5, httpx 0.28.1, pytest 8.4.2; Node v22.23.1, pnpm
9.15.9; Docker 29.6.2 (used only by the existing D harness). No database, GPU, pilot
host, cloud resource or real credential touched; every secret in tests is minted at
test time by `service.new_secret()`. Two mutants (`per_key_cap_unchecked`,
`key_in_args_unchecked`), when applied, let the client attempt DNS for reserved
`.test` names (RFC 2606), which never resolve.

## Commands and results (UTC 2026-09-22)

| Command (from repo root / `apps/infrx-api`) | Exit | Result |
|---|---|---|
| `make api-env` | 0 | venv created |
| `make check` (18:35:27 → 19:04:32, at `04dc441`) | 0 | api-test `2256 passed, 2 warnings`; api-mutants `1311 passed`; console-test `# tests 283 … # pass 283 … # skipped 0`; console-lint `0 errors, 2 warnings`; typecheck ok; console mutants `160 … 160 killed`, `40/40`, `64 killed`, `104 killed`, `23/23`; bench-test `67 passed`. No skips reported |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/ops/test_mutants.py` (at `db03f0b`) | 0 | `61 passed in 52.34s` (55 mutants + 4 runner self-tests + 2 list checks) |
| `uv run --frozen python tests/g/ops/mutants.py --list` | 0 | `55 mutants over 36 named cases` |
| `uv run --frozen pytest -q tests/g` | 0 | `316 passed, 2 warnings` |
| `uv run --frozen pytest -q tests/contracts` (incl. v2) | 0 | `1006 passed` |
| legacy-first: `pytest -q tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py tests/contracts tests/g tests/i tests/j tests/m tests/q tests/t tests/w` | 0 | `2180 passed, 2 warnings` |
| track-first: `pytest -q tests/g tests/i tests/j tests/m tests/q tests/t tests/w tests/test_*.py tests/contracts` | 0 | `2180 passed, 2 warnings` — same count |
| `uv run --frozen pytest -q tests/d` | 0 | first attempt `HarnessBusy` (lock held by `codex-d1r`, 50 cases refused, nothing altered); retried at 19:21:28: `76 passed in 48.31s` |

`make check` ran at `04dc441`; `db03f0b` changes only `service.adjust` (typed refusal)
and G6B tests, re-verified by the focused runs above. The shared G list
(`tests/g/test_mutants.py`) ran inside api-mutants and is unaffected (G6B's cases
live in `tests/g/ops/`, outside its non-recursive case glob).

## Failure drill

Crash between the port write and the audit append (`FakeAudit.fail_next`):
- key issue: row written, audit lost → retry finds the row by its deterministic id,
  reveals no second secret, writes no second row;
- adjustment: entry written, audit lost → retry reaches the ledger with the same
  operation id, appends nothing (total +25.5 −0.5 = exactly 10,025).
Mutant `operation_id_not_deterministic` shows both double-write without the fix.

## Limits

- Fake-only. The dedupe/immutability/one-way-revoke guarantees rely on the D
  constraints listed below; until D1R/D5/A1 land they hold only in the fakes.
- Audit actions: D1's CHECK has no key/publish/cancel/reconcile action. Mapping used:
  keys, publish, cancel → `admin_set_entitlements`; grant, adjustment, reconcile →
  `admin_grant`; suspension → `admin_set_suspension`; the real operation is in
  `after.operation`.
- Operator identity in the audit is the operator key id; a human principal needs the
  bootstrap row to carry one.
- Provider-dev keys are refused by this surface (Lab); only consumer keys are issued.
- `marlin_release` uses the fixture's placeholder `engine_options_digest` and no
  runtime image digest (moving tag) — W3 fills both; the rate is provisional (P-01).
- Data-access policy per deployment is assumed seeded by D; publish does not create it.
- Until D row 1 lands, `Operations._context` on a consumer key row with `user_id=None`
  raises an untyped pydantic `ValidationError` (from `AuthContextV2`) rather than a
  typed refusal; D's `user_id`-required CHECK removes the case.
- Upload form and async polling are specified, not served (G3/G4U/M3), and
  `bench.upload`'s shape does not match `wire.UploadCreated` (see E1B request).
- `item_key` does not include the segment's content sha256 (§3.1 "recommended").

## Migration / rollback

No migration, config, route or composition change. Rollback = revert the five
commits; `client_example.py` returns to the legacy client.

## Handback

Next unblocked: D1R/D5/A1 adapters for these ports; G1R to derive `AuthContextV2`
the same way as `Operations._context`; E3B.a provisions its two tenants through this CLI.

### integration_requests

**D (D1R/D5/A1) — exact rows**
1. `public.api_keys`: add `audience text not null default 'consumer' check (audience in ('consumer','provider_dev','operator'))`, `user_id uuid references public.profiles(id)` (required when audience='consumer'), `role text not null default 'service'`; RPC `key_by_hash(key_hash)` → `(id, org_id, audience, user_id, role, prefix, name, revoked_at)`; `revoke_key(org_id, key_id, at)` one-way (`revoked_at` NULL→value only); insert with `id` supplied by the caller, unique `key_hash`.
2. Operator bootstrap: one D-owned, audited insert of an `audience='operator'` key row (hash only) for the operator principal.
3. `infrx.audit_entries`: unique partial index on `idempotency_key where idempotency_key is not null`; RPC `audit_by_idempotency_key(key)`; either extend the action CHECK with `admin_key_issue, admin_key_revoke, admin_publish, admin_job_cancel, admin_reconcile, admin_adjust` or ratify the mapping above.
4. `verified_user(user_id)` → `(user_id, personal_org_id, verification_evidence_ref)` only when verified (A1's binding).
5. A1 `grant_initial(user_id, operation_id, at)` per 06a (`signup_grants` unique `(user_id, entitlement)`, creates/locks wallet bound to the personal org, returns existing on replay).
6. D5 `operator_adjustment(user_id → wallet, amount numeric(20,8) ≠ 0, operation_id unique, actor, reason)` into `wallet_ledger` kind `operator_adjustment`, refused if total would fall below reserved; `reconcile(org_id, request_id, operation_id, actor, at)` applying the 24 h rule.
7. Registry (**one transaction** for serving + deployment + card + alias: `publish` issues four port calls, so without a transaction a `Conflict` on a later row leaves a partial publication): inserts into `serving_versions`, `deployment_revisions`, `rate_cards` (immutable, identical re-insert is a no-op), alias row in `catalog_listings(requested_model → deployment_revision_id)`, and the per-deployment data-access policy row.
8. `organizations` suspension RPC over the 0003 columns (`suspended`, `suspended_at`, `suspension_reason` code).
9. Account view: `usage(org_id)` as `UsageRecordV2` rows, `holds(org_id)` as `(request_id, state, amount CREDIT)`.

**Coordinator**
10. `Makefile` `api-mutants`: append `tests/g/ops/test_mutants.py`.
11. When D adapters exist, `cli.build_operations()` becomes the composition root wiring them (coordinator-owned wiring).

**G1R** — build `AuthContextV2` from the key row's `audience`/`user_id` exactly as `Operations._context`; consider moving that derivation into `infrx/auth/` so both share it.

**E1B (`models/marlin2b/bench.py`)**
12. `_send` sends `mm_processor_kwargs` and `stream_options`; the pilot ingress refuses both (`validate.SUPPORTED`, marlin-sop §2.2) — gate them to `--target engine`.
13. `upload()` reads `created["handle"]` / `created["upload"]["url"]`; `wire.UploadCreated` is `upload_handle` / `destination_ref` — align before G4U lands (the client example's upload form inherits this).
14. Optionally add the segment sha256 to `item_key` (§3.1 recommendation).

## Verification log

- 2026-09-22: Written from the command output quoted above, including the tests/d retry after the shared harness lock freed.

## Review round 1 (coordinator review at `aa692fc`: PASS, 4 test gaps + 2 items)

| Item | Commit | Case | Mutant |
|---|---|---|---|
| 1 401/403 stop like 402 | `1f30972` | `test_api_ops__an_exhausted_wallet_pauses_the_sweep[401/402/403]` | `credential_failure_continues` |
| 2 public draining deployment refused | `ef1a698` | `…a_private_deployment_is_never_published_and_is_not_found` | `draining_deployment_published` |
| 3 mispriced on each arm | `5b668bb` | `…an_unpriced_or_mispriced_deployment_is_unserveable` | `card_serving_unchecked`, `card_model_unchecked` |
| 4 O_EXCL asserted directly | `b7d5c83` | `test_api_ops__the_secret_file_is_created_exclusively` | `cli_secret_file_not_exclusive` |
| 5 no overdraw via adjustment | `2ad5af0` | `test_credit_identity__an_adjustment_never_overdraws_the_wallet` | `fake_ledger_overdraws` (mutates the fake; declared) |
| 6 non-JSON 200/202 is a failed item | `506a374` | `…failures_are_explicit_and_never_retried_blindly` | `non_json_200_raises` (declared) |
| docstrings | `8f9aee1` | — | — |

Commands (UTC 19:33:32 → 19:35:04, at `8f9aee1`):

| Command | Exit | Tail |
|---|---|---|
| `uv run --frozen pytest -q -p no:cacheprovider tests/g` | 0 | `320 passed, 2 warnings in 27.94s` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/g/ops/test_mutants.py` | 0 | `68 passed in 62.85s (0:01:02)` |
| `uv run --frozen python tests/g/ops/mutants.py --list` | 0 | `62 mutants over 38 named cases` |

- 2026-09-22: Review round 1 appended; D row 7 transaction and the `_context` untyped-error limit recorded.
