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

## Round 2 — review fix round (review of `d99da09`)

The review (`research/plan/evidence/i/I3B-followup-review-d99da09.json` on `claude/backend-impl`,
drill and restore lenses, two refuters per blocking finding) returned **fix_required**: three
blocking findings (DR-1, RST-1, RST-4) and eight nonblocking ones. This round fixes all three
blocking findings and folds in DR-2 (as an integration request, because the file belongs to E),
DR-3, DR-4, RST-2 and RST-3. DR-5 and RST-5 are left, with the reasons given below. DR-6 needed
no change.

| Field | Value |
|---|---|
| Base | `d99da09` (round 1, pushed) |
| Round-2 head | `946e916` code (`7c2cb26` + a reflow of rollback.md); this section is committed on top of it |
| Verified against | a scratch clone (`git clone --shared`, removed afterwards) of `d99da09` merged with **`origin/codex/e3b-phase2-gate` @ `c587ddc`** (E3B2's WIP save). The merge was clean, so the `5e9bbf1` fallback was not needed. On top of it: request 1 (updated, see IR R2-1), IR R2-A, IR R2-B, and then the round-2 commits cherry-picked one at a time |
| Cross-check | this branch's head merged with E3B2's **local, unpushed** head `f97bfb7` (IRs R2-A/R2-B plus the `stack.py` half of request 1), at layer 0 only |

### Findings → fixes

| Finding | Commit | Change | Case (dies at) | Declared mutant → kill (quoted from E's runner on the merge) |
|---|---|---|---|---|
| **DR-1** (blocking) rc10's edge "byte for byte" was vacuous | `5e6f26d`, docs `7c2cb26` | `release()` marks both infrx sites with `# <image>`. The active Caddyfile copies the site, so it is marked too. rc10 now pins the premise that no backed-up file is equal across the two releases. `DEPLOY` is the copy's (`kit.ROOT`), so the runner's edit is the script that rc10 runs | `rc10`, `on_host(root) == release(IMAGE["previous"])` (test_recovery.py:815) | **i3bm94** rollback.sh `tar … -xpf files.tar` → `… --exclude=etc/caddy` (the reviewer's rv1): `killed`, `test_recovery.py:815: AssertionError`, `1 failed, 12 deselected in 7.70s` (layer 2, d3, R7) |
| **RST-1** (blocking) the schemas ACL family could not be killed | `6b43e13` | bk01f `DAMAGE["schemas"] = "grant usage on schema infrx to anon"` (0004 revokes that grant) | `bk01f[schemas]`, the family assertion (test_restore.py:464) | **i3bm95** `coalesce(nspacl, …)` → `coalesce(null, …)` (M5): `killed`, `test_restore.py:464: AssertionError` |
| **RST-4** (blocking) the sequence family was empty | `47d1489` | **Chosen: keep the branch and test it.** A sequence is one `serial` or identity column away, and removing the branch would reopen the gap silently. bk01g grants `usage on sequence infrx.s to anon`, asserts exactly `relations` naming `'infrx.s'`, and puts the grant back before the existing revoke-to-empty check. restore.md now says the migrated schema has no sequence today: grep of 0001-0017 finds no `serial`/`bigserial`/identity/`create sequence`/`nextval` | `bk01g` (test_restore.py:502) | **i3bm96** `relkind in ('r','p','v','m','S')` → without `'S'` (M6): `killed`, `test_restore.py:502: AssertionError` |
| DR-2 D-mode lock defeated by the runner's private TMPDIR | none on this branch (E's `run_one`) | **IR R2-B** (diff below). Measured on the scratch merge with `$TMPDIR/infrx-d3-postgres-55434.lock` held by `flock`: **without** the IR, `--only i3bm87` → `killed`, because the child took its own lock in a private TMPDIR and ran over the held port. **With** the IR → `baseline-red` (refused, 1 failed in 0.73 s). A plain `pytest -k bk01g` under the same TMPDIR shows the refusal: `HarnessBusy: another run holds …/infrx-d3-postgres-55434.lock` | - | - |
| DR-3 rc10 checked readiness only by probe order | `8a8223e` | The stub fails a call that names `$INFRX_I3B_FAIL`. New **rc10b[8001, 8002]** (layer 1, no database) checks five things: exit 4, `the restored runtime is not ready`, the argv sequence stop/daemon-reload/restart(/8001 ok), only retries of the failing probe, and **no `docker` call** (the edge is never reloaded). It also checks that the files on disk are already the previous release's, because the restore runs before the readiness wait. rollback.md step 4 says so | `rc10b` exit-code assertion (test_recovery.py:849) | **i3bm97** `wait_ready … \|\| true` (rv3): `killed`, `test_recovery.py:849: AssertionError`. **i3bm98** lib.sh `&& wait_http "$WORKER_READY"` → `;` (rv2): `killed`, `:849: AssertionError` ([8001]) |
| DR-4 pending ids were not pinned | `e3577b3` | New **rc00** (layer 0) checks that `kit.pending("NOPE")` and `kit.pending()` are refused, that rc03/rc05b/rc08b skip as exactly `PENDING[G2]`/`[M1-L2]`/`[I2B-R4]`, and that rc03 fails once `stack.ingress_is_mounted` is True. rc04 is not pinned, because its ids come from `stack.stubbed` on E2's stack | rc00 (:600/:607/:609) | **i3bm99** `unknown = []` (pk1): `:600: AssertionError`. **i3bm100** rc05b `"M1-L2"` → `"G2"` (pk4): `:607: AssertionError`. **i3bm101** rc03 `if False:` (pk2): `:609: AssertionError` |
| RST-2 the plain image was not refused | `2d5df9f` | `needs_pg` skips first with a typed reason, `UNSUPPORTED[plain D image] INFRX_I3B_PG=d needs INFRX_D1_IMAGE=supabase: the restore client (pgrestore.IMAGE) is PostgreSQL 17.6 and sends SET transaction_timeout, which D's plain 16.14 server rejects, and D's shim has no auth.users.confirmed_at`, before any container starts. The plain branches of `_admin`/`_create` are deleted. On the plain image, `test_restore.py` now gives `6 passed, 20 skipped in 0.75s`, exit 0, and no container (it was `5 failed, 10 passed, 1 skipped, 8 errors`). This is a skip, as the brief asks, not the reviewer's `fail`. A mutant run on the plain image reports `no-cases`, never a kill | **bk00** (layer 0, a stand-in harness reporting the plain image) | **i3bm102** refusal deleted: `killed`, `test_restore.py:122: Failed` (`DID NOT RAISE <class 'Skipped'>`) |
| RST-3 column ACLs were not compared | `05112f5` | New CATALOG family `column_acls` (schema.table, column, sorted `attacl`). A column has no owner default, so NULL means no grant. `DAMAGE["column_acls"]` revokes `update (full_name)` on `public.profiles` from authenticated (0001's grant). bk01_a, bk01b/c/h and the other families stayed green on d3/Supabase before the commit. restore.md A6 lists the family | `bk01f[column_acls]` (test_restore.py:464) | **i3bm103** family dropped: `killed`, `test_restore.py:464: AssertionError` |
| DR-5 rc04 pends the whole drill on D5 | **left** | Its admit/claim half needs a PostgreSQL restart under `PgJobStore`, which is E3B2's `stack.pg_jobstore()` on E2's compose stack. This lane may not start that stack, and a layer-3 drill that was never run is the "implemented, not exercised" shape the reviews reject. Deliberately deferred: rc04 pends on `stack.stubbed(("admit","claim","terminalize"))` (D5 expected), as before | - | - |
| DR-6 | none | Documented merge order (E3B2 first) | - | - |
| RST-5 owner not compared | **left** (follow-up) | The blind spot is an object whose ACL is `'{}'` whose owner changes. No project object has `'{}'`. Adding `*owner::regrole` columns to three families changes every row shape and gives no measured gain today | - | - |

Death shapes of the 10 new mutants: 9 `AssertionError` and 1 pytest `Failed` (`DID NOT RAISE`,
i3bm102). None is a crash.

### Round-2 runs

UTC 2026-09-23. `PY=apps/infrx-api/.venv/bin/python`. `D=INFRX_I3B_PG=d INFRX_D_TASK=d3
INFRX_D1_IMAGE=supabase INFRX_D2_VALKEY_PORT=55464 INFRX_D2_VALKEY_CONTAINER=infrx-d3-valkey`.
Mutant runs used a private `TMPDIR` under the session scratch.

| # | Command | Where | Exit | Result (quoted) |
|---|---|---|---|---|
| R1 | `$PY -m pytest -q -rs tests/integration/test_harness.py tests/integration/backend/test_stage.py tests/integration/test_run.py tests/integration/backend/recovery` (15:02:45Z) | this branch at `7c2cb26` (`946e916` only reflows prose) | **0** | `94 passed, 30 skipped, 2 warnings in 13.55s`; PENDING `[G2]`, `[M1-L2]`, `[I2B-R4]` once each (round 1: 90/28; +rc00, rc10b×2, bk00; +2 skips bk01f[schemas]/[column_acls] need PostgreSQL) |
| R2 | same (15:04:05Z) | scratch merge | **0** | `110 passed, 30 skipped, 1 xfailed, 2 warnings in 14.04s`. The first attempt with request 1 as written gave `3 failed, 107 passed`: test_stage's stand-in `PENDING[D4]` is stale because D4 has merged since (see IR R2-1). The stand-in became `D5` |
| R3 | same | branch head + E3B2 local `f97bfb7` + IRs | 1 → **0** | `1 failed, 109 passed`: `test_no_pending_id_names_a_merged_task…` → `({'D2','D3','G1R','M3','W3'}, {… 'I2B' …})`. E3B2's RESIDUAL still lists I2B, which I3B no longer names. With request 1's `stack.py` half applied: `110 passed, 30 skipped, 1 xfailed` |
| R4 | `$D $PY -m pytest -rs -v tests/integration/backend/recovery` (15:04:28Z) | scratch merge | **0** | `58 passed, 9 skipped, 2 warnings in 52.25s` (round 1: 52/9). test_restore.py: 26 passed, 1 skipped (bk03). rc00, rc10, rc10b×2, bk00, bk01_a, bk01f × 10 (incl. schemas, column_acls), bk01g, bk01h, rb08 PASSED. Skips: 3 PENDING + 6 need E2's stack. `docker ps -a` afterwards: no d3/i3b container |
| R5 | `INFRX_I3B_PG=d INFRX_D_TASK=d3 $PY -m pytest -q -rs …/test_restore.py` on the **plain** image | this branch | **0** | `6 passed, 20 skipped in 0.75s`; skip reason `UNSUPPORTED[plain D image] …` (RST-2); no container started |
| R6 | `$D TMPDIR=<private> $PY tests/integration/backend/recovery/mutants_i3b.py --layer all --report m-all.json` (15:05:44Z-15:10:5xZ) | scratch merge | 1 | `{"mutants": 96, "killed": 75, "controls_survived": 1, "not_killed": 20, "pending": 0}`. Problems: i3bm33 `no-cases` (rc06 needs E2's Valkey, as in round 1) and **19 `baseline-red`**: every baseline of rc10, bk01_a, bk01c, bk01d, bk01e_a/b and bk01f[policies…functions_config] failed in 1.0-1.24 s. The cause was found (R8) and it is not the code. Every other D-mode mutant was killed: bk01g's i3bm87-90/96, bk01f[schemas]/[column_acls] i3bm95/103, bk01h i3bm91, bk04 i3bm43 |
| R7 | the 19 again, `--only` ×19 | scratch merge | 1, then **0** | the first retry (15:12:30Z) was poisoned by my own R8 reproduction: all 19 failed again in 20 s while that experiment's `TIME-WAIT` held the port. The retry after the port was free (15:13:34Z): **exit 0, `{"mutants": 19, "killed": 19, "not_killed": 0, "problems": null}`** (15:13:34Z-15:20:24Z). Death sites: 17 `AssertionError`, 1 `Failed`, and 1 `RuntimeError` (i3bm48 at pgrestore.py:299, the tool's own read-only guard, as in round 1). i3bm94 `test_recovery.py:815: AssertionError`, i3bm92 `:819`, i3bm44 `test_restore.py:319`, i3bm86 `:268`. Together with R6, all **94 non-control mutants except i3bm33** are killed, the control survives and i3bm33 is `no-cases`: 96 = the declared list |
| R8 | diagnosis: a client socket bound to `127.0.0.1:55434` (connected to a local listener) held while `pytest -k bk01d` ran on d3 | scratch merge | 1 | `RuntimeError: could not start infrx-d3-postgres-supabase: … failed to bind host port 127.0.0.1:55434/tcp: address already in use`, `1 failed, 26 deselected in 0.99s`, and the container is left `Created`. This is the same signature as R6's reds: ~1 s, and a `Created` d3 container carrying this checkout's label was seen mid-window. A **TIME-WAIT** socket on 55434 blocks the bind too: after the socket closed, the runner's own baseline for i3bm44 still failed the same way until the 60 s TIME-WAIT expired. During R6's window `ss` showed `TIME-WAIT 127.0.0.1:55434 → 127.0.0.1:55531`: some local client had taken 55434 as its **ephemeral** port. The range is `32768-60999` and there are no reserved ports, so this is **P-21**. It explains round 1's #12 as well |
| R9 | `cd apps/infrx-api && $PY -m pytest -q -rs tests/i` (15:06:10Z) | scratch merge | **0** | `143 passed in 199.77s (0:03:19)` |
| R10 | `--only i3bm94` / `i3bm97 i3bm98` / `i3bm95 i3bm96` / `i3bm99-101` / `i3bm102` / `i3bm103`, each as it landed | scratch merge | **0** | each `killed`, `problems: null` (the kill lines are quoted in the table above) |
| R11 | **the full list again, one run**, `$D TMPDIR=<private> $PY …/mutants_i3b.py --layer all --report m-all2.json` (15:21:24Z-15:31:38Z, nothing else of mine running, 55434 free at start) | scratch merge | 1 | **`{"mutants": 96, "killed": 94, "controls_survived": 1, "not_killed": 1, "pending": 0, "problems": ["i3bm33"]}`**. The count equals the declared list (96). The only problem is i3bm33 `no-cases` (rc06 needs E2's Valkey), as in round 1. Death sites: 83 `AssertionError`, 8 `Failed`, 1 `RuntimeError` (i3bm48, pgrestore.py:299), and 2 not visible in the tail (i3bm11/12, as the review found). New ones: i3bm94 `test_recovery.py:815`, i3bm97/98 `:849`, i3bm99/100/101 `:600/:607/:609`, i3bm95/103 `test_restore.py:464`, i3bm96 `:502` (all `AssertionError`), and i3bm102 `test_restore.py:122: Failed` |

### Limits (round 2)

1. **E2's stack, the box and hosted: not run.** rc04/rc06-rc09 and bk03 are as in round 1.
   i3bm33 is still `no-cases` here.
2. **P-21 applies to d3's port too (R8):** any local client can take 55434 as an ephemeral
   port, and while its socket is ESTABLISHED or in TIME-WAIT (60 s) the d3 container cannot
   start. Every D-mode case then fails in about 1 s, and the runner calls it `baseline-red`.
   That is fail-closed: the failure is never counted as a kill. I did not change the host's
   `ip_local_reserved_ports`: it is a host-wide setting and not this lane's to change.
3. Round 1's Limits 5 is now explained by R8 (P-21) together with DR-2 (concurrent D runs
   under private TMPDIRs).
4. Request 1 as written in round 1 is stale on `c587ddc` (D4 merged). E3B2's local work
   (`324224a`, unpushed) already rewrote the stage XML with synthetic ids; what remains is the
   `stack.py` half (R3).

### Integration requests (round 2; they replace nothing above unless they say so)

- **R2-1 (E3B2 / coordinator; updates request 1).** Merge order and substance are unchanged,
  with two exceptions. First, the stand-in `PENDING[D4]` in `test_stage.py` must be an id that
  is not merged (`D5` was verified here; E3B2's `324224a` uses synthetic `X1`-`X3`, which is
  better). Second, on E3B2's current local head only the `stack.py` half is still needed:
  RESIDUAL must drop `I2B`, and with request 1 it empties (R3).
- **R2-A (E3B2, runner) - NEW, required before this branch merges.** `OWNED_TREES +=
  ("apps/infrx-api/deploy",)` on its own line, so that e3bm23's anchor `"apps/infrx-api/tests/d",
  "infra")` still occurs once. i3bm94, i3bm97 and i3bm98 mutate `rollback.sh`/`lib.sh` in the
  copy. Without this line the copy has no `deploy/`, and two things break. I measured both on
  the cross-check clone at `f97bfb7`. First, `mutants_i3b.py --only i3bm97` crashes the run:
  `FileNotFoundError: … /infrx-e2-i3bm97-…/apps/infrx-api/deploy/rollback.sh`, exit 1. Second,
  E3B2's own `-k every_list_through_one_runner` fails with `AssertionError: assert ['i3bm94',
  'i3bm97', 'i3bm98'] == []`. With the line applied the same case gives `1 passed`.
  ```diff
   OWNED_TREES = ("tests/integration", "models/marlin2b", "apps/infrx-api/tests/d", "infra")
  +# I3B follow-up round 2 (DR-1/DR-3): rc10 runs I2B's rollback.sh and lib.sh from the
  +# copy, and i3bm94/i3bm97/i3bm98 mutate them.
  +OWNED_TREES += ("apps/infrx-api/deploy",)
  ```
- **R2-B (E3B2, runner) - NEW (DR-2).** It is additive, so e3bm31's anchor and its litter
  case are untouched, and it is scoped to I3B's suites in D mode:
  ```diff
           private = None if mutant.suite.startswith("apps/infrx-api/") else root / "tmp"
  +        # I3B's D mode (INFRX_I3B_PG=d): its restore and rc10 cases run D's pgharness, whose
  +        # port lock lives in the shared TMPDIR too - a private one gives each run its own lock
  +        # on the one shared port, and a second run removes the first's container (DR-2).
  +        if (os.environ.get("INFRX_I3B_PG") == "d"
  +                and mutant.suite.startswith("tests/integration/backend/recovery/")):
  +            private = None
  ```
- **R2-C (coordinator / D, host) - NEW (P-21, R8).** Take the D/E task ports out of the
  ephemeral range on the dev host (`net.ipv4.ip_local_reserved_ports`, for example
  `55432-55439,55464-55469` plus E2's), or give pgharness one bounded retry of `docker run` on
  `address already in use`. Until one of these lands, a D-mode run that coincides with local
  connection churn goes `baseline-red`. The runner stays fail-closed.
- Unchanged from round 1: the merge order (E3B2 first, then this branch with R2-1/R2-A/R2-B);
  the coordinator's `I2B-R4`/`M1-L2` owners; request 4 (runner litter), which E3B2 has
  addressed (`2cc281c`, `01ecdce`).

### Changes (round 2)

`git diff --stat d99da09..946e916`: 6 files, +176/−30. The files are
`infra/runbooks/{pgrestore.py, restore.md, rollback.md}` and
`tests/integration/backend/recovery/{mutants_i3b.py, test_recovery.py, test_restore.py}`, all
owned by I3B. No module code, contract, migration, deploy script, Makefile, E3B file or D file
changed. **Migrations: none.** New mutants: i3bm94-i3bm103 (96 in the list). New cases: rc00,
rc10b[8001/8002], bk00, bk01f[schemas], bk01f[column_acls]; rc10 and bk01g gained assertions.
Artifacts (session scratch, sha256 prefix): `m-all.json d38573c0586c13e6` (R6), `l0-head.log
94261dfe0a488b6f` (R1), `l0-merge.log 6d0cf41b2556100b` (R2), `recovery-d3.log c9feecf9608d9df1`
(R4), `m-reds2.json fb7447b1346d3c4d` (R7), **`m-all2.json 0e12d59209e7c0af` (R11)**.

## Round 3 — confirmation fix round (confirmation of `662c07c`)

The confirmation of round 2 (`research/plan/evidence/i/I3B-followup-confirm-662c07c.json` on
`claude/backend-impl`) returned **fix_required**. The restore lens passed; the drill lens found
one blocking finding (DRL-1, both refuters agree), and there were seven nonblocking findings.
This round fixes DRL-1 and folds in every nonblocking finding: DRL-2, DRL-3, RST-R2-1 and
RST-R2-2 change code, while RST-R2-3, DRL-4 and RST-R2-4 are corrections and requests,
recorded below.

| Field | Value |
|---|---|
| Base | `662c07c` (round 2) |
| Round-3 head | `b140378` code; this section is committed on top of it |
| Verified against | a scratch clone (`git clone --shared`, removed afterwards) of `662c07c` merged with **`origin/codex/e3b-phase2-gate` @ `b0a64e1`**. The merge was clean. On top of it: request 1's `stack.py` half (b0a64e1's RESIDUAL still lists G1R/D2/D3/M3/W3/I2B), R2-A and R2-B, then each round-3 commit cherry-picked. `b0a64e1` has none of the three |

### Findings → fixes

| Finding | Commit | Change | Case (dies at) | Declared mutant → kill (E's runner, scratch merge) |
|---|---|---|---|---|
| **DRL-1** (blocking): the readiness retry was not pinned | `428dbe7` | The stub can fail a call only its first `$INFRX_I3B_FAILS` times, counted in its own events log. New **rc10c** (layer 1): the gateway's `/readyz` fails once, then answers; the case expects exit 0, **exactly rc10's commands with `curl … 8001/readyz` issued twice**, then 8002, `docker inspect`, `docker exec … reload`, and the previous release's files. rc10b now asserts `len(retries) > 1`. lib.sh is unchanged (it is I2B's); the mutant edits only the runner's copy | rc10c exit code (test_recovery.py:954); rc10b (:938) | **i3bm104** lib.sh `until curl … "$1"; do` → `… "$1" \|\| return 1; do` (H1): `killed`, `test_recovery.py:954: AssertionError` (`assert 4 == 0`, by hand). By hand, rc10b[8001/8002] also die at `:938` (`assert (1 > 1)`) |
| DRL-2: rc03 pinned to G2, which has merged | `9a17db2` | G2 merged (2391d4d) and its cutover is held. The cutover is **G2 integration request 1**, owned by the coordinator. `recoverykit.OWNERS` gains one entry, **`"G2-R1"`**, in the I2B-R4/M1-L2 shape. rc03 pends on `PENDING[G2-R1]` and rc00 pins it. rc03 still fails the day the ingress is mounted | rc00 (:686) | **i3bm105** rc03 back on `"G2"`: `killed`, `test_recovery.py:686: AssertionError` |
| DRL-3: rc04's admit/claim half | `b140378` | **It could run today, and now does** (rc04a). The rig is D's `PgJobStore` from `pgtesting.make_jobstore_factory` on a migrated `infrx_i3b_jobs`, on the D harness or E2's PostgreSQL. PostgreSQL is SIGKILLed and restarted after a claim by `test_restore.kill_postgres`: this run's own D container, or bk03's E2 path, which bk03 now calls. **The same store object carries on:** the claimed job is `running` with its pins and the queued job is `queued`; a replayed admission is the same job; a second claim raises `NotClaimable`; a new admission is accepted and prepared; the reaper requeues the claimed job (one `IndexEvent`, attempt 1) and the next claim is generation 2; the wallet shows ledger = the grant and reserved = the three holds. `pg_postmaster_start_time()` must move, which proves the loss was injected. Kill-to-connection on d3 was 1.7 s (junit property). **rc04b** keeps the settlement half, pending on `stack.stubbed(("terminalize",))` (D5). Measured along the way: an admitted job that is never prepared is failed free by the reaper once the lease TTL passes (`preparation_failed`/`released_free`), so rc04a prepares the third job | rc04a (:293) | **i3bm107** `kill_postgres`'s `docker kill` → `docker inspect`: `killed`, `test_recovery.py:293: AssertionError` ("PostgreSQL was not restarted") |
| RST-R2-2: the column ACL check only saw presence | `ef72763` | `DAMAGE["column_acls"]` now moves `update (full_name)` from authenticated to anon (the grantee is swapped, the cardinality is the same) | bk01f[column_acls] (test_restore.py:466) | **i3bm106** `array(select unnest(a.attacl)…)` → `cardinality(a.attacl)` (R8): `killed`, `:466: AssertionError` |
| RST-R2-1: i3bm103 was a key rename | `14efe44` | i3bm103 now adds `and false` to the family's WHERE clause (R2), so the family's rows really disappear. Round 2 called it "family dropped", which was wrong: it was a rename, killed only by the name mismatch | bk01f[column_acls] (:466) | **i3bm103**: `killed`, `:466: AssertionError` |
| RST-R2-3: plain-image count | correction | Round 2's `6 passed, 20 skipped` was measured at `2d5df9f`, before RST-3 added `bk01f[column_acls]`: 10 at :109 + 9 bk01f + bk03. At `662c07c` and at this head it is **`6 passed, 21 skipped`** (10 `UNSUPPORTED[plain D image]` at :109, 10 at :449, and bk03's `no infrx-e2 stack`), exit 0, re-run at `b140378` | - | - |
| DRL-4 / RST-R2-4: what R2-B shares | wording + request | R2-B stands, but it shares pgharness's lock only among runs with **the same `TMPDIR`**, because the lock is `gettempdir()/<container>-<port>.lock`. Under the private TMPDIR each lane uses for its list runs, R2-B keeps a list run's children on the parent's lock and nothing more: two lanes with different TMPDIRs on d3 are still not serialised. A fix that holds across TMPDIRs belongs to D: see **R3-2**. pgharness is not changed here | - | - |

Round-3 mutants: i3bm104-i3bm107, plus i3bm103 redefined, so **100** in the list. All die by
`AssertionError`.

### Round-3 runs

UTC 2026-09-23. `PY=apps/infrx-api/.venv/bin/python`, `D=INFRX_I3B_PG=d INFRX_D_TASK=d3
INFRX_D1_IMAGE=supabase INFRX_D2_VALKEY_PORT=55464 INFRX_D2_VALKEY_CONTAINER=infrx-d3-valkey`,
private `TMPDIR` for every D run and mutant run.

| # | Command | Where | Exit | Result (quoted) |
|---|---|---|---|---|
| Q1 | layer 0: `$PY -m pytest -q -rs tests/integration/test_harness.py tests/integration/backend/test_stage.py tests/integration/test_run.py tests/integration/backend/recovery` (16:16:52Z) | this branch at `b140378` | **0** | `95 passed, 31 skipped, 2 warnings in 13.72s`; PENDING `[G2-R1]`, `[I2B-R4]`, `[M1-L2]` once each (round 2: 94/30; +rc10c; +rc04a, a no-stack skip at layer 0) |
| Q2 | same (16:17:06Z) | scratch merge | **0** | `111 passed, 31 skipped, 1 xfailed, 2 warnings in 14.38s`; same three PENDING |
| Q3 | `$D $PY -m pytest -rs -v tests/integration/backend/recovery` (16:17:30Z) | scratch merge | **0** | `60 passed, 9 skipped, 2 warnings in 75.25s` (round 2: 58/9). test_restore.py: 26 passed, 1 skipped (bk03). rc04a, rc00, rc10, rc10b×2, rc10c, bk01f[column_acls] PASSED; rc04b skipped (no E2 stack). Skips: PENDING[G2-R1], [M1-L2], [I2B-R4] + 6 that need E2's stack. No d3/i3b container left |
| Q4 | `INFRX_I3B_PG=d INFRX_D_TASK=d3 $PY -m pytest -q -rs …/test_restore.py` on the **plain** image | this branch at `b140378` | **0** | `6 passed, 21 skipped in 0.43s` (RST-R2-3) |
| Q5 | `--only` i3bm104; i3bm105 (layer 1); i3bm103, i3bm106, i3bm107 (layer 2, `$D`), as each landed | scratch merge | **0** | each `killed`, `problems: null`; the kill lines are in the table above |
| Q6 | **the full list**, `$D $PY tests/integration/backend/recovery/mutants_i3b.py --layer all --report m-all3.json` (16:19:51Z-16:32:08Z) | scratch merge | 1 | **`{"mutants": 100, "killed": 98, "controls_survived": 1, "not_killed": 1, "pending": 0, "problems": ["i3bm33"]}`**, which equals the declared list (100). The only problem is i3bm33 `no-cases` (rc06 needs E2's Valkey), and there are no baseline-reds. Death sites: 87 `AssertionError`, 8 `Failed`, 1 `RuntimeError` (i3bm48, pgrestore.py:299, the tool's own read-only guard), and 2 not visible in the tail (i3bm11/12). Round-3 mutants: i3bm103/106 `test_restore.py:466`, i3bm104 `test_recovery.py:954`, i3bm105 `:686`, i3bm107 `:293`; i3bm94 is now `:898` (lines moved) |
| Q7 | `cd apps/infrx-api && $PY -m pytest -q -rs tests/i` (16:18:58Z) | scratch merge | **0** | `143 passed in 186.73s (0:03:06)` |
| Q8 | G2-merged simulation: `claude/backend-impl`'s `tasks.json` (G2 `implemented`) copied into the scratch merge; `$PY -m pytest -q tests/integration/backend/test_stage.py` | scratch merge (`origin/claude/backend-impl` @ `6208d65`'s tasks.json) | 1 | `AssertionError: ({'G2', 'G3'}, set())`, `1 failed, 8 passed`. Both ids come from E3B2's own `stack.PENDING`; no I3B case names either (R3-1) |

### Limits (round 3)

1. rc04a's **E2 path** (`kill_postgres` on E2's compose service) has not been run here, because
   E2's stack is not this lane's to start. It is bk03's code, moved into the helper without
   change, and bk03 calls it; the gate's layer 3 runs both. rc04b is still a no-stack skip here.
2. On the scratch merge, rc04a passed on the Supabase 17.6 D image with `pgtesting` as of
   `b0a64e1` (D4's PgStreamStore hooks). On this branch alone the case is a layer-0 skip.
3. R2-B does not serialise lanes that use different TMPDIRs (DRL-4); R3-2 is the fix.

### Integration requests (round 3)

- **R2-1 / R2-A / R2-B: unchanged, and still required on `b0a64e1`.** That head has none of
  them. Q2 passes only with all three applied.
- **R3-1 (E3B2) - NEW (DRL-2's other half).** G2 has merged, so `stack.PENDING["G2"]` is a
  merged task in E3B's own vocabulary. I3B no longer names G2 (it uses `G2-R1`, in
  `recoverykit.OWNERS`). Q8 fails with the coordinator's tasks.json (`({'G2', 'G3'}, set())`), because both are E3B2's `stack.PENDING` entries and both have merged. E3B2 should drop them, or move them to RESIDUAL with reasons.
- **R3-2 (D, pgharness) - NEW (DRL-4 / RST-R2-4).** Give the port lock a path that does not
  depend on TMPDIR (for example `/tmp/infrx-<task>-<port>.lock` or `/run/lock`). Two runs on the
  same task port are then serialised whatever TMPDIR each uses. Until then, D-mode list runs
  that could overlap must share one TMPDIR.
- R2-C (reserve the task ports from the ephemeral range) is unchanged.

### Changes (round 3)

`git diff --stat 662c07c..b140378`: 4 files, +176/−37 (`mutants_i3b.py`, `recoverykit.py`, `test_recovery.py`, `test_restore.py`). Every file changed is under
`tests/integration/backend/recovery/`. No runbook, pgrestore, module, contract, migration,
deploy script (lib.sh untouched), D or E file changed. **Migrations: none.** New cases:
rc10c, rc04a, rc04b (rc04 split). rc10b, rc00, rc03, bk01f[column_acls] and bk03 changed
(bk03 by refactor only). Artifacts (session scratch, sha256 prefix): `l0-head3.log b51dc00729edc00b` (Q1), `l0-merge3.log 98830782da193fcb` (Q2), `recovery-d3-r3.log 270a3b6ff6d6d2f6` (Q3), **`m-all3.json 137b74b78491762b` (Q6)**, `tests-i3.log c2b703242ed1fb46` (Q7).

## Round 4 — verifier fix round (verification of `1a7a256`)

The round-3 verifier (`research/plan/evidence/i/I3B-followup-verify-1a7a256.json` on
`claude/backend-impl`) returned **fix_required**, with one blocking finding (DRL-R3-1) and three
nonblocking ones. Both refuters agreed that DRL-R3-1's mechanism is real, although they rated
it nonblocking; the coordinator made it blocking. The verifier reproduced every round-3 count
and kill site. This round fixes all four findings.

| Field | Value |
|---|---|
| Base | `1a7a256` (round 3) |
| Round-4 head | `5af39c0` code; this section is committed on top of it |
| Verified against | a scratch clone (removed afterwards) of `1a7a256` merged with **`origin/codex/e3b-phase2-gate` @ `5eb7b85`** (pushed; it contains R2-A `d8384ed`, R2-B `dadbd79` and `stack.OWNERS['G2-R1']`). The merge was clean. **IR2-2** is applied on top: the RESIDUAL entries are deleted from `PENDING`, `RESIDUAL = {}` and `stack.OWNERS` is kept. The round-4 commits are then cherry-picked one at a time |

### Findings → fixes

| Finding | Commit | Change | Case (dies at) | Declared mutant → kill (E's runner, scratch merge) |
|---|---|---|---|---|
| **DRL-R3-1** (blocking): the READY_S budget was not pinned | `79f1ff0` | rc10b runs rollback.sh with **READY_S=3** (POLL_S=0.01), times the call, and asserts `waited >= 2`. Bash's `SECONDS` counts whole seconds, so the real give-up lands in [2, 3] s. `rollback_sh` gains `ready_s` (default 1, so rc10 and rc10c are unchanged). rc10b's comment now says "retried, not tried once; for the whole READY_S budget". lib.sh is unchanged; the mutants edit only the runner's copy | rc10b[8001] (test_recovery.py:951 at the head) | **i3bm108** `local deadline=$((SECONDS + $2))` → `+ 1)` (V1): `killed`, `test_recovery.py:951: AssertionError`; by hand `gave up after 0.96 s of a 3 s READY_S (62 probes)`. **i3bm109** two tries, then `return 1` (V2): `killed`, `:951`; by hand `gave up after 0.03 s … (2 probes)`. **i3bm104** (H1) is still `killed`, at rc10c `:966` |
| RST-R3-1: grantees only | `916ab4f` | New bk01f parameter **`column_acls_privilege`**: same grantee, different privilege (`update (full_name)` → `select (full_name)`, both granted to authenticated). `FAMILY` maps it, and `functions_config`, to the family it must name. The grantee-swap parameter stays | bk01f[column_acls_privilege] (test_restore.py:471) | **i3bm110** grantees only (`split_part(x::text, '=', 1)`, R9): `killed`, `:471: AssertionError`. i3bm103 and i3bm106 are still killed by bk01f[column_acls] at `:471` |
| DRL-R3-2: rc04a's wallet wording | `5af39c0` | After the reaper step rc04a reads `infrx.wallet_reconciliation` for ORG_A and expects `(ledger_drift, reserved_drift, active_holds) == (0, 0, the three holds)`, so the check runs against the holds themselves and not only the summary row. It then runs the reconcile runbook's detector, `pgrestore.drift`, which must return `[]`. The docstring no longer says "the SAME store object carries on": the store survives because its connector opens a connection per operation, and **a pooled store (DATABASE_POOL_*) is not covered** | rc04a (:323-324; :295 is the restart check) | **i3bm111** `pgrestore.drift` filter `ledger_drift <> 0` → `= 0`: `killed`, `test_recovery.py:324: AssertionError`. The drift arithmetic itself is D's SQL (0003's view and the hold RPCs). The runner takes migrations from the checkout, not the copy, so no runner mutant can reach it; the detector is the only I3B-owned edit that can fail this assertion |
| IR-R3-1: request 1's target head | evidence | See the integration requests below: request 1 is now **IR2-2 against E3B2 ≥ `e95bce5`** (verified on `5eb7b85`). R2-A and R2-B are on E3B2's branch and are dropped from this lane's list | - | - |

Round-4 mutants are i3bm108-i3bm111, **104** in the list. All die by `AssertionError`.

### Round-4 runs

UTC 2026-09-23. `PY`, `D` and the private `TMPDIR` are as in round 3.

| # | Command | Where | Exit | Result (quoted) |
|---|---|---|---|---|
| S1 | layer 0 (the four paths), 17:04:50Z | this branch at `5af39c0` | **0** | `95 passed, 32 skipped, 2 warnings in 15.44s`; PENDING `[G2-R1]`, `[I2B-R4]`, `[M1-L2]` (+1 skip: bk01f[column_acls_privilege] needs PostgreSQL) |
| S2 | same, 17:05:06Z | scratch merge (5eb7b85 + IR2-2) | **0** | `113 passed, 32 skipped, 1 xfailed, 2 warnings in 18.23s` |
| S3 | same, with the coordinator's `tasks.json` (`claude/backend-impl` @ `7ed6993`: G2, G3, I2B implemented) | scratch merge | **0** | `113 passed, 32 skipped, 1 xfailed, 2 warnings in 17.34s`. Round 3's Q8 failure (`({'G2','G3'}, set())`) is gone on 5eb7b85 + IR2-2 |
| S4 | `$D $PY -m pytest -rs -v tests/integration/backend/recovery` | scratch merge | 1, then **0** | The first attempt (17:06:03Z) failed: `12 failed, 38 passed, 9 skipped, 11 errors in 13.61s`, every one `could not start infrx-d3-postgres-supabase: … failed to bind host port 127.0.0.1:55434/tcp: address already in use`. `ss` showed `TIME-WAIT 127.0.0.1:55434 → 127.0.0.1:55436`, i.e. another local client had taken 55434 as its ephemeral port (P-21, R2-C). After the socket cleared (17:06:53Z): **`61 passed, 9 skipped, 2 warnings in 63.37s`** (round 3: 60/9). test_restore.py: 27 passed, 1 skipped. rc04a, rc10b×2, rc10c, bk01f[column_acls] and [column_acls_privilege] PASSED. The `Created` d3 container left by the first attempt carried this checkout's label and was replaced by the second; afterwards no d3/i3b container remained |
| S5 | `--only` i3bm104/108/109 (layer 1); i3bm103/106/110 and i3bm107/111 (layer 2, `$D`), as each landed | scratch merge | **0** | each `killed`, `problems: null`; the kill lines are in the table above |
| S6 | **the full list**, `$D $PY …/mutants_i3b.py --layer all --report m-all4.json` (17:08:09Z-17:19:21Z) | scratch merge | 1 | **`{"mutants": 104, "killed": 102, "controls_survived": 1, "not_killed": 1, "pending": 0, "problems": ["i3bm33"]}`**, which equals the declared list (104). The only problem is i3bm33 `no-cases` (rc06 needs E2's Valkey), and there are 0 baseline-reds. Death sites: 91 `AssertionError`, 8 `Failed`, 1 `RuntimeError` (i3bm48, pgrestore.py:299), and 2 not visible in the tail (i3bm11/12). Round 4: i3bm108/109 `test_recovery.py:951`, i3bm110 `test_restore.py:471`, i3bm111 `test_recovery.py:324`; earlier ones: i3bm104 `:966`, i3bm105 `:693`, i3bm107 `:295`, i3bm103/106 `test_restore.py:471` |
| S7 | `cd apps/infrx-api && $PY -m pytest -q -rs tests/i` (17:08:09Z) | scratch merge | **0** | `143 passed in 157.26s (0:02:37)` |

### Integration requests (round 4: this list replaces the earlier ones)

- **Request 1 → IR2-2 (E3B2), on E3B2 ≥ `e95bce5`, verified on `5eb7b85`.** Delete the whole
  RESIDUAL block from `stack.PENDING` (G2, G1R, D2, D3, M3, W3) and set `RESIDUAL: dict[str, str] =
  {}`. Keep `stack.OWNERS` (`G2-R1`, byte-equal to `recoverykit.OWNERS['G2-R1']`) and
  `PENDING = {**OWNERS, "D5": …}`. This is E3B2's own IR2-2. S2 and S3 pass with it, using both
  the merge's and the coordinator's `tasks.json`. **R2-A and R2-B are done** (`d8384ed`,
  `dadbd79` on E3B2's branch) and are dropped from this list.
- **R3-2 (D, pgharness): unchanged.** The port lock should live at a path that does not depend
  on TMPDIR.
- **R2-C (coordinator / D, host): unchanged, and hit again in S4.** Reserve the task ports from
  the ephemeral range, or give pgharness one bounded retry of `docker run` on "address already
  in use".
- Merge order is unchanged: E3B2 (with IR2-2) first, then this branch.

### Changes (round 4)

`git diff --stat 1a7a256..5af39c0`: 3 files, +52/−17 (`mutants_i3b.py`, `test_recovery.py`, `test_restore.py`). All files are under
`tests/integration/backend/recovery/`. No lib.sh, pgrestore, runbook, module, migration, D or
E file changed. **Migrations: none.** New case: bk01f[column_acls_privilege]; rc10b and rc04a
gained assertions. Artifacts (session scratch, sha256 prefix): `l0-head4.log e71da9bdb3f44fb6` (S1), `l0-merge4.log bb63bc05fa0a70c6` (S2), `l0-merge4-coord.log 6881a17407cbe698` (S3), `recovery-d3-r4.log 1af578ecf0f312ad` (S4, second attempt), **`m-all4.json c2a4f90f48397f0d` (S6)**, `tests-i4.log 73bb6d9ccf94cc8e` (S7).

## Verification log

- 2026-09-23: Authored from the runs above; every count is quoted from command output or the
  JSON reports. Status **implemented, not integrated**. Nothing deployed; E2's stack not started.
- 2026-09-23 (round 2): review of `d99da09` answered - DR-1, RST-1, RST-4 fixed with cases and
  mutants i3bm94-96; DR-3, DR-4, RST-2, RST-3 folded in (i3bm97-103); DR-2 as IR R2-B; DR-5 and
  RST-5 left with reasons. Every count is quoted from command output or the JSON reports; the
  19 baseline-reds of R6 were diagnosed (P-21, R8) and re-run (R7). Status **implemented, not
  integrated**. Nothing deployed; E2's stack not started.
- 2026-09-23 (round 3): confirmation of `662c07c` answered. DRL-1 is fixed (rc10c + rc10b's
  retry count, i3bm104). DRL-2 (G2-R1, i3bm105), DRL-3 (rc04a on the D harness, rc04b,
  i3bm107), RST-R2-1 (i3bm103 a real drop) and RST-R2-2 (grantee swap, i3bm106) are folded in.
  RST-R2-3, DRL-4 and RST-R2-4 are corrected in the text above, with R3-2 to D. Full list on
  the merge: 100 mutants, 98 killed, the control survived, i3bm33 no-cases. Status
  **implemented, not integrated**. Nothing deployed; E2's stack not started.
- 2026-09-23 (round 4): verification of `1a7a256` answered. DRL-R3-1 is fixed (rc10b times a
  3 s READY_S; i3bm108/109). RST-R3-1 (column_acls_privilege, i3bm110) and DRL-R3-2
  (reconciliation drift + detector, i3bm111; docstring reworded) are folded in. The requests
  are restated against E3B2 `5eb7b85` + IR2-2 (R2-A/R2-B done there). Full list on that merge:
  104 mutants, 102 killed, the control survived, i3bm33 no-cases. Status **implemented, not
  integrated**. Nothing deployed; E2's stack not started.
