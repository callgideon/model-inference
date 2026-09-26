import { NextResponse } from "next/server";
import { PRIVATE_HEADERS } from "@/lib/deploy/cache";
import { releaseIdentity } from "@/lib/deploy/env";

/** I2A: what is running — commit, build time, deployment, environment and the API origin the docs use. */
export function GET() {
  const identity = releaseIdentity({
    ...process.env,
    INFRX_RELEASE_SHA: process.env.INFRX_RELEASE_SHA,
    VERCEL_GIT_COMMIT_SHA: process.env.VERCEL_GIT_COMMIT_SHA,
    INFRX_BUILT_AT: process.env.INFRX_BUILT_AT,
  });
  return NextResponse.json(identity, { headers: PRIVATE_HEADERS });
}
