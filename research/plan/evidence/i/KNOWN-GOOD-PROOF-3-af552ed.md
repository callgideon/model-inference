# KNOWN-GOOD-PROOF-3 (track I8, P-25, RR:51): both rollback targets proven through 0026

Lane `codex/known-good-proof-3`, base `400a7e94` (the integration tip with D10-0026-FENCE merged:
0001-0026). Implementation head `af552ed` (tests, mutants). The record, this file and the
coordinator update are in the commit after it. Everything ran task-locally on block `i8`
(postgres 55450, valkey 55495), plus three stand-in containers with no published port
(`infrx-i8-migrated-{supabase,plain,plain-p0025}`, reached on their bridge IPs, removed at the
end). I did not touch the hosted project, the box, AWS, SSM or any secret. i8 was free when the
lane started (09:51Z): the coordinator's `tests/i` run had already released
`/tmp/infrx-i8-postgres-55450.lock`, and the loop waited on it before each run anyway.

## Result

| target | image | schema history | suites | passed | skipped | deselected (SHAPE) | xfailed | failed | exit | output sha256 |
|---|---|---|---|---|---|---|---|---|---|---|
| bda15866e5700f3856d7142580da842fba9bbd23 | supabase/postgres 17.6.1.173 (no shim) | PASS 0001-0026 | 26 PASS / 0 FAIL | 384 | 0 | 13 | 5 | 0 | 0 | `017ee735…` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | supabase/postgres 17.6.1.173 (no shim) | PASS 0001-0026 | 26 PASS / 0 FAIL | 384 | 0 | 13 | 5 | 0 | 0 | `6c8bd789…` |
| bda15866e5700f3856d7142580da842fba9bbd23 | plain postgres 16.14 + shim | PASS 0001-0026 | 26 PASS / 0 FAIL | 384 | 0 | 13 | 5 | 0 | 0 | `8b90c7cb…` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | plain postgres 16.14 + shim | PASS 0001-0026 | 26 PASS / 0 FAIL | 384 | 0 | 13 | 5 | 0 | 0 | `5ca66661…` |

Every run ends `PASS through 0026`. The counts are the same as the through-0025 proof's
(KNOWN-GOOD-PROOF-2-3f7df77), suite for suite. The probe passes in all four runs:
`admit -> prepare -> claim -> complete -> get_owned -> read_result`, cross-org `not_found`, and
zero wallet drift. That probe's `complete` writes the result through the target's own
lease-less `put_result({job_id, text})`, which 0026 keeps as 0014's write.

**SHAPE: unchanged (13 cases, no entry added).** None of the targets' own tests exercises
`put_result` in a way 0026 changes. Their result writers (`test_store_requests`, `test_admission`,
`test_settle`, the conformance suites and the probe) send `{job_id, text}` with no `lease` key,
and 0026 leaves that path as 0014's body. No case failed on 0001-0026, so the committed driver
`infra/runbooks/schema_proof.py` (sha256 `7560a92b…`, unchanged) reproduces the proof without a
wrapper.

## Method (KNOWN-GOOD-PROOF-2's, extended to 0026)

