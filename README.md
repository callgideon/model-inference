# Model inference platform

We serve specialist open models through a metered API. Marlin-2B finite-video inference is the first workload; SOP verification over recorded robotics data is the intended application.

Two products share the runtime:

- **Inference App** (`apps/app`): consumer accounts, API keys, model catalog, credits, usage and results. Free plan; one-time 10,000 CREDIT per verified individual.
- **Provider Lab** (`apps/lab`): provider workspaces, model versions, authorized observations, datasets, evaluations and improvement workflows. Several backend integrations remain incomplete.

**Deployed does not mean launch accepted.** The Marlin API and consumer App are deployed, public signup remains closed, and production acceptance is pending. The Lab is deployed for limited internal testing. Read [STATUS.md](STATUS.md) for the dated deployment inventory, evidence, open blockers and next work.

## Start here

1. [Current state](STATUS.md) — what is implemented, deployed, verified and still missing.
2. [Launch review and production test sequence](research/plan/26-launch-readiness-review-2026-10-01.md).
3. [Architecture and product requirements](research/platforms/README.md).
4. [Implementation index](research/plan/README.md), [carried work](research/plan/consumer-v1/10-carried-work-register.md) and [contributor instructions](CLAUDE.md).

## Repository

| Location | Purpose |
|---|---|
| [apps/app](apps/app/README.md) | Consumer Next.js App; Supabase auth and account data |
| [apps/lab](apps/lab/README.md) | Separate provider Next.js Lab |
| [apps/infrx-api](apps/infrx-api/README.md) | Durable inference gateway, workers, accounting, media and Lab services |
| [apps/app/supabase](apps/app/supabase/README.md) | Shared, ordered database migration history |
| [infra](infra/README.md) | Deployment, monitoring, recovery and release tooling |
| [models/marlin2b](models/marlin2b/README.md) | Pinned serving recipe, client, corpus and benchmarks |
| `models/common`, other `models/*` | Shared download tooling and additional model experiments; not a public model catalog |
| [research](research/README.md) | Dated research, product specifications, plans and verification evidence |

PostgreSQL owns accepted work, leases, output journal and accounting. Valkey is a rebuildable scheduling index; object storage holds serving media/results. Workers call the pinned engine. Neither web application is required for an already provisioned API client to perform inference.

## Local checks

```sh
make api-env
pnpm --dir apps/app install --frozen-lockfile
pnpm --dir apps/lab install --frozen-lockfile
make api-test console-test lab-test
```

Use each application's README for local configuration. Run checks appropriate to changed paths; the full command inventory and Docker-backed gates are in [the test guide](tests/integration/README.md) and [supported environment](tests/integration/ENVIRONMENT.md). Skipped checks are not passes. Current API typing uses an explicit error baseline.

Production changes use the [rollout runbooks](infra/rollout/README.md), with a recorded release and operating window. A documentation push, successful build or public health response is not a release certificate.

## Later scope

GPU autoscaling, scale-to-zero, general custom-model hosting, additional engines/hardware, payments and a complete model-improvement service are not delivered launch features. See the [hosting roadmap](research/plan/23-inference-hosting-roadmap.md) and [separate product roadmaps](research/platforms/README.md).

Historical session handoffs were removed from the working tree. [Documentation history](research/plan/DOCUMENTATION.md) explains what was consolidated and how to retrieve dated evidence.
