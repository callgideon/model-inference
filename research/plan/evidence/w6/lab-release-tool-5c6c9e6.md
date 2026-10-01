# W6 lab-release-tool — evidence (INFRA-09, INFRA-03, INFRA-01, DT-18, INFRA-08, DT-12, DT-13, LAB-03 deploy half)

- Lane: lab-release-tool (wave W6), branch `codex/w6-lab-release-tool`, base `08983639`, code head `5c6c9e60` (+ this evidence commit).
- Key: `i6` (task-local PostgreSQL `infrx-i6-postgres` :57525). Used only for one scratch check run by hand (§4). The committed suite needs no Docker.
- Nothing touched hosted Supabase, the box, AWS/SSM/S3 or Vercel. Every `aws`/`vercel`/`ssm.sh` in this file is a stub.

## 1. Changes

| path | change |
|---|---|
| `infra/lab/rollout/launch-v1.sh` → `infra/lab/rollout/lab-release.sh` | Renamed (git records it as A + M because the shim reuses the old path; history: `git log -- infra/lab/rollout/launch-v1.sh`). Actions: `preflight` (read-only half kept), `box`, `web` (was `vercel`), `members`, `main`. Removed: `window()`, the THROUGH table, `all`, preflight's R151 half, the stale :59 and :164 lines. 217 → 138 lines. |
| `infra/lab/rollout/launch-v1.sh` | A deprecation shim (shebang + 2 lines, removed next wave). It execs `lab-release.sh` and maps `vercel` to `web`. |
| `infra/lab/rollout/hosted-migrate-0052-0056.patch`, `-0057-0059.patch` | Moved with `git mv` to `research/plan/evidence/i/` as the artefacts of the two spent windows. |
| `apps/infrx-api/tests/i/test_lab_release.py` | New: 8 layer-0 cases. |
| `apps/infrx-api/tests/i/test_known_good_proof.py` | The KGR4-RV-1 window-patch case (40 lines) is replaced by the anchor-derived case (11 lines, DT-12): EXPECTED_PENDING equals this tree's migrations after the `HOSTED_APPLIED` anchor, computed the way lab-migrate.sh computes it, and the W7 post-check names the newest migration. The unused `subprocess` import is removed. |
| `apps/infrx-api/tests/i/mutants.py` | Removed `launch_v1_0059_hosted_at_0051` and `launch_v1_pushes_the_tip`. Added 3 `hosted_migrate_*` and 26 `lab_release_*` mutants. The layout now copies the two evidence patches. |
| `research/plan/consumer-v1/08-lab-internal-testing-rollout.md` | §2 gains "Next window (0060+)": a re-proof plus one reviewed edit of three lines, and BOX_RELEASE derived after `git fetch` (DT-13). §7 steps 2–4 now run `lab-release.sh members`. One log line added. |

`box()` and `main_ff()` are byte-identical to the base (checked by diff). `vercel_lab()` differs only in the token-staging lines (INFRA-08):
- the token is staged with `t=$(umask 077; mktemp)` plus an EXIT trap;
- `VERCEL_TOKEN=$(cat "$t") vercel whoami` must pass before `put-parameter`; an invalid paste exits 2 and leaves the stored token untouched;
- the file is shredded after the write;
- the prompt now goes to `/dev/tty`. On the base, `2>/dev/null` swallowed the `read -p` prompt.

Scope `callgideon`, the npx fallback, the login fallback, the repo-root link/deploy, the env loop and the domain are unchanged.

`members` (INFRA-01/DT-18):
- needs `TESTER_EMAILS`;
- operator = `OPERATOR_NAME` (default `git config user.name`);
- reads the owner DSN by SSM name (`OPS_DSN_PARAM`, default `/model-inference/pg_journal_url`) into `OPERATIONS_DATABASE_URL`;
- runs a psycopg program from stdin with `%s` parameters, in one transaction;
- prints the org's memberships as `email role granted_at`;
- has no prompt and does not use psql.

