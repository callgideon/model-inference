// L4 journeys (journeys.ts) on FakeControl, the executable shape of L3 (WR-L4-1, E3L-F3). stack.test.ts
// runs the same bodies through the HTTP adapter on the real control service. The fake world is L3's:
// provider A's model with operator-imported weights, listed on its first public revision.
import assert from "node:assert/strict";
import test from "node:test";
import { FakeControl } from "../../../lib/services/control/fake.ts";
import { j01, j02, type JourneyWorld } from "./journeys.ts";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const WEIGHTS = [`sha256:${"a".repeat(64)}`, `sha256:${"c".repeat(64)}`];

function world(): JourneyWorld {
  const control = new FakeControl();
  const first = control.importModel(A, "acme/acme-7b", WEIGHTS);
  control.observe(A, { deploymentRevisionId: first.deploymentRevisionId, windowStart: "2026-09-27T09:00:00Z", windowEnd: "2026-09-27T10:00:00Z", requests: 5, errors: 0, p95LatencyMs: 90 });
  return {
    A, B, port: () => control,
    registration: { name: "acme-7b", artifactDigest: WEIGHTS[1], schemaVersion: "chat.v2", runtime: "vllm@sha256:" + "b".repeat(64) },
    approve: async (id) => control.decide(id, true),
    reject: async (id) => control.decide(id, false),
    rollback: async () => control.rollback(A, "acme/acme-7b"),
    discoverable: async () => {
      const live = control.discoverable("acme/acme-7b");
      assert.equal(live.length, 1, "exactly one public revision per model");
      return live[0];
    },
  };
}

test("L4-J01 register → dev smoke → publish proposal → operator approval → App discovery → operator rollback", () => j01(world()));

test("L4-J02 unauthorized variants: another provider, a viewer, a developer proposing, an unimported model or digest, publish before smoke, a provider rollback, a repeat", () => j02(world()));
