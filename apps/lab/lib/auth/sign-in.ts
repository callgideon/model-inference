"use server";
// WR-L1-6 + AP-09 09c: signing in and out of the Lab, through the auth facade (/auth/v1/*). Public by
// design (tests/l/shell PUBLIC): neither reads provider data, and the session sign-in creates is
// checked by the guard on every request.
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { labApi } from "../api/index.ts";
import { WORKSPACE_COOKIE, labConfig } from "./config.ts";
import { clearSession, isTokens, sessionToken, signInFailure, storeSession } from "./session.ts";

export type SignInState = { error: "failed" | "unavailable" } | null;

export async function signIn(_state: SignInState, formData: FormData): Promise<SignInState> {
  const config = labConfig(process.env);
  if (config === null) return { error: "unavailable" };
  const email = formData.get("email");
  const password = formData.get("password");
  if (typeof email !== "string" || typeof password !== "string") return { error: "failed" };
  const answer = await labApi({ baseUrl: config.apiUrl }).call("post", "/auth/v1/sign-in", { body: { email, password } });
  if (!answer.ok) return { error: signInFailure(answer.error) };
  if (!isTokens(answer.data)) return { error: "unavailable" };
  storeSession(await cookies(), config, answer.data);
  redirect("/");
}

export async function signOut(): Promise<void> {
  const config = labConfig(process.env);
  const token = await sessionToken();
  if (config !== null && token !== null) {
    await labApi({ baseUrl: config.apiUrl, session: () => ({ token }) }).call("post", "/auth/v1/sign-out");
  }
  const jar = await cookies();
  clearSession(jar);
  jar.delete(WORKSPACE_COOKIE);
  redirect("/");
}
