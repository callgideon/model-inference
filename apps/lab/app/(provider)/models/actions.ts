"use server";
// UX-03 L-03: "New revision of an imported model" (the legacy four-field registration) as a form
// state: the workspace and role are the session's; the role is checked before anything is sent;
// invalid fields come back named, with the person's input, and are never sent; the service's answer
// is the record, its fixed refusal, or "not confirmed" (no replay receipt exists for registration).
import { holds } from "@/lib/auth/access";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { text } from "@/lib/services/common";
import { controlPort } from "@/lib/services/control/port";
import {
  REFUSAL_COPY, REGISTRATION_FIELDS, registrationErrors, registrationOutcome, type RegistrationState, type RegistrationValues,
} from "@/lib/services/control/view";

export async function registerRevision(_previous: RegistrationState, data: FormData): Promise<RegistrationState> {
  const w = await requireProviderWorkspace();
  const values = Object.fromEntries(REGISTRATION_FIELDS.map((f) => [f, text(data, f) ?? ""])) as RegistrationValues;
  if (!holds(w.role, "manage_dev_deployment")) return { values, errors: {}, outcome: { kind: "refused", message: REFUSAL_COPY.denied } };
  const errors = registrationErrors(values);
  if (Object.keys(errors).length > 0) return { values, errors, outcome: null };
  return { values, errors: {}, outcome: registrationOutcome(await controlPort().register(w, values)) };
}
