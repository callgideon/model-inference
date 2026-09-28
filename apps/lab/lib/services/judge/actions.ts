"use server";
// C3L server actions: each one calls the provider guard itself (tests/l/shell boundary), then the
// pure core with the user's own session client. The Lab holds no service-role key.
import { requireProviderWorkspace } from "../../auth/guard.ts";
import { configure, listCalibration, requestRun, setBudget, type Outcome } from "./core.ts";
import { session } from "./session.ts";

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