1. **Stand-in for hosted, per image.** The script is KNOWN-GOOD-PROOF-2's inlined `standin.sh`
   (`744b887a…`) with one change: an optional third argument caps the second apply, for the grant
   diff. The label is `known-good-proof-3`. sha256 `720c3ee3…`:
   ```bash
   #!/usr/bin/env bash
   set -euo pipefail
   MODE=$1
   R=$(git rev-parse --show-toplevel)
   W=${2:-${TMPDIR:-/tmp}/kgp3-standin}
   CAP=${3:-}
   NAME=infrx-i8-migrated-$MODE${CAP:+-p$CAP}
   if [ "$MODE" = plain ]; then EXTRA="-e POSTGRES_DB=postgres"; IMG=postgres@sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20
   else EXTRA=""; IMG=supabase/postgres@sha256:7768d0d1d377250b718a9ad07f4661d008ebe6c96ecbbc4c08f3c5e53553e8fd; fi
   # ... (docker run / pg_isready / shim if plain / supabase_migrations.schema_migrations: as 744b887a)
   rm -rf "$W/m0018" && mkdir -p "$W/m0018" && cp "$R"/apps/app/supabase/migrations/00{01..18}_*.sql "$W/m0018/"
   ALL="$R/apps/app/supabase/migrations"
   if [ -n "$CAP" ]; then ALL="$W/m$CAP"; rm -rf "$ALL" && mkdir -p "$ALL"
     for f in "$R"/apps/app/supabase/migrations/[0-9][0-9][0-9][0-9]_*.sql; do b=$(basename "$f"); [[ "${b:0:4}" > "$CAP" ]] || cp "$f" "$ALL/"; done; fi
   export MIGRATE_DATABASE_URL="postgresql://postgres:standin-local@$IP:5432/postgres"
   M="$R/apps/infrx-api/.venv/bin/python $R/apps/infrx-api/deploy/migrate.py"
   for d in "$W/m0018" "$ALL"; do
     $M plan --dir "$d" | tee "$W/plan-$MODE${CAP:+-p$CAP}-$(basename "$d").txt"
     DG=$(grep -oE '[0-9a-f]{64}' "$W/plan-$MODE${CAP:+-p$CAP}-$(basename "$d").txt" | tail -1)
     $M apply --dir "$d" --expect "$DG"; echo "apply exit=$?"
   done
   echo "DSN_IP=$IP"
   ```

   | stand-in | step | plan digest | applied | exit | plan output sha256 |
   |---|---|---|---|---|---|
   | supabase, plain | 0001-0018 | `6995aef8addda0d0e16f4cc3c0ba76ca6d99ca7ce8568952eedf7b8511d54347` (= every earlier proof's) | 0001..0018 | 0 | `473b082e…` (= KNOWN-GOOD-PROOF-2's) |
   | supabase, plain | 0019-0026 | `4a6bffd9fd4112f793facdac79e58eaeadbd2681850f4978c546a52025f74909` | 0019..0026 | 0 | `916ad40f…` |
   | plain-p0025 (grant diff only) | 0019-0025 | `c21cb7b25f2bfadaff2f1fea2d488723ea046a1e4f9e95085c56db9f5c596dc2` (= KNOWN-GOOD-PROOF-2's) | 0019..0025 | 0 | `80e4d725…` (= KNOWN-GOOD-PROOF-2's) |

   `plan` printed the pending hash for 0026 as `3644ab5a02ff4c13af016104df46c6d7af94257e6091d2ea1807f916df00b1f0`.
   The hashes for 0019-0025 match the previous proof's.
2. **The committed driver**, once per target and image. Candidate `400a7e94`, block i8, loop
   script sha256 `55280009…`. The loop waits for i8 before each run: no `infrx-i8-{postgres,
   pgbouncer,valkey}` and a free `/tmp/infrx-i8-postgres-55450.lock`, polled every 60 s for up to
   60 min.
   ```
   [INFRX_D1_IMAGE=supabase] SCHEMA_PROOF_DSN=postgresql://postgres:standin-local@<bridge IP>:5432/postgres \
     apps/infrx-api/.venv/bin/python infra/runbooks/schema_proof.py <40-hex target> \
     --candidate 400a7e94 --task i8 --work <scratch>/work-<image>
   ```
   | run | window (UTC) | exit |
   |---|---|---|
   | bda1586, Supabase | 09:51:22-10:03:18 | 0 |
   | 4226315, Supabase | 10:03:18-10:15:03 | 0 |
   | bda1586, plain | 10:15:03-10:19:13 | 0 |
   | 4226315, plain | 10:19:13-10:23:05 | 0 |

## 0026 changes no grant (the grant inventory and the credit_schema oracle)

- **Grant inventory, 0001-0025 vs 0001-0026** (plain stand-ins `plain-p0025` and `plain`). The
  query `grants.sql` (sha256 `0cad1979…`) is KNOWN-GOOD-PROOF-2's scope: EXECUTE on every
  `public`/`infrx` function, table and column privileges and every policy, for `infrx_runtime`,
  `infrx_monitor`, `service_role`, `anon` and `authenticated`. This lane adds one line per
  function: owner, `prosecdef` and `proacl`. Both dumps are 3,431 lines and byte-identical
  (`452cc37d…`); the diff is empty, exit 0. So `infrx.put_result(jsonb)` keeps 0014's/0021's
  owner, SECURITY DEFINER and ACL, and no role gains or loses anything.
- **The `credit_schema` oracle** is this tree's `tests/d/test_credit_schema.py`: every seam's
  callers match the catalog. I ran it on 0001-0026 with `INFRX_D_TASK=i8`:
  - plain: **21 passed**, exit 0, output `8fa166d6…`;
  - `INFRX_D1_IMAGE=supabase`: **21 passed**, exit 0, output `668762a6…`.

