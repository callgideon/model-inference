# KNOWN-GOOD-PROOF-2 (track I8, P-25, RR:51): both rollback targets proven through 0025

Lane `codex/known-good-proof-2`, base `9e4e34ca` (D10-MERGE-2 head: 0001-0025). Implementation
head `3f7df77` (tests, mutants); the record, this file and the coordinator update are in the
commit after it. Everything ran task-locally on block `i8` (postgres 55450, valkey 55495) plus
three stand-in containers with no published port (`infrx-i8-migrated-{plain,supabase,p0023}`,
reached on their bridge IPs, removed at the end). Hosted, the box, AWS, SSM and secrets were not
touched.

## Result

| target | image | schema history | suites | passed | skipped | deselected (SHAPE) | xfailed | failed | exit |
|---|---|---|---|---|---|---|---|---|---|
| bda15866e5700f3856d7142580da842fba9bbd23 | plain postgres 16.14 + shim | PASS 0001-0025 | 26 PASS / 0 FAIL | 384 | 0 | 13 | 5 | 0 | 0 |
| 422631591845fbd66b590c73d5ff4150318d9d7a | plain postgres 16.14 + shim | PASS 0001-0025 | 26 PASS / 0 FAIL | 384 | 0 | 13 | 5 | 0 | 0 |
| bda15866e5700f3856d7142580da842fba9bbd23 | supabase/postgres 17.6.1.173 (no shim) | PASS 0001-0025 | 26 PASS / 0 FAIL | 384 | 0 | 13 | 5 | 0 | 0 |
| 422631591845fbd66b590c73d5ff4150318d9d7a | supabase/postgres 17.6.1.173 (no shim) | PASS 0001-0025 | 26 PASS / 0 FAIL | 384 | 0 | 13 | 5 | 0 | 0 |

Every run ends `PASS through 0025`. Per-suite counts equal the through-0023 table
(KNOWN-GOOD-PROOF-aab4b41) except `test_credit_schema` 16 (was 17) and `test_schema_postgres`
15 (was 17): the three new SHAPE cases below. The probe (`admit -> prepare -> claim -> complete
-> get_owned -> read_result`, cross-org `not_found`, zero wallet drift) passes in all four.
The Supabase-image runs close the previous proof's "not proven: the Supabase image".

## Method (the previous lane's, extended to 0025)

