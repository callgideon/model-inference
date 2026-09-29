# KNOWN-GOOD-REPROOF-2 (task I8, KNOWN-GOOD-PROOF slice; R151/R201 condition 1 for the next window): both rollback targets re-proven through 0052

Lane `codex/w5-known-good-reproof-2`. The brief named base `1bb6f3c1`, which is not a commit in
this repository (`git rev-parse` fails). The lane therefore branched from the tip after merge #43,
`e9e32e0e`, which the coordinator note describes ("the tip after merge #43 ... carries migration
0052_lab_control_reject.sql"). Commits: `68ba65fe` tests first (failing; this file's head7), then
the record and this file, then the mutants, then the check results appended below and the
coordinator update. Everything ran task-locally:
- the proof harness on keys `e3b2d` (Supabase image: postgres 55438, valkey 55468) and `d10`
  (plain: postgres 55442, valkey 55469). Both were free at the start: no `infrx-e3b2d-*` /
  `infrx-d10-*` container, and `flock -n` took `/tmp/infrx-{e3b2d-postgres-55438,d10-postgres-55442}.lock`.
- the stand-ins `infrx-e3b2d-migrated-{supabase,plain}`, label `ai.infrx.lane=known-good-reproof-2`,
  no published port, reached on their bridge IPs. Both are removed (0 containers with the label).

`i8` was free too, but the first re-proof's key pair was kept so the runs are comparable. Nothing
hosted was touched: no hosted Supabase, box, AWS, SSM, S3, Vercel or secret.

## Result

The candidate is `e9e32e0e`, which carries migrations 0001-0052. `git diff e9e32e0e HEAD --
apps/app/supabase/migrations` is empty at every commit of this lane. The driver is the committed
`infra/runbooks/schema_proof.py`, unchanged since `fca3ea38` (sha256
`1a840fe3c958cd04db8042cc7b71ab50b53f7fa4f75c1989f12c624d48e12e62`). No driver change was needed:
the SHAPE set is the recorded 14.

| target | image | schema history | suites | passed | skipped | deselected (SHAPE) | xfailed | failed | exit | window (UTC) | output sha256 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bda15866e5700f3856d7142580da842fba9bbd23 | supabase/postgres@sha256:7768d0d1… (no shim) | PASS 0001-0052 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 11:19:03-11:32:30 | `de503e88c1c5f0119037b23d5c3f4c7429574e0cfd5adbc3a4cde4523f6930d1` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | supabase/postgres@sha256:7768d0d1… (no shim) | PASS 0001-0052 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 11:58:05-12:15:48 (rerun, below) | `c16b0ab0d7460e30b7bedbb45bac5321d7f5faa101b6452a1249cafe3e810ca0` |
| bda15866e5700f3856d7142580da842fba9bbd23 | postgres:16@sha256:33f923b0… + shim | PASS 0001-0052 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 11:19:03-11:25:20 | `5ffe02222a5446965fd88b7c3dc24e329ddd5b0ac2a04fa821e0957d28515eef` |
| 422631591845fbd66b590c73d5ff4150318d9d7a | postgres:16@sha256:33f923b0… + shim | PASS 0001-0052 | 26 PASS / 0 FAIL | 383 | 0 | 14 | 5 | 0 | 0 | 11:40:21-11:57:38 (rerun, below) | `d28d68289b7f03c222886e6722199a88b9c4a7cc2883a95da0f4c157fc42faa4` |

Every run ends `PASS through 0052` and prints `PASS schema history = the candidate's files
0001-0052`. The probe (`tests/d/test_schema_proof_probe.py`: admit -> prepare -> claim -> complete
-> get_owned -> read_result, cross-org `not_found`, zero wallet drift) passes in all four. The
counts equal the through-0051 proof's (KNOWN-GOOD-REPROOF-fca3ea3: 26 suites, 383 passed, 14
SHAPE, 5 xfailed) suite for suite. 0052 adds no SHAPE case.

## The two first runs that failed: host port collisions, not the schema

The two 4226315 runs failed the first time. In both, the target's own harness could not bind a
task-local port on 127.0.0.1 because the host kernel already had it in use. Both reruns (the same
command) passed whole.

