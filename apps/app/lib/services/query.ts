/**
 * Named queries and the query port (C1).
 *
 * Every read the console performs is one of the named queries in `NAMED_QUERIES`. There is no
 * caller-supplied SQL, no caller-supplied column and no caller-supplied relation: a filter is
 * accepted only when its name is in that query's `filters` allowlist, and it is then bound to the
 * column *the registry* names.
 *
 * The tenant is never a filter. It is appended after every caller predicate and its parameter is
 * bound **last**, under a reserved name no filter may use (`RESERVED_PARAM_NAMES`). That is the
 * whole point of the two rules together: a caller cannot name the tenant parameter (allowlist), and
 * even a parameter map merged the wrong way round cannot overwrite it (bound last).
 *
 * The same `QueryPlan` renders to PostgreSQL (positional `$n`) and to ClickHouse (named
 * `{name:Type}`), so both engines get the identical binding discipline. The SQL text here is
 * written against the relations of research/plan/06-database-map.md; D1 owns the migrations, so
 * until D1 lands every rendered statement is **integration-pending** and the only executor in this
 * repository is the in-memory port the track tests inject.
 */

/**
 * Server only. The console's dependency set is frozen, so there is no `server-only` package to
 * import (08 §6); this is the guard `lib/supabase/admin.ts` already uses, at module scope, so a
 * client component that imports this file fails loudly instead of shipping the statements in a
 * browser chunk. `tests/c/client-boundary.test.ts` is the static half of the same rule.
 */
if (typeof window !== "undefined") throw new Error("lib/services/query.ts is server-only");

// ---------------------------------------------------------------------------
// Plans
// ---------------------------------------------------------------------------

export type SqlValue = string | number | boolean | null;

/** The comparisons a named query may use. Nothing here is composed from caller input. */
export type FilterOp = "eq" | "ne" | "gte" | "lte" | "gt" | "not_null";

export type Predicate = {
  /** Logical field name, for the in-memory port. */
  field: string;
  /** SQL column, from the registry — never from a caller. */
  column: string;
  op: FilterOp;
  value: SqlValue;
};

export type Keyset = { at: string; id: string };

export type QueryPlan = {
  name: NamedQueryName;
  /** Caller filters, already validated against the query's allowlist. */
  predicates: Predicate[];
  /** Keyset bound from an authenticated cursor; `null` on the first page. */
  keyset: Keyset | null;
  /** Row cap. Always `≤ MAX_PAGE_LIMIT + 1` for a page (the extra row answers "is there more"). */
  limit: number | null;
  /** The trusted tenant. Bound last, under a reserved name. */
  tenant: { column: string; value: string } | null;
};

export type Row = Record<string, unknown>;

/** The injected executor. One per engine; both receive plans, never SQL from a caller. */
export type QueryPort = {
  run(plan: QueryPlan): Promise<Row[]>;
};

/**
 * The tenant check every port implementation passes through.
 *
 * It is here, wrapping the port, rather than inside any one implementation, because D1's views return
 * **every** organization's rows to an operator or service-role session: for those sessions this
 * predicate is the only thing scoping a tenant's own reads, so an implementation that forgot to apply
 * `plan.tenant` would answer a customer's page with the whole platform's rows and no test of the
 * implementation alone would say so. Two rules, both cheap:
 *
 * 1. a tenant-scoped named query may not run without a tenant (refused before the executor is called);
 * 2. every row that comes back carries that tenant, or the read fails.
 *
 * The second is a post-check on data the port has already fetched, so it costs one comparison per row
 * and turns "the port forgot the predicate" into a loud failure instead of a cross-tenant page.
 */
