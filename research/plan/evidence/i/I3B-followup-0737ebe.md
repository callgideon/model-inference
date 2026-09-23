# I3B follow-up — recovery-gate fixes requested by E3B phase 2 (R92, i3bm57, rc10, pending ids)

## Task and status

- **Task:** the I3B follow-up lane: E3B phase 2 integration request #2 (`E3B-e57b820.md` on
  `codex/e3b-phase2-gate`), the E3B2 review's findings lens (`F1-bk01-acl-misdiagnosis`,
  `F5-rc10-body-unspecified`) and rulings **R92** (restore comparison normalises ACLs) and
  **R83** (one runner, pristine baseline, assertion-shaped deaths).
- **Status: implemented.** Not integrated. Local only: no hosted project, no AWS, no pilot box,
  no GPU. Docker was used only for the D harness task **d3** (`infrx-d3-postgres[-supabase]`,
  127.0.0.1:55434, removed at exit) and the restore tool's throwaway `infrx-i3b-pgclient-*`
  client containers (`--rm`). E2's integration stack was **not** started.
- **Merge order:** after E3B2. Item 4 uses E3B2's `stack.stubbed()` and relies on E3B2's
  `OWNED_TREES += infra` (a3722b0); both are verified below on a scratch merge.
- **Owner/session:** Claude Opus 5.5 implementation session, worktree
  `.claude/worktrees/codex-i3bf`.

## Source

