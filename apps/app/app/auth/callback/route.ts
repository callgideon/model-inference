import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { completeCallback } from "@/app/(auth)/flow";
import { claimSignupGrant } from "@/app/(auth)/grant";

/**
 * Supabase sends the browser here from an email link, with ?code=… (PKCE) or
 * ?token_hash=…&type=… . `completeCallback` (app/(auth)/flow.ts, tests/a) verifies it, claims the
 * one-time grant for the verified user, and picks a same-site path; failures become fixed codes.
 */
export async function GET(request: NextRequest) {
  const supabase = await createClient();
  const target = await completeCallback(request.nextUrl.searchParams, {
    exchangeCode: (code) => supabase.auth.exchangeCodeForSession(code),
    verifyOtp: (tokenHash, type) => supabase.auth.verifyOtp({ token_hash: tokenHash, type }),
    verifiedUserId: async () => (await supabase.auth.getUser()).data.user?.id ?? null,
    claim: claimSignupGrant,
  });
  return NextResponse.redirect(new URL(target, request.nextUrl.origin));
}
