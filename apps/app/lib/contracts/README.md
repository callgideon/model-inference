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
| `fixtures/*.json` | Two organizations, sessions, money arithmetic/display constants, trace content templates, judge runs |
| `fake-services.ts` | Deterministic fixture-backed `ConsoleServices` with failure injection |
| `conformance.ts` | The shared test bodies: `runConsoleServicesConformance` and `runMutationSafetyConformance`, each taking a harness factory |
| `../../tests/contracts/money_cases.json` | The cross-language money accept/reject list (R11): data only, sorted by input, diffed against the Python half |

## Rules the interface exists to enforce

- The tenant is `session.orgId`. No operation takes an org id from the caller, except
  `adminGrant`'s `target_org_id`, which is authorized against `session.isOperator`.
- Feedback `channel`, `author_role`, `author_principal`, `calibration_set` and `rubric_version` are
  server-set; the input type has no way to supply them. **`feedback.submit` always produces a
  customer signal** — `channel: "console"`, `author_role: "customer"`, `calibration_set: false`,
  `rubric_version: null` — whatever authority the session holds, because the control belongs to the
  customer whose organization is open (02, R19). An operator label comes from `calibration.label`
  and nowhere else; its entry is the only one that may carry `author_role: "operator"`,
  `calibration_set: true` and a rubric version.
- **No customer view names an operator** (R41). `LedgerEntry.actor` is the organization's own
  principal for its own actions, the literal `platform` (`PLATFORM_ACTOR`) for anything an operator
  did, and null where nobody did; consent history records `platform` when an operator changes it. The
  real principal lives in the operator's views — `adminAudit`, and the ledger read *by an operator
  session* — so the masking is a view, not missing data. A tenant learns that the platform acted,
  never which person at the platform did.
- **A key secret is shown exactly once, in the first response** (R16). The idempotency record holds
  the key's id, never the secret, so a replay — by the creator, another owner or an operator —
  returns the key's *current* metadata with `secret: null` and `replayed: true`. There is nothing
  stored for an authorization mistake to hand over, which is a stronger property than a check.
- **An unknown input field is `invalid_request`**, mirroring pydantic `extra="forbid"` on the
  Python side (08 §2). A caller that smuggles `org_id`, `author_role`, `channel` or a storage key
  is refused, not silently ignored: ignoring it returns a page that looks right and teaches the
  caller nothing. The accepted names per input are exported from `types.ts`
  (`USAGE_QUERY_FIELDS`, `FEEDBACK_INPUT_FIELDS`, …).
- Lists return `Page<T>` with an opaque cursor bound to its tenant, operation and filters — but
  not to `limit`, so the page size may change mid-walk. `limit > 100` is `invalid_request`, never
  a clamp. The cursor carries the row's **sort key** — `(created_at, id)`, or `(name, org_id)` for
  the operator list — and the next page resumes strictly *after* it. Equal timestamps are therefore
  unambiguous, a row arriving at the head shifts nothing, and the cursor's own row may leave the
  result set mid-walk (a filter stops matching it) without breaking the walk.
- Money crosses every boundary as a canonical eight-digit decimal string. `number` is never a
  monetary value — not in arithmetic, not in display. The domain is `numeric(20, 8)`
  (`|value| < 10^12`, R11), identical in both languages.
- Failures come back as `Result` errors with the codes of 08 §3; nothing throws for an expected
  condition, and **an amount or total outside the money domain is an expected condition**.
- **Every mutation validates completely before it writes anything**, and files its idempotency
  record only afterwards. So a refused operation leaves the state it touched byte-identical, and
  the retry that always follows a refusal has exactly one effect. An idempotency key is scoped to
  (caller organization, operation, target organization, payload): required on `adminGrant` and
  `feedback.submit` (R3), `calibration.label`, `adminSetSuspension` and `adminSetEntitlements`
  (R19), optional on `keys.create`, `keys.revoke` and `settings.update` (R16). A key replayed with a
  changed payload — including a changed target organization — is `idempotency_conflict`, never a
  second effect. Keys are bounded at 255 characters (08 §3).
