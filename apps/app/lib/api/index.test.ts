// Oracle (AP-00 00c): the App port speaks the consumer client - a consumer path is typed and its
// session is forwarded - and a Lab path does not type-check (`make console-typecheck`).
import assert from "node:assert/strict";
import { test } from "node:test";
import { consumerApi } from "./index.ts";

test("the App port calls a consumer operation with the session it is given", async () => {
  const seen: RequestInit[] = [];
  const api = consumerApi({ baseUrl: "http://api.test", session: () => ({ token: "eyJ0.a.b" }),
    fetch: (async (_url: string, init: RequestInit) => {
      seen.push(init);
      return new Response('{"object":"list","data":[]}', { status: 200 });
    }) as unknown as typeof fetch });
  const answer = await api.call("get", "/v1/models");
  assert.ok(answer.ok);
  assert.equal((seen[0].headers as Record<string, string>).authorization, "Bearer eyJ0.a.b");
  if (false as boolean) {
    // @ts-expect-error the App's client has no Lab path
    await api.call("get", "/lab/v1/control/models");
  }
});
