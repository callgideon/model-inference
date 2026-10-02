"use server";
// Synthetic stand-ins for the datasets server actions (UX-06): no backend, no session, fixed records.
// Each echoes what it received so the browser cases can see which form values reached the action.
import type { ActionState } from "@/lib/services/datasets/flows";
import type { Derived, ExportRecord, ImportJob, Preview } from "@/lib/services/datasets/port";

export async function preview(_: ActionState<Preview>, form: FormData): Promise<ActionState<Preview>> {
  const spec = String(form.get("spec") ?? "");
  if (spec.includes("refuse")) return { status: "error", message: "Synthetic refusal: the import spec is not a JSON object." };
  if (spec.includes("slow")) await new Promise((r) => setTimeout(r, 1500));
  return {
    status: "ok",
    value: { fields: { content: ["string"], split: ["string"] }, rows: [{ line: 1, mapped: { content: "synthetic row one" } }, { line: 2, reason: "missing_field" }] },
  };
}

export async function start(_: ActionState<ImportJob>, form: FormData): Promise<ActionState<ImportJob>> {
  const file = form.get("file");
  return { status: "error", message: `Synthetic start received accept_rejects=${form.get("accept_rejects")} file=${file instanceof File ? file.name : "none"} spec=${String(form.get("spec") ?? "").length}` };
}

export async function derive(_: ActionState<Derived>, form: FormData): Promise<ActionState<Derived>> {
  return { status: "error", message: `Synthetic derive received train_bp=${form.get("train_bp")} validation_bp=${form.get("validation_bp")} dataset_id=${form.get("dataset_id")}` };
}

export async function exportVersion(_: ActionState<ExportRecord>, form: FormData): Promise<ActionState<ExportRecord>> {
  return { status: "error", message: `Synthetic export received ttl_s=${form.get("ttl_s")}` };
}
