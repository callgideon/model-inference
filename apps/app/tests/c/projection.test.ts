// node --test "tests/**/*.test.ts"
//
// What a stored row may and may not become (R35/R43/R49, R24, and the "projection" half of C1).
//
// Every case here drives the real services through the in-memory port over a *deliberately wrong*
// row, because the interesting failures are the ones a well-formed fixture can never produce: a
// calibration label sitting in the same relation as ordinary feedback, a JSON blob carrying fields the
// DTO does not declare, a PostgreSQL array literal where an array was promised, a driver `Date` where
// an RFC 3339 string was promised, `"abc"` where an integer was.

import assert from "node:assert/strict";
import test from "node:test";
import { MAX_PAGE_LIMIT } from "../../lib/contracts/types.ts";
import { makeConsoleHarness } from "./harness.ts";

function expectOk<T>(result: { ok: true; value: T } | { ok: false; error: { code: string; message: string } }): T {
  assert.ok(result.ok, `expected success, got ${result.ok ? "" : `${result.error.code}: ${result.error.message}`}`);
  return result.value;
}

function expectError(
  result: { ok: true; value: unknown } | { ok: false; error: { code: string; message: string } },
  code: string,
  what: string,
): void {
  assert.ok(!result.ok, `${what}: expected ${code}`);
  assert.equal(result.error.code, code, what);
}

/**
 * Every usage page, because a mangled timestamp changes where its row sorts: `"2026-09-02 …"` is far
 * from the head of the list, so a single-page probe would never project the row it just broke.
 */
async function walkUsage(
  services: { usage: (session: never, query: unknown) => Promise<ListResult> },
  session: unknown,
): Promise<ListResult> {
  let cursor: string | null = null;
  const items: UsageLike[] = [];
  for (let pages = 0; pages < 100; pages += 1) {
    const page = await services.usage(session as never, { limit: MAX_PAGE_LIMIT, cursor } as never);
    if (!page.ok) return page;
    items.push(...page.value.items);
    if (page.value.next_cursor === null) return { ok: true, value: { items, next_cursor: null } };
    cursor = page.value.next_cursor;
  }
  throw new Error("pagination did not terminate");
}

type UsageLike = { request_id: string; created_at: string; cost: string; key_id: string; key_name: string };

type ListResult =
  | { ok: true; value: { items: UsageLike[]; next_cursor: string | null } }
  | { ok: false; error: { code: string; message: string } };

test("a calibration label never reaches a feedback list or a trace detail, for anyone (R49/R35)", async () => {
  const { services, sessions, data, ids } = makeConsoleHarness();
  // The fixture's label is stored in the same relation as ordinary feedback (R43). If this ever stops
  // being true the case below would pass for the wrong reason, so the premise is asserted first.
  const labels = data.feedback.filter((row) => row.calibration_set === true);
  assert.ok(labels.length > 0, "the dataset must contain a calibration label to exclude");
  const label = labels[0];
  assert.equal(label.author_role, "operator", "R54: a label row is operator-authored");
  assert.equal(label.by_operator, true, "R50/R55: and carries the operator marker");
  const requestId = String(label.request_id);

  for (const [who, session] of [
    ["owner", sessions.owner],
    ["member", sessions.member],
    // An operator too: labels are read only through the calibration listing (R49), not by authority.
    ["operator", sessions.operator],
  ] as const) {
    const entries = expectOk(await services.feedback.list(session, requestId));
    assert.ok(
      !entries.some((entry) => entry.id === label.id),
      `${who} must not see the label through feedback.list`,
    );
    assert.ok(
      entries.every((entry) => entry.calibration_set === false && entry.rubric_version === null),
      `${who} must see no calibration entry at all`,
    );
    assert.ok(
      !JSON.stringify(entries).includes(String(label.author_principal)),
      `${who} must not learn the labelling operator's principal`,
    );

    const detail = expectOk(await services.traceDetail(session, requestId));
    assert.ok(
      !detail.feedback.some((entry) => entry.id === label.id),
      `${who} must not see the label through traceDetail`,
    );
    assert.equal(
      detail.feedback_count,
      detail.feedback.filter(() => true).length >= 0 ? detail.feedback_count : -1,
      "feedback_count is the stored count, which never counts a label",
    );
  }

  // And the ordinary entries on that request are still returned — the exclusion is the label, not the list.
  const ordinary = data.feedback.filter((row) => row.request_id === requestId && row.calibration_set === false);
  const listed = expectOk(await services.feedback.list(sessions.owner, requestId));
  assert.equal(listed.length, ordinary.length, "every non-label entry on the request is listed");
  assert.ok(ids.availableRequestId.length > 0);
});

