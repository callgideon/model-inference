# Coordinator workflow

Read [current state](../../STATUS.md), [launch closure](26-launch-readiness-review-2026-10-01.md) and [the implementation index](README.md). Do not dispatch from old session records or restart completed waves.

1. Reconcile the fetched main, active worktrees, operating window and latest evidence. Record the committed integration base and actual target branch; no old tool-specific branch name is mandatory.
2. Select a concrete existing task/carried item with an observable exit. Consumer acceptance/public onboarding have priority; provider enablement is separate.
3. Assign exclusive owned paths. One owner writes migrations. Coordinate shared contracts, composition roots, dependency locks, task metadata and final integration.
4. Independent modules may use parallel isolated worktrees. Shared GPU, hosted migration and production fault/load work are serialized by environment ownership.
5. Review the actual adapter path, focused regression and relevant real-service checks. An implemented task, fixture-only UI, build or health response does not prove a deployed workflow.
6. Integrate in dependency order. Preserve evidence and status provenance; update [tasks.json](tasks.json), the [carried register](consumer-v1/10-carried-work-register.md) and [STATUS.md](../../STATUS.md) only to the extent established.
7. Record release decisions with exact source/runtime/schema/model/profile/card identity, environment, result, skips, remaining risks and owner. Keep consumer/backend/Lab gates distinct.
8. Validate the manifest/ledger and links, then hand over by updating the existing state/index and the assigned brief. Do not create another dated resume chain.

Follow [execution rules](03-execution-protocol.md), [evidence format](evidence/README.md) and the relevant operating runbooks. Existing user authorization governs operations; ordinary local analysis, documentation and verification do not acquire a new approval requirement from this guide.
