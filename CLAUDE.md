# Contributor and agent instructions

These instructions apply to every implementation session, regardless of model or tool. The filename is retained for compatibility.

## Start with actual state

Read [STATUS.md](STATUS.md), the [launch review](research/plan/26-launch-readiness-review-2026-10-01.md), then [the implementation index](research/plan/README.md) and the assigned task's brief. Check newer commits and operating evidence before changing anything. Old numbered programs describe requirements and completed work; they are not instructions to restart earlier waves. Historical handoffs have been removed.

Current focus: qualify the deployed Marlin API and consumer App for an invited production pilot, then public self-service. Public signup and production acceptance are still pending at the recorded cutoff. Lab workers with unresolved dependencies/accounting risks remain disabled. A successful build, health check or task count is not launch acceptance.

## Architecture and invariants

- `apps/app`: consumer product; once-only 10,000 CREDIT grant per verified individual, free plan, no monthly refill. Historical USD stays separate.
- `apps/lab`: provider product and permissions; model ownership never implies access to a consumer's content. Provider allocation/budgets are not consumer credits.
- `apps/infrx-api/infrx/gateway/pilot.py`: consumer composition; `infrx/lab/compose.py` and `infrx/lab/control/app.py`: provider composition/control service.
- PostgreSQL owns acceptance, identity, holds/settlement, leases/fences, output journal and lifecycle eligibility. Valkey is rebuildable. Do not regenerate committed output, settle twice or infer usage from chunk counts.
- Sync stays sync unless async was explicitly requested. Admission, media preparation, queues and retries remain bounded. Trace loss must not stop inference; trace-off does not mean zero serving retention.
- Preserve model/image/processor/template/profile/card identity. Optimization is a newly qualified serving version, not an invisible replacement.

Binding contracts: [contract encoding/rulings](research/plan/08-contracts-v1-encoding.md), [durable protocols](research/plan/02-durable-protocols.md), [product specs](research/platforms/README.md) and [verification](research/plan/04-verification.md).

For App/Lab interface work, read the [UX design package](research/design/v1/README.md), including its live audit, screen/state specifications, service gaps and path-owned implementation lanes. The visual reference is illustrative, not a source of production data or permission to enable incomplete services.

## Worktrees and integration

Start from a recorded committed integration SHA, normally current main after reconciliation. Use isolated `codex/<task>` branches/worktrees and task-local ports, databases, object prefixes and temporary paths. Preserve unrelated local changes and other sessions' worktrees. No historical `claude/*` branch is a mandatory integration destination; the coordinator records the actual target for the current work.

Use [tasks.json](research/plan/tasks.json) and the [carried register](research/plan/consumer-v1/10-carried-work-register.md), not a second task graph. Record owned paths before parallel work. One owner controls migrations; coordinate composition roots, shared contracts, lockfiles and task metadata. Follow [the execution protocol](research/plan/03-execution-protocol.md).

Already-applied SQL migrations are immutable. Hosted schema is last recorded through 0059; a new migration requires the reviewed pending range, known-good schema reproof and R151 operating window. Never run a blanket hosted database push or reseed an active price table from an old README example.

## Development and verification

```sh
make api-env
pnpm --dir apps/app install --frozen-lockfile
pnpm --dir apps/lab install --frozen-lockfile
```

Run the relevant canonical Make targets: `api-test`, `api-lint`, `api-typecheck`, `api-mutants`; `console-test`, `console-lint`, `console-typecheck`, `console-mutants`, `console-built`; `bench-test`; `lab-test`, `lab-lint`, `lab-typecheck`, `lab-build`, `lab-mutants`. `make check` combines these. The API type gate currently tolerates a documented baseline; exit 0 does not mean no type errors.

Real-service gates include `consumer-local`, `backend-local`, `backend-certify`, `app-e2e`, `lab-compositions`, `lab-local`, `lab-e2e`, `lab-operate`, `lab-evaluate`, `lab-observe`, `lab-rollout`, `lab-improve`, and the `console-*-real`/`console-pg` targets. Read [supported environment](tests/integration/ENVIRONMENT.md) and [test guide](tests/integration/README.md). Missing Docker/Linux/GPU prerequisites are BLOCKED/NOT RUN, not passes. Use the task-local registry in `infrx/contracts/tasklocal.py`; never use a production database for a destructive test.

Prove behavior at its actual seam with a focused regression, then relevant real-service integration and release evidence. Preserve failures/skips and exact identities. Do not weaken thresholds after seeing results. The App's local auth stand-in and controlled engine do not prove hosted email or Marlin performance.

## Production and secrets

Consult [STATUS.md](STATUS.md) and the active operator record before any deployment or test. The last reported E4C window was still running; do not disturb it or assume it finished. Consumer runtime upgrades, migrations, Lab enablement and fault/load tests are separate coordinated actions. Existing authorization remains effective; do not invent a new approval process for ordinary local work.

Use [consumer rollout tooling](infra/rollout/README.md) and [Lab release tooling](infra/lab/app/README.md). Remote steps go through `infra/rollout/ssm.sh`; Lab operations use `infra/lab/rollout/lab-release.sh`. Select the release actually being qualified, not `origin/main` by habit. Record limits, owned resources, stop/cleanup procedure and the deployment combination for production tests.

Secret values stay in authorized environment/secret stores, never docs, argv, screenshots or committed logs. Validate the current AWS identity/profile; old host-specific credential workarounds are not universal configuration. Public code must not receive privileged database or infrastructure credentials.

## Documentation and evidence

Update [STATUS.md](STATUS.md) for current state; keep component READMEs about current behavior and commands. Do not add another dated resume/handoff chain. [Documentation policy](research/plan/DOCUMENTATION.md) explains historical citations and retired files.

Keep immutable test/operating reports under `research/plan/evidence/` with source/integration/deployed SHA, environment, commands, outcomes, failures/skips and remaining work. Preserve accepted task statuses unless evidence justifies a change. Validate documentation with:

```sh
python3 research/plan/scripts/validate_plan.py
python3 research/plan/scripts/progress.py check
```

Research uses [METHODOLOGY.md](research/METHODOLOGY.md): distinguish estimates from measurements, state units and workload, and source prices/claims. Model experiments and GPU sizing research are not the set of publicly served models. Put shared download logic in `models/common/`; keep per-model metadata in `model.env`. Marlin weights are gated and require authorized access.
