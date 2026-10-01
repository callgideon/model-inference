import type { NextConfig } from "next";
import { resolve } from "node:path";

// L1: provider data is private per session; nothing is cached or shared between users.
const nextConfig: NextConfig = {
  poweredByHeader: false,
  turbopack: { root: resolve(import.meta.dirname, "../..") },
  headers: async () => [{ source: "/:path*", headers: [{ key: "Cache-Control", value: "private, no-store" }] }],
};

export default nextConfig;
