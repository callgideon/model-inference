/**
 * I3: browser error reports. POST a small JSON `{digest, route, name}`; the route logs one
 * sanitised line with the release identity (lib/deploy/report.ts) and answers 204 with no body.
 * It never echoes what it was sent: every refusal is a bare status. GET and other methods get
 * Next's 405 (only POST is exported).
 */
import { PRIVATE_HEADERS } from "../../../lib/deploy/cache.ts";
import { runningRelease } from "../../../lib/deploy/release.ts";
import { REPORT_MAX_BYTES, limiter, parseClientReport } from "../../../lib/deploy/report.ts";

const admit = limiter(60, 60_000);
const empty = (status: number) => new Response(null, { status, headers: PRIVATE_HEADERS });

/** The body as text, or null once it passes `max` bytes: the rest is never read. */
async function readCapped(body: ReadableStream<Uint8Array> | null, max: number): Promise<string | null> {
  if (body === null) return "";
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let text = "";
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return text + decoder.decode();
    size += value.byteLength;
    if (size > max) {
      await reader.cancel();
      return null;
    }
    text += decoder.decode(value, { stream: true });
  }
}

export async function POST(request: Request): Promise<Response> {
  // The browser's own verdict, whatever host the server sees itself as. A browser without
  // Sec-Fetch-Site cannot POST application/json cross-origin without a CORS preflight, which
  // this route never grants (no OPTIONS export, no Access-Control-* header).
  const site = request.headers.get("sec-fetch-site");
  if (site !== null && site !== "same-origin") return empty(403);
  if (Number(request.headers.get("content-length") ?? 0) > REPORT_MAX_BYTES) return empty(413);
  if (!admit(Date.now())) return empty(429);
  const body = await readCapped(request.body, REPORT_MAX_BYTES);
  if (body === null) return empty(413);
  const parsed = parseClientReport(request.headers.get("content-type"), body, runningRelease());
  if (parsed.status !== 204) return empty(parsed.status);
  console.error(JSON.stringify(parsed.line));
  return empty(204);
}
