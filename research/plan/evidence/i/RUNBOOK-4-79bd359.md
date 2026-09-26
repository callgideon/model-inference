# RUNBOOK-4: E4C window documents (lane evidence)

- Lane RUNBOOK-4 (task E4C, support lane before the RELEASE freeze), branch `codex/runbook-4`, worktree `.claude/worktrees/codex-runbook-4`.
- Base `b967a033`; docs head `79bd359b` (this file and the coordinator update are committed after it).
- Docs only. No script, step or test changed. No docker, no i8 stack, no box, AWS, SSM or hosted access.

## Changed sentences (before → after, source, pin)

| # | File:line (at head) | Before | After | What makes it true | Pinned by |
|---|---|---|---|---|---|
| 1 | `infra/runbooks/rollout.md:207-209` (§2 W7) | "The known-good proof reaches 0025 (KNOWN-GOOD-PROOF-2; …): extend it to 0026 before relying on R2 after a 0026 apply." | "reaches 0026 for both targets (KNOWN-GOOD-PROOF-3; `infra/rollout/known-good.json` `schema_proof.through`; …): extend it to 0027 before relying on R2 after a 0027 apply." | `known-good.json`: 4226315 and bda1586 `schema_proof.through` = `"0026"`, evidence `KNOWN-GOOD-PROOF-3-af552ed.md` | Cites the file (no text test pins it) |
| 2 | `infra/runbooks/rollout.md:304-306` (§3 Known-good record) | "reaches 0025 (…, KNOWN-GOOD-PROOF-2-3f7df77.md); a migration beyond 0025 needs the proof extended" | "reaches 0026 (…, KNOWN-GOOD-PROOF-3-af552ed.md); a migration beyond 0026 needs the proof extended" | Same as 1 | Cites the file |
| 3 | `models/marlin2b/results/E4C-runbook.md:43` (§1 step 1) | "W7 takes hosted from 0001–0018 to 0001–0025" | "… to 0001–0026" | rollout.md:182 (`apply` prints 0019-0026) and :197 (`applied: 0001 … 0026`); the tree's newest migration is 0026 | rb09 already asserts `0001-{newest}` in rollout.md W7 (same fact) |
| 4 | `models/marlin2b/results/E4C-runbook.md:63` (§1a) | "until W7 applies 0019–0025" | "until W7 applies 0019–0026" | Same as 3 | Same as 3 |
| 5 | `rollout.md` W7f step cell | "(`--freeze-only` is not needed)" | Adds a check before H2 (F3). Rerun `10-inventory.sh` after W5. Its `== units` lines must show `marlin2b-gateway` and `infrx-worker` inactive and no `infrx-canary.timer` active. Otherwise, G8 step 3 applies (`--freeze-only` first) | `deploy/drain.sh` pause stops `$runtime_units`; `deploy/lib.sh:15` gives `infrx-worker marlin2b-gateway`. `10-inventory.sh` lists `'infrx-*'` units with `--all`. `72-observe-install.sh:82` enables the canary timer only with `P24_APPROVED` | Cites the files |
| 6 | `rollout.md` W10b step cell | (none) | CS-6/F4: at the window the step runs before the edge reopens, in the order W10 exit 0 → `95-maintenance.sh RELEASE=$RELEASE` → this step → `drain.sh resume` (reopens only after both `/readyz`) → W11 | `95-maintenance.sh` = `drain.sh pause`. That stops worker and gateway only (`lib.sh:15`; the engine stays up). `drain.sh resume` starts them, runs `wait_ready`, then `caddy_site Caddyfile` | rv02 (the row after the install still names `55-runtime-login.sh`); rb12 (last-column parse unchanged) |
| 7 | `rollout.md` W10b verify cell | "a rerun prints `unchanged`" | "…, but it has still re-set both roles' SCRAM verifiers on hosted: the ALTER ROLE (`55-runtime-login.sh:67-68`) runs before the no-op check (`:89`). Rerun it to recover, never just to verify (CS-4)" | `55-runtime-login.sh:61-68` (the loop with ALTER) comes before `:89` `cmp -s` | Cites the file |
| 8 | `rollout.md` W10b rollback cell | "exit 4 puts the previous env file back itself; else …" | Adds the exits the header does not name. F2: a missing SSM parameter exits with aws's code, before any SQL (`:42-43`). CS-5: exit 3 also means not both DSNs came back (`:79-80`). CS-5: a failed `systemctl restart` (`:100`) has no put-back, so copy back the newest `/var/backups/infrx/runtime-login-<UTC>.env`. F5: remove a leftover staged `/etc/marlin2b-gateway.env.XXXXXX` (`:85`; removed only on the cmp and envcheck branches) | `55-runtime-login.sh`: lines 22-25 are the header; 39 is the trap (removes `$work` only); 42-43, 79-80, 85, 89-95, 97-100 | Cites the file. rb12 still passes: the cell has no `91-abort`/`90-revert`/`R1`/`R2`/`R4` token |
| 9 | `E4C-runbook.md` §4 ledger-half bullet | (none) | F2: a missing `pg_journal_url` exits with the aws CLI's code, not the launcher's 2 (`e4c-certify.sh:33-35` under `set -e`), before docker runs | `e4c-certify.sh:8` `set -euo pipefail`; `:33-35` is `OPS6543=$(aws … \| sed …)` | test_rollout's launcher case (its §4 command regex still matches: the bullet sits above the fenced command) |
| 10 | `infra/app/README.md:126` (S1) | "(anonymous once WR-I2A-1 is applied; until then signed in, in a browser)" | "(anonymous: `/api/version` is public, `apps/app/lib/supabase/middleware.ts:5`, WR-I2A-1 `416da075`)" | `middleware.ts:5` has `PUBLIC = ["/api/version", …]`; `416da075` is an ancestor of the base; `apps/app/tests/a/public-routes.test.ts:51` pins it | `public-routes.test.ts:51` (the code fact; the README line has no text test) |
| 11 | `research/plan/evidence/coordinator/E4C-readiness-2026-09-26.md` log | (none) | One line saying §2h's runtime-login gap (line 20) is superseded by rollout.md W10b (`55-runtime-login.sh`) and the SSM parameters the user created on 2026-09-26 | `rollout.md` W10b row; `15-pending-inputs.md:187` (the user's `unblock-coordinator.sh` run created `infrx_runtime_password`, `infrx_monitor_password`, `monitor_database_url`) | validate_plan (links) |

