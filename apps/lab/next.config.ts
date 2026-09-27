import type { NextConfig } from "next";

// L1: provider data is private per session; nothing is cached or shared between users.
const nextConfig: NextConfig = {
  poweredByHeader: false,
  headers: async () => [{ source: "/:path*", headers: [{ key: "Cache-Control", value: "private, no-store" }] }],
};

export default nextConfig;
