// Oracle (AP-00 00c): the Lab port speaks the Lab client - a control listing is typed down to its
// records and a refusal is the `{refusal}` kind - and a consumer path does not type-check.
import assert from "node:assert/strict";
import { test } from "node:test";
import { labApi } from "./index.ts";

const answering = (status: number, body: unknown) => (async () =>
  new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

test("the Lab port types a control listing and maps a refusal", async () => {
  const ok = await labApi({ baseUrl: "http://api.test", fetch: answering(200, { data: [] }) })
    .call("get", "/lab/v1/control/models", { query: { provider_org_id: "p" } });
  assert.ok(ok.ok);
  const ids: string[] = ok.ok ? ok.data.data.map((m) => m.model_id) : [];
  assert.deepEqual(ids, []);
  const refused = await labApi({ baseUrl: "http://api.test", fetch: answering(403, { refusal: "denied" }) })
    .call("get", "/lab/v1/control/models");
  assert.deepEqual(!refused.ok && refused.error, { kind: "refusal", status: 403, reason: "denied" });
  if (false as boolean) {
    // @ts-expect-error the Lab's client has no consumer path
    await labApi({ baseUrl: "" }).call("post", "/v1/jobs");
  }
});