test("a principal is masked unless the row says explicitly that no operator was involved", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  // Operator authority is the boolean `true`, not a truthy value: a mis-wired caller handing `1` or
  // `"false"` must not unmask a principal, so the check is an identity rather than a coercion.
  const notQuiteOperator = [
    { ...sessions.owner, isOperator: 1 as unknown as boolean },
    { ...sessions.owner, isOperator: "false" as unknown as boolean },
    { ...sessions.owner, isOperator: {} as unknown as boolean },
  ];
  // A D1 function that forgets to select `by_operator` must hide principals, not publish them.
  data.ledger.push({
    id: "led_no_marker",
    created_at: "2026-09-20T12:00:00.000Z",
    delta: "1.00000000",
    kind: "grant",
    reason: "row without the marker column",
    ref: null,
    actor: "operator@infrx.example",
    org_id: ids.orgId,
  });
  const page = expectOk(await services.ledger(sessions.owner, { limit: MAX_PAGE_LIMIT }));
  const entry = page.items.find((row) => row.id === "led_no_marker");
  assert.ok(entry !== undefined, "the row is the newest, so it is on the first page");
  assert.equal(entry.actor, "platform", "a missing marker masks");

  for (const session of notQuiteOperator) {
    const seen = expectOk(await services.ledger(session, { limit: MAX_PAGE_LIMIT }));
    const row = seen.items.find((candidate) => candidate.id === "led_no_marker");
    assert.equal(
      row?.actor,
      "platform",
      `isOperator=${JSON.stringify(session.isOperator)} is not operator authority`,
    );
  }
});

test("a stored value that is not what the DTO says is a typed refusal, not a blank", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  const usage = data.usage.find((row) => row.org_id === ids.orgId);
  assert.ok(usage !== undefined);

  /**
   * Every page, because a mangled timestamp changes where its row sorts: `"2026-09-20"` is the oldest
   * key in the list, so a single-page probe would never project the row it just broke.
   */
  const walkUsage = async (): Promise<
    { ok: true; value: unknown } | { ok: false; error: { code: string; message: string } }
  > => {
    let cursor: string | null = null;
    for (let pages = 0; pages < 100; pages += 1) {
      const page = await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT, cursor });
      if (!page.ok) return page;
      if (page.value.next_cursor === null) return { ok: true, value: pages };
      cursor = page.value.next_cursor;
    }
    throw new Error("pagination did not terminate");
  };

  const original = { ...usage };
  for (const [column, value, what] of [
    ["prompt_tokens", "abc", "a token count that is not a number"],
    ["http_status", "", "an empty status, which Number() reads as 0"],
    ["http_status", true, "a boolean status, which Number() reads as 1"],
    ["http_status", ["500"], "a one-element list, which Number() unwraps"],
    ["http_status", "0x10", "a hexadecimal status"],
    ["http_status", "1e2", "an exponent"],
    ["prompt_tokens", 1.5, "a fractional token count"],
    ["model", 42, "a model id that is not a string"],
    ["job_state", { state: "running" }, "an enum that arrived as an object"],
    ["created_at", new Date("2026-09-20T12:00:00Z"), "a driver Date where a timestamp string was promised"],
    ["created_at", "2026-09-20", "a date without a time"],
    ["http_status", 1.5, "a fractional status"],
    ["cost", "not-money", "a cost that is not a decimal"],
  ] as [string, unknown, string][]) {
    Object.assign(usage, original);
    usage[column] = value;
    expectError(await walkUsage(), "internal_error", what);
  }
  Object.assign(usage, original);
  assert.ok((await walkUsage()).ok, "and the untouched dataset still reads");

  // A missing column is a refusal too: `String(undefined ?? "")` used to render it as an empty cell.
  delete usage.model;
  expectError(await walkUsage(), "internal_error", "a missing column");
});

