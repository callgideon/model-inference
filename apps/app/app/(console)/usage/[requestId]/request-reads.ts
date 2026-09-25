/**
 * The owned request and its result (U4), over the signed-in user's own Supabase session (anon key +
 * user JWT, RLS on) — D10's consumer read surface (0021), never a customer API key:
 *
 * - `consumer_jobs(p_request_id)`: the request, only if it is the caller's (auth.uid() -> their
 *   consumer wallet -> its personal organization). Parsed by U1R's own reader, unchanged.
 * - `consumer_job_result(p_request_id)`: the content, refused typed by the persisted expiry.
 *
 * The content read is made only when the job read says the API would serve it (F2C.b
 * `read_outcome` = available): `consumer_job_result` alone would also hand back the body of a
 * success whose usage is unknown, which the API withholds (wiring request WR-U4-2).
 *
 * A malformed id never reaches the database; a foreign id and an unknown one read the same. Nothing
 * here logs, and nothing here throws.
 *
 * Pure `.ts` with relative imports (R48): the client is injected, so `node --test` loads this.
 */

import type { Result } from "../../../../lib/contracts/types.ts";
import { defaultCreditFixture } from "../../billing/credit-fixture.ts";
import { postgrestCreditReads, type Answer, type ConsumerJob, type CreditClient } from "../../billing/credit-reads.ts";
import { previewAllowed } from "../fake-console-context.ts";
import { RESULT_STATUS, resultAccessOf, type ResultRead } from "./request-view-model.ts";

export type { ResultRead } from "./request-view-model.ts";

export interface RequestReads {
  /** The caller's own request, or `null` (not theirs, not there, or not an id). */
  job(requestId: string): Promise<Result<ConsumerJob | null>>;
  result(requestId: string): Promise<ResultRead>;
}

const REQUEST_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** The canonical (lowercase) id, or null for anything that is not exactly a UUID. */
function requestIdOf(value: string): string | null {
  const id = value.toLowerCase();
  return REQUEST_ID.test(id) ? id : null;
}

/** What a result gate that is not `available` tells the browser. */
const WITHHELD: Record<string, ResultRead> = {
  pending: { state: "pending" },
  held_unknown: { state: "withheld" },
  no_result: { state: "no_result" },
  unavailable: { state: "unavailable" },
  expired: { state: "expired" },
};

/** A refusal of `consumer_job_result` (0021 -> 0020 `read_result`, `infrx.refuse` = P0001). */
function refusal(error: NonNullable<Answer["error"]>): ResultRead {
  const message = error.message ?? "";
  // 42501: no JWT subject; PGRST30x: PostgREST refused the JWT (expired, malformed, claims).
  if (error.code === "42501" || /^PGRST30\d$/.test(error.code ?? "")) return { state: "signed_out" };
  if (error.code === "P0001") {
    if (message.startsWith("not_found:")) return { state: "not_found" };
    if (message.startsWith("result_pending:")) return { state: "pending" };
    if (message.startsWith("result_expired:")) return { state: "expired" };
  }
  return { state: "unavailable" };
}

export function postgrestRequestReads(client: CreditClient, userId: string): RequestReads {
  async function job(requestId: string): Promise<Result<ConsumerJob | null>> {
    const id = requestIdOf(requestId);
    if (id === null) return { ok: true, value: null };
    // U1R's jobs read, narrowed to this one request: the same RPC, parser and error mapping.
    const one: CreditClient = {
      from: (relation) => client.from(relation),
      rpc: (fn, args) => client.rpc(fn, { ...args, p_request_id: id }),
    };
    const page = await postgrestCreditReads(one, userId).jobs({ limit: 1, cursor: null });
    if (!page.ok) return page;
    const found = page.value.items[0];
    return { ok: true, value: found !== undefined && found.requestId === id ? found : null };
  }

  return {
    job,
    async result(requestId) {
      const id = requestIdOf(requestId);
      if (id === null) return { state: "not_found" };
      const read = await job(id);
      if (!read.ok) return { state: "unavailable" };
      if (read.value === null) return { state: "not_found" };
      const access = resultAccessOf(read.value);
      if (access !== "available") return WITHHELD[access];
      let answer: Answer;
      try {
        answer = await client.rpc("consumer_job_result", { p_request_id: id });
      } catch {
        return { state: "unavailable" };
      }
      if (answer.error !== null && answer.error !== undefined) return refusal(answer.error);
      return typeof answer.data === "string" ? { state: "ready", text: answer.data } : { state: "unavailable" };
    },
  };
}

/**
 * The result route's answer: JSON, private and never stored by a browser, proxy or CDN, so an
 * expired result cannot come back from a cache. Only a ready answer carries content.
 */
export function resultResponse(read: ResultRead): Response {
  return Response.json(read, {
    status: RESULT_STATUS[read.state],
    headers: { "Cache-Control": "private, no-store, max-age=0" },
  });
}

// ---------------------------------------------------------------------------
// The development preview (never a production build: `previewAllowed`)
// ---------------------------------------------------------------------------

export function fixtureRequestReads(jobs: ConsumerJob[] = defaultCreditFixture().jobs): RequestReads {
  const find = (requestId: string) => jobs.find((j) => j.requestId === requestIdOf(requestId)) ?? null;
  return {
    job: async (requestId) => ({ ok: true, value: find(requestId) }),
    async result(requestId) {
      const found = find(requestId);
      if (found === null) return { state: "not_found" };
      const access = resultAccessOf(found);
      return access === "available"
        ? { state: "ready", text: `Demo result for request ${found.requestId}. This is an example, not your output.` }
        : WITHHELD[access];
    },
  };
}

export type RequestSource = { reads: RequestReads; preview: boolean };

/** The one place the detail's data source is chosen (same gate as U1R's `creditSource`); null = signed out. */
export async function requestSource(
  real: () => Promise<RequestSource | null>,
  env: { NODE_ENV?: string; INFRX_CONSOLE_PREVIEW?: string } = process.env,
): Promise<RequestSource | null> {
  if (previewAllowed(env)) return { reads: fixtureRequestReads(), preview: true };
  return real();
}