export function scopedPort(port: QueryPort): QueryPort {
  return {
    async run(plan: QueryPlan): Promise<Row[]> {
      const spec = namedQuery(plan.name);
      if (spec.tenantColumn === null) return port.run(plan);
      if (plan.tenant === null) {
        throw new QueryPlanError(`${plan.name} is tenant-scoped and must not run without a tenant`);
      }
      const field = spec.tenantField;
      // A tenant-scoped query with no declared field would have disabled the check silently, so the
      // registry invariant is enforced here as well as in a test.
      if (field === undefined) {
        throw new QueryPlanError(`${plan.name} is tenant-scoped but declares no tenantField`);
      }
      const rows = await port.run(plan);
      const scoped: Row[] = [];
      for (const row of rows) {
        // `hasOwn`, so a tenant carried on a prototype is not a tenant; and a row that simply does not
        // say whose it is cannot be accepted — that was the hole: eleven of twelve row queries did not
        // select the column, so every row passed a check that only fired when the field was present.
        if (!Object.hasOwn(row, field)) {
          throw new QueryPlanError(`${plan.name} returned a row with no ${field}`);
        }
        if (row[field] !== plan.tenant.value) {
          throw new QueryPlanError(`${plan.name} returned a row belonging to another organization`);
        }
        // Stripped here, so the tenant column exists for the check and never reaches a DTO.
        const { [field]: _tenant, ...rest } = row;
        scoped.push(rest);
      }
      return scoped;
    },
  };
}

/** Parameter names a caller filter may never use, because the tenant binding owns them. */
export const RESERVED_PARAM_NAMES = ["__org_id"] as const;

const TENANT_PARAM = RESERVED_PARAM_NAMES[0];

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

type FilterSpec = { column: string; op: FilterOp; field: string };

type SortSpec = {
  at: { field: string; column: string };
  id: { field: string; column: string };
  direction: "asc" | "desc";
};

/**
 * An aggregate, stated declaratively so the SQL renderer and the in-memory port describe the same
 * computation instead of two hand-written ones. `filters` are the `filter (where …)` conditions.
 *
 * A tenant-scoped aggregate groups by its tenant column as well, so the row it returns carries the
 * organization it was computed for and `scopedPort` can check it. Without that, a totals query was
 * exempt from the tenant check by construction: an executor could return another organization's sums
 * and nothing in the response said whose they were.
 */
type AggregateSpec = {
  name: string;
  kind: "count" | "sum";
  field?: string;
  column?: string;
  filters?: Predicate[];
};

type NamedQuerySpec = {
  engine: "pg" | "clickhouse";
  /** Logical row set the in-memory port serves. */
  source: string;
  /** SQL `FROM` clause, including joins. Registry-owned text. */
  from: string;
  /** SQL select list for a row query. */
  columns?: string;
  tenantColumn: string | null;
  /** The tenant column's logical field name, for the in-memory port. Required with `tenantColumn`. */
  tenantField?: string;
  sort?: SortSpec;
  filters?: Record<string, FilterSpec>;
  /** Predicates the query always applies, whatever the caller asked for. */
  constants?: Predicate[];
  aggregates?: AggregateSpec[];
  /**
   * The bound applied when the caller asks for no limit. The contract gives some reads no cursor at
   * all (`keys.list`, `feedback.list`, `settings.get`'s consent history, `usageDaily`), and an
   * unbounded read is exactly the defect this task replaced in `getCredits`. Documented caps:
   * 100 keys, 100 feedback entries per request, 100 consent versions, 400 daily rows (13 calendar
   * months of metadata retention). A tenant past a cap is a contract revision, not a silent scan.
   */
  hardLimit?: number;
  /** Grouping for `usage_daily`: one day per row, derived from the timestamp. */
  groupBy?: { field: string; column: string; derive: "date"; direction: "asc" | "desc" };
};

const TIME_FILTERS = (column: string): Record<string, FilterSpec> => ({
  from: { column, op: "gte", field: "created_at" },
  to: { column, op: "lte", field: "created_at" },
});

