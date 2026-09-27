// N4: the Lab's datasets port over the backend's N1/N2/N3 service (apps/infrx-api/infrx/datasets).
// The Lab holds no storage or database credential: every call goes to the backend with the user's own
// session token, and the backend re-derives the acting provider from it (WR-N-2). Every answer is
// parsed here and anything unexpected fails closed as `unavailable`, so a page never renders success
// from a response it did not understand.

export type Rejection = { line: number; reason: string; detail: string };
export type ImportReport = { accepted: number; rejected: Rejection[]; datasetRef: string | null; sourceRef: string | null };
export const IMPORT_STATES = ["running", "published", "rejected", "failed"] as const;
export type ImportState = (typeof IMPORT_STATES)[number];
export type ImportJob = { importId: string; state: ImportState; report: ImportReport | null; error: string | null };
export type PreviewRow = { line: number; mapped?: Record<string, unknown>; reason?: string };
export type Preview = { fields: Record<string, string[]>; rows: PreviewRow[] };
export const SPLITS = ["train", "validation", "holdout"] as const;
export type Split = (typeof SPLITS)[number];
export type VersionSummary = { datasetRef: string; datasetId: string; version: number; derivation: string; samples: number };
export type Trace = { grantorOrgId: string; requestId: string; contentUntil: string };
export type SampleView = { sampleId: string; split: Split; sourceRef: string; grantRef: string; trace: Trace | null; restricted: string | null };
export type VersionStatus = { datasetRef: string; parentRefs: string[]; samples: SampleView[] };
export type Omission = { sampleId: string; reason: string };
export type Derived = { datasetRef: string; splitDigest: string; omitted: Omission[]; review: string[][] };
export type ExportPart = { items: number; sha256: string };
export type ExportRecord = { exportId: string; datasetRef: string; parts: ExportPart[]; omitted: Omission[]; expiresAt: string };
export type Leak = { samples: string[]; splits: string[] };
export type DeriveRequest = { datasetId: string; version: number; seed: number; trainBp: number; validationBp: number; base: string | null; add: string[] };

export const ERRORS = ["denied", "not_found", "invalid", "conflict", "gone", "unavailable"] as const;
export type Failure = { ok: false; error: (typeof ERRORS)[number]; detail: string; leaks?: Leak[]; report?: ImportReport };
export type Result<T> = { ok: true; value: T } | Failure;

export interface DatasetsPort {
  preview(provider: string, spec: unknown, head: string): Promise<Result<Preview>>;
  startImport(provider: string, spec: unknown, body: string, acceptRejects: boolean): Promise<Result<ImportJob>>;
  importJob(provider: string, importId: string): Promise<Result<ImportJob>>;
  versions(provider: string): Promise<Result<VersionSummary[]>>;
  version(provider: string, ref: string): Promise<Result<VersionStatus>>;
  derive(provider: string, request: DeriveRequest): Promise<Result<Derived>>;
  exportVersion(provider: string, ref: string, exportId: string, ttlS: number): Promise<Result<ExportRecord>>;
  readPart(provider: string, exportId: string, part: number): Promise<Result<string>>;
}

const STATUS: Record<number, Failure["error"]> = { 400: "invalid", 403: "denied", 404: "not_found", 409: "conflict", 410: "gone", 413: "invalid", 422: "invalid" };
const unavailable = (detail: string): Failure => ({ ok: false, error: "unavailable", detail });

// --- parsing: the backend's snake_case JSON, checked field by field --------------------------------
type Json = Record<string, unknown>;
const isObj = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string => {
  if (typeof v !== "string") throw new TypeError("expected a string");
  return v;
};
const int = (v: unknown): number => {
  if (typeof v !== "number" || !Number.isInteger(v) || v < 0) throw new TypeError("expected a count");
  return v;
};
const list = <T>(v: unknown, each: (x: unknown) => T): T[] => {
  if (!Array.isArray(v)) throw new TypeError("expected a list");
  return v.map(each);
};
const obj = (v: unknown): Json => {
  if (!isObj(v)) throw new TypeError("expected an object");
  return v;
};
const opt = (v: unknown): string | null => (v === null || v === undefined ? null : str(v));
const omission = (v: unknown): Omission => ({ sampleId: str(obj(v).sample_id), reason: str(obj(v).reason) });

export function parseReport(v: unknown): ImportReport {
  const o = obj(v);
  return {
    accepted: int(o.accepted),
    rejected: list(o.rejected, (r) => ({ line: int(obj(r).line), reason: str(obj(r).reason), detail: typeof obj(r).detail === "string" ? str(obj(r).detail) : "" })),
    datasetRef: opt(o.dataset_ref),
    sourceRef: opt(o.source_ref),
  };
}

export function parseJob(v: unknown): ImportJob {
  const o = obj(v);
  const state = str(o.state) as ImportState;
  if (!IMPORT_STATES.includes(state)) throw new TypeError("unknown import state");
  const report = o.report === null || o.report === undefined ? null : parseReport(o.report);
  if (state === "published" && (report === null || report.datasetRef === null)) throw new TypeError("published without a dataset");
  return { importId: str(o.import_id), state, report, error: opt(o.error) };
}

export function parsePreview(v: unknown): Preview {
  const o = obj(v);
  const fields: Record<string, string[]> = {};
  for (const [k, t] of Object.entries(obj(o.fields))) fields[k] = list(t, str);
  const rows = list(o.rows, (r) => {
    const row = obj(r);
    return row.reason !== undefined ? { line: int(row.line), reason: str(row.reason) } : { line: int(row.line), mapped: obj(row.mapped) };
  });
  return { fields, rows };
}

