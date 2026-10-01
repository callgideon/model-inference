// N4: the datasets server actions' decisions, pure over a port and the selected workspace (node --test
// runs them; app/(provider)/datasets/actions.ts only adds the guard and the real port). The provider is
// always the guarded workspace's, never a form field, and a viewer is refused before any call.
import type { Membership } from "../../auth/access.ts";
import type { DatasetsPort, Derived, ExportRecord, Failure, ImportJob, ImportReport, Leak, Preview } from "./port.ts";
import { FAILURE_COPY } from "./views.ts";

export type ActionState<T> =
  | { status: "idle" }
  | { status: "ok"; value: T }
  | { status: "error"; message: string; leaks?: Leak[]; report?: ImportReport };

/** ponytail: an upload rides the server action body (Next's 1 MB default); a signed direct-to-backend
 * upload when providers bring larger files (wiring with the backend route). */
export const MAX_UPLOAD_BYTES = 1_000_000;
export const PREVIEW_BYTES = 64 * 1024;
export const MAX_TTL_S = 7 * 86_400;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const refused = (message: string) => ({ status: "error", message }) as const;
const failed = (f: Failure) => ({ status: "error", message: `${FAILURE_COPY[f.error]} ${f.detail}`, leaks: f.leaks, report: f.report }) as const;

function writer(w: Membership): boolean {
  return w.role === "developer" || w.role === "administrator";
}

function spec(form: FormData): { ok: true; value: unknown } | { ok: false } {
  try {
    const value: unknown = JSON.parse(String(form.get("spec") ?? ""));
    return typeof value === "object" && value !== null && !Array.isArray(value) ? { ok: true, value } : { ok: false };
  } catch {
    return { ok: false };
  }
}

async function upload(form: FormData, limit: number): Promise<string | null> {
  const file = form.get("file");
  if (!(file instanceof Blob) || file.size === 0) return null;
  return file.slice(0, limit).text();
}

export async function previewImport(port: DatasetsPort, w: Membership, form: FormData): Promise<ActionState<Preview>> {
  if (!writer(w)) return refused(FAILURE_COPY.denied);
  const parsed = spec(form);
  if (!parsed.ok) return refused("The import spec is not a JSON object.");
  const head = await upload(form, PREVIEW_BYTES);
  if (head === null) return refused("Choose a JSONL file to preview.");
  const result = await port.preview(w.providerId, parsed.value, head);
  return result.ok ? { status: "ok", value: result.value } : failed(result);
}

export async function startImport(port: DatasetsPort, w: Membership, form: FormData): Promise<ActionState<ImportJob>> {
  if (!writer(w)) return refused(FAILURE_COPY.denied);
  const parsed = spec(form);
  if (!parsed.ok) return refused("The import spec is not a JSON object.");
  const file = form.get("file");
  if (file instanceof Blob && file.size > MAX_UPLOAD_BYTES) return refused(`An upload here is at most ${MAX_UPLOAD_BYTES} bytes.`);
  const body = await upload(form, MAX_UPLOAD_BYTES);
  if (body === null) return refused("Choose a JSONL file to import.");
  const result = await port.startImport(w.providerId, parsed.value, body, form.get("accept_rejects") === "on");
  return result.ok ? { status: "ok", value: result.value } : failed(result);
}

/** "Import again" (R252): the failed import as a new job; the answer's importId is the page to open. */
export async function requeueImport(port: DatasetsPort, w: Membership, form: FormData): Promise<ActionState<ImportJob>> {
  if (!writer(w)) return refused(FAILURE_COPY.denied);
  const result = await port.requeue(w.providerId, String(form.get("import_id") ?? ""));
  return result.ok ? { status: "ok", value: result.value } : failed(result);
}

function integer(form: FormData, name: string, min: number, max: number): number | null {
  const raw = String(form.get(name) ?? "").trim();
  const n = /^-?\d+$/.test(raw) ? Number(raw) : NaN;
  return Number.isSafeInteger(n) && n >= min && n <= max ? n : null;
}

export async function deriveVersion(port: DatasetsPort, w: Membership, form: FormData): Promise<ActionState<Derived>> {
  if (!writer(w)) return refused(FAILURE_COPY.denied);
  const datasetId = String(form.get("dataset_id") ?? "").trim();
  const version = integer(form, "version", 1, 1_000_000);
  const seed = integer(form, "seed", 0, 2 ** 31 - 1);
  const trainBp = integer(form, "train_bp", 0, 10_000);
  const validationBp = integer(form, "validation_bp", 0, 10_000);
  const base = String(form.get("base") ?? "").trim() || null;
  const add = String(form.get("add") ?? "").split(/\s+/).filter(Boolean);
  if (!UUID.test(datasetId)) return refused("The new dataset id is a lowercase UUID.");
  if (version === null || seed === null || trainBp === null || validationBp === null || trainBp + validationBp > 10_000) {
    return refused("Version, seed and split basis points (0..10000 in all) must be whole numbers.");
  }
  if (base === null && add.length === 0) return refused("Name a base version or versions to add.");
  const result = await port.derive(w.providerId, { datasetId, version, seed, trainBp, validationBp, base, add });
  return result.ok ? { status: "ok", value: result.value } : failed(result);
}

export async function exportVersion(port: DatasetsPort, w: Membership, form: FormData, exportId: string): Promise<ActionState<ExportRecord>> {
  if (!writer(w)) return refused(FAILURE_COPY.denied);
  const ref = String(form.get("dataset_ref") ?? "");
  const ttl = integer(form, "ttl_s", 1, MAX_TTL_S);
  if (ref === "") return refused("Choose a version to export.");
  if (ttl === null) return refused(`An export lives 1..${MAX_TTL_S} seconds.`);
  const result = await port.exportVersion(w.providerId, ref, exportId, ttl);
  return result.ok ? { status: "ok", value: result.value } : failed(result);
}
