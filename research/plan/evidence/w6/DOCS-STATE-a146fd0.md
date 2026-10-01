# W6 docs-state — evidence at a146fd07 (base 08983639)

Lane docs-state of wave 6 (behaviour-preserving clean-up). Findings: INT-05, INFRA-07,
DT-02/03/07 (docs half)/08 (state half)/09/14/17, LAB-11 (docs half). Branch
`codex/w6-docs-state`, worktree `.claude/worktrees/codex-w6-docs-state`. Docs only: no code, no
test, no migration, no docker, nothing hosted/box/AWS/Vercel touched.

## Commits (base 08983639 → a146fd07), in the brief's order

| Commit | Task |
|---|---|
| be3e3a16 | `research/plan/25-state-2026-10-01.md` (new, 63 lines): what runs where, accepted gates, pending in order, carried work, reading order, the real make targets |
| 9e2b1aff | CLAUDE.md: dated state entry point; repo description (apps/lab = the live provider Lab); branch note (main moves only by the operator); Commands: lab-* targets, Docker gates outside check (P-21 band), gate switches, standalone installs, `ssm.sh` + the Lab release tool; log line |
| c597a1c4, ff24b513, 10eba6f7, b3432569 + f2cd712e | README.md, HANDOFF.md, research/plan/README.md, research/platforms/README.md: one dated state line each replacing the stacked 2026-09-2x banners; history kept (links in one "historical" line; log lines appended) |
| ec936247, b6df2c45, fa19915e | apps/lab, apps/app, apps/infrx-api READMEs: Lab rewritten as the built/deployed product (pages→families→backends verified against `app/(provider)/*/page.tsx` imports; env names from `.env.example` + code); App: dated live state, signup routes, invite-only marked pre-v1; API: dated state, legacy diagram labelled, `INFRX_MODE`/`DATABASE_URL`/`S3_MEDIA_BUCKET` rows (from `infrx/config.py`, `deploy/preflight.py`), CLI and test-environment sentences to the code |
| bbb1d47a | infra/lab/app/README.md: dated enabled state; `lab.json` `enabled` documented as the repository default (only `tests/i/lab/test_lab_packaging.py:173` reads it; the box switch is the marker 40-lab-control.sh creates); control env owner `ubuntu` at §2 and the §4 manual command; §3 origins cite P-08 |
| d6f1b720 | infra/rollout/README.md: preamble = what has run and where recorded; §6 "The Lab on the box" (launch-v1.sh, lab-migrate.sh, lab-checkout.sh, steps 10–90 with exits, from each script's header) |
| 0120a829 | infra/runbooks/README.md: inline `ssm()` copy → `infra/rollout/ssm.sh`; post-cutover unit list (from `apps/infrx-api/deploy/*.service`, `deploy/lab/`); runbook-08 row; restore row cites the windows' verified dumps |
| adde95fe | runbook 08: head says it has run; "State 2026-10-01" block (both windows + digests, SSM, box L0–L6 + L5d, L7 skipped WR-LDP-7, Vercel from the repo root, pending); step tables unchanged |
| e785f4df | ENVIRONMENT.md (DT-14): six standing strict xfails enumerated with file:line, owner, trigger; owner/trigger convention |
| a146fd07 | DT-17: 25 root `HANDOFF-2026*.md` → `research/plan/handoffs/operational/` (git mv, 0 content change); `HANDOFF-20260924T2115Z.md` stays at the root (plans 16/21/24 link it; wiring request WR-W6DS-3); pointer line in HANDOFF.md |

Line counts before → after: CLAUDE.md 127→144, README.md 91→92, HANDOFF.md 407→403,
research/plan/README.md 57→55, research/platforms/README.md 38→38, apps/app/README.md 149→156,
apps/lab/README.md 24→72, apps/infrx-api/README.md 214→229, infra/lab/app/README.md 171→177,
infra/rollout/README.md 171→200, infra/runbooks/README.md 104→92, 08 375→391,
tests/integration/ENVIRONMENT.md 181→195, 25-state 0→63. Total `git diff --shortstat`: 39 files,
+327/−129 (26 of the files are pure renames).

## Oracles and checks (exit codes, counts)

1. **Citation oracle (red first)**: `python3 scratchpad/cite_check.py research/plan/25-state-2026-10-01.md`
   (every 7–64-hex SHA/digest and every https origin in the state file must occur in the session-03
   record, 09 or the audit). First run: exit 1, `missing: ['0e8449a4ffca29bab']` (the instance id,
   which none of the three sources carries) → the state file names "the pilot box (instance id in
   CLAUDE.md)" instead → exit 0, `checked 11 shas/digests + 3 urls; missing: []`.
