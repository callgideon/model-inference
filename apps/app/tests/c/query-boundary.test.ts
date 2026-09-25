// node --test "tests/**/*.test.ts"
//
// The tenant query boundary (TRACE-TENANT, DUR-RLS): what a caller can and cannot reach through a
// named query. These are the assertions the conformance suite cannot make, because it only ever sees
// the service's answer — a plan whose tenant predicate was merged away can still answer correctly on
// a single-tenant fixture, so the binding itself is checked here.

import assert from "node:assert/strict";
import test from "node:test";
import {
  allowedFilters,
  buildPlan,
  MAX_PLAN_ROWS,
  NAMED_QUERY_NAMES,
  namedQuery,
  QueryPlanError,
  renderClickHouseSql,
  renderSql,
  RESERVED_PARAM_NAMES,
  tenantParamIsLast,
} from "../../lib/services/query.ts";

const ORG = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";

/**
 * The tenanted set, pinned as a literal.
 *
 * Deriving it from the spec under test is what made the old proof vacuous: setting
 * `tenantColumn: null` on `settings_get` left every case green, because the case then simply agreed
 * that an untenanted query binds no tenant. These two lists are the claim.
 */
const TENANT_SCOPED = [
  "consent_history",
  // C0: the owner is the tenant of the wallet lookup, the wallet the tenant of its CREDIT ledger.
  "consumer_wallet",
  "credit_ledger_page",
  "feedback_by_request",
  "judge_runs_page",
  "key_by_id",
  "keys_list",
  "ledger_page",
  "org_status",
  "settings_get",
  "trace_by_request",
  "traces_page",
  "usage_daily",
  "usage_page",
  "usage_summary",
  "wallet_summary",
];

/** Operator-only reads, platform-wide by R26 — the only queries that legitimately bind no tenant. */
const OPERATOR_WIDE = ["admin_audit_page", "admin_orgs_page"];

/** Every key an object carries without owning it: the allowlist bypass a bracket lookup opens. */
const PROTOTYPE_KEYS = [
  "constructor",
  "toString",
  "valueOf",
  "hasOwnProperty",
  "isPrototypeOf",
  "propertyIsEnumerable",
  "toLocaleString",
  "__proto__",
  "__defineGetter__",
];

/** A page query is one with a sort key: the ones a cursor and a limit apply to. */
const pageQueries = NAMED_QUERY_NAMES.filter((name) => namedQuery(name).sort !== undefined);

test("the tenanted set is exactly the pinned one, and nothing else is untenanted", () => {
  const tenanted = NAMED_QUERY_NAMES.filter((name) => namedQuery(name).tenantColumn !== null).sort();
  const untenanted = NAMED_QUERY_NAMES.filter((name) => namedQuery(name).tenantColumn === null).sort();
  assert.deepEqual(tenanted, TENANT_SCOPED, "a query that stopped binding its tenant would show up here");
  assert.deepEqual(untenanted, OPERATOR_WIDE, "only the operator-wide reads bind no tenant (R26)");
});

test("no named query declares a filter under a reserved parameter name", () => {
  for (const name of NAMED_QUERY_NAMES) {
    for (const filterName of allowedFilters(name)) {
      assert.ok(
        !(RESERVED_PARAM_NAMES as readonly string[]).includes(filterName),
        `${name} declares ${filterName}, which the tenant binding owns`,
      );
    }
  }
});

test("a prototype key is not a filter, on any query that has filters", () => {
  const filtered = NAMED_QUERY_NAMES.filter((name) => allowedFilters(name).length > 0);
  assert.ok(filtered.length >= 8, "the filtered queries must all be covered");
  for (const name of filtered) {
    const tenanted = namedQuery(name).tenantColumn !== null;
    for (const key of PROTOTYPE_KEYS) {
      // Built the way a request body arrives, so `__proto__` is a real own key rather than a setter.
      const filters = JSON.parse(`{"${key}": "x"}`) as Record<string, string>;
      assert.throws(
        () => buildPlan(name, { orgId: tenanted ? ORG : null, filters }),
        QueryPlanError,
        `${name} accepted the inherited key ${key}`,
      );
    }
  }
});