- **The effect and its idempotency record are one write.** If they can diverge, a crash between them
  turns the client's retry into a second effect, so C must write the record in the same transaction
  as the effect. A grant additionally carries its key as the ledger row's `ref`, so a store that
  lost the record can still recognise the grant it already made.

## U and V: build against the fake

```ts
import { createFakeConsoleServices } from "@/lib/contracts/fake-services.ts";
import type { ConsoleServices } from "@/lib/contracts/services.ts";

const services: ConsoleServices = createFakeConsoleServices();
const page = await services.usage(services.sessions.owner, { limit: 25 });
if (!page.ok) return renderError(page.error); // typed code, safe message

// A mutation carries the idempotency key of the form submission, so a double-submit,
// a refresh or a retry after a timeout produces one signal rather than two.
const ack = await services.feedback.submit(services.sessions.owner, {
  request_id,
  name: "rating",          // thumb | rating | correction | comment
  value: 4,                // boolean | 1..5 | non-empty text, per name (R3)
  comment: "cut off early", // optional
  idempotency_key: formToken,
});
```

`services.sessions` gives six sessions: `owner`, `member`, `operator` (a platform operator who also
owns the first organization), `operatorMember` (a platform operator whose organization role is only
`member` — operator authority is the flag, never the role), `otherOwner` (a second organization) and
`suspendedOwner` (an organization suspended before anything runs, so `org_suspended` is reachable —
R18). `services.ids` gives the identifiers a page has to render: `orgId`, `otherOrgId`,
`suspendedOrgId`, `availableRequestId`, `offRequestId`, `otherOrgRequestId` (must be `not_found`),
`unknownRequestId`, `keyId`, `otherOrgKeyId` and `modelId` (a model the platform serves).

What the fixture data covers, so a page can be built without guessing:

- 160 usage rows and 107 trace rows for the established organization (the 53 off-mode requests
  have no trace row, R13), plus 137 ledger entries: more than one page of each at the maximum
  limit of 100. Some of those rows **share a `created_at`**, deliberately placed either side of the
  page boundaries the suite walks, because that is where a keyset cursor either holds or quietly
  drops a row.
- Every trace content state. Five of them — `available`, `metadata_only`, `pending`, `lost`,
  `expired` — appear in the trace list; `off` is reached only from a usage row, through
  `traceDetail`/`traceContent` on `ids.offRequestId`.
- Non-terminal rows (`preparing`, `queued`, `running`) with `settlement_state: null` and an
  outstanding `max_hold`, so a usage table has to render "not settled yet" rather than a state.
- Outstanding holds, so `available` is strictly below `ledger_total`; unknown-usage rows that
  are held rather than charged; platform-absorbed and free failures that cost nothing.
- A ledger with all four kinds, including one legacy `purchase` row that must render although
  nothing creates one (R13).
- Seeded feedback for each R3 name (`thumb`, `rating`, `correction`, `comment`), both channels, a
  judge-authored entry, and one `calibration_label` — the operator-authored, calibration-set shape
  that only `calibration.label` creates.
- A suspended organization (`sessions.suspendedOwner`, `ids.suspendedOrgId`) for the
  `org_suspended` state, and the operator controls to suspend, restore and entitle any organization.
- A zero-balance new organization with no ledger history (`sessions.otherOwner`).
- Judge runs in `dry_run`, `settled` and `ambiguous` state, a sample with
  `limited_evaluation` (no media, so no groundedness score), and a held budget. `judgeRuns` is
  owner and operator only, so a member session gets `forbidden` (R13).
- Per-operation failure injection: `services.failNext("usage", "dependency_unavailable")`
  makes exactly the next call fail, for loading/error/retry states. An injected failure is
  returned before anything is written, so it never leaves half-changed state behind. A fourth
  argument, `"after_write"`, instead loses the *response* after the write and its idempotency
  record are committed — the crash-after-commit case, for testing that a retry replays.

