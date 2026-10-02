#!/usr/bin/env node
// UX-03 / AP-09 09d mutant runner (R32) on the shared Lab harness (tests/l/shell/harness.mjs): each
// operate decision is one edit that an OP case it names must fail by assertion. The suite renders the
// real pages statically and lays them out in Chromium (render.ts), so a run takes a few minutes.
// Usage: node tests/ux/operate/run-mutants.mjs [--only ID,ID]
import { m, runMutants } from "../../l/shell/harness.mjs";

const SUITE = ["shell", "view", "pages", "actions", "wizard", "journeys"].map((f) => `tests/ux/operate/${f}.test.ts`);
const P = "app/(provider)";
const LAYOUT = `${P}/layout.tsx`;
const NAV = `${P}/nav.tsx`;
const CSS = `${P}/operate.module.css`;
const FORM = "lib/auth/sign-in-form.tsx";
const VIEW = "lib/services/control/view.ts";
const OVERVIEW = `${P}/overview/page.tsx`;
const MODELS = `${P}/models/page.tsx`;
const MODEL_ACTIONS = `${P}/models/actions.ts`;
const REVISION = `${P}/models/revision-form.tsx`;
const WIZARD = `${P}/models/new/wizard.ts`;
const API = `${P}/models/new/api.ts`;
const NEW_ACTIONS = `${P}/models/new/actions.ts`;
const NEW_PAGE = `${P}/models/new/page.tsx`;
const DEPLOY = `${P}/deployments/page.tsx`;
const SETTINGS = `${P}/settings/page.tsx`;

const C = {
  s01: "OP-S01 the shell groups Operate and Improve, marks only the current page and names the workspace and role",
  s02: "OP-S02 the closed mobile menu keeps the sidebar out of the keyboard order; nothing scrolls sideways at any width",
  s03: "OP-S03 the sign-in card explains provider access, labels its fields and offers no guessed recovery page",
  s04: "OP-S04 no workspace, choose-workspace and unavailable are distinct states",
  v01: "OP-V01 the setup stages come from the records: a model, never a verified deployment from a record, a publication request",
  v02: "OP-V02 counts are the records' own, and a failed read is not available, never zero",
  v03: "OP-V03 measured traffic: no samples says so, a missing p95 is not available, and the evidence time is the window's",
  v04: "OP-V04 a revision registration is checked field by field with the existing rules, and every outcome keeps the record or says it is unconfirmed",
  v05: "OP-V05 a deployment record is registered, not healthy; a recorded smoke never verifies an engine; publication and the operator's decision are separate facts",
  v06: "OP-V06 a dev smoke is never offered until readiness checks exist; publication only to an administrator after a recorded pass",
  p01: "OP-P01 overview: an empty workspace has one obvious next action; a viewer is told why it has none",
  p02: "OP-P02 overview: control records and measured traffic load and fail apart; neither failure becomes a zero",
  p03: "OP-P03 models: a developer gets Add model and the revision form for imported models only; a viewer gets neither; an empty workspace is pointed at Add model",
  p04: "OP-P04 the revision fields keep the user's values and tie each error to its field",
  p05: "OP-P05 add model: without the import service, or for a viewer, no form is offered - only the actual prerequisite",
  p06: "OP-P06 add model: a project form with a stable operation key, then repository import and browser upload for that project",
  p07: "OP-P07 add model: verification shows the operation's own state; only a succeeded one naming its artifact reads as verified",
  p08: "OP-P08 add model: serving setup only for a compatible verified artifact; a created revision is not a deployment and no readiness is claimed",
  p09: "OP-P09 deployments: records read as registered; no dev smoke is offered; only an administrator can request publication, with a confirmation summary",
  p10: "OP-P10 deployments: a failed proposal read keeps the records and withholds the request; a failed record read is unavailable, never empty",
  p11: "OP-P11 settings: the workspace, the role in words with what it allows, customer content apart from every role, services not verified here; no invented controls",
  a01: "OP-A01 a revision registers as the session's workspace and role, whatever the form claims, and returns the record",
  a02: "OP-A02 a viewer is refused before the control service is asked; invalid values come back with the input and are never sent",
  a03: "OP-A03 an unanswered registration is not confirmed; the service's refusal is its fixed reason",
  a04: "OP-A04 a model project is created as the session's workspace with the form's key; a viewer or a stale form is refused before the API is asked",
  a05: "OP-A05 an import sends the pinned source, a secret reference and the manifest; a bad manifest stays in the form and is never sent; the reference is never echoed",
  a06: "OP-A06 the browser upload's three calls refuse a viewer and malformed arguments, and pass the session's workspace and key",
  a07: "OP-A07 a serving revision is created for the verified artifact; an unsupported one comes back with the server's reasons",
  w01: "OP-W01 the URL names a step only through validated ids, each after its project",
  w02: "OP-W02 an operation reads as its actual state: verified only when it succeeded and names its artifact",
  w03: "OP-W03 a pasted manifest is a non-empty list of whole file entries, or says what is wrong",
  w04: "OP-W04 a refusal is safe copy: an unanswered mutation is not confirmed, a stale key or form is a conflict, field errors are listed",
  w05: "OP-W05 every call is the actor's workspace with the session's own token, no-store, and the form's Idempotency-Key",
  w06: "OP-W06 the port's paths, methods and the fields the wizard reads exist in the checked OpenAPI artifact",
  j01: "OP-J01 UX-T07 roles: the same routes and actions answer by the session's role; no membership reaches no page",
  j02: "OP-J02 UX-T08 registration: errors keep the input, the receipt is the record, a recorded smoke is not readiness, an administrator only requests",
  j03: "OP-J03 UX-T09 partial services: each failed read keeps the page's heading and a reachable Try again, and is never a zero, an empty list or healthy",
};