export function parseVersions(v: unknown): VersionSummary[] {
  return list(v, (x) => {
    const o = obj(x);
    return { datasetRef: str(o.dataset_ref), datasetId: str(o.dataset_id), version: int(o.version), derivation: str(o.derivation), samples: int(o.samples) };
  });
}

export function parseStatus(v: unknown): VersionStatus {
  const o = obj(v);
  return {
    datasetRef: str(o.dataset_ref),
    parentRefs: list(o.parent_refs, str),
    samples: list(o.samples, (x) => {
      const s = obj(x);
      const split = str(s.split) as Split;
      if (!SPLITS.includes(split)) throw new TypeError("unknown split");
      const t = s.trace === null || s.trace === undefined ? null : obj(s.trace);
      return {
        sampleId: str(s.sample_id), split, sourceRef: str(s.source_ref), grantRef: str(s.grant_ref),
        trace: t && { grantorOrgId: str(t.grantor_org_id), requestId: str(t.request_id), contentUntil: str(t.content_until) },
        restricted: opt(s.restricted),
      };
    }),
  };
}

export function parseDerived(v: unknown): Derived {
  const o = obj(v);
  return { datasetRef: str(o.dataset_ref), splitDigest: str(o.split_digest), omitted: list(o.omitted, omission), review: list(o.review, (f) => list(f, str)) };
}

export function parseExport(v: unknown): ExportRecord {
  const o = obj(v);
  return {
    exportId: str(o.export_id), datasetRef: str(o.dataset_ref), expiresAt: str(o.expires_at), omitted: list(o.omitted, omission),
    parts: list(o.parts, (p) => ({ items: int(obj(p).items), sha256: str(obj(p).sha256) })),
  };
}

function failure(status: number, body: unknown): Failure {
  const detail = isObj(body) && typeof body.detail === "string" ? body.detail : `HTTP ${status}`;
  const out: Failure = { ok: false, error: STATUS[status] ?? "unavailable", detail };
  try {
    if (isObj(body) && body.leaks !== undefined) out.leaks = list(body.leaks, (l) => ({ samples: list(obj(l).samples, str), splits: list(obj(l).splits, str) }));
    if (isObj(body) && body.report !== undefined) out.report = parseReport(body.report);
  } catch {
    return unavailable("the backend's refusal was malformed");
  }
  return out;
}

export type HttpOptions = { baseUrl: string; token: string; fetch?: typeof fetch };

/** The backend adapter. `token` is the signed-in user's own session token; nothing else is sent. */
export function httpDatasets({ baseUrl, token, fetch: send = fetch }: HttpOptions): DatasetsPort {
  const root = (provider: string) => `${baseUrl.replace(/\/$/, "")}/lab/v1/providers/${encodeURIComponent(provider)}/datasets`;
  async function call<T>(provider: string, path: string, parse: (v: unknown) => T, body?: unknown, raw = false): Promise<Result<T>> {
    let response: Response;
    try {
      response = await send(`${root(provider)}${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers: { authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "content-type": "application/json" }) },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
      });
    } catch {
      return unavailable("the datasets service could not be reached");
    }
    let payload: unknown;
    try {
      payload = raw && response.ok ? await response.text() : await response.json();
    } catch {
      return response.ok ? unavailable("the datasets service answered with something unreadable") : failure(response.status, null);
    }
    if (!response.ok) return failure(response.status, payload);
    try {
      return { ok: true, value: parse(payload) };
    } catch {
      return unavailable("the datasets service answered with something unreadable");
    }
  }
  const id = encodeURIComponent;
  return {
    preview: (p, spec, head) => call(p, "/imports/preview", parsePreview, { spec, head }),
    startImport: (p, spec, body, acceptRejects) => call(p, "/imports", parseJob, { spec, body, accept_rejects: acceptRejects }),
    importJob: (p, importId) => call(p, `/imports/${id(importId)}`, parseJob),
    versions: (p) => call(p, "/versions", parseVersions),
    version: (p, ref) => call(p, `/versions/${id(ref)}`, parseStatus),
    derive: (p, r) =>
      call(p, "/versions", parseDerived, {
        dataset_id: r.datasetId, version: r.version, base: r.base, add: r.add,
        policy: { seed: r.seed, train_bp: r.trainBp, validation_bp: r.validationBp },
      }),
    exportVersion: (p, ref, exportId, ttlS) => call(p, "/exports", parseExport, { dataset_ref: ref, export_id: exportId, ttl_s: ttlS }),
    readPart: (p, exportId, part) => call(p, `/exports/${id(exportId)}/parts/${part}`, str, undefined, true),
  };
}

/** The port when the backend is not configured or the session has no token: every call is unavailable. */
export function offlineDatasets(detail = "the datasets service is not configured"): DatasetsPort {
  const down = async (): Promise<Failure> => unavailable(detail);
  return { preview: down, startImport: down, importJob: down, versions: down, version: down, derive: down, exportVersion: down, readPart: down };
}

/** The HTTP status a Lab route answers with for a failure (a download never fakes a 200). */
export function failureStatus(f: Failure): number {
  return { denied: 403, not_found: 404, invalid: 400, conflict: 409, gone: 410, unavailable: 503 }[f.error];
}
