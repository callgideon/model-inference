import { createServerClient } from "@supabase/ssr";
// `.js`: the package has no exports map, and `node --test` loads this file (tests/a/middleware.test.ts).
import { NextResponse, type NextRequest } from "next/server.js";

const PUBLIC = ["/api/version", "/api/client-errors", "/login", "/signup", "/verify-email", "/forgot-password", "/auth"];

/** Refreshes the Supabase session cookie and guards the console routes. */
export async function updateSession(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (list) => {
          for (const { name, value } of list) request.cookies.set(name, value);
          response = NextResponse.next({ request });
          for (const { name, value, options } of list) response.cookies.set(name, value, options);
        },
      },
    },
  );

  // getUser() revalidates the token with Supabase; do not replace it with getSession().
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const to = redirectFor(request, user !== null);
  return to === null ? response : NextResponse.redirect(to);
}

/** The guard's decision: where to send this request, or `null` to serve it. */
export function redirectFor(request: NextRequest, signedIn: boolean) {
  const path = request.nextUrl.pathname;
  const isPublic = PUBLIC.some((p) => path === p || path.startsWith(p + "/"));

  if (!signedIn && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    url.searchParams.set("next", path + request.nextUrl.search);
    return url;
  }
  // Only a navigation is sent away: the login form's server action (claimOnboarding) POSTs to /login
  // right after sign-in set the cookie, and redirecting it dropped the first-login claim (E3A F-1).
  if (signedIn && (path === "/login" || path === "/signup") && (request.method === "GET" || request.method === "HEAD")) {
    const url = request.nextUrl.clone();
    url.pathname = "/models";
    url.search = "";
    return url;
  }
  return null;
}