Type-only imports keep the fake out of client bundles: import DTOs from `types.ts` in
components and call the services from server components or server actions.

### Operator surface (R19)

```ts
// Operator authority is the session flag, never a field in the request.
await services.adminSetSuspension(operatorSession, {
  target_org_id, suspended: true, reason: "payment dispute", idempotency_key: token,
});
await services.adminSetEntitlements(operatorSession, {
  target_org_id, model_ids: ["marlin-2b@2026-09-01"],
  limits: { max_concurrent_requests: 4 }, reason: "pilot tier", idempotency_key: token,
});
await services.calibration.label(operatorSession, {
  request_id, rubric_version: 3, label: "partially_correct", idempotency_key: token,
});
const set = await services.calibration.list(operatorSession, { limit: 50 });
```

**Suspension gates new work and configuration only (R33).** A suspended organization keeps every
read — `usage*`, `balances`, `ledger`, `traces`, `traceDetail`, `traceContent`, `feedback.list`,
`settings.get`, `keys.list` — and keeps `keys.revoke`, so a leaked key can always be revoked;
refusing that would make a suspension a security problem instead of a billing one. `keys.create`,
`settings.update`, `feedback.submit` and `judgeRuns` return `org_suspended`. Operator operations keep
working, or a suspension could never be lifted. It never rewrites a ledger entry or a terminal usage
row, because accounting that already happened is a fact. Entitlement limit names are a closed provisional set
(`ENTITLEMENT_LIMIT_NAMES`); an unknown name is `invalid_request`, not an ignored control.

**The three entitlement states, and how U3 must word them (R24).** `model_ids` is deliberately
nullable, because "nobody has decided" and "decided: nothing" are different facts and rendering them
the same way is how an operator suspends a tenant by accident:

| `model_ids` | Means | Suggested copy |
|---|---|---|
| `null` | The platform default set. No per-organization decision recorded; `updated_at` is null too. | "Platform default models" |
| `[]` | **Nothing entitled** — every admission fails `model_not_entitled`. A recorded, fail-closed decision. | "No models — all requests will be refused" |
| `["…"]` | Exactly those models, nothing else. | The list, named |

A list may only name models the platform serves and may not repeat one; `null` and `[]` hash to
different idempotency payloads, so a key replayed from one to the other is `idempotency_conflict`
rather than a silent switch. `services.ids.modelId` is a served model id for building against.

**A replay returns the original result, not the current state (R34).** A replayed grant reports the
balance as it is now, but a replayed suspension, entitlement write or label returns exactly what the
first call returned. U3 must re-read the state it is about to render after a replay rather than
trusting the replayed body — the point of a replay is that nothing happened this time.

**Operator scope is platform-wide in the pilot (R26).** `calibration.label` reaches a trace in *any*
organization: the operator flag is the authority, the tenant comes from the row the label names, and
the entry records the operator principal. Contracts v1 has no notion of an operator scoped to a
subset of organizations — a deliberate pilot decision, not an oversight. If that changes it is a
contract revision, and `calibration.label`/`calibration.list` are where it lands.

## C: pass the same tests with the real implementation

`conformance.ts` exports the test bodies, so the real services are held to the fixtures'
behaviour rather than to a parallel set of assertions:

```ts
// apps/app/tests/c/console-services.test.ts
import {
  runConsoleServicesConformance,
  runMutationSafetyConformance,
} from "../../lib/contracts/conformance.ts";
import { createConsoleServices } from "../../lib/services/console.ts";

const harness = async () => {
  const services = await createConsoleServices({ /* task-local PG/CH per 08 §8 */ });
  return { services, sessions: await seedSessions(), ids: await seedIds() };
};

runConsoleServicesConformance(harness, "PostgreSQL ConsoleServices");
// `runConsoleServicesConformance` already calls this; call it alone while wiring writes.
runMutationSafetyConformance(harness, "PostgreSQL ConsoleServices");
```