/**
 * D1 ships the usage read as one view (`0005_console_read_surface.sql`), because the three-way join
 * this registry used to render reached `infrx.credit_holds`, which no browser role may see. The view
 * LEFT JOINs `api_keys` — `usage_events.api_key_id` is nullable (`on delete set null`), and an inner
 * join would silently drop every row whose key was deleted and understate a cost total.
 */
const USAGE_FROM = "public.console_usage u";

const USAGE_FILTERS: Record<string, FilterSpec> = {
  ...TIME_FILTERS("u.created_at"),
  key_id: { column: "u.key_id", op: "eq", field: "key_id" },
  model: { column: "u.model", op: "eq", field: "model" },
};

const USAGE_COLUMNS = `u.org_id, u.request_id, u.created_at, u.model, u.key_id, u.key_name, u.execution_mode,
         u.job_state, u.terminal_cause, u.http_status, u.prompt_tokens, u.completion_tokens,
         u.usage_certainty, u.settlement_state, u.cost, u.max_hold, u.trace_mode`;

const TRACE_COLUMNS = `org_id, request_id, created_at, model, key_id, job_state, http_status, trace_mode,
         content, loss_reason, prompt_tokens, completion_tokens, ttft_ms, wall_ms, cost,
         feedback_count, score_count, execution_mode, terminal_cause, error_code, usage_certainty,
         settlement_state, auth_ms, media_ms, admit_ms, queue_ms, gateway_version, model_revision,
         price_snapshot_version, trace_schema_version, content_expires_at, metadata_expires_at`;

const TRACE_FILTERS: Record<string, FilterSpec> = {
  ...TIME_FILTERS("created_at"),
  key_id: { column: "key_id", op: "eq", field: "key_id" },
  model: { column: "model", op: "eq", field: "model" },
  job_state: { column: "job_state", op: "eq", field: "job_state" },
  trace_mode: { column: "trace_mode", op: "eq", field: "trace_mode" },
  content: { column: "content", op: "eq", field: "content" },
};

