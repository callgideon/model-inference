import { cache } from "react";
import { redirect } from "next/navigation";
import { onceGetUser, personalOrg } from "@/lib/services/console";
import { createClient } from "@/lib/supabase/server";

export type Session = {
  userId: string;
  email: string;
  isOperator: boolean;
  orgId: string;
  orgName: string;
  role: "owner" | "member";
};

/**
 * The request's one Supabase server client: its `getUser()` reaches GoTrue once per request
 * (U1R WR-6), whether the layout's session, operator flag or credit read or the page asks.
 */
export const requestClient = cache(async () => onceGetUser(await createClient()));

/**
 * The signed-in user and their personal organization (C0 WR-6, R66): the one they created and own,
 * never the first of their memberships. Cached per request so the layout and the page share it.
 */
export const getSession = cache(async (): Promise<Session> => {
  const supabase = await requestClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const [{ data: profile }, org] = await Promise.all([
    supabase.from("profiles").select("email, is_operator").eq("id", user.id).single(),
    // The cookie client implements the slice `personalOrg` declares.
    personalOrg(supabase as unknown as Parameters<typeof personalOrg>[0], user.id),
  ]);

  if (!org) {
    // The auth.users trigger creates profile + personal org + owner membership; a user without
    // exactly one is not an account to guess at.
    throw new Error("No personal organization for this account yet — sign out and back in.");
  }

  return {
    userId: user.id,
    email: profile?.email ?? user.email ?? "",
    isOperator: profile?.is_operator ?? false,
    orgId: org.orgId,
    orgName: org.orgName,
    role: "owner",
  };
});