1. **Stand-in for hosted, per image** (`standin.sh plain|supabase`, sha256 `b505ff2a…`):
   `docker run` the harness image by digest (plain `postgres@sha256:33f923b0…` then
   `supabase_shim.sql` sha256 `9e3e322a…`; Supabase `supabase/postgres@sha256:7768d0d1…`, no
   shim), create `supabase_migrations.schema_migrations (version text primary key, statements
   text[], name text)`, then `deploy/migrate.py plan` + `apply --expect` for 0001-0018 and again
   for 0019-0025:

   | step | plan digest | result | exit |
   |---|---|---|---|
   | 0001-0018 (both images) | `6995aef8addda0d0e16f4cc3c0ba76ca6d99ca7ce8568952eedf7b8511d54347` (= the through-0023 proof's) | applied 0001..0018 | 0 |
   | 0019-0025 (both images) | `c21cb7b25f2bfadaff2f1fea2d488723ea046a1e4f9e95085c56db9f5c596dc2` | applied 0019..0025 | 0 |

   Pending file hashes printed by `plan`: 0024 `8e0bfd6288ad716342de932053b7f7aca8c9517b551458e1b86a1a84acc394e0`,
   0025 `276af0e24ed09af91386b0e7848c24f1c4aa57df1827a1ecabfa5fa035c554eb`; 0019-0023 unchanged
   from the previous proof (`65d3d693…`, `06a5dfde…`, `2aea0c8e…`, `1f44b4ae…`, `0ea64faa…`).
   Real Supabase image: 0001-0025 apply through migrate.py with exit 0.
2. **The recorded driver**, `infra/runbooks/schema_proof.py` (sha256 `34e5a73f…`, unchanged):
   ```
   SCHEMA_PROOF_DSN=<stand-in, bridge IP> [INFRX_D1_IMAGE=supabase] apps/infrx-api/.venv/bin/python \
     <driver> <40-hex target> --candidate 9e4e34ca --task i8 --work <scratch>
   ```
   It checks the stand-in's history against the candidate's 0001-0025 byte for byte, archives
   the TARGET's `apps/infrx-api` into scratch with the candidate's migrations, and runs every
   one of the target's own `tests/d` suites plus the probe, one pytest process each, on the
   target's own harness (which builds its database from 0001-0025).
3. **Three new SHAPE cases** (below). The committed driver's `SHAPE` does not name them, so the
   proof runs went through a scratch wrapper that loads the driver unchanged and adds them
   (sha256 `faf7799b…`, abridged below; the committed-driver patch is WR-KGP2-1):
   ```python
   proof = runpy.run_path(DRIVER, run_name="schema_proof")
   proof["SHAPE"].update({<the three cases and reasons in WR-KGP2-1>})
   main = proof["main"]
   sys.exit(main(sys.argv[1:]))
   ```

## What 0024/0025 change for the old releases: three SHAPE cases, no runtime path

First run, the unmodified driver (`run-plain-4226315.out`, sha256 `49b51241…`), 4226315 on the
plain stand-in: `24 PASS / 2 FAIL`, 384 passed, **3 failed**, 10 deselected, 5 xfailed, exit 1:

| case | failure | cause | runtime path? |
|---|---|---|---|
| test_schema_postgres::test_dur_rls__browser_roles_cannot_reach_protected_state | `owner creates a key (owner): 42501 new row violates row-level security policy for table "api_keys"` | 0024 (C3A WR-C3A-4) recreates `api_keys_insert_owner` with `public.consumer_may_create_key()`: a BROWSER key insert needs a verified individual. The old rig's owner has no confirmed email (the current rig confirms it, 9e4e34ca) | no: the App's browser path. The old runtime reads keys (`auth/keys.py`, PostgREST GET/PATCH as service) and inserts them only through `PgTenantStore` on the service seam, which `test_operations_pg` (13/13) walks on 0001-0025 |
| test_credit_schema::test_operator_seams__audit_keys_suspension_usage_holds | the same 42501 at its "deployed console's own insert" (`_jwt(CONSUMER_1)`) | same 0024 policy, unverified consumer | no: same. Its other seams (`bootstrap_operator_key`, `revoke_key`) are not called by the old runtime (`state/operations.py` calls `set_suspension`, `audit_by_idempotency_key`, `usage_records`, `active_holds`, `now`) |
| test_schema_postgres::test_dur_rls__the_browser_privilege_surface_is_enumerated | `authenticated holds ['SELECT'] on operator_unknown_usage / operator_wallet_drift, which is not in the enumerated surface` | 0025 grants the two operator views to `authenticated` (R143) | no: an enumeration of the old browser surface, like the six enumeration cases already in SHAPE |

All three passed on 0001-0023 (the through-0023 proof ran them: 26/26 suites PASS). bda1586's
first run showed the two `test_schema_postgres` failures identically; its other suites in that
run hit a foreign `infrx-i8-postgres` (see "i8 collision") and are not counted. The clean
bda1586 rerun of the unmodified driver on the two suites (05:47Z, after the collision; sha256
`83a72d28…`) gives exactly the same three: `test_credit_schema` 1 failed / 16 passed,
`test_schema_postgres` 2 failed / 15 passed, `FAIL through 0025`, exit 1.

Grant inventory, 0001-0023 vs 0001-0025 (plain stand-ins `p0023` and `plain`, query
`grants.sql` sha256 `5a8b9ba2…`: EXECUTE on every `public`/`infrx` function, table and column
privileges, and every policy, for `infrx_runtime`, `infrx_monitor`, `service_role`, `anon`,
`authenticated`; dumps `fddcb860…` (670 lines) and `83e872b0…` (683 lines); diff `a68f4fef…`):

- `infrx_runtime`: **no change**.
- `infrx_monitor`: + column SELECT `infrx.credit_wallet_holds.state` and policy `monitor_reads`
  (0024, W5-F5).
- `service_role`, `authenticated`: + `consumer_credit_ledger`, `consumer_may_create_key`,
  `operator_adjust_credit`/`operator_revoke_key`/`operator_set_suspension` (authenticated only),
  SELECT on the two operator views; `consumer_jobs(text, integer, uuid)` replaced by the
  defaulted 7-argument signature (same answers for old calls).
- policy `api_keys_insert_owner` gains `AND consumer_may_create_key()`.
- Nothing is revoked from any role. Neither target references `consumer_jobs`,
  `consumer_job_result`, `consumer_credit_ledger`, `credit_wallet_holds` grants, the operator
  RPCs/views or `console_operator` (`git grep` at both shas over `apps/infrx-api/infrx` and
  `deploy`: only the ledger kind string `operator_adjustment`).

## Commands, exit codes, digests

| command | exit | result | output sha256 |
|---|---|---|---|
| `standin.sh plain` | 0 | 0001-0018 `6995aef8…`, 0019-0025 `c21cb7b2…` applied | plans `473b082e…` / `80e4d725…` |
| `standin.sh supabase` | 0 | same digests, same applied lists | same plan outputs |
| unmodified driver, bda1586, plain (05:02Z) | 1 | contaminated by a foreign `infrx-i8-postgres`; `test_schema_postgres` 2 failed as above | `98787579…` |
| unmodified driver, 4226315, plain (05:06Z) | 1 | 24/26, 3 failed (the table above) | `49b51241…` |
| unmodified driver, bda1586, plain, `--suite test_credit_schema --suite test_schema_postgres` (05:47Z) | 1 | the same 3 failed, nothing else | `83a72d28…` |
| wrapper, bda1586, plain 05:09:13Z-05:13:33Z | 0 | 26/26, 384 passed, 13 deselected, 5 xfailed | `7be3ee87…` |
| wrapper, 4226315, plain 05:13:33Z-05:18:04Z | 0 | 26/26, 384 passed, 13 deselected, 5 xfailed | `0296159b…` |
| wrapper, bda1586, Supabase 05:18:04Z-05:30:23Z | 0 | 26/26, 384 passed, 13 deselected, 5 xfailed | `fb3b8028…` |
| wrapper, 4226315, Supabase 05:30:23Z-05:42:19Z | 0 | 26/26, 384 passed, 13 deselected, 5 xfailed | `2c44d0c1…` |
| grant inventory p0023 vs plain | diff 1 | the list above | `a68f4fef…` |

The Supabase image's `test_jobstore_conformance` takes 7 min 10-23 s per target (plain: 83 s).

## known-good.py before / after

`python3 infra/rollout/known-good.py <sha> --applied NNNN --set MAX_VIDEO_SECONDS --set WORKER_CONCURRENCY`:

| target | 0023 | 0024 | 0025 | 0026 |
|---|---|---|---|---|
| bda1586 before | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 (`migrations`: "['0024'] are not the bytes its schema_proof ran on") | NOT-KNOWN-GOOD 1 (['0024', '0025']) | NOT-KNOWN-GOOD 1 |
| 4226315 before | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 | NOT-KNOWN-GOOD 1 | NOT-KNOWN-GOOD 1 |
| bda1586 after | KNOWN-GOOD 0 | KNOWN-GOOD 0 | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 (`migrations`: "['0026'] are not the bytes its schema_proof ran on") |
| 4226315 after | KNOWN-GOOD 0 | KNOWN-GOOD 0 | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 |

The exact form the lane asked for, after the record:

```
$ python3 infra/rollout/known-good.py bda15866e5700f3856d7142580da842fba9bbd23 --applied 0025
bda1586 0025 KNOWN-GOOD commit=ok; preparation=ok; migrations=ok; config=ok; record=ok     exit=0
$ python3 infra/rollout/known-good.py bda15866e5700f3856d7142580da842fba9bbd23 --applied 0026
bda1586 0026 NOT-KNOWN-GOOD migrations=FAIL: tree carries up to 0018; hosted has 0026; applied beyond
  the tree: 0019-0026 but this checkout's ['0026'] are not the bytes its schema_proof ran on   exit=1
$ python3 infra/rollout/known-good.py 422631591845fbd66b590c73d5ff4150318d9d7a --applied 0025
4226315 0025 KNOWN-GOOD commit=ok; preparation=ok; migrations=ok; config=ok; record=ok     exit=0
$ python3 infra/rollout/known-good.py 422631591845fbd66b590c73d5ff4150318d9d7a --applied 0026
4226315 0026 NOT-KNOWN-GOOD migrations=FAIL: (same detail)                                  exit=1
```

(JSON condensed to one line per run.) rollback.md step 1 without `--bundles`:
`known-good.py --list --applied 0025 --set S3_MEDIA_BUCKET --set MAX_VIDEO_SECONDS --set
WORKER_CONCURRENCY --set LARGE_BODY_LIMIT --set DATABASE_POOL_MAX_SIZE` gives 27af05a
NOT-KNOWN-GOOD, 4226315 KNOWN-GOOD, bda1586 KNOWN-GOOD, exit 0. `--bundles` (S3) is outside
this lane.

## Record change (infra/rollout/known-good.json)

Each target's `schema_proof` is replaced by the through-0025 proof (it was rerun whole, from an
empty database, so it supersedes the through-0023 one; `known-good.py` reads one proof per
entry): `through` 0025, `result`, `candidate` 9e4e34ca, `shape_added` (new informational key;
`known-good.py` ignores it), `not_proven`, `files` 0019-0025 and `evidence` (this file, then the
through-0023 evidence). No other field of any entry changed. `known-good.py` is unchanged: the
format needed no new field.

