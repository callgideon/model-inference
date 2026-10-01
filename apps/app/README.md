# Consumer inference App

Next.js consumer product: account onboarding, API keys, model catalog/docs, CREDIT balance, usage/request history, owned results and operator controls. It is deployed at `https://app.callbill.ai`; **public signup and production acceptance remain pending**. See [current state](../../STATUS.md) and [launch gaps](../../research/plan/26-launch-readiness-review-2026-10-01.md).

## Implemented behavior

- Email/password signup, verification, sign-in, resend and password recovery; protected console routes.
- A verified individual receives one 10,000 CREDIT entitlement, including safe callback/relogin retries. No monthly refill or paid plan.
- Consumer keys are revealed once and stored as hashes; grants and adjustments are audited. New admissions reject revoked keys; existing reads/cancels follow the documented identity-cache policy.
- Catalog and documentation read the API's published capabilities and rates. CREDIT, holds and legacy USD are distinct; unavailable reads are not replaced with fixture balances.
- Usage/request detail includes owned result retrieval and expiry. Consumer access does not imply provider access.

The approved CAPTCHA requirement is **not implemented** in signup/recovery yet. Hosted confirmation/recovery delivery, abuse/rate limits and a two-user production journey need verification before public signup opens. Local browser tests use controlled auth/engine components and do not establish these hosted outcomes.

## Develop

From this directory, install with `pnpm install --frozen-lockfile`, configure `.env.local` from `.env.example` using a development project, then run `pnpm dev` (port 3000). From the repository root:

```sh
make console-test console-lint console-typecheck
make console-built
```

Additional conformance, real database and browser checks: [test guide](../../tests/integration/README.md). A missing stack is NOT RUN, not acceptance.

## Configuration and database

The authoritative environment matrix is [lib/deploy/env.ts](lib/deploy/env.ts); production validation runs at startup. Public configuration uses `NEXT_PUBLIC_*`; privileged keys and `CONSOLE_CURSOR_SECRET` remain server-only. `INFRX_API_BASE_URL` identifies the API origin. Preview deployments must not inherit production write credentials. Development fixture views are explicitly labeled and ignored in production.

[Supabase configuration](supabase/README.md) and [migration conventions](supabase/migrations/README.md) govern the shared database. Applied migrations are immutable. Signup entitlement is once per individual, not per organization or browser callback. Price publication and credit changes use the audited operations, never direct balance edits or catalog reseeding.

## Deploy and verify

Vercel project `infrx-app`, root `apps/app`, production builds from main. Follow the [App runbook](../../infra/app/README.md) and [operations checklist](../../infra/app/operations.md). `GET /api/version` reports the actual build identity; check it rather than copying a SHA from an old README.

A deployed build is not APP-PILOT acceptance. The [launch review §4](../../research/plan/26-launch-readiness-review-2026-10-01.md#4-shortest-verifiable-path-to-usable-production) defines the real account → key → inference → result → ledger workflow.

## Source map

- `app/(auth)`, `app/auth/callback`: onboarding and recovery.
- `app/(console)`: catalog, docs, keys, billing, usage/results, settings and operator UI.
- `lib/services`, `lib/session.ts`: authenticated server adapters and personal account selection.
- `lib/contracts`: shared contracts and fixtures; fixtures are not production data.
- `tests`: UI/service/conformance/integration checks.
