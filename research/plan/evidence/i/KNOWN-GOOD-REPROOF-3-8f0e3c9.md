# KNOWN-GOOD-REPROOF-3 (task I8, KNOWN-GOOD-PROOF slice; R151 condition 1 for the next hosted window): both rollback targets re-proven through 0056

Lane `codex/w5-known-good-reproof-3`, base `33547abd` (the tip; migrations 0001-0056, the newest Lab
migrations 0052 control reject, 0053 composition reads, 0054 release live, 0055 variants + requeue,
0056 control grants). `git diff 33547abd HEAD -- apps/app/supabase/migrations` is empty at every commit
of this lane. Commits: `8f0e3c93` tests first (red; this file's head7), `62f0e4f0` the record,
`1e1c4df7` the mutants, then this file + the coordinator update. (The `62f0e4f0` message says
`infrx/runbooks/schema_proof.py`; the path is `infra/runbooks/schema_proof.py`. Not amended, per the lane rules.)

Everything ran task-locally: keys `e3b2d` (Supabase image; postgres 55438, valkey 55468) and `d10`
(plain; postgres 55442, valkey 55469). Both were free at the start: no `infrx-e3b2d-*` / `infrx-d10-*`
container, and `flock -n` took `/tmp/infrx-{e3b2d-postgres-55438,d10-postgres-55442}.lock`. The stand-ins
`infrx-e3b2d-migrated-{supabase,plain}` (label `ai.infrx.lane=known-good-reproof-3`, no published port,
reached on their bridge IPs) are removed (0 containers with the label). Nothing hosted was touched: no
hosted Supabase, box, AWS, SSM, S3, Vercel or secret. `infra/rollout/hosted-migrate.sh` is NOT edited;
its patch is below as text.

## Result

Candidate `33547abd` (0001-0056). Driver: the committed `infra/runbooks/schema_proof.py`, unchanged since
`fca3ea38` (sha256 `1a840fe3c958cd04db8042cc7b71ab50b53f7fa4f75c1989f12c624d48e12e62`); SHAPE set = the
recorded 14. No driver change was needed.

| target | image | schema history | suites | passed | skipped | deselected (SHAPE) | xfailed | failed | exit | window (UTC) | output sha256 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bda15866e5700f3856d7142580da842fba9bbd23 | supabase/postgres@sha256:7768d0d1… (no shim) | PASS 0001-0056 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 20:19:18-20:49:48 | `14b31e03a2e9e7bfd2804c1bab35a9fcc36894bfdb873cc0bfb31b17b127d1de` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | supabase/postgres@sha256:7768d0d1… (no shim) | PASS 0001-0056 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 20:49:48-21:04:14 | `3b7035ccf93805f0c099effa588dea05887350789868db4fe91c12e278132278` |
| bda15866e5700f3856d7142580da842fba9bbd23 | postgres:16@sha256:33f923b0… + shim | PASS 0001-0056 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 20:19:18-20:42:56 | `db19d9438c71686d9da50bf8ca96dfc07fda90c8fc352ffc7f67856e8447e979` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | postgres:16@sha256:33f923b0… + shim | PASS 0001-0056 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 20:42:56-20:53:34 | `a5dbea7cbedbdc0b0b6c2b606871068bf621b5ac487bf6f3a2b7c0219b976027` |

Every run ends `PASS through 0056` after `PASS schema history = the candidate's files 0001-0056`; the probe
(`tests/d/test_schema_proof_probe.py`) passes in all four. The counts equal the through-0052 proof's
(KNOWN-GOOD-REPROOF-2-68ba65f) suite for suite: 0053-0056 add no SHAPE case.

**Port collisions: none.** All four runs passed on the first attempt (no `address already in use` in any
output), so no rerun was needed (`rerun.sh` below was prepared and not used).

## What 0053-0056 change for the old releases

| file | sha256 | top-level SQL |
|---|---|---|
| `0053_lab_composition_reads.sql` | `f166aa99ce7c9778595d601a62c3cd3a525abfd2608b6b1faf5df4f349b42631` | 3 new functions (`lab_release_decisions`, `lab_checkpoint_receipt`, `lab_providers_with`) |
| `0054_lab_release_live.sql` | `7658a7be0e9f7057300ea6fdab464674116a3602ecf2b50cec81b17252d027cc` | an index on the Lab's `lab_rollout_assignments` (0033); 1 new function `lab_release_live` |
| `0055_lab_variants_requeue.sql` | `4f05914d3734abe288aadd2d48e6e86e4c9cc074cb4f453b4bc54add775e342c` | a column `requeued_from` on the Lab's `lab_import_jobs` (0051); 2 new functions; grants to `infrx_lab_control` |
| `0056_lab_control_grants.sql` | `b499223e1f90a8a4fd5a32d9a18666294de5259d95953bc0b932606e969c60ab` | `grant execute` on Lab functions to `infrx_lab_control` only |

`grep -l` over 0001-0056 finds each new function name only in 0053-0056 (0053's two also in 0056's grant).
No 0001-0026 table, column, constraint, function or grant is touched; the old runtime calls none of these.
The unchanged suite counts and the probe confirm it.

## Method

1. **Stand-in per image.** `standin.sh plain|supabase` (sha256
   `a87c73f6109f2176e3a45fb9da4ae0c56f72123c052707a0c6026612ff8a420f`) = KNOWN-GOOD-REPROOF's script
   (`edc7f2eb…`) with the label `known-good-reproof-3` and the scratch default `kgr3-standin`. It runs
   `deploy/migrate.py plan` + `apply --expect` for 0001-0018, then for 0001-0056.

   | stand-in | step | plan digest | applied | exit | log sha256 |
   |---|---|---|---|---|---|
   | supabase | 0001-0018 | `6995aef8addda0d0e16f4cc3c0ba76ca6d99ca7ce8568952eedf7b8511d54347` (= every earlier proof's) | 0001..0018 | 0 | `5855195542e51ff0edfec85fd76d1da2f8b95563e2c1fbfa77006013e4e282d2` |
   | supabase | 0019-0056 | `977cc2eb8d1c50852ff46bb858dad83fe8672ad89ab07b273e3cc94745b10dae` | 0019..0056 | 0 | (same log) |
   | plain | 0001-0018 / 0019-0056 | the same two digests | 0001..0056 | 0 | `2863337520ee1b1999740ae9371c381c3bad4fa20c6134232dbb36f786df6ac6` |

   `plan` printed pending hashes 0019-0052 equal to the through-0052 record's `files`, and 0053-0056 as in the table above.
2. **The committed driver**, the two images in parallel (Supabase on `e3b2d`, plain on `d10`), each covering
   both targets in turn (`run.sh`, sha256 `b08559da07bd686c981de8e2816750559a6b1f48ef6f9185987b45a2bb73f0bb`;
   `rerun.sh` sha256 `1894afef82d15438b01f120c1a15e2df1396db45fe20e67bd85bbc21a7a5d1ef`, unused):
   ```
   [INFRX_D1_IMAGE=supabase] SCHEMA_PROOF_DSN=postgresql://postgres:standin-local@<bridge IP>:5432/postgres \
     apps/infrx-api/.venv/bin/python infra/runbooks/schema_proof.py <40-hex target> \
     --candidate 33547abd --task e3b2d|d10 --work <scratch>/work-<image>
   ```
   `standin-local` is a throwaway literal for a container with no published port, not a secret. The
   scripts and outputs live in the lane's scratch directory (`<scratchpad>/kgr3/`), not committed; their
   sha256 values here are the record.

## The record (infra/rollout/known-good.json), R224

For each known-good target (4226315, bda1586) every field is kept; only `schema_proof` changes:
- `through` 0052 -> **0056**; `result` gives the counts above; `candidate` is `33547abd (...)`;
- `shape_added` and `not_proven` are extended by appending (`; 0053-0056: none (...)`, `; 0053-0056 are the Lab's too (...)`); no earlier text removed;
- `files` gains 0053-0056; 0019-0052 byte-identical;
- `evidence` is this file first, then the five earlier proofs;
- `superseded` gains the through-0052 proof's `through`, `result`, `candidate` and `evidence` **word for
  word** in front: now 0052, 0051, 0026 (newest first).

The update script (`record.py`, a text edit keeping the layout) asserted before writing: every
non-`schema_proof` field equal; `superseded[0]` = the old four fields; `superseded[1:]` = the old list;
both texts start with the old text; `files` restricted to <= 0052 = the old `files`; `evidence[1:]` = the old
list. Both targets still qualify; 27af05a unchanged (`known_good: false`).

## known-good.py before / after

`apps/infrx-api/.venv/bin/python infra/rollout/known-good.py <sha> --applied NNNN`; "before" uses
`--registry` with `git show 33547abd:infra/rollout/known-good.json`.

| target | 0051 | 0052 | 0053 | 0056 | 0057 |
|---|---|---|---|---|---|
| bda1586 before (through 0052) | KNOWN-GOOD 0 | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 (`migrations`: ['0053'] not the bytes its schema_proof ran on) | NOT-KNOWN-GOOD 1 | NOT-KNOWN-GOOD 1 |
| 4226315 before | KNOWN-GOOD 0 | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 | NOT-KNOWN-GOOD 1 | NOT-KNOWN-GOOD 1 |
| bda1586 after | KNOWN-GOOD 0 | KNOWN-GOOD 0 | KNOWN-GOOD 0 | **KNOWN-GOOD 0** | **NOT-KNOWN-GOOD 1** (`migrations` only: ['0057']) |
| 4226315 after | KNOWN-GOOD 0 | KNOWN-GOOD 0 | KNOWN-GOOD 0 | **KNOWN-GOOD 0** | **NOT-KNOWN-GOOD 1** (same) |

Full shas: `bda15866e5700f3856d7142580da842fba9bbd23`, `422631591845fbd66b590c73d5ff4150318d9d7a`.
Rollback step 1 without `--bundles`: `known-good.py --list --applied 0056 --set S3_MEDIA_BUCKET --set
MAX_VIDEO_SECONDS --set WORKER_CONCURRENCY --set LARGE_BODY_LIMIT --set DATABASE_POOL_MAX_SIZE` exits 0
(27af05a NOT-KNOWN-GOOD, 4226315 KNOWN-GOOD, bda1586 KNOWN-GOOD). `--bundles` (S3) is outside this lane.

**R151 gate dry-check (no hosted).** `infra/lab/rollout/lab-migrate.sh --release <40 x a> --hosted-at 0051
--window P-08:dry` with `HOSTED_MIGRATE` = a stub carrying only the PATCHED `EXPECTED_PENDING` line and the
patched W7 POST `case` line, then `exec echo` (sha256 `8eb5edf0…`), and the REAL `known-good.py`: prints
`R151 conditions hold (window P-08:dry; pending 0052, 0053, 0054, 0055, 0056)`, exit 0, the stub echoed
(nothing dialled). The same with today's (unpatched) lines: `STOP (R151): condition 2: ... EXPECTED_PENDING
is '0027, ..., 0051', not '0052, 0053, 0054, 0055, 0056'`, exit 2. So with this record merged, condition 1
holds for 0056 and condition 2 needs only the patch below.

## The reviewed EXPECTED_PENDING patch for infra/rollout/hosted-migrate.sh (NOT applied; the operator applies it under runbook 08 §2)

`git apply --check -p1` passes on `33547abd`; `bash -n` of the patched file passes; patch sha256
`4439c31def559c1fc3d4729df580cd6826ca4234f2201a6974b6deefbe5ee287`. `EXPECTED_FLAGS` is unchanged: no file in
0052-0056 names `infrx.feature_flags` or the drift view.

```diff
--- a/infra/rollout/hosted-migrate.sh
+++ b/infra/rollout/hosted-migrate.sh
@@ -32,7 +32,7 @@
 [ -x apps/infrx-api/.venv/bin/python ] || { echo "run from the repo root after make api-env" >&2; exit 2; }
 PY=apps/infrx-api/.venv/bin/python
 HOSTED="host=aws-0-us-east-2.pooler.supabase.com port=5432 user=postgres.fcbnscgsymzdykendbrc dbname=postgres sslmode=require"
-EXPECTED_PENDING="0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051"   # R151/R201 window 2026-09-29 (operator: "deploy v1"): 0001-0026 -> 0001-0051, the Lab migrations
+EXPECTED_PENDING="0052, 0053, 0054, 0055, 0056"   # the next R151 window (runbook 08 §2): 0001-0051 -> 0001-0056, the Lab migrations; both targets proven through 0056 (KNOWN-GOOD-REPROOF-3)
 EXPECTED_FLAGS="credit_admission=true legacy_usd_admission=false signup_grant=true"   # hosted since the W7f CREDIT activation (2026-09-27); the Lab window changes no flag
 PORT=${PGPORT_LOCAL:-55697}
 BACKUP_ROOT=${BACKUP_ROOT:-$HOME/infrx-backups}
@@ -98,7 +98,7 @@
 HOSTED_PLAN=$($PY apps/infrx-api/deploy/migrate.py plan 2>>"$LOG") || { say "stop: hosted plan refused (nothing changed): 91-abort.sh, 93-restore-edge.sh"; exit 10; }
 printf '%s\n' "$HOSTED_PLAN" | tee -a "$LOG"
 HOSTED_APPLIED=$(sed -n 's/^applied: //p' <<<"$HOSTED_PLAN")
-case "$HOSTED_APPLIED" in *"0026 fenced_result") ;; *) say "stop: hosted applied list does not end at 0026 fenced_result (unrecorded hosted change)"; exit 10;; esac
+case "$HOSTED_APPLIED" in *"0051 lab_import_jobs") ;; *) say "stop: hosted applied list does not end at 0051 lab_import_jobs (unrecorded hosted change)"; exit 10;; esac
 SEED=$(sed 's/, /\n/g' <<<"$HOSTED_APPLIED" | sed "s/'/''/g" | sed -E "s/^([0-9]{4}) ?(.*)$/('\1', '\2')/" | paste -sd, -)
 docker exec "$CONTAINER" psql -q -U postgres -d infrx_rollout_copy -v ON_ERROR_STOP=1 -c "create schema supabase_migrations" -c "create table supabase_migrations.schema_migrations (version text primary key, statements text[], name text)" -c "insert into supabase_migrations.schema_migrations (version, name) values $SEED" >/dev/null
 export MIGRATE_DATABASE_URL="postgresql://postgres:$LOCALPW@127.0.0.1:$PORT/infrx_rollout_copy"
