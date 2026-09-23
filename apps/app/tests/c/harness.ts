/**
 * The C1 test harness: an in-memory query port, and a dataset seeded from the shared fake.
 *
 * The fake (`lib/contracts/fake-services.ts`) is the executable specification of the console
 * contract, and its `unsafeDebugState()` is the fixture data D1 will hold: two organizations of
 * different sizes, a suspended one, a zero-balance one, timestamp ties either side of the page
 * boundaries the suite walks, outstanding holds, every trace content state, every ledger kind and
 * every entitlement state. Flattening that state into relations gives the real services the same
 * data the fake answers from — without inventing a second fixture set that could drift from it, and
 * without a new dependency (08 §6: the console dependency set is frozen).
 *
 * The port interprets a `QueryPlan` the way the rendered SQL says it should: predicates, then the
 * tenant, then the keyset bound, then the order, then the limit. It is a *test double for the
 * executor* — the statements in `lib/services/query.ts` are what will run against D1's schema, and
 * they are integration-pending until D1 and T3 land.
 */

import { moneyFromUnits, moneyUnits, type Money } from "../../lib/contracts/money.ts";
import { createFakeConsoleServices } from "../../lib/contracts/fake-services.ts";
import orgsFixture from "../../lib/contracts/fixtures/orgs.json" with { type: "json" };
import { createConsoleServices } from "../../lib/services/console.ts";
import { namedQuery, type QueryPlan, type QueryPort, type Row } from "../../lib/services/query.ts";
import type { ConsoleHarness } from "../../lib/contracts/conformance.ts";

export const TEST_CURSOR_SECRET = "c1-test-cursor-secret-0123456789";

export type Dataset = Record<string, Row[]>;

/**
 * The two UTC forms of one instant, as a single comparable string: `+00:00` becomes `Z` and the
 * fraction is padded to six digits, exactly as the service's projection does.
 *
 * PostgreSQL compares `timestamptz` **values**, so `…12:00:00+00:00` and `…12:00:00.000000Z` are equal
 * there. A double that compared the raw strings instead is not a smaller version of that behaviour, it
 * is a different one: `+` sorts before `Z`, so a cursor minted from the projected form would never find
 * its row again, and a walk over a `+00:00` relation repeated or lost rows. The keyset must compare
 * instants; the note applies to the supabase-js port C2 writes, too.
 */
const TIMESTAMP_FORM = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|\+00:00)$/;

function comparable(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  const match = TIMESTAMP_FORM.exec(text);
  if (match === null) return text;
  return `${match[1]}.${(match[2] ?? "").slice(0, 6).padEnd(6, "0")}Z`;
}

function compare(a: unknown, b: unknown): number {
  const left = comparable(a);
  const right = comparable(b);
  return left < right ? -1 : left > right ? 1 : 0;
}

function matches(row: Row, field: string, op: string, value: unknown): boolean {
  const actual = row[field];
  switch (op) {
    case "eq":
      return String(actual) === String(value);
    case "ne":
      return String(actual) !== String(value);
    case "not_null":
      return actual !== null && actual !== undefined;
    case "gte":
    case "lte":
    case "gt": {
      // Timestamps compare as instants, never as strings: `.000Z` and `Z` are the same moment, and
      // `.` sorts before `Z`, so a lexical range filter drops a row stamped on the boundary.
      const isTime = typeof actual === "string" && typeof value === "string" && !Number.isNaN(Date.parse(value)) && value.includes("T");
      const left = isTime ? Date.parse(actual) : Number(actual);
      const right = isTime ? Date.parse(value as string) : Number(value);
      if (Number.isNaN(left) || Number.isNaN(right)) return false;
      if (op === "gte") return left >= right;
      if (op === "lte") return left <= right;
      return left > right;
    }
    default:
      throw new Error(`unsupported comparison ${op}`);
  }
}

function sumOf(rows: Row[], field: string): unknown {
  let numeric = 0;
  let units = BigInt(0);
  let money = false;
  for (const row of rows) {
    const value = row[field];
    if (value === null || value === undefined) continue;
    if (typeof value === "string") {
      money = true;
      units += moneyUnits(value as Money);
    } else {
      numeric += Number(value);
    }
  }
  return money ? moneyFromUnits(units) : numeric;
}

