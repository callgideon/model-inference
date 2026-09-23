# Merge plan: the eight in-flight backend branches → checkpoint 2 → `main`

MERGE-ANALYSIS lane (Opus), 2026-09-23. The eight branches were merged for real on a scratch
clone (`git clone --shared` of the `infrx-impl` worktree), every conflict was resolved by a
script in this directory, and the key suites ran on the result. Nothing outside the scratch
and this directory was changed, and nothing was pushed.

## 1. What was analysed

| # | branch | head analysed (full SHA) | base | since then (read-only check at the end) |
|---|---|---|---|---|
| — | `claude/backend-impl` (integration) | `c3ab33cd45d3cba526960157dc09919a5f25fd9b` | = interim-gate tree `602b1e0` + plan-only commits | — |
| 1 | `codex/d5-terminal-transaction` | `8554b47177887485424c8e2e6b58706f2a3f2d2d` | merged `d926cc9` at `c807bb5` | unchanged |
| 2 | `codex/e3b-phase3-bodies` | `a4facba5ff91a1bedf4c65f02beedc12953db8f0` | `7c52627`; contains D5 `c67e4f5`, cutover `6cb8ebe`, M1-L2 `e1bb54f` | `4ac1419` (evidence only: IR3-1…IR3-7) |
| 3 | `codex/cutover-mount` | `351d084c64c87fd907b2fc265bde0f32417ad69a` | `7c52627`; contains D5 `c67e4f5`, M1-L2 `e1bb54f` | uncommitted work in its worktree (install.sh, preflight.py, config.py, app.py, pilot.py, metrics.py, `evidence/g/CUTOVER-351d084.md`) |
| 4 | `codex/m1l2-object-store` | `0b9fc5010b4522a2680549ef563f0cf6509f8bda` | contains cutover `6cb8ebe` | unchanged |
| 5 | `codex/m-pilot-media` | `078eefeb1ac76c0bf46ef9c1ffdca10d5660ed4e` | contains cutover `6cb8ebe` | unchanged (no evidence file yet) |
| 6 | `codex/e4b-certify` | `5afe3d9a1b3a143865e4730f89d3ac808b8f9a26` | `7c52627` (before D5 and phase 3) | `7b5dbd7` (evidence only) |
| 7 | `codex/box-measure` | `c85930c6fd0992bb85d05fa554193ee0c0f382e0` | `6781fac` | `eea17d8` (evidence + `bench.jsonl`/raw rows; window closed 21:54:51Z; no serve.sh / serving-version.json change) |
| 8 | `codex/rollout-prep` | `6ac2beca792262d5d1c74c2b9c263dc351a108be` | ancestor of the integration head | no commit yet: "Already up to date" |

**The entanglement that decides the gating.** Steps 2, 3 and 4 are not independent merges:

- The phase-3 branch carries the cutover at `6cb8ebe` and M1-L2 at `e1bb54f`.
- The cutover branch carries M1-L2 at `e1bb54f`, which is the head M1-L2's review found fix_required (A1–A7). The fixes land only with step 4.

So **steps 1–4 are one unit**, run back to back and gated on all four verdicts: the D5 verifier, the phase-3 review, the cutover review and the M1-L2 verifier. The checks run once, after step 4.

If D5's verdict comes much earlier, `1b-d5-alone-option.sh` is the fallback. It merges D5 alone, cherry-picks the phase-3 branch's identical-text commits `eb08901`, `8c00bb4` and `54d3955`, and applies D5 request 4. At the later phase-3 merge, take the phase-3 side of `Makefile` and `tests/integration/pgstate.py`. Measured: the tree then equals option A's tree after step 2's merge. The intermediate tree was not tested, and it is no gate candidate: 0018 makes `terminalize` real, so the layer-3 tripwires dr07c and rc04b fail until phase 3 brings their bodies (D5 review CF-5/H-B1).

## 2. Order and scripts

Run from the repo root on `claude/backend-impl`, with a clean tree:

```
D5_VERIFY="…" E4B_VERIFY="…" bash research/plan/evidence/coordinator/merge-plan/replay.sh
```

Or run the steps one at a time. Each script merges with `git merge --no-ff`, applies its resolutions, and makes one commit per coordinator edit. Every edit is an assert-guarded Python or `git apply` step, so a moved head fails loudly instead of mis-applying. `*_REF` overrides a branch head.

