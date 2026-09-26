# LINT-2: the pre-existing ruff findings in `apps/infrx-api/tests`

Branch `codex/lint-2`, base `8c9c72f9`, code commit `f6e9e66c`. Scope: the 118 findings
`ruff check infrx tests` reported under `apps/infrx-api/tests/` at the base (LINT-1 had already
cleared `infrx/`), in 33 files, with no behaviour change and no weakened test. Only
`apps/infrx-api/tests/**` and this file changed. Hosted database, pilot box, AWS and SSM were not
touched. Docker was used for task `g8` (PostgreSQL 55447); see "Docker" below for the `i8`
pooler cases the full suite reaches on its own.

## Counts by rule

`uv run --frozen --no-sync ruff check infrx tests` (ruff 0.15.12, default rule set; there is no
ruff configuration in the repository).

| Rule | Base `8c9c72f9` | Head `f6e9e66c` | How |
|---|---|---|---|
| F811 redefined-while-unused | 35 | 0 | 33 moved to a conftest, 2 `noqa` (see the table) |
| E702 multiple statements (semicolon) | 30 | 0 | split one statement per line (tokenize script, AST-identical) |
| F401 unused import | 17 | 0 | 16 removed; 1 kept as a re-export with `noqa` |
| F541 f-string without placeholders | 14 | 0 | `ruff --fix`: prefix dropped, `{{text}}` unescaped to `{text}` |
| E402 import not at top | 7 | 0 | `tests/m/support.py`: moved to the top |
| E741 ambiguous name | 7 | 0 | renamed |
| F841 unused local | 6 | 0 | binding dropped, call kept |
| E731 lambda assignment | 2 | 0 | `def` |
| **Total** | **118** | **0** | |

## F811: every finding was a pytest fixture parameter

No F811 was a shadowed test, helper or duplicate. Each flagged line is a test function whose
**parameter** is named after a fixture imported into the module (pytest injects the fixture by
parameter name, so the import is the registration). No test was hidden, so none was resurrected;
the 35 test node ids are present at the base and at the head (the collection is identical, below).

`tests/m/test_retention.py` (33): the two fixtures `make_world` and `make_d10_world`
(`tests/m/worlds.py:212-222`, parametrized over the `f2c` and `d10` worlds) are now registered
in a new `tests/m/conftest.py` instead of being imported into the module. No other file in
`tests/m` names either fixture, and the mutant runner copies the whole `tests/` tree (so the
conftest travels with `tests/m/test_retention_mutants.py`). The file's old `# noqa: F401` on that
import was then unused (`ruff --extend-select RUF100`) and was removed. Moving the fixtures keeps
F811 live for any real redefinition later.

`tests/g/test_relay_readiness_pg.py` (2): the `box` fixture is imported from
`tests/w/test_worker_main.py`. A conftest would import that test module into every `tests/g`
run, so the two parameter lines carry `# noqa: F811` instead.

