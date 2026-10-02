"use server";
// C3L server actions: each one calls the provider guard itself (tests/l/shell boundary), then the
// pure core over the Lab port as the user's own session (AP-09). The Lab holds no service-role key.
import { requireProviderWorkspace } from "../../auth/guard.ts";
import { sessionApi } from "../../auth/session.ts";
import { calibration, configure, requestRun, setBudget, type Outcome } from "./core.ts";

export async function configureJudge(formData: FormData): Promise<Outcome> {
  const workspace = await requireProviderWorkspace();
  return configure(sessionApi(), workspace, Object.fromEntries(formData));
}

export async function setJudgeBudget(formData: FormData): Promise<Outcome> {
  const workspace = await requireProviderWorkspace();
  return setBudget(sessionApi(), workspace, Object.fromEntries(formData));
}

export async function requestJudgeRun(formData: FormData): Promise<Outcome> {
  const workspace = await requireProviderWorkspace();
  return requestRun(sessionApi(), workspace, Object.fromEntries(formData));
}

export async function judgeCalibrationPage(formData: FormData): Promise<Outcome> {
  const workspace = await requireProviderWorkspace();
  return calibration(sessionApi(), workspace, Object.fromEntries(formData));
}
