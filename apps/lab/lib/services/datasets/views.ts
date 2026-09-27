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

export type ImportView = { tone: "success" | "progress" | "error"; title: string; detail: string; poll: boolean };

/** The job page's headline. Only a published job with its dataset reads as success. */
export function importView(job: ImportJob): ImportView {
  const report = job.report;
  if (job.state === "published" && report?.datasetRef) {
    return { tone: "success", title: "Imported", detail: `${report.accepted} rows published as ${report.datasetRef}`, poll: false };
  }
  if (job.state === "running") return { tone: "progress", title: "Importing", detail: "The import runs on the backend; this page refreshes until it ends.", poll: true };
  if (job.state === "rejected") {
    const n = report?.rejected.length ?? 0;
    return { tone: "error", title: "Not published", detail: `${n} row(s) were rejected and ${report?.accepted ?? 0} accepted. Download the rejected rows, fix the upload or its mapping, and import again under the same import id to resume.`, poll: false };
  }
  return { tone: "error", title: "Import failed", detail: `${job.error ?? "The import stopped."} Importing again under the same import id resumes from the staged rows.`, poll: false };
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
