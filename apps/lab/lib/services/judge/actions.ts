"use server";
// C3L server actions: each one calls the provider guard itself (tests/l/shell boundary), then the
// pure core with the user's own session client. The Lab holds no service-role key.
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { requireProviderWorkspace } from "../../auth/guard.ts";
import { configure, listCalibration, requestRun, sessionRpc, setBudget, type Outcome, type Rpc } from "./core.ts";

async function session(): Promise<Rpc> {
  return sessionRpc(process.env, await cookies(), (url, key, options) => {
    const client = createServerClient(url, key, options);
    return { rpc: (name, args) => client.rpc(name, args) };
  });
}

export async function configureJudge(formData: FormData): Promise<Outcome> {
  const workspace = await requireProviderWorkspace();
  return configure(await session(), workspace, Object.fromEntries(formData));
}

export async function setJudgeBudget(formData: FormData): Promise<Outcome> {
  const workspace = await requireProviderWorkspace();
  return setBudget(await session(), workspace, Object.fromEntries(formData));
}

export async function requestJudgeRun(formData: FormData): Promise<Outcome> {
  const workspace = await requireProviderWorkspace();
  return requestRun(await session(), workspace, Object.fromEntries(formData));
}

export async function judgeCalibrationPage(formData: FormData): Promise<Outcome> {
  const workspace = await requireProviderWorkspace();
  return listCalibration(await session(), workspace, Object.fromEntries(formData));
}