@@ -128,7 +128,7 @@
 [ "$(sed -n 's/^plan digest: //p' <<<"$PLAN")" = "$COPY_DIGEST" ] || { say "stop: hosted plan digest ≠ COPY_DIGEST (nothing changed): 91-abort.sh, 93-restore-edge.sh"; exit 10; }
 code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://marlin2b.callbill.ai/health || true)
 [ "$code" = 503 ] || { say "stop: public /health is '$code', not 503 just before the hosted apply (nothing changed)"; exit 10; }
-say "W7 hosted apply --expect $COPY_DIGEST (0027–0051, the Lab migrations under the R151/R201 window; never reverted afterwards)"
+say "W7 hosted apply --expect $COPY_DIGEST (0052–0056, the Lab migrations under the next R151 window; never reverted afterwards)"
 WROTE=1
 APPLY_OUT=$($PY apps/infrx-api/deploy/migrate.py apply --expect "$COPY_DIGEST" 2>>"$LOG") && rc=0 || rc=$?
 printf '%s\n' "$APPLY_OUT" | tee -a "$LOG"
@@ -140,7 +140,7 @@
 [ "$(sed -n 's/^applied: //p' <<<"$APPLY_OUT")" = "$EXPECTED_PENDING" ] || { say "W7: the apply printed '$APPLY_OUT' (a concurrent migrator?). restore.md A8"; exit 20; }
 POST=$($PY apps/infrx-api/deploy/migrate.py plan 2>>"$LOG") || { say "W7: hosted plan failed after the apply. restore.md A8, maintenance stays"; exit 20; }
 printf '%s\n' "$POST" | tee -a "$LOG"