| run | window (UTC) | result | output sha256 | failing cases and cause |
|---|---|---|---|---|
| 4226315 plain (d10) | 11:25:20-11:30:51 | 25 PASS / 1 FAIL, 381 passed, 2 failed, exit 1 | `4888d7ecb83fbc4696eee2123a60e63a0e13a49abb2bd4e86931d607b4cdd65e` | `test_pgharness.py::test_a_second_concurrent_run_is_refused_and_alters_nothing` and `::test_a_run_killed_mid_provision_is_cleaned_up_by_the_next_one`: `could not start infrx-d10-dharness-postgres: ... failed to bind host port 127.0.0.1:55482/tcp: address already in use` |
| 4226315 Supabase (e3b2d) | 11:32:30-11:55:58 | 24 PASS / 2 FAIL, 380 passed, 3 failed, exit 1 | `de21fb203c6bc17f2d675b596f8971378238f55c2155fc4106a7a7f17af89b83` | the same two `test_pgharness` cases (`infrx-e3b2d-dharness-postgres`, `127.0.0.1:55478 ... address already in use`), and `test_outbox_relay.py::test_outbox__both_dispatch_kinds_are_indexed_once_and_acknowledged[valkey]`: `could not start infrx-e3b2d-valkey: ... 127.0.0.1:55468/tcp: address already in use` |

Cause: the host's ephemeral source-port range is `32768 60999`
(`/proc/sys/net/ipv4/ip_local_port_range`), and it covers the task-local registry's ports. While
the Supabase run was still going, `ss -tan` showed `TIME-WAIT 172.18.0.1:55468 -> 172.18.0.3:5432`:
another lane's client (network `infrx-e8l_default`, container `infrx-e8l-postgres`) had used 55468
as its source port. Nothing ran SQL against the wrong catalog. The failing cases are the rig's own
container start (`docker run -p 127.0.0.1:<port>`), and the same cases pass in the reruns and in
both bda1586 runs. The first-run logs of those suites are kept beside the outputs
(`first-{plain,supabase}-4226315-test_pgharness.log`, `first-supabase-4226315-test_outbox_relay.log`).
The host sysctl is outside this lane, so this is reported as an open issue (below), not fixed.

## What 0052 changes for the old releases

`0052_lab_control_reject.sql` (sha256 `e170da75bd4309758682afb4aa7f3a4440fe6a806de0fdd88de4c1dfb75d16d5`)
does the following:
- creates two new functions, `infrx.lab_control_reject(jsonb)` and `infrx.lab_control_operator(jsonb)`.
  No earlier file names them: `grep -l` over 0001-0052 finds only 0052;
- `revoke all ... from public, anon, authenticated`;
- `grant execute ... to service_role, infrx_lab_control`.

Its only other top-level SQL is inside the function bodies. It has no DDL on an existing table,
column, constraint or function. The old runtime calls neither function. The unchanged suite
counts and the probe confirm this.

## Method

