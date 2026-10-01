# Provider Lab (`apps/lab`)

**2026-09-22 sequence:** [Marlin inference backend first](../../research/plan/18-marlin-backend-first.md), consumer App next, provider Lab afterward. The backend has independent headless deployment/recovery/performance gates; frontend feature work is subsequent.

**Sequencing:** consumer Marlin App launch comes first. [Complete plan](../../research/plan/12-complete-build-plan.md), [detailed later Lab packages](../../research/plan/13-lab-improvement-handoffs.md) and [fresh-session handoff](../../research/plan/16-fresh-session-handoff.md) are ready for the next accepted Lab scope.

**Package only (LW0, R154).** `package.json` and `pnpm-lock.yaml` exist, standalone like `apps/app` (no root pnpm workspace, which would re-root the live App's lockfile and Vercel build); the coordinator owns both. There is no application yet: L1 creates `app/`. Lab-only shared TypeScript comes from `packages/shared` (`link:` dependency; no reinstall after an edit there), never from `apps/app`. Dev port 3100 (the App keeps 3000). No deployment.

```bash
cd apps/lab && pnpm install --frozen-lockfile
make lab-test lab-lint lab-typecheck lab-build lab-mutants   # typecheck/build report "not run" until app/ exists
```

This is the planned provider product: register model versions, configure private dev and production endpoints, inspect authorized inference evidence, benchmark candidates, curate data and connect improvement workflows.

- [Requirements](../../research/platforms/05-lab-spec.md)
- [Roadmap](../../research/platforms/06-lab-roadmap.md)
- [Shared architecture and authorization](../../research/platforms/01-architecture.md)
- [Implementation changes and task routing](../../research/plan/08-platform-split.md)
- [New workstream briefs](../../research/plan/09-amendment-workstreams.md)

L1 creates the app shell after F2P publishes the revised contract base. Wave 2 is now imported and audited; [V1M](../../research/plan/11-wave3-revision-handoffs.md) moves the existing App trace explorer after L1/L2. Use the existing Next.js/UI conventions where suitable, a separate app deployment/session boundary, and shared packages created by the coordinator. Do not copy the consumer app wholesale or reuse consumer owner status as provider authorization.

Provider trace/review/evaluation UI belongs here. Shared runtime and data workers remain in the backend. No consumer credit wallet, migration history or inference gateway is duplicated inside this app. Customer content requires explicit access grants; a provider's model ownership alone is insufficient.
