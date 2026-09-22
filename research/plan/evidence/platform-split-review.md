# Two-platform amendment — documentation review

> Historical documentation-only review before the wave-2 pull. Current counts, implementation state and validation are in [the wave-2 audit evidence](wave2-platform-audit.md). Statements below about 68/62 tasks and unseen remote work do not describe the current imported tree.

Date: 2026-09-21. Baseline: `25b9829`. Scope: product architecture, requirements, roadmaps, implementation amendment and handoff. This report is not application test or production evidence.

## Observed state

Local main was clean at the original documentation-package commit before this work. One older detached worktree was also clean. The user confirmed implementation is running on another system. No remote implementation commits, applied schemas or live deployments were inspected. Local worktree observations do not establish that implementation's status.

## Checks performed

- The revised manifest retains all 45 original task IDs and maps each in the implementation impact table.
- Six mixed task records are retained as superseded for scheduling. There are 68 total records and 62 active tasks, including split successors and new product work.
- Active task IDs are unique, dependency references resolve to active tasks, and the combined start/integration dependency graph is acyclic.
- The consumer E4 pilot dependency closure has 36 tasks and contains no Lab tasks. Optional enabled capture/callbacks still require their own feature gates.
- All manifest handoff destinations and replacement-task references resolve. Required test IDs are defined in the verification document.
- Local Markdown destinations are checked, including balanced parentheses in Next.js route paths; historical placeholder link examples are excluded.
- Documentation whitespace/diff checks and the session-handoff validator are run before completion; actual final result is recorded below.

## Review findings incorporated

1. Separated consumer inference and provider improvement scope, navigation and independent release gates.
2. Replaced zero-balance signup with one-time individual entitlement; added concurrent issuance/backfill and org-change tests.
3. Separated CREDIT from baseline USD; documented original-regime job draining, preserved legacy statements and affected-account cutover decisions.
4. Added explicit provider roles, trusted model ownership, source-data purpose grants and revocation; model ownership does not imply customer-content access.
5. Kept consumer usage available from authoritative accounting without a trace/judge backend. Split mixed task dependencies to enforce this.
6. Kept migrations/runtime shared; moved provider UI destinations only in the plan. Preserved unseen implementation through a cross-system reconciliation handoff.
7. Specified metered provider preview wallets separately from individual grants and external judge/training USD budgets.
8. Kept OpenAI-style compatibility capability-specific; separated output SSE, live input windows and robotics sessions.
9. Recorded longer-term Lab work as phased packages requiring later decomposition, rather than claiming every training/robotics feature is implementation-ready today.

## Not executed

Application tests, migrations, builds, GPU benchmarks, cloud operations, paid inference/evaluation and remote-worktree integration. No runtime files, dependencies or infrastructure are changed by this documentation task. `apps/lab` contains a README scaffold only.

## Final validation

- Passed: 68 unique task records, 62 active tasks, acyclic combined dependency graph, all successor and handoff references valid.
- Passed: E4 closure has 36 tasks and no Lab prerequisites; all 45 original task IDs mapped; every test ID has an oracle.
- Passed: 337 local Markdown destinations across 44 changed/new Markdown files; no missing destination. This custom link check complements the handoff validator, which does not inspect Markdown-link targets itself.
- Passed: `git diff --check`; change inventory contains only Markdown and the documentation manifest.
- Passed: session-handoff validator **100/100**, no TODO placeholders, required sections complete and no potential secrets detected. This is a heuristic check, not a comprehensive repository security audit.

Production credit rates, affected legacy-account transition and actual remote implementation state remain explicit release/integration inputs; none is inferred from this review.
