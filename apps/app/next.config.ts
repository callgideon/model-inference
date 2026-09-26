import type { NextConfig } from "next";
import { cacheHeaders } from "./lib/deploy/cache.ts";

const nextConfig: NextConfig = {
  // I2A release identity, fixed at build time (Next inlines these where read textually).
  env: {
    INFRX_RELEASE_SHA: process.env.INFRX_RELEASE_SHA || process.env.VERCEL_GIT_COMMIT_SHA || "",
    INFRX_BUILT_AT: new Date().toISOString(),
  },
  // I2A: private, no-store by default (lib/deploy/cache.ts).
  headers: async () => cacheHeaders(),
};

export default nextConfig;
