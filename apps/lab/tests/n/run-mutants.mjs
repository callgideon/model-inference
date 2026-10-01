#!/usr/bin/env node
// N4's mutant runner (R32; LANE-RULES addendum) on the shared harness (tests/l/shell/harness.mjs): every N4
// decision is one edit that a case it names must fail by assertion. The suite includes L1's boundary
// test, which kills an unguarded datasets page, route or action; every N4 case is named.
// Usage: node tests/n/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../l/shell/harness.mjs";

const SUITE = ["tests/n/port.test.ts", "tests/n/views.test.ts", "tests/n/flows.test.ts", "tests/n/wiring.test.ts", "tests/l/shell/boundary.test.ts"];

const PORT = "lib/services/datasets/port.ts";
const VIEWS = "lib/services/datasets/views.ts";
const FLOWS = "lib/services/datasets/flows.ts";
const SERVER = "lib/services/datasets/server.ts";
const D = "app/(provider)/datasets";

const C = {
  p01: "N4-P01 every call is the provider-scoped path with the user's own token and nothing else",
  p02: "N4-P02 refusals are named failures, carrying leaks and the rejection report",
  p03: "N4-P03 a success the Lab does not understand is unavailable, never success",
  p04: "N4-P04 an unreachable or unconfigured service is unavailable for every call",
  p05: "N4-P05 a download route answers a failure with its status, never 200",
  w01: "N4-W01 the datasets port reads its backend as the session's own access token; no token, no backend URL or no Lab config sends nothing",
  v01: "N4-V01 only a published import with its dataset reads as success; the rest say how to resume",
  v02: "N4-V02 the rejected-rows download is line, reason and detail only",
  v03: "N4-V03 the split summary counts samples and restrictions per split",
  v04: "N4-V04 every restriction is explained; an unknown reason is still a restriction",
  v05: "N4-V05 leakage warnings name leaks, near-duplicate families and holdout relatives",
  v06: "N4-V06 an Import again action is offered for a failed job only (R252)",
  f01: "N4-F01 a viewer is refused before any backend call",
  f02: "N4-F02 the provider is the guarded workspace's, never a submitted field",
  f03: "N4-F03 a malformed mapping or a missing file never leaves the Lab; a preview reads only the head",
  f04: "N4-F04 an upload over the bound is refused before the backend",
  f05: "N4-F05 a derivation needs a UUID, whole numbers within 10000 basis points and a parent",
  f06: "N4-F06 an export lives 1 s to 7 days and names a version",
  f07: "N4-F07 a backend failure is an error state carrying its leaks and report, never success",
  f08: "N4-F08 a new dataset id is a lowercase v4 UUID: a v1 or uppercase id is refused (shapes UUID_RE, LAB-10)",
  b01: "L1-B01 every page, route, provider layout and server action calls the provider guard",
};

