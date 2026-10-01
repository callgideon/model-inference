# Provider Lab (`apps/lab`)

**State 2026-10-01:** live for internal testing at `https://lab.callbill.ai` (Vercel project
`infrx-lab`, team `callgideon`), reading the Lab control service at
`https://lab-control.callbill.ai` on the pilot box. What runs where and what is pending:
[state of record 25](../../research/plan/25-state-2026-10-01.md); the operator runbook is
[08](../../research/plan/consumer-v1/08-lab-internal-testing-rollout.md).

The provider product: a model team signs in, selects a provider workspace, and works on its own
models — deployments, authorized inference evidence, evaluation, data curation, improvement
pipelines and releases. Next.js (App Router), its own origin and session (cookie
`sb-infrx-lab-auth`, host-only), the App's Supabase project for auth only (the 0030 session door);
it holds no service-role key, no consumer wallet and no inference gateway.

## Pages and the backend family each reads

| Pages (`app/(provider)/`) | Service family (`lib/services/`) | Backend |
|---|---|---|
| `overview`, `models`, `deployments` | `control` | Lab control service `/lab/v1/control` |
| `requests`, `requests/[id]` | `traces`, `review` | lab-api `/lab/v1/traces` (behind `LAB_TRACES`); review through Supabase RPCs under the session |
| `judge` | `judge` | Supabase RPCs under the signed-in session |
| `evaluations`, `evaluations/checkpoints`, `experiments/[id]` | `evaluation` | `/lab/v1/evaluations` (behind `LAB_EVALS`) |
| `annotations`, `training` | `pipelines` | `/lab/v1/pipelines` (behind `LAB_PIPELINES`) |
| `datasets`, `datasets/[ref]`, `datasets/imports/[id]` | `datasets` | the datasets backend |
| `releases`, `optimizations` | `rollouts` | `/lab/v1/releases`, `/lab/v1/optimizations` (behind `LAB_RELEASES`) |
| `settings`, sign-in | `control`, `lib/auth/` | Supabase auth + `public.lab_provider_memberships()` |

A family whose base URL is unset, or whose backend refuses, renders "unavailable", never a
stand-in. Which families answer for testers today (some stay 503/404 until carried WRs land) is
runbook 08 §0 and the state file's carried-work table.

## Environment (`.env.example`; names only, values in the Vercel project)

- Public: `NEXT_PUBLIC_LAB_URL` (the Lab's https origin, no path), `NEXT_PUBLIC_SUPABASE_URL`,
  `NEXT_PUBLIC_SUPABASE_ANON_KEY` (publishable key).
- Server-only base URLs: `LAB_CONTROL_URL`, `LAB_TRACES_API_URL`, `LAB_EVALS_API_URL`,
  `LAB_PIPELINES_API_URL`, `LAB_RELEASES_API_URL`, `LAB_DATASETS_API_URL` (wave-6 lane lab-B adds
  one `LAB_API_URL` with these six as fallbacks).
- Development only (ignored in production): `LAB_CONTROL_PREVIEW`, `LAB_PIPELINES_PREVIEW`,
  `LAB_EVALS_PREVIEW`, `LAB_RELEASES_PREVIEW` — labelled in-memory stand-ins.

## Develop and check

Standalone install like `apps/app` (own `pnpm-lock.yaml`, no root workspace, which would re-root
the App's lockfile and Vercel build); Lab-only shared TypeScript comes from `packages/shared`
(`file:` dependency), never from `apps/app`. Dev port 3100 (the App keeps 3000).

```bash
cd apps/lab && pnpm install --frozen-lockfile && pnpm dev
make lab-test lab-lint lab-typecheck lab-build lab-mutants   # in `make check`
make lab-e2e                                                  # Docker, key l4; not in check
```

Tests live in `tests/<track>/` (`b c e2e l n p r v`); each track's `run-mutants.mjs` runner is
listed in `make lab-mutants`.

## Deploy

Vercel project `infrx-lab` with **Root Directory `apps/lab`**, deployed from the **repository root**
(an `apps/lab`-only upload lacks `packages/shared`: pnpm ENOENT). The operator tool is
`infra/lab/rollout/launch-v1.sh vercel` (renamed `lab-release.sh` in wave 6); the box half is
`launch-v1.sh box` and [`infra/lab/app/README.md`](../../infra/lab/app/README.md).

[Requirements](../../research/platforms/05-lab-spec.md) · [Roadmap](../../research/platforms/06-lab-roadmap.md) · [Architecture and authorization](../../research/platforms/01-architecture.md)

Customer content requires explicit access grants; a provider's model ownership alone is
insufficient, and consumer owner status is never provider authorization.

## Verification log

- 2026-09-27 (LW0, R154): the package and lockfile only; `app/` followed with L1.
- 2026-10-01 (W6 docs-state): rewritten as the built, deployed product (pages, families, env, checks, deploy from the repository root; lab.callbill.ai per the session-03 record line 595 and 09's log); the 2026-09-22 sequence banners retired.
