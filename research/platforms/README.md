# Consumer App and provider Lab

The company operates model inference infrastructure. Two products serve different users on that shared runtime:

| Product | Audience and outcome | Location |
|---|---|---|
| Inference App | A developer discovers a model, obtains a key, spends a central CREDIT balance and understands their usage/results | `apps/app` |
| Provider Lab | A model team manages versions, examines authorized evidence, evaluates changes and improves its models | `apps/lab` |

**Implementation state:** the consumer App/Marlin API are deployed, public signup and release acceptance remain pending, and the Lab has a deployed internal surface with incomplete services. [STATUS.md](../../STATUS.md) is the dated state of record. Requirements below describe intended behavior and must not be read as proof of availability.

## Product documents

1. [Architecture and shared boundaries](01-architecture.md).
2. [Credits and signup policy](02-credits.md): one-time 10,000 CREDIT per verified individual; free plan, no refill or USD conversion.
3. [App requirements](03-app-spec.md) and [roadmap](04-app-roadmap.md).
4. [Lab requirements](05-lab-spec.md) and [roadmap](06-lab-roadmap.md).
5. [API/model contracts](07-api-contracts.md).
6. [Decisions and sources](08-decisions-and-sources.md).
7. [Implementation index](../plan/README.md), [launch review](../plan/26-launch-readiness-review-2026-10-01.md) and [carried work](../plan/consumer-v1/10-carried-work-register.md).
8. [App / Lab UX design package](../design/v1/README.md): chosen visual direction, actual-screen audit, detailed interaction states and implementation lanes. Proposed UI work; backend requirements and launch gates remain authoritative.

## Shared boundaries

The [API-first lifecycle specification](../plan/api-lifecycle/README.md) is the current implementation amendment for both products. Every shipped product operation must use structured FastAPI APIs; existing frontend database/RPC adapters require migration. The first-model import → deployment → consumer distribution → consented trace/judge joins are specified there and remain incomplete.

One company may use both products, but consumer membership, provider membership and platform operations remain separate permissions. Model ownership does not grant access to customer content. The applications do not call each other to complete an inference request. Shared runtime and database contracts govern identity, durable execution, accounting, data-use grants and immutable versions.

The [task manifest](../plan/tasks.json), [contracts](../plan/01-contracts.md), [rulings](../plan/08-contracts-v1-encoding.md) and [durable protocols](../plan/02-durable-protocols.md) govern implementation. Earlier research is supporting context, not an instruction to ship every proposed feature.

## Sequencing

Qualify a bounded Marlin consumer pilot, prove the real App/API/credit journey, then open public self-service after hosted onboarding and operating checks. Provider workflows can be developed independently but stay disabled until their adapters, permissions and accounting checks pass.

The first application is SOP analysis of recorded robotics videos. An agreed rubric, representative data and human labels are needed before accuracy claims. Native video streams, robot actuation, speech and other model families remain separate scope decisions.

The longer-term Lab loop is production evidence → curated data → evaluation → prompt/harness/model change → comparison → approved deployment → new evidence. Initial integration can use externally trained checkpoints and existing annotation pipelines. GPU scaling, custom-model qualification and hardware/engine optimization follow the [hosting roadmap](../plan/23-inference-hosting-roadmap.md); a complete training service is later work.
