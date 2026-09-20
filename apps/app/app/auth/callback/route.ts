import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";

/** Supabase redirects here with ?code=… after a magic link or an OAuth round-trip. */
export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const code = searchParams.get("code");
  const next = searchParams.get("next") ?? "/models";
  // Only same-site paths: an attacker-supplied ?next=https://… must not be followed.
  const target = next.startsWith("/") && !next.startsWith("//") ? next : "/models";

  if (!code) {
    return NextResponse.redirect(`${origin}/login?error=${encodeURIComponent("missing code")}`);
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) {
    return NextResponse.redirect(`${origin}/login?error=${encodeURIComponent(error.message)}`);
  }
  return NextResponse.redirect(`${origin}${target}`);
}