2. **Stale-wording grep** `grep -rn "no application yet\|preparation only\|README scaffold" CLAUDE.md README.md HANDOFF.md apps/*/README.md infra/**/README.md` (globstar):
   before = 6 hits in 5 files (HANDOFF.md:75, apps/lab/README.md:7, infra/lab/app/README.md:3 and
   :169, infra/app/README.md:3, infra/lab/observe/README.md:3; CLAUDE.md:22-23's "README\nscaffold"
   is split over a line and escaped the grep, fixed anyway). After = 3 hits:
   - `infra/lab/app/README.md:174` — the 2026-09-27 verification-log line ("preparation only,
     nothing hosted run"), true when written; kept: logs are appended, never rewritten (CLAUDE.md
     research rule; audit INT-05 "preserve history").
   - `infra/app/README.md:3`, `infra/lab/observe/README.md:3` — outside this lane's owned paths
     (the glob covers them): wiring requests WR-W6DS-1/2 with the exact text.
3. `python3 research/plan/scripts/validate_plan.py` after every commit: exit 0, PASS (final: 949
   local Markdown links across 456 documents; the 25 moved handoffs are now inside the checked tree).
   Moving all 26 handoffs first gave 3 broken links (plans 16, 21, 24 → `../../HANDOFF-20260924T2115Z.md`)
   → that one file stays at the root (WR-W6DS-3).
4. Local links of the owned docs validate_plan does not cover (`scratchpad/links.py`, the same
   parser): infra/rollout, infra/lab/app, infra/runbooks READMEs + ENVIRONMENT.md: 43 links, 0 broken.
5. Suites that read the edited docs (`cd apps/infrx-api && .venv/bin/python -m pytest -q -p no:cacheprovider
   tests/i/test_rollout.py tests/i/lab/test_lab_packaging.py ../../tests/integration/backend/recovery/test_runbooks.py
   ../../tests/integration/ops/test_i3_operations.py`): exit 1, 60 passed / 1 failed —
   `test_e4c_rb09_the_copy_is_seeded_with_hosted_s_own_applied_history`, **pre-existing and
   independent of this diff**: it reads `infra/runbooks/rollout.md` §W7 for `0001-<newest>` (0059)
   and that file is untouched here (`git diff --stat 08983639 HEAD -- infra/runbooks/rollout.md
   tests/integration/backend/recovery/` empty). Open item OI-1.
6. Mutant lists whose anchors name edited docs (`apps/infrx-api/tests/i/lab/mutants.py` R =
   infra/lab/app/README.md; `tests/i/mutants.py` readme_* = infra/rollout/README.md):
   - `INFRX_MUTANTS=all pytest -q tests/i/lab/test_mutants.py`: exit 0, 70 passed (0 survivors).
   - `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py`: exit 0, 470 passed, 4 skipped (I-HARNESS-KEY not-run skips: Docker/key-held mutants, visible by design; none anchors an edited doc), 1763.8 s; the two README-anchored mutants (`readme_inline_env_value`, `readme_inline_key_literal`) rerun alone with `-k readme`: 2 passed (killed).
7. `tests/i/test_observe.py -k every_alert_rule_names`: 1 xfailed (the DT-14 entry still stands).
8. Not run, with reason: `make api-test` in full (docs-only diff; every test that reads an edited
   file ran in 5–6), console-*/lab-* (no code under apps/ changed), any Docker gate (no key).

## Wiring requests (exact; not applied)

- **WR-W6DS-1** `infra/app/README.md:3-6` — replace the paragraph with:
  `**State 2026-10-01: this runbook has run.** The App is live since 2026-09-27 (first known-good release d3a99e01) and production builds from \`main\` = 41693d5d since the 2026-09-29 window (session-03 record lines 455, 525; research/plan/25-state-2026-10-01.md). The I2A-PREP lane wrote this runbook and the code it relies on (\`apps/app/lib/deploy/\`, \`instrumentation.ts\`, \`next.config.ts\`, \`app/api/version/route.ts\`, tests \`apps/app/tests/i2a/\`). Every item marked **[OP]** is held by the operator.`
  (keep the log; append a 2026-10-01 line). Test: `tests/integration/ops/test_i3_operations.py` stays green (it needs "operations.md" in the file).
- **WR-W6DS-2** `infra/lab/observe/README.md:3` — `**Disabled by default. Operator-run, preparation only.**` →
  `**Disabled by default — still disabled on 2026-10-01** (the Lab control service is ON on the box, the observe units are installed inert; runbook 08's state block). **Operator-run; not yet run.**`
