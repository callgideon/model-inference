// The swap's row check, shared by the evaluation, pipelines and releases HTTP adapters: a record the
// page cannot read (a missing field, a value of another type, a state it does not know) fails the whole
// answer closed as "unavailable" instead of reaching a page. Unnamed fields pass through untouched.
export type Check = (value: unknown) => boolean;

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
export const str: Check = (v) => typeof v === "string";
export const num: Check = (v) => typeof v === "number" && Number.isFinite(v);
export const bool: Check = (v) => typeof v === "boolean";
export const nul = (check: Check): Check => (v) => v === null || check(v);
export const opt = (check: Check): Check => (v) => v === undefined || check(v);
export const oneOf = (...values: readonly unknown[]): Check => (v) => values.includes(v);
export const list = (check: Check): Check => (v) => Array.isArray(v) && v.every(check);
export const map = (check: Check): Check => (v) => isObj(v) && Object.values(v).every(check);
export const obj = (spec: Record<string, Check>): Check => (v) => isObj(v) && Object.entries(spec).every(([k, check]) => check(v[k]));
