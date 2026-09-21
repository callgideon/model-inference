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
  /** Grouping for `usage_daily`: one day per row, derived from the timestamp. */
  groupBy?: { field: string; column: string; derive: "date"; direction: "asc" | "desc" };
};

const TIME_FILTERS = (column: string): Record<string, FilterSpec> => ({
  from: { column, op: "gte", field: "created_at" },
  to: { column, op: "lte", field: "created_at" },
});

const USAGE_FROM = `public.usage_events e
       join public.api_keys k on k.id = e.api_key_id
       left join public.credit_holds h on h.request_id = e.id and h.state in ('held', 'unknown')`;

const USAGE_FILTERS: Record<string, FilterSpec> = {
  ...TIME_FILTERS("e.created_at"),
  key_id: { column: "e.api_key_id", op: "eq", field: "key_id" },
  model: { column: "e.model_id", op: "eq", field: "model" },
};

const USAGE_COLUMNS = `e.id as request_id, e.created_at, e.model_id as model, e.api_key_id as key_id,
         k.name as key_name, e.execution_mode, e.job_state, e.terminal_cause, e.status as http_status,
         e.prompt_tokens, e.completion_tokens, e.usage_certainty, e.settlement_state,
         e.cost_usd as cost, h.max_amount_usd as max_hold, e.trace_mode`;

const TRACE_COLUMNS = `request_id, created_at, model, key_id, job_state, http_status, trace_mode,
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
    columns: "w.ledger_total, w.reserved_total, (w.ledger_total - w.reserved_total) as available",
    tenantColumn: "w.org_id",
    tenantField: "org_id",
  },

  ledger_page: {
    engine: "pg",
    source: "ledger",
    from: "public.credit_ledger l",
    columns: "l.id, l.created_at, l.delta_usd as delta, l.kind, l.reason, l.ref, l.actor, l.by_operator",
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
    tenantColumn: "e.org_id",
    tenantField: "org_id",
    filters: USAGE_FILTERS,
    sort: {
      at: { field: "created_at", column: "e.created_at" },
      id: { field: "request_id", column: "e.id" },
      direction: "desc",
    },
  },

  usage_summary: {
    engine: "pg",
    source: "usage",
    from: USAGE_FROM,
    tenantColumn: "e.org_id",
    tenantField: "org_id",
    filters: USAGE_FILTERS,
    aggregates: [
      { name: "requests", kind: "count" },
      { name: "failed_requests", kind: "count", filters: [{ field: "http_status", column: "e.status", op: "gte", value: 400 }] },
      { name: "prompt_tokens", kind: "sum", field: "prompt_tokens", column: "e.prompt_tokens" },
      { name: "completion_tokens", kind: "sum", field: "completion_tokens", column: "e.completion_tokens" },
      { name: "cost", kind: "sum", field: "cost", column: "e.cost_usd" },
      {
        name: "pending_reconciliation",
        kind: "sum",
        field: "max_hold",
        column: "h.max_amount_usd",
        filters: [
          { field: "usage_certainty", column: "e.usage_certainty", op: "eq", value: "unknown" },
          { field: "max_hold", column: "h.max_amount_usd", op: "not_null", value: null },
        ],
      },
      {
        name: "platform_absorbed_requests",
        kind: "count",
        filters: [{ field: "settlement_state", column: "e.settlement_state", op: "eq", value: "released_platform_absorbed" }],
      },
    ],
  },

  usage_daily: {
    engine: "pg",
    source: "usage",
    from: USAGE_FROM,
    tenantColumn: "e.org_id",
    tenantField: "org_id",
    filters: USAGE_FILTERS,
    groupBy: { field: "day", column: "(e.created_at at time zone 'utc')::date", derive: "date", direction: "desc" },
    aggregates: [
      { name: "requests", kind: "count" },
      { name: "prompt_tokens", kind: "sum", field: "prompt_tokens", column: "e.prompt_tokens" },
      { name: "completion_tokens", kind: "sum", field: "completion_tokens", column: "e.completion_tokens" },
      { name: "cost", kind: "sum", field: "cost", column: "e.cost_usd" },
    ],
  },

  keys_list: {
    engine: "pg",
    source: "keys",
    from: "public.api_keys k",
    columns: "k.id, k.name, k.prefix, k.created_at, k.last_used_at, k.revoked_at, k.trace_mode",
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
    columns: "k.id, k.name, k.prefix, k.created_at, k.last_used_at, k.revoked_at, k.trace_mode",
    tenantColumn: "k.org_id",
    tenantField: "org_id",
    filters: { key_id: { column: "k.id", op: "eq", field: "id" } },
  },

  settings_get: {
    engine: "pg",
    source: "settings",
    from: "public.org_settings s",
    columns: "s.trace_mode, s.content_retention_days, s.evaluation_consent",
    tenantColumn: "s.org_id",
    tenantField: "org_id",
  },

  consent_history: {
    engine: "pg",
    source: "consent",
    from: "public.consent_history c",
    columns: "c.changed_at, c.evaluation_consent, c.changed_by, c.by_operator",
    tenantColumn: "c.org_id",
    tenantField: "org_id",
    sort: {
      at: { field: "changed_at", column: "c.changed_at" },
      id: { field: "changed_at", column: "c.changed_at" },
      direction: "asc",
    },
  },

  feedback_by_request: {
    engine: "pg",
    source: "feedback",
    from: "public.feedback f",
    columns: `f.id, f.request_id, f.created_at, f.channel, f.author_role, f.author_principal,
         f.name, f.value, f.comment, f.calibration_set, f.rubric_version, f.by_operator`,
    tenantColumn: "f.org_id",
    tenantField: "org_id",
    filters: { request_id: { column: "f.request_id", op: "eq", field: "request_id" } },
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
    columns: `r.id, r.created_at, r.state, r.mode, r.rubric_version, r.judge_model,
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
  for (const [filterName, value] of Object.entries(options.filters ?? {})) {
    if (value === undefined) continue;
    const filter = spec.filters?.[filterName];
    if (filter === undefined) {
      throw new QueryPlanError(`${filterName} is not a filter of ${name}`);
    }
    if ((RESERVED_PARAM_NAMES as readonly string[]).includes(filterName)) {
      throw new QueryPlanError(`${filterName} is a reserved parameter name`);
    }
    predicates.push({ field: filter.field, column: filter.column, op: filter.op, value });
  }
  const orgId = options.orgId ?? null;
  if (spec.tenantColumn !== null && (orgId === null || orgId === "")) {
    throw new QueryPlanError(`${name} is tenant-scoped and needs the session organization`);
  }
  return {
    name,
    predicates,
    keyset: options.keyset ?? null,
    limit: options.limit ?? null,
    // Last, always: a plan with the tenant anywhere else is not constructible from here.
    tenant: spec.tenantColumn === null || orgId === null ? null : { column: spec.tenantColumn, value: orgId },
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
  if (spec.aggregates !== undefined) {
    if (spec.groupBy !== undefined) selected.push(`${spec.groupBy.column} as ${spec.groupBy.field}`);
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
  if (spec.groupBy !== undefined) {
    text += `\n group by 1\n order by 1 ${spec.groupBy.direction}`;
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
    if (name !== TENANT_PARAM && (RESERVED_PARAM_NAMES as readonly string[]).includes(name)) {
      throw new QueryPlanError(`${name} is a reserved parameter name`);
    }
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
