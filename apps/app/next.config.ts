import { realpathSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import type { NextConfig } from "next";
import { cacheHeaders } from "./lib/deploy/cache.ts";

// AP-09: the generated API client is a linked package (`link:../../packages/api-client`) and
// Turbopack refuses files outside its root, so the root is the directory holding both.
const app = import.meta.dirname;
const client = realpathSync(resolve(app, "node_modules/@infrx/api-client"));
let root = app;
while (relative(root, client).startsWith("..")) root = dirname(root);

const nextConfig: NextConfig = {
  // I2A release identity, fixed at build time (Next inlines these where read textually). Each commit
  // source is baked as itself; releaseIdentity (lib/deploy/env.ts) prefers the host's and refuses a
  // disagreement.
  env: {
    INFRX_RELEASE_SHA: process.env.INFRX_RELEASE_SHA || "",
    VERCEL_GIT_COMMIT_SHA: process.env.VERCEL_GIT_COMMIT_SHA || "",
    INFRX_BUILT_AT: new Date().toISOString(),
  },
  // I2A: private, no-store by default (lib/deploy/cache.ts).
  headers: async () => cacheHeaders(),
  turbopack: { root },
  outputFileTracingRoot: root,
};

export default nextConfig;