const NAMED_QUERIES = {
  /** Suspension and existence, read before anything else a tenant asks for (R33). */
  org_status: {
    engine: "pg",
    source: "orgs",
    from: "public.organizations o",
    columns: "o.id as org_id, o.name, o.suspended, o.suspension_reason",
    tenantColumn: "o.id",
    tenantField: "org_id",
  },

  /**
   * The wallet summary columns (06 `wallets`). `available` is a derived column, never a sum over
   * the ledger: the reconciled summary is what serialises against grants and settlements.
   */
  wallet_summary: {
    engine: "pg",
    source: "wallets",
    from: "public.wallets w",
    // D1's view also exposes a stored `available`; this service derives it from the two totals in
    // one place instead, so the identity the conformance suite asserts has a single home.
    columns: "w.org_id, w.ledger_total, w.reserved_total",
    tenantColumn: "w.org_id",
    tenantField: "org_id",
  },

  ledger_page: {
    engine: "pg",
    source: "ledger",
    from: "public.console_ledger l",
    columns: "l.org_id, l.id, l.created_at, l.delta, l.kind, l.reason, l.ref, l.actor, l.by_operator",
    tenantColumn: "l.org_id",
    tenantField: "org_id",
    sort: {
      at: { field: "created_at", column: "l.created_at" },
      id: { field: "id", column: "l.id" },
      direction: "desc",
    },
  },

  usage_page: {
    engine: "pg",
    source: "usage",
    from: USAGE_FROM,
    columns: USAGE_COLUMNS,
    tenantColumn: "u.org_id",
    tenantField: "org_id",
    filters: USAGE_FILTERS,
    sort: {
      at: { field: "created_at", column: "u.created_at" },
      id: { field: "request_id", column: "u.request_id" },
      direction: "desc",
    },
  },

  usage_summary: {
    engine: "pg",
    source: "usage",
    from: USAGE_FROM,
    tenantColumn: "u.org_id",
    tenantField: "org_id",
    filters: USAGE_FILTERS,
    aggregates: [
      { name: "requests", kind: "count" },
      { name: "failed_requests", kind: "count", filters: [{ field: "http_status", column: "u.http_status", op: "gte", value: 400 }] },
      { name: "prompt_tokens", kind: "sum", field: "prompt_tokens", column: "u.prompt_tokens" },
      { name: "completion_tokens", kind: "sum", field: "completion_tokens", column: "u.completion_tokens" },
      { name: "cost", kind: "sum", field: "cost", column: "u.cost" },
      {
        name: "pending_reconciliation",
        kind: "sum",
        field: "max_hold",
        column: "u.max_hold",
        filters: [
          { field: "usage_certainty", column: "u.usage_certainty", op: "eq", value: "unknown" },
          { field: "max_hold", column: "u.max_hold", op: "not_null", value: null },
        ],
      },
      {
        name: "platform_absorbed_requests",
        kind: "count",
        filters: [{ field: "settlement_state", column: "u.settlement_state", op: "eq", value: "released_platform_absorbed" }],
      },
    ],
  },

  usage_daily: {
    engine: "pg",
    hardLimit: 400,
    source: "usage",
    from: USAGE_FROM,
    tenantColumn: "u.org_id",
    tenantField: "org_id",
    filters: USAGE_FILTERS,
    groupBy: { field: "day", column: "(u.created_at at time zone 'utc')::date", derive: "date", direction: "desc" },
    aggregates: [
      { name: "requests", kind: "count" },
      { name: "prompt_tokens", kind: "sum", field: "prompt_tokens", column: "u.prompt_tokens" },
      { name: "completion_tokens", kind: "sum", field: "completion_tokens", column: "u.completion_tokens" },
      { name: "cost", kind: "sum", field: "cost", column: "u.cost" },
    ],
  },

  keys_list: {
    engine: "pg",
    hardLimit: 100,
    source: "keys",
    from: "public.api_keys k",
    columns: "k.org_id, k.id, k.name, k.prefix, k.created_at, k.last_used_at, k.revoked_at, k.trace_mode",
    tenantColumn: "k.org_id",
    tenantField: "org_id",
    sort: {
      at: { field: "created_at", column: "k.created_at" },
      id: { field: "id", column: "k.id" },
      direction: "desc",
    },
  },

  key_by_id: {
    engine: "pg",
    source: "keys",
    from: "public.api_keys k",
    columns: "k.org_id, k.id, k.name, k.prefix, k.created_at, k.last_used_at, k.revoked_at, k.trace_mode",
    tenantColumn: "k.org_id",
    tenantField: "org_id",
    filters: { key_id: { column: "k.id", op: "eq", field: "id" } },
  },

  settings_get: {
    engine: "pg",
    source: "settings",
    from: "public.org_settings s",
    columns: "s.org_id, s.trace_mode, s.content_retention_days, s.evaluation_consent",
    tenantColumn: "s.org_id",
    tenantField: "org_id",
  },

  /** Oldest first: consent history is an audit trail, and `version` is 06's key within an org. */
  consent_history: {
    engine: "pg",
    hardLimit: 100,
    source: "consent",
    from: "public.consent_history c",
    columns: "c.org_id, c.version, c.changed_at, c.evaluation_consent, c.changed_by, c.by_operator",
    tenantColumn: "c.org_id",
    tenantField: "org_id",
    sort: {
      at: { field: "changed_at", column: "c.changed_at" },
      id: { field: "version", column: "c.version" },
      direction: "asc",
    },
  },

  feedback_by_request: {
    engine: "pg",
    hardLimit: 100,
    source: "feedback",
    from: "public.feedback f",
    columns: `f.org_id, f.id, f.request_id, f.created_at, f.channel, f.author_role, f.author_principal,
         f.name, f.value, f.comment, f.calibration_set, f.rubric_version, f.by_operator`,
    tenantColumn: "f.org_id",
    tenantField: "org_id",
    filters: { request_id: { column: "f.request_id", op: "eq", field: "request_id" } },
    /**
     * R49: a calibration label is operator data and leaves this list for *everyone*, operators
     * included — it is read only through the calibration listing. The exclusion is a constant of the
     * query, like the trace list's off-mode predicate, so no caller and no forgotten filter can drop
     * it, and the customer's `feedback_count` never counts one (R35).
     */
    constants: [{ field: "calibration_set", column: "f.calibration_set", op: "eq", value: false }],
    sort: {
      at: { field: "created_at", column: "f.created_at" },
      id: { field: "id", column: "f.id" },
      direction: "asc",
    },
  },

  judge_runs_page: {
    engine: "pg",
    source: "judge",
    from: "public.console_judge_runs r",
    columns: `r.org_id, r.id, r.created_at, r.state, r.mode, r.rubric_version, r.judge_model,
         r.judge_model_version, r.sample_count, r.limited_evaluation_count, r.budget_reserved,
         r.budget_settled, r.consent_snapshot_at, r.external_batch_id, r.quarantine_reason, r.samples`,
    tenantColumn: "r.org_id",
    tenantField: "org_id",
    sort: {
      at: { field: "created_at", column: "r.created_at" },
      id: { field: "id", column: "r.id" },
      direction: "desc",
    },
  },

  /**
   * Operator-only and deliberately untenanted: platform operators are platform-wide in the pilot
   * (R26). Authority is checked before the plan is built, never inside it.
   */
  admin_orgs_page: {
    engine: "pg",
    source: "orgs",
    from: "public.console_admin_orgs o",
    columns: `o.org_id, o.name, o.owner_email, o.created_at, o.suspended, o.suspension_reason,
         o.ledger_total, o.reserved_total, o.requests_30d, o.model_ids, o.limits,
         o.entitlements_updated_at, o.entitlements_updated_by`,
    tenantColumn: null,
    sort: {
      at: { field: "name", column: "o.name" },
      id: { field: "org_id", column: "o.org_id" },
      direction: "asc",
    },
  },

  admin_audit_page: {
    engine: "pg",
    source: "audit",
    from: "public.operator_audit a",
    columns: `a.id, a.at, a.actor_principal, a.action, a.target_org_id, a.reason, a.before,
         a.after, a.idempotency_key`,
    tenantColumn: null,
    filters: { target_org_id: { column: "a.target_org_id", op: "eq", field: "target_org_id" } },
    sort: {
      at: { field: "at", column: "a.at" },
      id: { field: "id", column: "a.id" },
      direction: "desc",
    },
  },

  /**
   * ClickHouse projection (T3). Off-mode requests are not trace rows for the list (R13); the
   * constant predicate is part of the query, not a caller filter, so no caller can remove it.
   */
  traces_page: {
    engine: "clickhouse",
    source: "traces",
    from: "infrx.trace_metadata",
    columns: TRACE_COLUMNS,
    tenantColumn: "org_id",
    tenantField: "org_id",
    filters: TRACE_FILTERS,
    constants: [{ field: "trace_mode", column: "trace_mode", op: "ne", value: "off" }],
    sort: {
      at: { field: "created_at", column: "created_at" },
      id: { field: "request_id", column: "request_id" },
      direction: "desc",
    },
  },

  trace_by_request: {
    engine: "clickhouse",
    source: "traces",
    from: "infrx.trace_metadata",
    columns: TRACE_COLUMNS,
    tenantColumn: "org_id",
    tenantField: "org_id",
    filters: { request_id: { column: "request_id", op: "eq", field: "request_id" } },
  },
} as const satisfies Record<string, NamedQuerySpec>;

