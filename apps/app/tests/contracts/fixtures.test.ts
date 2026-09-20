// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
//
// The fixture JSON is data, so it is validated against the frozen vocabulary with small
// hand-written guards. No schema dependency is added: 08 §6 forbids a new console package,
// and a bad fixture must fail here rather than surface as a mystery in a UI track.
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import test from "node:test";
import judgeFixture from "../../lib/contracts/fixtures/judge.json" with { type: "json" };
import orgsFixture from "../../lib/contracts/fixtures/orgs.json" with { type: "json" };
import traceFixture from "../../lib/contracts/fixtures/traces.json" with { type: "json" };
import { isMoney } from "../../lib/contracts/money.ts";
import {
  AUTHOR_ROLES,
  CALIBRATION_LABELS,
  ENTITLEMENT_LIMIT_NAMES,
  FEEDBACK_CHANNELS,
  FEEDBACK_ENTRY_NAMES,
  FEEDBACK_RATING_MAX,
  FEEDBACK_RATING_MIN,
  JUDGE_MODES,
  JUDGE_RUN_STATES,
  JUDGE_SCORE_KINDS,
  MAX_CONTENT_RETENTION_DAYS,
  ORG_ROLES,
  TRACE_CONTENT_AVAILABILITY,
  TRACE_MODES,
} from "../../lib/contracts/types.ts";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;

function inSet(allowed: readonly string[], value: unknown): boolean {
  return typeof value === "string" && allowed.includes(value);
}

const orgs = orgsFixture as unknown as {
  clock: string;
  models: string[];
  orgs: {
    org_id: string;
    name: string;
    owner_email: string;
    created_at: string;
    usage_rows: number;
    all_free: boolean;
    suspended: boolean;
    suspension_reason: string | null;
    grants: { amount: string; reason: string; created_at: string }[];
    adjustment: { amount: string; reason: string } | null;
    legacy_purchase: { amount: string; reason: string; created_at: string } | null;
    entitlements: { model_ids: string[] | null; limits: Record<string, number> };
    keys: { id: string; prefix: string; trace_mode: string; revoked_at: string | null }[];
    settings: {
      trace_mode: string;
      content_retention_days: number;
      evaluation_consent: boolean;
      consent_history: { changed_at: string; evaluation_consent: boolean; changed_by: string }[];
    };
  }[];
  sessions: Record<string, { userId: string; email: string; orgId: string; role: string; isOperator: boolean }>;
};

const traces = traceFixture as unknown as {
  availability_cycle: string[];
  content_templates: {
    v: number;
    request: { model: string; messages: { role: string; content: unknown }[] };
    response: { status: number; choices: unknown[]; error: { code: string } | null };
  }[];
  seed_feedback: {
    trace_index: number;
    channel: string;
    author_role: string;
    name: string;
    value: boolean | number | string;
    comment: string | null;
    calibration_set: boolean;
    rubric_version: number | null;
  }[];
};

const judge = judgeFixture as unknown as {
  runs: {
    id: string;
    state: string;
    mode: string;
    rubric_version: number;
    budget_reserved: string;
    budget_settled: string | null;
    external_batch_id: string | null;
    quarantine_reason: string | null;
    consent_snapshot_at: string;
    samples: {
      id: string;
      trace_index: number;
      limited_evaluation: boolean;
      limited_reason: string | null;
      scores: { name: string; kind: string; estimated: boolean }[];
    }[];
  }[];
};

test("every fixture file is one the contract knows about", () => {
  const files = readdirSync(new URL("../../lib/contracts/fixtures/", import.meta.url))
    .filter((name) => name.endsWith(".json"))
    .sort();
  assert.deepEqual(files, ["judge.json", "money.json", "orgs.json", "traces.json"]);
});

