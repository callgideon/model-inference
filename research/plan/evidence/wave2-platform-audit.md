# Wave-2 platform audit — local verification

Date: 2026-09-21. Imported implementation: `271add946771ddc4efc3cbc2044758443080759b`. Working branch: `codex/wave2-platform-audit`. Scope and findings: [audit](../10-wave2-platform-audit.md). This is local code/test/documentation evidence, not a hosted-state or independent reviewer certification.

## Reconciliation

- Preserved the uncommitted product amendment in a named stash before pulling `origin/main --ff-only`.
- Main advanced to the supplied `271add9` baseline. Reapplied the amendment, resolved four documentation/manifest conflicts and retained original statuses, merge SHAs and integration notes.
- Original migration files 0001–0005 and dependency locks are unchanged. No SQL applied, no cloud/production mutation, no paid model call, no push.
- Only current checkout and an older detached worktree are locally visible. Other-system task worktrees and unpushed changes are not verified.

## Environment

macOS ARM64; uv 0.10.9; Python 3.12.13 installed by `make api-env` from the frozen lock; Node v25.5.0 (satisfies repo >=22.18 engine constraint); project-selected pnpm 9.15.9 (`pnpm` at the repository root reports 10.28.0). `pnpm install --frozen-lockfile` ran under `apps/app`. No lockfile was regenerated.

Docker CLI exists, but the daemon is unavailable. PostgreSQL/Valkey/ClickHouse/MinIO integration and live GPU behavior are **not verified here**. D1's older real-service evidence is retained with its own date/environment.

## Baseline and fixes

Initial `make check` on pulled wave-2 runtime stopped in API tests: **1 failed, 1515 passed, 70 skipped**. Failure was the Linux-only `/proc/self/fd` spool leak oracle on macOS. The repair checks the actual opened descriptor for EBADF after each failed write, alongside file/charge cleanup. This does not skip or weaken the leak check.

The subsequent full API mutation run detected a pre-existing surviving media mutant on Python 3.12.13: `mapped_address_not_unwrapped`. Production validation already unwraps correctly, but existing cases relied on an older interpreter classifying every mapped address as reserved. Added explicitly denied mapped IPv4 destinations, preserving valid public mapped behavior. The complete affected media test/mutation run then passed **163 tests**.

R-3 deadline correction required replacing old gateway skew-margin tests and two obsolete heuristic mutants with DB-clamp and shortened-budget oracles. Other local runtime changes: exact Money/available-balance rendering, strict missing-wallet handling, non-production fixture opt-in, deterministic trace clock and fresh explicit Decimal contexts. These remain legacy USD contracts; no CREDIT schema or signup grant was implemented by these fixes.

The new integration command exposed a stale two-migration inventory assertion and a Linux-only orphan-process scanner. Both were corrected. The orphan mutant now applies before either platform path, so a broken scan cannot hide behind OS selection.

## Checks performed

| Command / check | Outcome |
|---|---|
| `make api-env` | Passed, frozen environment installed |
| App `pnpm install --frozen-lockfile` | Passed; lock unchanged |
| Focused money/deadline/judge/spool tests | 113 passed; final API run includes them |
| `make api-test` after corrections | 1518 passed, 70 Docker-dependent skips |
| `make api-mutants` first full audit run | 954 passed, 84 skipped, one media survivor; resolved by complete affected media rerun above |
| `make console-test` | 255 passed, no skipped tests |
| `make console-lint` | Exit 0; two existing unused-type/variable warnings in contract files |
| `make console-typecheck` | Exit 0, includes Next route type generation |
| `make console-mutants` | Contract 144, V 39, U 61 and C 88 mutants detected; C runner self-tests passed |
| `make bench-test` | 40 passed; offline client/corpus tests, not GPU benchmarking |
| `make integration INTEGRATION_ARGS="--layer 1 --canary --report /tmp/model-inference-audit-integration-final.json"` | Exit 0: socket engine conformance 8/8, integration tests 67 passed/21 service skips, API/console/bench reruns passed, 42 mutants detected + one intentional survivor control; Python/console canaries detected |
| Decimal guard sensitivity in isolated temporary copies | Reintroducing ambient `abs()` and shared mutable context each causes its named test to fail; source checkout unchanged by probes |
| Manifest graph and baseline preservation | 77 unique records, 71 active; all 45 original IDs/status/merge evidence retained; valid references; no cycles; E4 closure 41 tasks with no Lab/V/J/C2 dependencies |
| Session-handoff validator | 100/100, required sections present, no placeholders/potential secrets reported; heuristic only |

The final complete `make check` rerun passed and is recorded below. Do not infer real-service readiness from its exit status: Docker cases are skipped by the existing D runner. E2R still owns resource isolation, real RLS inversions and shared clock work.

## Review of the change

The repository audit reviewed code paths, schema and fixtures rather than treating green unit totals as evidence of composition. Corrective changes do not mount the new ingress, construct a real JobStore, move provider routes prematurely or rewrite financial units. UI fixtures are opt-in development examples; production account reporting remains unavailable until C0.

The task graph separates App reporting from Lab content and assigns existing USD-only consumer views to U1R, preserves all original task history, retires six mixed IDs from scheduling and adds nine bounded revision tasks. Original F2.2 items are all fixed, pending with an owner, already completed upstream or explicitly transferred to the appropriate release gate. A dependency checker verified the combined graph and original task state preservation against `git show 271add9:research/plan/tasks.json`.

## Still required

Independent review at the final handback HEAD; F2R remaining repairs; F2P v2 encoding; D1R additive migrations; C0 real reporting; I0/G2 safe runtime composition; E2R real service proof; A1/A2 public signup/grant journey; Lab implementation and permission gates. Hosted state, production rates/legacy-account transition, browser/GPU/staging release evidence and paid judge authorization remain outside this local audit.

## Documentation checks

Local Markdown destinations across the changed/new documentation resolve (inline/fenced examples excluded). `git diff --check` passes. Original module briefs now carry current dependency/status notices, so an old F2/D1/U1 header does not invite duplicate implementation.

## Completion note

Final `make check`: **exit 0** on the audited code. API tests: **1518 passed / 70 skipped**. Full API mutation suite: **955 passed / 84 skipped**, no surviving declared mutant. Console: **255 tests passed**, lint exit 0 (two pre-existing warnings), route type generation/typecheck passed; all four mutation lists passed (**144/39/61/88**). Offline benchmark tests: **40 passed**. The skipped checks need Docker and remain pending real-service coverage.

Container-free integration: **exit 0** with **8/8** socket engine cases, **67 passed / 21 service skips**, **42 detected mutants + one expected-survivor control**, and both intentional failure canaries detected. This is Layer 1 evidence only.

Final plan validation: **77 records, 71 active, nine revision tasks, E4 closure 41 tasks**; original task status/merge/integration-note preservation checked directly against `271add9`. All handoff/test-oracle references resolve, combined dependency graph is acyclic and App excludes Lab/provider-content/judge dependencies. Handoff validator: **100/100**. Diff whitespace check passed. No independent reviewer signoff, push or deployment is claimed.

Raw local logs: `/tmp/model-inference-wave2-baseline-check.log`, `/tmp/model-inference-wave2-audit-final-check.log`, `/tmp/model-inference-audit-integration-final.json` and `/tmp/model-inference-audit-media.log`. These temporary logs are not required to run the committed tests on another system; use the exact make commands above.
