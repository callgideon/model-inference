"use server";
// WR-L1-6: signing in and out of the Lab. Public by design (tests/l/shell PUBLIC): neither reads
// provider data, and the session sign-in creates is checked by the guard on every request.
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { WORKSPACE_COOKIE, labConfig } from "./config.ts";
import { clientOptions } from "./request.ts";

export type SignInState = { error: "failed" | "unavailable" } | null;

async function labClient() {
  const config = labConfig(process.env);
  return config === null ? null : createServerClient(config.supabaseUrl, config.anonKey, clientOptions(config, await cookies()));
}

export async function signIn(_state: SignInState, formData: FormData): Promise<SignInState> {
  const client = await labClient();
  if (client === null) return { error: "unavailable" };
  const email = formData.get("email");
  const password = formData.get("password");
  if (typeof email !== "string" || typeof password !== "string") return { error: "failed" };
  const { error } = await client.auth.signInWithPassword({ email, password }).catch(() => ({ error: true }));
  if (error) return { error: "failed" };
  redirect("/");
}

export async function signOut(): Promise<void> {
  const client = await labClient();
  await client?.auth.signOut().catch(() => undefined);
  (await cookies()).delete(WORKSPACE_COOKIE);
  redirect("/");
}