export type NamedQueryName = keyof typeof NAMED_QUERIES;

export function namedQuery(name: NamedQueryName): NamedQuerySpec {
  return NAMED_QUERIES[name] as NamedQuerySpec;
}

export const NAMED_QUERY_NAMES = Object.keys(NAMED_QUERIES) as NamedQueryName[];

/** The filter names a named query accepts. Anything else is `invalid_request`, never ignored. */
export function allowedFilters(name: NamedQueryName): string[] {
  return Object.keys(namedQuery(name).filters ?? {});
}

// ---------------------------------------------------------------------------
// Plan construction
// ---------------------------------------------------------------------------

export class QueryPlanError extends Error {}

/**
 * Build a plan. `filters` values are already validated by the caller's vocabulary check; this
 * function is what guarantees they can only reach the columns the registry names, and that the
 * tenant predicate goes on last.
 */
/** The largest row count any named query may be asked for: a page plus its look-ahead row. */
export const MAX_PLAN_ROWS = 1000;

/**
 * `buildPlan` is an exported API, so it validates its own arguments rather than trusting a caller to
 * have done it. Three details matter more than they look:
 *
 * - **`Object.hasOwn`, not a bracket lookup.** `spec.filters?.["constructor"]` on an object literal
 *   returns the inherited `Object.prototype` member, which is not `undefined`, so a filter named
 *   `constructor`, `toString`, `valueOf`, `hasOwnProperty`, `isPrototypeOf` or `__proto__` used to
 *   pass the allowlist and render `where undefined undefined $1`. Every place an untrusted string
 *   indexes an object goes through `hasOwn`.
 * - **`limit` is interpolated into the statement text**, so it is checked as an integer in range
 *   rather than bound as a parameter (PostgreSQL will not take a placeholder there in every driver).
 * - **the tenant is a non-empty string**, because `orgId` reaches the statement as the one value that
 *   decides which organization's rows come back.
 */
