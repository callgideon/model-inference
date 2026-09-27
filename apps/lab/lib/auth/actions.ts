"use server";
// L1 workspace selection. The cookie is a preference: it stores a membership this action validated,
// and every request re-checks it against the membership read (access.ts).
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { chooseWorkspace } from "./access.ts";
import { WORKSPACE_COOKIE, labConfig, workspaceCookieOptions } from "./config.ts";
import { requireProviderSession } from "./guard.ts";

export async function selectWorkspace(formData: FormData): Promise<void> {
  const access = await requireProviderSession();
  const chosen = chooseWorkspace(access, formData.get("providerId"));
  const config = labConfig(process.env);
  if (chosen === null || config === null) notFound();
  (await cookies()).set(WORKSPACE_COOKIE, chosen.providerId, workspaceCookieOptions(config));
  redirect("/");
}
