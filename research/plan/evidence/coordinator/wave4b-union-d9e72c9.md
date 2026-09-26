# WAVE4B-UNION round 3b: code head `d9e72c9c`

Continues `wave4b-union-05d6309.md` (round 3, evidence commit `897e92c3`). Same lane and
isolation: union block, PostgreSQL 55458, Valkey 55454, MinIO `infrx-union-s3` 55455 (restarted,
removed after). The same forced `tests/q` port 55456 and `tests/i` `infrx-i8-*` pooler apply. I
never used the tip's worktree. No hosted DB, box, AWS or SSM. Nothing pushed.

## Commit

| Commit | What |
|---|---|
| `d9e72c9c` | **WR-UNION-2** (the coordinator sanctioned it in round 3b): `tests/w/prep_worker_mutants.py` `_layout` and `_pg_layout` call `copytree(API_DIR / "deploy", api / "deploy", dirs_exist_ok=True, …)`. P25-ENACT's `worker_main_mutants._layout` already copies `deploy/preflight.py`. Two lines. |

Fails before, at `05d63097` (round 3, recorded there):
- `make api-test`: 5 failed, all `FileExistsError: …/apps/infrx-api/deploy`. They are
  `test_prep_worker_mutants` (pristine, `prep_count_guessed`, `service_preparation_not_started`)
  and `test_w5_mutants` (pristine, `w5_readiness_barrier_media_only`).
- `python -m tests.w.prep_worker_mutants`: a runner error.
- The prep PG list: 8 failed.

The cc49ae90 test edit (`"(P-25"` or `"exact"`) is confirmed as is by the coordinator.

## Checks at `d9e72c9c` (apps/infrx-api; union env + MinIO)

| Command | Exit | Result |
|---|---|---|
| root: `make api-test` | 0 | **4510 passed, 5 skipped, 11 xfailed, 2 warnings in 2422.94s (0:40:22)**, 0 failed |
| `python -m tests.w.prep_worker_mutants` (memory list) | 0 | **70/70 killed** |
| `python -m tests.w.w5_mutants` (memory list) | 0 | **28/28 killed** |
| `pytest -q tests/w/test_prep_worker_mutants.py tests/w/test_w5_mutants.py` (default) | 0 | **13 passed, 2 skipped** (the two empty-parameter placeholders) |
| `INFRX_MUTANTS=all pytest -q tests/w/test_prep_worker_mutants.py tests/w/test_w5_mutants.py -k test_pg_mutant_is_killed` | 0 | **15 passed**: prep PG 8/8 and w5 PG 7/7 killed |
| `make api-mutants` | — | **not run**: it does not fit in the hour. It is every list (G 396, D migration/code lists on Docker, W/M/Q/I/T/J/contracts) serially on one PostgreSQL block, several hours. The lists this round touches are the prep-worker and W5 lists above, and the round-3 lists (relay G 94/94, worker-main 46/46 + 7/7 PG, KGP 3/3) |

`git merge-tree --write-tree --name-only claude/consumer-v1 codex/wave4b-union`, with the tip at
`4acb4f42`: exit 0, tree `6894a3ff…`, **no conflicted paths**. At round-3b start, with the tip
at `15e0d134`, it was also clean.

## Rulings draft status (R135–R139 reserved; the coordinator numbers them at the merge)

The draft is the coordinator's `scratchpad/wave4b/M6-rulings-draft.md` ("W5 rows" R135–R138,
the W5AW-6 comment, "W5-F5 row" R139). This is how each draft reads against the union at `d9e72c9c`:

| Row | Draft vs the union tree | Needed at numbering |
|---|---|---|
| R135 Readiness barrier (W5) | Implemented: `PreparationRunner(readiness=PgLifecycle(...))` in `worker/__main__.py`; the relay's `readiness.admit_ready` in `gateway/routes/relay.py`; `pilot.admission_readiness` fail-closed. | Apply **W5AW-6**: replace the row's last (gateway) sentence with the W5AW-6 text. The draft's comment still says "the R134 row"; after renumbering it is **R135**. The W5AW-6 text matches the tree: four fail-closed start refusals in every mode, refusals as lifecycle-table errors with no job/hold/outbox, `jobs.admit/admit_credit` only over the fakes, and media prepared from the refs the marker names (0019 `admit_ready` binds sources in the marker's transaction). |
| R136 fail_preparation (W5) | Implemented: 0022 `infrx.fail_preparation`, `PgJobStore.fail_preparation`, and `PreparationRunner._end` takes it. | The draft's last sentence ("Until the SQL exists (D10 0022) the worker logs and takes the bounded lapse path") is now history: 0022 is in the tree. Replace it with D10-FOLLOWUP's clause: "fenced like `prepared`, released free, settled once; only the same lease's identical call replays; every other refusal lapses its lease". |
| R137 R105 video bound, amended (W5) | Implemented (`preparation.py` `patches <= video <= most`; `encoder_cache_tokens` 16,384 → `unsupported_media`). | as drafted |
| R138 Reconciliation gauges (W5) | Implemented, then refined by W5-F5. | "a failed read publishes nothing and counts a reap error" holds only for non-`42501` failures now. Either add "(R139: on the monitor login; `42501` disables once)" or fold it into R139. |
| R139 Committed readiness is final (W5-F5) | Implemented: no post-admission recheck on the `admit_ready` path; `reconciliation_reader(MONITOR_DATABASE_URL)`; `42501` disables once. WR-W5F5-2's MANIFEST entry is in (`f145cd18`). | as drafted. Open follow-ups stay outside it: WR-W5F5-1 (D10 grant for `credit_wallet_holds`), WR-W5F5-4 (attach refusal after cancel; strict-xfail probe) |
| Log line in the draft | "Rulings R132–R135 numbered at the W5 merge … Next free ruling R136" is stale. | Should read "Rulings R135–R139 numbered at the backend union merge …; next free ruling R144", since R140–R143 are taken |

Proposed texts the union carries that have **no reserved number** (the coordinator decides:
new numbers from R144, or amendments):
- D10-FOLLOWUP "Flag writes" (V-G8TL-2: operator writes a regime flag only through
  `infrx.set_feature_flag`, EXCLUSIVE lock then the guarded update).
- D10-FOLLOWUP "Written re-registration" (M6 WR-7: a `written` re-registration restarts,
  never shortens, the grace; `discovered` never). This fits as an R129 amendment.
- DOOR-REVOKE (0023) closes R123's interim `runtime_unmarked_doors` clause. This fits as an
  R123 amendment: the runtime login lost `admit`/`claim_preparation`; the cutover check
  reports `[]`.
- P-25's enactment (P25-ENACT, WR-P25-1): a decided input, recorded in
  `15-pending-inputs.md` (WR-P25-5), not a ruling.

## Remaining effort

The coordinator's merge onto the tip (merge-tree clean at `4acb4f42`), numbering R135–R139,
and the E3C final run. Optimistic 0.5 h, likely 1 h, pessimistic 2 h; confidence medium-high.
Basis: `make api-test` is 0 failed and every touched mutant list is fully killed at `d9e72c9c`.

## Verification log

- 2026-09-26: round 3b written at code head `d9e72c9c`. Logs are in the session scratchpad: `union/results.txt` (`r3e-*`, `r3f-*`).