const MUTANTS = [
  // the shell and access (L-01)
  m("OP-X01", "every nav link reads as the current page", NAV, "const current = path === href || path.startsWith(`${href}/`);", "const current = true;", [C.s01]),
  m("OP-X02", "the sidebar stays in the mobile keyboard order", CSS, "  .sidebar {\n    display: none;\n  }", "  .sidebar {\n    display: block;\n  }", [C.s02]),
  m("OP-X03", "the top strip does not name the role", LAYOUT, "<Badge>{ROLE_LABEL[access.workspace.role]}</Badge>", "", [C.s01]),
  m("OP-X04", "the sign-in card guesses a recovery page", LAYOUT, "        <SignInForm />\n        <p>{APP_HINT}</p>", '        <SignInForm />\n        <a href="/reset-password">Forgot password?</a>\n        <p>{APP_HINT}</p>', [C.s03]),
  m("OP-X05", "the email field is not the username for password managers", FORM, 'autoComplete="username"', 'autoComplete="email"', [C.s03]),
  m("OP-X06", "an access-check failure reads as no workspace", LAYOUT, '{access.kind === "denied" ? (', '{access.kind !== "signed-out" ? (', [C.s04]),
  m("OP-X07", "a workspace row's button does not name its workspace", LAYOUT, " aria-label={`Open workspace ${w.providerName}`}", "", [C.s04]),
  m("OP-X08", "the skip link has no target", LAYOUT, '<main id="lab-main"', "<main", [C.s01]),
  // overview (L-02)
  m("OP-X09", "a failed read is 'not started'", VIEW, '!r.ok ? "unknown"', '!r.ok ? "todo"', [C.v01, C.p02]),
  m("OP-X10", "a record completes the private-deployment stage", VIEW, '      state: "todo",\n      detail: "Serving readiness', '      state: "done",\n      detail: "Serving readiness', [C.v01]),
  m("OP-X11", "any proposal completes the publication stage", VIEW, 'rows.some((p) => p.kind === "publish")', "rows.length > 0", [C.v01]),
  m("OP-X12", "a failed count reads zero", VIEW, "(r.ok ? String(r.value.filter(keep).length) : null)", '(r.ok ? String(r.value.filter(keep).length) : "0")', [C.v02, C.p02]),
  m("OP-X13", "retired records are counted as registered", VIEW, 'value: n(deployments, (d) => d.state === "active") }', "value: n(deployments, () => true) }", [C.v02]),
  m("OP-X14", "an idle window reads as measured", VIEW, "measured: aggregates[i].requests > 0,", "measured: true,", [C.v03]),
  m("OP-X15", "a missing p95 is not said to be unavailable", VIEW, 'aggregates[i].p95LatencyMs === null ? "Not available" : row.p95', "row.p95", [C.v03]),
  m("OP-X16", "observed-through is the earliest window", VIEW, "a.windowEnd > latest", "a.windowEnd < latest", [C.v03]),
  m("OP-X17", "a traffic failure takes the whole overview down", OVERVIEW, '  const mayAdd = holds(workspace.role, "manage_dev_deployment");', '  const mayAdd = holds(workspace.role, "manage_dev_deployment");\n  if (!aggregates.ok) return <p role="alert">Control records could not be read.</p>;', [C.p02, C.j03]),
  m("OP-X18", "a viewer is offered Add model", OVERVIEW, "actions={mayAdd ?", "actions={true ?", [C.p01, C.j01]),
  // models and the legacy revision form (L-03)
  m("OP-X19", "the revision form shows in a workspace with no imported model", MODELS, "{mayManage && models.ok && models.value.length > 0 && (", "{mayManage && models.ok && (", [C.p03]),
  m("OP-X20", "a viewer gets the revision form", MODELS, "{mayManage && models.ok && models.value.length > 0 && (", "{models.ok && models.value.length > 0 && (", [C.p03, C.j01]),
  m("OP-X21", "registration skips the role check", MODEL_ACTIONS, '  if (!holds(w.role, "manage_dev_deployment")) return { values, errors: {}, outcome: { kind: "refused", message: REFUSAL_COPY.denied } };\n', "", [C.a02, C.j01]),
  m("OP-X22", "invalid values are sent", MODEL_ACTIONS, "  if (Object.keys(errors).length > 0) return { values, errors, outcome: null };\n", "", [C.a02, C.j02]),
  m("OP-X23", "registration acts as the form's workspace", MODEL_ACTIONS, "await controlPort().register(w, values)", 'await controlPort().register({ ...w, providerId: String(data.get("providerId") ?? w.providerId) }, values)', [C.a01]),
  m("OP-X24", "an unanswered registration reads as refused", VIEW, 'if (result.reason === "unavailable")', "if (false)", [C.v04, C.a03]),
  m("OP-X25", "any digest passes", VIEW, "artifactDigest: [DIGEST_RE,", "artifactDigest: [/./,", [C.v04, C.a02, C.j02]),
  m("OP-X26", "a model name has no length bound", VIEW, "name: [/^[a-z0-9][a-z0-9-]{0,62}$/,", "name: [/^[a-z0-9][a-z0-9-]*$/,", [C.v04]),
  m("OP-X27", "a refused field loses the input", REVISION, "defaultValue={values.artifactDigest}", 'defaultValue=""', [C.p04]),
  m("OP-X28", "one field's error marks another", REVISION, "error={errors.name}", "error={errors.name ?? errors.artifactDigest}", [C.p04]),
  // the Add model wizard (09d)
  m("OP-X29", "any URL value names a record", WIZARD, 'typeof v === "string" && ID.test(v) ? v : undefined', 'typeof v === "string" ? v : undefined', [C.w01]),
  m("OP-X30", "a step is reached without its project", WIZARD, '  if (q.project === undefined) return "project";\n', "", [C.w01]),
  m("OP-X31", "a succeeded operation without an artifact reads as verified", WIZARD, "return op.resource_id\n        ?", "return true\n        ?", [C.w02, C.p07]),
  m("OP-X32", "a cancellation request reads as an outcome", WIZARD, 'label: "Cancellation requested", terminal: false,', 'label: "Cancellation requested", terminal: true,', [C.w02]),
  m("OP-X33", "a malformed manifest entry passes", WIZARD, "if (bad !== -1) return", "if (false) return", [C.w03]),
  m("OP-X34", "an unanswered mutation reads as unavailable, not unconfirmed", WIZARD, "return { fields, message: mutation\n      ?", "return { fields, message: false\n      ?", [C.w04, C.a04]),
  m("OP-X35", "a conflict is a generic refusal", WIZARD, "if (status === 409)", "if (status === 499)", [C.w04]),
  m("OP-X36", "a query value overrides the actor's workspace", API, "query: { ...(init.query ?? {}), provider_org_id: actor.providerId }", "query: { provider_org_id: actor.providerId, ...(init.query ?? {}) }", [C.w05]),
  m("OP-X37", "the port is built without a Lab config", API, "if (config === null) return null;", "if (false) return null;", [C.w05]),
  m("OP-X38", "the port declares an operation the API does not have", API, '"/lab/v1/artifacts/{artifact_id}": { get: Get<Artifact> };', '"/lab/v1/artifacts/{artifact_id}": { get: Get<Artifact>; post: Get<Artifact> };', [C.w06]),
  m("OP-X39", "the wizard's actions skip the role check", NEW_ACTIONS, '  if (!holds(w.role, "manage_dev_deployment")) return refuse("Your role in this workspace does not allow that.");\n  if (!UUID_RE', "  if (!UUID_RE", [C.a04, C.a06, C.a07, C.j01]),
  m("OP-X40", "a stale form's key is accepted", NEW_ACTIONS, "if (!UUID_RE.test(key)) return refuse(", "if (false) return refuse(", [C.a04, C.a06]),
  m("OP-X41", "the secret reference is echoed back", NEW_ACTIONS, 'const values = read(data, ["host", "repo", "commit", "manifest"]);', 'const values = read(data, ["host", "repo", "commit", "manifest", "secretRef"]);', [C.a05]),
  m("OP-X42", "a refused manifest loses the input", NEW_ACTIONS, 'if ("error" in manifest) return { ...refuse(manifest.error), values };', 'if ("error" in manifest) return { ...refuse(manifest.error), values: {} };', [C.a05]),
  m("OP-X43", "the project is created without the form's key", NEW_ACTIONS, '{ body: { name: values.name, slug: values.slug, description: values.description }, idempotencyKey: text(data, "idempotencyKey")! }', "{ body: { name: values.name, slug: values.slug, description: values.description } }", [C.a04]),
  m("OP-X44", "malformed upload arguments are sent", NEW_ACTIONS, 'if (typeof projectId !== "string" || !ID.test(projectId) || !Array.isArray(files) || files.length === 0)', "if (false)", [C.a06]),
  m("OP-X45", "the revision step loses its artifact", NEW_ACTIONS, "redirect(step({ project, artifact, revision: r.data.serving_version_id }));", "redirect(step({ project, revision: r.data.serving_version_id }));", [C.a07]),
  m("OP-X46", "a viewer gets the wizard's forms", NEW_PAGE, 'if (!holds(workspace.role, "manage_dev_deployment"))\n    return', "if (false)\n    return", [C.p05, C.j01]),
  m("OP-X47", "an unread operation reads as failed", NEW_PAGE, 'if (!op.ok) body = unread(op, "this verification");', "if (!op.ok) body = <p>Verification failed</p>;", [C.p07]),
  m("OP-X48", "a running operation offers no re-check", NEW_PAGE, "{!view.terminal && back(", "{false && back(", [C.p07]),
  m("OP-X49", "an unsupported artifact is offered serving setup", NEW_PAGE, "{a.compatibility.supported ? (", "{true ? (", [C.p08]),
  m("OP-X50", "a project of another workspace falls back to the first", NEW_PAGE, "const project = projects.data.data.find((p) => p.project_id === q.project);", "const project = projects.data.data.find((p) => p.project_id === q.project) ?? projects.data.data[0];", [C.p06]),
  m("OP-X64", "Try again names an absent id as 'undefined'", NEW_PAGE, ".filter((e): e is [string, string] => e[1] !== undefined)", "", [C.p07]),
  m("OP-X51", "a created revision claims to serve", NEW_PAGE, "title=\"Serving readiness can't be verified here yet\"", 'title="Ready to serve"', [C.p08]),
  // deployments (L-04)
  m("OP-X52", "a dev smoke is offered before AP-05", VIEW, 'row.actions.filter((a) => a !== "smoke")', "row.actions", [C.v06]),
  m("OP-X53", "an active record reads as healthy", VIEW, 'active: { tone: "info", label: "Registered · active record" }', 'active: { tone: "success", label: "Healthy" }', [C.v05, C.p09, C.j02]),
  m("OP-X54", "a recorded smoke reads as engine readiness", VIEW, '{ stage: "Private smoke on this engine and revision", ...NOT_HERE },', '{ stage: "Private smoke on this engine and revision", status: d.smoke === "passed" ? "Ready" : "Not verified here", tone: "neutral" as const },', [C.v05]),
  m("OP-X55", "another record's request is shown as this one's", VIEW, "proposals?.filter((p) => p.deploymentRevisionId === d.deploymentRevisionId).at(-1)", "proposals?.at(-1)", [C.v05]),
  m("OP-X56", "an unread decision reads as none", VIEW, "...(proposals === null ? UNREAD : latest ? DECISION[latest.state]", "...(latest ? DECISION[latest.state]", [C.v05]),
  m("OP-X57", "a request is offered without knowing what is pending", DEPLOY, "const actions = known === null ? [] : operateActions(row);", "const actions = operateActions(row);", [C.p10]),
  m("OP-X58", "a developer is not told who may request publication", DEPLOY, "{openDev && !mayPropose && <p", "{false && <p", [C.p09]),
  m("OP-X59", "the confirmation proposes a rollback", DEPLOY, '<input type="hidden" name="kind" value="publish" />', '<input type="hidden" name="kind" value="rollback" />', [C.p09]),
  m("OP-X60", "a recorded smoke is said to verify the engine", VIEW, "The record names no engine, so it does not verify serving readiness.", "The engine is verified.", [C.v05, C.j02]),
  // settings (L-06)
  m("OP-X61", "a role grants customer content", SETTINGS, "<dt>Read customer content</dt><dd>Not part of any role</dd>", '<dt>Read customer content</dt><dd>{holds(workspace.role, "manage_members") ? "Yes" : "No"}</dd>', [C.p11, C.j01]),
  m("OP-X62", "every capability reads as held", SETTINGS, '{holds(workspace.role, c) ? "Yes" : "No"}', '{"Yes"}', [C.p11]),
  m("OP-X63", "services are claimed available", SETTINGS, "<dt>{name}</dt><dd>Not yet verified here</dd>", "<dt>{name}</dt><dd>Available</dd>", [C.p11]),
];

process.exit(await runMutants({ suite: SUITE, prefix: "OP", mutants: MUTANTS }));