-case "$POST" in *"0051 lab_import_jobs"$'\n'"nothing pending") ;; *) say "W7: hosted is not 0001-0051 with nothing pending. restore.md A8"; exit 20;; esac
+case "$POST" in *"0056 lab_control_grants"$'\n'"nothing pending") ;; *) say "W7: hosted is not 0001-0056 with nothing pending. restore.md A8"; exit 20;; esac
 HSTATE=$($PY - 2>>"$LOG" <<'PY'
 import os, psycopg
 with psycopg.connect(os.environ["MIGRATE_DATABASE_URL"]) as c:
@@ -152,4 +152,4 @@
 ) || HSTATE="read failed"
 say "hosted after apply: $HSTATE"
 [ "$HSTATE" = "$EXPECTED_FLAGS; drift_rows 0" ] || { say "W7: hosted flags/drift differ from the copy. restore.md A8"; exit 20; }
-say "W7 PASS: hosted 0001-0051, nothing pending; MIGRATION_DIGEST=$COPY_DIGEST"
+say "W7 PASS: hosted 0001-0056, nothing pending; MIGRATION_DIGEST=$COPY_DIGEST"
```

Note for the operator: `tests/i/lab/test_lab_rollout_steps.py::test_ldp__todays_hosted_migrate_carries_the_reviewed_patch`
is `xfail(strict=True)` until this patch lands (its reason/docstring still say "0052"); applying the patch
flips it to XPASS, i.e. a strict failure, so the same commit must drop that marker (WR-KGR3-3).

## Tests

| command | result |
|---|---|
| `INFRX_D_TASK=d10 uv run --frozen --no-sync pytest -q tests/i/test_known_good_proof.py` at `8f0e3c93` (tests at 0056, record through 0052) | **4 failed, 6 passed**, exit 1 (log `69eefa57…`): `…through_the_lab_migrations_0056` (`assert '0052' >= '0056'`), `…superseded_0051_…` and `…superseded_0052_…` (superseded[1]/[0] differ), `…known_good_through_0056_and_not_beyond` (0053+ NOT-KNOWN-GOOD) |
| the same at `62f0e4f0` (the record) | 10 passed, exit 0 |
| `pytest -q tests/i/test_mutants.py -k "well_formed or every_case"` at `1e1c4df7` | 2 passed, exit 0 (at `8f0e3c93` every_case failed: the new 0052 word-for-word case had no mutant yet) |
| `INFRX_D_TASK=d10 INFRX_MUTANTS=all pytest -q -rs tests/i/test_mutants.py -k "known_good or schema_proof"` | **35 passed** (35/35 killed, 0 survivors), exit 0, 204 s (log `0874bc04…`) |
| `INFRX_D_TASK=d10 pytest -q -rs tests/i` (whole, incl. test_mutants' default subset, and `tests/i/lab*`) at `1e1c4df7` | **396 passed, 6 skipped, 2 xfailed**, exit 0, 514 s (log `bb3039ce…`); the 6 skips are other lanes' keys (`tests/i/lab_control/test_control_routes_pg.py` wants l4, `tests/i/lab_eval/test_drills_pg.py` wants i5), not this lane's cases |
| `python3 research/plan/scripts/validate_plan.py` (with this file present) | PASS, exit 0 (957 local Markdown links across 418 documents) |

`make api-test` (the whole apps/infrx-api) was not run: this lane changes only `infra/rollout/known-good.json`
and `tests/i` files, and every reader of the record is in `tests/i` (`known-good.py`, `steps/86-cleanup.sh`
through `test_ops_steps.py`, `test_known_good_proof.py`, `tests/i/mutants.py`, and `lab-migrate.sh` through
`tests/i/lab/test_lab_rollout_steps.py`), all green above.

Cases and mutants:

| case | oracle | mutants |
|---|---|---|
| `the_record_proves_both_targets_through_the_lab_migrations_0056` (renamed from `…0052`, extended) | fails when a proof stops short of 0056; when a 0027-0056 hash is not this checkout's bytes; when this re-proof's evidence is not first; when `superseded` is not 0052, 0051, 0026 with REPROOF-2 then fca3ea3 evidence first (R224) | `known_good_record_stops_at_0052` (new), `known_good_record_proves_other_0056` (new: one byte in 0056's header comment), `known_good_record_drops_the_0052_proof` (new), `known_good_record_drops_the_0051_proof` (re-anchored at superseded[1]), plus the earlier `…stops_at_*` / `…proves_other_*` / `…unproven` (re-anchored at `"through": "0056"`) |
| `the_superseded_0052_proof_is_the_recorded_one_word_for_word` (new) | superseded[0] equals the through-0052 proof literal copied from the git blob at `33547abd` | `known_good_record_drops_the_0052_proof` (new), `known_good_record_rewords_the_0052_proof` (new: 383 -> 384 in its result) |
| `the_superseded_0051_proof_is_the_recorded_one_word_for_word` (now reads superseded[1]) | the through-0051 entry is still the ac8bc06d literal | `known_good_record_rewords_the_0051_proof`, `known_good_record_drops_the_0051_candidate` (both re-anchored at `}, {"through": "0051"`) |
| `both_targets_are_known_good_through_0056_and_not_beyond` (renamed from `…0052…`) | each real entry, judged on a stand-in target carrying this checkout's real 0019-0056, is KNOWN-GOOD at 0024/0026/0027/0051/0052/0056 and NOT at 0057, where only `migrations` fails | `known_good_record_stops_at_0052` (new), `known_good_record_proves_other_0056` (new), plus the earlier ones |

## Wiring requests (text for the coordinator; not applied)

- **WR-KGR3-1** `infra/rollout/README.md`:
  - RR row, line 55. Replace the first line below with the second (fenced so the README's relative link stays literal here):

```
carry a `schema_proof` through 0052 (0027-0052 are the Lab migrations at e9e32e0e; KNOWN-GOOD-REPROOF-2, plain PostgreSQL and the Supabase image; see the paragraph below), so the [known-good rollback](../runbooks/rollback.md#known-good-rollback-drill) has a target up to 0052; beyond 0052 none qualifies
carry a `schema_proof` through 0056 (0027-0056 are the Lab migrations at 33547abd; KNOWN-GOOD-REPROOF-3, plain PostgreSQL and the Supabase image; see the paragraph below), so the [known-good rollback](../runbooks/rollback.md#known-good-rollback-drill) has a target up to 0056; beyond 0056 none qualifies
```

  - Paragraph, lines 61-72:
    - "carry `through: 0052`" -> "carry `through: 0056`";
    - "0027-0052 the Lab migrations at e9e32e0e; KNOWN-GOOD-PROOF-2/3 and KNOWN-GOOD-REPROOF(-2) reran" -> "0027-0056 the Lab migrations at 33547abd; KNOWN-GOOD-PROOF-2/3 and KNOWN-GOOD-REPROOF(-2/-3) reran";
    - "the sha256 of 0019-0052 (`files`)" -> "the sha256 of 0019-0056 (`files`)";
    - "Not proven: any migration after 0052, or a\n0022-0052 other than those bytes" -> "Not proven: any migration after 0056, or a\n0022-0056 other than those bytes".
  - Verification log line to append: "- 2026-09-29 (KNOWN-GOOD-REPROOF-3, WR-KGR3-1): both targets proven through 0056 on plain PostgreSQL and the Supabase image with the driver at fca3ea38, unchanged (SHAPE 14, 26/26 suites, 383 passed); the through-0052 proof is kept in `superseded` in front of the through-0051 one (R224); evidence `research/plan/evidence/i/KNOWN-GOOD-REPROOF-3-8f0e3c9.md`; the row and the paragraph say 0056 and `files` 0019-0056. Task-local only, doc only; hosted stays at 0051 until the next R151 window."
  - Test: `known-good.py <bda1586|4226315 full sha> --applied 0056` exits 0, `--applied 0057` exits 1 (above).
- **WR-KGR3-2** `research/plan/15-pending-inputs.md`:
  - P-25 row, line 169. Replace "(I8-20260925T0002Z.json:27-28; KNOWN-GOOD-REPROOF-2-68ba65f: schema_proof reaches 0052 for both targets, plain PostgreSQL and the Supabase image; the through-0051 proof (KNOWN-GOOD-REPROOF-fca3ea3) kept in `superseded`; extend at 0053)" with "(I8-20260925T0002Z.json:27-28; KNOWN-GOOD-REPROOF-3-8f0e3c9: schema_proof reaches 0056 for both targets, plain PostgreSQL and the Supabase image; the through-0052 (KNOWN-GOOD-REPROOF-2-68ba65f) and through-0051 (KNOWN-GOOD-REPROOF-fca3ea3) proofs kept in `superseded`; extend at 0057)".
  - Log line to append: "- 2026-09-29: P-25 known-good: schema_proof reaches 0056 for bda1586 and 4226315 on plain PostgreSQL and the Supabase image (KNOWN-GOOD-REPROOF-3-8f0e3c9; merged on <merge lane>); `known-good.py <t> --applied 0056` exits 0, `--applied 0057` exits 1. R151 condition 1 for the window that applies 0052-0056 is met; the window still needs the reviewed EXPECTED_PENDING patch (WR-KGR3-3, operator, runbook 08 §2) and the operator window (conditions 2-3)."
- **WR-KGR3-3** (operator, runbook 08 §2; a Production Deploy edit, never a lane's): apply the `hosted-migrate.sh`
  diff above in one commit with `tests/i/lab/test_lab_rollout_steps.py`: drop the `xfail(strict=True)` marker on
  `test_ldp__todays_hosted_migrate_carries_the_reviewed_patch` and say 0056 / `*"0056 lab_control_grants"` in its
  reason/docstring (it XPASSes, i.e. fails strict, once the patch lands). Test: `lab-migrate.sh --release <R>
  --hosted-at 0051 --window <P-08 ref> --through w6b` passes all three conditions (dry-checked above with a stub).
- **WR-KGR3-4** (coordinator, R151 bookkeeping): condition 1 for applying 0052-0056 hosted is met only once this
  lane merges; until then `known-good.py --applied 0053..0056` on the tip refuses both targets, so nothing past
  0052 may be applied hosted before this merge. A new Lab migration 0057+ merged before the window re-opens
  condition 1 (rerun this lane one migration later). WR-KGP3-3 still holds (the R147 follow-up that refuses the
  lease-less `put_result` breaks both targets).

## Proposed ruling (the coordinator numbers it)

None new. R224 applied as written, with REPROOF-2's clarification (newest first; each re-proof prepends the proof it replaces).

## Not proven

1-5 as in KNOWN-GOOD-REPROOF-fca3ea3 (the `infrx_runtime` login; hosted pre-window rows, including Lab rows;
a browser key INSERT by an unverified owner; the targets' unfenced lease-less `put_result`; no catalog grant
diff 0052 -> 0056, the suites and probe carry the proof).
6. A migration after 0056, or 0019-0056 bytes other than these: rerun, then append (R224).
7. The Lab's own 0053-0056 paths (reads, release live, variants/requeue, the control login's grants): not the old
   runtime's SQL; their proof is the lab-sql lanes' `tests/l`/`l3sql` suites.
8. The patched `hosted-migrate.sh` itself was never run (it dials hosted); only its two gate-read lines were, through the stub.

## Open issues

- Registry ports below 57000 (e.g. 55468/55438/55442) remain inside the host ephemeral range
  (REPROOF-2 open issue 1). No collision this time; it is still possible.
- `tests/i` under `INFRX_D_TASK=i8`: the key clash REPROOF-2 recorded is unchanged (this lane used d10).

## Estimate

Lane: 0 h remaining. Coordinator: WR-KGR3-1/2 text patches 0.1/0.2/0.4 h; WR-KGR3-3 (operator patch + xfail
drop) 0.1/0.25/0.5 h (optimistic/likely/pessimistic). Confidence high. Basis: WR-KGR2-1/2 were the same
patches one re-proof earlier; this lane's own wall time 1.2 h (proofs 45 min, parallel).
