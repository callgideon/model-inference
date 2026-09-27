import type { NextRequest } from "next/server";
import { refreshSession } from "@/lib/auth/routes";

// WR-L1-6: keep the Lab session cookie fresh on every request; access is decided by the guard.
export function proxy(request: NextRequest) {
  return refreshSession(request);
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
