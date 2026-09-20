import { NextResponse, type NextRequest } from "next/server";
import type { EmailOtpType } from "@supabase/supabase-js";
import { createClient } from "@/lib/supabase/server";
import { safeNext } from "@/lib/utils";

const loginWithError = (origin: string, message: string) =>
  NextResponse.redirect(`${origin}/login?error=${encodeURIComponent(message)}`);

/**
 * Supabase sends the browser here with either ?code=… (PKCE) or
 * ?token_hash=…&type=recovery (the plain email-link form), then we forward to ?next=.
 */
export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const code = searchParams.get("code");
  const tokenHash = searchParams.get("token_hash");
  const type = searchParams.get("type") as EmailOtpType | null;
  const target = safeNext(searchParams.get("next"));

  // Supabase reports a failed round-trip as ?error=…&error_description=…
  const linkError = searchParams.get("error");
  if (linkError) {
    return loginWithError(origin, searchParams.get("error_description") ?? linkError);
  }
  if (!code && !(tokenHash && type)) return loginWithError(origin, "missing code");

  const supabase = await createClient();
  const { error } = code
    ? await supabase.auth.exchangeCodeForSession(code)
    : await supabase.auth.verifyOtp({ token_hash: tokenHash!, type: type! });
  if (error) return loginWithError(origin, error.message);

  return NextResponse.redirect(`${origin}${target}`);
}
