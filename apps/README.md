# Applications and shared runtime

Read [current state](../STATUS.md) before treating a feature as available in production.

| Component | Audience and responsibility | Entry point |
|---|---|---|
| Consumer App | Accounts, one-time individual CREDIT grant, API keys, model discovery/docs, own usage and results | [apps/app](app/README.md) |
| Provider Lab | Provider access, model/data/evaluation/improvement interfaces; incomplete integrations remain scoped and disabled | [apps/lab](lab/README.md) |
| Inference API | Durable admission, accounting, media, scheduling/worker execution, job/result routes and provider service compositions | [apps/infrx-api](infrx-api/README.md) |

The web apps are separate Next.js packages with their own origins, sessions and lockfiles. They share one ordered [database migration history](app/supabase/README.md). Consumer and provider authorization are distinct. API inference does not depend on either web application being online.

The consumer App and Marlin API are deployed; public onboarding and production acceptance remain open. The Lab's deployed shell/control access does not mean its full improvement workflow is usable. No paid plan, payments, automatic credit refill, dedicated-hosting product or Google-login launch claim is made.

Requirements and architecture live in [research/platforms](../research/platforms/README.md). The former duplicate September application specification has been removed; its historical content remains in Git. Use the [launch review](../research/plan/26-launch-readiness-review-2026-10-01.md) for current gaps.