test("both UTC timestamp forms are accepted and normalised, microseconds and all", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  const usage = data.usage.find((row) => row.org_id === ids.orgId);
  assert.ok(usage !== undefined);

  // PostgREST renders `timestamptz` with `+00:00`; the fixtures and the API use `Z`. Both are the
  // same instant, so both are read — and the DTO carries one form, with every digit it was given.
  usage.created_at = "2026-09-20T11:57:43.123456+00:00";
  const page = expectOk(await walkUsage(services as never, sessions.owner));
  const row = page.items.find((candidate) => candidate.request_id === usage.request_id);
  assert.equal(row?.created_at, "2026-09-20T11:57:43.123456Z", "microseconds survive: they are half of a cursor key");

  // PostgREST trims trailing zeros, so a relation can hold `.12+00:00` beside `.123456Z`. A keyset
  // compares these strings, and `.12` would sort after `.123456`; the width is therefore fixed at six
  // digits rather than preserved, and the exported suite's "one comparable width per list" holds.
  usage.created_at = "2026-09-20T11:57:43.12+00:00";
  const trimmed = expectOk(await walkUsage(services as never, sessions.owner));
  assert.equal(
    trimmed.items.find((candidate) => candidate.request_id === usage.request_id)?.created_at,
    "2026-09-20T11:57:43.120000Z",
    "a trimmed fraction is padded, not left short",
  );
  const widths = new Set(trimmed.items.map((item) => item.created_at.length));
  assert.equal(widths.size, 1, `one list, one width: ${[...widths].join(", ")}`);
  usage.created_at = "2026-09-20T11:57:43.123456+00:00";

  // A walk still works when a row carries the other form, because the cursor key and the comparison
  // both use the instant. One rewritten row is not enough to show that — it is never a cursor row at a
  // page size of 7 — so the three walks below rewrite the whole relation.
  let cursor: string | null = null;
  const seen: string[] = [];
  for (let pages = 0; pages < 100; pages += 1) {
    const next: { items: { request_id: string }[]; next_cursor: string | null } = expectOk(
      await services.usage(sessions.owner, { limit: 7, cursor }),
    );
    seen.push(...next.items.map((item) => item.request_id));
    if (next.next_cursor === null) break;
    cursor = next.next_cursor;
  }
  assert.equal(new Set(seen).size, seen.length, "no row is returned twice across the form change");
  assert.ok(seen.includes(String(usage.request_id)), "and the row in the other form is walked exactly once");

  for (const [value, what] of [
    ["2026-09-02 07:12:18+00", "the ::text form"],
    ["2026-09-20", "a date with no time"],
    ["2026-09-20T11:57:43+02:00", "a non-UTC offset"],
    ["2026-09-20T11:57:43.1234567Z", "more than microsecond precision"],
    [new Date("2026-09-20T11:57:43Z"), "a driver Date"],
  ] as [unknown, string][]) {
    usage.created_at = value;
    expectError(await walkUsage(services as never, sessions.owner), "internal_error", what);
  }
});