Verification-log entries were appended to `rollout.md` and `E4C-runbook.md`, following the repository rule. `infra/app/README.md` is owned for its S1 line only, so it got no log entry.

Deliberately not changed: `rollout.md:207` and `E4C-runbook.md:89`, "`migration_version` 0025 or newer". That is E4C's freeze floor, not the proof's reach. `rollout.md:205` ("0024/0025 land from `codex/d10-merge-2`") and the verification-log history were also left unchanged.

## Commands (worktree root; `api` = `apps/infrx-api`)

| Command | Exit | Result |
|---|---|---|
| `make api-env` | 0 | pinned env synced |
| base, api: `uv run --frozen --no-sync pytest -q -p no:cacheprovider` on each of `tests/i/test_rollout.py`, `tests/w/test_p25_runbooks.py`, `../../models/marlin2b/tests/test_profile.py`, `../../tests/integration/backend/recovery/test_runbooks.py`, `../../tests/integration/ops/test_runbook_reversal.py` | 0 ×5 | 12 / 2 / 25 / 13 / 3 passed (55) |
| head, the same five | 0 ×5 | 12 / 2 / 25 / 13 / 3 passed (55) |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS; 943 local Markdown links across 271 documents |
| `git diff --stat b967a033..HEAD` | 0 | owned paths only (the four documents, this file, the update JSON) |

No test pinned a phrase that moved: grep over the five test files, `apps/infrx-api/tests/i/mutants.py` and the console tests found no `0025`, `reaches`, `signed in` or `api/version` README anchor. So there was no fails-before/passes-after pair, and no test changed.

## Wiring requests

1. `infra/rollout/README.md` row 8b (not owned) repeats W10b. Add one sentence: "At the window: `95-maintenance.sh RELEASE=$RELEASE` right after step 8's exit 0, then this step, then `drain.sh resume` (E4C-RUNBOOK-2 CS-6/F4; rollout.md W10b)". Without it, an operator who follows the README restarts the runtime with the edge open.
2. (Optional, script lanes) The script fixes behind CS-4, CS-5, F2, F4's `--max-time` and F5 are still in `55-runtime-login.sh` / `e4c-certify.sh`. This lane only documented them.

## Open issues

- The window order W10 → 95-maintenance → W10b → `drain.sh resume` has not been run. It is composed from `drain.sh` and `lib.sh` as read.

## Remaining effort

Optimistic 0.1 h, likely 0.25 h, pessimistic 0.75 h; confidence high. What remains is review and any wording fix it asks for.

## Verification log

- 2026-09-26: written at docs head 79bd359b; the commands above ran in this worktree.