The harness must supply a *fresh* tenant per factory call (the suite mutates settings, submits
feedback and issues grants) and the identifiers listed above, including one belonging to another
organization and one belonging to a suspended one. Beyond that it must supply:

- **two operator sessions**: one that also owns `ids.orgId`, and one whose organization role is only
  `member`. Operator authority is the flag; a harness with a single owner-operator cannot tell an
  implementation that checks the flag from one that checks the role.
- **rows that share a `created_at`** in usage, the ledger and traces, and all timestamps in one list
  in the same format: the suite asserts a tie is present and that the widths match, because a
  `(created_at, id)` keyset resume is only tested where timestamps actually collide.
- **an outstanding hold** on at least one usage row, and two organizations of *different sizes* with
  *different settings*, so the tenant-isolation and wallet cases compare real numbers rather than
  shapes.
- **an entitlement record of each kind** — `null`, `[]` and a non-empty list — across the
  organizations.
- a cursor may be any opaque string: the suite never parses one, and
  `tests/contracts/opaque-cursor.test.ts` demonstrates the suite passing against cursors the
  implementation alone can read. The suite asserts invariants, never row counts, so real data satisfies
it: each list walked at two page sizes yields the identical ordered id list (so a keyset
off-by-one that drops a row at a page boundary fails, not just a duplicate), the order is stable
and descending, cursors are rejected when forged, reused with different filters or minted for
another tenant, `available = ledger_total - reserved`, a trace's `error_code` is the code 08 §3
pairs with its HTTP status, an out-of-vocabulary filter is `invalid_request`,
cross-tenant identifiers are `not_found`, members cannot mutate, non-operators cannot grant or
list organizations, grants are idempotent per key and conflict on a changed payload, retention
is capped at 90 days, consent moves independently of trace mode, and content DTOs never carry
a storage path or signed URL.

`runMutationSafetyConformance` is the half that hurts to implement, so it is stated plainly. It
asserts that an amount which would take the wallet out of `numeric(20, 8)` is refused with
nothing written and is still refused on retry under the same key, and that balances and the
operator list keep working afterwards; that a refusal on each mutating operation leaves the
ledger, keys, settings and feedback byte-identical; that an idempotency key is scoped to its
operation, organization and payload, and replays rather than duplicating; that generated ids
never collide within or across organizations; that a list walked while rows arrive at its head
returns exactly the rows that existed when the walk began, in order; that a *filtered* walk
survives its own cursor row leaving the filter; that nothing creates a `purchase` entry; and that
an invented input field is `invalid_request`. Nothing in the suite may throw: a rejected promise
fails the test with its own message.

The main suite adds, this round: a created secret is shown once and to the first response only —
same user, another session, after revocation — and appears in no response of any read operation;
the same console control yields `author_role: "customer"` for owner, member *and* operator
sessions, while `calibration.label` is the only producer of an operator-authored, calibration-set
entry; a suspended organization can do nothing while the operator still sees it and its wallet;
suspension and restoration leave every ledger and usage row byte-identical; entitlements accept
only the names the contract knows; and 27 error codes carry an HTTP status, which is how a code
added on one side of the parity table only gets caught here instead of at integration.

Passing it against the fake means **implemented**. C is *integrated* only when the same suite
passes against real PostgreSQL and ClickHouse (F-CONTRACT, TRACE-TENANT, DUR-RLS,
CONSOLE-FLOWS); see [04-verification.md](../../../../research/plan/04-verification.md).

## Rulings of contract revision r1, as encoded here

[08 §10](../../../../research/plan/08-contracts-v1-encoding.md) settled the amendments F2 raised.
What that means in this directory:

