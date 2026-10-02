/**
 * The owned request and its result (U4), over infrx-api (AP-09 09a) as the signed-in user - never
 * a customer API key:
 *
 * - `GET /console/v1/requests/{id}`: the request, only if it is the caller's (a foreign id and an
 *   unknown one are the same 404). Parsed by U1R's own reader (`jobOf`), unchanged.
 * - `GET /console/v1/requests/{id}/result`: the content, refused typed by the persisted expiry.
 *
 * The content read is made only when the request says the API would serve it (F2C.b
 * `read_outcome` = available); the API refuses the body of a success whose usage is unknown too
 * (U4 WR-U4-2), so the two agree.
 *
 * A malformed id never reaches the API. Nothing here logs, and nothing here throws.
 *
 * Pure `.ts` with relative imports (R48): the client is injected, so `node --test` loads this.
 */

import type { ConsumerApi } from "../../../../lib/api/index.ts";
import { read } from "../../../../lib/api/result.ts";
import type { Result } from "../../../../lib/contracts/types.ts";
import { jobOf, type ConsumerJob } from "../../billing/credit-reads.ts";
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

/** The API's refusal of the content read, as the browser's state. */
const REFUSED: Record<string, ResultRead> = {
  not_found: { state: "not_found" },
  result_pending: { state: "pending" },
  result_expired: { state: "expired" },
};

export function apiRequestReads(api: ConsumerApi): RequestReads {
  async function job(requestId: string): Promise<Result<ConsumerJob | null>> {
    const id = requestIdOf(requestId);
    if (id === null) return { ok: true, value: null };
    const found = await read(api.call("get", "/console/v1/requests/{request_id}", { params: { request_id: id } }), jobOf);
    if (!found.ok) return found.error.code === "not_found" ? { ok: true, value: null } : found;
    return { ok: true, value: found.value.requestId === id ? found.value : null };
  }

  return {
    job,
    async result(requestId) {
      const id = requestIdOf(requestId);
      if (id === null) return { state: "not_found" };
      const found = await job(id);
      // A session the API refuses (401) reads as `forbidden`: the reader signs in again.
      if (!found.ok) return { state: found.error.code === "forbidden" ? "signed_out" : "unavailable" };
      if (found.value === null) return { state: "not_found" };
      const access = resultAccessOf(found.value);
      if (access !== "available") return WITHHELD[access];
      let answer;
      try {
        answer = await api.call("get", "/console/v1/requests/{request_id}/result", { params: { request_id: id } });
      } catch {
        return { state: "unavailable" };
      }
      if (answer.ok) return typeof answer.data?.text === "string" ? { state: "ready", text: answer.data.text } : { state: "unavailable" };
      const { error } = answer;
      if (error.status === 401) return { state: "signed_out" };
      const code = error.kind === "error" ? error.code : null;
      return (code !== null && REFUSED[code]) || { state: "unavailable" };
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

export type RequestSource = { reads: RequestReads; preview: boolean };