export function buildPlan(
  name: NamedQueryName,
  options: {
    filters?: Record<string, SqlValue>;
    keyset?: Keyset | null;
    limit?: number | null;
    orgId?: string | null;
  } = {},
): QueryPlan {
  const spec = namedQuery(name);
  const predicates: Predicate[] = [...(spec.constants ?? [])];
  const filters = options.filters ?? {};
  for (const filterName of Object.keys(filters)) {
    const value = filters[filterName];
    if (value === undefined) continue;
    if (spec.filters === undefined || !Object.hasOwn(spec.filters, filterName)) {
      throw new QueryPlanError(`${filterName} is not a filter of ${name}`);
    }
    const filter = spec.filters[filterName];
    if (typeof value !== "string" && typeof value !== "number" && typeof value !== "boolean") {
      throw new QueryPlanError(`${filterName} must be a string, a number or a boolean`);
    }
    predicates.push({ field: filter.field, column: filter.column, op: filter.op, value });
  }

  const orgId = options.orgId ?? null;
  if (spec.tenantColumn !== null && (typeof orgId !== "string" || orgId === "")) {
    throw new QueryPlanError(`${name} is tenant-scoped and needs the session organization`);
  }
  if (spec.tenantColumn === null && orgId !== null) {
    throw new QueryPlanError(`${name} is not tenant-scoped, so it must not be given an organization`);
  }

  const limit = options.limit ?? spec.hardLimit ?? null;
  if (limit !== null && (!Number.isInteger(limit) || limit < 1 || limit > MAX_PLAN_ROWS)) {
    throw new QueryPlanError(`${name}: a row limit must be an integer between 1 and ${MAX_PLAN_ROWS}`);
  }

  const keyset = options.keyset ?? null;
  if (keyset !== null && (typeof keyset.at !== "string" || typeof keyset.id !== "string")) {
    throw new QueryPlanError(`${name}: a keyset bound is two strings`);
  }
  if (keyset !== null && spec.sort === undefined) {
    throw new QueryPlanError(`${name} has no sort key, so it cannot resume from a cursor`);
  }

  return {
    name,
    predicates,
    keyset,
    limit,
    // Last, always: a plan with the tenant anywhere else is not constructible from here.
    tenant: spec.tenantColumn === null ? null : { column: spec.tenantColumn, value: orgId as string },
  };
}

