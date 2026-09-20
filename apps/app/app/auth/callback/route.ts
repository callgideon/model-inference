import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";

const loginWithError = (origin: string, message: string) =>
  NextResponse.redirect(`${origin}/login?error=${encodeURIComponent(message)}`);

/** Supabase redirects here with ?code=… after the Google OAuth round-trip. */
export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const code = searchParams.get("code");
  const next = searchParams.get("next") ?? "/models";
  // Only same-site paths: an attacker-supplied ?next=https://… must not be followed.
  const target = next.startsWith("/") && !next.startsWith("//") ? next : "/models";

  // Supabase reports a failed provider round-trip as ?error=…&error_description=…
  const providerError = searchParams.get("error");
  if (providerError) {
    return loginWithError(origin, searchParams.get("error_description") ?? providerError);
  }

  if (!code) return loginWithError(origin, "missing code");

  const supabase = await createClient();
  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) return loginWithError(origin, error.message);

  return NextResponse.redirect(`${origin}${target}`);
}
