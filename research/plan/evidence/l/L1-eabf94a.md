# L1 — Lab shell and separate build/auth boundary (lane lab-app, wave LW1)

- Base `9a6c3685` (LW0 merged) · code head `eabf94ae` · branch `codex/w5-lab-app` · worktree `.claude/worktrees/codex-w5-lab-app`
- Tasklocal: L1 is fake-only (track `l`, R152); key `l4` (PG 57503) unused by L1. Lab dev port 3100 (smoke only). No docker, no hosted service, no secrets.
- Oracles: LAB-ACCESS (shell half: consumer-only denial, membership selection, direct-route/server-action guard), SPLIT-CONTRACT (own origin and session cookie, no App import, App still builds).

## Changed paths (all owned)

`apps/lab/app/layout.tsx`, `apps/lab/app/(provider)/layout.tsx`, `apps/lab/lib/auth/{access,memberships,config,request,guard,actions}.ts`, `apps/lab/next.config.ts`, `apps/lab/tests/l/shell/{access,config,request,boundary}.test.ts`, `apps/lab/tests/l/shell/run-mutants.mjs`.

## Design (one line each)

- `access.ts` (pure): signed-out / unavailable / denied / select / ready. No membership = denied (a consumer owner is not a provider); a failed read = unavailable (fail closed, never "denied" or a workspace); one membership auto-selects; the workspace cookie is a preference honoured only when it names one of the user's memberships.
- `memberships.ts`: the L2 read is the no-argument RPC `lab_my_provider_memberships` on the user's own session (the DB derives `auth.uid()`); any malformed/unknown-role/duplicate row fails the whole read closed.
- `config.ts`: explicit origin (`NEXT_PUBLIC_LAB_URL`, dev default `http://localhost:3100`; production needs its own https origin with no path, else unavailable). Session cookie `sb-infrx-lab-auth` (never the App's `sb-<ref>-auth-token`), host-only (no Domain), SameSite=Lax, Secure iff https. Workspace cookie http-only, 30 days.
- `request.ts` holds everything the guard decides (config → session client with the Lab cookie options → user → memberships → selection); `guard.ts` only passes Next's `cookies()`/`createServerClient` and `notFound()`s, `cache`d per request.
- Next's Partial Rendering means a layout does not stop a page or action running (node_modules/next/dist/docs/01-app/02-guides/authentication.md:1350-1356), so every page, route, provider layout and exported server action calls the guard itself; `boundary.test.ts` enforces it by walking `app/` and `lib/`.
- `actions.ts` `selectWorkspace`: requires a provider session, stores only the membership `chooseWorkspace` validated.
- `next.config.ts`: `Cache-Control: private, no-store` on every path, no `X-Powered-By`.

## Failing seam tests first (red, recorded before the implementation)

| cmd | exit | result |
|---|---|---|
| `cd apps/lab && node --test tests/l/shell/*.test.ts` (before lib/auth, app/, next.config existed) | 1 | 6 tests, 0 pass, 6 fail: `ERR_MODULE_NOT_FOUND lib/auth/access.ts`, `… lib/auth/config.ts`; L1-B01..B04 fail (no action, no layout) |
| `node --test tests/l/shell/request.test.ts` (request.ts moved aside, after the guard refactor) | 1 | `ERR_MODULE_NOT_FOUND lib/auth/request.ts` |

## Green (on `eabf94ae`)

| cmd | exit | result |
|---|---|---|
| `make lab-test` | 0 | tests 24, pass 24, fail 0, skipped 0 |
| `make lab-lint` | 0 | eslint, 0 problems |
| `make lab-typecheck` | 0 | `next typegen` + `tsc --noEmit` clean |
| `make lab-build` | 0 | Next 16.3.5 build OK (only `/_not-found` until WR-L1-1 adds a page) |
| `cd apps/lab && node tests/l/shell/run-mutants.mjs` | 0 | 24 cases, all named by a mutant; 41 mutants, 41 killed, 0 not killed |
| `node tests/l/shell/run-mutants.mjs --only NOPE` | 1 | runner refuses an empty selection |
| `cd apps/app && pnpm install --frozen-lockfile && pnpm build` | 0 / 0 | App builds unchanged |
| WR-L1-1 page in a scratch copy of apps/lab: `pnpm build` | 0 | `ƒ /` dynamic; boundary tests 24/24 with the page present |
| same copy, `pnpm start` on 3100, production env with an http origin, `curl /` | — | 200, `Cache-Control: private, no-store`, layout: "Provider access could not be checked…" (misconfig fails closed) |
| same, https origin, no session cookie, `curl /` | — | 200, `private, no-store`, "Sign in to the Lab…"; the page's own guard 404'd (`NEXT_HTTP_ERROR_FALLBACK;404` digest), no page content in the payload |

Mutants surfaced one gap on the first run: X22 (missing anon key accepted) survived → C01 gained the url-only/key-only assertions; X19 (production falls back to the dev origin) was equivalent (the https rule already rejects it) → the redundant branch was deleted. The added unparsable-origin assertion is named by X41.

Not killable under `node --test`: `guard.ts` (3 statements of Next plumbing: `cookies()`, `createServerClient`, `notFound()`); covered by B01 (every entry calls it) and the smoke above.

## Wiring requests

- **WR-L1-1** `apps/lab/app/(provider)/page.tsx` (new; without a page the provider layout is never mounted):
  ```tsx
  import { requireProviderWorkspace } from "@/lib/auth/guard";

  // L1 wiring: the Lab home. It calls the guard itself (a layout does not stop a page rendering).
  export default async function Home() {
    const workspace = await requireProviderWorkspace();
    return <p>{workspace.providerName}: no Lab features are available yet.</p>;
  }
  ```
  Proof: scratch-copy build + smoke above; `L1-B01` then scans it.
- **WR-L1-2** `Makefile` `lab-mutants:` → `cd apps/lab && node tests/l/shell/run-mutants.mjs` (replacing the "not run" echo).
- **WR-L1-3** `apps/lab/.gitignore` with `next-env.d.ts` (generated by typegen/build; `apps/app/.gitignore:41` does the same). It was left untracked, not committed.
- **WR-L1-4** `apps/lab/.env.example`:
  ```
  # Lab (provider) app. Copy to .env.local. Its own origin; never the App's.
  # Production: its own https origin, no path; otherwise every request is "unavailable".
  NEXT_PUBLIC_LAB_URL=http://localhost:3100
  NEXT_PUBLIC_SUPABASE_URL=
  # Publishable ("anon") key; RLS and the L2 RPC decide what a session sees.
  NEXT_PUBLIC_SUPABASE_ANON_KEY=
  # The Lab session cookie is sb-infrx-lab-auth: host-only (never set a cookie Domain), SameSite=Lax, Secure on https.
  ```
- **WR-L1-5 (schema request → lab-sql / L2-SQL; interface for lab-access L2)** `public.lab_my_provider_memberships()`: no arguments; returns `setof (provider_org_id uuid, provider_name text, role text)` with role in `viewer|developer|administrator`; only `auth.uid()`'s active, unrevoked provider memberships; one row per provider org; EXECUTE for `authenticated` only (revoked from `anon`/`public`); never derived from consumer ownership or the operator bit. `lib/auth/memberships.ts` fails closed on any other shape.
- **WR-L1-6 (scope)** A Lab sign-in page, `/auth/callback` route and session-refresh `middleware.ts` are outside L1's owned paths. Until they exist no Lab session can be established (the shell shows "Sign in…"). Needs: extend lab-app's owned paths (L1 fix round or L4) or assign; the callback URL then joins the env example and the Supabase redirect allow-list (operator).

## Open issues

- Integration is fake-only: L1 merges after L2 (tasks.json `integration_dependencies: [L2]`); the RPC is not on the base. After L2/L2-SQL merge, rerun LAB-ACCESS with the real RPC on `l4` (PG 57503): 2 consumers, 2 providers, 1 user in both.
- `packages/shared` not used yet (nothing shared needed for the shell).

## Estimate (remaining for L1)

optimistic 1 h / likely 2.5 h / pessimistic 5 h, confidence medium. Basis: code, tests and mutants complete in one session; remaining = one verify round (47-234 min, session-03 analogue) + applying WR-L1-1..4 + a real-RPC LAB-ACCESS rerun once L2/L2-SQL merge; WR-L1-6 not included.

## Audit log

- 2026-09-27T09:26Z: evidence written for `eabf94ae` (lane lab-app, LW1).
