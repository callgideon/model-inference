# F2R-B — console contract and configuration carryovers (track F, lane B of F2R)

## Task and status

| Field | Value |
|---|---|
| Task | **F2R-B** — carryover item 6 (console DTO nullability/bounds, including the last open G0 leftover), item 7 (08 §5 configuration, assigned mid-task once the coordinator confirmed lane A had not started it), and ruling **R62** (a model revision carries its public model id, also assigned mid-task) |
| Owner / session | Claude Opus 5 (1M context), session `session_015Tix7PsULvyhV5Lw6chMwh` |
| Status | **implemented** — every owned suite passes and every declared mutant of every list I touch is killed by a named case. Not integrated: nothing ran against a real PostgreSQL, ClickHouse or Supabase project, nothing was deployed or pushed, no cloud or paid provider was contacted. **Two canonical console targets are red on merged consumers I do not own** (`console-test` 14 failures, all in `tests/c`; `console-typecheck` 34 errors in `lib/services/console.ts`, `app/(console)/usage/*`, `tests/c`, `tests/u`) — the exact changes are in *Integration requests* below, per the lane brief's "list the exact change as an integration request instead of editing it". |

## Source

| Field | Value |
|---|---|
| Base SHA | `ec6c5483f472ee84e10d48ca3c51ba474b4efed2` |
| Implementation SHA | `e06af7f` |
| Integrated SHA | — (coordinator merges) |
| Branch / worktree | `codex/f2r-b-console-contracts` in `.claude/worktrees/codex-f2r-b` |
| Commits | 5: `1b7687b` item 6, `213204a` item 7, `904a5f0` R62, `cfa4adf` the last G0 leftover, `e06af7f` an item-7 docstring that overstated its guard |

## Environment

| Field | Value |
|---|---|
| Classification | **local** developer host; no staging or production resource touched |
| OS / kernel | Linux 7.0.0-1010-aws (x86_64) |
| Python | 3.12.3 in `apps/infrx-api/.venv`, built by `make api-env` (`uv sync --frozen --all-extras`), uv 0.11.8 |
| Node / pnpm / tsc | Node v22.23.1, pnpm 9.15.9, TypeScript 5.9.3 (`pnpm install --frozen-lockfile`) |
| Docker | 29.6.2 available, **deliberately unused** (see *Commands*) |
| Services | none. No PostgreSQL, ClickHouse, Valkey, MinIO, Supabase project, GPU or model was contacted |
| Seeds | none needed: the console fake is deterministic (`mulberry32` from the fixture namespaces, frozen clock `orgs.json:clock` = `2026-09-20T12:00:00.000Z`) |

## Requirement coverage

Test IDs are console `it(...)`/`test(...)` titles, pytest node ids, or mutant ids from
`apps/app/tests/contracts/mutants.json`. Every invariant below is killed by at least one
single-edit mutant that fails a **named declared** case (R32/R40).

### Item 6 — the legacy/nullable console surface