/** `has_feedback` is a boolean over a counter, so it becomes a comparison rather than a column. */
export function feedbackCountPredicate(hasFeedback: boolean): Predicate {
  return hasFeedback
    ? { field: "feedback_count", column: "feedback_count", op: "gt", value: 0 }
    : { field: "feedback_count", column: "feedback_count", op: "eq", value: 0 };
}

// ---------------------------------------------------------------------------
// SQL rendering — shipped for D1/T3, integration-pending until they land
// ---------------------------------------------------------------------------

const SQL_OPERATOR: Record<Exclude<FilterOp, "not_null">, string> = {
  eq: "=",
  ne: "<>",
  gte: ">=",
  lte: "<=",
  gt: ">",
};

function aggregateSql(aggregate: AggregateSpec, placeholder: (value: SqlValue) => string): string {
  const inner = aggregate.kind === "count" ? "count(*)" : `coalesce(sum(${aggregate.column}), 0)`;
  if (aggregate.filters === undefined || aggregate.filters.length === 0) {
    return `${inner} as ${aggregate.name}`;
  }
  const conditions = aggregate.filters
    .map((predicate) => conditionSql(predicate, placeholder))
    .join(" and ");
  const filtered = aggregate.kind === "count" ? `count(*) filter (where ${conditions})` : `coalesce(sum(${aggregate.column}) filter (where ${conditions}), 0)`;
  return `${filtered} as ${aggregate.name}`;
}

function conditionSql(predicate: Predicate, placeholder: (value: SqlValue) => string): string {
  if (predicate.op === "not_null") return `${predicate.column} is not null`;
  return `${predicate.column} ${SQL_OPERATOR[predicate.op]} ${placeholder(predicate.value)}`;
}

export type RenderedSql = {
  text: string;
  /** Parameter names in binding order. The reserved tenant name is always last. */
  paramNames: string[];
  params: SqlValue[];
};

/**
 * PostgreSQL rendering: positional parameters, the tenant bound last under its reserved name.
 * Shipped against 06's relations; no executor in this repository runs it yet (integration-pending
 * on D1).
 */
export function renderSql(plan: QueryPlan): RenderedSql {
  const spec = namedQuery(plan.name);
  const paramNames: string[] = [];
  const params: SqlValue[] = [];
  const bind = (name: string, value: SqlValue): string => {
    paramNames.push(name);
    params.push(value);
    return `$${params.length}`;
  };
  const conditions: string[] = [];
  let index = 0;
  const next = (value: SqlValue): string => bind(`f${index++}`, value);

  const selected: string[] = [];
  // An aggregate carries its tenant as a grouping column, so the row it returns says whose totals
  // these are and `scopedPort` can check it like any other row.
  const grouped: string[] = [];
  if (spec.aggregates !== undefined) {
    if (spec.tenantColumn !== null) {
      selected.push(`${spec.tenantColumn} as ${spec.tenantField}`);
      grouped.push(String(selected.length));
    }
    if (spec.groupBy !== undefined) {
      selected.push(`${spec.groupBy.column} as ${spec.groupBy.field}`);
      grouped.push(String(selected.length));
    }
    for (const aggregate of spec.aggregates) selected.push(aggregateSql(aggregate, next));
  } else {
    selected.push(spec.columns ?? "*");
  }

  for (const predicate of plan.predicates) {
    conditions.push(conditionSql(predicate, next));
  }
  if (plan.keyset !== null && spec.sort !== undefined) {
    const comparison = spec.sort.direction === "desc" ? "<" : ">";
    conditions.push(
      `(${spec.sort.at.column}, ${spec.sort.id.column}) ${comparison} (${bind("k_at", plan.keyset.at)}, ${bind("k_id", plan.keyset.id)})`,
    );
  }
  // The tenant goes on after every caller predicate and binds last: a filter can narrow this
  // statement, never widen it, and no parameter map merge order can displace it.
  if (plan.tenant !== null) conditions.push(`${plan.tenant.column} = ${bind(TENANT_PARAM, plan.tenant.value)}`);

  let text = `select ${selected.join(", ")}\n  from ${spec.from}`;
  if (conditions.length > 0) text += `\n where ${conditions.join("\n   and ")}`;
  if (grouped.length > 0) {
    text += `\n group by ${grouped.join(", ")}`;
    if (spec.groupBy !== undefined) {
      text += `\n order by ${grouped[grouped.length - 1]} ${spec.groupBy.direction}`;
    }
  } else if (spec.aggregates === undefined && spec.sort !== undefined) {
    text += `\n order by ${spec.sort.at.column} ${spec.sort.direction}, ${spec.sort.id.column} ${spec.sort.direction}`;
  }
  if (plan.limit !== null) text += `\n limit ${plan.limit}`;
  return { text, paramNames, params };
}