/** The executor stand-in. It receives plans, never SQL, and never a caller's organization. */
export function createMemoryPort(data: Dataset): QueryPort {
  return {
    async run(plan: QueryPlan): Promise<Row[]> {
      const spec = namedQuery(plan.name);
      let rows = [...(data[spec.source] ?? [])];
      for (const predicate of plan.predicates) {
        rows = rows.filter((row) => matches(row, predicate.field, predicate.op, predicate.value));
      }
      if (plan.tenant !== null) {
        const field = spec.tenantField;
        if (field === undefined) throw new Error(`${plan.name} binds a tenant with no tenantField`);
        rows = rows.filter((row) => String(row[field]) === plan.tenant?.value);
      }
      if (spec.sort !== undefined) {
        const direction = spec.sort.direction === "desc" ? -1 : 1;
        rows.sort(
          (a, b) =>
            direction *
            (compare(a[spec.sort!.at.field], b[spec.sort!.at.field]) ||
              compare(a[spec.sort!.id.field], b[spec.sort!.id.field])),
        );
        if (plan.keyset !== null) {
          const bound = plan.keyset;
          rows = rows.filter((row) => {
            const order =
              compare(row[spec.sort!.at.field], bound.at) || compare(row[spec.sort!.id.field], bound.id);
            return direction === -1 ? order < 0 : order > 0;
          });
        }
      }
      if (spec.aggregates !== undefined) {
        // Grouped the way the statement groups: by the tenant column when there is one, and by the day
        // when there is one. The key comes from the rows, never from the plan, so a port that returned
        // another organization's rows produces a row that says so.
        const tenantField = spec.tenantColumn === null ? null : spec.tenantField ?? null;
        const groups = new Map<string, Row[]>();
        for (const row of rows) {
          const parts: string[] = [];
          if (tenantField !== null) parts.push(String(row[tenantField]));
          if (spec.groupBy !== undefined) parts.push(String(row.created_at ?? "").slice(0, 10));
          const key = parts.join("\u0000");
          const bucket = groups.get(key) ?? [];
          bucket.push(row);
          groups.set(key, bucket);
        }
        const out: Row[] = [];
        for (const bucket of groups.values()) {
          const aggregated: Row = {};
          if (tenantField !== null) aggregated[tenantField] = bucket[0][tenantField];
          if (spec.groupBy !== undefined) {
            aggregated[spec.groupBy.field] = String(bucket[0].created_at ?? "").slice(0, 10);
          }
          for (const aggregate of spec.aggregates) {
            const selected =
              aggregate.filters === undefined
                ? bucket
                : bucket.filter((row) =>
                    aggregate.filters!.every((predicate) =>
                      matches(row, predicate.field, predicate.op, predicate.value),
                    ),
                  );
            aggregated[aggregate.name] =
              aggregate.kind === "count" ? selected.length : sumOf(selected, aggregate.field ?? "");
          }
          out.push(aggregated);
        }
        if (spec.groupBy !== undefined) {
          const direction = spec.groupBy.direction === "desc" ? -1 : 1;
          out.sort((a, b) => direction * compare(a[spec.groupBy!.field], b[spec.groupBy!.field]));
        }
        // The rendered statement carries `limit`, so the double honours it for grouped aggregates too.
        return plan.limit === null ? out : out.slice(0, plan.limit);
      }
      return plan.limit === null ? rows : rows.slice(0, plan.limit);
    },
  };
}

// ---------------------------------------------------------------------------
// Seeding
// ---------------------------------------------------------------------------

type DebugState = { orgs: DebugOrg[] };

type DebugOrg = {
  org_id: string;
  name: string;
  owner_email: string;
  created_at: string;
  suspended: boolean;
  suspension_reason: string | null;
  entitlements: { model_ids: string[] | null; limits: Record<string, number>; updated_at: string | null; updated_by: string | null };
  settings: {
    trace_mode: string;
    content_retention_days: number;
    evaluation_consent: boolean;
    consent_history: Record<string, unknown>[];
  };
  keys: Record<string, unknown>[];
  usage: Record<string, unknown>[];
  ledger: Record<string, unknown>[];
  traces: Record<string, unknown>[];
  judge: Record<string, unknown>[];
  /**
   * The operator calibration labels. They are stored in the same feedback relation as every other
   * entry (R43), which is exactly why they have to be seeded: dropping them here is what made the
   * R49 leak invisible — nothing in the dataset could come back through `feedback.list`.
   */
  labels: Record<string, unknown>[];
};

const CLOCK_MS = Date.parse((orgsFixture as unknown as { clock: string }).clock);

/**
 * D1's reconciliation, done in the seed: the wallet summary columns are the ledger total and the sum
 * of the outstanding holds. `balances` then reads those columns instead of summing the ledger, which
 * is the whole point of the change — and the conformance suite checks the two against each other.
 */
function walletRow(org: DebugOrg): Row {
  let ledgerUnits = BigInt(0);
  for (const entry of org.ledger) ledgerUnits += moneyUnits(entry.delta as Money);
  let reservedUnits = BigInt(0);
  for (const row of org.usage) {
    if (row.max_hold !== null && row.max_hold !== undefined) reservedUnits += moneyUnits(row.max_hold as Money);
  }
  return {
    org_id: org.org_id,
    ledger_total: moneyFromUnits(ledgerUnits),
    reserved_total: moneyFromUnits(reservedUnits),
  };
}

/**
 * Operator audit entries. C3 appends them; the read, its target filter and its paging are C1's, so
 * the harness seeds enough of them to page — otherwise `adminAudit` is only ever tested empty.
 */