## known-good.py before / after

Command: `python3 infra/rollout/known-good.py <sha> --applied NNNN`. The JSON is condensed to
verdict and checks.

| target | 0025 | 0026 | 0027 |
|---|---|---|---|
| bda1586 before (record through 0025) | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 (`migrations`: "applied beyond the tree: 0019-0026 but this checkout's ['0026'] are not the bytes its schema_proof ran on") | NOT-KNOWN-GOOD 1 (['0026', '0027']) |
| 4226315 before | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 (same) | NOT-KNOWN-GOOD 1 |
| bda1586 after | KNOWN-GOOD 0 | **KNOWN-GOOD 0** | NOT-KNOWN-GOOD 1 (['0027']) |
| 4226315 after | KNOWN-GOOD 0 | **KNOWN-GOOD 0** | NOT-KNOWN-GOOD 1 (['0027']) |

The run after the record:

```
$ python3 infra/rollout/known-good.py bda15866e5700f3856d7142580da842fba9bbd23 --applied 0026
bda1586 0026 KNOWN-GOOD commit=ok; preparation=ok; migrations=ok; config=ok; record=ok        exit=0
$ python3 infra/rollout/known-good.py bda15866e5700f3856d7142580da842fba9bbd23 --applied 0027
bda1586 0027 NOT-KNOWN-GOOD commit=ok; preparation=ok; migrations=FAIL: tree carries up to 0018; hosted has 0027;
  applied beyond the tree: 0019-0027 but this checkout's ['0027'] are not the bytes its schema_proof ran on; config=ok; record=ok   exit=1
$ python3 infra/rollout/known-good.py 422631591845fbd66b590c73d5ff4150318d9d7a --applied 0026
4226315 0026 KNOWN-GOOD commit=ok; preparation=ok; migrations=ok; config=ok; record=ok        exit=0
$ python3 infra/rollout/known-good.py 422631591845fbd66b590c73d5ff4150318d9d7a --applied 0027
4226315 0027 NOT-KNOWN-GOOD (the same migrations detail)                                     exit=1
```

Step 1 of rollback.md, without `--bundles`: `known-good.py --list --applied 0026 --set
S3_MEDIA_BUCKET --set MAX_VIDEO_SECONDS --set WORKER_CONCURRENCY --set LARGE_BODY_LIMIT --set
DATABASE_POOL_MAX_SIZE`. It exits 0 with 27af05a NOT-KNOWN-GOOD, 4226315 KNOWN-GOOD and bda1586
KNOWN-GOOD. `--bundles` (S3) is outside this lane.

## Record change (infra/rollout/known-good.json)

This proof was rerun whole, from an empty database, so each target's `schema_proof` is replaced
by the through-0026 proof. The fields that changed:
- `through` is 0026.
- `result` says 0001-0026.
- `candidate` is `400a7e94`.
- `shape_added` adds "0026: none" and that the grants are identical from 0025 to 0026.
- `not_proven` adds the lease-less write that is still unfenced (R147).
- `files` gains 0026 `3644ab5a…`.
- `evidence` lists this file first, then the two earlier proofs.

No other field of any entry changed. `known-good.py` is unchanged because the format needed no
new field.

## Tests