test("the organization fixtures match the contract vocabulary", () => {
  assert.match(orgs.clock, RFC3339);
  // Two for cross-tenant denial, a third suspended so `org_suspended` is reachable (R18).
  assert.equal(orgs.orgs.length, 3, "an established, a new and a suspended organization");
  const ids = new Set(orgs.orgs.map((org) => org.org_id));
  assert.equal(ids.size, 3, "the organizations need distinct ids");
  const suspended = orgs.orgs.filter((org) => org.suspended);
  assert.equal(suspended.length, 1, "exactly one organization is suspended");
  assert.ok(
    suspended[0].suspension_reason !== null && suspended[0].suspension_reason.length > 0,
    "a suspended organization records why",
  );
  for (const org of orgs.orgs.filter((candidate) => !candidate.suspended)) {
    assert.equal(org.suspension_reason, null, `${org.name} is not suspended, so it has no reason`);
  }

  for (const org of orgs.orgs) {
    assert.match(org.org_id, UUID, org.name);
    assert.match(org.created_at, RFC3339, org.name);
    assert.ok(org.usage_rows > 0, `${org.name} needs rows`);
    assert.ok(inSet(TRACE_MODES, org.settings.trace_mode), `${org.name} settings trace_mode`);
    assert.ok(
      Number.isInteger(org.settings.content_retention_days) &&
        org.settings.content_retention_days >= 1 &&
        org.settings.content_retention_days <= MAX_CONTENT_RETENTION_DAYS,
      `${org.name} retention must respect the ${MAX_CONTENT_RETENTION_DAYS}-day cap`,
    );
    assert.equal(typeof org.settings.evaluation_consent, "boolean", `${org.name} consent`);
    for (const entry of org.settings.consent_history) {
      assert.match(entry.changed_at, RFC3339);
      assert.equal(typeof entry.evaluation_consent, "boolean");
      assert.ok(entry.changed_by.length > 0);
    }
    for (const grant of org.grants) {
      assert.ok(isMoney(grant.amount), `${org.name} grant amount ${grant.amount} is not canonical money`);
      assert.ok(grant.reason.trim().length > 0, "a grant needs a reason");
      assert.match(grant.created_at, RFC3339);
    }
    if (org.adjustment !== null) {
      assert.ok(isMoney(org.adjustment.amount), "adjustment amount");
    }
    // R13: one historical `purchase` row exists so the kind stays renderable.
    if (org.legacy_purchase !== null) {
      assert.ok(isMoney(org.legacy_purchase.amount), "legacy purchase amount");
      assert.ok(org.legacy_purchase.reason.trim().length > 0, "a legacy purchase says what it was");
      assert.match(org.legacy_purchase.created_at, RFC3339);
    }
    assert.ok(org.keys.length > 0, `${org.name} needs a key`);
    for (const key of org.keys) {
      assert.match(key.id, UUID, `${org.name} key id`);
      assert.match(key.prefix, /^sk-infrx-[A-Za-z0-9]+$/, "key prefix");
      assert.ok(inSet(TRACE_MODES, key.trace_mode), "key trace_mode");
      assert.ok(key.revoked_at === null || RFC3339.test(key.revoked_at), "key revoked_at");
    }
    // No fixture may carry anything that looks like a live credential.
    assert.ok(!JSON.stringify(org).includes("sk-infrx-FAKE") || org.all_free, "keys are prefixes only");
  }

  // R24: the three entitlement states are different facts, and U3 must render each of them.
  const states = orgs.orgs.map((org) =>
    org.entitlements.model_ids === null
      ? "default"
      : org.entitlements.model_ids.length === 0
        ? "none"
        : "explicit",
  );
  assert.deepEqual([...states].sort(), ["default", "explicit", "none"], "one organization of each kind");
  for (const org of orgs.orgs) {
    const list = org.entitlements.model_ids;
    if (list !== null) {
      assert.equal(new Set(list).size, list.length, `${org.name} entitlement list repeats a model`);
      for (const model of list) {
        assert.ok(orgs.models.includes(model), `${org.name} is entitled to ${model}, which is not served`);
      }
    }
    for (const [name, value] of Object.entries(org.entitlements.limits)) {
      assert.ok(inSet(ENTITLEMENT_LIMIT_NAMES, name), `${org.name} limit ${name}`);
      assert.ok(Number.isInteger(value) && value >= 0, `${org.name} limit ${name} must be a whole number`);
    }
  }

  const zeroBalance = orgs.orgs.filter((org) => org.grants.length === 0);
  assert.equal(zeroBalance.length, 1, "exactly one organization must start at zero");
  const established = orgs.orgs.filter((org) => org.grants.length > 0 && !org.suspended);
  assert.equal(established.length, 1);
  assert.ok(
    established[0].keys.some((key) => key.trace_mode === "full") &&
      established[0].keys.some((key) => key.trace_mode === "minimal") &&
      established[0].keys.some((key) => key.trace_mode === "off"),
    "the established organization needs a key per trace mode",
  );
  assert.ok(
    established[0].keys.some((key) => key.revoked_at !== null),
    "a revoked key is needed for the keys page",
  );

  const sessions = Object.values(orgs.sessions);
  assert.equal(
    sessions.length,
    6,
    "owner, member, two operators (one an owner, one only a member), another organization's owner, and a suspended one",
  );
  // B2: operator authority is the flag, so the harness needs an operator who is *not* an owner —
  // otherwise an implementation that checks the role instead of the flag passes.
  const operators = sessions.filter((session) => session.isOperator);
  assert.equal(operators.length, 2, "two operator sessions");
  assert.ok(operators.some((session) => session.role === "owner"), "one operator is also an owner");
  assert.ok(operators.some((session) => session.role === "member"), "and one is only a member");
  assert.ok(
    sessions.some((session) => session.orgId === suspended[0].org_id),
    "R18: a session must belong to the suspended organization, or org_suspended is unreachable",
  );
  for (const session of sessions) {
    assert.match(session.userId, UUID);
    assert.ok(inSet(ORG_ROLES, session.role), "a session carries an organization role");
    assert.equal(typeof session.isOperator, "boolean", "operator authority is a separate flag");
    assert.ok(ids.has(session.orgId), "a session must belong to a fixture organization");
  }

  assert.equal(
    sessions.filter((session) => session.role === "member" && !session.isOperator).length,
    1,
    "exactly one plain member",
  );
});