- **R13 accepted** `keys.list/create/revoke` (U2 needs key management behind the same session and
  role checks), `adminGrant.target_org_id` with the idempotency scope including the target and the
  payload, separate `usageSummary`/`usageDaily`, `TraceMode` = `off | minimal | full`,
  `UsageRow.settlement_state` nullable before terminal, `judgeRuns` owner and operator only, and
  `ERROR_CODE_HTTP_STATUS` — which must equal the Python table exactly (the G0 parity test).
- **R13 amended** two things this README previously stated the other way round:
  - `LedgerEntryKind` **keeps `purchase`** as a legacy read-only value. Historical rows must
    render; nothing creates one, which `runMutationSafetyConformance` asserts.
    `CREATABLE_LEDGER_ENTRY_KINDS` is the set a running system may write.
  - Off-mode requests **do not** appear in `traces` at all — an off-mode request has no trace row.
    They remain usage rows, and `traceDetail`/`traceContent` reached from a usage row report
    availability `off` with `content: null`, never `not_found`.
- **R3** fixes the feedback body: `name` ∈ `thumb | rating | correction | comment`, `value` a
  boolean for `thumb`, an integer 1–5 for `rating`, non-empty text for `correction`/`comment`, an
  optional `comment` alongside, and a required `idempotency_key`. An empty body is
  `invalid_request`. This replaces the earlier `rating: "up" | "down"` with `comment`/`correction`
  fields; V reads `research/traces/06` §2 for the field semantics.
- **R11** bounds money to `numeric(20, 8)` in both languages, with the accept/reject set in
  `tests/contracts/money_cases.json`. `adminGrant` refuses a negative, zero, non-decimal or
  over-scale amount, and refuses a *total* that would leave the domain.
- **`TraceMode` `off | minimal | full`** supersedes the `off | metadata | full` of
  [`research/traces/07-console-spec.md`](../../../../research/traces/07-console-spec.md)
  (`TraceLevel`, `trace-level-select.tsx`, the `X-Infrx-Trace` header). V reads 07 for field
  semantics and must write `minimal` wherever 07 writes `metadata`; the fake rejects `"metadata"`
  as `invalid_request`.

### Round-3 change requests, as ruled

- **R16**: `keys.create`, `keys.revoke` and `settings.update` accept an optional `idempotency_key`
  (`revoke` as a third argument) — accepted. The secret question was ruled the other way from the
  request: a replay returns metadata with `secret: null` and `replayed: true`, and nothing stores
  the secret. C stores the key id against the record and rereads the key, exactly as the fake does.
- **R17**: `MAX_KEY_NAME_CHARS` = 200 and `MAX_GRANT_REASON_CHARS` = 500 accepted as provisional;
  unknown input fields are `invalid_request` in both languages. `MAX_RUBRIC_VERSION` = 1000,
  `MAX_ENTITLEMENT_LIMIT` = 1,000,000 and the closed `ENTITLEMENT_LIMIT_NAMES` set are this round's
  equivalents, and equally provisional.
- **R18**: the suspended organization and session are in the harness, and suspension never alters
  existing terminal accounting.
- **R19**: `adminSetSuspension`, `adminSetEntitlements`, `calibration.label` and `calibration.list`
  exist, operator-only, audited and idempotent; ordinary console feedback from an operator session
  stays a customer signal.
- **R22**: `upload_expired` is a 410 in the union and the status table.
- **R24**: the `OrgEntitlements` shape is the contract, with `model_ids` `null` = platform default,
  `[]` = nothing entitled, a list = exactly that set (table above). D1 aligns its schema; the
  per-model defaults stay in `models.limits`.
- **R25**: `journal_write_failed` is an internal-only code as well as a `TerminalCause`, so the
  union is 37 codes: 27 with an HTTP status, 2 in-stream (`IN_STREAM_ONLY_CODES`), 8 internal
  (`INTERNAL_ONLY_CODES`). The three sets are exported for the G0 parity test and asserted to
  partition the union exactly.
- **R26**: platform operators are platform-wide; no org-subset scoping in contracts v1.

## `pnpm test:mutants` — the suite's own test

