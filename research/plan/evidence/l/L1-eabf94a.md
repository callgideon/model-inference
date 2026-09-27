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

## Fix round (review of `b7de1b73`; code head `2b1bd41b`)

Changed paths (all owned, test-only): `apps/lab/tests/l/shell/boundary.test.ts`, `apps/lab/tests/l/shell/guard.test.ts` (new), `apps/lab/tests/l/shell/run-mutants.mjs`. No `lib/auth` source changed: both findings were test gaps, not wrong behaviour.

| id | status | what changed |
|---|---|---|
| 0-L1-R-1 | fixed | `L1-B01` now parses with the TypeScript compiler (devDependency already installed) instead of splitting on `^export `. Every runtime export of a `"use server"` file (async function, arrow/`const`, `export default`, `export { … }`; type exports are erased and skipped) and every function whose body opens with `"use server"` (inline action) must contain a guard call. The old split also missed a file whose directive follows a comment, and passed an unguarded action followed by a guarded helper; both are now in the self-check list with the arrow, default, list and inline forms. An export list or `export default name` is flagged even if its target is guarded (write the guard in the exported function). |
| 0-L1-R-2 | fixed | New `guard.test.ts` imports `guard.ts` itself: `module.registerHooks` (Node ≥22.15, no CLI flag, so `pnpm test` is unchanged) maps `next/navigation` to the real `next/navigation.js` (real `notFound()`, digest `NEXT_HTTP_ERROR_FALLBACK;404`), `next/headers` to a fake cookie store and `@supabase/ssr` to a fake session client. G01: page guard = workspace only when ready; select, denied, signed-out, unavailable are 404. G02: action guard = any provider session; the rest 404. G03: the client gets the env URL/key and the Lab cookie options; the RPC gets only its name. The earlier "guard.ts cannot be mutated under node --test" was wrong. The pure-function refactor the finding offered was not needed once guard.ts runs under test. |
| 0-L1-R-3 | not fixed (coordinator ruling + lab-sql + L2) | Nothing in L1's owned paths can settle it: the SQL is lab-sql's, `memberships_for_user` is L2's, and neither RPC exists on any branch yet (`codex/w5-lab-sql` is at the base). Proposal filed as **WR-L1-7** below. L1's code already matches option (a): no change is needed on the Lab side if the ruling picks it. |

### Red first (before the fix)

| cmd | exit | result |
|---|---|---|
| new B01 self-check list, old regex detector: `node --test tests/l/shell/boundary.test.ts` | 1 | B01 fails: detector returned only the page; missed `lib/fn.ts:2` (trailing guarded helper), `lib/arrow.ts:2`, `lib/default.ts:2`, `lib/list.ts:3`, `lib/comment.ts:3`, `app/(provider)/y/page.tsx:1` (inline) |
| scratch copy of `eabf94ae` + reviewer's H1 (arrow `peekWorkspaces` appended to actions.ts): `node --test tests/l/shell/*.test.ts` | 0 | 24 pass, 0 fail (reproduced: H1 survives) |
| same copy + reviewer's H2' in guard.ts: `node --test tests/l/shell/*.test.ts` | 0 | 24 pass, 0 fail (reproduced: H2' survives) |
| H2' copy + the new `guard.test.ts` | 1 | `not ok L1-G01` (3 tests, 2 pass, 1 fail) |
| H1 copy + the new `boundary.test.ts` | 1 | `not ok L1-B01`: `lib/auth/actions.ts:19` |
| first mutant run of X42–X50 | 1 | X46 not killed: G03 failed by `TypeError`, not by assertion → G03 reads `cookieOptions?.name`; killed on the rerun |

### Green (on `2b1bd41b`)

| cmd | exit | result |
|---|---|---|
| `make lab-test` | 0 | tests 27, pass 27, fail 0, skipped 0 |
| `make lab-lint` | 0 | 0 problems (the first run flagged a helper named `useServer…` under react-hooks/rules-of-hooks; renamed `serverPrologue`) |
| `make lab-typecheck` | 0 | `next typegen` + `tsc --noEmit` clean |
| `make lab-build` | 0 | Next 16.3.5 build OK |
| `cd apps/lab && node tests/l/shell/run-mutants.mjs` | 0 | 27 cases, all named by a mutant; 50 mutants, 50 killed, 0 not killed |
| `cd apps/app && pnpm install --frozen-lockfile && pnpm build` | 0 / 0 | App builds unchanged |

New mutants: X42 page guard falls back to the first workspace (H2', G01) · X43 page guard drops its 404 (G01) · X44 action guard drops its 404 (G02) · X45 action guard admits any signed-in state (G02) · X46 guard client drops the Lab cookie options (G03) · X47 guard's RPC sends an identity (G03) · X48 unguarded arrow-const action (H1, B01) · X49 unguarded default-export action (B01) · X50 unguarded inline action in the provider layout (B01).

### Wiring request added

- **WR-L1-7 (ruling for the coordinator; lab-sql + lab-access; supersedes the currency part of WR-L1-5 and aligns WR-L2-1).** Proposed text: *"A current provider membership has one definition, in SQL, on the database clock: `granted_at <= now() and (revoked_at is null or now() < revoked_at)`. lab-sql ships it once, as an internal function `lab_current_provider_memberships(p_user_id uuid)` (EXECUTE revoked from public/anon/authenticated). The session RPC `lab_my_provider_memberships()` (L1, WR-L1-5 shape, EXECUTE for authenticated only) is that function over `auth.uid()`. L2's `AccessStore.memberships_for_user` reads the same function through the service role. L2 keeps `workspaces()`'s `is_current` filter as a documented equivalent: it can only narrow, so any clock-skew disagreement fails closed. Per-call `membership()`/`permits()` checks are unchanged."* Option (a) is recommended over (b): (b) needs an infrx-api HTTP route plus gateway composition wiring for the Lab to reach L2's Python service. Acceptance, after L2-SQL merges, on `l4` (PG 57503): 2 consumers, 2 providers, 1 user in both, plus one membership revoked earlier (`revoked_at` in the past), one revoked during the run, and one not yet effective (`granted_at` in the future). The membership contract (`ProviderMembership`) has no `expires_at`, so "expired" means revoked in the past. The L1 RPC and L2 `workspaces()` must return the same set, and the revoked-during-run row must disappear from both on the next call.

### Estimate (remaining for L1)

optimistic 1 h / likely 2 h / pessimistic 4.5 h, confidence medium. Basis: R-1 and R-2 are closed in this round. What remains is the WR-L1-7 ruling, L2-SQL shipping the RPC, the real-RPC LAB-ACCESS run on `l4` (about 1 h once the RPC exists), and applying WR-L1-1..4. WR-L1-6 is not included.

- 2026-09-27T09:55Z: fix round appended (code head `2b1bd41b`; lane lab-app, LW1).
