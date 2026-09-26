/**
 * I2A: check the deployment environment once when a server starts. A refusal (lib/deploy/env.ts,
 * variable names only) stops the server from serving rather than failing on a user's request.
 * I3: every server error Next captures is logged as one sanitised line (lib/deploy/report.ts):
 * release, route pattern, digest and class name. The request's path, query and headers and the
 * error's message are never logged.
 */
import type { Instrumentation } from "next";
import { serverErrorLine } from "./lib/deploy/report.ts";

export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { assertDeployEnv } = await import("./lib/deploy/env.ts");
  assertDeployEnv(process.env);
}

export const onRequestError: Instrumentation.onRequestError = async (error, _request, context) => {
  const { runningRelease } = await import("./lib/deploy/release.ts");
  console.error(JSON.stringify(serverErrorLine(error, context.routePath, runningRelease())));
};
