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
