# Two-platform product architecture

Updated 2026-09-21. **Target product architecture, reconciled with wave 2 at `271add9`; deployment is not claimed.**

The business operates inference infrastructure. Two products make that infrastructure useful to different users:

| Product | Audience and outcome | Location | First milestone |
|---|---|---|---|
| Inference App | A developer finds a supported model, gets an API key, spends a central credit balance and understands their usage | `apps/app` | Public signup, one-time 10,000 promotional credits per individual user, reliable metered Marlin inference |
| Provider Lab | A model team deploys versions, understands performance, evaluates changes and improves its models using authorized data | `apps/lab` | Assisted Marlin onboarding, versioned dev/prod endpoints and provider-scoped operational visibility |

One company can use both products. Consumer membership, provider membership and platform operations remain distinct permissions. One shared runtime serves both; the applications do not call each other to complete inference.

## Read in order

1. [Architecture and shared boundaries](01-architecture.md): deployment, ownership, data flow and security.
2. [Credits and signup policy](02-credits.md): individual grant, rate cards, settlement and legacy USD migration.
3. [App requirements](03-app-spec.md) and [App roadmap](04-app-roadmap.md).
4. [Lab requirements](05-lab-spec.md) and [Lab roadmap](06-lab-roadmap.md).
5. [API and model contracts](07-api-contracts.md): compatibility, versioning and modality-specific extensions.
6. [Decisions, open questions and sources](08-decisions-and-sources.md).
7. [Implementation impact](../plan/08-platform-split.md), [new workstream briefs](../plan/09-amendment-workstreams.md), and [cross-system handoff](../plan/PLATFORM-SPLIT-HANDOFF.md).

## Authority and implementation status

This directory supersedes earlier product scope where it conflicts. The amended [task manifest](../plan/tasks.json) and [implementation impact](../plan/08-platform-split.md) govern task routing and dependencies; [shared contracts](../plan/01-contracts.md) and [durable protocols](../plan/02-durable-protocols.md) govern the runtime. Earlier module briefs remain useful subject to these amendments. Historical research is evidence to evaluate, not an instruction to implement every feature.

The original package was committed as `25b9829`; wave-2 implementation has now been pulled from main at `271add9`. Read the [audit](../plan/10-wave2-platform-audit.md) and [revision handoffs](../plan/11-wave3-revision-handoffs.md). Original module completions are preserved. CREDIT/individual wallets and Lab are target behavior; the existing schema and executable contracts still implement the legacy USD pilot. Hosted state and other-system worktrees remain unverified.

## Product sequencing

Deliver App's free inference journey while building Lab's provider controls independently. Marlin is the first operational workload. Establish a bounded robotics design-partner trial before committing to a general robotics service; pursue LLM migration where a customer's benchmark and workload demonstrate a benefit. Speech remains deferred. Model optimization and heterogeneous hardware are shared infrastructure investments, evaluated against workload quality and cost, rather than separate customer dashboard requirements.

Longer-term Lab closes the loop: production evidence → curated dataset → evaluation → prompt/harness or model change → comparison → approved deployment → new evidence. Initial Lab integrates externally trained checkpoints and existing annotation pipelines. A complete training service is a later milestone.
