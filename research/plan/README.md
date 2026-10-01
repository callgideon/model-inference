# Implementation index

Start with [current state](../../STATUS.md), then the [launch review and production workflow](26-launch-readiness-review-2026-10-01.md). The consumer App/API are deployed; production acceptance and public onboarding remain open. The Lab is only partially operational. Current priority is consumer launch closure, not repeating completed foundation waves.

## What to use

| Need | Document |
|---|---|
| Actual deployment, gates and immediate gaps | [STATUS.md](../../STATUS.md) |
| Findings, priorities and production acceptance workflow | [Review 26](26-launch-readiness-review-2026-10-01.md) |
| Task IDs, dependencies and implementation status | [tasks.json](tasks.json), [generated ledger](17-task-ledger.md) |
| Reconciled task activity and release gates | [HTML tracker](evidence/coordinator/progress.html), [Markdown tracker](evidence/coordinator/PROGRESS.md); ETA remains unknown while operating evidence is incomplete |
| Carried omissions and enablement blockers | [Carried register](consumer-v1/10-carried-work-register.md) — its testing column describes the narrow Lab checklist |
| Latest operating events | [Operational log](consumer-v1/09-path-to-internal-testing.md), [session record](evidence/coordinator/2026-09-24-session-03.md) |
| Current product requirements | [App / Lab architecture and specs](../platforms/README.md) |
| Binding runtime contracts | [Contracts](01-contracts.md), [v2 mapping](01a-contracts-v2-map.md), [rulings](08-contracts-v1-encoding.md), [durable protocols](02-durable-protocols.md) |
| Worktree ownership and coordination | [Execution protocol](03-execution-protocol.md), [coordinator workflow](COORDINATOR.md) |
| Test/acceptance criteria | [Verification](04-verification.md), [evidence format](evidence/README.md), [consumer briefs](consumer-v1/README.md) |
| Decisions and unresolved external inputs | [Input/decision record](15-pending-inputs.md) — later dated decisions supersede earlier rows |
| Future hosting and optimization | [Roadmap 23](23-inference-hosting-roadmap.md) |

## Dispatch now

1. Check the existing E4C window and fetch its full report; the latest record has dataset-oracle and envelope failures. Ingest the outcome before scheduling another production workload.
2. Close the missing certification/operating cells and final combined verification. Use existing implementations and valid evidence.
3. Finish CAPTCHA/onboarding requirements and run the bounded real two-user App → Marlin → ledger journey.
4. Record invited-pilot and public-signup decisions independently of Lab enablement.

The [review](26-launch-readiness-review-2026-10-01.md) maps these to existing tasks and carried rows. No implementation status is promoted by documentation cleanup. Record owner, paths, base, integration target, environment and verifiable exit before assigning work.

## Retained requirements versus current state

Programs 12/18/22, amendment/revision briefs 09/11, improvement briefs 13 and the module files under `handoffs/` remain task-linked requirements and acceptance references. They are **not current session handoffs or deployment inventories**. Their original delivery bands describe the implementation history; consult the current manifest/evidence before selecting any task.

[Expansion gates](14-expansion-gates.md) and the hosting roadmap remain conditional. Consumer pilot acceptance does not depend on complete Lab evaluation/training, autoscaling, a second engine, payments or general custom-model hosting.

Historical root/session/resume handoffs have been removed, rather than kept as competing entry points. [Documentation policy and history](DOCUMENTATION.md) explains the retained proof and historical links.
