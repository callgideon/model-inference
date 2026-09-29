# KNOWN-GOOD-REPROOF (task I8, KNOWN-GOOD-PROOF slice; R151/R201 condition 1): both rollback targets re-proven through 0051

Lane `codex/w5-known-good-reproof`, base `72dc76ad`. Commits: `1cea5025` tests first (failing),
`fca3ea38` driver SHAPE +1 (this file's head7), `aafd387e` the record, `e40f57b7` the mutants,
then this file and the coordinator update. Everything ran task-locally:
- the proof harness on keys `e3b2d` (Supabase image: postgres 55438, valkey 55468) and `d10`
  (plain: postgres 55442, valkey 55469). Both were free: no container and no held
  `/tmp/infrx-<key>-postgres-*.lock`.
- the stand-ins `infrx-e3b2d-migrated-{supabase,plain}`, with label `ai.infrx.lane=known-good-reproof`
  and no published port, reached on their bridge IPs.

All of these containers are removed. `i8` was not used for the proof, because another checkout
held its lock (`/tmp/infrx-i8-postgres-55450.lock`) when the lane started. The brief allows a
free key the driver accepts, and the driver needs a key with a PostgreSQL and a Valkey. I did not
touch the hosted project, the box, AWS, SSM, Vercel or any secret.

## Result

The candidate is `72dc76ad`, which carries migrations 0001-0051. The tip `claude/consumer-v1`
(`1b88b53b` at handback) has byte-identical migrations: `git diff 72dc76ad claude/consumer-v1 --
apps/app/supabase/migrations` is empty. The driver is committed `infra/runbooks/schema_proof.py`
at `fca3ea38` (sha256 `1a840fe3…`). It runs once per target and image.

| target | image | schema history | suites | passed | skipped | deselected (SHAPE) | xfailed | failed | exit | window (UTC) | output sha256 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bda15866e5700f3856d7142580da842fba9bbd23 | supabase/postgres 17.6.1.173 (no shim) | PASS 0001-0051 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 08:31:04-08:49:07 | `e1b318ce…` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | supabase/postgres 17.6.1.173 (no shim) | PASS 0001-0051 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 08:49:07-09:07:16 | `241d80b0…` |
| bda15866e5700f3856d7142580da842fba9bbd23 | plain postgres 16.14 + shim | PASS 0001-0051 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 08:31:04-08:39:03 | `ab3d51e2…` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | plain postgres 16.14 + shim | PASS 0001-0051 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 08:39:03-08:46:15 | `0ba71745…` |

Every run ends `PASS through 0051`. The probe passes in all four runs:
`admit -> prepare -> claim -> complete -> get_owned -> read_result`, cross-org `not_found`, and
zero wallet drift. Compared with KNOWN-GOOD-PROOF-3 (through 0026), 383 = 384 - 1 and 14 = 13 + 1:
the one case moved to SHAPE is below. Every other suite's count is unchanged.

## The one new SHAPE case: the candidate's port registry, not the schema

First run, with the driver unchanged since KNOWN-GOOD-PROOF-3 (sha256 `7560a92b…`): bda1586,
plain, 08:21:59-08:29:42Z. It exited 1 with `25 PASS / 1 FAIL`. The Supabase run and the 4226315
run were stopped at that point and all four runs were repeated from scratch.

```
FAIL tests/d/test_pgharness.py 1 failed, 6 passed
  test_the_decoy_is_the_tasks_own_and_d1s_is_unchanged: AssertionError: ('dlab', 57540)
  assert (57540 not in {<every registry port>})
```

The cause is outside the old runtime and outside the migrations.
- The driver copies the candidate's `infrx/contracts/tasklocal.py` into the scratch tree (see
  `scratch`).
- At 72dc76ad that registry carries the Lab keys.
- The old test computes each `d*` key's decoy as port + 40, which gives `dlab` 57500 -> 57540.
- 57540 is `t2i`'s ClickHouse port in `TASK_BLOCKS`.

This test lints a port registry: no SQL runs and no catalog is read. It passed with 400a7e94's
registry (KNOWN-GOOD-PROOF-3, 26/26). `schema_proof.py` therefore adds one SHAPE entry, deselected
by name and printed as SKIP with its reason, which makes 14. The new mutant
`schema_proof_drops_the_registry_shape_case` deletes that entry. The record-count case
(`f"{len(SHAPE)} SHAPE cases" in result`) kills it.