| # | Flagged line (base) | Fixture (first binding) | Test (the "redefinition") | Decision | Resurrected-test result |
|---|---|---|---|---|---|
| 1 | `tests/g/test_relay_readiness_pg.py:56` | `box` (import :26) | `test_w5_pg__the_worker_process_runs_a_video_admit_ready_admitted` | parameter: `# noqa: F811` | none shadowed; skipped at head and base (no MinIO: `INFRX_M_S3_ENDPOINT` unset) |
| 2 | `tests/g/test_relay_readiness_pg.py:94` | `box` (import :26) | `test_w5_pg__a_claim_the_moment_the_marker_commits_prepares_the_manifest` | parameter: `# noqa: F811` | same as 1 |
| 3 | `tests/m/test_retention.py:157` | `make_world` (import :32) | `test_a_restarted_collector_keeps_a_live_jobs_source` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 4 | `tests/m/test_retention.py:181` | `make_world` (import :32) | `test_the_local_only_view_deletes_a_live_jobs_source_after_a_restart` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 5 | `tests/m/test_retention.py:195` | `make_world` (import :32) | `test_candidates_come_from_the_store_in_bounded_pages` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 6 | `tests/m/test_retention.py:220` | `make_world` (import :32) | `test_a_row_naming_a_key_outside_the_media_prefixes_is_never_deleted` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 7 | `tests/m/test_retention.py:238` | `make_world` (import :32) | `test_an_attach_before_the_claim_or_the_tombstone_keeps_the_object` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 8 | `tests/m/test_retention.py:259` | `make_world` (import :32) | `test_an_attach_after_the_tombstone_is_refused_and_the_key_comes_back_new` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 9 | `tests/m/test_retention.py:282` | `make_world` (import :32) | `test_a_collector_whose_claim_expired_deletes_nothing` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 10 | `tests/m/test_retention.py:298` | `make_world` (import :32) | `test_an_unavailable_database_deletes_nothing_and_says_so` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 11 | `tests/m/test_retention.py:311` | `make_world` (import :32) | `test_a_lost_delete_acknowledgement_is_finished_by_a_later_pass` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 12 | `tests/m/test_retention.py:332` | `make_world` (import :32) | `test_a_failed_object_delete_stays_tombstoned_unreadable_and_is_retried` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 13 | `tests/m/test_retention.py:354` | `make_world` (import :32) | `test_a_delayed_delete_holds_its_key_until_it_is_acknowledged` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 14 | `tests/m/test_retention.py:383` | `make_world` (import :32) | `test_the_delete_is_sent_only_while_the_claim_has_a_request_timeout_left` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 15 | `tests/m/test_retention.py:406` | `make_world` (import :32) | `test_the_grace_boundary_is_exact_on_the_store_clock` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 16 | `tests/m/test_retention.py:480` | `make_d10_world` (import :32) | `test_pg_an_attach_racing_the_delete_serializes_on_the_content_row` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 17 | `tests/m/test_retention.py:564` | `make_world` (import :32) | `test_every_content_kind_goes_and_the_financial_metadata_stays` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 18 | `tests/m/test_retention.py:605` | `make_world` (import :32) | `test_the_persisted_result_expiry_is_the_boundary_not_todays_configuration` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 19 | `tests/m/test_retention.py:626` | `make_world` (import :32) | `test_a_failed_scrub_keeps_the_expired_result_unreadable_and_is_retried` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 20 | `tests/m/test_retention.py:655` | `make_world` (import :32) | `test_late_worker_output_for_an_ended_job_never_becomes_readable` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 21 | `tests/m/test_retention.py:677` | `make_world` (import :32) | `test_an_interrupted_upload_is_kept_for_its_window_then_collected` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 22 | `tests/m/test_retention.py:710` | `make_world` (import :32) | `test_the_runtimes_writers_register_every_object_before_writing_it` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 23 | `tests/m/test_retention.py:750` | `make_world` (import :32) | `test_a_source_fetched_again_is_not_collected_before_its_admission` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 24 | `tests/m/test_retention.py:770` | `make_world` (import :32) | `test_rv03_probe_the_durable_collector_keeps_a_restarted_gateways_live_upload` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 25 | `tests/m/test_retention.py:802` | `make_world` (import :32) | `test_a_write_over_a_key_being_deleted_is_a_retryable_refusal` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 26 | `tests/m/test_retention.py:831` | `make_world` (import :32) | `test_two_collectors_at_once_delete_each_object_exactly_once` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 27 | `tests/m/test_retention.py:852` | `make_world` (import :32) | `test_a_delete_whose_answer_was_lost_is_repeated_harmlessly` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 28 | `tests/m/test_retention.py:865` | `make_world` (import :32) | `test_a_runtime_restarted_mid_delete_finishes_it_and_keeps_live_work` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 29 | `tests/m/test_retention.py:906` | `make_world` (import :32) | `test_the_schedule_survives_a_failed_pass` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 30 | `tests/m/test_retention.py:932` | `make_world` (import :32) | `test_one_collector_across_passes_keeps_nothing_between_them` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 31 | `tests/m/test_retention.py:961` | `make_d10_world` (import :32) | `test_pg_an_unreachable_database_fails_the_pass_and_deletes_nothing` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 32 | `tests/m/test_retention.py:992` | `make_d10_world` (import :32) | `test_pg_an_unreachable_object_store_keeps_the_content_tombstoned_and_retried` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 33 | `tests/m/test_retention.py:1018` | `make_d10_world` (import :32) | `test_pg_load_and_cleanup_together_never_delete_live_content` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 34 | `tests/m/test_retention.py:1133` | `make_world` (import :32) | `test_run_records_every_pass_on_the_registry` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |
| 35 | `tests/m/test_retention.py:1162` | `make_d10_world` (import :32) | `test_pg_one_journal_prune_pass_removes_only_chunks_past_their_ttl` | parameter, not a redefinition: import moved to `tests/m/conftest.py` | none shadowed; passed at head |

