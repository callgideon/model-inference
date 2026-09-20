import { cache } from "react";
import { redirect } from "next/navigation";
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
 * The signed-in user with their single organization (spec F2: one org per user).
 * Cached per request so the layout and the page share one round-trip.
 */
export const getSession = cache(async (): Promise<Session> => {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const [{ data: profile }, { data: membership }] = await Promise.all([
    supabase.from("profiles").select("email, is_operator").eq("id", user.id).single(),
    supabase
      .from("org_members")
      .select("org_id, role, organizations(name)")
      .eq("user_id", user.id)
      .limit(1)
      .maybeSingle(),
  ]);

  if (!membership) {
    // The auth.users trigger creates profile + org + membership; if it has not run yet
    // there is nothing to show, and nothing the console can do about it.
    throw new Error("No organization for this account yet — sign out and back in.");
  }

  const org = membership.organizations as unknown as { name: string } | null;
  return {
    userId: user.id,
    email: profile?.email ?? user.email ?? "",
    isOperator: profile?.is_operator ?? false,
    orgId: membership.org_id as string,
    orgName: org?.name ?? "Personal",
    role: membership.role as "owner" | "member",
  };
});

export async function getBalance(orgId: string): Promise<number> {
  const supabase = await createClient();
  const { data } = await supabase.rpc("org_balance", { p_org: orgId });
  return Number(data ?? 0);
}