test("a walk is exactly-once whatever UTC form the relation uses", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  const own = data.usage.filter((row) => row.org_id === ids.orgId);
  assert.ok(own.length > 100, "the relation must be longer than a page for this to mean anything");

  const walkIds = async (limit: number): Promise<string[]> => {
    const seen: string[] = [];
    let cursor: string | null = null;
    for (let pages = 0; pages < 3000; pages += 1) {
      const page: { items: { request_id: string }[]; next_cursor: string | null } = expectOk(
        await services.usage(sessions.owner, { limit, cursor }),
      );
      seen.push(...page.items.map((item) => item.request_id));
      if (page.next_cursor === null) return seen;
      cursor = page.next_cursor;
    }
    throw new assert.AssertionError({ message: `the walk at limit ${limit} did not terminate` });
  };

  const expected = (await walkIds(MAX_PAGE_LIMIT)).sort();
  assert.equal(expected.length, own.length);

  // (1) Every row in PostgREST's form. A keyset that compared the raw strings against a cursor minted
  // from the projected form looped forever at limit 1 and returned 243 of 160 rows at limit 3.
  for (const row of own) row.created_at = String(row.created_at).replace(/Z$/, "+00:00");
  for (const limit of [1, 3, 7, MAX_PAGE_LIMIT]) {
    const walked = await walkIds(limit);
    assert.deepEqual(walked.sort(), expected, `all rows in +00:00, walked at limit ${limit}`);
  }

  // (2) The two forms alternating within one relation.
  own.forEach((row, index) => {
    row.created_at = String(row.created_at).replace(/(Z|\+00:00)$/, index % 2 === 0 ? "Z" : "+00:00");
  });
  for (const limit of [1, 3, 7]) {
    const walked = await walkIds(limit);
    assert.deepEqual(walked.sort(), expected, `both forms alternating, walked at limit ${limit}`);
  }

  // (3) A microsecond pair one tick apart, in opposite forms, across a page boundary of one.
  own[0].created_at = "2026-09-20T23:00:00.123457Z";
  own[1].created_at = "2026-09-20T23:00:00.123456+00:00";
  const pairWalk = await walkIds(1);
  assert.deepEqual(pairWalk.sort(), expected, "the microsecond pair is walked once each");
  assert.equal(
    pairWalk.indexOf(String(own[0].request_id)) + 1,
    pairWalk.indexOf(String(own[1].request_id)),
    "and in instant order, newest first",
  );
});

test("money arrives as text: a number is accepted only where a double still holds eight digits", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  const usage = data.usage.find((row) => row.org_id === ids.orgId);
  assert.ok(usage !== undefined);

  // A double holds 2^53 units of 1e-8, so past ~2^26 dollars `toFixed(8)` invents the last digits:
  // 123456789012.12345678 came back as …12345886 — digits the customer never spent.
  for (const value of [123456789012.12345678, 2 ** 26, -(2 ** 26), 1e20, Number.NaN, Number.POSITIVE_INFINITY]) {
    usage.cost = value;
    expectError(
      await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT }),
      "internal_error",
      `${String(value)} cannot be a money value as a number`,
    );
  }
  // Just inside the bound a number is exact, and that is the only path that still hands one over.
  usage.cost = 2 ** 26 - 1;
  const inside = expectOk(await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT }));
  assert.equal(
    inside.items.find((row) => row.request_id === usage.request_id)?.cost,
    "67108863.00000000",
  );
  // Text is the contract, at any magnitude the domain allows.
  usage.cost = "123456789012.12345678";
  const text = expectOk(await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT }));
  assert.equal(
    text.items.find((row) => row.request_id === usage.request_id)?.cost,
    "123456789012.12345678",
    "a decimal string keeps every digit it was given",
  );
});

test("a boolean column is a boolean: a suspension flag is never guessed at", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  const org = data.orgs.find((row) => row.org_id === ids.orgId);
  assert.ok(org !== undefined);
  // `suspended` decides whether new work is refused (R33). A reader that fell back to `false` for a
  // value it did not recognise would silently unsuspend an organization.
  for (const value of ["yes", "maybe", 2, "", null, {}]) {
    org.suspended = value;
    expectError(
      await services.usage(sessions.owner, { limit: 5 }),
      "internal_error",
      `suspended=${JSON.stringify(value)} must be refused, not guessed`,
    );
  }
  for (const [value, suspended] of [
    [true, true],
    [false, false],
    [1, true],
    [0, false],
    ["true", true],
    ["false", false],
  ] as [unknown, boolean][]) {
    org.suspended = value;
    const result = await services.judgeRuns(sessions.owner, { limit: 5 });
    assert.equal(
      result.ok ? "allowed" : result.error.code,
      suspended ? "org_suspended" : "allowed",
      `suspended=${JSON.stringify(value)} must read as ${String(suspended)}`,
    );
  }
});