`tests/m/test_retention.py` collects 79 node ids at the base and at the head. 43 of them are
`d10` cases, which ran against the `g8` PostgreSQL. All 79 passed at the head.

## Other changes

| Rule | Where (base line) | Change | Note |
|---|---|---|---|
| E702 | `tests/j/fakes.py:82,84,87`; `tests/j/test_rubric.py` (22 lines, 157-504); `tests/w/test_loop.py:1500,1503,1504` | each `a; b` split onto its own line at the statement's indent | `ast.dump` of both `tests/j` files is identical to the base. The `test_loop.py` generator keeps its statement order |
| E731 | `tests/d/test_ready.py:142` `gate = lambda: ...` | nested `def gate(): return ...` | same closure over `conn` |
| E731 | `tests/m/test_prepare.py:743` `shaped = (lambda ref: ...)` | nested `def shaped(ref): return (...)` | same closure over `tmp_path` |
| E741 | `tests/d/test_lease_races.py:318` `lambda c, l=lease` | `held=lease` | default argument still binds the current lease |
| E741 | `tests/i/test_observe.py:401`, `:419` comprehension `l` | `line`, `labels` | comprehension-local |
| E741 | `tests/d/code_mutants.py:19` `O`; `code_mutants_d5.py:22` `O`; `tests/g/mutants.py:31` `I`; `tests/w/w3_mutants.py:32` `I` | `OUTBOX` (14 uses), `OPS` (17), `INTAKE` (66), `INVENTORY_SH` (3; `INVENTORY` is already a case name there) | Renamed token by token with `tokenize`, so no string or attribute changed. `ast.dump` equals the base once the name is mapped. 12 continuation strings were re-indented to stay under the string they continue (whitespace inside brackets, AST-checked). No module reads these names from outside. |
| E402 | `tests/m/support.py:177,264-270` | `struct`, `os`, `pathlib`, `SimpleNamespace`, `Settings`, `digest_of`, `Media` moved to the top, after `from infrx.contracts import errors` (relative order of the `infrx` imports kept) | Nothing before line 264 binds or mutates these names or the environment. No `sys.path` or environment setup precedes them, so no `noqa` was needed. |
| F401 | `tests/contracts/test_conformance.py:156` `TraceLossReason`, `TraceMode` (function-local); `tests/d/checks_dispatch.py:13` `errors`; `checks_followup.py:24` `kinds`; `checks_ready.py:24` `DEFAULTS`; `tests/g/mutants.py:22` `sys`; `g/test_auth.py:19` `b`; `g/test_composition.py:14` `contextlib`; `g/test_errors.py:8` `asyncio`; `g/test_relay_matrix.py:21` `errors`; `m/perf/harness.py:52` `SimpleNamespace`; `t/test_trace_spool.py:20,40` `binascii`, `TraceEnvelope`; `w/loop_mutants.py:19` `Outcome`; `w/test_engine.py:38` `MODEL_EOS_TOKEN_IDS`; `w/test_prep_worker.py:34` `from_env` | removed (16) | Re-export search: every alias of each module (`cdp`, `cf`, `cr`, `mutation_list`, `suite`, `engine_tests`, ...) was grepped for `alias.name` and `from module import name`. No hits, and no `import *` in `tests/` |
| F401 | `tests/q/valkey_mutants.py:30` `Outcome` | **kept**, `# noqa: E402, F401` with the reason | `tests/q/test_valkey_mutants.py:98,105` reads `mutation_list.Outcome` (`valkey_mutants` imported as `mutation_list`). Removing it would break that list's self-tests |
| F541 | `tests/d/checks.py:1285,1619`; `tests/d/checks_credit.py:991-994,1632-1634,1668,2040,2042-2043`; `tests/w/test_worker_main.py:716` | `f` prefix dropped (`ruff --fix`) | `checks_credit.py:994` `f"'{{text}}', '{{text}}'); "` became `"'{text}', '{text}'); "`: the same runtime string |
| F841 | `tests/d/checks_credit.py:443,1206` `o1, o2 = personal_org(...), personal_org(...)` | `o1, _ = ...` | Both calls still run. `personal_org` does `fetchone()[0]`, which is an implicit existence check |
| F841 | `tests/g/jobs/test_jobs.py:544` `job = world.only_job()` | `world.only_job()` | kept: `only_job` unpacks `(job,) =`, so the case still fails unless there is exactly one job |
| F841 | `tests/w/test_engine.py:1553` `events = asyncio.run(collect(stream))` | `asyncio.run(collect(stream))` | the stream is still drained before the asserts |
| F841 | `tests/j/test_sampling.py:78` `revoked_at = datetime(...)` | removed | a pure constructor; the case uses the string `"2026-12-01T00:00:00Z"` |
| F841 | `tests/w/test_loop.py:560` `holder = {}` | removed | never read |