A conformance case that names an invariant it cannot enforce is worse than no case: it tells C the
invariant is checked. `pnpm test:mutants` (`tests/contracts/run-mutants.mjs`, list in
`tests/contracts/mutants.json`) applies each declared single-edit mutant to a temporary copy of the
console and requires it to fail at least one case of the **exported** conformance functions — not the
fake-only tests, which are not what C runs. A surviving mutant exits non-zero, and so does a mutant
whose `find` text no longer matches, because a stale mutant tests nothing.

**What counts as a kill.** A non-zero exit is not enough: each mutant declares in `cases` the
conformance cases that must catch it, and a kill requires one of *those* cases to fail by name. A
mutant that fails to load, does not parse, uses a construct Node's type stripping rejects, hangs,
fails without naming a case, or names a case the suite does not have is a **runner error** — it told
us nothing — and fails the run, as does a stale `find`. Three mutants kill by *throwing*, because an
accepted out-of-domain value throwing downstream is the defect; they are marked `kills_by: "throw"`.

`node tests/contracts/run-mutants.mjs --self-test` checks the runner against its own claims: a
syntax error, a load throw, an `enum`, a hang, a no-op, a stale `find`, a real defect attributed to
the wrong case, a genuine kill, and a mutant with no declared cases must each be classified
correctly. Run it after touching the runner.

It is deliberately not part of `pnpm test`: it costs one Node process per mutant. Add a mutant with
every new invariant a case claims — and if an invariant cannot be expressed as a mutant an exported
case kills, it is fake-only and the README should say so rather than the case implying otherwise.

## Known fake-only behaviour

Things U, V and C should not read as contract:

- The fake's cursor is base64 JSON `{a, b, k}` — the row's `(created_at, id)` key plus a hash of the
  query scope — resolved by an O(n) scan. That shape is *not* contract: the exported suite treats a
  cursor as opaque (R36) and `tests/contracts/opaque-cursor.test.ts` proves it by running the whole
  suite against a wrapper whose cursors the fake cannot parse. C should sign or encrypt its cursors;
  the fake's are tamper-*evident* for the query scope but not tamper-proof, so swapping the key
  component for another row's resumes elsewhere rather than failing. The shape-aware forgery cases
  live in the fake's own tests, not in the exported suite.
- The fixture clock is frozen at `orgs.json`'s `clock`; timestamps generated by a mutation
  advance a local counter by one second per call.
- Idempotency records live for the lifetime of the instance and never expire, so the fake cannot
  produce `idempotency_expired`. C's records expire (08 §5) and it must.
- **Two invariants are fake-only, because `ConsoleServices` cannot express them.** (1) *The
  idempotency record is written in the same transaction as the effect*: the interface has no failure
  injection, so no exported case can lose a response between the two. The fake's `after_write`
  injection covers it, and C gets it from its database transaction — a store that commits the effect
  and the record separately will double-apply a retry, and no conformance run will tell it so. (2)
  *An operator label does not shift the id or timestamp a customer's next write receives*: only
  observable because the fake's ids and clock are deterministic. Both are pinned in
  `tests/contracts/services.test.ts`.
- `unsafeDebugState()` exists on the fake only, for the one assertion the contract cannot make from
  outside: that a key secret is retained nowhere in the state. Nothing but a test may call it, and C
  has no equivalent — the portable half of that assertion is the read sweep in the conformance suite.
- `calibration.list` scans every organization, because an operator's calibration set spans tenants.
  C will index it; the suite only requires that a non-operator gets `forbidden`.
- The entitlement limit names, `MAX_RUBRIC_VERSION` and the calibration label vocabulary are
  provisional (R17): they are shaped like the contract, and the numbers are this round's guesses.
- `KNOWN_MODEL_IDS` is the fixture's model list. C validates entitlements against the real model
  catalogue instead; only the shape of the check is contract.
