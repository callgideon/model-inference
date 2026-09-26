/**
 * I3: the running release for error lines — commit, deployment, environment — from I2A's
 * `releaseIdentity`, with the build-time inputs read textually so Next inlines them (the same
 * reads as app/api/version/route.ts). Server-only: it imports env.ts.
 */
import type { Release } from "./report.ts";
import { releaseIdentity } from "./env.ts";

export function runningRelease(): Release {
  const { commit, deployment, environment } = releaseIdentity({
    ...process.env,
    INFRX_RELEASE_SHA: process.env.INFRX_RELEASE_SHA,
    VERCEL_GIT_COMMIT_SHA: process.env.VERCEL_GIT_COMMIT_SHA,
    INFRX_BUILT_AT: process.env.INFRX_BUILT_AT,
  });
  return { commit, deployment, environment };
}
