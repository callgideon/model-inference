// WR-L1-6: the two request-level entry points (wired by app/auth/callback/route.ts and proxy.ts).
// Both use the Lab's own session client over the request's cookies and write refreshed cookies onto
// the response; neither decides access (the guard does, per page and action).
import { createServerClient } from "@supabase/ssr";
// `.js`: next has no exports map and node --test loads this file (tests/l/shell/session.test.ts).
import { NextResponse, type NextRequest } from "next/server.js";
import { authCookieOptions, labConfig, type LabConfig } from "./config.ts";

function requestClient(config: LabConfig, request: NextRequest, onSet: (list: { name: string; value: string; options?: object }[]) => void) {
  return createServerClient(config.supabaseUrl, config.anonKey, {
    cookieOptions: authCookieOptions(config),
    cookies: { getAll: () => request.cookies.getAll(), setAll: onSet },
  });
}

/** Email links land here. Only sign-in links verify (signup and recovery are the App's); always home. */
export async function authCallback(request: NextRequest): Promise<NextResponse> {
  const config = labConfig(process.env);
  const response = NextResponse.redirect(new URL("/", config?.origin ?? request.nextUrl.origin));
  if (config === null) return response;
  const auth = requestClient(config, request, (list) => {
    for (const { name, value, options } of list) response.cookies.set(name, value, options);
  }).auth;
  const params = request.nextUrl.searchParams;
  const code = params.get("code");
  const tokenHash = params.get("token_hash");
  const type = params.get("type");
  if (code) await auth.exchangeCodeForSession(code).catch(() => undefined);
  else if (tokenHash && (type === "magiclink" || type === "email")) await auth.verifyOtp({ token_hash: tokenHash, type }).catch(() => undefined);
  return response;
}

/** The proxy: refresh the Lab session cookie (getUser revalidates it); never redirect. */
export async function refreshSession(request: NextRequest): Promise<NextResponse> {
  let response = NextResponse.next({ request });
  const config = labConfig(process.env);
  if (config === null) return response;
  const client = requestClient(config, request, (list) => {
    for (const { name, value } of list) request.cookies.set(name, value);
    response = NextResponse.next({ request });
    for (const { name, value, options } of list) response.cookies.set(name, value, options);
  });
  await client.auth.getUser().catch(() => undefined);
  return response;
}
