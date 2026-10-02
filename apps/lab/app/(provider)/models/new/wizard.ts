// AP-09 09d: the Add model wizard's pure rules - which step the URL names, how an operation, a
// compatibility report and an API refusal read, and the manifest a person pastes. Nothing here
// claims more than the record it is given: "Artifact verified" needs a succeeded operation that names
// its artifact; a serving revision is not a deployment.
import type { ApiError } from "@infrx/api-client/transport";
import type { FileEntry, OperationDoc } from "./api";

export type Step = "project" | "source" | "validation" | "setup" | "done";
export type WizardQuery = { project?: string; operation?: string; artifact?: string; revision?: string };

/** An id the URL may carry (opaque, bounded); anything else is ignored, never sent. */
export const ID = /^[A-Za-z0-9_-]{1,64}$/;
const id = (v: unknown): string | undefined => (typeof v === "string" && ID.test(v) ? v : undefined);

export function wizardQuery(q: Record<string, string | string[] | undefined>): WizardQuery {
  return { project: id(q.project), operation: id(q.operation), artifact: id(q.artifact), revision: id(q.revision) };
}

/** The step a (validated) query names: each later id needs the project it belongs to. */
export function step(q: WizardQuery): Step {
  if (q.project === undefined) return "project";
  if (q.revision !== undefined) return "done";
  if (q.artifact !== undefined) return "setup";
  if (q.operation !== undefined) return "validation";
  return "source";
}

export const STEPS: { key: Step; label: string }[] = [
  { key: "project", label: "Model project" },
  { key: "source", label: "Repository or upload" },
  { key: "validation", label: "Verification" },
  { key: "setup", label: "Serving setup" },
];

type Tone = "neutral" | "info" | "warning" | "danger" | "success";
export type OperationView = { tone: Tone; label: string; terminal: boolean; artifactId: string | null; detail: string };
/** An operation as the person reads it: its actual state and phase, no percentage or ETA. */
export function operationView(op: OperationDoc): OperationView {
  const phase = op.phase ? ` (${op.phase})` : "";
  switch (op.state) {
    case "queued":
    case "running":
      return { tone: "info", label: "Verifying", terminal: false, artifactId: null, detail: `The server is checking every file against the manifest${phase}.` };
    case "succeeded":
      return op.resource_id
        ? { tone: "success", label: "Artifact verified", terminal: true, artifactId: op.resource_id, detail: "The server re-hashed every declared file and recorded the artifact." }
        : { tone: "warning", label: "Finished without an artifact", terminal: true, artifactId: null, detail: "The operation names no artifact. Start the source step again." };
    case "failed":
      return { tone: "danger", label: "Verification failed", terminal: true, artifactId: null, detail: op.error?.message ?? "The server gave no reason." };
    case "cancel_requested":
      return { tone: "warning", label: "Cancellation requested", terminal: false, artifactId: null, detail: "The server is stopping the work it started." };
    case "cancelled":
      return { tone: "neutral", label: "Cancelled", terminal: true, artifactId: null, detail: "Nothing was recorded." };
  }
}

/** The pasted manifest: a JSON list of {relative_path, bytes, sha256, media_type}. The server checks the
 *  rest (paths, code-bearing files, limits) and re-hashes the real bytes; this only refuses what cannot
 *  be a manifest, so the person fixes it in place. */
export function parseManifest(source: string): { files: FileEntry[] } | { error: string } {
  let value: unknown;
  try {
    value = JSON.parse(source);
  } catch {
    return { error: "The manifest is not valid JSON." };
  }
  const list = Array.isArray(value) ? value : (value as { files?: unknown } | null)?.files;
  if (!Array.isArray(list) || list.length === 0) return { error: "The manifest needs a non-empty list of files." };
  const bad = list.findIndex((f) => {
    const e = f as Record<string, unknown> | null;
    return !e || typeof e.relative_path !== "string" || !Number.isInteger(e.bytes) || typeof e.sha256 !== "string" || typeof e.media_type !== "string";
  });
  if (bad !== -1) return { error: `File ${bad + 1} needs relative_path, bytes (a whole number), sha256 and media_type.` };
  return { files: list.map((f: FileEntry) => ({ relative_path: f.relative_path, bytes: f.bytes, sha256: f.sha256, media_type: f.media_type })) };
}

export type Problem = { message: string; fields: string[] };
/** An API refusal as safe copy. A mutation nobody answered is "not confirmed" (retry with the same form
 *  replays it: the Idempotency-Key is the form's); the R270 field errors are listed as the server named them. */
export function problem(error: ApiError, mutation: boolean): Problem {
  const fields = error.kind === "error" ? error.fieldErrors.map((f) => `${f.field}: ${f.message}`) : [];
  const status = error.status ?? 0;
  if (error.kind === "unavailable" || status >= 500)
    return { fields, message: mutation
      ? "Outcome not confirmed: the Lab API did not answer. Submitting this same form again is safe; it cannot create a second record."
      : "The Lab's model import service isn't available right now. Try again shortly." };
  if (status === 401 || status === 403) return { fields, message: "Your role in this workspace does not allow that." };
  if (status === 404) return { fields, message: "That record is not in this workspace." };
  if (status === 409) return { fields, message: "This form was already submitted with different values, or the record moved on. Reload the page to start again." };
  if (status === 410) return { fields, message: "This upload session expired. Start the upload again." };
  if (status === 413) return { fields, message: "The files are larger than the import limits allow." };
  return { fields, message: "Those values were not accepted." };
}