| step | script | conflicts | coordinator edits (one commit each) |
|---|---|---|---|
| 1 | `1-d5-terminal-transaction.sh` | none | none. D5 alone is red on `tests/contracts` until step 2 |
| 2 | `2-e3b-phase3-bodies.sh` | none | (a) D5 request 4, `cli.build_operations` diff (sha256 `d2a26348…`, read from D5's evidence); (b) D5 request 6, `q3rig.diff` (`4b69278e…`; the default `fake` store is unchanged); (c) `validate_plan.py` skips fenced code, and the handoff links to files the cutover retired become plain text; (d) tasks.json: D5 → implemented, E3B disposition += phase 3; ledger regenerated |
| 3 | `3-cutover-mount.sh` | none | none: E4B's B1 is the cutover's own item 6 (`351d084`); the 08 §5/§5.1 rows are the lane's |
| 4 | `4-m1l2-object-store.sh` | none | none: the Makefile s3 list, the Dockerfile `--extra traces` and the §5.1 rows are already in |
| 5 | `5-m-pilot-media.sh` | **Makefile** `api-mutants`: keep ours, insert `tests/m/test_pilot_mutants.py` after `tests/m/test_mutants.py` | none: no migration (PgAttachments uses 0003's `staged_media`/`job_media`), so no harness list or pgstate row |
| 6 | `6-e4b-certify.sh` | **Makefile**: keep ours, add E4B's 2 comment lines and its second root-run command. **Semantic, E4B × phase 3**: `certify.dataset_check` pends on owner `D5`, which left the vocabulary, so it became an untyped FAIL (`('FAIL', ['D5']) == ('PENDING', ['D5'])`); the owner is now BOX. **Semantic, E4B × cutover**: the generated `E4B-endpoint.md` went stale (`test_endpoint_doc` red; all 126 E4B mutants `broken_runner` at the pristine baseline) | (a) owner BOX; (a2) the generator's release rows reworded and regenerated with `--write`; (b) namespace `e4b` (NAMESPACES +1300 → 56800–56899, TASK_BLOCKS, TASK_PORTS pg 56832; E4B request 1); (c) tasks.json E4B → implemented (software half); ledger regenerated; (d) Makefile lists in one canonical order |
| 7 | `7-box-measure.sh` | none | none (P-20 is a configuration decision, §6) |
| 8 | `8-rollout-prep.sh` | none, nothing to merge at analysis time | the script refuses if the branch touched code |
| 9 | `9-rulings.sh` | — | 08 §10 R95–R98 with the lanes' text, plus one verification-log line |

`private-harness.sh` is **not** a merge step. It is the scratch's private D task: `infrx-merge-analysis-postgres` on 55691, decoy 55693, D Valkey 55692, Q 55695, MinIO 55694. It was never committed.

### Identical-text double applications: apply ONCE (they are already in the branches)

| request | already applied as | do not |
|---|---|---|
| D5 IR1: Makefile += `tests/d/test_code_mutants_d5.py` | phase-3 `eb08901` | re-add the list |
| D5 IR1: `test_harness.py` migration list ends at `0018_terminal_settlement.sql` | phase-3 `54d3955` | re-add the line |
| D5 IR2: `contracts-retire-0016-refusal.diff` (sha256 `a8e75c3f…`) | phase-3 `8c00bb4`; its +/- lines are identical to the diff, and the diff reverse-applies on the merged tree | apply the diff |
| D5 IR8: pgstate 0018 rows (7 definer + 3 invoker functions, CHECK, guard, columns per role) | phase-3 `54d3955`; rows equal to D5's evidence | paste the rows |
| RAISES cleanup (coordinator's `75cd7bb`, `f52308a`) | D5's own merge `c807bb5` (RAISES = the dur_cap entry only; `set(RAISES) == set(PENDING)`) | touch RAISES/PENDING in `tests/d` |
| G2-R1 / D5 pendings in `stack.PENDING` / `recoverykit` | phase-3 (`3037a83`, `a4facba`) | edit the vocabulary |
| E4B request 3 (B1: fixture pins = W3's) | cutover `351d084` (+ its G6B case reading `serving-version.json`) | apply E4B's request text |
| E4B request 5 = D5 IR4 (`cli.build_operations`) | nowhere yet | apply it once, at step 2(a) |
| M1-L2 IR5: Makefile += `tests/m/test_s3_mutants.py` | M1-L2's own commit, arrived through the cutover/phase-3 merges of `e1bb54f` | re-add the list |

## 3. Results on the merged scratch

Tree tested: `scratch/replay` = `replay.sh`, with the two semantic fixes found by these runs
folded into script 6. A fresh `replay.sh` of the final scripts (`scratch/final`) gives an identical
tree except the merge SHAs written into tasks.json. The private D-harness patch was uncommitted.

**Hashes for comparison** (after 9-rulings; placeholders `<D5 verifier ref>` / `<E4B verifier ref>`):

- full tree `473e1b44f421588a904405ad06f9be1a5c629c90`. It moves with the merge SHAs and dates in tasks.json/08, so compare these instead:
  - `HEAD:apps` `f9c69845b9a238bcd604dd0ddcfc72df893b355a`
  - `HEAD:tests` `3cd65635232482a5a09616fcd4c67396c7e37577`
  - `HEAD:models` `3d764642c0ac60744272c79c6ba729017959991a`
  - `HEAD:Makefile` `92f65cb74c805d032daa22887d9df310ca798b84`

| check (merged scratch) | tail |
|---|---|
| `make bench-test` (foreground) | `67 passed in 6.71s` |
| layer 0, repo root, API venv, INFRX_E2_NAMESPACE unset (1st run, before fix 6a2) | `6 failed, 193 passed, 102 skipped`. 3 E4B mutants `broken_runner` + `test_endpoint_doc` = the stale E4B-endpoint.md (**fixed: 6a2**); `test_fake_vllm` (bind 55586) + `test_busy_ports` (55580) = another lane's live `infrx-e2-*` stack held E2's 55500-55599 at that moment (environment, P-21; green on the rerun) |
| layer 0 rerun after 6a2 | `199 passed, 102 skipped` exit 0. Pendings: `PENDING[I2B-R4]` only (no D5, no G2-R1). `test_every_mutant_anchor_occurs_as_declared_on_the_checkout` PASSED. The e4b namespace cases (4) pass. `stale_pending`: none named (D5 and E4B flipped) |
| `tests/integration/backend/test_certify.py` before fix 6a | `1 failed, 28 passed`: `('FAIL', ['D5']) == ('PENDING', ['D5'])` (**fixed: 6a**) |
| `test_endpoint_doc.py test_certify.py test_e4b_mutants.py` (default) after 6a + 6a2 | `42 passed in 15.54s` |
| E4B list `INFRX_MUTANTS=all` (1st run) | `126 failed, 2 passed`: all `broken_runner`, pristine baseline red on `test_endpoint_doc` (fixed: 6a2) |
| E4B list `INFRX_MUTANTS=all` rerun after 6a2 | **not finished.** Stopped at about 60 % by the enforced handback. By then two `F` had appeared, not yet attributed (possibly the load; the host load was 16). **Owner: E4B / checkpoint 2 must rerun it** |
| `tests/g` whole | `571 passed, 2 warnings in 190.47s` |
| `tests/i` | `151 passed in 228.42s` |
| `validate_plan.py` | red before step 2(c): `ERROR: Broken link` ×10 (9 handoff links to files the cutover retired, and the `](org, "1000")` inside D5's fenced q3rig diff). After 2(c) and 6(c): `PASS … 725 local Markdown links checked across 152 documents; ledger current` |
| `tests/d` whole on Supabase (default subset) | **not finished.** Stopped at about 35 %, 0 failures so far (`x` = the strict xfails) |
| tests/d plain focused, D5 mutants all (code; migration `-k d5_`), `tests/contracts`, `tests/m` + MinIO, s3 / pilot lists all, `tests/w -x`, `tests/q` | **not run / cut off** by the enforced handback: `tests/w` reached 40 % with no failure; tests/q was just starting |

**Could not run at all:** the E2 compose stack (layer 1–3, the phase-3 gate, `certify.py` on a stack namespace), the console suites (not needed, §5) and a quiet host for the W3 sigint mutant.

## 4. Checks after each merge (minimal: the interim gate on `602b1e0` covered every line before D5)

`c3ab33c` is `602b1e0` plus plan-only commits, so nothing needs re-running for the base.

- **After steps 1–4 (one unit):**
  - `tests/d`: whole on Supabase; D5's files on plain.
  - `tests/contracts`; `tests/g`; `tests/i`; `tests/m` with MinIO; `tests/q`.
  - Layer 0 from the repo root (INFRX_E2_NAMESPACE unset): expect pendings `I2B-R4` only, the anchor guard green, `stale_pending` empty.
  - `validate_plan.py` (the scripts already enforce it).
  - Lists with `INFRX_MUTANTS=all`: `tests/d/test_code_mutants_d5.py`, `tests/d/test_migration_mutants.py -k d5_`, `tests/m/test_s3_mutants.py` (MinIO).
- **After step 5:** `tests/m`, `tests/g`, `tests/contracts` (its mutants.py auto-merged); `INFRX_MUTANTS=all tests/m/test_pilot_mutants.py` (D harness); layer 0.
- **After step 6:** layer 0; `INFRX_MUTANTS=all tests/integration/backend/test_e4b_mutants.py`; `tests/integration/backend/test_endpoint_doc.py` and `test_certify.py`; `tests/contracts/test_config_and_imports.py` and `tests/integration/test_harness.py` for the new namespace.
- **After step 7:** `make bench-test` in the foreground.
- **After steps 8–9:** `validate_plan.py`.

## 5. Checkpoint 2 gate (rerun vs reuse)

**Rerun**, because the code changed since `602b1e0` in `infrx/{state,gateway,media,contracts,operations,auth,config}`, `deploy/`, `tests/{d,g,i,m,contracts,q}`, `tests/integration` and `models/marlin2b/bench.py`:

- `make api-test`: whole. The change spans six test trees, so a per-directory rerun saves little.
- Layer 0 from the repo root.
- `make bench-test` in the foreground.
- `make api-mutants` for the lists whose targets changed:
  - contracts (mutants.py changed by D5 IR2 and M pilot);
  - m, pilot, s3 (with MinIO);
  - d (migration all + code d5; d3/d4 re-anchored by D5);
  - g, g/ops, g/uploads, g/jobs (re-anchored by the cutover and M pilot);
  - i (the cutover's I mutants);
  - E4B's root-run list.
- The W3 `sigint_not_handled` mutant on a quiet host.
- One layer-3 run of `run.py --layer 3` on an E2 namespace. It needs the compose stack, which this lane could not run. Expect exit 3 with PENDING only: `M3-U1`×3 until the phase-3 lane's IR3-3 follow-up, and `I2B-R4`.

**Reuse** from the interim gate:

- `tests/j`, `tests/t`, `tests/w` (untouched; run once anyway, `-x`, see §3);
- the q/j/w/t mutant lists;
- console-test, lint, typecheck and mutants (the only `apps/app` change is the 0018 SQL file; no TS references migration names).

## 6. Fast-forward `main`

The remote `origin/main` = `01a7dfc`, and the main checkout's local `main` = `f9ba5d2`. Both are ancestors of the integration head, so after the checkpoint-2 gate:

```
git push origin <checkpoint-2 sha>:main
git -C <main checkout> merge --ff-only <sha>
```

This is a fast-forward, never a merge commit.

## 7. Box phase 2 preconditions (the I2B rollout → E1B L2–L7 → E4B `certify.py --box` → release decision)

1. **Release SHA.** `main` is fast-forwarded to the checkpoint-2 tree, and the box checkout sits at that SHA, clean. R97: `git_head == git_head_end`; `--release-sha` goes to certify.
2. **P-20 decision.** Box result: W4 phase B decision B at 82 s; `serve.sh` and `serving-version.json` unchanged. Choose one:
   - `MAX_VIDEO_SECONDS=82`: E4B's `CRITERIA["applied_cap_s"]=72` and the protocol move in the same commit.
   - Keep 72.
3. **The pinned engine.** Deploy the pinned `serve.sh`: `ENGINE_MAX_NUM_SEQS` 8 and `--allowed-local-media-path` (E4B B2; the box runs 32 today). `ENGINE_MAX_NUM_SEQS`/`WORKER_CONCURRENCY` stay 8.
4. **Hosted Supabase.**
   - Back up first.
   - Apply 0010–0018; 0018 takes ACCESS EXCLUSIVE on `infrx.jobs` briefly; no backfill; there is no 0019.
   - `ACCOUNTING_REGIME` stays `legacy_usd` (D5 request 5: CREDIT needs W's `load_work_credit`/`complete_credit`).
5. **Settings.**
   - `install.sh INFRX_MODE=pilot` (explicit: an unset mode refuses since the cutover), with `DATABASE_URL` and `VALKEY_URL`, plus `--set S3_MEDIA_BUCKET=llm-bootcamp-641134885443` (prefix `infrx/`; HeadBucket must answer).
   - The instance role needs ListBucket, plus Get/Put/Delete on `infrx/*` and on `test/m1l2/*` for M1-L2's AWS verification run: `tests/m/test_s3.py` with `INFRX_M_S3_LOCAL_CREDS` unset (M1-L2 limit 8).
6. **The edge.** Deploy the cutover's Caddyfile (jobs/uploads proxied unbuffered, `6cb8ebe`) with the rollout. The box lane's live Caddyfile `ff47f706` is today's restore point.
7. **E4B box protocol v2.**
   - A git-capable certify image; `--release-sha`; image ids read from docker; the `E4B_WINDOW_OK` window record.
   - App/Lab stopped on the box.
   - The ledger half of the dataset drill needs `DATABASE_URL` in certify's environment (owner BOX, step 6a).
8. **Open owners that stay PENDING.**
   - `I2B-R4` (the `python -m infrx.worker` entry point).
   - `M3-U1` (until IR3-3).
   - Nothing starts `MediaCollector.run` (M1-L2 request 7).
   - One gateway process per media prefix (M1-L2 limit 6).

## 8. Open items for the coordinator

- **R98's text.** It is the cutover lane's draft, from an uncommitted evidence file. Re-read the committed `evidence/g/CUTOVER-*.md` at step 9. M pilot-media proposed no ruling at `078eefe`; a later candidate takes R99. D5's ruling candidates (i)–(iv) and `platform_cancelled` are still unnumbered.
- **Re-running on newer heads.** When a lane's head has moved, run its script against the new head. The asserts catch drift, and the tree hash in §3 then no longer applies. Compare the subtree hashes for the lanes that did not move.
- **Follow-ups owned elsewhere:**
  - IR3-3: phase 3 retires `M3-U1` after step 5.
  - Q owns the 4 `INFRX_Q3_STORE=postgres` rig failures.
  - D5 requests 3/5/7.
  - The G6B audit action map.
  - The tracker (`progress.py`) after each flip.

## Verification log

- 2026-09-23: Written by the MERGE-ANALYSIS lane from the scratch replay. Every count in §3 is quoted from a log of this session. The handback was enforced before the long suites finished; the unfinished ones are marked as such, not as passes. Containers `infrx-merge-analysis-{postgres,postgres-supabase,valkey,dharness-postgres,minio}` and `infrx-q3-valkey-55695` were removed, the run processes were killed, and the scratch clone was removed. The codex/merge-plan commit is kept as a bundle in the lane's session scratchpad.


## Coordinator amendments (2026-09-23T23:5xZ)
- Step 2(a) dropped: D5 request 4 (cli.build_operations) is superseded by the cutover's f7d9b03; the script now asserts the diff no longer applies forward.
- Step 7 is idempotent: the box branch merged at 0117d48.
- Step 8 accepts rollout-prep's code (f0a1a82: rehearse.sh step 8, release-bundle.sh, infra/rollout/steps, rollout.md, tests/i/test_rollout.py) and runs its tests instead of refusing.
- Step 8b added: the worker composition root (branch codex/i2b-r4-worker, I2B-R4), gated on WORKER_VERIFY; it carries I's rehearse.sh change for the release tree (the G5 finding).
- Heads moved since the analysis: D5 4bfdbf0 (code 8554b47), phase 3 ≥ 1fa825b (fix round running; contains M pilot 8b91648), cutover a1e88dc, M1-L2 ba26ca4 (contains cutover f7d9b03), M pilot 8b91648, E4B 4d9360c, rollout-prep f0a1a82. The scripts' SHA pins (Makefile line resolutions, 6a/6a2) are assert-guarded: re-run `git merge-tree` per step before replay and expect step 4's Makefile to already carry the s3 line.
- The release carries D5's FINAL 0018 (only D5 changed it since the base); the rollout's plan digest is recomputed on the day (W6/W7).
