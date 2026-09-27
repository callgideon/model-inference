// L1 data-access guard. A layout does not stop its pages or actions from rendering (Next's Partial
// Rendering), so every page, route and server action calls one of these itself (tests/l/shell).
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { notFound } from "next/navigation";
import { cache } from "react";
import { readyWorkspace, sessionAccess, type Membership, type SessionAccess } from "./access.ts";
import { accessFromRequest } from "./request.ts";

export const providerAccessForRequest = cache(async () =>
  accessFromRequest(process.env, await cookies(), (url, key, options) => {
    const client = createServerClient(url, key, options);
    return { auth: client.auth, rpc: (name) => client.rpc(name) };
  }),
);

/** Signed in with at least one provider membership (selected or not); anything else is a 404. */
export async function requireProviderSession(): Promise<SessionAccess> {
  const access = sessionAccess(await providerAccessForRequest());
  if (access === null) notFound();
  return access;
}

/** The selected provider workspace; anything else is a 404. */
export async function requireProviderWorkspace(): Promise<Membership> {
  const workspace = readyWorkspace(await providerAccessForRequest());
  if (workspace === null) notFound();
  return workspace;
}