test("the trace fixtures are renderable content with no storage reference", () => {
  for (const state of traces.availability_cycle) {
    assert.ok(inSet(TRACE_CONTENT_AVAILABILITY, state), `${state} is not an availability state`);
  }
  assert.ok(traces.content_templates.length > 0);
  for (const template of traces.content_templates) {
    assert.equal(template.v, 1, "content schema version 1");
    assert.ok(Array.isArray(template.request.messages) && template.request.messages.length > 0);
    for (const message of template.request.messages) {
      assert.ok(message.role.length > 0);
      assert.ok(
        typeof message.content === "string" || Array.isArray(message.content),
        "content is text or parts",
      );
    }
    assert.ok(Array.isArray(template.response.choices));
    assert.ok(Number.isInteger(template.response.status));
  }
  const serialized = JSON.stringify(traces);
  assert.ok(!serialized.includes("s3://"), "no storage path in a fixture");
  assert.ok(!serialized.includes("X-Amz-"), "no signed URL in a fixture");
  assert.ok(!serialized.includes("https://"), "no fetchable media URL in a fixture");

  for (const seed of traces.seed_feedback) {
    assert.ok(Number.isInteger(seed.trace_index) && seed.trace_index >= 0);
    assert.ok(inSet(FEEDBACK_CHANNELS, seed.channel), "feedback channel");
    assert.ok(inSet(AUTHOR_ROLES, seed.author_role), "feedback author role");
    assert.ok(inSet(FEEDBACK_ENTRY_NAMES, seed.name), "feedback name");
    // R3 pairs each name with its value type; a fixture that drifts would teach V the wrong form.
    if (seed.name === "thumb") {
      assert.equal(typeof seed.value, "boolean", "a thumb value is a boolean");
    } else if (seed.name === "rating") {
      assert.ok(
        typeof seed.value === "number" &&
          Number.isInteger(seed.value) &&
          seed.value >= FEEDBACK_RATING_MIN &&
          seed.value <= FEEDBACK_RATING_MAX,
        "a rating value is an integer from 1 to 5",
      );
    } else if (seed.name === "calibration_label") {
      // R19: the one entry shape that may claim operator authorship and calibration membership.
      assert.ok(inSet(CALIBRATION_LABELS, seed.value), "a calibration label value");
      assert.equal(seed.author_role, "operator", "a calibration label is operator-authored");
      assert.equal(seed.calibration_set, true, "a calibration label is in the set");
      assert.ok(Number.isInteger(seed.rubric_version), "a calibration label names its rubric");
    } else {
      assert.ok(typeof seed.value === "string" && seed.value.trim().length > 0, "text feedback is non-empty");
    }
    if (seed.name !== "calibration_label") {
      assert.equal(seed.rubric_version, null, "only a calibration label carries a rubric version");
      assert.notEqual(seed.author_role, "operator", "ordinary feedback is never operator-authored");
      assert.equal(seed.calibration_set, false, "ordinary feedback is not in the calibration set");
    }
    assert.ok(seed.comment === null || (typeof seed.comment === "string" && seed.comment.length > 0));
  }
  assert.ok(
    traces.seed_feedback.some((seed) => seed.channel === "api") &&
      traces.seed_feedback.some((seed) => seed.channel === "console"),
    "both feedback channels must be represented",
  );
  for (const name of FEEDBACK_ENTRY_NAMES) {
    assert.ok(
      traces.seed_feedback.some((seed) => seed.name === name),
      `V needs a seeded ${name} to render`,
    );
  }
  assert.ok(
    traces.seed_feedback.some((seed) => seed.author_role === "judge"),
    "a judge-authored signal must be renderable",
  );
});

