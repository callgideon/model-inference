// The Lab service families' shared action, refusal-copy and preview helpers (LAB-09). Replaces the
// land()/field()/oneOf() copies in control, evaluation and pipelines actions (and rollouts' inline
// one), the four refusalCopy() guards, and the four preview-switch blocks in the ports. Pure: the
// actions redirect to what land() answers. Each family keeps its own refusals, copy and record checks.
import { holds, type Actor, type Capability } from "../auth/access.ts";

type Answer<T> = { ok: true; value: T } | { ok: false; reason: string };
type Env = Record<string, string | undefined>;

/** A form field as text; anything else (a file, nothing) is null. */
export const text = (data: FormData, name: string): string | null => {
  const v = data.get(name);
  return typeof v === "string" ? v : null;
};
/** A form field in `shape`, whole, or null. */
export const field = (data: FormData, name: string, shape: RegExp): string | null => {
  const v = text(data, name);
  return v !== null && shape.test(v) ? v : null;
};
/** A form field that is one of `values`, or null. */
export const oneOf = <T extends string>(data: FormData, name: string, values: readonly T[]): T | null => {
  const v = text(data, name);
  return values.includes(v as T) ? (v as T) : null;
};

/**
 * Where a server action lands: refused here (capability, then shape) without asking the service, or the
 * service's answer. AP-09: the capability is the workspace's own set as the API states it (holds()). A refusal is its fixed reason on `page` (which may already carry a query); success
 * is where `to` says, by default a plain return to the page, which re-reads the records.
 */
export async function land<T>(page: string, w: Actor, capability: Capability, valid: boolean, call: () => Promise<Answer<T>>, to: (value: T) => string = () => page): Promise<string> {
  const refused = !holds(w, capability) ? "denied" : !valid ? "invalid" : null;
  const result: Answer<T> = refused === null ? await call() : { ok: false, reason: refused };
  return result.ok ? to(result.value) : `${page}${page.includes("?") ? "&" : "?"}refused=${result.reason}`;
}

/** `?refused=` is anyone's to write: only a known reason's fixed copy is ever shown. */
export const fixedCopy = <R extends string>(refusals: readonly R[], copy: Record<R, string>) => (value: unknown): string | null =>
  (refusals as readonly unknown[]).includes(value) ? copy[value as R] : null;

/** Every call of a port that is not configured. */
export const down = async () => ({ ok: false, reason: "unavailable" }) as const;

/**
 * A family's port: the HTTP adapter when configured, else `unavailable` (fails closed), or - only
 * outside production and only when `flag` is "1" - the labelled preview fake, one per process.
 */
export function previewPort<P>(flag: string, fake: () => P, real: (env: Env) => P | null, unavailable: P) {
  let preview: P | undefined;
  const isPreview = (env: Env = process.env) => env[flag] === "1" && env.NODE_ENV !== "production";
  const port = (env: Env = process.env): P => (isPreview(env) ? (preview ??= fake()) : real(env) ?? unavailable);
  return { isPreview, port };
}

/** The first failed read's reason, in read order (a read the page skipped is null), or null when none failed. */
export function firstFailure<R extends string>(...results: ({ ok: true } | { ok: false; reason: R } | null)[]): R | null {
  for (const r of results) if (r !== null && !r.ok) return r.reason;
  return null;
}