### Mutant anchors

Before committing, a session-scratch script (not committed) parsed every `tests/**/*mutants*.py` at
the base and collected 15,575 string constants of 6 or more characters. It then checked every one
that appears in a changed file at the base. Result: **0 anchors lost**. Four lists use a test file as
a target (`tests/m/test_s3.py`, `tests/g/ops/fakes.py`, `tests/i/pooler.py`, `tests/i/test_observe.py`).
The only one of those touched is `test_observe.py`, and its anchor (`rule_metrics`, the
`producer_scan_blind` mutant) is on a line that did not change. No `find`/`replace` string was
edited. The renamed `file=` constants carry the same values.

## Every `noqa` this lane added or changed

| Where | Directive | Reason |
|---|---|---|
| `tests/m/conftest.py:4` (new file) | `# noqa: F401 (pytest fixtures)` | The import is the fixture registration for `make_world` and `make_d10_world` |
| `tests/g/test_relay_readiness_pg.py:56`, `:94` | `# noqa: F811 (box: the fixture imported above)` | `box` is a fixture parameter. A `tests/g` conftest would import `tests/w/test_worker_main` into every G run |
| `tests/q/valkey_mutants.py:30` | `# noqa: E402, F401 (Outcome: test_valkey_mutants reads mutation_list.Outcome)` | E402 was already there (the import follows a `sys.path` insert). F401 was added because `Outcome` is re-exported |
| `tests/m/test_retention.py:31` | `# noqa: F401` **removed** | unused once the fixtures left the import (RUF100) |

## Collection: base vs head

| Where | Command | Collected |
|---|---|---|
| base: `git clone --shared` of the `infrx-impl` checkout, at `8c9c72f9`, `make api-env` (session scratch `lint2/base`) | `uv run --frozen --no-sync pytest --collect-only -q -p no:cacheprovider tests` | **4597** (4597 with `INFRX_D_TASK=g8` too) |
| head `f6e9e66c` (worktree) | same | **4597** (4597 with `INFRX_D_TASK=g8`) |

The full node-id lists were diffed. They are **identical**, both with and without `INFRX_D_TASK=g8`.
The brief's `pytest -q --collect-only -q tests | tail -1` prints only the warnings footer, so the
count is the `N tests collected` line of the single-`-q` form. The per-file `-qq` counts also sum to
4597.

## Commands (`apps/infrx-api` of the worktree unless noted)

| Command | Exit | Result |
|---|---|---|
| `make api-env` (worktree root; also in the base clone) | 0 | venv synced (frozen) |
| `uv run --frozen --no-sync ruff check infrx tests` at base | 1 | 118 findings (F811 35, E702 30, F401 17, F541 14, E741 7, E402 7, F841 6, E731 2) in 33 files |
| `uv run --frozen --no-sync ruff check infrx tests` at `f6e9e66c` | 0 | All checks passed |
| AST comparison of the 33 changed files against the base (scratch script) | 0 | `tests/j/fakes.py`, `tests/j/test_rubric.py`, `tests/g/test_relay_readiness_pg.py` and `tests/q/valkey_mutants.py` are AST-identical. The four renamed mutant lists are identical modulo the name. The others differ only by the edits above |
| mutant-anchor check (scratch script) | 0 | 15,575 anchor strings, 0 lost |
| `pytest --collect-only -q tests` base / head (plain and `INFRX_D_TASK=g8`) | 0 / 0 | 4597 / 4597, node ids identical |
| `INFRX_D_TASK=g8 uv run --frozen --no-sync pytest -q -p no:cacheprovider -rfEs tests` at `f6e9e66c` (run 2, the result of record) | 1 | **4538 passed, 0 failed**, 42 skipped, 9 xfailed, **8 errors** (46m06s). The 8 errors are all `tests/i` `i8_stack` setups refused with `BlockingIOError` because another run holds the I8 pooler lock `/tmp/infrx-i8-pooler-55450.lock` (see "Docker"). 4538 + 42 + 9 + 8 = 4597 |
| `INFRX_MUTANTS=all ... pytest -q tests/m/test_mutants.py` | 0 | 225 passed |
| `INFRX_MUTANTS=all ... pytest -q tests/j/test_mutants.py` | 0 | 124 passed |
| `INFRX_MUTANTS=all ... pytest -q tests/d/test_code_mutants.py` | 0 | 30 passed |
| `INFRX_MUTANTS=all ... pytest -q tests/w/test_mutants.py` | 0 | 164 passed |
| `INFRX_MUTANTS=all ... pytest -q tests/g/test_mutants.py` | 0 | 406 passed |
| extra, for lists this lane edited or whose suites it edited: `INFRX_MUTANTS=all` `tests/d/test_code_mutants_d5.py` / `tests/w/test_w3_mutants.py` / `tests/w/test_loop_mutants.py` / `tests/m/test_retention_mutants.py` | 0 each | 41 / 87 / 69 / 63 passed |
| `python3 research/plan/scripts/validate_plan.py` (root) | 0 | PASS |
| `git diff --stat 8c9c72f9..f6e9e66c` | 0 | 34 files (33 changed and the new `tests/m/conftest.py`), 222 insertions, 194 deletions, all under `apps/infrx-api/tests/` |