const CLICKHOUSE_TYPE = (value: SqlValue): string => {
  if (typeof value === "number") return Number.isInteger(value) ? "Int64" : "Float64";
  if (typeof value === "boolean") return "UInt8";
  return "String";
};

export type RenderedClickHouseSql = {
  text: string;
  paramNames: string[];
  params: Record<string, SqlValue>;
};

/**
 * ClickHouse rendering: named parameters. The tenant is assigned into the parameter record **after**
 * every caller parameter, so the merge order that lost tenancy in the original console (`chQuery`
 * merging caller parameters over the bound organization) cannot happen here — and a reserved name
 * is unreachable from a filter anyway.
 */
export function renderClickHouseSql(plan: QueryPlan): RenderedClickHouseSql {
  const spec = namedQuery(plan.name);
  const paramNames: string[] = [];
  const params: Record<string, SqlValue> = {};
  let index = 0;
  const bind = (name: string, value: SqlValue): string => {
    paramNames.push(name);
    params[name] = value;
    return `{${name}:${CLICKHOUSE_TYPE(value)}}`;
  };
  const conditions: string[] = [];
  const next = (value: SqlValue): string => bind(`f${index++}`, value);
  for (const predicate of plan.predicates) {
    conditions.push(conditionSql(predicate, next));
  }
  if (plan.keyset !== null && spec.sort !== undefined) {
    const comparison = spec.sort.direction === "desc" ? "<" : ">";
    conditions.push(
      `(${spec.sort.at.column}, ${spec.sort.id.column}) ${comparison} (${bind("k_at", plan.keyset.at)}, ${bind("k_id", plan.keyset.id)})`,
    );
  }
  if (plan.tenant !== null) conditions.push(`${plan.tenant.column} = ${bind(TENANT_PARAM, plan.tenant.value)}`);

  let text = `select ${spec.columns ?? "*"}\n  from ${spec.from}`;
  if (conditions.length > 0) text += `\n where ${conditions.join("\n   and ")}`;
  if (spec.sort !== undefined) {
    text += `\n order by ${spec.sort.at.column} ${spec.sort.direction}, ${spec.sort.id.column} ${spec.sort.direction}`;
  }
  if (plan.limit !== null) text += `\n limit ${plan.limit}`;
  return { text, paramNames, params };
}

/** The reserved tenant parameter must be the last binding of every tenant-scoped statement. */
export function tenantParamIsLast(rendered: { paramNames: string[] }, tenanted: boolean): boolean {
  const last = rendered.paramNames[rendered.paramNames.length - 1];
  const occurrences = rendered.paramNames.filter((name) => name === TENANT_PARAM).length;
  return tenanted ? last === TENANT_PARAM && occurrences === 1 : occurrences === 0;
}