**Defect fixed in passing:** the base statement selected `p.id`, but the column is `provider_org_id` (0007). On 0001–0059 it fails with `UndefinedColumn column p.id does not exist` (§4). `members` had never run, so nothing hosted was affected.

## 2. Red first (the seam tests against the base launch-v1.sh)

`test_lab_release.py` ran with TOOL pointed at the base `launch-v1.sh` (scratch copy, deleted): 8 failed. Every action refused with "this checkout carries 0060_… (its newest migration is not the re-proven 0059)" — the THROUGH gate. The patches were still under `infra/lab/rollout/`. Against the new tool before it existed: 8 failed (FileNotFoundError).

## 3. Checks (exit codes, counts)

| command (from apps/infrx-api unless noted) | exit | result |
|---|---|---|
| `uv run --frozen pytest -q tests/i/test_known_good_proof.py` (base, before) | 0 | 13 passed |
| `uv run --frozen pytest -q tests/i/test_known_good_proof.py tests/i/test_lab_release.py` | 0 | 21 passed |
| `uv run --frozen pytest -q tests/i --ignore=tests/i/test_mutants.py --ignore=tests/i/{lab_control,lab_eval,lab_pipeline,lab_rollout}` (the PG/docker subdirs are other keys' and untouched) | 0 | 261 passed, 1 skipped, 1 xfailed |
| `uv run --frozen pytest -q tests/i/test_mutants.py -k "well_formed or every_case"` | 0 | 2 passed |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/test_mutants.py -k 'launch or lab_release or known_good or hosted_migrate'` | 0 | 68 passed, 0 survivors (282 s) |
| `uv run --frozen pytest -q tests/i/test_mutants.py` (default subset + guards) | 0 | 59 passed (374 s) |
| `uv run --frozen ruff check tests/i/test_lab_release.py tests/i/test_known_good_proof.py tests/i/mutants.py` | 0 | clean |
| `bash -n` lab-release.sh, launch-v1.sh | 0 | |
| `python3 research/plan/scripts/validate_plan.py` (repo root) | 0 | PASS |
| scratch dry run: a clone at `5c6c9e60`, `ssm.sh` replaced by a stub that exits 1, PATH `aws` stub, `lab-release.sh box` | 1 | stopped at the first ssm call: `ssm.sh STUB (first call) infra/lab/rollout/lab-checkout.sh RELEASE=5c6c9e60…` |

## 4. members on the real schema (i6, scratch, by hand)

`INFRX_D_TASK=i6`, `tests/d/pgharness` (plain image + shim), `migrations.sql_for()` 0001–0059 (89 infrx tables). Two `auth.users`: `a@x.io` and `o'brien@x.io`. The PATH `aws` stub answers the i6 DSN. `lab-release.sh members` ran with `TESTER_EMAILS="a@x.io o'brien@x.io nobody@x.io"` and `OPERATOR_NAME="O'Neil"`, twice:
- both runs exited 0 and printed the same two rows; the rerun inserted nothing;
- the org and both memberships carry `operator:O'Neil P-08 P-08:i6-dry`;
- `nobody@x.io` is absent from the output;
- the DSN never appeared in the output;
- verdict: `MEMBERS-PG PASS`.

The base statement on the same schema fails with `UndefinedColumn column p.id does not exist`. The container was removed at exit. The scripts are scratch files and are not committed (the committed suite pins the SQL shape against a stub psycopg).

## 5. Default mutant subset

`tests/i/test_mutants.py` without INFRX_MUTANTS: 59 passed, exit 0. The layout change (the evidence patches copied) keeps every pristine baseline green.

## 6. Mutants added (29), each named by a test_lab_release / known-good case

- **hosted_migrate_*:** pending_short, anchor_stale, post_check_stale.
- **lab_release_*:**
  - through_guard_back, window_offered;
  - l0_dropped, box_without_supabase_url, control_dsn_renamed, edge_before_control;
  - token_stored_before_whoami, token_fixed_home_path, token_world_readable, token_kept;
  - scope_unset, scope_ignored, env_name_dropped;
  - members_dsn_on_argv, members_dsn_name_fixed, members_email_spliced, members_operator_spliced, members_without_emails, members_operator_default, members_org_id_column;
  - main_pushes_the_tip, main_not_fast_forward;
  - preflight_reads_a_value, preflight_any_release;
  - shim_keeps_vercel, shim_silent.

Anchors retired: `launch_v1_0059_hosted_at_0051` and `launch_v1_pushes_the_tip`. Their invariants are now covered by `hosted_migrate_anchor_stale` and `lab_release_main_pushes_the_tip`. The next window's reviewed edit must update the three `hosted_migrate_*` anchors in the same commit (08 §2).

## 7. Not done / deviations

- **LAB-03 deploy half:** not done. lab-B's `LAB_API_URL` fallback is not on base `08983639`, so the six names stay in `vercel_lab()`. Follow-up, once lab-B merges:
  - replace the six `LAB_*_URL=` entries with `"LAB_API_URL=https://lab-control.callbill.ai"`;
  - add a `vercel env rm` of the six old names;
  - update `test_lab_release__web_deploys…`'s expected env and the `lab_release_env_name_dropped` anchor.
- **BOX_RELEASE:** only `window()` used it. `box()` never did, so it leaves the tool with `window()`. Its derivation (fetch, then `origin/main`, DT-13) is in 08 §2's next-window commands. It is not a tool input or a test pin.
- **DT-12:** `test_lab_rollout_steps.py` is owned by infra-libs, so its docstring fix and the anchor-derived `--hosted-at` go in as a wiring request (WR-W6-LRT-1). The sed that edited it went away with `window()`. The anchor derivation is also in the replacement known-good case.
- **`preflight`:** kept (read-only half), as the brief's "preflight()'s window half" implies.

## 8. Wiring requests

- **WR-W6-LRT-1 (infra-libs, `apps/infrx-api/tests/i/lab/test_lab_rollout_steps.py::test_ldp__todays_hosted_migrate_carries_the_reviewed_patch`):**
  - Docstring: replace `(hosted at 0051 since 2026-09-29: EXPECTED_PENDING 0052-0056, its W7 post-check `*"0056 lab_control_grants"`)` with `(--hosted-at is read from hosted-migrate.sh's HOSTED_APPLIED anchor, so the case follows each window's reviewed edit without a sed)`.
  - Body: add `hosted_at = re.search(r'case "\$HOSTED_APPLIED" in \*"(\d{4}) ', (REPO / "infra/rollout/hosted-migrate.sh").read_text())[1]` and pass `"--hosted-at", hosted_at` in place of `"--hosted-at", "0056"`.
  - Proof: the case stays green, and the existing hosted-migrate mutants kill it.
- **WR-W6-LRT-2 (docs-state, `CLAUDE.md` Commands, `infra/rollout/README.md` §6, `infra/lab/app/README.md`):** name the maintained tool as `infra/lab/rollout/lab-release.sh preflight|box|web|members|main` (inputs RELEASE, SUPABASE_URL, VERCEL_SCOPE, TESTER_EMAILS, OPERATOR_NAME, OPS_DSN_PARAM, WINDOW). `launch-v1.sh` is a deprecated shim, removed in W7.
- **WR-W6-LRT-3 (plan-ledger, carried-work register):**
  - the operator step `members` is now `TESTER_EMAILS=… infra/lab/rollout/lab-release.sh members`, and `main` is `lab-release.sh main`;
  - remove the shim next wave;
  - LAB-03 deploy half: the env switch after lab-B, then the coordinator sets `LAB_API_URL` on Vercel.

## 9. Proposed ruling (unnumbered)

"Hosted migration windows carry no patch file and no between-window strict xfail. The window is one reviewed commit editing hosted-migrate.sh's EXPECTED_PENDING, HOSTED_APPLIED anchor and W7 post-check, together with the three `hosted_migrate_*` mutant anchors. `tests/i/test_known_good_proof.py`'s anchor case pins EXPECTED_PENDING to the tree. This supersedes R264 and KGR4-RV-1's patch-order case."

## 10. Estimate (remaining, this lane)

- optimistic 0.5 h / likely 1 h / pessimistic 2.5 h; confidence medium.
- Basis: the code and tests are done. What remains is the LAB-03 env switch after lab-B merges (~0.5 h incl. the mutant re-run of the web case), the shim removal next wave, and any review round.

## 11. Coordinator rulings

- **R269** (08-contracts §10, appended after R268): a hosted window carries no patch file and no between-window strict xfail; it is one reviewed commit on the integration tip editing hosted-migrate.sh's three window lines together with the `hosted_migrate_*` mutant anchors, run through lab-migrate.sh from that tip; between a migration landing and its window, the commit adding NNNN carries that edit or a strict xfail with an owner and an expiry; R264's launch/window-<THROUGH> branch rule otherwise stands. Supersedes R264's patch-file and xfail clauses and KGR4-RV-1. This is §9's proposal extended with the between-states sentence (lens LRT-RV-3).

## 12. Merge

- Merged at lane head `f4cdfb13` onto `87637f37` as merge #71 on `codex/w5-merge-71` (verdict ACCEPT; lenses ACCEPT / ACCEPT_WITH_FIXES, minors only).
- **Conflict** `infra/lab/rollout/launch-v1.sh`: the tip's merge-#69 comment edit ("packages/shared, a link: dependency", R268) vs the lane's 3-line shim. The lane's shim was taken, and the comment edit is carried into `lab-release.sh` `vercel_lab()` (the lane's copy still said "a file: dependency").
- **LRT-RV fixes applied** (wirings commit):
  - LRT-RV-1: the bad-paste run now asserts the scratch TMPDIR is empty, plus mutant `lab_release_token_trap_dropped` (the EXIT trap removed), named by the token case.
  - LRT-RV-2: new case `test_lab_release__box_defaults_release_to_the_claude_consumer_v1_tip` (RELEASE unset, HEAD moved past the branch; the L0 line carries the claude/consumer-v1 sha), plus mutant `lab_release_release_default_head`.
  - LRT-RV-3: 08 §2 step 2 names test_lab_rollout_steps.py's literal `--hosted-at` until WR-W6-LRT-1 lands. R269 carries the between-states sentence.
  - LRT-RV-4: `OP=${OPERATOR_NAME:-$(git config user.name || true)}; need OP`. Without a git user.name, set -e no longer exits silently; it exits 2 with "set OP". The `lab_release_members_operator_default` anchor follows.