`tests/q/test_valkey_mutants.py` was not run in full mode. Its harness needs a Valkey outside
`g8`, and this lane changed only a comment on its import line (the file is AST-identical). The
full suite ran its default subset, which passed.

## Docker

- **`g8` (PostgreSQL 55447).** The base-comparison suite in the scratch clone was stopped at about
  35% (no failures up to that point) so the head suite could have the harness. The stop left
  `infrx-g8-postgres` labelled with the clone's checkout. Head run 1 (27m27s: 843 failed, 3693 passed,
  18 errors) was therefore **invalid**: every PostgreSQL case was refused with `ForeignContainer ...
  another checkout's run (.../lint2/base)`, and `tests/d/test_migration_mutants.py` and the rest
  failed the same way. After checking the container's `ai.infrx.d1.checkout` label (the
  scratch clone), I removed it with `docker rm -f -v infrx-g8-postgres` and ran again. Run 2 is the
  result of record, and its harness removed its own container at the end.
- **`i8` (not this lane's namespace).** `tests/i/conftest.py` `i8_stack` is not keyed by
  `INFRX_D_TASK`, so the full suite on `g8` still reaches the I8 pooler stack
  (`infrx-i8-postgres` :55450, `infrx-i8-pgbouncer`).
  - Run 1: the stack took the lock, and its `docker run` failed with exit 125 because another lane's
    `infrx-i8-postgres-supabase` held 127.0.0.1:55450. That left a never-started
    `infrx-i8-postgres` in `Created` state, with the I8 harness label. The I8 harness removes its
    own label's container on start, and the container was gone before run 2 finished. The harness
    refuses any container without its label, so no other lane's container was touched.
  - Run 2: another run held the lock, so all 8 `i8_stack` cases errored at setup without touching
    docker.
  - The 8 errored cases are the pooler, privilege-probe, rollback-drill and `test_observe`
    pooler cases. This lane did not rerun them. The one `tests/i/test_observe.py` case this lane
    edited (the canary comprehension) is not docker-gated, and it passed in run 2.

## Deviations

1. The E741 constants in four mutant lists were **renamed** (not `noqa`), per the brief. That
   is 100 name uses across those lists, all AST-equal once the name is mapped.
2. F811 resolved by a new `tests/m/conftest.py` (inside `tests/`) rather than 33 `noqa` markers.
3. The base collection was taken in the brief's fresh clone. The base full suite was not completed,
   because `g8` could only serve one run and head run 2 had 0 failures, so there was nothing to
   compare.
4. Four extra mutant lists were run in full mode.

## Unresolved

- 8 `tests/i` cases errored at setup in run 2 because the shared I8 pooler lock was held by
  another run. They need the `i8` stack free, and are the owning lane's to rerun. Nothing this lane
  changed is on their path.
- 2 `box` cases (`tests/g/test_relay_readiness_pg.py`) and the other MinIO-gated cases skip
  without `INFRX_M_S3_ENDPOINT`. They skip the same way at the base, and no MinIO was in this
  lane's grant.
- Wider `noqa` hygiene is out of scope. `ruff check tests --extend-select RUF100` lists 8
  pre-existing directives under `tests/`, and 13 more under `infrx/`, that name rules outside the default set (`BLE001`,
  `N802`, `A003`). They are harmless and were left unchanged. None of this lane's directives is
  unused.

## Verification log

- 2026-09-26: written by LINT-2 after code commit `f6e9e66c`, base `8c9c72f9`.
