"use server";
// N4 server actions. Each one calls the provider guard itself (a layout does not stop an action) and
// hands the guarded workspace to the pure flow; the provider is never read from the form.
import { redirect } from "next/navigation";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { deriveVersion, exportVersion, previewImport, startImport, type ActionState } from "@/lib/services/datasets/flows";
import type { Derived, ExportRecord, ImportJob, Preview } from "@/lib/services/datasets/port";
import { datasetsPort } from "@/lib/services/datasets/server";

export async function previewImportAction(_: ActionState<Preview>, form: FormData): Promise<ActionState<Preview>> {
  const workspace = await requireProviderWorkspace();
  return previewImport(await datasetsPort(), workspace, form);
}

export async function startImportAction(_: ActionState<ImportJob>, form: FormData): Promise<ActionState<ImportJob>> {
  const workspace = await requireProviderWorkspace();
  const state = await startImport(await datasetsPort(), workspace, form);
  if (state.status === "ok") redirect(`/datasets/imports/${encodeURIComponent(state.value.importId)}`);
  return state;
}

export async function deriveAction(_: ActionState<Derived>, form: FormData): Promise<ActionState<Derived>> {
  const workspace = await requireProviderWorkspace();
  return deriveVersion(await datasetsPort(), workspace, form);
}

export async function exportAction(_: ActionState<ExportRecord>, form: FormData): Promise<ActionState<ExportRecord>> {
  const workspace = await requireProviderWorkspace();
  return exportVersion(await datasetsPort(), workspace, form, crypto.randomUUID());
}