| command | result |
|---|---|
| `uv run --frozen --no-sync pytest -q tests/i/test_known_good_proof.py`, new tests with the OLD record (at `af552ed`) | **2 failed, 4 passed**, exit 1: `the_record_proves_both_targets…` (`assert ('0025' >= '0026')`); `both_targets_are_known_good_through_0026_and_not_beyond` (0026 NOT-KNOWN-GOOD) |
| the same, new record | 6 passed |
| `pytest -q tests/i/test_mutants.py -k "well_formed or every_case_is_covered"` | 2 passed |
| the 20 known_good/schema_proof mutants through the shared `run_mutant`, baseline narrowed to them (KNOWN-GOOD-PROOF-2's scratch method, driver `dfa8e9c0…`) | **20/20 killed**, exit 0 (`8eac5b10…`) |
| `INFRX_D_TASK=i8 uv run --frozen --no-sync pytest -q tests/i` (whole, including test_mutants' default subset and the i8 pooler cases) | **239 passed, 1 xfailed**, exit 0 (241 s, `d98b34aa…`) |
| `python3 research/plan/scripts/validate_plan.py` | exit 0 |

Changed cases and mutants:

| case | oracle | mutants |
|---|---|---|
| `the_record_proves_both_targets_on_the_candidate_schema` (now `through >= "0026"`) | fails on a record that proves only through 0025, or on a 0026 whose bytes differ from the proof's `files` | `known_good_record_stops_at_0025` (new, bda1586), `known_good_record_proves_other_0026` (new: one byte in 0026's header comment), `known_good_record_stops_at_0023`, `known_good_record_unproven` (anchors moved to `"through": "0026"`) |
| `both_targets_are_known_good_through_0026_and_not_beyond` (renamed from `…0025…`) | each REAL record entry, judged on a stand-in target commit carrying this checkout's real 0019-0026, must be KNOWN-GOOD at 0024, 0025 and 0026 and NOT-KNOWN-GOOD at 0027, with only `migrations` failing | the same four |

## Wiring requests

- **WR-KGP3-1** `research/plan/15-pending-inputs.md`, P-25 row (line 169): change "(…KNOWN-GOOD-PROOF-2:
  schema_proof reaches 0025 for both targets, plain PostgreSQL and the Supabase image; extend at
  0026)" to "(KNOWN-GOOD-PROOF-3: schema_proof reaches 0026 for both targets, plain PostgreSQL and
  the Supabase image; extend at 0027)". Append to its log: "2026-09-26: P-25 known-good:
  schema_proof reaches 0026 for bda1586 and 4226315 (KNOWN-GOOD-PROOF-3-af552ed); 0026 changes no
  grant". If WR-KGP2-3 was never applied, the row still says 0023; replace it the same way.
- **WR-KGP3-2** `infra/rollout/README.md`: line 53 (RR:51) and the paragraph at lines 59-74.
  - "through 0025 (KNOWN-GOOD-PROOF-2 …)" -> "through 0026 (KNOWN-GOOD-PROOF-3, plain
    PostgreSQL and the Supabase image)".
  - "up to 0025; beyond 0025 none qualifies" -> "up to 0026; beyond 0026 none qualifies".
  - "0024/0025 from `codex/d10-merge-2` 9e4e34ca" -> add ", 0026 from D10-0026-FENCE (400a7e94)".
  - `files` "0019-0025" -> "0019-0026".
  - "Not proven: any migration after 0025, or a 0022-0025 other than those bytes" -> "after
    0026 … 0022-0026".
  - Add to Not proven: "the targets' lease-less result write stays unfenced on 0026 (R147
    follow-up)".
  - Verification-log line: "2026-09-26 (KNOWN-GOOD-PROOF-3): `bda1586` and `4226315` proven on
    0001-0026, plain and Supabase image, SHAPE unchanged (13); evidence
    `research/plan/evidence/i/KNOWN-GOOD-PROOF-3-af552ed.md`."
  - Test: `known-good.py <t> --applied 0026` exits 0; `--applied 0027` exits 1.
- **WR-KGP3-3** (coordinator; R147 follow-up). The follow-up migration that refuses the lease-less
  `put_result` makes both targets fail on it. Their `PgJobStore.put_result` sends `{job_id, text}`,
  and their runner turns a refused result write into `platform_error`. So that migration must not
  be applied while `known-good.json` names 4226315 or bda1586 as the rollback target. When it
  lands, `known-good.py --applied <it>` stays NOT-KNOWN-GOOD for both, correctly, until a newer
  target that sends its lease is recorded.

## Not proven

1. The old release on the `infrx_runtime` login. 0023 revokes admit/claim_preparation from it,
   and 0024-0026 leave `infrx_runtime`'s grants unchanged.
2. Hosted rows written before the window: the suites use fresh rows.
3. A browser key INSERT by an unverified owner. 0024 refuses it whichever backend runs.
4. The fence for the targets' own writes. On 0026 their lease-less `put_result` is 0014's
   unfenced write, so F-1 stays reachable through a rolled-back release's stale generation. This
   is R147 as amended, not a regression of the proof.
5. A migration after 0026, or 0019-0026 bytes other than these: rerun, then record a new
   `through` and `files`.

## Estimate

Lane: 0 h remaining. Coordinator: WR-KGP3-1/2 take 0.1/0.2/0.4 h (optimistic/likely/
pessimistic). Confidence is high: these are text patches only, and no driver change is needed.
