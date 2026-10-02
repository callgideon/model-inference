import { cache } from "react";
import { redirect } from "next/navigation";
import { apiSource, sessionEmail } from "@/lib/api/server";

export type Session = {
  userId: string;
  email: string;
  isOperator: boolean;
  orgId: string;
};

/**
 * The signed-in user as infrx-api established it (`GET /console/v1/me`): the operator flag and the
 * personal organization are the server's. Cached per request so the layout and the page share it.
 * A session the API refuses goes to `/auth/expired` (clears the cookie); an outage is an error, not
 * a sign-out.
 */
export const getSession = cache(async (): Promise<Session> => {
  const [{ api }, email] = await Promise.all([apiSource(), sessionEmail()]);
  const me = await api.call("get", "/console/v1/me");
  if (!me.ok) {
    if (me.error.status === 401) redirect("/auth/expired");
    throw new Error("Your account could not be read right now. Reload to try again.");
  }
  const { actor } = me.data;
  return { userId: actor.user_id ?? "", email, isOperator: actor.operator === true, orgId: actor.org_id ?? "" };
});