1. **Stand-in for hosted, per image.** `standin.sh plain|supabase` (sha256
   `edf847bd44569a23caa8aad13fff620bada68d55e489508a86515200bcd0147d`) is KNOWN-GOOD-REPROOF's
   script (KNOWN-GOOD-PROOF-2's inlined `744b887a…` with `KEY` defaulting to `e3b2d` and the name
   `infrx-$KEY-migrated-$MODE`), with two changes: the label is `known-good-reproof-2` and the
   default scratch directory is `kgr2-standin`. It runs `deploy/migrate.py plan` + `apply --expect`
   for 0001-0018, then for 0001-0052.

   | stand-in | step | plan digest | applied | exit | output sha256 |
   |---|---|---|---|---|---|
   | supabase | 0001-0018 | `6995aef8addda0d0e16f4cc3c0ba76ca6d99ca7ce8568952eedf7b8511d54347` (= every earlier proof's) | 0001..0018 | 0 | `6a6703b8a8a8755eeadedd862d126f7bd4c2199d005d8ad5344419e380b7208e` |
   | supabase | 0019-0052 | `3e80d577def944937caf0b60ce73a841ca0fd742c42bed92c87ec9d371c4aa42` | 0019..0052 | 0 | (same output) |
   | plain | 0001-0018 / 0019-0052 | the same two digests | 0001..0052 | 0 | `1a8d2434834cb2d032f824ef656f61e4287e124037f7d3b5afee245c023ffa06` |

   `plan` printed pending hashes for 0019-0051 that equal the through-0051 record's `files`, and
   printed `0052_lab_control_reject.sql sha256=e170da75…`.
2. **The committed driver**, run twice in parallel: Supabase on `e3b2d`, plain on `d10`. Each run
   covers both targets in turn (`run.sh`, sha256
   `21c7d9811c712dc45147e1590d583190c0d3e5755692850df90f487f54062587`). The two reruns used
   `rerun.sh <mode> <target>` (sha256 `ad136da93e3ab0a832c42598caad990ee3bf55b9da430ed11b0ae0fbbd779409`),
   which runs the same command for one target:
   ```
   [INFRX_D1_IMAGE=supabase] SCHEMA_PROOF_DSN=postgresql://postgres:standin-local@<bridge IP>:5432/postgres \
     apps/infrx-api/.venv/bin/python infra/runbooks/schema_proof.py <40-hex target> \
     --candidate e9e32e0e --task e3b2d|d10 --work <scratch>/work-<image>
   ```
   The stand-in password `standin-local` is a throwaway literal for a container with no published
   port. It is not a secret.

## The record (infra/rollout/known-good.json)

This change applies R224. For each known-good target (4226315, bda1586), every field is kept and
only its `schema_proof` changes:
- `through` 0051 -> **0052**;
- `result` gives the counts above;
- `candidate` is `e9e32e0e`;
- `shape_added` and `not_proven` are extended by appending ("; 0052: none (...)" and "; 0052 is the
  Lab's too (...)"). No earlier text is removed;
- `files` gains `"0052": "e170da75…"`, and 0019-0051 are byte-identical;
- `evidence` is this file first, then the four earlier proofs;
- `superseded` gains the through-0051 proof's `through`, `result`, `candidate` and `evidence`
  word for word, in front of the through-0026 entry (newest first).

A script checked all of this against the through-0051 record before the commit: every
non-`schema_proof` field is equal, `superseded[0]` equals the old four fields, `superseded[1:]`
equals the old list, both texts start with the old text, and `files` minus 0052 equals the old
`files`. Both targets still qualify, so no `known_good: false` entry is needed. 27af05a is
unchanged and still `known_good: false`.

## known-good.py before / after

Command: `apps/infrx-api/.venv/bin/python infra/rollout/known-good.py <sha> --applied NNNN`. The
"before" column uses `--registry` with `git show e9e32e0e:infra/rollout/known-good.json`.

| target | 0051 | 0052 | 0053 |
|---|---|---|---|
| bda1586 before (record through 0051) | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 (`migrations`: this checkout's ['0052'] are not the bytes its schema_proof ran on) | - |
| 4226315 before | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 (same) | - |
| bda1586 after | KNOWN-GOOD 0 | **KNOWN-GOOD 0** | NOT-KNOWN-GOOD 1 (['0053'], `migrations` only) |
| 4226315 after | KNOWN-GOOD 0 | **KNOWN-GOOD 0** | NOT-KNOWN-GOOD 1 (same) |

Rollback step 1, without `--bundles`: `known-good.py --list --applied 0052 --set S3_MEDIA_BUCKET
--set MAX_VIDEO_SECONDS --set WORKER_CONCURRENCY --set LARGE_BODY_LIMIT --set
DATABASE_POOL_MAX_SIZE` exits 0 with 27af05a NOT-KNOWN-GOOD, 4226315 KNOWN-GOOD and bda1586
KNOWN-GOOD. `--bundles` (S3) is outside this lane.

Note on "kept beside the outputs" above: the run outputs, the first-run suite logs and the three
scripts are in the lane's scratch directory (`<scratchpad>/kgr2/`). They are not committed. Their
sha256 values in this file are the record.

## Tests

| command | result |
|---|---|
| `uv run --frozen --no-sync pytest -q tests/i/test_known_good_proof.py` at `68ba65fe` (tests moved to 0052, record through 0051) | **2 failed, 6 passed**, exit 1 (log `1cf3f36e…`): `…through_the_lab_migrations_0052` (`assert '0051' >= '0052'`), `…known_good_through_0052_and_not_beyond` (0052 NOT-KNOWN-GOOD) |
| the same at `52eb276d` (the record) | 8 passed, exit 0 |
| `pytest -q tests/i/test_mutants.py -k "well_formed or every_case"` | 2 passed, exit 0 |
| `INFRX_MUTANTS=all pytest -q -rs tests/i/test_mutants.py -k "known_good or schema_proof"` | **29 passed** (29/29 killed, 0 survivors), exit 0, 173 s (log `abb0a293…`) |
| `INFRX_D_TASK=i8 pytest -q tests/i/test_mutants.py` (the default subset + the runner's self-tests, i8 free) | 59 passed, exit 0, 241 s (`ac8060b4…`) |
| `INFRX_D_TASK=d10 pytest -q -rs tests/i` (whole, including test_mutants' default subset) | **363 passed, 4 skipped, 1 xfailed**, exit 0, 379 s (`af3b4961…`) |
| `python3 research/plan/scripts/validate_plan.py` | exit 0 at `a71f0029` (before this evidence existed); **exit 1** at `62b9adab`/`6d8f7476` (broken link from the WR-KGR2-1 quote); exit 0 after the fix round (see "Fix round") |

Two earlier whole-`tests/i` runs used `INFRX_D_TASK=i8` and failed. Neither failure was in a case
this lane touches.
- First run (`0603abed…`): 44 failed. Another checkout (`codex-w5-known-good-reproof`, pid
  2187889) held `/tmp/infrx-i8-postgres-55450.lock`. The pristine baseline of the mutant list
  therefore failed its pooler/observe cases, and 43 default-subset mutants were `broken_runner`.
  `test_ops_steps`'s PostgreSQL case failed with `HarnessBusy`. Run alone once i8 was free,
  test_observe + test_pooler gave 28 passed and 1 xfailed, and the ops_steps case gave 1 passed.
- Second run (`e324554e…`): 1 failed, `test_ops_login__on_postgresql_the_first_run_sets_both_and_a_rerun_neither`,
  with `HarnessBusy` naming this checkout's own run. With `INFRX_D_TASK=i8`, that case's harness
  wants the same i8 lock that the session-scoped `i8_stack` (tests/i/conftest.py) already holds in
  the same pytest session. This is the suite's own key clash under that env, not a regression. It
  is recorded under open issues. With `INFRX_D_TASK=d10` (the row above) the whole list is green.

`make api-test` (the whole apps/infrx-api) was not run. This lane changes only
`infra/rollout/known-good.json` and `tests/i` files. Every reader of the record is in `tests/i`:
`known-good.py`, `steps/86-cleanup.sh` (through `tests/i/test_ops_steps.py`), `test_known_good_proof.py`
and `tests/i/mutants.py`. On this host every lane's `api-test` shares the i8/w5 keys.

Cases and mutants:

| case | oracle | mutants |
|---|---|---|
| `the_record_proves_both_targets_through_the_lab_migrations_0052` (renamed from `…0051`, extended) | fails when a proof stops short of 0052; when a proof's 0027-0052 hash is not this checkout's bytes; when a target loses its proof; when this re-proof's evidence is not named first; when the through-0051 proof it replaces is not kept first in `superseded`, ahead of the through-0026 one (R224) | `known_good_record_stops_at_0051` (new), `known_good_record_proves_other_0052` (new: one byte in 0052's header comment), `known_good_record_drops_the_0051_proof` (new, R224), `known_good_record_stops_at_0026`, `known_good_record_proves_other_0051`, `known_good_record_proves_other_0027`, `known_good_record_unproven`, `…stops_at_0023/0025` |
| `both_targets_are_known_good_through_0052_and_not_beyond` (renamed from `…0051…`) | each real entry, judged on a stand-in target commit that carries this checkout's real 0019-0052, is KNOWN-GOOD at 0024/0026/0027/0051/0052 and NOT-KNOWN-GOOD at 0053, where only `migrations` fails | `known_good_record_stops_at_0051` (new), `known_good_record_proves_other_0052` (new), plus the earlier `…stops_at_*` / `…proves_other_*` |
| `the_record_proves_both_targets_on_the_candidate_schema` (unchanged) | the record states the driver's SHAPE count | `schema_proof_drops_the_registry_shape_case`, `schema_proof_drops_a_0025_shape_case`, `known_good_record_proves_other_0052` (it also names this case) |

## Wiring requests (text for the coordinator; not applied)

- **WR-KGR2-1** `infra/rollout/README.md`:
  - RR row, line 55. Replace the first line below with the second (fenced so the quoted README link stays literal in this file):

```
carry a `schema_proof` through 0051 (0027-0051 are the Lab migrations at 72dc76ad; KNOWN-GOOD-REPROOF, plain PostgreSQL and the Supabase image; see the paragraph below), so the [known-good rollback](../runbooks/rollback.md#known-good-rollback-drill) has a target up to 0051; beyond 0051 none qualifies
carry a `schema_proof` through 0052 (0027-0052 are the Lab migrations at e9e32e0e; KNOWN-GOOD-REPROOF-2, plain PostgreSQL and the Supabase image; see the paragraph below), so the [known-good rollback](../runbooks/rollback.md#known-good-rollback-drill) has a target up to 0052; beyond 0052 none qualifies
```

  - Paragraph, lines 61-72:
    - "carry `through: 0051`" -> "carry `through: 0052`";
    - "0027-0051 the Lab migrations at 72dc76ad; KNOWN-GOOD-PROOF-2/3 and KNOWN-GOOD-REPROOF reran" -> "0027-0052 the Lab migrations at e9e32e0e; KNOWN-GOOD-PROOF-2/3 and KNOWN-GOOD-REPROOF(-2) reran";
    - "the sha256 of 0019-0051 (`files`)" -> "the sha256 of 0019-0052 (`files`)";
    - "Not proven: any migration after 0051, or a\n0022-0051 other than those bytes" -> "Not proven: any migration after 0052, or a\n0022-0052 other than those bytes".
  - Verification log line to append: "- 2026-09-29 (KNOWN-GOOD-REPROOF-2, WR-KGR2-1): both targets proven through 0052 on plain PostgreSQL and the Supabase image with the driver at fca3ea38, unchanged (SHAPE 14, 26/26 suites, 383 passed); the through-0051 proof is kept in `superseded` (R224); evidence `research/plan/evidence/i/KNOWN-GOOD-REPROOF-2-68ba65f.md`; the row and the paragraph say 0052 and `files` 0019-0052. Task-local only, doc only; hosted stays at 0051 until the next R151 window."
  - Test: `known-good.py <bda1586|4226315 full sha> --applied 0052` exits 0; `--applied 0053` exits 1
    (above). `grep -c 0051 infra/rollout/README.md` falls by the replaced mentions; the log lines
    keep theirs.
- **WR-KGR2-2** `research/plan/15-pending-inputs.md`:
  - P-25 row, line 169. Replace "(I8-20260925T0002Z.json:27-28; KNOWN-GOOD-REPROOF-fca3ea3: schema_proof reaches 0051 for both targets, plain PostgreSQL and the Supabase image; extend at 0052)" with "(I8-20260925T0002Z.json:27-28; KNOWN-GOOD-REPROOF-2-68ba65f: schema_proof reaches 0052 for both targets, plain PostgreSQL and the Supabase image; the through-0051 proof (KNOWN-GOOD-REPROOF-fca3ea3) kept in `superseded`; extend at 0053)".
  - Log line to append: "- 2026-09-29: P-25 known-good: schema_proof reaches 0052 for bda1586 and 4226315 on plain PostgreSQL and the Supabase image (KNOWN-GOOD-REPROOF-2-68ba65f; merged on <merge lane>); `known-good.py <t> --applied 0052` exits 0, `--applied 0053` exits 1. R151 condition 1 for the window that applies 0052 is met; the window still needs a new `EXPECTED_PENDING` and the operator window (conditions 2-3)."
- **WR-KGR2-3** (coordinator, R151 bookkeeping): condition 1 for applying 0052 hosted is met when
  this lane merges. Until then, `known-good.py --applied 0052` on the tip refuses both targets, so
  0052 must not be applied hosted before this merge. `hosted-migrate.sh`'s `EXPECTED_PENDING` is
  outside this lane. WR-KGP3-3 still holds: the R147 follow-up that refuses the lease-less
  `put_result` breaks both targets.

## Proposed ruling (the coordinator numbers it)

None new. R224 was applied as written. Optional clarification: `superseded` is ordered newest
first, and each re-proof prepends the proof it replaces.

## Not proven

1-5 are as in KNOWN-GOOD-REPROOF-fca3ea3 (the `infrx_runtime` login; hosted pre-window rows,
including Lab rows; a browser key INSERT by an unverified owner; the targets' unfenced lease-less
`put_result`; no catalog grant diff, here 0051 -> 0052 was not rerun as a catalog diff, and the
suites and probe carry the proof).
6. A migration after 0052, or 0019-0052 bytes other than these: rerun, then append a new `through`
   and `files` (R224).
7. The Lab's own 0052 paths (reject/operator). They are not the old runtime's SQL, and their proof
   is lab-control-2's `tests/l`/`l3sql`.

## Open issues

- **Host ephemeral ports overlap the task-local registry.** `ip_local_port_range` is 32768-60999,
  so another process's source port can take a registry port and make a rig's
  `docker run -p 127.0.0.1:<port>` fail with "address already in use". Two of six proof runs here
  hit it. Fix options, for the coordinator: reserve the registry blocks with
  `net.ipv4.ip_local_reserved_ports` on the host, or move them below 32768. Both are outside this
  lane.
- `tests/i` under `INFRX_D_TASK=i8`: `test_ops_steps`'s PostgreSQL case and the session `i8_stack`
  want the same i8 lock, so the whole list is green only with another PG key (e.g. `d10`). This
  lane does not own that suite.

## Estimate

Lane: 0 h remaining. Coordinator: WR-KGR2-1/2 text patches take 0.1/0.2/0.4 h
(optimistic/likely/pessimistic). Confidence is high. Basis: WR-KGR-1/2 were the same patches one
migration earlier.

## Fix round (0-KGR2-RV-1, 1-KGR2-RV-1)

Finding: the WR-KGR2-1 RR-row quote carried the README's live link
`../runbooks/rollback.md#known-good-rollback-drill`. That link is relative to `infra/rollout/`, so
from this file it is broken. `validate_plan.py` strips only column-0 fenced blocks before it checks
links. It exited 1 at `62b9adab` and `6d8f7476`, while the checks table and the update JSON said exit 0.
The run behind that claim was made at `a71f0029`, before the WR text existed.

- Red: `python3 research/plan/scripts/validate_plan.py` at `6d8f7476` printed
  `ERROR: Broken link in research/plan/evidence/i/KNOWN-GOOD-REPROOF-2-68ba65f.md: ../runbooks/rollback.md`
  twice, exit 1. A first attempt put the fence inside the list item, indented. It stayed red because
  the stripper matches only a fence at column 0.
- Fix: the two quoted strings (old, new) now sit in a column-0 fenced block, byte for byte the
  same text. The checks-table row for validate_plan now records the real results.
- Green: `validate_plan.py` exit 0 (PASS, 953 links across 400 documents);
  `INFRX_D_TASK=d10 pytest -q tests/i/test_known_good_proof.py` 8 passed;
  `INFRX_D_TASK=d10 INFRX_MUTANTS=all pytest -q -rs tests/i/test_mutants.py -k "known_good or schema_proof"`
  29 passed (29/29 killed), 167 s. No code, test, mutant or record changed, only this file and the update JSON.