test("buildPlan validates its own arguments, because it is exported", () => {
  for (const limit of [0, -1, 2.5, Number.NaN, Number.POSITIVE_INFINITY, MAX_PLAN_ROWS + 1, "10"]) {
    assert.throws(
      () => buildPlan("usage_page", { orgId: ORG, limit: limit as number }),
      QueryPlanError,
      `a limit of ${String(limit)} is interpolated into the statement, so it must be refused`,
    );
  }
  for (const orgId of [42, {}, [], true]) {
    assert.throws(
      () => buildPlan("usage_page", { orgId: orgId as unknown as string }),
      QueryPlanError,
      `an organization of ${typeof orgId} must be refused`,
    );
  }
  assert.throws(
    () => buildPlan("admin_orgs_page", { orgId: ORG }),
    QueryPlanError,
    "an operator-wide query must not be given an organization",
  );
  assert.throws(
    () => buildPlan("usage_page", { orgId: ORG, keyset: { at: 1 as unknown as string, id: "x" } }),
    QueryPlanError,
    "a keyset bound is two strings",
  );
  assert.throws(
    () => buildPlan("wallet_summary", { orgId: ORG, keyset: { at: "a", id: "b" } }),
    QueryPlanError,
    "a query with no sort key cannot resume from a cursor",
  );
  assert.throws(
    () => buildPlan("usage_page", { orgId: ORG, filters: { model: {} as unknown as string } }),
    QueryPlanError,
    "a filter value is a scalar",
  );
});

test("a read with no cursor in the contract still has a documented cap", () => {
  for (const [name, cap] of [
    ["keys_list", 100],
    ["feedback_by_request", 100],
    ["consent_history", 100],
    ["usage_daily", 400],
  ] as [string, number][]) {
    assert.equal(namedQuery(name as never).hardLimit, cap, `${name} must carry its documented cap`);
    const plan = buildPlan(name as never, { orgId: ORG });
    assert.equal(plan.limit, cap, `${name} must apply the cap when the caller asks for no limit`);
  }
});

test("every page query is ordered, bounded and keyset-resumable in BOTH renderers", () => {
  for (const name of pageQueries) {
    const spec = namedQuery(name);
    const tenanted = spec.tenantColumn !== null;
    const plan = buildPlan(name, {
      orgId: tenanted ? ORG : null,
      keyset: { at: "<at>", id: "<id>" },
      limit: 26,
    });
    const rendered = spec.engine === "clickhouse" ? renderClickHouseSql(plan) : renderSql(plan);
    const sort = spec.sort!;
    const direction = sort.direction;
    const comparison = direction === "desc" ? "<" : ">";
    assert.match(rendered.text, /\n limit 26$/, `${name} must be bounded`);
    assert.ok(
      rendered.text.includes(`order by ${sort.at.column} ${direction}, ${sort.id.column} ${direction}`),
      `${name} must order by its whole sort key: ${rendered.text}`,
    );
    assert.ok(
      rendered.text.includes(`(${sort.at.column}, ${sort.id.column}) ${comparison} (`),
      `${name} must resume on the row-wise comparison in its own direction: ${rendered.text}`,
    );
    if (tenanted) {
      const conditions = rendered.text.slice(rendered.text.indexOf("where")).split(/\n *and /);
      assert.match(
        conditions[conditions.length - 1],
        new RegExp(`${spec.tenantColumn!.replace(".", "\\.")} = `),
        `${name}: the tenant must be the final condition, after every other one`,
      );
      assert.ok(tenantParamIsLast(rendered, true), `${name}: and the last binding`);
    }
  }
});

