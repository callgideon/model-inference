# Provider Lab (`apps/lab`)

**Documentation scaffold only. No runnable application, dependencies or deployment has been created yet.**

This is the planned provider product: register model versions, configure private dev and production endpoints, inspect authorized inference evidence, benchmark candidates, curate data and connect improvement workflows.

- [Requirements](../../research/platforms/05-lab-spec.md)
- [Roadmap](../../research/platforms/06-lab-roadmap.md)
- [Shared architecture and authorization](../../research/platforms/01-architecture.md)
- [Implementation changes and task routing](../../research/plan/08-platform-split.md)
- [New workstream briefs](../../research/plan/09-amendment-workstreams.md)

L1 creates the app shell after F2P publishes the revised contract base. Wave 2 is now imported and audited; [V1M](../../research/plan/11-wave3-revision-handoffs.md) moves the existing App trace explorer after L1/L2. Use the existing Next.js/UI conventions where suitable, a separate app deployment/session boundary, and shared packages created by the coordinator. Do not copy the consumer app wholesale or reuse consumer owner status as provider authorization.

Provider trace/review/evaluation UI belongs here. Shared runtime and data workers remain in the backend. No consumer credit wallet, migration history or inference gateway is duplicated inside this app. Customer content requires explicit access grants; a provider's model ownership alone is insufficient.
