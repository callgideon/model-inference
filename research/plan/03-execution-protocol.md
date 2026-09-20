# Worktree and integration protocol

## Session start

Read root CLAUDE and HANDOFF, this package, and the selected module brief. Inspect actual branch/worktree status and newer evidence before coding. The manifest is the planned dependency graph, not a live scheduler. Coordinator records assigned task, owner, base SHA and integration target in an append-only session record under `research/plan/evidence/`; do not have every worker rewrite the shared manifest.

Use an isolated checkout of the coordinator's **committed integration SHA**. Do not branch from uncommitted foundation changes. Example after selecting task D1 and a verified SHA:

```bash
git fetch origin
git worktree add -b codex/d1-durable-schema ../model-inference-d1 <verified-integration-sha>
```

Replace the angle-bracket argument with the recorded SHA; it is intentionally not an executable command until selected. The `codex/` prefix is the repository handoff convention, irrespective of the implementation model. Use distinct local ports, database names/schema prefixes, object prefixes and temporary directories per worktree. Never share test credentials or destructive test targets with production. Do not delete or reset another session's checkout.

## Parallelism and ownership

F1 owns extraction first. F2 owns global types/fixtures/dependency locks and test discovery. After F2, ownership transfers for feature roots in the manifest; F does not keep editing feature code. Coordinator alone changes composition roots, shared CI/package manifests/lockfiles, navigation/layout wiring, shared global types and the task manifest. Workers submit a small integration request describing imports/routes/dependencies rather than editing shared wiring concurrently. D alone owns SQL migrations; reserve migration sequence numbers centrally.

Each task is one reviewable unit. Start dependencies must be integrated before coding except explicitly independent tasks. Integration dependencies may be faked during development but must be real and tested before the task is marked integrated. All module test directories are separate; E owns cross-module tests, never replaces modules' unit tests. The module handoff's paths are planned ownership, not evidence of existing files.

## Definition of done

1. Implement only the assigned task and its documented public surface. Add a regression test for the stated failure boundary before fixing it where practical.
2. Run the task's focused tests, shared contract suite and relevant baseline tests; capture command, exit code, environment, seed and commit SHA. No secrets or customer content in reports.
3. Review diff for tenant isolation, bounded resources, failure ordering and shared-file collisions. Compare against task acceptance, not just test count.
4. Commit owned files. Write a session handback with implementation SHA, changed paths, tests/results, contract changes, known limits, migration/rollback notes and next unblocked task.
5. Coordinator integrates one dependency-safe change at a time, reruns affected cross-module tests and records integrated SHA. A worker's green fake tests mean **implemented**, not integrated or live verified.

## Merge lanes

- Critical execution: F -> D1/D2 -> D3/D4/D5 + M -> Q/W -> G -> E3.
- Observability: F -> T; then durable feedback D6 -> C/J/G -> V; U consumes C. Trace loss must not block execution lane tests.
- Deployment: I1 and E1 early; I2 after pilot runtime integration; I3 recovery; E4 pilot decision; I4 fleet only after E4. Infrastructure code can be drafted against contracts earlier only where the manifest permits it.

UI sessions build with contract fixtures while C implements services. They may not create alternate server actions to unblock themselves. J uses coordinator fakes until D6; it may not create separate budget tables. Q never invents a second durable job store. G never duplicates financial settlement in route handlers.

## Rollback and migration

Use additive expand/contract migrations; prove compatibility with the previous deployed runtime before rollout. Pause admission, drain/fence work and reconcile durable jobs before rollback. Rebuild queue indices from PG; disabling Valkey is not a data migration and cannot discard jobs. Once enforced credits are enabled, reverting to the original unmetered gateway is unsafe: use maintenance 503 until a compatible runtime is available. Do not drop ledger/journal/job tables to roll back code.

Live tests/deployment use a coordinator-allocated environment and a single deployment lock. No module independently changes shared production configuration or purchases capacity. Document required environments early; do local work while waiting for an allocated staging target. The documentation commit does not authorize later agent sessions to submit paid judge work or mutate production without their assigned deployment scope.

## Verification log

- 2026-09-20: Defined isolated worktree ownership, mock/integration distinction and rollback rules for external implementation sessions.
