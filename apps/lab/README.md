# Provider Lab (`apps/lab`)

**Deployed for limited internal testing**, not an accepted complete improvement platform.
`https://lab.callbill.ai` (Vercel project `infrx-lab`, team `callgideon`) reads the control
service at `https://lab-control.callbill.ai`. See [current state](../../STATUS.md), the
[carried register](../../research/plan/consumer-v1/10-carried-work-register.md) and
[operator runbook](../../research/plan/consumer-v1/08-lab-internal-testing-rollout.md).

Evaluation/pipeline listings, trace composition, dedicated worker logins and actual engine
smoke have unresolved gaps. Other worker roles remain inert. The reported evaluation
double-debit race must be fixed before enabling that worker; the trace pump needs its real
lost-ack retry proof. The current tester checklist covers access/settings and a judge dry-run,
not a complete dataset → evaluation → training → deployment workflow.

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
| `settings`, sign-in | `control`, `lib/auth/` | the auth facade `/auth/v1/*` + `/lab/v1/workspaces` (AP-09) |

A family whose base URL is unset, or whose backend refuses, renders "unavailable", never a
stand-in. Which families answer for testers today (some stay 503/404 until carried WRs land) is
runbook 08 §0 and the state file's carried-work table.

## Environment (`.env.example`; names only, values in the Vercel project)

- Public: `NEXT_PUBLIC_LAB_URL` (the Lab's https origin, no path).
- Server-only base URL: `LAB_API_URL` (lab-api, which serves every family and the Lab shell's
  sign-in and memberships; `lib/auth/config.ts` `labApiUrl`). Unset, the Lab is misconfigured.
  The six per-family names and the Supabase URL/key are read nowhere (AP-09, register row 76).
- Development only (ignored in production): `LAB_CONTROL_PREVIEW`, `LAB_PIPELINES_PREVIEW`,
  `LAB_EVALS_PREVIEW`, `LAB_RELEASES_PREVIEW` — labelled in-memory stand-ins.

## Develop and check

Standalone install like `apps/app` (own `pnpm-lock.yaml`, no root workspace, which would re-root
the App's lockfile and Vercel build); Lab-only shared TypeScript comes from `packages/shared`
(`link:` dependency; no reinstall after an edit there), never from `apps/app`. Dev port 3100 (the App keeps 3000).

From the repository root:

```bash
pnpm --dir apps/lab install --frozen-lockfile
pnpm --dir apps/lab dev
```

Checks, also from the repository root:

```bash
make lab-test lab-lint lab-typecheck lab-build lab-mutants   # in `make check`
make lab-e2e                                                  # Docker, key l4; not in check
```

Tests live in `tests/<track>/` (`b c e2e l n p r v`); each track's `run-mutants.mjs` runner is
listed in `make lab-mutants`.

## Deploy

Vercel project `infrx-lab` with **Root Directory `apps/lab`**, deployed from the **repository root**
(an `apps/lab`-only upload lacks `packages/shared`: pnpm ENOENT). The operator tool is
`infra/lab/rollout/lab-release.sh web` (`launch-v1.sh` is its deprecated shim, `vercel` → `web`); the box half is
`lab-release.sh box` and [`infra/lab/app/README.md`](../../infra/lab/app/README.md).

[Requirements](../../research/platforms/05-lab-spec.md) · [Roadmap](../../research/platforms/06-lab-roadmap.md) · [Architecture and authorization](../../research/platforms/01-architecture.md)

Customer content requires explicit access grants; a provider's model ownership alone is
insufficient, and consumer owner status is never provider authorization.

## Conventions (LAB-07/08/09/13; pinned by tests/l/shell L1-A10/A11, L1-B06, L1-B07)
- Roles: `holds(role, capability)`, `ROLE_CAPABILITIES` and `Actor` come only from lib/auth/access.ts (a copy of the
  App's contracts/v2 table, drift-pinned). Pages and actions pass the session's `workspace` to a port directly.
- Server actions: `redirect(await land(page, w, capability, valid, call))` with the `text`/`field`/`oneOf` readers
  (lib/services/common.ts); a view's `refusalCopy = fixedCopy(REFUSALS, REFUSAL_COPY)`; a port is
  `previewPort(flag, fake, real, UNAVAILABLE)`; a page labels the preview with `<PreviewNote records service />`
  under `isPreview()`; a page names the first failed read with `firstFailure(...)`.
- Pages type props with Next's `PageProps<"/route">` and export `metadata = { title: "<Page> · infrx Lab" }`;
  app/ code imports lib/ and components/ by `@/…` (co-located app files relatively).

## Verification log

- 2026-09-27 (LW0, R154): the package and lockfile only; `app/` followed with L1.
- 2026-10-01 (W6 docs-state): rewritten as the built, deployed product (pages, families, env, checks, deploy from the repository root; lab.callbill.ai per the session-03 record line 595 and 09's log); the 2026-09-22 sequence banners retired.
- 2026-10-01 (merge #76): `LAB_API_URL` first with the six old names deprecated (lab-B, merge #74); the deploy tool is `lab-release.sh` (lab-release-tool, merged).
- 2026-10-01 (merge #80, lab-D WR-4): the Conventions section (roles from access.ts, the common.ts action/preview helpers, page typing/metadata/imports) added.
