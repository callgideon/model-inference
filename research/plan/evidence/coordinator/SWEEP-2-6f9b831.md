# SWEEP-2: post-freeze minors sweep 2 (evidence)

| Field | Value |
|---|---|
| Task / branch / worktree | SWEEP-2 (support lane; merges after RELEASE d3a99e01) / `codex/sweep-2` / `.claude/worktrees/codex-sweep-2` |
| Base / code head | `7d10f586` / `6f9b8315` (this file and the update land in the next commit) |
| Isolation | Docker only through the tests below: the integration list's D-harness decoy ran on d4 (`INFRX_D_TASK=d4`: `infrx-d4-dharness-postgres` on 25435, 0 left after), and i8 was used for the one briefed command (00:04:46–00:07:07Z, lock free before, 0 `infrx-i8-*` containers before and after; G2's api-mutants was at 39 %, and `tests/i/test_mutants.py` is its last file). No E3C run. e2c, Q 55430, the e4b/e3c blocks and 55432 were not touched. 55430 was held throughout by the live G2 Q container (`infrx-q3-valkey-55430`). No case in this lane binds it: the only valkey-q case patches `pf.port_free`, and every bind in `test_preflight.py` is on the e2c-selftest block. No hosted DB, pilot box, AWS or SSM. No migration. No product code (`apps/infrx-api/infrx/` untouched; `tasklocal.py` unchanged, see item 2) |

## Items

### 1. SW1-R2: one integration mutant per SWEEP-1 case

- Defect: the two SWEEP-1 cases had no mutant in `tests/integration/mutants.py`.
- Change: `e2cp07` removes `valkey-q` from environment.json e2c `services`. `e3cr01` anchors reverts.py's `nc-dur-cap-org` on the `per_key` comparison. Neither mutant could be judged in the runner's copy as it was. The copy lacked `apps/app/supabase/migrations` (read by `reverts.latest`) and `research/plan/tasks.json` (read at module level by `test_e3c_runner.py`). So `OWNED_TREES` now includes the migrations tree, and a new `COPIED_FILES` tuple holds tasks.json (copied by `_copy_trees`, +3 lines).
- Fails-before: with the copy change reverted, `mutants.py --only e3cr01` gave `baseline-red` ("the unmutated copy is already red … 1 error").
- Passes-after, alone: `e2cp07` was **killed** (`1 failed, 58 deselected`, test_preflight.py:141, the busy assertion). `e3cr01` was **killed** (`1 failed, 48 deselected`, test_e3c_runner.py:531, the per-org assertion).
- In the list: the same (full run below).

### 2. SW1-R3 / SW1-RV-2: the valkey-q container identity

- Defect: vkharness names Q's container `infrx-q3-valkey-55430`, which is q3's container name plus the port. tasklocal gives e2c `valkey-q` the name `infrx-e2c-valkey-q`, which no container uses. Preflight's stale-container rule matches the `infrx-e2c-` prefixes, so it never sees a leftover Q container. None of the three files said so.
- Option taken: document and pin. A one-line registry change is not possible because tasklocal derives every name as `infrx-{task}-{service}`. Changing that needs a per-service override, which is registry logic in product code.
- Change: environment.json e2c gains `undetected_containers: ["infrx-q3-valkey-55430"]` and an `undetected_note`. ENVIRONMENT.md's e2c row states the same, with a log line. The note says why this is not a blocker: vkharness starts and reuses a stopped leftover of that name, and a running one holds 55430, which the port probe reports busy with `held_by` naming it.
- Test: `test_the_environment_manifest_is_self_consistent` loads the checkout's `vkharness.py` with `INFRX_Q_VALKEY_PORT` set to the valkey-q port. It asserts that the manifest names exactly `vkharness.CONTAINER`, and that the name lies outside the e2c prefixes.
- Fails-before: `KeyError: 'undetected_containers'`, 1 failed. Passes-after: 1 passed.
- Mutant `e2cp08` (the manifest names `infrx-q3-valkey`): **killed** alone and in the list.

### 3. SW1-RV-3: l8ref.sh's declared stop

- Defect: the script stopped only `infrx-observe.timer`. A cycle already running (`infrx-observe.service`, a oneshot) could still see the engine go down and page.
- Test first: `test_e1b_l8.py` expects `stop infrx-observe.timer infrx-observe.service` before `stop marlin2b-vllm`. Fails-before: 1 failed, 2 passed.
- Change: `systemctl stop $OBSERVE infrx-observe.service`. `systemctl stop` returns once the oneshot is down, so the running cycle ends before the engine stops. The header comment is updated. The restore still starts the timer only, and the timer triggers the service.
- Passes-after: 3 passed, and `bash -n` is clean.

### 4. SW1-RV-4: the lock notes

- `tests/integration/test_run.py` D-mode docstring now reads: since WR-BM-1 the pgharness lock is `/tmp/<container>-<port>.lock` whatever TMPDIR is.
- The D-mode TMPDIR note is updated to say the same. It lives in `tests/integration/mutants.py` `run_one`, in two comments; `apps/infrx-api/tests/i/mutants.py` has no TMPDIR note.
- Comments and docstrings only. `test_run.py -k "anchor or mutation_stage or d_mode or tmpdir"`: 4 passed.

### 5. WR-SWEEP1-1: WC-2's decisive c = 1 gateway half

