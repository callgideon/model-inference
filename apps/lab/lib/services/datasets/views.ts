// N4: what the datasets pages say, as pure functions (node --test runs them). A page shows success only
// for a server record that says so; every other state is named, including the ones a provider cannot fix.
import { SPLITS, type Derived, type Failure, type ImportJob, type Leak, type Split, type VersionStatus } from "./port.ts";

export const FAILURE_COPY: Record<Failure["error"], string> = {
  denied: "Your provider role cannot do this, or the data's grant no longer allows it.",
  not_found: "Nothing by that name in this provider workspace.",
  invalid: "The request was refused as invalid.",
  conflict: "This conflicts with what is already stored.",
  gone: "This has expired or was cancelled.",
  unavailable: "The datasets service did not answer. Nothing was changed on your side; try again.",
};

/** `again`: the page offers "Import again" (a new job of the same upload, R252) - for a failed job only. */
export type ImportView = { tone: "success" | "progress" | "error"; title: string; detail: string; poll: boolean; again: boolean };

/** The job page's headline. Only a published job with its dataset reads as success. */
export function importView(job: ImportJob): ImportView {
  const report = job.report;
  if (job.state === "published" && report?.datasetRef) {
    return { tone: "success", title: "Imported", detail: `${report.accepted} rows published as ${report.datasetRef}`, poll: false, again: false };
  }
  if (job.state === "running") return { tone: "progress", title: "Importing", detail: "The import runs on the backend; this page refreshes until it ends.", poll: true, again: false };
  if (job.state === "rejected") {
    const n = report?.rejected.length ?? 0;
    return { tone: "error", title: "Not published", detail: `${n} row(s) were rejected and ${report?.accepted ?? 0} accepted. Download the rejected rows, fix the upload or its mapping, and start a new import (a new import id).`, poll: false, again: false };
  }
  return { tone: "error", title: "Import failed", detail: `${job.error ?? "The import stopped."} "Import again" starts a new import of the same upload; this one stays failed.`, poll: false, again: job.state === "failed" };
}

/** The rejected rows as a JSONL download: line, reason and detail only (never the row's content). */
export function rejectedRowsJsonl(job: ImportJob): string {
  return (job.report?.rejected ?? []).map((r) => JSON.stringify({ line: r.line, reason: r.reason, detail: r.detail })).join("\n") + "\n";
}

export type SplitRow = { split: Split; samples: number; restricted: number };

/** Samples per split, and how many of them are restricted now. */
export function splitSummary(status: VersionStatus): SplitRow[] {
  return SPLITS.map((split) => {
    const inSplit = status.samples.filter((s) => s.split === split);
    return { split, samples: inSplit.length, restricted: inSplit.filter((s) => s.restricted !== null).length };
  });
}

export const RESTRICTED_COPY: Record<string, string> = {
  grant_not_current: "The data owner's grant no longer allows this use. The sample is excluded from reads, derivations and exports.",
  deleted: "The data owner deleted the request behind this sample. It is excluded everywhere and its copy is purged.",
  content_expired: "The content passed its retention bound. It is excluded everywhere and its copy is purged.",
  grant_revoked: "The data owner revoked access. The sample is excluded from reads, derivations and exports for good.",
  grant_narrowed: "The data owner's current grant no longer names this sample's model or data category. It is excluded from reads, derivations and exports for good.",
};

/** Why one sample is restricted; an unknown reason is still a restriction, never readable. */
export function restrictedCopy(reason: string): string {
  return RESTRICTED_COPY[reason] ?? `Restricted (${reason}). The sample is excluded from reads, derivations and exports.`;
}

/** Leakage warnings: families a derivation grouped across several group keys (near duplicates to
 * review), and a refused base whose splits leak. */
export function leakageWarnings(result: { derived?: Derived; leaks?: Leak[] }): string[] {
  const out: string[] = [];
  for (const leak of result.leaks ?? []) {
    out.push(`Leak: ${leak.samples.length} related samples sit in ${leak.splits.join(" and ")}. The version was refused.`);
  }
  for (const family of result.derived?.review ?? []) {
    out.push(`Review: ${family.length} samples from different groups share a clip or near-identical text and were kept in one split.`);
  }
  for (const o of result.derived?.omitted ?? []) {
    if (o.reason === "related_to_holdout") out.push(`Omitted ${o.sampleId}: related to a frozen holdout sample.`);
  }
  return out;
}

// --- UX-06 (L-07): the guided import and the split, as pure decisions -------------------------------

/** An exact percentage with at most two decimals ("80", "12.5", "0.25") as integer basis points, or
 * null: the digits are read as text, never through float arithmetic (0.29 % is 29 bp, not 28.99…). */
export function percentToBp(text: string): number | null {
  const hit = /^(\d{1,3})(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (hit === null) return null;
  const bp = Number(hit[1]) * 100 + Number((hit[2] ?? "").padEnd(2, "0"));
  return bp <= 10_000 ? bp : null;
}

/** Basis points as a percentage with two decimals: 8025 → "80.25%". */
export const bpPercent = (bp: number): string => `${Math.trunc(bp / 100)}.${String(bp % 100).padStart(2, "0")}%`;

export type SplitPlan = { ok: true; trainBp: number; validationBp: number; holdoutBp: number } | { ok: false; message: string };

/** Train and validation as typed; holdout is the remainder, so all three are shown before a submit. */
export function splitPlan(train: string, validation: string): SplitPlan {
  const trainBp = percentToBp(train);
  const validationBp = percentToBp(validation);
  if (trainBp === null || validationBp === null) return { ok: false, message: "Enter train and validation as percentages with at most two decimals (for example 80 or 12.5)." };
  if (trainBp + validationBp > 10_000) return { ok: false, message: "Train and validation add up to more than 100%." };
  return { ok: true, trainBp, validationBp, holdoutBp: 10_000 - trainBp - validationBp };
}

export type SourceFile = { name: string; size: number; lastModified: number } | null;

/** What a preview checked: the mapping text and the file. A preview counts only while this is unchanged. */
export const previewKey = (spec: string, file: SourceFile): string => JSON.stringify([spec, file?.name ?? null, file?.size ?? null, file?.lastModified ?? null]);

export const IMPORT_STEPS = ["source", "mapping", "validate", "import"] as const;
export type ImportStep = (typeof IMPORT_STEPS)[number];

/** Source → Mapping → Validate → Import. `previewed` is the key of the last successful preview; an
 * edit to the file or the mapping after it sends the provider back to Validate. */
export function importStep({ file, spec, previewed }: { file: SourceFile; spec: string; previewed: string | null }): ImportStep {
  if (file === null) return "source";
  if (spec.trim() === "") return "mapping";
  return previewed === previewKey(spec, file) ? "import" : "validate";
}

/** The import spec (infrx.dataset_import.1) with this workspace's provider and ids minted for this one
 * import. Licence, grant, annotation version and the content path are left empty: the backend refuses
 * them until the provider fills them, so nothing is authorised or labelled on the provider's behalf. */
export function importTemplate({ providerId, importId, datasetId, createdAt }: { providerId: string; importId: string; datasetId: string; createdAt: string }): string {
  return JSON.stringify({
    format: "infrx.dataset_import.1", provider_org_id: providerId, import_id: importId, dataset_id: datasetId, version: 1,
    created_at: createdAt, modality: "text", ownership: "provider_owned", license: "", use_restrictions: [], grant_ref: "",
    annotation: { method: "imported", method_version: "" }, fields: { content: "" },
  }, null, 2);
}
