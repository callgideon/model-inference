# Infrastructure and operations

Read [STATUS.md](../STATUS.md) for the dated deployment inventory and open operating checks. The current platform is a single-GPU Marlin service plus the consumer App and limited Lab deployment. It is not an autoscaling fleet or an accepted HA service.

## Current topology

- TLS edge and consumer gateway on the existing GPU host; workers call the pinned loopback vLLM engine.
- PostgreSQL is authoritative for jobs, output journal, accounts and settlement. Valkey is a rebuildable scheduling index.
- Durable media/object storage and bounded local preparation cache; NVMe loss must not be treated as loss of the authoritative job ledger.
- Consumer App and Lab web are separate Vercel projects. The Lab control service has its own identity and deployment; several worker roles remain inert.
- Observability, retention, backup and recovery tooling exists. Alert delivery/canary and full release acceptance are not established merely by installation.

## Operating entry points

| Task | Procedure |
|---|---|
| Consumer runtime rollout / certification | [rollout/README.md](rollout/README.md), `rollout/ssm.sh`, `rollout/certify-window.sh` |
| App deployment and hosted onboarding | [app/README.md](app/README.md), [app/operations.md](app/operations.md) |
| Lab release | [lab/app/README.md](lab/app/README.md), `lab/rollout/lab-release.sh` |
| Lab monitoring | [lab/observe/README.md](lab/observe/README.md) |
| Recovery, retention and alerts | [runbooks/README.md](runbooks/README.md) |
| Migration history / immutability | [migration conventions](../apps/app/supabase/migrations/README.md) |
| Local service isolation | [test environment](../tests/integration/ENVIRONMENT.md) |

## Window ownership

One recorded owner mutates a target environment at a time. Record release/schema/profile identities, purpose, bounds, start/end UTC, owned resources and rollback/cleanup. Check the latest operator log before touching a target; the last reported E4C window was still running. Local work and read-only investigation can continue independently.

Use the target's approved secret names and current AWS identity; do not assume a historical administrator's shell/profile or branch is present. Fetching source is not deploying it. Runtime upgrades, hosted migration, Lab role enablement and fault/load experiments are distinct actions.

## Release and recovery truth

`rollout/known-good.json` records rollback candidates and their evidence. Its checks inspect candidate code, schema compatibility and artifacts; an arbitrary install backup is not a known-good runtime. Compatibility evidence currently reaches 0059 and must be extended for a new migration. Preserve runtime config/card/model identity during a measured window.

Same-host proofs do not establish replacement-host restoration. A fresh-host restore remains unproved until artifacts, secrets, weights/cache recreation and a real request/settlement are exercised within the declared scope. See the [launch review](../research/plan/26-launch-readiness-review-2026-10-01.md).

The former September pilot-design document mixed proposed and obsolete process layouts. Its historical measurements remain in Git and the dated implementation reports. Current instructions live in the runbooks above. Future scaling/placement/engine qualification follows [roadmap 23](../research/plan/23-inference-hosting-roadmap.md).