## What 0027-0051 change for the old releases

- No 0027-0051 file redefines a function created in 0001-0026: the intersection of
  `create [or replace] function <schema>.<name>` across the two ranges is empty.
- The only statements on pre-0027 objects:
  - `infrx.feature_flags_name_check` is dropped and re-added, only widened by `feedback` and
    the Lab flags (0028, 0031, 0041).
  - `grant usage on schema infrx to infrx_runtime` (already held).
- The Lab tables and functions are new. That is why the targets' suites pass unchanged on
  0001-0051 except for the registry lint above.
- I did not repeat KNOWN-GOOD-PROOF-3's grant-inventory diff (0026 vs 0051). The suites' EXECUTE
  and RLS enumerations are already SHAPE. So "no grant the old runtime uses changed" rests on the
  26 suites and the probe, not on a catalog diff. (Not proven, item 5.)

## Method

1. **Stand-in for hosted, per image.** `standin.sh plain|supabase` (sha256 `edc7f2ee…`) is
   KNOWN-GOOD-PROOF-2's inlined script (`744b887a…`) with three changes:
   - the container name is `infrx-$KEY-migrated-$MODE`, with `KEY` defaulting to `e3b2d`;
   - the label is `known-good-reproof`;
   - the default scratch directory is `kgr-standin`.

   After those, it runs `deploy/migrate.py plan` + `apply --expect` for 0001-0018, then for
   0001-0051.

   | stand-in | step | plan digest | applied | exit | log sha256 |
   |---|---|---|---|---|---|
   | supabase | 0001-0018 | `6995aef8addda0d0e16f4cc3c0ba76ca6d99ca7ce8568952eedf7b8511d54347` (= every earlier proof's) | 0001..0018 | 0 | `7b60cd95…` |
   | supabase | 0019-0051 | `50cf89cbc76149724c173cdb5772506ad354922bd2f24dd0ba7828e0d04efd91` | 0019..0051 | 0 | (same log) |
   | plain | 0001-0018 / 0019-0051 | the same two digests | 0001..0051 | 0 | `465b836e…` |

   The pending hashes that `plan` printed for 0027-0051 equal the sha256 of this checkout's files,
   which are the record's `files` (compared by script, no diff).
2. **The committed driver**, run twice in parallel: Supabase on `e3b2d`, plain on `d10`. Each
   runs both targets in turn (`run.sh`, sha256 `21950623…`):
   ```
   [INFRX_D1_IMAGE=supabase] SCHEMA_PROOF_DSN=postgresql://postgres:standin-local@<bridge IP>:5432/postgres \
     apps/infrx-api/.venv/bin/python infra/runbooks/schema_proof.py <40-hex target> \
     --candidate 72dc76ad --task e3b2d|d10 --work <scratch>/work-<image>
   ```

## The record (infra/rollout/known-good.json, commit aafd387e)

Each known-good target (4226315, bda1586) keeps every field. Only its `schema_proof` changes:
- `through` 0026 -> **0051**; `result` gives the counts above (the SHAPE count is `len(SHAPE)` = 14);
- `candidate` 72dc76ad;
- `shape_added` and `not_proven` are extended by appending, and no earlier text is removed;
- `files` gains 0027-0051, and 0019-0026 are unchanged;
- `evidence` is this file first, then the three earlier proofs;
- `superseded` is new and keeps the through-0026 proof's `through`, `result`, `candidate` and
  `evidence` verbatim.

This is the append-only rule applied to the proof. It does not reuse KNOWN-GOOD-PROOF-3's approach
of replacing the proof in place and noting "supersedes". `known-good.py` reads only `through`,
`files` and `evidence`, so `superseded` is inert. No target stopped qualifying, so no new
`known_good: false` entry was needed. 27af05a is unchanged, still `known_good: false`.

The 0027-0051 hashes:
0027 `d3ace256…` 0028 `23d40c37…` 0029 `77f23f58…` … 0049 `3e30eae0…` 0050 `464d978d…`
0051 `8f13e155…`. All 25 are in the record. The case
`the_record_proves_both_targets_through_the_lab_migrations_0051` binds them to this checkout's
bytes.

## known-good.py before / after

Command: `python3 infra/rollout/known-good.py <sha> --applied NNNN`.

| target | 0026 | 0027 | 0051 | 0052 |
|---|---|---|---|---|
| bda1586 before (record through 0026) | KNOWN-GOOD 0 | - | NOT-KNOWN-GOOD 1 (`migrations`: this checkout's ['0027' … '0051'] are not the bytes its schema_proof ran on) | NOT-KNOWN-GOOD 1 |
| 4226315 before | KNOWN-GOOD 0 | - | NOT-KNOWN-GOOD 1 (same) | NOT-KNOWN-GOOD 1 |
| bda1586 after | KNOWN-GOOD 0 | KNOWN-GOOD 0 | **KNOWN-GOOD 0** | NOT-KNOWN-GOOD 1 (['0052']) |
| 4226315 after | KNOWN-GOOD 0 | KNOWN-GOOD 0 | **KNOWN-GOOD 0** | NOT-KNOWN-GOOD 1 (['0052']) |

Rollback step 1, without `--bundles`: `known-good.py --list --applied 0051 --set S3_MEDIA_BUCKET
--set MAX_VIDEO_SECONDS --set WORKER_CONCURRENCY --set LARGE_BODY_LIMIT --set
DATABASE_POOL_MAX_SIZE`. It exits 0: 27af05a NOT-KNOWN-GOOD, 4226315 KNOWN-GOOD, bda1586
KNOWN-GOOD. The "after" rows were first taken with this file present as an empty placeholder, and
are rerun with it committed (below). `--bundles` (S3) is outside this lane.

## Tests

| command | result |
|---|---|
| `uv run --frozen --no-sync pytest -q tests/i/test_known_good_proof.py` at `1cea5025` (new cases, through-0026 record) | **2 failed, 5 passed**, exit 1 (`bd0cde00…`): `…through_the_lab_migrations_0051` (`assert '0026' >= '0051'`), `…known_good_through_0051_and_not_beyond` (0051 NOT-KNOWN-GOOD) |
| the same, after the record | 7 passed, exit 0 |
| `pytest -q tests/i/test_mutants.py -k "well_formed or every_case"` | 2 passed, exit 0 |
| `python tests/i/mutants.py <the 25 known_good*/schema_proof* mutants>` (the shared runner; this list's whole KNOWN-GOOD block plus the four I8-slice-6 judge mutants) | **25/25 killed**, exit 0 (`e3f8f5a6…`) |
| `INFRX_D_TASK=i8 uv run --frozen --no-sync pytest -q -rs tests/i` (whole, including test_mutants' default subset) | 354 passed, 4 skipped, 1 xfailed, **8 errors**, exit 1 (`b9df30fe…`) |
| `INFRX_D_TASK=i8 … pytest -q tests/i/test_observe.py tests/i/test_pooler.py tests/i/test_privilege_probe.py tests/i/test_rollback_drill.py` (the files of those 8, on a free i8) | **36 passed, 1 xfailed**, exit 0 (`13a243d7…`) |
| `python3 research/plan/scripts/validate_plan.py` | exit 0 |

Notes on the whole-`tests/i` run:
- **The 8 errors are the I8 pooler stack's setup.** `docker run infrx-i8-postgres` /
  `infrx-i8-pgbouncer` failed with exit 125 while another checkout's run held i8 (several lanes'
  `make api-test` start the same i8 stack). The same 8 cases pass when i8 is free (the row after
  it).
- **The 4 skips** are `tests/i/lab_eval/test_drills_pg.py`, which runs only on the `i5` key
  ("PostgreSQL only on the i5 task-local key"). They are not this lane's.
- **`make api-test` (the whole apps/infrx-api) was not run.** This lane changes only
  `infra/runbooks/schema_proof.py`, one SHAPE entry that only `tests/i` loads, and `tests/i`
  files. On this host every lane's `api-test` runs on the shared i8/w5 keys concurrently.

Cases and mutants:

| case | oracle | mutants |
|---|---|---|
| `the_record_proves_both_targets_through_the_lab_migrations_0051` (new) | fails when a proof stops short of 0051, when a proof's 0027-0051 hash is not this checkout's bytes, when a target loses its proof, or when the re-proof evidence is not named first | `known_good_record_stops_at_0026` (new), `known_good_record_proves_other_0051` (new: one byte in 0051's header comment), `known_good_record_proves_other_0027` (new), `known_good_record_unproven`, `known_good_record_stops_at_0023`, `known_good_record_stops_at_0025` |
| `both_targets_are_known_good_through_0051_and_not_beyond` (renamed from `…0026…`) | each REAL entry, judged on a stand-in target commit carrying this checkout's real 0019-0051, must be KNOWN-GOOD at 0024/0026/0027/0050/0051 and NOT-KNOWN-GOOD at 0052, with only `migrations` failing | the three new mutants, plus `…stops_at_0023/0025`, `…proves_other_0025/0026` |
| `the_record_proves_both_targets_on_the_candidate_schema` (unchanged) | the SHAPE count the record states is the driver's | `schema_proof_drops_the_registry_shape_case` (new), `schema_proof_drops_a_0025_shape_case` |

## Wiring requests

- **WR-KGR-1** `infra/rollout/README.md`: RR:51 row at line 55, the paragraph at lines 59-75,
  and the verification log.
  - "carry `through: 0026` (…)" -> "carry `through: 0051` (… 0027-0051 the Lab migrations at
    72dc76ad; KNOWN-GOOD-REPROOF reran the whole proof, plain PostgreSQL and the Supabase image)".
  - `files` "0019-0026" -> "0019-0051".
  - "Not proven: any migration after 0026, or a 0022-0026 other than those bytes" -> "after
    0051 … 0022-0051".
  - Log line: "2026-09-29 (KNOWN-GOOD-REPROOF, WR-KGR-1): both targets proven through 0051 on
    plain PostgreSQL and the Supabase image, SHAPE 14 (+ the registry decoy lint); evidence
    `research/plan/evidence/i/KNOWN-GOOD-REPROOF-fca3ea3.md`."
  - Test: `known-good.py <t> --applied 0051` exits 0; `--applied 0052` exits 1.
- **WR-KGR-2** `research/plan/15-pending-inputs.md`, P-25 known-good row: "reaches 0026 … extend
  at 0027" -> "reaches 0051 (KNOWN-GOOD-REPROOF-fca3ea3) … extend at 0052". Log line dated
  2026-09-29.
- **WR-KGR-3** (coordinator; R151/R201 bookkeeping). Condition 1 (the re-proof) is met by this
  lane's merge. Commit `95d5c050` records hosted already at 0001-0051 (deploy window
  2026-09-29). Until this record merges, `known-good.py --applied 0051` on the tip refuses both
  targets. The window therefore ran without a qualifying recorded rollback target between the
  apply and this merge. Record that in the window's log. WR-KGP3-3 still holds: the R147
  follow-up that refuses the lease-less `put_result` breaks both targets.

## Proposed ruling (the coordinator numbers it)

- A known-good re-proof appends to its `schema_proof`: the previous proof's `through`, `result`,
  `candidate` and `evidence` move verbatim into `superseded`, and `files` only gains entries. A
  SHAPE case that the candidate's copied port registry makes fail (not its SQL or catalog) is
  deselected by name like any other SHAPE case, with a mutant.

## Not proven

1. The old release on the `infrx_runtime` login. 0023 revokes admit/claim_preparation from it,
   and 0027-0051 grant it only `usage on schema infrx`, which it already holds.
2. Hosted rows written before the window, including any Lab rows. The suites use fresh rows.
   A rollback leaves the Lab tables unread and the Lab flags as they were set.
3. A browser key INSERT by an unverified owner. 0024 refuses it whichever backend runs.
4. The fence for the targets' own writes. Their lease-less `put_result` stays 0014's unfenced
   write (R147).
5. A catalog grant diff 0026 -> 0051 like KNOWN-GOOD-PROOF-3's. It was not rerun; the suites and
   the probe carry the proof.
6. A migration after 0051, or 0019-0051 bytes other than these: rerun, then append a new
   `through` and `files`.

## Estimate

Lane: 0 h remaining. Coordinator: WR-KGR-1/2 text patches take 0.1/0.2/0.4 h
(optimistic/likely/pessimistic). Confidence is high. Basis: KNOWN-GOOD-PROOF-3's WR-KGP3-1/2 are
the same patches.
