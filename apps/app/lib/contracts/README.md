# Console contracts v1

The console half of [contracts v1](../../../../research/plan/01-contracts.md), encoded as
[08 §3, §4, §7 and §9](../../../../research/plan/08-contracts-v1-encoding.md) fix it. This
directory is coordinator-owned: a change to a type, a fixture or the fake's behaviour is a
contract revision, not a track-local edit.

| File | What it is |
|---|---|
| `money.ts` | Branded `Money` string, BigInt units of 1e-8 USD, `parse/format/add/sub/compare/isNegative`, display formatter |
| `types.ts` | Frozen vocabularies (`as const` lists with the unions derived from them), `SessionContext`, `Result`, `Page`, every DTO |
| `services.ts` | The `ConsoleServices` interface and the operation names |
| `fixtures/*.json` | Two organizations, sessions, money cases, trace content templates, judge runs |
| `fake-services.ts` | Deterministic fixture-backed `ConsoleServices` with failure injection |
| `conformance.ts` | The shared test bodies: one exported function taking a harness factory |

## Rules the interface exists to enforce

- The tenant is `session.orgId`. No operation takes an org id from the caller, except
  `adminGrant`'s `target_org_id`, which is authorized against `session.isOperator`.
- Feedback `channel`, `author_role`, `author_principal` and `calibration_set` are server-set;
  the input type has no way to supply them.
- Lists return `Page<T>` with an opaque cursor bound to the query that produced it, and reject
  `limit > 100` with `invalid_request` rather than clamping.
- Money crosses every boundary as a canonical eight-digit decimal string. `number` is never a
  monetary value — not in arithmetic, not in display.
- Failures come back as `Result` errors with the codes of 08 §3; nothing throws for an expected
  condition.

## U and V: build against the fake

```ts
import { createFakeConsoleServices } from "@/lib/contracts/fake-services.ts";
import type { ConsoleServices } from "@/lib/contracts/services.ts";

const services: ConsoleServices = createFakeConsoleServices();
const page = await services.usage(services.sessions.owner, { limit: 25 });
if (!page.ok) return renderError(page.error); // typed code, safe message
```

`services.sessions` gives `owner`, `member`, `operator` and `otherOwner` (a second
organization), and `services.ids` gives request and key identifiers for the states a page has
to render: `availableRequestId`, `offRequestId`, `otherOrgRequestId` (must be `not_found`),
`unknownRequestId`, `keyId`, `otherOrgKeyId`.

What the fixture data covers, so a page can be built without guessing:

- 137 usage rows, ~118 ledger entries and 137 traces for the established organization: more
  than one page of each at the maximum limit of 100.
- Every trace content state: `available`, `metadata_only`, `pending`, `lost`, `expired`, `off`.
- Outstanding holds, so `available` is strictly below `ledger_total`; unknown-usage rows that
  are held rather than charged; platform-absorbed and free failures that cost nothing.
- A zero-balance new organization with no ledger history (`sessions.otherOwner`).
- Judge runs in `dry_run`, `settled` and `ambiguous` state, a sample with
  `limited_evaluation` (no media, so no groundedness score), and a held budget.
- Per-operation failure injection: `services.failNext("usage", "dependency_unavailable")`
  makes exactly the next call fail, for loading/error/retry states.

Type-only imports keep the fake out of client bundles: import DTOs from `types.ts` in
components and call the services from server components or server actions.

## C: pass the same tests with the real implementation

`conformance.ts` exports the test bodies, so the real services are held to the fixtures'
behaviour rather than to a parallel set of assertions:

```ts
// apps/app/tests/c/console-services.test.ts
import { runConsoleServicesConformance } from "../../lib/contracts/conformance.ts";
import { createConsoleServices } from "../../lib/services/console.ts";

runConsoleServicesConformance(async () => {
  const services = await createConsoleServices({ /* task-local PG/CH per 08 §8 */ });
  return { services, sessions: await seedSessions(), ids: await seedIds() };
}, "PostgreSQL ConsoleServices");
```

The harness must supply a *fresh* tenant per factory call (the suite mutates settings, submits
feedback and issues grants) and the identifiers listed above, including one belonging to
another organization. The suite asserts invariants, never row counts, so real data satisfies
it: pagination covers every row exactly once in a stable descending order, cursors are
rejected when forged or reused with different filters, `available = ledger_total - reserved`,
cross-tenant identifiers are `not_found`, members cannot mutate, non-operators cannot grant or
list organizations, grants are idempotent per key and conflict on a changed payload, retention
is capped at 90 days, consent moves independently of trace mode, and content DTOs never carry
a storage path or signed URL.

Passing it against the fake means **implemented**. C is *integrated* only when the same suite
passes against real PostgreSQL and ClickHouse (F-CONTRACT, TRACE-TENANT, DUR-RLS,
CONSOLE-FLOWS); see [04-verification.md](../../../../research/plan/04-verification.md).

## Amendments requested against 08 §9

- `keys.list/create/revoke` are declared here although §9 does not list them: U2 needs key
  management behind the same session and role checks. Recorded in the F2 evidence report.
- `adminGrant` takes `target_org_id`, the one caller-supplied organization id in the
  interface, because an operator grant has to name its target. Every tenant-scoped operation
  still binds the organization from the session only.
- `usageSummary` and `usageDaily` are separate operations beside `usage`, matching the existing
  `org_usage_summary` / `org_usage_daily` SQL functions.

## Verification log

- 2026-09-20: Written with F2 (console half). `pnpm --dir apps/app test`, `lint`, `tsc --noEmit`
  and `build` passed locally against the fake; no real service, cloud or paid provider was
  contacted.