- **The members `p.id` → `provider_org_id` fix is a real bug fix**, not a refactor. The base statement could never have run on 0001–0059 (UndefinedColumn, §4). `members` had never been run, so nothing hosted was affected.
- **WR-W6-LRT-3 applied**: register rows 1 and 2 name `TESTER_EMAILS=... infra/lab/rollout/lab-release.sh members` and `lab-release.sh main`. New rows: 75 (launch-v1.sh shim removal, W7) and 76 (LAB-03 deploy half after lab-B: the coordinator sets the Vercel var and runs `vercel env rm` on the six names). A log line was added.
- **Carried:**
  - WR-W6-LRT-1 goes to the infra-libs merge (owner of `test_lab_rollout_steps.py`).
  - WR-W6-LRT-2 goes to the docs-state merge (owner of CLAUDE.md and the READMEs).
  - For the infra-libs merge (lens LRT-RV-1, first lens): `tests/i/mutants.py` will conflict with infra-libs' INFRA-05/06 blocks. Resolve it there by keeping both lanes' anchors.

## Verification log

- 2026-10-01T03:15Z: written by the lab-release-tool lane at code head 5c6c9e60.
- 2026-10-01: §11 Coordinator rulings (R269) and §12 Merge appended at merge #71 on codex/w5-merge-71.