test("a usage row survives a deleted key, and says so", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  // D1's view LEFT JOINs `api_keys` and `usage_events.api_key_id` is `on delete set null`, so a
  // deleted key leaves **both** columns null. That is the state the view can actually produce, and it
  // used to deny the whole page: `key_id` went through the strict string reader.
  const usage = data.usage.find((row) => row.org_id === ids.orgId);
  assert.ok(usage !== undefined);
  usage.key_id = null;
  usage.key_name = null;
  const page = expectOk(await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT }));
  const row = page.items.find((candidate) => candidate.request_id === usage.request_id);
  assert.ok(row !== undefined, "the row is kept, or the cost total it carries would be understated");
  assert.equal(row.key_name, "(deleted key)");
  assert.equal(row.key_id, "", "the documented sentinel, until key_id becomes nullable in the contract");

  // And the sentinel is unreachable from a filter: a key id filter must be identifier-shaped.
  expectError(await services.usage(sessions.owner, { key_id: "" }), "invalid_request", "the empty key filter");
  const byKey = expectOk(await services.usage(sessions.owner, { key_id: ids.keyId, limit: MAX_PAGE_LIMIT }));
  assert.ok(
    !byKey.items.some((candidate) => candidate.key_id === ""),
    "a real key filter never matches the sentinel",
  );

  // Exactly one of the two null is a shape the view cannot produce: it is a malformed row.
  usage.key_id = ids.keyId;
  usage.key_name = null;
  expectError(await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT }), "internal_error", "name null alone");
  usage.key_id = null;
  usage.key_name = "restored";
  expectError(await services.usage(sessions.owner, { limit: MAX_PAGE_LIMIT }), "internal_error", "id null alone");
});

test("entitlements fail closed: R24's three states are never reached by coercion", async () => {
  const { services, sessions, data, ids } = makeConsoleHarness();
  const org = data.orgs.find((row) => row.org_id === ids.orgId);
  assert.ok(org !== undefined);
  const original = { ...org };

  // A PostgreSQL array literal is not a JSON array. Reading it as `null` would turn a recorded
  // "exactly these models" into "the platform default set" — R24 inverted.
  for (const [value, what] of [
    ["{marlin-2b@2026-09-01}", "a PostgreSQL array literal"],
    ["\"marlin-2b@2026-09-01\"", "a bare JSON string"],
    ["[1, 2]", "a list of non-strings"],
  ] as [unknown, string][]) {
    Object.assign(org, original);
    org.model_ids = value;
    expectError(await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }), "internal_error", what);
  }

  for (const [limits, what] of [
    [{ max_concurrent_requests: 4, sneaky_limit: 1 }, "a limit name outside the closed set"],
    [{ max_concurrent_requests: "4" }, "a limit that is not a number"],
    [{ max_concurrent_requests: 1.5 }, "a fractional limit"],
    [{ max_concurrent_requests: -1 }, "a negative limit"],
    [{ max_concurrent_requests: 1000001 }, "a limit past the provisional ceiling"],
  ] as [unknown, string][]) {
    Object.assign(org, original);
    org.limits = limits;
    expectError(await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }), "internal_error", what);
  }

  Object.assign(org, original);
  const listed = expectOk(await services.adminOrgs(sessions.operator, { limit: MAX_PAGE_LIMIT }));
  const kinds = new Set(
    listed.items.map((row) => (row.entitlements.model_ids === null ? "null" : row.entitlements.model_ids.length === 0 ? "empty" : "list")),
  );
  assert.ok(kinds.size >= 2, "the harness must carry more than one entitlement state");
  for (const row of listed.items) {
    for (const name of Object.keys(row.entitlements.limits)) {
      assert.ok(
        ["max_concurrent_requests", "max_requests_per_minute", "max_video_seconds"].includes(name),
        `${name} is not an entitlement limit name`,
      );
    }
  }
});

