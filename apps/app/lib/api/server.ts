/**
 * AP-09: the request's infrx-api client, server-side only. The App's one way to read or change
 * product state: the signed-in user's session cookie becomes the Bearer the API verifies
 * (SessionActors); there is no service credential, DSN or fallback path. A missing or invalid
 * INFRX_API_BASE_URL leaves the client without an origin, so every call is `unavailable` - never a
 * fixture, never a zero. The development preview (INFRX_CONSOLE_PREVIEW) swaps in the client fake.
 */
import { cache } from "react";
import { cookies } from "next/headers";
import { apiBaseUrl } from "@/app/(console)/models/catalog";
import { consoleContext } from "@/app/(console)/usage/fake-console-context";
import { decodeSession, SESSION_COOKIE, tokenEmail } from "./cookie";
import { consumerApi, type ConsumerApi } from "./index";
import { onceGets } from "./result";

if (typeof window !== "undefined") throw new Error("lib/api/server.ts is server-only");

export type ApiSource = { api: ConsumerApi; preview: boolean; now: Date };

/** The API origin; "" when unconfigured, which the transport answers as `unavailable`. */
export const apiOrigin = () => apiBaseUrl(process.env) ?? "";

export const apiSource = cache(async (): Promise<ApiSource> => {
  const preview = consoleContext();
  if (preview !== null) return { ...preview, preview: true };
  const session = decodeSession((await cookies()).get(SESSION_COOKIE)?.value);
  return {
    api: consumerApi({ baseUrl: apiOrigin(), fetch: onceGets(fetch), session: () => ({ token: session?.access ?? null }) }),
    preview: false,
    now: new Date(),
  };
});

/** The display email of the signed-in session (the preview's fixture account in a preview). */
export async function sessionEmail(): Promise<string> {
  if (consoleContext() !== null) return "preview@example.com";
  const access = await accessToken();
  return access === null ? "" : (tokenEmail(access) ?? "");
}

/** The signed-in session's access token (for the auth facade's bearer-only routes), or null. */
export async function accessToken(): Promise<string | null> {
  return decodeSession((await cookies()).get(SESSION_COOKIE)?.value)?.access ?? null;
}
