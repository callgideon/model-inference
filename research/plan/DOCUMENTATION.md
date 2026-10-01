# Documentation policy and retired entry points

`STATUS.md` is the current-state entry point. Root/component READMEs describe present behavior and supported commands. `CLAUDE.md` applies to all agents. The planning index routes tasks to requirements, current gaps and evidence.

## What remains authoritative

- Product requirements, executable contracts, acceptance definitions and task-linked implementation briefs remain. Future requirements are not claims that their features are live.
- Dated test/operating reports and raw evidence remain as provenance. Their historical branch, path and line citations are not current operating instructions.
- The task graph, gate statuses and applied migrations are not changed by this cleanup. The generated ledger is regenerated from the same tasks.
- The progress overlay is reconciled at revision 395: reviewed main/App identity 252f3ea8, merged W6 lanes complete, old estimates and local assignments retired from the current display, and E4C awaiting its recorded outcome. The standard ingester explicitly rejected 16 malformed historical updates; their files and rejection reasons remain. No ETA or release acceptance is invented.
- Rollback evidence references formerly pointing to handoff 20 now point to the original session-02 operating record, which records both the non-serving 27af05a release and the 4226315 serving smoke. No rollback eligibility or schema proof changed.

## Removed session documents

The following documents were removed from the working tree on 2026-10-01 after their current facts were consolidated in [STATUS.md](../../STATUS.md) and [review 26](26-launch-readiness-review-2026-10-01.md). Git retains the originals; old Markdown citations now use the immutable blob at `252f3ea8fda0d144fe03151840bb232fb1c76633`, preserving the date/facts of the citation rather than pointing them at today's state.

```sh
git show 252f3ea8fda0d144fe03151840bb232fb1c76633:HANDOFF.md
git show 252f3ea8fda0d144fe03151840bb232fb1c76633:research/plan/25-planning-handoff.md
```

| Removed document | Replacement for current use |
|---|---|
| `.claude/handoff/wave3/PREP-WORKER.md` | STATUS.md → planning index / current review |
| `HANDOFF.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260922T1739Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260922T2215Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T0041Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T0149Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T0234Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T0339Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T0357Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T0512Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T0655Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T1015Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T1125Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T1300Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T1330Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T1500Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T1620Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T1820Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T2130Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260923T2320Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260924T0030Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260924T0105Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260924T0215Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260924T0545Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260924T1010Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260924T1700Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260924T1745Z.md` | STATUS.md → planning index / current review |
| `research/plan/handoffs/operational/HANDOFF-20260924T2115Z.md` | STATUS.md → planning index / current review |
| `research/plan/16-fresh-session-handoff.md` | STATUS.md → planning index / current review |
| `research/plan/20-platform-handoff-2026-09-24.md` | STATUS.md → planning index / current review |
| `research/plan/24-consumer-v1-session-handoff.md` | STATUS.md → planning index / current review |
| `research/plan/25-planning-handoff.md` | STATUS.md → planning index / current review |
| `research/plan/25-state-2026-10-01.md` | STATUS.md → planning index / current review |
| `research/plan/PLATFORM-SPLIT-HANDOFF.md` | STATUS.md → planning index / current review |

## Retained brief and research policy

The files under `research/plan/handoffs/` that remain are module requirement/acceptance briefs referenced by tasks.json, not old session continuation prompts. Programs 12/18/22 and amendments retain task-linked acceptance detail; their original dispatch sequence is no longer current. Start with the planning index.

Do not manufacture a new historical result while consolidating docs. Use explicit dates, release identities and attribution; preserve failures/skips and the distinction between implemented, deployed, tested and accepted. New evidence updates STATUS.md and its owning register/log, rather than another dated handoff chain.

## Verification

Verified locally on 2026-10-01:

- Manifest/ledger/link validation: PASS, all 133 tasks; task records/dependencies/statuses and release-gate definitions unchanged.
- Tracker validation: PASS, 257 lanes, zero errors/warnings. Candidate gate evidence and decisions are unchanged.
- `apps/infrx-api/.venv/bin/python -m pytest -q research/plan/scripts/test_validate_plan.py research/plan/scripts/test_progress.py tests/integration/backend/recovery/test_runbooks.py`: 65 passed, including the existing carried-row-78 regression.
- From `apps/infrx-api`, `uv run --frozen pytest -q tests/i/test_known_good_proof.py`: 13 passed. Only two rollback evidence paths changed; no eligibility or schema proof changed.
- All 34 deleted documents are inventoried above and present at the immutable historical commit. A cross-repository Markdown scan found no links to removed local paths and no broken fragments into modified documents.
- No SQL migrations changed; whitespace validation passed. The only application source edit changes a stale comment, with no behavior change.

No runtime deployment, production mutation or interference with the reported E4C window was performed. The checks above validate this documentation cleanup; the broader runtime findings and verification limits remain in review 26 and STATUS.md.
