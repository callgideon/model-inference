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
  assert.match(conditions[conditions.length - 1], /e\.org_id = \$\d+/, "the tenant must be the final condition");
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
  assert.match(usage.text, /from public\.usage_events e/);
  assert.match(usage.text, /order by e\.created_at desc, e\.id desc/);
  assert.match(usage.text, /limit 26$/);
  assert.ok(!usage.text.includes("select *"), "a read selects named columns, never everything");

  const wallet = renderSql(buildPlan("wallet_summary", { orgId: ORG, limit: 1 }));
  // The available balance is a wallet column, not a sum over the ledger.
  assert.match(wallet.text, /\(w\.ledger_total - w\.reserved_total\) as available/);
  assert.ok(!wallet.text.includes("credit_ledger"), "the balance must not be recomputed from the ledger");

  const summary = renderSql(buildPlan("usage_summary", { orgId: ORG }));
  assert.match(summary.text, /count\(\*\) filter \(where e\.status >= \$\d+\) as failed_requests/);
  assert.match(summary.text, /h\.max_amount_usd is not null/);

  const daily = renderSql(buildPlan("usage_daily", { orgId: ORG }));
  assert.match(daily.text, /group by 1\n order by 1 desc/);
});

test("a keyset bound compares the whole sort key, in the list's own direction", () => {
  const descending = renderSql(
    buildPlan("ledger_page", { orgId: ORG, keyset: { at: "2026-09-01T00:00:00Z", id: "abc" }, limit: 5 }),
  );
  assert.match(descending.text, /\(l\.created_at, l\.id\) < \(\$\d+, \$\d+\)/);
  const ascending = renderSql(buildPlan("admin_orgs_page", { keyset: { at: "Acme", id: "abc" }, limit: 5 }));
  assert.match(ascending.text, /\(o\.name, o\.org_id\) > \(\$\d+, \$\d+\)/);
});