| Test ID | Invariant demonstrated | Killing mutant |
|---|---|---|
| `usage pages walk every row exactly once, newest first` (via `assertUsageRow`) | `UsageRow` carries **exactly** the 17 declared fields (Q16 pin, so `accounting_regime` cannot stop being projected); `accounting_regime` is in `ACCOUNTING_REGIMES`; `key_id`/`key_name` are null **together** and a present `key_id` is non-empty; a `legacy_usd` row's `execution_mode`, `job_state`, `usage_certainty`, `trace_mode`, `settlement_state` and `max_hold` are **all** null (one `deepEqual`, so filling any one in fails); R13's settlement rules are applied to `pilot` rows only | `REGIME-01` (every row claims `pilot`), `REGIME-02` (a legacy row is given a job state and an execution mode), `KEYNULL-01` (only the id nulls), `KEYNULL-02` (the `""` sentinel instead of null) |
| `legacy and key-less usage rows keep their nulls and are still counted` | Legacy rows exist, at least one carries a **non-zero** charge (so "still counted" is not a set of zeroes), the summary over the whole window counts and totals them, a window holding only legacy rows returns exactly them, and a key-less row is in the unfiltered walk while **no** key filter returns it and an empty `key_id` filter is `invalid_request` | `REGIME-03` (the list drops non-pilot rows), `REGIME-04` (the summary totals pilot rows only) |
| `an aggregate without a window is invalid_request, not an all-time total` | `usageSummary` and `usageDaily` refuse no window, only `from`, and only `to`; the same call with both bounds succeeds, so the refusal is about the window and not about the operation | `WINDOW-01` (the guard never fires), `WINDOW-02` (only `from` required) |
| `an unrecorded capture mode, an orphaned audit target and an unreached provider are nulls, not defaults` | `ApiKeySummary.trace_mode` null reads as `off` through `traceModeOf`; an audit entry with `target_org_id: null` is returned unfiltered and **never** matched by a `target_org_id` filter for any of the three organizations; a run with `judge_model_version: null`, one with `consent_snapshot_at: null` and a sample with `request_id: null` all exist and are not filled in | `TRACEMODE-01` (null defaults to `full`), `AUDITTARGET-01` (the orphan is attributed to a surviving org), `AUDITTARGET-02` (the filter also matches null targets), `JUDGESAMPLE-02` (an invented request id), `JUDGECONSENT-01` (the creation instant used as the snapshot) |
| `judge runs separate estimates, limited evaluations and held budgets` (via `assertJudgeRun`) | `JudgeSample` carries **exactly** `{sample_id, rubric_version, request_id, scores}`; `sample_id` is a lower-case UUIDv4 and unique within the run; a score's `rubric_version` is the **sample's**; `sample_count >= samples.length`, `samples.length <= JUDGE_RUN_SAMPLE_CAP`, and the count is **exact** while the array is not capped; a `dry_run` has no `judge_model_version` | `JUDGESAMPLE-01` (a field no view can produce), `JUDGESAMPLE-03` (an under-count), `JUDGEVERSION-01` (a dry run given a served version), `XJUDGE-02` re-expressed (an over-count with nothing capped) |
| `a filter or cursor from another organization never widens the tenant` | A foreign key filter returns neither another tenant's rows **nor** a row that names no key | `XTENANT-01` (unchanged, still killed) |
| `reads and writes never cross the tenant boundary, in either direction` | The per-organization summary is computed from the rows, legacy ones included, at the required window | `REGIME-04` (collateral), unchanged tenant mutants |
| `the organization fixtures match the contract vocabulary` | A key's `trace_mode` may be null **only if the key is revoked**: an active key with no recorded capture mode would generate traffic whose capture nobody chose | fixture-data assertion (see *Limits*) |
| `the judge fixtures keep dry-run, live and ambiguous runs honest` | A dry run's `judge_model_version` is null; a run's `consent_snapshot_at` is an instant or null; a sample's `trace_index` is an index or null | fixture-data assertion (see *Limits*) |
| `tests/contracts/test_parity_console.py::test_the_legacy_nullable_console_fields_are_frozen[UsageRow\|ApiKeySummary\|AuditEntry\|JudgeRun\|JudgeSample]` | The nullability D1's real history needs, pinned field by field, **including** `accounting_regime` as non-nullable (a null there would make the regime distinction unreadable) | proven by flipping `accounting_regime` to `\| null` in `types.ts`: `FAILED …[UsageRow]` (reverted, `git diff` clean) |
| `…::test_the_console_only_vocabularies_are_recorded[ACCOUNTING_REGIMES]` | The console-only vocabulary and its values are pinned, so adding the Python half is a decision | proven by renaming the values to `["legacy", "pilot"]`: `FAILED …[ACCOUNTING_REGIMES]` (reverted) |
| `…::test_the_judge_sample_carries_exactly_d1s_field_set` | The frozen sample field set, from the Python side of the fence | same parser as above |
| `…::test_the_unpaired_console_types_still_exist[UsageRow\|ApiKeySummary\|AuditEntry\|JudgeSample]` | "Not compared with a Python record" is a recorded decision, not an oversight |  |
| `node tests/contracts/run-mutants.mjs --self-test` | The runner still cannot report a false kill (syntax error, load failure, timeout, unnamed failure and undeclared exception are runner errors); the twelfth self-test now **names the mutant it replicates** (`SELF-IDEM-05-REPLICA`, not `SELF-V03-REPLICA`: V03 was the separate fake-only finding), which is the last of the four G0 leftovers the wave-2 handoff filed under item 6 | 12/12 self-tests |

### Item 7 — the 08 §5 deployment table

| Test ID | Invariant demonstrated |
|---|---|
| `test_every_deployment_name_and_default_is_frozen` | All 15 names and defaults, name by name: a rename or a changed default fails here first |
| `test_the_deployment_names_are_nobodys_existing_names` | No deployment name collides with a pilot (08 §5) or F1 variable, so one setting cannot silently retune another |
| `test_each_deployment_name_is_read_from_the_environment[<15>]` | Each name is actually read, and coerced by its default's type |
| `test_a_missing_deployment_name_takes_its_default[<15>]` | Absent is unset is the frozen default — for that name only, with every other name set, so the read cannot disturb a neighbour |
| `test_an_empty_deployment_value_is_refused[<15>]` | `NAME=`, `NAME=" "` and `NAME="\t"` are refused rather than read as unset |
| `test_a_deployment_value_of_the_wrong_type_is_refused[<14 numbers>]` | `abc` and `-1` are refused, naming the variable and never its value |
| `test_a_deployment_bound_a_zero_would_disable_is_refused[<14 numbers>]` | Zero is refused for every number (a zero pool admits no connection, a zero `statement_timeout` means *no* limit in PostgreSQL, a zero message cap refuses every request, a zero segment never rotates) |
| `test_the_pool_bounds_must_be_ordered` | `DATABASE_POOL_MIN_SIZE > MAX_SIZE` is refused; equal is accepted |
| `test_a_short_cursor_secret_is_refused_without_echoing_it` | A secret shorter than 16 characters is refused and the value does not appear in the message |
| `test_the_cursor_secret_is_the_consoles_requirement_and_not_this_gateways` | Unset, it is not a gateway startup failure in any mode including `pilot`, and `CONSOLE_ONLY_SETTINGS` records why |
| `test_a_bad_deployment_value_refuses_before_anything_mounts[""\|dev\|test\|pilot]` | `create_app` raises `RuntimeMisconfigured` before it builds the app, in **every** mode, and the same configuration with the value corrected starts |
| `test_the_g1_and_q1_constants_match_the_deployment_defaults` | `validate.MAX_MESSAGES/MAX_PARTS_PER_MESSAGE/MAX_TEXT_CODEPOINTS/MAX_URL_CHARS`, `intake.MAX_NUMBER_DIGITS`, `intake.LargeBodies().limit/threshold`, `memory.MAX_INDEX_ITEMS/MAX_INDEX_BYTES` equal the new defaults, so the constants and the settings cannot drift while the wiring is an integration request |
| `test_an_unset_mode_is_the_legacy_f1_behaviour`, `test_pilot_starts_with_authentication_and_metering`, `tests/g` (268) | The new validation changes no existing startup outcome |