const MUTANTS = [
  m("N4-X01", "the backend gets a credential other than the user's session", PORT, "authorization: `Bearer ${token}`", "authorization: `Bearer service-role`", [C.p01]),
  m("N4-X02", "a provider id can rewrite the backend path", PORT, "/providers/${encodeURIComponent(provider)}/datasets", "/providers/${provider}/datasets", [C.p01]),
  m("N4-X03", "provider data becomes cacheable", PORT, '        cache: "no-store",\n', "", [C.p01]),
  m("N4-X04", "a denial reads as not found", PORT, '403: "denied"', '403: "not_found"', [C.p02]),
  m("N4-X05", "an unknown server error reads as the user's mistake", PORT, 'STATUS[status] ?? "unavailable"', 'STATUS[status] ?? "invalid"', [C.p02]),
  m("N4-X06", "a refused derivation loses its leaks", PORT, "    if (isObj(body) && body.leaks !== undefined) out.leaks =", "    if (false) out.leaks =", [C.p02]),
  m("N4-X07", "a malformed refusal is passed on as understood", PORT, '    return unavailable("the backend\'s refusal was malformed");', "    return out;", [C.p02]),
  m("N4-X08", "a published job without its dataset reads as success", PORT, '  if (state === "published" && (report === null || report.datasetRef === null)) throw new TypeError("published without a dataset");\n', "", [C.p03]),
  m("N4-X09", "an unknown import state is accepted", PORT, '  if (!IMPORT_STATES.includes(state)) throw new TypeError("unknown import state");\n', "", [C.p03]),
  m("N4-X10", "an unknown split is accepted", PORT, '      if (!SPLITS.includes(split)) throw new TypeError("unknown split");\n', "", [C.p03]),
  m("N4-X11", "an unparsed body is success", PORT, "return { ok: true, value: parse(payload) };", "return { ok: true, value: payload as T };", [C.p03]),
  m("N4-X12", "a negative count is accepted", PORT, "!Number.isInteger(v) || v < 0)", "!Number.isInteger(v))", [C.p03]),
  m("N4-X13", "a network error escapes as a crash", PORT, '    } catch {\n      return unavailable("the datasets service could not be reached");', "    } catch (error) {\n      throw error;", [C.p04]),
  m("N4-X14", "the unconfigured port answers success", PORT, "const down = async (): Promise<Failure> => unavailable(detail);", "const down = async (): Promise<Failure> => ({ ok: true, value: [] }) as never;", [C.p04]),
  m("N4-X15", "a failed download is a 200", PORT, "unavailable: 503 }", "unavailable: 200 }", [C.p05]),
  m("N4-X16", "a published job without a dataset reads as imported", VIEWS, 'if (job.state === "published" && report?.datasetRef)', 'if (job.state === "published")', [C.v01]),
  m("N4-X17", "a running import is not followed", VIEWS, "this page refreshes until it ends.\", poll: true, again: false }", "this page refreshes until it ends.\", poll: false, again: false }", [C.v01]),
  m("N4-X40", "'Import again' is offered for a rejected import (its same rows reject again)", VIEWS, "start a new import (a new import id).`, poll: false, again: false };", "start a new import (a new import id).`, poll: false, again: true };", [C.v06]),
  m("N4-X41", "the datasets port reads another family's old name", SERVER, "labApiUrl(process.env, \"datasets\")", "labApiUrl(process.env, \"traces\")", [C.w01]),
  m("N4-X42", "an unset backend URL still builds the adapter", SERVER, "if (!baseUrl || config === null) return offlineDatasets();", "if (config === null) return offlineDatasets();", [C.w01]),
  m("N4-X43", "the publishable key is sent as the credential", SERVER, "const token = await sessionToken(config)();", "const token = config.anonKey;", [C.w01]),
  m("N4-X44", "a tokenless session still sends", SERVER, "return token ? httpDatasets({ baseUrl, token }) : offlineDatasets(\"the session has no token\");", "return httpDatasets({ baseUrl, token: token ?? \"\" });", [C.w01]),
  m("N4-X18", "the rejected-rows download carries the row", VIEWS, "JSON.stringify({ line: r.line, reason: r.reason, detail: r.detail })", "JSON.stringify(r)", [C.v02]),
  m("N4-X19", "restricted samples are not counted", VIEWS, "restricted: inSplit.filter((s) => s.restricted !== null).length", "restricted: 0", [C.v03]),
  m("N4-X20", "an unknown restriction reads as readable", VIEWS, "return RESTRICTED_COPY[reason] ?? `", 'return RESTRICTED_COPY[reason] ?? "readable" ?? `', [C.v04]),
  m("N4-X21", "a leak is not shown", VIEWS, "for (const leak of result.leaks ?? [])", "for (const leak of [] as Leak[])", [C.v05]),
  m("N4-X22", "a holdout relative is not shown", VIEWS, 'if (o.reason === "related_to_holdout")', "if (false)", [C.v05]),
  m("N4-X23", "a viewer reaches the backend", FLOWS, 'return holds(w.role, "run_evaluation");', "return true;", [C.f01]),
  m("N4-X24", "the provider comes from the form", FLOWS, "port.startImport(w.providerId,", 'port.startImport(String(form.get("providerId") ?? w.providerId),', [C.f02]),
  m("N4-X25", "accepting rejects is ignored", FLOWS, 'form.get("accept_rejects") === "on"', "false", [C.f02]),
  m("N4-X26", "an array spec is sent", FLOWS, "typeof value === \"object\" && value !== null && !Array.isArray(value)", "typeof value === \"object\" && value !== null", [C.f03]),
  m("N4-X27", "the preview sends the whole upload", FLOWS, "const head = await upload(form, PREVIEW_BYTES);", "const head = await upload(form, MAX_UPLOAD_BYTES);", [C.f03]),
  m("N4-X28", "an empty file is sent", FLOWS, "if (!(file instanceof Blob) || file.size === 0) return null;", "if (!(file instanceof Blob)) return null;", [C.f03]),
  m("N4-X29", "an oversized upload is sent", FLOWS, "if (file instanceof Blob && file.size > MAX_UPLOAD_BYTES) return", "if (false) return", [C.f04]),
  m("N4-X30", "split basis points over 10000 are sent", FLOWS, " || trainBp + validationBp > 10_000", "", [C.f05]),
  m("N4-X31", "a non-UUID dataset id is sent", FLOWS, "if (!UUID.test(datasetId))", "if (!datasetId)", [C.f05]),
  m("N4-X32", "a derivation without a parent is sent", FLOWS, "if (base === null && add.length === 0) return", "if (false) return", [C.f05]),
  m("N4-X33", "an export lifetime is unbounded", FLOWS, 'integer(form, "ttl_s", 1, MAX_TTL_S)', 'integer(form, "ttl_s", 0, 2 ** 40)', [C.f06]),
  m("N4-X34", "a refusal loses its leaks and report", FLOWS, "leaks: f.leaks, report: f.report }) as const;", "}) as const;", [C.f07]),
  m("N4-X35", "a refusal is shown without saying what failed", FLOWS, "message: `${FAILURE_COPY[f.error]} ${f.detail}`", "message: f.detail", [C.f07]),
  m("N4-X36", "the datasets page renders without the guard", `${D}/page.tsx`, "const workspace = await requireProviderWorkspace();", 'const workspace = { providerId: "", providerName: "", role: "developer" as const };', [C.b01]),
  m("N4-X37", "the export action runs without the guard", `${D}/actions.ts`, "export async function exportAction(_: ActionState<ExportRecord>, form: FormData): Promise<ActionState<ExportRecord>> {\n  const workspace = await requireProviderWorkspace();",
    'export async function exportAction(_: ActionState<ExportRecord>, form: FormData): Promise<ActionState<ExportRecord>> {\n  const workspace = { providerId: "", providerName: "", role: "developer" as const };', [C.b01]),
  m("N4-X38", "an export part is served without the guard", `${D}/exports/[id]/[part]/route.ts`, "const workspace = await requireProviderWorkspace();", 'const workspace = { providerId: "" };', [C.b01]),
  m("N4-X39", "the version page renders without the guard", `${D}/[ref]/page.tsx`, "const workspace = await requireProviderWorkspace();", 'const workspace = { providerId: "", providerName: "", role: "developer" as const };', [C.b01]),
  m("N4-X45", "the derivation imports the read-back strictness (any version, either case)", FLOWS, "import { UUID_RE as UUID }", "import { UUID_ANY_RE as UUID }", [C.f08]),
];

// N4 and L1 cases are counted; only N4's must each be named (L1's are its own runner's).
process.exit(await runMutants({ suite: SUITE, prefix: "(?:N4|L1)", named: "N4", mutants: MUTANTS }));