test("an aggregate carries its tenant as a grouping column, and is ordered and bounded", () => {
  const daily = renderSql(buildPlan("usage_daily", { orgId: ORG }));
  assert.match(daily.text, /select u\.org_id as org_id, \(u\.created_at at time zone 'utc'\)::date as day/);
  assert.match(daily.text, /group by 1, 2\n order by 2 desc/, "grouped by tenant and day, ordered by day");
  assert.match(daily.text, /\n limit 400$/, "the day rows are bounded by the metadata horizon");

  const summary = renderSql(buildPlan("usage_summary", { orgId: ORG }));
  assert.match(summary.text, /select u\.org_id as org_id, count\(\*\) as requests/);
  assert.match(summary.text, /group by 1/, "a totals query says whose totals they are");

  // Every tenant-scoped query returns its tenant column, or `scopedPort` has nothing to check: a
  // totals query was exempt by construction, and eleven of twelve row queries said nothing either.
  for (const name of NAMED_QUERY_NAMES) {
    const spec = namedQuery(name);
    if (spec.tenantColumn === null) continue;
    assert.ok(spec.tenantField !== undefined, `${name} is tenant-scoped and must declare tenantField`);
    const rendered =
      spec.engine === "clickhouse"
        ? renderClickHouseSql(buildPlan(name, { orgId: ORG, limit: 1 }))
        : renderSql(buildPlan(name, { orgId: ORG, limit: 1 }));
    const selectList = rendered.text.slice(0, rendered.text.indexOf("\n  from"));
    assert.ok(
      new RegExp(`\\b${spec.tenantField}\\b`).test(selectList),
      `${name} must return ${spec.tenantField}: ${selectList}`,
    );
  }
});

test("every tenant-scoped named query binds the tenant last, under the reserved name", () => {
  for (const name of NAMED_QUERY_NAMES) {
    const spec = namedQuery(name);
    const tenanted = spec.tenantColumn !== null;
    if (tenanted) {
      assert.ok(spec.tenantField !== undefined, `${name} binds a tenant but declares no tenantField`);
    }
    const plan = buildPlan(name, { orgId: tenanted ? ORG : null, limit: 10 });
    const rendered = spec.engine === "clickhouse" ? renderClickHouseSql(plan) : renderSql(plan);
    assert.ok(
      tenantParamIsLast(rendered, tenanted),
      `${name}: the tenant parameter must be the single last binding (${rendered.paramNames.join(", ")})`,
    );
    if (tenanted) {
      assert.match(
        rendered.text,
        new RegExp(`${spec.tenantColumn!.replace(".", "\\.")} = `),
        `${name}: the tenant column must appear in the statement`,
      );
    }
  }
});

test("a tenant-scoped query cannot be built without the session organization", () => {
  for (const name of NAMED_QUERY_NAMES) {
    if (namedQuery(name).tenantColumn === null) continue;
    assert.throws(() => buildPlan(name, {}), QueryPlanError, `${name} must refuse to run untenanted`);
    assert.throws(() => buildPlan(name, { orgId: "" }), QueryPlanError, `${name} must refuse an empty tenant`);
  }
});

test("the tenant predicate is the last condition, after every caller filter", () => {
  const plan = buildPlan("usage_page", {
    orgId: ORG,
    filters: { from: "2026-09-01T00:00:00Z", key_id: "k1", model: "m1" },
    limit: 5,
  });
  const rendered = renderSql(plan);
  const conditions = rendered.text.slice(rendered.text.indexOf("where")).split("and");
  assert.match(conditions[conditions.length - 1], /u\.org_id = \$\d+/, "the tenant must be the final condition");
  assert.equal(rendered.params[rendered.params.length - 1], ORG, "and the last bound value");
  assert.equal(rendered.params.filter((value) => value === ORG).length, 1, "bound exactly once");
});