function seedAudit(orgs: DebugOrg[]): Row[] {
  const rows: Row[] = [];
  let ordinal = 0;
  // r2: `audit_entries.target_org_id` is `on delete set null`, so the trail outlives its targets.
  // The entry is still the record that the write happened, and `admin_audit_page` is untenanted
  // (operator-only), so nothing else has to change for it to be readable. A `target_org_id` filter
  // must not sweep it into some surviving organization's page.
  rows.push({
    id: "aud_0000",
    at: new Date(CLOCK_MS - 60000).toISOString(),
    actor_principal: "operator@infrx.example",
    action: "admin_set_entitlements",
    target_org_id: null,
    reason: "closed the account's entitlements before deletion",
    before: { model_ids: null },
    after: { model_ids: [] },
    idempotency_key: "seed-entitlements-closed",
  });
  for (const org of orgs) {
    for (let i = 0; i < 12; i += 1) {
      ordinal += 1;
      rows.push({
        id: `aud_${String(ordinal).padStart(4, "0")}`,
        at: new Date(CLOCK_MS - ordinal * 60000).toISOString(),
        actor_principal: "operator@infrx.example",
        action: i % 2 === 0 ? "admin_grant" : "admin_set_suspension",
        target_org_id: org.org_id,
        reason: "seeded for the read side",
        before: null,
        after: { seeded: true },
        idempotency_key: `seed-${ordinal}`,
      });
    }
  }
  return rows;
}

export function seedDataset(state: DebugState): Dataset {
  const data: Dataset = { orgs: [], wallets: [], ledger: [], usage: [], keys: [], settings: [], consent: [], traces: [], feedback: [], judge: [], audit: [] };
  const cutoff = CLOCK_MS - 30 * 86400000;
  for (const org of state.orgs) {
    const wallet = walletRow(org);
    data.wallets.push(wallet);
    data.orgs.push({
      org_id: org.org_id,
      name: org.name,
      owner_email: org.owner_email,
      created_at: org.created_at,
      suspended: org.suspended,
      suspension_reason: org.suspension_reason,
      ledger_total: wallet.ledger_total,
      reserved_total: wallet.reserved_total,
      requests_30d: org.usage.filter((row) => Date.parse(String(row.created_at)) >= cutoff).length,
      model_ids: org.entitlements.model_ids,
      limits: org.entitlements.limits,
      entitlements_updated_at: org.entitlements.updated_at,
      entitlements_updated_by: org.entitlements.updated_by,
    });
    for (const entry of org.ledger) data.ledger.push({ ...entry, org_id: org.org_id });
    for (const row of org.usage) {
      // The fake holds the DTO's `accounting_regime`; `console_usage` holds the database's
      // `settlement_regime` (`legacy`/`pilot`), and this harness stands in for the view, so it
      // must speak the view's column names — the mapping back to the console vocabulary is the
      // projection's job and is what `accountingRegimeOf` is tested on.
      const { accounting_regime: regime, ...rest } = row;
      data.usage.push({
        ...rest,
        settlement_regime: regime === "legacy_usd" ? "legacy" : "pilot",
        org_id: org.org_id,
      });
    }
    for (const key of org.keys) data.keys.push({ ...key, org_id: org.org_id });
    data.settings.push({
      org_id: org.org_id,
      trace_mode: org.settings.trace_mode,
      content_retention_days: org.settings.content_retention_days,
      evaluation_consent: org.settings.evaluation_consent,
    });
    org.settings.consent_history.forEach((entry, index) => {
      data.consent.push({ ...entry, version: index + 1, org_id: org.org_id });
    });
    // R43: a label is a feedback row. R49: the read excludes it. Both facts need it in the relation.
    for (const label of org.labels) data.feedback.push({ ...label, org_id: org.org_id });
    for (const trace of org.traces) {
      const { feedback, timings, versions, ...rest } = trace as Record<string, unknown> & {
        feedback: Record<string, unknown>[];
        timings: Record<string, unknown>;
        versions: Record<string, unknown>;
      };
      data.traces.push({ ...rest, ...timings, ...versions, org_id: org.org_id });
      for (const entry of feedback) data.feedback.push({ ...entry, org_id: org.org_id });
    }
    for (const run of org.judge) data.judge.push({ ...run, org_id: org.org_id });
  }
  data.audit = seedAudit(state.orgs);
  return data;
}

/**
 * One harness: a fresh dataset per call, because the conformance suite expects a fresh tenant per
 * factory call, and the identifiers and sessions the suite needs come from the fake that seeded it.
 */
export function makeConsoleHarness(): ConsoleHarness & { data: Dataset } {
  const fake = createFakeConsoleServices();
  const data = seedDataset(fake.unsafeDebugState() as DebugState);
  const port = createMemoryPort(data);
  return {
    services: createConsoleServices({ pg: port, ch: port, cursorSecret: TEST_CURSOR_SECRET }),
    sessions: fake.sessions,
    ids: fake.ids,
    // The dataset is seeded from the fake, so it carries exactly the pre-pilot and nullable
    // history the fixtures do: legacy_usd rows with a charge, rows whose key was deleted, a key
    // with no recorded capture mode, a judge run that reached no provider — plus the orphaned
    // audit entry seeded above. Declaring it makes the gated conformance cases **run** here
    // instead of skipping, which is the point: they are what proves the real projection.
    hasLegacyRows: fake.hasLegacyRows,
    data,
  };
}