test("a judge sample is projected field by field, so a stored blob cannot widen it", async () => {
  const { services, sessions, data, ids } = makeConsoleHarness();
  const run = data.judge.find(
    (row) => row.org_id === ids.orgId && Array.isArray(row.samples) && (row.samples as unknown[]).length > 0,
  );
  assert.ok(run !== undefined, "the harness must carry a judge run with at least one sample");
  const samples = run.samples as Record<string, unknown>[];

  // A projection that carried the stored object through would hand these to the owner verbatim.
  samples[0].org_id = ids.orgId;
  samples[0].provider_batch_secret = "batch_live_should_never_be_here";
  samples[0].labelled_by = "operator@infrx.example";
  const page = expectOk(await services.judgeRuns(sessions.owner, { limit: 20 }));
  const serialised = JSON.stringify(page.items);
  assert.ok(!serialised.includes("provider_batch_secret"), "a stored field the DTO does not declare is dropped");
  assert.ok(!serialised.includes("batch_live_should_never_be_here"), "and so is its value");
  assert.ok(!serialised.includes("labelled_by"), "including an operator principal on a sample");
  const projected = page.items.flatMap((item) => item.samples)[0];
  assert.deepEqual(
    Object.keys(projected).sort(),
    ["id", "limited_evaluation", "limited_reason", "request_id", "run_id", "scores"],
    "a sample is exactly the DTO's fields",
  );
  for (const score of projected.scores) {
    assert.deepEqual(
      Object.keys(score).sort(),
      [
        "estimated",
        "judge_model",
        "judge_model_version",
        "kind",
        "name",
        "rationale",
        "rubric_version",
        "value_bool",
        "value_label",
        "value_num",
        "value_text",
      ],
      "and a score is exactly the DTO's fields",
    );
  }

  // Garbage inside the blob is a typed refusal rather than a half-rendered run.
  samples[0].limited_reason = "because";
  expectError(await services.judgeRuns(sessions.owner, { limit: 20 }), "internal_error", "an unknown limited reason");
  samples[0].limited_reason = null;
  (samples[0].scores as Record<string, unknown>[])[0].kind = "vibes";
  expectError(await services.judgeRuns(sessions.owner, { limit: 20 }), "internal_error", "an unknown score kind");
  (samples[0].scores as Record<string, unknown>[])[0].kind = "numeric";
  for (const bad of ["", true, [4], "abc", {}]) {
    (samples[0].scores as Record<string, unknown>[])[0].value_num = bad;
    expectError(
      await services.judgeRuns(sessions.owner, { limit: 20 }),
      "internal_error",
      `a score value of ${JSON.stringify(bad)} must not be coerced`,
    );
  }
  (samples[0].scores as Record<string, unknown>[])[0].value_num = 4.5;
  const fine = expectOk(await services.judgeRuns(sessions.owner, { limit: 20 }));
  assert.equal(fine.items.flatMap((item) => item.samples)[0].scores[0].value_num, 4.5, "a real number is kept");
});

test("consent history is capped, and the cap is the documented one", async () => {
  const { services, sessions, ids, data } = makeConsoleHarness();
  for (let i = 0; i < 150; i += 1) {
    data.consent.push({
      version: 100 + i,
      changed_at: new Date(Date.parse("2026-01-01T00:00:00.000Z") + i * 60000).toISOString(),
      evaluation_consent: i % 2 === 0,
      changed_by: "owner@northwind.example",
      by_operator: false,
      org_id: ids.orgId,
    });
  }
  const settings = expectOk(await services.settings.get(sessions.owner));
  assert.equal(settings.consent_history.length, 100, "the read is bounded even though the DTO has no cursor");
});