- **WR-W6DS-3** plans `research/plan/16-fresh-session-handoff.md`, `21-v1-consumer-readiness-review-2026-09-24.md`, `24-consumer-v1-session-handoff.md`: `(../../HANDOFF-20260924T2115Z.md)` → `(handoffs/operational/HANDOFF-20260924T2115Z.md)`; in the same commit `git mv HANDOFF-20260924T2115Z.md research/plan/handoffs/operational/`, and in HANDOFF.md's pointer line point the 2115Z link at `research/plan/handoffs/operational/HANDOFF-20260924T2115Z.md` and drop the clause "stays at the root while plans 16, 21 and 24 link it there". Proof: validate_plan.py PASS.
- **WR-W6DS-4** `infra/lab/app/lab.json` (unowned; coordinator): `"enabled"` is documentation, not configuration (read only by `tests/i/lab/test_lab_packaging.py:173`, never by a deploy step; the box switch is `enable_marker`). Keep `false`; amend `"comment"` to add: `"enabled" is the repository default, never read by a deploy step; the box's switch is enable_marker.`
- **WR-W6DS-5** `research/plan/consumer-v1/README.md` index: list 09 as entry 9 (today a log-style bullet at :18) and, once plan-ledger lands it, `10-carried-work-register.md` as entry 10; add a link to `../25-state-2026-10-01.md`.
- **WR-W6DS-6** (infra-libs) `infra/lab/rollout/steps/40-lab-control.sh:8` header `(root, 0600, by rename)` → `(ubuntu, 0600, by rename)`; and `apps/infrx-api/tests/i/lab/test_lab_rollout_steps.py:416-417` docstring → `hosted at 0056 since 2026-09-30: EXPECTED_PENDING 0057-0059, post-check *"0059 lab_control_grants_2"` (INFRA-07/DT-12 text).
- **WR-W6DS-7** (lab-B) `apps/lab/.env.example`: add the two dev-only flags it omits, `LAB_EVALS_PREVIEW` and `LAB_RELEASES_PREVIEW` (read at `lib/services/evaluation/port.ts`, `lib/services/rollouts/port.ts`), beside the two it lists (LAB-11).

## Open items