| Field | Value |
|---|---|
| Base SHA | `1a26137` (`claude/backend-impl`, the integration head; E3B2 not merged) |
| Implementation SHA | **`0737ebe`** (this report's commit is on top) |
| Branch / worktree | `codex/i3b-followup` in `.claude/worktrees/codex-i3bf` |
| Verified against | a scratch merge of this branch with `codex/e3b-phase2-gate@8fb3ea2` (E3B2's fix round in progress), local, detached, removed afterwards |
| Integrated SHA | none (coordinator) |

## Items

| Item | Commit | Change | Case (dies at) | Mutant → result |
|---|---|---|---|---|
| 1 R92 | `33a95d8` | `pgrestore.CATALOG` compares `coalesce(relacl, acldefault(case relkind when 'S' then 's' else 'r' end::"char", relowner))`, `coalesce(proacl, acldefault('f', proowner))`, `coalesce(nspacl, acldefault('n', nspowner))`. `test_restore` runs on the D harness with `INFRX_I3B_PG=d` (its task database built like `infrx_e2`: every migration, the test clock, E's seed). restore.md documents the rule | `bk01_a` (test_restore.py:255); new **`bk01g`** (NULL vs spelled-out owner default for a schema, table, sequence and function is equal; a revoke-to-empty ACL is still named; :479/:483); new **`bk01h`** (restore of a migrated, D-seeded DB: `job_results` relacl `{postgres=arwdDxtm/postgres}` on the source and NULL on the target, the check equal, `put_result`/`read_result` work as service_role, anon has no execute, service_role cannot read the table; :514) | **i3bm86** relation normalisation dropped → bk01_a killed; **i3bm87** function, **i3bm88** schema, **i3bm89** sequence kind → bk01g killed; **i3bm90** revoke-to-empty treated as default → bk01g killed; **i3bm91** restore drops every ACL TOC entry → bk01h killed |
| 2 | `585238b` | i3bm57 anchored on `loop.py`'s `from __future__ import annotations` (W3's 65e2c99 changed the old last-line anchor) | `rc08b` (test_recovery.py:575, `Failed`) | **i3bm57** killed |
| 3 rc10 | `fa71117` | The rollout-rollback drill body (spec below). rollback.md step 4 names `sudo ./apps/infrx-api/deploy/rollback.sh "$BACKUP"` and what it does; new **`rb08`** pins that to rollback.sh's own usage line | `rc10` (test_recovery.py:777); `rb08` (test_runbooks.py:73) | **i3bm92** (the rebuild reads the queued snapshot) → rc10 killed; **i3bm93** (step 4's command changed) → rb08 killed |
| 4 | `6cacdea`, `2b6110c` | rc03 → `PENDING[G2]`; rc04 → `kit.needs_stack()` + `stack.stubbed(("admit", "claim", "terminalize"))`, pending on each stub's owner, `pytest.fail` when none is a stub; rc05b → `M1-L2`, rc08b → `I2B-R4` (`recoverykit.OWNERS`: the two ids that are not tasks); `recoverykit.PENDING` drops `I2B`; the kit's skip names `harness.PROJECT`; test_restore docstrings name `harness.PG_DATABASE`; `_copy_with_infra` and its monkeypatch deleted | layer 0 green (below); E3B2's `test_stage` green on the merge with the diff in request 1 | existing i3bm39/i3bm57 still kill rc05b/rc08b |
| 5 | `09652eb`, `0737ebe` | R83 death shape for three pre-existing mutants found by the runs: `bk01e_c` fails by assertion once `refuse_live_target` gets past the identity check (was `OperationalError` on 127.0.0.1:5432); `rb03` asserts the `set role` line exists before ordering it (was `ValueError`); `pgrestore.run` decodes client output with `errors="replace"` (a damaged archive made pg_restore print non-UTF-8: `UnicodeDecodeError`) | bk01e_c (:384), rb03 (:60), bk01d (:306) | **i3bm80–83**, **i3bm56**, **i3bm44** killed by `AssertionError` |

### rc10 — what the drill is, and what is stubbed

`test_i3b_rc10_a_rollout_rollback_loses_no_job_and_restores_the_previous_runtime`, rollback.md
steps 2–7 in order. A host serves pilot at the current release (image `sha256:bbbb…`) with four
accepted jobs over two tenants: one done, one mid-attempt (a hanging engine), two queued. The
previous release is a pilot runtime (image `sha256:aaaa…`), backed up the way install.sh step 3
does it (`tar -cpf files.tar` of the present paths plus `absent`).

1. **Maintenance** (step 2): bk04's `MAINTENANCE` statement as `service_role` on a scratch
   PostgreSQL database with every migration; `infrx.require_feature('credit_admission')`
   refuses with **55000**.
2. **Drain and fence** (step 3): W2's `WorkerLoop.drain()` releases the in-flight attempt
   (`released == 1`, outcome still None: nothing settled).
3. **rollback.sh** (step 4), I2B's script unmodified, in bash, `INFRX_ROOT` = a sandbox root:
   - to the pre-pilot monolith backup: **exit 2**, stderr names `drain.sh pause`, no command
     issued, every file byte-identical;
   - to the previous pilot backup: **exit 0**, and the commands issued are exactly
     `systemctl stop infrx-worker marlin2b-gateway` → `systemctl daemon-reload` →
     `systemctl restart infrx-worker marlin2b-gateway` → `curl … 8001/readyz` →
     `curl … 8002/readyz` → `docker inspect caddy` → `docker exec caddy caddy reload …`;
     the env file (the image pin), all four units and the three edge files equal the previous
     release's byte for byte.
4. **Reap and rebuild** (step 5): after the lease TTL the reaper requeues the released job
   (1 event); `rebuild(snapshot)` == 3.
5. **Resume** (step 6): the statement with `true`; `require_feature` passes.
6. **Reconcile** (step 7): a new WorkerLoop finishes; `reconcile()` = 4 `succeeded/completed`,
   one projection each, debit = price snapshot × usage, no unknown hold.

**Stubbed, precisely:** `systemctl`, `docker` and `curl` (PATH stubs that append their argv to
a log and exit 0, so "readiness back" is the order of the probes, not a live `/readyz`); the
host root (a temporary directory, never `/`); the worker's SIGTERM stop is W2's `drain()` in
process, run before rollback.sh's own `systemctl stop`; the "restored runtime" is a new
in-process `WorkerLoop` started after the stub recorded the restart; the job store is the
contracts' reference fake (as in every rc drill), and the index is the fake scheduler. **Real:**
rollback.sh + lib.sh, `tar` into the sandbox, the pilot→unmetered refusal, W2's drain, the
store's `recover()`, and the maintenance statement on PostgreSQL. E's compose stack has no
systemd units, so rollback.sh cannot drive it; nothing here touches a box.

## Requirement coverage (new or changed cases)

| Case | Oracle | Invariant |
|---|---|---|
| `bk01_a` | OPS-RECOVER | A populated, fully migrated database restores equal, now including `infrx.job_results` (R92) |
| `bk01f[8 families]` | OPS-RECOVER | Unchanged; its `good_restore` fixture no longer errors |
| `bk01g` | OPS-RECOVER (R92) | NULL and the spelled-out owner default are one privilege set for schema/table/sequence/function; `{}` after a self-revoke is a named `relations` loss |
| `bk01h` | OPS-RECOVER, DUR-RLS (R92) | After a restore `job_results` is NULL on the target (premise pinned), the check is equal, `put_result`/`read_result` keep service_role's access and nothing widens |
| `rc10` | OPS-RECOVER | As specified above |
| `rb08` | OPS-RECOVER (runbook) | rollback.md step 4 = rollback.sh's usage line, no placeholder |
| `rc03`/`rc04`/`rc05b`/`rc08b` | honest pending | `G2`; the stub owners (via `stack.stubbed`); `M1-L2`; `I2B-R4` |
| `bk01e_c`, `rb03`, `bk01d` | R83 shape | Their mutants die by assertion |

## Environment

| Item | Value |
|---|---|
| Host | `Linux 7.0.0-1010-aws x86_64`, shared local dev box, classification **local** |
| Python / deps | 3.12.3 (`make api-env`), psycopg 3.3.6, pytest 8.4.2 |
| Docker | 29.6.2 |
| D harness | `INFRX_D_TASK=d3` → 127.0.0.1:55434; `INFRX_D1_IMAGE=supabase` → `supabase/postgres@sha256:7768d0d1…` (PostgreSQL 17.6, E2's pin); unset → `postgres@sha256:33f923b0…` (16.14) + D's shim |
| Restore client | the pinned Supabase 17.6 image (`pgrestore.IMAGE`) |
| Mutation runs | private `TMPDIR` under the session scratch (see Limits 4) |

## Commands and results

UTC 2026-09-23. Env var names only. `PY=apps/infrx-api/.venv/bin/python`.
`D=INFRX_I3B_PG=d INFRX_D_TASK=d3`.

| # | Command | Exit | Result (quoted) |
|---|---|---|---|
| 1 | `$PY -m pytest -q tests/integration/test_harness.py tests/integration/backend/test_stage.py tests/integration/test_run.py tests/integration/backend/recovery` at base `1a26137` | 1 | `2 failed, 88 passed, 25 skipped`: `test_i3b_mutant_list_is_well_formed` (i3bm57), `rc10` |
| 2 | `$D INFRX_D1_IMAGE=supabase $PY -m pytest …/test_restore.py -k bk01_a` before the fix | 1 | `relations differ: missing [('infrx.job_results', 'r', True, False, '{postgres=arwdDxtm/postgres}')], extra [('infrx.job_results', 'r', True, False, '{}')]`: the E3B2 red, reproduced on d3 |
| 3 | probe on d3: a table after `grant all … to postgres` then `revoke all … from postgres` | 0 | `('{}', False, False, 0)`: `relacl = '{}'` is **false** for a revoked-to-empty ACL (1 dimension, 0 items), so i3bm90's first edit (`nullif(relacl, '{}')`) survived and was rewritten with `cardinality` |
| 4 | layer 0 at head `0737ebe`, same files as #1 (12:33:56Z) | **0** | `90 passed, 28 skipped`; PENDING `[G2]`, `[M1-L2]`, `[I2B-R4]`; the rest are "no infrx-e2 stack" skips |
| 5 | `$PY -m pytest -q tests/integration` at head, no stack (12:33:23Z) | **0** | `124 passed, 81 skipped`. The first attempt (12:32Z) had `2 failed`: `test_busy_ports_finds_a_listener_on_a_task_local_port` and `test_the_server_log_is_never_left_behind…`, both `[Errno 98] Address already in use` on E2's 55580/55586 (ephemeral-port collision, P-21); nothing listened there when rerun |
| 6 | `$D INFRX_D1_IMAGE=supabase $PY -m pytest -rs -v tests/integration/backend/recovery` at head (12:35:29Z) | **0** | `52 passed, 9 skipped`: **bk01_a PASSED, bk01f × 8 PASSED**, bk01g, bk01h, rc10, rb08 PASSED; skips = 3 PENDING + 6 needing E2's stack (rc04, rc06, rc07, rc08, rc09, bk03). The first attempt (12:34:18Z) could not bind 55434: a TIME-WAIT socket `127.0.0.1:55434 → 127.0.0.1:55432` (another lane's D1 connection took it as an ephemeral port); my two `Created` containers were removed and the run repeated |
| 7 | `$D $PY -m pytest -rs -v …/test_restore.py` on the **plain** image at head (12:37:26Z) | 1 | `5 failed, 10 passed, 1 skipped, 8 errors`: every restore-dependent case (bk01_a/b/c, bk01f × 8, bk01h) stops at `pg_restore: error: … unrecognized configuration parameter "transaction_timeout"` (the 17.6 client into a 16.14 server), bk02 at `column "confirmed_at" of relation "users" does not exist` (the shim). bk01d/e_a/e_b/e_c/g and bk04 pass. **The fixture does not allow the plain image** |
| 8 | `$D INFRX_D1_IMAGE=supabase TMPDIR=<scratch> $PY tests/integration/backend/recovery/mutants_i3b.py --layer all` at `fa71117` (standalone, E's runner without a baseline, `_copy_with_infra` still present) | 1 | `{"mutants": 86, "killed": 84, "controls_survived": 1, "not_killed": 1, "problems": ["i3bm33"]}`; i3bm33 `no-cases` (rc06 needs E2's Valkey). Its deaths flagged item 5: i3bm80–83 `OperationalError`, i3bm56 `ValueError` |
| 9 | the same list at `6cacdea` on this branch alone, `--only i3bm93` | 1 | `FileNotFoundError: …/infrx-e2-i3bm93-…/infra/runbooks/rollback.md`: without E3B2's `OWNED_TREES += infra` the copy lacks `infra/` (the merge-order dependency) |
| 10 | scratch merge `09652eb` + `8fb3ea2`: layer 0 as #1 | 1 | `1 failed, 104 passed, 28 skipped, 1 xfailed`: E3B2's `test_no_pending_id_names_a_merged_task…` → `{'I2B-R4', 'M1-L2'}` not in tasks.json |
| 11 | the same, plus `2b6110c`, `0737ebe` and the diff in request 1 (12:37:54Z) | **0** | `105 passed, 28 skipped, 1 xfailed` |
| 12 | scratch merge, `$D INFRX_D1_IMAGE=supabase TMPDIR=<scratch> $PY …/mutants_i3b.py --layer all` (E3B2's runner: R83 pristine baseline) — first run | 1 | `{"mutants": 86, "killed": 60, …, "not_killed": 25}`: i3bm33 `no-cases` and **24 `baseline-red`** (every D-mode layer-2 selector, each baseline `1 failed … in ~1.0s`). Not reproduced: see #13, #14 |
| 13 | the 24 baseline-red ids again (`--only` ×24) | **0** | `{"mutants": 24, "killed": 24, "not_killed": 0, "problems": null}`, every pristine baseline green |
| 14 | **the full list again on the merge (12:20:01Z)** | 1 | `{"mutants": 86, "killed": 84, "controls_survived": 1, "not_killed": 1, "pending": 0, "problems": ["i3bm33"]}`; no baseline-red. Death sites: 73 `AssertionError`, 7 `Failed`, 1 `RuntimeError` (i3bm48, see Limits 3), 1 `UnicodeDecodeError` (i3bm44, fixed in `0737ebe`) |
| 15 | after `0737ebe` on the merge: `--only i3bm44 i3bm55 i3bm58 i3bm59` | **0** | all four killed by `AssertionError` (bk01d test_restore.py:306; rb06/rb07) |
| 16 | `cd apps/infrx-api && .venv/bin/python -m pytest -q -rs tests/i` (12:05:26Z) | **0** | `143 passed in 196.23s` |

Every I3B mutant's named case is green on the pristine tree after item 1 (#6 on d3, #13/#14 by
the runner's own baseline) **except** those E2's stack alone can run: `rc06` (i3bm33) is not
exercised here; it was green in E3B2's gate at `e57b820` and this lane did not change it.

## Failure drill

| Injection | Durable state before → after | Verdict |
|---|---|---|
| bk01 dump/restore on d3 (Supabase 17.6) | source `job_results` ACL explicit owner-only → target NULL | equal after R92; RPCs keep access, nothing widens (bk01h) |
| rc10: maintenance, drain, rollback.sh ×2, reap, rebuild, resume | 4 accepted (1 done, 1 in flight, 2 queued) → 4 succeeded once each; host files current release → previous release | no job lost, no double settlement; the refused rollback changed nothing |
| live R92 probe (#3) | throwaway `infrx_i3b_probe` in d3 | container removed at exit |

Cleanup: d3 containers are removed at process exit by `pgharness`; the two left `Created` by the
bind failure in #6 carried this checkout's label and were removed by hand. At the end
`docker ps -a` lists no `*d3*` or `*i3b*` container. The scratch-merge worktree was removed.

## Artifacts

Session scratch (not committed), sha256 prefix: `m-all-fa71117.json da8a800ca68cc236` (#8),
`m-all-merge.json 8842645c62ee7281` (#12), `m-l2-merge.json 96f6d5daafb9ebb4` (#13),
**`m-all-merge2.json 75343367fcebfa92`** (#14), `m-44.json f0defb3e3808a759` (#15),
`m-r92.json 54ef868f3817961d`, `m-r92b.json 6843f4d5696b280d`, `m-r92c.json f638432b3aed123d`,
`m-rc10.json 592a3da16beef0b0`, `m57.json 1e38faf040584eae`, `recovery-d3-head.log
8cea0e37e1d3ec47` (#6), `restore-d3-plain-head.log 228675ca4ce347de` (#7), `l0-head2.log
44bdbee313263193` (#4), `l0-all-head.log 5196d983ce4f4aa7`, `l0-all-head2.log b429e1b861c724e2`
(#5), `tests-i.log 3e7702374029563b` (#16), `e3b2-merge-request.diff b4bf5cd5cef83668`.

## Changes

`git diff --stat 1a26137..0737ebe`: 8 files, +486/−87: `infra/runbooks/{pgrestore.py, restore.md, rollback.md}` and
`tests/integration/backend/recovery/{mutants_i3b.py, recoverykit.py, test_recovery.py,
test_restore.py, test_runbooks.py}` — all I3B-owned. No module code, contract, migration,
composition root, deploy script, Makefile, run.py or E3B file changed. **Migrations: none.**
Rolling back means reverting the commits. New mutants: i3bm86–i3bm93 (86 in the list).

## Limits

1. **E2's stack was not run** (as instructed): rc04, rc06, rc07, rc08, rc09 and bk03 were not
   exercised; rc04's new `stack.stubbed` path was verified only through E3B2's layer-1 stage test
   on the merge. On the gate stack it is expected to pend on `D5` (terminalize's stub).
2. **Plain D image:** not a supported restore target (#7): the restore client is hosted's 17.6.
3. **i3bm48** still dies by the tool's own `RuntimeError("refusing to fingerprint: the session
   is not read-only")` — the invariant's reason, but raised in `pgrestore.py`, not an assertion;
   E's runner has no `dies_by` to declare it.
4. **Runner hazard (measured):** with the default `TMPDIR`, E's runner's litter sweep
   (`{PROJECT}-*` in `/tmp`) removed this lane's live mutant copy while another lane's runner
   ran: i3bm57's first run was `setup-error` (`FileNotFoundError: /tmp/infrx-e2-i3bm57-…`), and
   the same sweep from this lane could delete another lane's copy. Every mutant run here used a
   private `TMPDIR`.
5. **#12's 24 baseline-red** were not reproduced in two later runs (#13, #14); the cause was
   not identified (each baseline failed in ~1 s, consistent with the D container failing to
   start; #6 measured one such bind failure on 55434).
6. Ephemeral-port collisions on task ports (P-21) hit this lane twice (#5, #6).

## Integration requests

1. **E3B2 / coordinator, at the E3B2 + I3B-followup merge (RESIDUAL/PENDING lines).** No I3B case
   names a merged task any more, so `RESIDUAL` empties; the two owner ids are admitted. Verified
   green on the scratch merge (#11). Exact diff:
   ```diff
   --- a/tests/integration/backend/stack.py
   @@ PENDING = {
   -    # RESIDUAL (merged; I3B's cases only - see RESIDUAL)
   -    "G1R": …, "D2": …, "D3": …, "M3": …, "W3": …,
   -}
   -RESIDUAL = { "G1R": …, "D2": …, "D3": …, "M3": …, "W3": …, "I2B": … }
   +}
   +# Merged tasks still in the vocabulary, and why. None since I3B's follow-up: its recovery
   +# cases name G2, the stub owners (stubbed()) and recoverykit.OWNERS.
   +RESIDUAL: dict[str, str] = {}
   --- a/tests/integration/backend/test_stage.py
   -  <skipped message="PENDING[D2] no adapter"/>          +  PENDING[D4]
   -  <skipped message="Skipped: PENDING[G1R,G2] not mounted"/>   +  PENDING[G3,G2]
   -    assert cases["pending"] == {"D2": [...], "G1R": [...], "G2": [...]}   (+ D4, G3)
   -    assert summary["pending_by_id"] == {"D2": 1, "G1R": 1, "G2": 1}      (+ D4, G3)
   -    assert set(vocabulary) <= set(tasks), set(vocabulary) - set(tasks)
   +    # I3B's two owner references (recoverykit.OWNERS) are the only non-task ids
   +    assert set(vocabulary) - set(recoverykit.OWNERS) <= set(tasks), \
   +        set(vocabulary) - set(recoverykit.OWNERS) - set(tasks)
   -    merged = {task for task in vocabulary if tasks[task] in ("implemented", "integrated")}
   +    merged = {task for task in vocabulary if tasks.get(task) in ("implemented", "integrated")}
   ```
   (full text: `e3b2-merge-request.diff`, 2 files, +12/−27). Consequences for E3B2: **e3bm16
   survives** an empty RESIDUAL (measured: with its edit, `-k merged_task` → `2 passed`), so its
   refusal case needs a synthetic RESIDUAL entry; dr17's fallback `stack.pending("G1R", …)` now
   names an unknown id (it is refused today as a RESIDUAL id either way). E3B's evidence line
   140 and IR #2(c) should drop "not even the owner's", "would lose access" and "fix the
   restore/D's grant" (R92; bk01h measures the opposite). I3B's pendings after the merge: `G2`
   (rc03), the stub owners (rc04; `D5` expected), `M1-L2` (rc05b), `I2B-R4` (rc08b).
2. **Merge order:** merge this branch after E3B2 (`stack.stubbed`, `OWNED_TREES += infra`,
   `all_mutants()`); alone on `1a26137`, infra mutants cannot run (#9).
3. **Coordinator:** `I2B-R4` (the worker composition root `python -m infrx.worker`) and `M1-L2`
   (an S3 ObjectStore) are owned by no task in tasks.json; register them, or keep them as the
   documented owner references in `recoverykit.OWNERS`.
4. **E (runner):** limit `_temp_litter`'s sweep to the copy this run made, or run each list under
   a private `TMPDIR` (Limits 4).
5. **I2B:** none. rollback.sh needed no change; rc10 adds a pilot→pilot rollback (both `/readyz`
   probes) that `tests/i` does not have.

## Handback

- **Next unblocked:** the E3B2 merge (with request 1) makes I3B's recovery cases green or
  honestly pending on the gate; the gate's remaining I3B reds (bk01_a, 8 bk01f errors, rc10,
  the mutant list, i3bm60–66 `no-cases`) are closed here on the D harness and on the merge.
- **Box/hosted:** unchanged coordinator operations (the hosted backup rehearsal; rollback on the
  box is still ⚠️ P-18).

## Verification log

- 2026-09-23: Authored from the runs above; every count is quoted from command output or the
  JSON reports. Status **implemented, not integrated**. Nothing deployed; E2's stack not started.
