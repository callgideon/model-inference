// WR-E3L-J, real evidence: the journeys J01/J02 (journeys.ts, the same bodies journey.test.ts runs on the
// fake) through the Lab's HTTP control adapter against the Lab control service as R186's factory
// (infrx.lab.control.app:create_app) over the real L3 and PgControlStore on the task-local PostgreSQL
// (backend.py, key l4). Skipped unless LAB_L4_REAL=1 (needs Docker and the l4 key):
//   cd apps/lab && LAB_L4_REAL=1 INFRX_D_TASK=l4 node --test tests/l/ui/stack.test.ts
// Outside the mutant suite (like tests/r/stack.test.ts): its oracle is the real route, not Lab code.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import test from "node:test";
import { httpControl } from "../../../lib/services/control/http.ts";
import { j01, j02, type JourneyWorld, type Who } from "./journeys.ts";

const REAL = process.env.LAB_L4_REAL === "1";
const lab = resolve(import.meta.dirname, "../../..");
type World = { A: string; B: string; name: string; weights: string[]; tokens: Record<Who | "consumer", string> };

async function backend(): Promise<{ url: string; world: World; stop: () => void }> {
  const child = spawn("uv", ["run", "--frozen", "--project", "../infrx-api", "python", "tests/l/ui/backend.py"], { cwd: lab, stdio: ["ignore", "pipe", "inherit"] });
  const line = await new Promise<string>((done, failed) => {
    let out = "";
    child.stdout.on("data", (chunk) => {
      out += chunk;
      const ready = out.split("\n").find((l) => l.startsWith("READY "));
      if (ready) done(ready);
    });
    child.on("exit", (code) => failed(new Error(`backend exited ${code}: ${out}`)));
  });
  const [, port, world] = line.match(/^READY (\d+) (.*)$/)!;
  return { url: `http://127.0.0.1:${port}`, world: JSON.parse(world), stop: () => child.kill("SIGINT") };
}

test("L4-S01..S03 the journeys J01/J02 through the HTTP adapter on the real control service (L3 over PgControlStore)", { skip: !REAL && "LAB_L4_REAL=1 (Docker, l4)" }, async (t) => {
  const { url, world: w, stop } = await backend();
  t.after(stop);
  const door = async (method: string, path: string, body?: unknown) => {
    const r = await fetch(`${url}/_test/${path}`, { method, headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    assert.equal(r.status, 200, path);
    return r.json();
  };
  const real: JourneyWorld = {
    A: w.A, B: w.B,
    port: (who) => httpControl({ baseUrl: url, token: async () => w.tokens[who] }),
    registration: { name: w.name, artifactDigest: w.weights[0], schemaVersion: "chat.v1", runtime: "vllm/vllm-openai@sha256:" + "ab".repeat(32) },
    approve: async (proposalId) => void (await door("POST", "approve", { proposal_id: proposalId })),
    // no reject: L3 has no rejection of a proposal (E3L-F4), so J02 skips that step here
    rollback: async () => void (await door("POST", "rollback")),
    discoverable: async () => (await door("GET", "discoverable")).deployment_revision_id,
  };
  await t.test("S01 (J01) register → dev smoke → publish proposal → operator approval → App discovery → operator rollback", () => j01(real));
  await t.test("S02 (J02) the unauthorized variants", () => j02(real));
  await t.test("S03 a consumer-only session is refused by the control service itself", async () => {
    const consumer = httpControl({ baseUrl: url, token: async () => w.tokens.consumer });
    assert.deepEqual(await consumer.deployments({ providerId: w.A, role: "administrator" }), { ok: false, reason: "denied" });
  });
});