test("only the filters a named query declares can be bound, and never a reserved name", () => {
  assert.deepEqual(allowedFilters("usage_page").sort(), ["from", "key_id", "model", "to"]);
  assert.deepEqual(allowedFilters("ledger_page"), []);
  // The names a caller would reach for to widen the tenant are simply not filters.
  for (const smuggled of ["org_id", "o.org_id", "e.org_id", ...RESERVED_PARAM_NAMES]) {
    assert.throws(
      () => buildPlan("usage_page", { orgId: ORG, filters: { [smuggled]: OTHER } }),
      QueryPlanError,
      `${smuggled} must not be bindable`,
    );
    assert.throws(
      () => buildPlan("traces_page", { orgId: ORG, filters: { [smuggled]: OTHER } }),
      QueryPlanError,
      `${smuggled} must not be bindable on the projection either`,
    );
  }
});

test("a ClickHouse parameter map cannot lose its tenant to a caller parameter", () => {
  const plan = buildPlan("traces_page", {
    orgId: ORG,
    filters: { key_id: "k1", model: "m1", job_state: "succeeded" },
    limit: 7,
  });
  const rendered = renderClickHouseSql(plan);
  assert.equal(rendered.params.__org_id, ORG, "the bound organization is the session's");
  assert.equal(rendered.paramNames[rendered.paramNames.length - 1], "__org_id", "assigned last");
  assert.ok(
    !Object.values(rendered.params).includes(OTHER),
    "no other organization can appear in the parameter map",
  );
  // The projection's own predicate is part of the query, so no caller can drop it (R13).
  assert.match(rendered.text, /trace_mode <> \{f0:String\}/);
  assert.equal(rendered.params.f0, "off");
});

test("the rendered statements are the shipped SQL: one relation, named columns, bounded", () => {
  const usage = renderSql(buildPlan("usage_page", { orgId: ORG, limit: 26 }));
  // D1 ships the usage read as one view; the hold it joins lives in `infrx`, which browsers cannot see.
  assert.match(usage.text, /from public\.console_usage u/);
  assert.match(usage.text, /order by u\.created_at desc, u\.request_id desc/);
  assert.match(usage.text, /limit 26$/);
  assert.ok(!usage.text.includes("select *"), "a read selects named columns, never everything");
  // r2: every column the projection reads has to be asked for. `settlement_regime` is the one that
  // says which accounting rules wrote the row, and the statement omitted it while the view emitted
  // it — a column list is the only place that gap is visible, because a double that hands back
  // whole rows cannot show it.
  for (const column of ["u.settlement_regime", "u.key_name", "u.usage_certainty", "u.trace_mode"]) {
    assert.ok(usage.text.includes(column), `the usage statement must select ${column}`);
  }

  const wallet = renderSql(buildPlan("wallet_summary", { orgId: ORG, limit: 1 }));
  // The balance reads the wallet's stored totals; it is never recomputed from the ledger.
  assert.match(wallet.text, /w\.ledger_total, w\.reserved_total/);
  assert.ok(!wallet.text.includes("credit_ledger"), "the balance must not be recomputed from the ledger");

  const summary = renderSql(buildPlan("usage_summary", { orgId: ORG }));
  assert.match(summary.text, /count\(\*\) filter \(where u\.http_status >= \$\d+\) as failed_requests/);
  assert.match(summary.text, /u\.max_hold is not null/);

  const daily = renderSql(buildPlan("usage_daily", { orgId: ORG }));
  assert.match(daily.text, /group by 1, 2\n order by 2 desc/);
});

test("a keyset bound compares the whole sort key, in the list's own direction", () => {
  const descending = renderSql(
    buildPlan("ledger_page", { orgId: ORG, keyset: { at: "2026-09-01T00:00:00Z", id: "abc" }, limit: 5 }),
  );
  assert.match(descending.text, /\(l\.created_at, l\.id\) < \(\$\d+, \$\d+\)/);
  const ascending = renderSql(buildPlan("admin_orgs_page", { keyset: { at: "Acme", id: "abc" }, limit: 5 }));
  assert.match(ascending.text, /\(o\.name, o\.org_id\) > \(\$\d+, \$\d+\)/);
});
