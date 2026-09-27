import type { NextRequest } from "next/server";
import { authCallback } from "@/lib/auth/routes";

// WR-L1-6: public by design (tests/l/shell PUBLIC): email links land here before any session exists.
export function GET(request: NextRequest) {
  return authCallback(request);
}