- Row counts, ids and secrets are fixture data: 160 usage rows, the `sk-infrx-FAKE…` secret shape
  and the `fb_<namespace><ordinal>` id shape are all fake-only. The contract is that ids are
  opaque, not how these are built.
- Row counts and the third organization are fixture data. `org_suspended` is now reachable through
  `sessions.suspendedOwner`, so the state is exercised rather than merely present in the code.

## Verification log

- 2026-09-20: Written with F2 (console half). `pnpm --dir apps/app test`, `lint`, `tsc --noEmit`
  and `build` passed locally against the fake; no real service, cloud or paid provider was
  contacted.
- 2026-09-20: Post-review revision. Recorded the three amendments the evidence claimed but the
  README omitted (ledger kinds, off-mode trace rows, `TraceMode` superseding 07's `TraceLevel`),
  added the fake-only behaviour section, and restated what the suite proves about pagination now
  that each list is walked at two page sizes. Row counts are unchanged (137 / 117 / 137).
- 2026-09-20: Round-6 revision. The mutation runner can no longer report a false kill: a kill needs a
  named case the mutant declares, and load, parse, stripping, hang and unnamed failures are runner
  errors that fail the run (nine self-tests pin it). One false kill was deleted and the invariant it
  claimed — the idempotency record written in the same transaction as the effect — is recorded as
  fake-only, with a note for C that its database transaction is what provides it. R41 keeps operator
  principals out of every customer view. New cases close the invariants the suite named but could not
  enforce: `adminAudit` by role *and* query shape, R35 over every label with an unannotated trace,
  replays and conflicts appending no audit entry, grant and entitlement before/after, the
  member-operator across the whole matrix, an operator whose own organization is suspended, a
  suspension that can be lifted, and the wallet identity on a grant result.
- 2026-09-20: Round-5 revision, for a review that found the *suite* rather than the fake wanting.
  The exported cases now treat cursors as opaque (R36, proved by `opaque-cursor.test.ts`), drive the
  role, suspension and input cases from the operation list, target operator writes at another
  organization with a member-operator session, change one payload field per conflict assertion,
  refuse a later field in every mutating operation with deep-equal snapshots, compare filtered walks
  and per-organization figures against computed expectations, and require an outstanding hold.
  Suspension gained its scope (R33), operator writes an append-only audit trail with `adminAudit`
  (R34), and calibration labels became invisible to customers by living outside the feedback list
  (R35). `pnpm test:mutants` (R32) holds all of it: 115 declared mutants, 115 killed by the exported
  suite.
- 2026-09-20: Rulings R24–R26 applied. Entitlements fail closed with three explicit states and
  copy guidance for U3, `journal_write_failed` joins the internal-only set (37 codes: 27 / 2 / 8,
  partition asserted), and platform-wide operator scope is recorded as a deliberate pilot decision.
- 2026-09-20: Round-4 revision. A created key's secret is shown exactly once and stored nowhere
  (R16); ordinary console feedback is a customer signal whatever the session, and operator labels
  come only from the new `calibration.label` (R19, with `adminSetSuspension` and
  `adminSetEntitlements`); a suspended organization and session are in the harness (R18);
  `upload_expired` joins the status table at 410 (R22); the two provisional bounds are confirmed and
  joined by three more (R17). Cursors carry the `(created_at, id)` sort key and resume after it, the
  fixture seeds timestamp ties at the page boundaries, and the discovery guard now reports any path
  with a dot segment, which no glob runs.
- 2026-09-20: Round-3 revision for contract revision r1. Rewrote the amendment section as the
  rulings actually settled (R3 feedback body, R11 money domain, R13 — including the two items this
  file previously stated the other way round: `purchase` is kept as a legacy kind, and off-mode
  requests are absent from `traces`). Documented the validate-before-write rule, the idempotency
  scope on every mutating operation, the refusal of unknown input fields, the keyset cursors, and
  `runMutationSafetyConformance` as the second entry point C runs. Row counts change with the
  fixture: 160 usage rows, 107 trace rows, 137 ledger entries.