- Tests first:
  - `test_rollout.py`: the container list gains `infrx-e1b-pair-c1` after `pair-r2.0`, cells.tsv has 3 WC-2 rows, and the stamped `pair-c1` profile is closed-loop c = 1 on `e1b-w1-pair-c1`.
  - `test_profile.py`: the plan count is 12.
  - Fails-before: `test_e1b_window__cells_run_in_order…` 1 failed, and `test_every_section7_command…` 1 failed.
- Change:
  - `e1b-window.sh`: after WC-2's direct loop, `cell WC-2 E1B-box.json $BOX -c 1 -n 64 --engine-state warm --dataset-version e1b-w1-pair-c1`.
  - E1B-protocol §7.2 WC-2 row: the bench line; wall 9–12 min (+ 64 requests at WC-1's `meas.` c = 1 rate); ceiling ≤ 865.0752 (64 × 13.5168). The row's title is now "paired legs", and the "NOT RUN until WR-SWEEP1-1" sentence is gone.
  - The §7.2 budget paragraph follows: cells 54–77 min (the 1.0–1.6 h window and its USD unchanged at this rounding), gateway ceilings 9,002.1888, and a certify-tenant share of 8,096.5632, read before WC-2's c = 1 half. One line is appended to the verification log.
  - `test_profile.py` reads `n` from `-n` when `--requests` is absent. The c = 1 half is the first gateway cell spelled `-n`, and without this the ceiling check raised ValueError.
- Passes-after: models/marlin2b/tests + test_rollout 136 passed. The pair-c1 argv validates against the box base (0 blocks, 0 warnings, CREDIT spend = the ceiling).
- Mutants: the six `e1b_window_*` anchors are unchanged and still occur once. `INFRX_MUTANTS=all tests/i/test_mutants.py -k "e1b_window or well_formed or every_case"`: 8 passed (6 killed, list well-formed, every case covered).

### 6. WR-SWEEP1-2

The e3c README DUR-CAP row's control column now reads: `nc-dur-cap` (all three comparisons), `nc-dur-cap-org`, `nc-dur-cap-key` (one comparison each, caught by the scoped burst).

### 7. WR-SWEEP1-3

The `tests/integration/app/runner.py` comment above `E3C_FINAL` now says four things:
- The RELEASE run is the reference: E3C final at d3a99e01, 16/16 scenarios and 12/12 controls, per RELEASE-d3a99e01.md.
- The values still name the E3C-CELLS evidence (9227e9ed), on the same scenario set.
- 5620546c (14 controls) is the next candidate.
- Adopting it is a coordinator change of the values.

The values are unchanged.

## Commands (exit, counts)

| Command | Head | Exit | Result |
|---|---|---|---|
| `pytest -q tests/integration/test_preflight.py tests/integration/backend/e3c/test_e3c_runner.py tests/integration/app` | 6f9b8315 | 0 | 128 passed (59 + 49 + 20) |
| `INFRX_D_TASK=d4 python tests/integration/mutants.py` (layer 1, full list) | 6f9b8315 | 1 | 196 mutants: **190 killed** (e2cp07, e2cp08, e3cr01 among them), 3 controls survived. Problems `e2m70` (SURVIVED), `i3bm117` (no-cases) and `i3bm54` (baseline-red), the same three as SWEEP-1 at b640c35c, not this lane's |
| `python models/marlin2b/tests/mutants.py` | 6f9b8315 | 0 | 119: 116 killed, 3 controls survived, no problems (`e1bm17`'s `--seed 20260922` still ×4) |
| `pytest -q models/marlin2b/tests tests/i/test_rollout.py` | 6f9b8315 | 0 | 136 passed |
| `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py -k "e1b_window or well_formed or every_case"` (i8, once) | 6f9b8315 | 0 | 8 passed, 376 deselected (140 s) |
| `pytest -q tests/integration/test_run.py -k "anchor or mutation_stage or d_mode or tmpdir"` | 6f9b8315 | 0 | 4 passed |
| `ruff check` on the 7 edited Python files | 6f9b8315 | 1 | no new finding: 5 files clean; test_profile.py 16 and test_run.py 1, both identical at 7d10f586 |
| `python3 research/plan/scripts/validate_plan.py` | final | 0 | PASS |
| `git diff --stat 7d10f586..HEAD` | final | 0 | 13 code paths + this file + the update; all owned, deviations below |

## Deviations

- `tests/integration/mutants.py` copy layout (`OWNED_TREES` + migrations, `COPIED_FILES` tasks.json). Without it, e3cr01 is baseline-red.
- The SW1-RV-4 note is in `tests/integration/mutants.py`, not `tests/i/mutants.py`.
- One mutant beyond the brief: `e2cp08`, for the item-2 assertion (LANE-RULES coverage rule).
- `test_profile.py`: the `-n` lookup, beyond the plan count.
- E1B-protocol §7.2: the WC-2 row's wall and ceiling cells, the budget paragraph and a verification-log line, beyond the bench line.

## Wiring requests

- **WR-SWEEP2-1** (`models/marlin2b/results/E1B-protocol.md` WC-6a row, E1B owner): "`infrx-observe.timer` (the alert cycle) is stopped for the span" becomes "`infrx-observe.timer` and a running `infrx-observe.service` cycle are stopped for the span". The script and test already do this; the row names only the timer.

## Open issues

- Pre-existing, not this lane's: integration mutants `e2m70`, `i3bm117` and `i3bm54` (as SWEEP-1).
- E1BP-11's decisive pair is now planned. It runs in the E4C window; nothing here was run on a box.

## Remaining effort

Optimistic 0.1 h / likely 0.3 h / pessimistic 1 h, confidence medium. Basis: merge after RELEASE; the pessimistic case covers a review round on the §7.2 budget arithmetic.