test("the judge fixtures keep dry-run, live and ambiguous runs honest", () => {
  assert.ok(judge.runs.length >= 3);
  for (const run of judge.runs) {
    assert.match(run.id, UUID);
    assert.ok(inSet(JUDGE_RUN_STATES, run.state), `judge state ${run.state}`);
    assert.ok(inSet(JUDGE_MODES, run.mode), `judge mode ${run.mode}`);
    assert.ok(Number.isInteger(run.rubric_version) && run.rubric_version > 0, "rubric version");
    assert.match(run.consent_snapshot_at, RFC3339, "a run snapshots the consent it relied on");
    assert.ok(isMoney(run.budget_reserved), "budget_reserved");
    assert.ok(run.budget_settled === null || isMoney(run.budget_settled), "budget_settled");
    if (run.state === "ambiguous") {
      assert.equal(run.budget_settled, null, "an ambiguous run holds its reservation");
      assert.equal(run.external_batch_id, null, "an ambiguous run has no known provider id");
      assert.ok(run.quarantine_reason !== null, "an ambiguous run is quarantined with a reason");
    }
    if (run.mode === "dry_run") {
      assert.equal(run.external_batch_id, null, "a dry run never reaches a provider");
    }
    for (const sample of run.samples) {
      assert.match(sample.id, UUID);
      assert.ok(Number.isInteger(sample.trace_index) && sample.trace_index >= 0);
      if (sample.limited_evaluation) {
        assert.ok(sample.limited_reason !== null, "a limited evaluation says why");
        assert.equal(
          sample.scores.filter((score) => score.name === "groundedness").length,
          0,
          "no media means no groundedness score",
        );
      }
      for (const score of sample.scores) {
        assert.ok(inSet(JUDGE_SCORE_KINDS, score.kind), `score kind ${score.kind}`);
        assert.equal(score.estimated, run.mode === "dry_run", "estimates only come from dry runs");
      }
    }
  }
  assert.ok(judge.runs.some((run) => run.state === "ambiguous"), "an ambiguous run is required");
  assert.ok(judge.runs.some((run) => run.mode === "dry_run"), "a dry run is required");
  assert.ok(
    judge.runs.some((run) => run.samples.some((sample) => sample.limited_evaluation)),
    "a limited evaluation is required",
  );
});
