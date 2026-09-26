/**
 * I3: browser error reports. POST a small JSON `{digest, route, message}`; the route logs one
 * redacted line with the release identity (lib/deploy/report.ts) and answers 204 with no body.
 * It never echoes what it was sent: every refusal is a bare status. GET and other methods get
 * Next's 405 (only POST is exported).
 */
import { PRIVATE_HEADERS } from "../../../lib/deploy/cache.ts";
import { runningRelease } from "../../../lib/deploy/release.ts";
import { REPORT_MAX_BYTES, limiter, parseClientReport } from "../../../lib/deploy/report.ts";

const admit = limiter(60, 60_000);
const empty = (status: number) => new Response(null, { status, headers: PRIVATE_HEADERS });

export async function POST(request: Request): Promise<Response> {
  const origin = request.headers.get("origin");
  if (origin !== null && origin !== new URL(request.url).origin) return empty(403);
  if (Number(request.headers.get("content-length") ?? 0) > REPORT_MAX_BYTES) return empty(413);
  if (!admit(Date.now())) return empty(429);
  const parsed = parseClientReport(request.headers.get("content-type"), await request.text(), runningRelease());
  if (parsed.status !== 204) return empty(parsed.status);
  console.error(JSON.stringify(parsed.line));
  return empty(204);
}