### R62 — the model revision carries its public model id

| Test ID | Invariant demonstrated |
|---|---|
| `V1-Q10 every query this page builds is one the service accepts, and its walk is stable` | The page's model filter matches the fixture's rows under the prefixed revision (this case was the only test in the tree that asserted the unprefixed spelling, and it failed until it was aligned) |
| `the organization fixtures match the contract vocabulary` / `tests/contracts` 57 cases | The fixture model list, the entitlement lists and `TraceVersions.model_revision` all carry `nemostation/marlin-2b@2026-09-01`; `node tests/contracts/run-mutants.mjs` 160/160 and `tests/v/run-mutants.mjs` 39/39 still hold |

## Commands

Exact commands, from the worktree root unless stated. UTC times are the wall-clock of the
run; exit status as reported by the shell. No seed applies (see *Environment*).

| # | Command | Exit | When (UTC) |
|---|---|---|---|
| 1 | `make api-env` | 0 | 2026-09-22 ~15:20 |
| 2 | `cd apps/app && pnpm install --frozen-lockfile` | 0 | 2026-09-22 ~15:22 |
| 3 | `make console-test` (baseline at `ec6c548`) | 0 | 2026-09-22 ~15:25 |
| 4 | `make console-typecheck` (baseline) | 0 | 2026-09-22 ~15:26 |
| 5 | `make console-lint` (baseline) | 0 | 2026-09-22 ~15:26 |
| 6 | `cd apps/infrx-api && uv run --frozen pytest -q --ignore=tests/d` (baseline) | 0 | 2026-09-22 ~15:30 |
| 7 | `cd apps/app && node tests/contracts/run-mutants.mjs --only REGIME-01,…` (the 16 new) | 0 | 2026-09-22 ~16:05 |
| 8 | `cd apps/app && node tests/contracts/run-mutants.mjs --self-test` | 0 | 2026-09-22 ~16:10 |
| 9 | `cd apps/app && node tests/contracts/run-mutants.mjs` | 0 | 2026-09-22 ~16:12 |
| 10 | `cd apps/infrx-api && uv run --frozen pytest -q tests/contracts` | 0 | 2026-09-22 ~16:15 |
| 11 | `cd apps/infrx-api && uv run --frozen pytest -q tests/g` | 0 | 2026-09-22 ~16:30 |
| 12 | `cd apps/infrx-api && uv run --frozen pytest -q --ignore=tests/d` (final) | 0 | 2026-09-22 ~16:40 |
| 13 | `make console-test` (final) | **2** | 2026-09-22 ~16:43 |
| 14 | `make console-typecheck` (final) | **2** | 2026-09-22 ~16:45 |
| 15 | `make console-lint` (final) | 0 | 2026-09-22 ~16:44 |
| 16 | `cd apps/app && node tests/v/run-mutants.mjs` | 0 | 2026-09-22 ~16:42 |
| 17 | `cd apps/app && node tests/u/run-mutants.mjs` | 0 | 2026-09-22 ~16:47 |
| 18 | `cd apps/app && node tests/c/run-mutants.mjs --self-test` | 0 | 2026-09-22 ~16:48 |
| 19 | `cd apps/app && node tests/c/run-mutants.mjs` | **1** | 2026-09-22 ~16:49 |
| 20 | `make bench-test` | 0 | 2026-09-22 ~16:52 |
| 21 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py tests/m/test_mutants.py tests/q/test_mutants.py tests/j/test_mutants.py tests/w/test_mutants.py tests/t/test_trace_mutants.py tests/g/test_mutants.py` (the `api-mutants` list **without** `tests/d/test_migration_mutants.py`) | 0 | 2026-09-22 16:44–17:04 |

Environment variable names used: `INFRX_MUTANTS`. No secret, token or credential was read
or written by any command.

**Not run, and why.**

- `make api-mutants` as written, and `tests/d`: **not run (harness collision, E2R)** — the
  coordinator's interim rule for concurrent lanes. `tests/d`'s harness hard-codes one
  container name and port, and this branch is based on `ec6c548`, which predates E2R's
  per-process labelled container (`c23d804` on `claude/backend-impl`); rebasing is forbidden
  by the common brief's hard rules, so the exclusion stands for this branch. The coordinator
  runs the full list on the merged tree.
- `make check`: **not run as one target**, because it invokes `api-mutants` (above). Its other
  six targets were run individually and are reported here, `make bench-test` included
  (`40 passed in 4.54s`; `models/marlin2b/tests` does exist on this tree, so the target does
  not take its "not run" branch).
- `make integration INTEGRATION_ARGS="--layer 1 --canary"`: **not run**. This lane touched no
  file under `tests/integration`, the harness creates containers that are not named
  `infrx-f2r-b-*` (the only names this lane is allowed to create), and the coordinator has an
  open collision warning about Docker harnesses across lanes. Docker is available, so this is
  a deliberate abstention, not an unavailable service.

## Results

| Run | Result | Expected |
|---|---|---|
| `make console-test` baseline / final | 255 passed, 0 failed / **247 passed, 14 failed** | the 14 are all in `tests/c` and are the integration request below; `diff` of the failing-case lists before and after the R62 commit is empty, so R62 added none |
| `make console-typecheck` baseline / final | exit 0 / **exit 2, 34 errors**: 21 `tests/c/read-services.test.ts`, 5 `tests/u/usage-view-model.test.ts`, 5 `app/(console)/usage/*`, 3 `lib/services/console.ts`. **Zero in any owned path.** | the integration request below |
| `make console-lint` baseline / final | `✖ 2 problems (0 errors, 2 warnings)`, exit 0 / identical | unchanged, both warnings pre-existing (`conformance.ts` unused `ids`, `fake-services.ts` unused `AuditQuery` import) |
| `node tests/contracts/run-mutants.mjs` | `160 mutants: 160 killed by a named declared case, 0 survived, 0 stale, 0 runner errors` (38.2s at `904a5f0`, 49.9s re-run at final HEAD); baseline unmutated `48 exported cases pass` | 144 before, +16 new; `XJUDGE-02` re-expressed |
| `node tests/contracts/run-mutants.mjs --self-test` | `12 self-tests, 12 passed, 0 failed` | unchanged |
| `node tests/v/run-mutants.mjs` | `39/39 mutants killed by a declared case` | unchanged |
| `node tests/u/run-mutants.mjs` | `2 self-checks, 2 as expected; 61 mutants, 61 killed, 0 not killed` | unchanged (Node strips types rather than checking them, so `tests/u` still *runs* green; only `tsc` fails) |
| `node tests/c/run-mutants.mjs --self-test` | `4 self-tests, 0 failed` | unchanged |
| `node tests/c/run-mutants.mjs` | `88 mutants: 56 killed by a named declared case, 0 survived, 0 stale, 32 runner errors`, exit 1 | **0 survived on a claimed invariant.** All 32 runner errors are `these declared cases do not pass unmutated` — the same 14 red `tests/c` cases. They become kills again with the integration request applied |
| `pytest -q --ignore=tests/d` baseline / final | 1518 passed / **1612 passed**, 2 warnings (both pre-existing anyio fixture warnings) | +94: 83 new deployment cases, 11 new/parametrised parity cases |
| `pytest -q tests/contracts` | 654 passed | includes `test_parity_console.py` 29 passed and `test_config_and_imports.py` 214 passed |
| `pytest -q tests/g` | 268 passed | G1 untouched by the new validation |
| `make bench-test` | 40 passed in 4.54s | unrelated to this lane, run for the record |
| the `api-mutants` list minus `tests/d` (7 of the 8 lists, `INFRX_MUTANTS=all`) | `955 passed in 1170.61s (0:19:30)`, exit 0 — every declared mutant of `tests/contracts`, `tests/m`, `tests/q`, `tests/j`, `tests/w`, `tests/t` and `tests/g` killed | unchanged by this lane; `tests/d/test_migration_mutants.py` **not run** (harness collision, E2R) |

`tests/d` was **not run**; no count for it is claimed here.

## Failure drill

There is no durable state in this lane: the console fake is in-memory and
`config.DeploymentSettings` is a frozen dataclass. The drills that apply are refusal
drills, and each one is a named case above.

| Injection | Before | After | Retry / duplicate | Cleanup |
|---|---|---|---|---|
| `accounting_regime` flipped to nullable in `types.ts` (to prove the new parity test bites) | `pytest tests/contracts/test_parity_console.py` 29 passed | `2 failed, 27 passed` — `test_the_legacy_nullable_console_fields_are_frozen[UsageRow]` and `test_the_console_only_vocabularies_are_recorded[ACCOUNTING_REGIMES]` | n/a | file restored from a scratchpad copy; `grep` re-confirmed both literals and the suite back to 29 passed. Nothing of it is committed |
| `CONSOLE_CURSOR_SECRET` required at gateway startup in `pilot` (the first reading of item 7) | `pytest tests/g` 268 passed | **224 failed** — every pilot-mode case in G1's suite, because `tests/g/support.py:settings()` builds pilot settings with no console secret | n/a | reverted to "validate the value, never require it"; `tests/g` back to 268 passed. Recorded as a **finding**: the requirement belongs to the console runtime (C2) and the deployment checklist, and coupling inference startup to a console signing key is the wrong dependency |
| `legacy_usage_rows: 3 -> 0` in `orgs.json` while `hasLegacyRows` stays `true` (to prove the flag cannot buy a green run) | `tests/contracts/services.test.ts` 57 passed | `56 pass, 1 fail, 0 skipped` — `not ok 35 - legacy and key-less usage rows keep their nulls and are still counted` ("the harness declared legacy rows and has none") | n/a | `git checkout --` the fixture; `git status` clean |
| The same two cases against a harness that declares **nothing** (C1's, `tests/c/conformance/console-services.conformance.ts`) | — | `ok 35 … # SKIP the harness declares no legacy history (ConsoleHarness.hasLegacyRows)` and `ok 36 … # SKIP …`, `# skipped 2` — visible in the TAP output, and not a pass | n/a | nothing changed |
| 16 single-edit mutants on the new item-6 invariants | 48 exported cases pass unmutated | each fails a **named declared** case (table above) | n/a | the runner works on a temporary copy of `apps/app`; `git status` clean afterwards |
| `MAX_MESSAGES=0`, `=abc`, `=-1`, `=` (and 13 other names) | app starts | `RuntimeMisconfigured`/`ValueError` naming the variable, raised by `create_app` **before** `FastAPI()` is constructed, so no app object exists to serve | the same configuration with the value corrected starts | none needed |

## Artifacts

All under version control at `904a5f0`; no external storage, no credentials, no customer
content. Raw run logs live in this session's scratchpad only and are **not** durable
artifacts — every number above is quoted from the command output at the time it ran.

| Path | sha256 (first 16) |
|---|---|
| `apps/app/lib/contracts/types.ts` | `git cat-file` at `904a5f0` |
| `apps/app/lib/contracts/conformance.ts` | idem |
| `apps/app/lib/contracts/fake-services.ts` | idem |
| `apps/app/tests/contracts/mutants.json` | idem |
| `apps/infrx-api/infrx/config.py` | idem |
| `apps/infrx-api/tests/contracts/test_config_and_imports.py` | idem |
| `research/plan/08-contracts-v1-encoding.md` | idem |

(Digests are not hand-typed here: `git rev-parse 904a5f0:<path>` is the immutable identifier
for each file, and the commit itself is the digest of the set.)

## Changes

### Owned paths changed

| Path | Item |
|---|---|
| `apps/app/lib/contracts/types.ts` | 6 |
| `apps/app/lib/contracts/services.ts` | 6 |
| `apps/app/lib/contracts/fake-services.ts` | 6 |
| `apps/app/lib/contracts/conformance.ts` | 6 |
| `apps/app/lib/contracts/README.md` | 6, R62 |
| `apps/app/lib/contracts/fixtures/orgs.json` | 6, R62 |
| `apps/app/lib/contracts/fixtures/judge.json` | 6 |
| `apps/app/lib/contracts/fixtures/traces.json` | R62 |
| `apps/app/tests/contracts/mutants.json` | 6 |
| `apps/app/tests/contracts/services.test.ts` | 6 |
| `apps/app/tests/contracts/opaque-cursor.test.ts` | 6 |
| `apps/app/tests/contracts/fixtures.test.ts` | 6 |
| `apps/app/tests/contracts/run-mutants.mjs` | 6 |
| `apps/infrx-api/tests/contracts/test_parity_console.py` | 6 (TS-side rows only, per the lane brief) |
| `apps/infrx-api/infrx/config.py` | 7 |
| `apps/infrx-api/tests/contracts/test_config_and_imports.py` | 7 |
| `research/plan/08-contracts-v1-encoding.md` | 7 (new §5.1) + verification-log entries for 6 and 7 |
| `apps/app/tests/v/trace-query.test.ts` | R62 (the one test that asserted the old spelling; the coordinator's instruction covers it) |

Not touched: `infrx/contracts/**` (lane A), any SQL migration, any composition root,
`gateway/app.py`, `tasks.json`, coordinator records, `CLAUDE.md`, lockfiles, package
manifests, `apps/app` pages, `lib/services/**`, `tests/c`, `tests/u`.

### Contract change

Item 6 **is** a contract revision (r2) of the console half. It is encoded in
`apps/app/lib/contracts/` and documented in that directory's README (new section
"Contract revision r2 (F2R): legacy history is read, not rejected") and in 08's
verification log. `usageSummary`/`usageDaily` requiring `from`/`to` was already **accepted**
by the coordinator in 08 §10's log (the R59 entry); the rest closes wave-2 audit finding A07.

### Migration / deploy / rollback

No SQL, no migration, no deploy. D1's migrations `0003`–`0005` already produce every
nullable column this revision admits (`usage_events.settlement_regime` default `'legacy'`,
`execution_mode`/`job_state`/`usage_certainty`/`trace_mode`/`settlement_state` nullable,
`api_keys.trace_mode` nullable, `audit_entries.target_org_id on delete set null`,
`judge_runs.judge_model_version` nullable, `console_judge_runs.consent_snapshot_at` from an
outer join, the 50-sample cap), so **no schema change is needed for this contract** — that is
the point of the revision. Rollback is `git revert` of the three commits; the console has no
persisted state that depends on them.

## Limits

1. **`make console-test` and `make console-typecheck` are red on merged consumers** (14 cases,
   34 type errors, every one listed in *Integration requests*). Owner: C0/C1 for
   `lib/services/console.ts`, `lib/services/query.ts`, `tests/c`; U1 for
   `app/(console)/usage/*` and `tests/u`; V1 for `app/(console)/traces/view-model.ts`. I did
   **not** apply the patch and therefore do **not** claim that the tree is green with it
   applied — that is an inference, and it is not made here.
2. **Fake-only.** Every item-6 invariant is proven against the fixture-backed fake. No
   console service ran against PostgreSQL or ClickHouse, so item 6 is *implemented*, never
   integrated. The gated cases will only prove the real projection once C0's harness sets
   `ConsoleHarness.hasLegacyRows`; until then they **skip on that harness, naming the flag**,
   and a skip is visible in the TAP output and is not a pass.
3. **Two fixture assertions have no mutant** (`the organization fixtures match the contract
   vocabulary`, `the judge fixtures keep dry-run, live and ambiguous runs honest`): they
   constrain fixture *data*, and `tests/contracts/mutants.json` mutates source. The
   invariants they carry (an active key always records its mode; a dry run has no served
   version) are **also** asserted by the exported cases, which do have mutants
   (`TRACEMODE-01`, `JUDGEVERSION-01`).
4. **One unclaimed consistency fix with no mutant:** `badFilter`'s 200-character identifier
   bound on `key_id`/`model` now counts code points (R54) instead of UTF-16 units. No case
   claims it and no mutant is declared, because `codePoints → .length` only *tightens* the
   bound for astral input that no case sends, so a mutant would survive. The four **shared**
   bounds were already code points (R54, closed by F2.1) and are proven by
   `text_bounds.json` on both halves. Recorded so a reviewer's mutant surviving there is a
   known unclaimed edit, not a hole in a claimed invariant.
5. **The item-7 defaults are provisional and partly `⚠️ TO BE VERIFIED`**: the four
   `DATABASE_POOL_*` numbers (owner D2, against a real pool) and
   `TRACE_SPOOL_SEGMENT_BYTES` (owner T2, against a real spool). The G1/Q1 numbers are the
   values already in the code, pinned by a test rather than guessed.
6. **The item-7 names are in `config.DeploymentSettings`, not `contracts.limits.PilotSettings`.**
   `contracts/**` is lane A's path, and these are deployment knobs rather than numbers both
   language halves enforce. If the coordinator prefers one object, the names, defaults and
   checks move unchanged — listed as an integration request.
7. **No Python mutant is declared for item 7.** `apps/infrx-api/tests/contracts/mutants.py` is
   lane A's file (its item 9 consolidates the runners), so the five mutant entries item 7
   needs are listed as an integration request instead of committed. Each of them is a
   single-edit change to `infrx/config.py` whose killing case is named in the list.
8. **`tests/d` and `make integration` were not run** (reasons under *Commands*). No result
   for either is claimed.
9. **R62 is partial by instruction.** The console fixtures' second model,
   `deepseek-v41-flash@2026-08-15`, is console-only and has no established public id, so it
   keeps its spelling. A sweep for the unprefixed form leaves four places untouched, each
   deliberately: `app/(console)/traces/query.ts:61` (a **comment** giving the shape of a served
   id — V1's file, one word, listed for V1), `tests/c/projection.test.ts:402-403` and
   `tests/u/usage-view-model.test.ts` (the string used as arbitrary filter *input*, asserting
   nothing about the revision), and the historical evidence reports
   `research/plan/evidence/f/F2-ts-b48ffaa.md` and `u/U1-9bef497.md`, which are an audit trail
   and are never rewritten.

## Integration requests

### IR-1 (blocking `console-typecheck` / `console-test`) — `apps/app/lib/services/console.ts`, owner C0/C1

1. Delete `DELETED_KEY_ID` / `DELETED_KEY_NAME` and their doc block. `deletedKeyOr` becomes
   `{ key_id: string | null; key_name: string | null }`: both null → `{key_id: null, key_name: null}`;
   exactly one null keeps the existing `TypeError` (a shape the view cannot produce).
2. `usageRowOf`: add
   `accounting_regime: text(row, "settlement_regime") === "legacy" ? "legacy_usd" : text(row, "settlement_regime") === "pilot" ? "pilot" : (() => { throw new TypeError("unknown settlement regime"); })()`
   (fail closed on an unknown value — a regime nobody recognises must not read as `pilot`),
   and change `execution_mode`, `job_state`, `usage_certainty`, `trace_mode` from `text(...)`
   to `optionalText(...)`.
3. `apps/app/lib/services/query.ts`: `USAGE_COLUMNS` must select `u.settlement_regime`
   (D1's `console_usage` view already emits it; C1's column list does not ask for it).
4. `keyOf`: `trace_mode: optionalText(row, "trace_mode") as ApiKeySummary["trace_mode"]`
   (today `text(...)` **throws** on the nullable column, which is the A07 defect).
5. `judgeSampleOf`: return `{sample_id: text(row, "sample_id"), rubric_version: integer(row, "rubric_version"), request_id: optionalText(row, "request_id"), scores: scores.map(judgeScoreOf)}`;
   delete the `limited_reason`/`limited_evaluation`/`run_id`/`id` reads and the now-unused
   `JUDGE_LIMITED_REASONS` import. **Note a real defect this exposes:** the current code reads
   `id` and `run_id`, which `console_judge_runs` never emits — it emits `sample_id`.
6. `judgeRunOf`: `judge_model_version: optionalText(row, "judge_model_version")`,
   `consent_snapshot_at: optionalTimestamp(row, "consent_snapshot_at")`.
7. `auditEntryOf`: `target_org_id: optionalText(row, "target_org_id")`. Also worth a decision:
   C's port refuses a row whose tenant column is null, and `operator_audit` projects
   `target_org_id as org_id`, so an entry whose target organization was deleted is currently
   **unreadable**. That is a query-boundary rule, not a DTO rule — filed for C0.
8. `usageSummary` and `usageDaily`: refuse a missing `from` or `to` with `invalid_request`
   before the tenant lookup (mirror of `missingWindow` in the fake).

### IR-2 — `apps/app/app/(console)/usage/`, owner U1

- `view-model.ts:438-440` in `usageRowView`: `keyName: row.key_name ?? "(deleted key)"`,
  `mode: row.execution_mode ?? "—"`, `outcome: row.terminal_cause ?? row.job_state ?? "—"`.
  The sentinel becomes a *presentation* choice, which is where it belongs.
- `page.tsx:41-42`: pass the page's resolved window to `usageSummary`/`usageDaily`
  (the page already computes one for its range filter).
- `tests/u/usage-view-model.test.ts`: 5 call sites pass a `UsageQuery` where a
  `UsageWindowQuery` is now required.

### IR-3 — `apps/app/app/(console)/traces/view-model.ts:342`, owner V1

`context.keys.some((key) => key.trace_mode !== "off" && …)` must become
`traceModeOf(key) !== "off"` (imported from `lib/contracts/types.ts`). It compiles either
way, and that is the danger: a key with a **null** capture mode currently makes the page say
the organization is capturing.

### IR-4 — `apps/app/tests/c/`, owner C1/C0

- `read-services.test.ts`: 21 aggregate call sites need a `from`/`to` window.
- `projection.test.ts` `a usage row survives a deleted key, and says so`: expect
  `key_id === null` / `key_name === null` instead of the two sentinels.
- `mutants.json`: the two `deletedKeyOr` mutants (`find` text at the sentinel lines) go stale
  and must be re-expressed against the nullable projection; the `usage_certainty`/judge-sample
  mutants should be re-read once IR-1 lands.
- `harness.ts`: to make the gated conformance cases *run* against C1 rather than skip, the
  harness needs one `legacy_usd` row with a charge, one row with both key columns null, one
  key with a null `trace_mode`, one audit entry with a null target, one judge run with a null
  `judge_model_version` and `consent_snapshot_at`, one sample with a null `request_id` — and
  then `hasLegacyRows: true`.

### IR-5 — item 7 wiring, owners G1/G2 and Q1/Q2

- `infrx/gateway/routes/validate.py`: `MAX_MESSAGES`, `MAX_PARTS_PER_MESSAGE`,
  `MAX_TEXT_CODEPOINTS`, `MAX_URL_CHARS` become reads of `settings.deployment`;
  `intake.py`: `MAX_NUMBER_DIGITS` and `LargeBodies(limit, threshold)`.
- `infrx/scheduling/memory.py`: `MAX_INDEX_ITEMS`/`MAX_INDEX_BYTES` likewise.
- `infrx/traces/spool.py`: the segment size reads `TRACE_SPOOL_SEGMENT_BYTES` (lane A keeps
  the local default; T2 wires it).
- The G2/I2 cutover checklist and `apps/app`'s deployment notes must add
  `CONSOLE_CURSOR_SECRET` (≥ 16 characters) as a **console** requirement.
- Until each lands, `test_the_g1_and_q1_constants_match_the_deployment_defaults` keeps the
  constants and the defaults from drifting.

### IR-6 — Python mutants for item 7, owner lane A (`tests/contracts/mutants.py`)

Five single-edit mutants, each with the case it must fail:

| Mutant | Edit in `infrx/config.py` | Killed by |
|---|---|---|
| `DEPLOY-01` | `if raw.strip() == "":` → `if False:` | `test_an_empty_deployment_value_is_refused` |
| `DEPLOY-02` | `if getattr(deployment, name) <= 0:` → `< 0` | `test_a_deployment_bound_a_zero_would_disable_is_refused` |
| `DEPLOY-03` | delete the `validate_deployment(...)` call in `validate_runtime` | `test_a_bad_deployment_value_refuses_before_anything_mounts` |
| `DEPLOY-04` | `min_size > max_size` → `min_size >= max_size` | `test_the_pool_bounds_must_be_ordered` (the equal case) |
| `DEPLOY-05` | `len(secret) < MIN_CONSOLE_CURSOR_SECRET_CHARS` → `< 1` | `test_a_short_cursor_secret_is_refused_without_echoing_it` |

### IR-7 — Python counterparts for F2P / lane A

- `records.AccountingRegime` (`legacy_usd`/`pilot`) and its mapping to D1's
  `settlement_regime`, then move `ACCOUNTING_REGIMES` from `UNPAIRED_CONSOLE_ENUMS` into
  `SHARED_ENUMS` in `test_parity_console.py`.
- Lane A's item 5 should spell the judge sample DTO `{sample_id, rubric_version, request_id,
  scores}` on the Python half; this lane chose D1's view shape as the brief directed and
  recorded it here, in the console README and in 08's log.
- `AuditAction` values differ between the halves: the console freezes
  `grant | suspension_set | entitlements_set | calibration_label`, D1's
  `audit_entries.action` check constraint says
  `admin_grant | admin_set_suspension | admin_set_entitlements | calibration_label`. One of
  the two is wrong; neither is in this lane's scope. Also: `AuditEntry.after` and
  `idempotency_key` are non-null in the console DTO while `audit_entries.after` and
  `.idempotency_key` are nullable columns.
- 08 §5's `FETCH_CONNECT_TIMEOUT_S` / `FETCH_TIMEOUT_S` / `FETCH_MAX_REDIRECTS` rows name
  variables the code spells `MEDIA_FETCH_*` (r1 R2 renamed them; the table was not updated).
  Reported, not changed: §5's pilot table belongs to the contract lane.

## Handback

**What the v1 console base now guarantees** (for F2P, which starts from the accepted commit):
a usage row states the regime it was written under and keeps every NULL that regime implies;
a deleted key is two nulls and nothing else; an aggregate has a window; a capture mode that
was never recorded is `off`; an audit entry outlives its target; a judge run may have reached
no provider, and a judge sample is exactly the four fields D1 can produce. None of it has
been read from a real database.

**Next unblocked task:** F2P (product-v2 encoding) can start from `e06af7f`; C0 is unblocked
on IR-1/IR-4 and is the task that turns item 6 from *implemented* into *integrated*.

**Pending coordinator wiring:** IR-1 … IR-7 above. IR-1, IR-2 and IR-4 are what turn
`console-test` and `console-typecheck` green again; the coordinator merges lane A first and
resolves `test_parity_console.py`, `08 §5` (lane A also edits §5 for item 3's
`spool segment 1 → 2`) and `08 §10` alongside it.

**Unresolved findings** (each recorded above, none acted on): the `AuditAction` vocabulary
drift and the two non-null audit DTO fields; C's port refusing an audit row whose tenant
column is null; `console_judge_runs` never emitting the `id`/`run_id` that C1's projection
reads; 08 §5's stale `FETCH_*` names; `deepseek-v41-flash@2026-08-15` having no public model
id.

**Carryover items → commit and killing test**

| Item | Commit | Killing test |
|---|---|---|
| 6 console DTO / nullability / bounds | `1b7687b` | `legacy and key-less usage rows keep their nulls and are still counted` + `REGIME-03`; `usage pages walk every row exactly once, newest first` + `REGIME-01`/`REGIME-02`/`KEYNULL-01`/`KEYNULL-02`; `an aggregate without a window is invalid_request, not an all-time total` + `WINDOW-01`/`WINDOW-02`; `an unrecorded capture mode, an orphaned audit target and an unreached provider are nulls, not defaults` + `TRACEMODE-01`/`AUDITTARGET-01`/`AUDITTARGET-02`/`JUDGESAMPLE-02`/`JUDGECONSENT-01`; `judge runs separate estimates, limited evaluations and held budgets` + `JUDGESAMPLE-01`/`JUDGESAMPLE-03`/`JUDGEVERSION-01`/`XJUDGE-02` |
| 6, G0 leftovers: Q18, the Q16 pins, `walkAll` `assert.fail` | **closed before this lane** by F2.1 (`244bcf3`, item G): mutants `R41-08`, `Q16-01`, `Q16-02` and the `assert.fail` in `walkAll`. Verified present at `ec6c548` and still green | `no customer view ever names an operator (R41)`, `retention is capped and evaluation consent is a separate control`, `an operator label is the only path to operator authorship and calibration membership`, `ledger and trace pages walk every row exactly once` |
| 6, G0 leftover: the self-test label | `cfa4adf` — **still open at `ec6c548`**: F2.1 renamed a *different* label (`SELF-WRONG-CASE → SELF-UNKNOWN-CASE`) and `SELF-V03-REPLICA` was untouched. It is the IDEM-05 variant, so it is `SELF-IDEM-05-REPLICA` now | `node tests/contracts/run-mutants.mjs --self-test` 12/12 (the case `the old IDEM-05 edit is killed on an assertion, not on an exception`) |
| 6, code-point bounds in the TS fake | **closed before this lane** by F2.1/R54 (`codePoints` + `text_bounds.json` on both halves); one unclaimed extension in this lane, see *Limits* 4 | `the provisional input bounds hold` + `R54-01`; `test_python_classifies_every_text_bound_case_by_code_points` |
| 7 config names / defaults / `validate_runtime` | `213204a` | the 83 cases of the item-7 table, notably `test_a_bad_deployment_value_refuses_before_anything_mounts` and `test_the_g1_and_q1_constants_match_the_deployment_defaults`; Python mutants are IR-6 (lane A's file) |
| R62 model revision prefix | `904a5f0` | `V1-Q10 every query this page builds is one the service accepts, and its walk is stable` |

## Verification log

- 2026-09-22: Written with the three commits it identifies. Every count is quoted from the
  command output at the time it ran; no result is inferred, and the two red console targets
  are reported as red with their exact error lists. `tests/d`, `make api-mutants` as written
  and `make integration` were not run, with reasons. Nothing was pushed or deployed; no
  cloud, Supabase project or paid provider was contacted.
