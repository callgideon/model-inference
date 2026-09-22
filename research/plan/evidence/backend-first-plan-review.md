# Backend-first plan review — 2026-09-22

User direction: complete all backend work for robust and optimized Marlin2B inference via an endpoint before the consumer App. This updates the sequence at `2ea562a`; no runtime work was performed in this planning revision.

## Concrete changes

- Added G6B protected headless operations, E3B actual-service endpoint integration, I2B independent deployment, I3B recovery/telemetry/restore, E1B representative GPU profiling, M4 media preparation optimization, W4 serving tuning and E4B final combined readiness evidence.
- Retained A1 as shared D-owned individual entitlement, existing auth/tenant/credit policy, exact accounting, secure media and durable job semantics. Frontend delivery is deferred without replacing backend controls with shortcuts.
- E3A/I2A/I3/E4 now reuse backend evidence and add consumer/browser deltas. Conditional I4 fleet follows backend capacity/availability evidence independently of UI.
- Added P-18 for declared workload/performance/quality/recovery targets and distinguished recoverable single-GPU service from high availability. Actual engine capabilities/configuration are pinned and verified during implementation.
- Updated current entry points, roadmap, scope, detailed handoffs, requirements, failure oracles, task ledger/validator and the copyable fresh-session prompt. Historical completion evidence is retained.

## Checks actually run

- `python3 research/plan/scripts/validate_plan.py --write-ledger`, followed by validation without write: 119 unique task records; combined DAG acyclic; all test-oracle/release references valid; current ledger generated.
- E4B backend closure: **37 tasks**, excluding frontend/App release and Lab work. Later App closure: **50 tasks**, reusing the complete backend closure and excluding Lab. Conditional fleet is independent of UI; imported Lab evaluation remains independent of observation and rollout independent of training.
- 10 backend, 13 App and 16 Lab requirement IDs mapped to owners and acceptance.
- Local Markdown links resolve; exact file/link counts are reported by the validator and include this evidence on the final run.
- Direct comparison to `git show 2ea562a:research/plan/tasks.json`: all **111 prior IDs/statuses preserved exactly**; eight new tasks are planned.
- Session-handoff validator: **100/100**, no placeholders or detected secrets. Its link detector reports zero recognized references, so the repository link checker supplies the actual local-file check.
- `git diff --check`: passed.

This is author consistency review, not independent signoff. No application tests were rerun for documentation/validator changes. No hosted migration, inference optimization measurement, real GPU result, paid resource allocation or endpoint deployment is claimed. New implementation sessions must produce those results under the named gates.