## Tests

| command | result |
|---|---|
| `uv run --frozen --no-sync pytest -q tests/i/test_known_good_proof.py`, new tests, OLD record | **2 failed, 4 passed**, exit 1: `the_record_proves_both_targets…` (`assert ('0023' >= '0025')`), `both_targets_are_known_good_through_0025_and_not_beyond` (0024 NOT-KNOWN-GOOD) |
| the same, new record | 6 passed |
| `INFRX_D_TASK=i8 … pytest -q tests/i --deselect tests/i/test_mutants.py` | 187 passed, 50 deselected, 1 xfailed, exit 0 (the i8 pooler cases included) |
| `INFRX_D_TASK=i8 uv run --frozen --no-sync pytest -q tests/i` (whole, incl. test_mutants' default subset) | **237 passed, 1 xfailed**, exit 0 (248 s) |
| `pytest -q tests/i/test_mutants.py -k "well_formed or every_case_is_covered"` | 2 passed |
| the 14 KNOWN-GOOD mutants through the shared `run_mutant`, baseline narrowed to their own cases (the previous lane's scratch driver: `_SHARED._siblings = lambda m: chosen`) | **14/14 killed** |
| `python3 research/plan/scripts/validate_plan.py` | exit 0 |

New and changed cases:

| case | failure oracle | mutant |
|---|---|---|
| `the_record_proves_both_targets_on_the_candidate_schema` (now `through >= "0025"`) | a record proving only through 0023, or a 0025 whose bytes differ from the proof's `files` | `known_good_record_stops_at_0023`, `known_good_record_proves_other_0025` (plus the moved `known_good_record_unproven`) |
| `both_targets_are_known_good_through_0025_and_not_beyond` (new) | each REAL record entry, on a stand-in target commit (0001-0018 + preparation loop) with this checkout's real 0019-0025, must be KNOWN-GOOD at 0024/0025 and NOT-KNOWN-GOOD at 0026 with only `migrations` failing. A stand-in commit because a mutation copy is not a git checkout; the real-sha verdicts are the commands above | `known_good_record_stops_at_0023`, `known_good_record_proves_other_0025` |

`known_good_record_unproven`'s anchor moved from `"through": "0023"` to `"through": "0025"`
(it would otherwise be misdeclared).

## i8 collision (lane isolation finding)

The i8 block was not free for the whole run. Another run using the I8 pooler harness
(`tests/i/pooler.py`, label `ai.infrx.i8.harness=infrx-i8`; processes under a scratch clone
`rv1-e3a-clone`) created `infrx-i8-postgres` on 55450 twice:

1. At ~05:02Z, during this lane's first bda1586 run: the old `tests/d` harness refused it
   (`ForeignContainer: … no ai.infrx.d1.checkout label`), so 11 suites of that run failed
   without touching it. That run is not counted; the proof runs above came after and are clean.
2. At 05:37:40Z it created `infrx-i8-postgres` again while this lane's Supabase run held 55450;
   docker left it `Created` (`Bind for 127.0.0.1:55450 failed: port is already allocated`).
   This lane's later `tests/i` run (the I8 pooler cases, `test_rollback_drill`) brought up the
   pooler stack, whose `up()` removes any container with its label as "our crashed run": that
   never-started `Created` container was removed by it. Nothing ran in it and no data was lost,
   but it was the other run's object; recorded as a lapse. The pooler flock
   (`/tmp/infrx-i8-pooler-55450.lock`) and the `tests/d` flock
   (`/tmp/infrx-i8-postgres-55450.lock`) are different files for the same port, so the two
   harnesses do not serialise with each other (WR-KGP2-4).

## Wiring requests

- **WR-KGP2-1** `infra/runbooks/schema_proof.py` (not owned): add to `SHAPE`, after the
  `test_store_requests` entry, so the committed driver reproduces this proof without the
  wrapper:
  ```python
      # 0024/0025 (KNOWN-GOOD-PROOF-2): the browser key insert and the browser surface
      "tests/d/test_schema_postgres.py::test_dur_rls__browser_roles_cannot_reach_protected_state":
          "its positive control 'owner creates a key' is a browser INSERT by an unverified owner, which "
          "0024 (C3A WR-C3A-4) refuses by design - the App's path; the old runtime inserts keys only "
          "through PgTenantStore on the service seam (test_operations_pg)",
      "tests/d/test_credit_schema.py::test_operator_seams__audit_keys_suspension_usage_holds":
          "its 'deployed console's own insert' is a browser INSERT by an unverified consumer, which 0024 "
          "refuses by design; the old runtime's key/suspension SQL is walked by test_operations_pg",
      "tests/d/test_schema_postgres.py::test_dur_rls__the_browser_privilege_surface_is_enumerated":
          "enumerates the old browser surface (0025 grants authenticated SELECT on the operator views)",
  ```
  Test: rerun `schema_proof.py <each target> --candidate <tip> --task i8` on a stand-in; expect
  `26 PASS`, 384 passed, 13 deselected, `PASS through 0025`, exit 0.
- **WR-KGP2-2** `infra/rollout/README.md` line 51 (RR:51) and the paragraph at lines 56-68:
  "carry a `schema_proof` through 0023" -> "through 0025 (KNOWN-GOOD-PROOF-2, on plain
  PostgreSQL and the Supabase image)"; "beyond 0023 none qualifies" -> "beyond 0025 none
  qualifies"; `files` "0019-0023" -> "0019-0025"; drop "the Supabase image" from Not proven;
  add "a browser key INSERT by an unverified owner (0024 refuses it whichever backend runs)".
  Verification-log line: "2026-09-26 (KNOWN-GOOD-PROOF-2): `bda1586` and `4226315` proven on
  0001-0025, plain and Supabase image; evidence `research/plan/evidence/i/KNOWN-GOOD-PROOF-2-3f7df77.md`."
  Test: `known-good.py <t> --applied 0025` exit 0, `--applied 0026` exit 1.
- **WR-KGP2-3** `research/plan/15-pending-inputs.md`, P-25 row (line 169): "(…KNOWN-GOOD-PROOF
  proves both targets through 0023 on the union; extend at 0024/0025)" -> "(KNOWN-GOOD-PROOF-2:
  schema_proof reaches 0025 for both targets, plain and Supabase image; extend at 0026)"; append
  to its log: "2026-09-26: P-25 known-good: schema_proof reaches 0025 for bda1586 and 4226315
  (KNOWN-GOOD-PROOF-2-3f7df77)".
- **WR-KGP2-4** (coordinator, lanes): `tests/i/pooler.py` and `tests/d/pgharness.py` lock the
  same i8 port through different lock files; either share one lock path per port or give the
  pooler its own block. Until then, do not assign i8 to two concurrent runs.

## Not proven

1. The old release on the `infrx_runtime` login (0023 revokes admit/claim_preparation from it;
   unchanged by 0024/0025, whose `infrx_runtime` grants are identical to 0023's).
2. Hosted rows written before the window (the suites use fresh rows).
3. A browser key INSERT by an unverified owner: 0024 refuses it whichever backend runs; it is
   an App/policy consequence, not a rollback-target property.
4. A migration after 0025, or other 0019-0025 bytes: rerun and record a new `through`/`files`.

## Estimate

Lane: 0 h remaining. Coordinator: apply WR-KGP2-1..3 0.2/0.3/0.5 h
(optimistic/likely/pessimistic), confidence high (text patches plus one driver rerun per target,
~4.5 min each on plain).
