# KNOWN-GOOD-REPROOF-4 (task I8, KNOWN-GOOD-PROOF slice; R151 condition 1 for the operator's SECOND hosted window): both rollback targets re-proven through 0059

Lane `codex/w5-known-good-reproof-4`, base `6ca7879f` (the tip; migrations 0001-0059, the newest
0057 trace_consent_read, 0058 lab_variant_identities, 0059 lab_control_grants_2, all LOCAL-ONLY).
`git diff 6ca7879f HEAD -- apps/app/supabase/migrations infra/runbooks` is empty at every commit of
this lane. Commit `1c986c61` = tests first (red; this file's head7). The sections below are
appended step by step.

## Result

Candidate `6ca7879f` (0001-0059). Driver: the committed `infra/runbooks/schema_proof.py`, unchanged since
`fca3ea38` (sha256 `1a840fe3c958cd04db8042cc7b71ab50b53f7fa4f75c1989f12c624d48e12e62`); SHAPE set = the
recorded 14. No driver change was needed.

| target | image | schema history | suites | passed | skipped | deselected (SHAPE) | xfailed | failed | exit | window (UTC) | output sha256 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bda15866e5700f3856d7142580da842fba9bbd23 | supabase/postgres@sha256:7768d0d1… (no shim) | PASS 0001-0059 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 00:38:19-00:50:20 | `76f674d48558f935576fd32e2094babfe6928b7becd459e90b03b02da5a0e1e6` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | supabase/postgres@sha256:7768d0d1… (no shim) | PASS 0001-0059 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 00:50:20-01:06:14 | `c3d79bd6f6b4100690351a8a942627a426370322a0d889ab13460a06f7515981` |
| bda15866e5700f3856d7142580da842fba9bbd23 | postgres:16@sha256:33f923b0… + shim | PASS 0001-0059 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 01:03:06-01:08:50 (third attempt) | `afbb886bcaa9e933aab5f12d1fc6f04305a24329c69600b5bc5608b4d0b9246f` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | postgres:16@sha256:33f923b0… + shim | PASS 0001-0059 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 00:42:14-00:47:48 | `14e00f61373d1bda180add0fdf1ee1ce401926d4f4eaabaaef4d781ef7384cff` |

All on 2026-09-30. Every run ends `PASS through 0059` after `PASS schema history = the candidate's files
0001-0059`; the probe (`tests/d/test_schema_proof_probe.py`) passes in all four. The counts equal the
through-0056 proof's (KNOWN-GOOD-REPROOF-3-8f0e3c9) suite for suite: 0057-0059 add no SHAPE case.

**Port collisions: two, both on plain/bda1586 (key d10), both rerun.**
1. 00:38:19-00:42:14: 6 suites FAIL (composition_pg, credit_jobstore_conformance, credit_schema,
   e3b_drills, jobstore_conformance, journal) with `could not start infrx-d10-postgres: ... failed to bind
   host port 127.0.0.1:55442/tcp: address already in use` in every failing case (output sha256
   `285dc9af803bdd68d204280277eb9198e9bb67e8fabf9613600db0062ecae9a8`, 6 suite logs kept).
2. rerun 00:47:50-00:53:46: `tests/d/test_pgharness.py` 2 failed (`could not start
   infrx-d10-dharness-postgres: ... 127.0.0.1:55482/tcp: address already in use`), 25 suites PASS
   (output sha256 `97fa920dfd03831a4f376002840a7a5a335b56ccaf0072e6565c003ad0d022d4`).
3. rerun 01:03:06-01:08:50: PASS (the row above); no `address already in use` in any kept output.

Both ports sit inside the host's ephemeral range (`ip_local_port_range` 32768-60999): an outgoing
connection's source port, not another lane's listener (no `infrx-d10-*` container existed before or
after). REPROOF-2 open issue 1, now observed.

Everything ran task-locally: keys `e3b2d` (Supabase image; postgres 55438, valkey 55468) and `d10`
(plain; postgres 55442, valkey 55469), both free at the start (no `infrx-e3b2d-*` / `infrx-d10-*`
container; `flock -n` on `/tmp/infrx-{e3b2d-postgres-55438,d10-postgres-55442,e3b2d-valkey-55468,d10-valkey-55469}.lock`
succeeded). Never d1/55432, nor l3/l4/t2f/e5l/e8l/lab-on/r2/b1/p3. The stand-ins
`infrx-e3b2d-migrated-{supabase,plain}` (label `ai.infrx.lane=known-good-reproof-4`, no published port,
reached on their bridge IPs) are removed (0 containers with the label; 0 `infrx-e3b2d-*`/`infrx-d10-*`).
Nothing hosted was touched: no hosted Supabase, box, AWS, SSM, S3, Vercel or secret.
`infra/rollout/hosted-migrate.sh` is NOT edited; neither patch is applied to the tree.

## What 0057-0059 change for the old releases

| file | sha256 | top-level SQL |
|---|---|---|
| `0057_trace_consent_read.sql` | `a1590379519eb129a357e8723a018f344b4745a58b7d6f25301f0814745fc9cd` | 1 new SECURITY DEFINER read `infrx.trace_consent(uuid, uuid)` over 0003's `public.api_keys` / `infrx.consent_history` (unchanged); `revoke all ... from public`; EXECUTE to `infrx_runtime` only |
| `0058_lab_variant_identities.sql` | `21c724b7a703c9d7fbfb3e0bc76316da9ac4a233384de00f8dcf4e89af43998e` | 1 new Lab table `lab_variant_identities` (RLS on; SELECT to service_role only), 2 triggers on it calling the existing `forbid_update_delete`/`forbid_truncate`, 5 new functions; EXECUTE on two of them to `infrx_lab_control` |
| `0059_lab_control_grants_2.sql` | `acb4067ebb2b03a56a72d2a5fd67591a6dff34593bd9ba02777ca3c096e5d4c9` | `grant execute` on `lab_release_live` (0054) and `lab_experiments` (0043) to `infrx_lab_control` only |

No 0001-0026 table, column, constraint, function or existing grant is touched. 0057 is the only file
that reaches the old runtime's login: it widens `infrx_runtime`'s EXECUTE surface by one function the
old runtime never calls; the one old case that enumerates that surface
(`test_dur_rls__the_execute_surface_is_enumerated`) was already a SHAPE case (deselected since
KNOWN-GOOD-PROOF). The unchanged suite counts and the probe confirm it.

## Method

1. **Stand-in per image.** `standin.sh plain|supabase` (sha256
   `78eee2b65b02ec36690ed4b437acfef3eb0ac28bc12e884cffadc36e45fec5a9`) = KNOWN-GOOD-REPROOF-3's script
   with the label `known-good-reproof-4` and the scratch default `kgr4-standin`. It runs
   `deploy/migrate.py plan` + `apply --expect` for 0001-0018, then for 0001-0059.

   | stand-in | step | plan digest | applied | exit | log sha256 |
   |---|---|---|---|---|---|
   | supabase | 0001-0018 | `6995aef8addda0d0e16f4cc3c0ba76ca6d99ca7ce8568952eedf7b8511d54347` (= every earlier proof's) | 0001..0018 | 0 | `4e3b870a7e06ca9651ceb3c0b41f3d0dead1443b50363ee391df4ea172481f84` |
   | supabase | 0019-0059 | `8fb10ea7f3ecf826ab39576cc1191d80dcad0494756b62129a2ed9c584de7853` | 0019..0059 | 0 | (same log) |
   | plain | 0001-0018 / 0019-0059 | the same two digests | 0001..0059 | 0 | `1dd6f37bf077d845141df66bb20376dc15aa29624fd77db8f88206d0fb31172c` |

2. **The committed driver**, the two images in parallel (Supabase on `e3b2d`, plain on `d10`), each covering
   both targets in turn (`run.sh`, sha256 `5b14885617e00f9e3547f18abc6ffa28b7ab3bf573c1faec3bca7897eb48aa32`;
   `rerun.sh` sha256 `837f9078a1961e4dc275e65f719e85a40e2acf00fcc11bf59c2c9b729f95de8a`, used twice for plain/bda1586):
   ```
   [INFRX_D1_IMAGE=supabase] SCHEMA_PROOF_DSN=postgresql://postgres:standin-local@<bridge IP>:5432/postgres \
     apps/infrx-api/.venv/bin/python infra/runbooks/schema_proof.py <40-hex target> \
     --candidate 6ca7879f --task e3b2d|d10 --work <scratch>/work-<image>
   ```
   `standin-local` is a throwaway literal for a container with no published port, not a secret. Scripts
   and outputs live in the lane's scratch directory (`<scratchpad>/kgr4/`), not committed; their sha256
   values here are the record.

## The record (infra/rollout/known-good.json), R224

For each known-good target (4226315, bda1586) every field is kept; only `schema_proof` changes:
- `through` 0056 -> **0059**; `result` gives the counts above; `candidate` is `6ca7879f (...)`;
- `shape_added` and `not_proven` are extended by appending (`; 0057-0059: none (...)`, `; 0057's trace_consent ... 0058-0059 are the Lab's too (...)`); no earlier text removed;
- `files` gains 0057-0059; 0019-0056 byte-identical;
- `evidence` is this file first, then the six earlier proofs;
- `superseded` gains the through-0056 proof's `through`, `result`, `candidate` and `evidence` **word for
  word** in front: now 0056, 0052, 0051, 0026 (newest first).

The update script (`record.py`, sha256 `49ba703e7d8d0ee1d7c647f8784a5e6a4a5e973ffa08d26f321a2ed37cc6975c`,
a text edit keeping the layout) asserted before writing: every non-`schema_proof` field equal;
`superseded[0]` = the old four fields; `superseded[1:]` = the old list; both texts start with the old
text; `files` restricted to <= 0056 = the old `files`; `evidence[1:]` = the old list. Both targets still
qualify; 27af05a unchanged (`known_good: false`).

## known-good.py before / after

`apps/infrx-api/.venv/bin/python infra/rollout/known-good.py <sha> --applied NNNN`; "before" uses
`--registry` with `git show 6ca7879f:infra/rollout/known-good.json`.

| target | 0056 | 0057 | 0059 | 0060 |
|---|---|---|---|---|
| bda1586 before (through 0056) | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 | NOT-KNOWN-GOOD 1 | NOT-KNOWN-GOOD 1 |
| 4226315 before | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 | NOT-KNOWN-GOOD 1 | NOT-KNOWN-GOOD 1 |
| bda1586 after | KNOWN-GOOD 0 | KNOWN-GOOD 0 | **KNOWN-GOOD 0** | **NOT-KNOWN-GOOD 1** (`migrations` only: ['0060'] not the bytes its schema_proof ran on) |
| 4226315 after | KNOWN-GOOD 0 | KNOWN-GOOD 0 | **KNOWN-GOOD 0** | **NOT-KNOWN-GOOD 1** (same) |

Full shas: `bda15866e5700f3856d7142580da842fba9bbd23`, `422631591845fbd66b590c73d5ff4150318d9d7a`.
Rollback step 1 without `--bundles`: `known-good.py --list --applied 0059 --set S3_MEDIA_BUCKET --set
MAX_VIDEO_SECONDS --set WORKER_CONCURRENCY --set LARGE_BODY_LIMIT --set DATABASE_POOL_MAX_SIZE` exits 0
(27af05a NOT-KNOWN-GOOD, 4226315 KNOWN-GOOD, bda1586 KNOWN-GOOD; output sha256 `4dbe15da…`).
`--bundles` (S3) is outside this lane.

## The second window's reviewed patch (committed, NOT applied): infra/lab/rollout/hosted-migrate-0057-0059.patch

sha256 `5954d7add66ffa1b897191e3a3e0d7bdfb940afde7982ef19dc1eec55e0e8ebc`; the same shape as
`hosted-migrate-0052-0056.patch` (no `diff --git`/`index` header, bare `@@` hunks, five hunks), built in a
scratch copy from the state AFTER the first window's patch:
- `EXPECTED_PENDING="0057, 0058, 0059"` (comment: the second R151 window, 0001-0056 -> 0001-0059, KNOWN-GOOD-REPROOF-4);
- the applied-list anchor `case "$HOSTED_APPLIED" in *"0056 lab_control_grants")` and its stop text;
- the W7 say line (0057–0059, the second R151 window);
- the W7 POST check `*"0059 lab_control_grants_2"$'\n'"nothing pending"` and its stop text;
- `W7 PASS: hosted 0001-0059`.
`EXPECTED_FLAGS` is unchanged: no file in 0057-0059 names `infrx.feature_flags` or the drift view.

Checks, each in a scratch copy of today's `infra/rollout/hosted-migrate.sh` (never the tree):

| check | result |
|---|---|
| `git apply --check -p1 hosted-migrate-0057-0059.patch` on today's file | exit 1 (`patch failed: ...:32`): it needs the first window first |
| `git apply -p1 hosted-migrate-0052-0056.patch` then `git apply --check -p1 hosted-migrate-0057-0059.patch` | exit 0 |
| both applied, `bash -n` | exit 0; byte-identical to the file the patch was built from |

**R151 gate dry-check (no hosted).** `infra/lab/rollout/lab-migrate.sh --release <40 x a> --window P-08:dry`
with `HOSTED_MIGRATE` = a stub whose first line is `exec echo "stub hosted-migrate (nothing dialled)" "$@"`
followed by the doubly patched `EXPECTED_PENDING` and W7 POST `case` lines (sha256 `686326c5…`), and the
REAL `known-good.py` (this record):

| stub | --hosted-at | result |
|---|---|---|
| both patches | 0056 | `R151 conditions hold (window P-08:dry; pending 0057, 0058, 0059)`, exit 0, the stub echoed (nothing dialled) |
| today's lines (sha256 `80d95eda…`) | 0056 | `STOP (R151): condition 2: ... EXPECTED_PENDING is '0027, ..., 0051'`, exit 2 |
| both patches | 0051 | `STOP (R151): condition 2: ... EXPECTED_PENDING is '0057, 0058, 0059'` (not 0052-0059), exit 2 |

So with this record merged, condition 1 holds for 0059, and condition 2 needs the two patches in order.

## launch-v1.sh: THROUGH picks the window

`THROUGH` defaults to **0059** (the newest re-proven level) and picks, per level: hosted's prior level and
its applied-list anchor (0056 -> `0051 lab_import_jobs`; 0059 -> `0056 lab_control_grants`), the patch
(0056 -> `hosted-migrate-0052-0056.patch`; 0059 -> `hosted-migrate-0057-0059.patch`), the release hint,
`PENDING` and `--hosted-at` for both `lab-migrate.sh` calls, and the log lines. Any other THROUGH exits 2.
`window()`: "already applied" when `EXPECTED_PENDING` equals this checkout's `PENDING`; otherwise
`git apply --check` first (for 0059 on a file without the first window's patch it stops, exit 2, naming it);
then the applied-list anchor in `hosted-migrate.sh` must be `*"<HOSTED_AT>"` (exit 2 otherwise); the
strict-xfail drop runs only for 0056 and only while the marker is there; for 0059 the
`test_ldp__todays_hosted_migrate_carries_the_reviewed_patch` case moves to `--hosted-at 0056`.

Dry runs in a scratch clone (`git clone --shared`, branch `claude/consumer-v1` at `1c986c61` + this
step's two files; `WINDOW=P-08:dry`, answer `n` at the commit prompt, so nothing past the prompt runs;
nothing hosted is dialled before it):

| case | result |
|---|---|
| `THROUGH=0056 launch-v1.sh x` on the tip (newest 0059) | exit 2: `carries 0059_lab_control_grants_2.sql (newer than the re-proven 0056): run from a worktree at a58eb0d6…` |
| `THROUGH=0057` | exit 2: `THROUGH=0057 is not a re-proven level (0056 or 0059)` |
| default THROUGH, `window`, hosted-migrate.sh as today | exit 2: `git apply --check` fails, `not in the state ...0057-0059.patch expects (for 0059: the 0056 window's patch committed first)` |
| the first window's commit simulated (0052-0056 patch + xfail drop), then `window` | the second patch applied, anchor OK, the test case moved to 0056, diff shown, exit 1 at `n`; then `pytest tests/i/lab/test_lab_rollout_steps.py::test_ldp__todays_hosted_migrate_carries_the_reviewed_patch` in the clone: 1 passed |
| EXPECTED_PENDING already 0057-0059 but the anchor edited to `0055 lab_variants_requeue` | exit 2: `applied-list anchor is not *"0056 lab_control_grants"` |

`bash -n infra/lab/rollout/launch-v1.sh` exit 0.

## Tests

Commits: `1c986c61` tests first (red), `e3da9051` the record (+ this file's first part, so the
evidence-path case holds there), `76dfa23a` the mutants, `7386b425` the second-window patch + launch-v1.sh,
then this file's remainder + the coordinator update.

| command | result |
|---|---|
| `INFRX_D_TASK=d10 uv run --frozen --no-sync pytest -q tests/i/test_known_good_proof.py` at `1c986c61` (tests at 0059, record through 0056) | **5 failed, 7 passed**, exit 1 (log `53668807…`): `…through_the_lab_migrations_0059` (`assert '0056' >= '0059'`), `…superseded_0051_…`, `…superseded_0052_…`, `…superseded_0056_…` (superseded[2]/[1]/[0] differ), `…known_good_through_0059_and_not_beyond` (0057/0059 NOT-KNOWN-GOOD) |
| the same at `e3da9051` (the record) | 12 passed, exit 0 |
| `pytest -q tests/i/test_mutants.py -k every_case` with `tests/i/mutants.py` as at `1c986c61` | 1 failed: `cases no mutant can break: ['test_ops_recover__the_superseded_0056_proof_is_the_recorded_one_word_for_word']` |
| `pytest -q tests/i/test_mutants.py -k "well_formed or every_case"` at step 3 | 2 passed, exit 0 |
| `INFRX_D_TASK=d10 INFRX_MUTANTS=all pytest -q -rs tests/i/test_mutants.py -k "known_good or schema_proof"` | **41 passed** (41/41 killed, 0 survivors; 36 earlier + 5 new), exit 0, 182 s (log `b5609785…`) |
| `INFRX_D_TASK=d10 pytest -q -rs tests/i/lab/test_lab_rollout_steps.py` (tree as committed) | 16 passed, 1 xfailed (the strict xfail, unchanged: no patch applied), exit 0 |
| `INFRX_D_TASK=d10 pytest -q -rs tests/i` (whole) at step 4 | **1 failed, 398 passed, 7 skipped, 2 xfailed**, exit 1, 415 s (log `68923f58…`). The one failure is `tests/i/lab_control/test_mutants.py::test_every_case_is_covered_by_a_mutant`: `test_control_routes_pg__an_assigned_running_release_reads_its_verdict_on_the_lab_login` has no mutant. PRE-EXISTING on base `6ca7879f` (this lane changes nothing under `tests/i/lab_control`; the case came with lab-rollout-7's wirings `f86220ab`); not this lane's path. The 7 skips are other lanes' keys (l4 ×3, i5 ×4). |
| `python3 research/plan/scripts/validate_plan.py` (with this file present) | PASS, exit 0 (957 local Markdown links across 425 documents) |
| `bash -n infra/lab/rollout/launch-v1.sh`; `bash -n` of the doubly patched hosted-migrate.sh (scratch) | exit 0; exit 0 |

`make api-test` (the whole apps/infrx-api) was not run: its default `INFRX_D_TASK` is the shared d1/55432,
which this lane must never use; this lane changes only `infra/rollout/known-good.json`, two `tests/i`
files, a new patch file and `launch-v1.sh`, and every reader of the record is in `tests/i` (`known-good.py`,
`steps/86-cleanup.sh` through `test_ops_steps.py`, `test_known_good_proof.py`, `tests/i/mutants.py`, and
`lab-migrate.sh` through `tests/i/lab/test_lab_rollout_steps.py`), all run above.

Cases and mutants:

| case | oracle | mutants |
|---|---|---|
| `the_record_proves_both_targets_through_the_lab_migrations_0059` (renamed from `…0056`, extended) | fails when a proof stops short of 0059; when a 0027-0059 hash is not this checkout's bytes; when this re-proof's evidence is not first; when `superseded` is not 0056, 0052, 0051, 0026 with REPROOF-3, REPROOF-2, fca3ea3 evidence first (R224) | `known_good_record_stops_at_0056` (new), `known_good_record_proves_other_0057` / `…_0059` (new: one byte in each header comment), `known_good_record_drops_the_0056_proof` (new), `known_good_record_drops_the_0052_proof` (re-anchored behind the 0056 entry), plus the earlier ones re-anchored at `"through": "0059"` |
| `the_superseded_0056_proof_is_the_recorded_one_word_for_word` (new) | superseded[0] equals the through-0056 proof literal copied from the git blob at `6ca7879f` | `known_good_record_drops_the_0056_proof` (new), `known_good_record_rewords_the_0056_proof` (new: 383 -> 384 in its result) |
| `the_superseded_0052_proof_…` (now superseded[1]) / `the_superseded_0051_proof_…` (now superseded[2]) | the through-0052 / through-0051 entries are still the 33547abd / ac8bc06d literals | `known_good_record_rewords_the_0052_proof` (re-anchored at `}, {"through": "0052"`), `…_0051_proof`, `…drops_the_0051_candidate` |
| `both_targets_are_known_good_through_0059_and_not_beyond` (renamed from `…0056…`) | each real entry, judged on a stand-in target carrying this checkout's real 0019-0059, is KNOWN-GOOD at 0024/0026/0027/0051/0052/0056/0057/0059 and NOT at 0060, where only `migrations` fails | `known_good_record_stops_at_0056` (new), `…proves_other_0057/0059` (new), plus the earlier ones |

## Wiring requests (text for the coordinator; not applied)

- **WR-KGR4-1** `infra/rollout/README.md`:
  - RR row, line 55. Replace the first line below with the second (fenced so the README's relative link stays literal here):

```
carry a `schema_proof` through 0056 (0027-0056 are the Lab migrations at 33547abd; KNOWN-GOOD-REPROOF-3, plain PostgreSQL and the Supabase image; see the paragraph below), so the [known-good rollback](../runbooks/rollback.md#known-good-rollback-drill) has a target up to 0056; beyond 0056 none qualifies
carry a `schema_proof` through 0059 (0027-0059 are the Lab migrations and 0057's trace consent read at 6ca7879f; KNOWN-GOOD-REPROOF-4, plain PostgreSQL and the Supabase image; see the paragraph below), so the [known-good rollback](../runbooks/rollback.md#known-good-rollback-drill) has a target up to 0059; beyond 0059 none qualifies
```

  - Paragraph, lines 61-72:
    - "carry `through: 0056`" -> "carry `through: 0059`";
    - "0027-0056 the Lab migrations at 33547abd; KNOWN-GOOD-PROOF-2/3 and KNOWN-GOOD-REPROOF(-2/-3) reran" -> "0027-0059 the Lab migrations and the trace consent read at 6ca7879f; KNOWN-GOOD-PROOF-2/3 and KNOWN-GOOD-REPROOF(-2/-3/-4) reran";
    - "the sha256 of 0019-0056 (`files`)" -> "the sha256 of 0019-0059 (`files`)";
    - "Not proven: any migration after 0056, or a\n0022-0056 other than those bytes" -> "Not proven: any migration after 0059, or a\n0022-0059 other than those bytes".
  - Verification log line to append: "- 2026-09-30 (KNOWN-GOOD-REPROOF-4, WR-KGR4-1): both targets proven through 0059 on plain PostgreSQL and the Supabase image with the driver at fca3ea38, unchanged (SHAPE 14, 26/26 suites, 383 passed); the through-0056 proof is kept in `superseded` in front of the through-0052 one (R224); evidence `research/plan/evidence/i/KNOWN-GOOD-REPROOF-4-1c986c6.md`; the row and the paragraph say 0059 and `files` 0019-0059. Task-local only, doc only; hosted stays where the operator's windows leave it (0051 now; 0056 after the first Lab window)."
  - Test: `known-good.py <bda1586|4226315 full sha> --applied 0059` exits 0, `--applied 0060` exits 1 (above).
- **WR-KGR4-2** `research/plan/15-pending-inputs.md`:
  - P-25 row, line 169. Replace "(I8-20260925T0002Z.json:27-28; KNOWN-GOOD-REPROOF-3-8f0e3c9: schema_proof reaches 0056 for both targets, plain PostgreSQL and the Supabase image; the through-0052 (KNOWN-GOOD-REPROOF-2-68ba65f) and through-0051 (KNOWN-GOOD-REPROOF-fca3ea3) proofs kept in `superseded`; extend at 0057)" with "(I8-20260925T0002Z.json:27-28; KNOWN-GOOD-REPROOF-4-1c986c6: schema_proof reaches 0059 for both targets, plain PostgreSQL and the Supabase image; the through-0056 (KNOWN-GOOD-REPROOF-3-8f0e3c9), through-0052 (KNOWN-GOOD-REPROOF-2-68ba65f) and through-0051 (KNOWN-GOOD-REPROOF-fca3ea3) proofs kept in `superseded`; extend at 0060)".
  - Log line to append: "- 2026-09-30: P-25 known-good: schema_proof reaches 0059 for bda1586 and 4226315 on plain PostgreSQL and the Supabase image (KNOWN-GOOD-REPROOF-4-1c986c6; merged on <merge lane>); `known-good.py <t> --applied 0059` exits 0, `--applied 0060` exits 1. R151 condition 1 for the second window (0057-0059) is met; that window still needs the first window (0052-0056) done and committed, the reviewed `infra/lab/rollout/hosted-migrate-0057-0059.patch` (operator, runbook 08 §2, via `launch-v1.sh window`), and the operator window (conditions 2-3)."
- **WR-KGR4-3** (coordinator, after merge; `infra/lab/rollout/launch-v1.sh` line with `WINDOW_RELEASE_HINT="<the known-good-reproof-4 merge on claude/consumer-v1>"`): replace the placeholder with the 40-hex merge commit of this lane (its newest migration is 0059 and its record proves 0059), so a tip that has moved past 0059 names a release to run the second window from. Test: `THROUGH=0059 launch-v1.sh x` on a checkout carrying 0060 prints that sha.
- **WR-KGR4-4** (operator, runbook 08 §2; a Production Deploy edit): the first window's commit (hosted-migrate-0052-0056.patch + the strict-xfail drop) must land on `claude/consumer-v1` before the second window's `launch-v1.sh window` runs on it; `launch-v1.sh` refuses otherwise (dry-checked above). Note: the first window runs from a worktree at `a58eb0d6` (detached); its commit then needs merging onto the tip, where `test_ldp__todays_hosted_migrate_carries_the_reviewed_patch` fails (the tip carries 0057-0059, pending after 0051 ≠ 0052-0056) until the second patch lands with the case's `--hosted-at 0056`. Recommended: run both windows back to back and land both commits together (or land the first with the case marked xfail(strict) naming the second patch).
- **WR-KGR4-5** (coordinator, R151 bookkeeping): condition 1 for applying 0057-0059 hosted is met only once this lane merges; a Lab migration 0060+ merged before the second window re-opens condition 1 (rerun this lane one migration later: the stand-ins, the record, the `THROUGH` case in `launch-v1.sh` and a `hosted-migrate-00NN-00MM.patch`). WR-KGP3-3 still holds.
- **WR-KGR4-6** (not this lane's path; owner lab-control / lab-rollout-7): `tests/i/lab_control/test_mutants.py::test_every_case_is_covered_by_a_mutant` fails on base `6ca7879f`: `test_control_routes_pg__an_assigned_running_release_reads_its_verdict_on_the_lab_login` needs a mutant in `tests/i/lab_control/mutants.py` (G2 `make check` blocks on it).

## Proposed ruling (the coordinator numbers it)

None new. R224 applied as written (newest first; each re-proof prepends the proof it replaces). Proposal
for the coordinator's consideration only: "R151 windows are sequential: a window's reviewed
hosted-migrate patch is built from the state after the previous window's patch and is committed as
`infra/lab/rollout/hosted-migrate-<from>-<to>.patch`; `launch-v1.sh` picks it by `THROUGH`."

## Not proven

1-5 as in KNOWN-GOOD-REPROOF-fca3ea3 (the `infrx_runtime` login; hosted pre-window rows, including Lab rows;
a browser key INSERT by an unverified owner; the targets' unfenced lease-less `put_result`; no catalog grant
diff 0056 -> 0059, the suites and probe carry the proof).
6. A migration after 0059, or 0019-0059 bytes other than these: rerun, then append (R224).
7. 0057's `trace_consent` on the `infrx_runtime` login and the Lab's 0058-0059 paths: not the old
   runtime's SQL; their proof is lab-capture-2's and the lab-sql lanes' suites.
8. The patched `hosted-migrate.sh` itself was never run (it dials hosted); only its gate-read lines were,
   through the stub. `launch-v1.sh window` was run only up to its commit prompt, in a scratch clone.

## Open issues

- Registry ports below 61000 (55442, 55482 here) sit inside the host's ephemeral range 32768-60999:
  two collisions this run (REPROOF-2 open issue 1 realised). A registry move above 61000 for the D keys
  would remove it (a registry change, not this lane's).
- WR-KGR4-6: the pre-existing lab_control every-case failure (G2).

## Estimate

Lane: 0 h remaining. Coordinator: WR-KGR4-1/2 text patches 0.1/0.2/0.4 h; WR-KGR4-3 hint 0.05/0.1/0.2 h;
operator windows (WR-KGR4-4) outside this estimate. Confidence high. Basis: WR-KGR3-1/2 were the same
patches one re-proof earlier; this lane's own wall time ~1.1 h (proofs 30 min in parallel, incl. two reruns).