- OI-1: `tests/integration/backend/recovery/test_runbooks.py::test_e4c_rb09…` red at the base (rollout.md §W7 range text vs the tree's newest migration 0059); `infra/runbooks/rollout.md` has no W6 owner.
- OI-2: merge note — runbook 08's log tail is appended by this lane and by lab-release-tool (§2/§7): an append-only conflict, keep both lines.
- OI-3: when lab-B/lab-release-tool land, the Lab README's env and deploy lines (six URLs + `LAB_API_URL`; `launch-v1.sh` → `lab-release.sh`) were written to stay true either way; a one-line follow-up can drop the old names.

## Estimate (remaining for this lane)

optimistic 0.5 h / likely 1 h / pessimistic 2 h, confidence medium — basis: one review round over
14 docs (no code), plus applying WR-W6DS-3's HANDOFF.md line if the coordinator routes it back here.

## Fix round (2026-10-01, head 22bf779a)

- **1-DS-1 fixed.** `apps/lab/README.md:46` now says `(\`link:\` dependency; no reinstall after an edit there)`, matching lab-A's 4ccb4440 (R268) and `apps/lab/package.json` `link:../../packages/shared` on the tip; `research/plan/25-state-2026-10-01.md:18` now says "the `packages/shared` dependency is uploaded; it was `file:` at deploy time and is `link:` since R268".
- Oracle: `grep -rn '\`file:\`' apps/lab/README.md` 1 hit before, 0 after; 25-state's only `file:` is the dated historical clause.
- Merge note: `git merge-tree --write-tree 87637f37 22bf779a` still reports CONFLICT (content) in `apps/lab/README.md` (both sides rewrote the file). Resolve by taking this branch's version whole (`git checkout --theirs`/ours as seen from the merge); it now carries the tip's `link:` wording, so nothing from 4ccb4440 is lost.
- Reruns: `validate_plan.py` PASS (949 links, 457 documents); `pytest -q tests/i/lab/test_lab_packaging.py tests/i/test_rollout.py` 38 passed; `INFRX_MUTANTS=all pytest -q tests/i/lab/test_mutants.py` 70 passed (0 survivors); `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py -k readme` 2 passed (killed). Full `tests/i/test_mutants.py` not rerun: no anchored file other than the README mutants changed.

## Merge (merge #76, codex/w5-merge-76, 2026-10-01)

- Merged `codex/w6-docs-state` at **94d1b9cd** onto 70cf254f (`--no-ff`, merge commit 128a277a). Conflicts: `apps/lab/README.md` (both sides rewrote it; the tip's only change was `file:` → `link:`, which the fix round carries) → this lane's version whole; runbook 08's log tail (append-adjacency with lab-release-tool and merges #71/#72) → every line from both sides kept, the tip's first. `infra/lab/app/README.md` auto-merged with merge #74's `LAB_API_URL` row kept.
- Wirings (one commit): `apps/lab/README.md` env section names `LAB_API_URL` first with the six old names deprecated (lab-B, merge #74) and keeps `(\`link:\` dependency; no reinstall after an edit there)` (DS-1); its deploy line names `lab-release.sh web|box`. **WR-W6DS-1** `infra/app/README.md:3-6` and **WR-W6DS-2** `infra/lab/observe/README.md:3` as requested. **WR-W6DS-3** `HANDOFF-20260924T2115Z.md` git-moved to `research/plan/handoffs/operational/`; links re-pointed in plans 16, 21, 24, **and `research/plan/README.md:5`** (a fourth link the request did not list) and HANDOFF.md's pointer line; validate_plan PASS. **WR-W6DS-4** `lab.json` comment gains the `enabled`/`enable_marker` sentence; `LAB_API_URL` entry kept. **WR-W6DS-5** consumer-v1 README: the log-style 09 bullet folded into index entry 9 (entries 9/10 from merge #70 kept, no duplicate) and a link to `../25-state-2026-10-01.md`. **WR-W6DS-6** already applied on the tip (40-lab-control.sh:8 says `ubuntu:ubuntu` since merge #72; the test docstring is anchor-based) — nothing to do. **WR-W6DS-7** `apps/lab/.env.example` gains commented `LAB_EVALS_PREVIEW`/`LAB_RELEASES_PREVIEW` lines beside `LAB_PIPELINES_PREVIEW` (commented, so test_lab_packaging's `^NAME=` scan does not require a lab.json entry).
- Lens minors: DS-2 one statement on public signup in `apps/app/README.md` and `research/platforms/README.md`: **closed** — P-05 keeps `disable_signup true` (15-pending-inputs, I1B-inventory-2026-09-22.md:34), the session-03 record logs no X7 run, internal users are created by script (line 455). DS-3/DS-RV-2 `infra/rollout/README.md` §6 50-lab-role.sh exits `2/3: refused, nothing replaced; 5: a pending role refused by name after start; 4: started, not ready`. DS-4 CLAUDE.md: only lab-lint/lab-typecheck/lab-build fail fast naming the install (Makefile `LAB_INSTALLED`). DS-RV-1 Docker-gate port sentence → task-local key whose ports come from `infrx/contracts/tasklocal.py` (CLAUDE.md and 25-state). DS-RV-3 ENVIRONMENT.md notes `d/test_reads.py:138-157` reuses the three credit strict xfails. DS-RV-7 staging callback `https://infrx-lab-*-callgideon.vercel.app/auth/callback**` in infra/lab/app/README.md §3 and `lab.json:13`.
- Carried pointers: WR-W6-LRT-2 CLAUDE.md names `lab-release.sh preflight|box|web|members|main` with its seven inputs and `launch-v1.sh` as the deprecated shim (removed in W7); makefile-pins: `make api-lint`/`make api-typecheck` in CLAUDE.md's canonical targets and 25-state's `make check` list, the DT-16 wall-clock pointer in CLAUDE.md and ENVIRONMENT.md; WR-IL-4 confirmed (infra/lab/app/README.md §2 and §4 already say `ubuntu`).
- DS-RV-4 rewordings (only merged outcomes stated): `lab-release.sh` is named as current (lab-release-tool merged) in CLAUDE.md, apps/lab README, infra/rollout README §6, 25-state pending item 1 and runbook 08's head/State block (the spent window patches are in `research/plan/evidence/i/`). A grep of this lane's files for lab-E, api-L2–L5, certify-release, pgrestore-tests and lab-D deliverables found no sentence stating them as done (apps/infrx-api README's legacy chat diagram is "kept while it is mounted", true until api-L2).
- Pre-existing: `tests/integration/backend/recovery/test_runbooks.py::test_e4c_rb09_the_copy_is_seeded_with_hosted_s_own_applied_history` red (rollout.md's stale migration range; OI-1), unowned in wave 6 → register row 78 (consumer-v1/10).
