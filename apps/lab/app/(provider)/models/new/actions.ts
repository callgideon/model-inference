"use server";
// AP-09 09d: the Add model wizard's actions, thin over AP-04 (api.ts). Each one is the session's
// workspace and role (requireProviderWorkspace), refuses a role without manage_dev_deployment and a
// stale form before anything is sent, and carries the rendered form's Idempotency-Key, so a repeated
// submit after a lost answer replays the same record instead of making a second one. A failure keeps
// the person's input (never a secret reference) and names the server's field errors.
import { redirect } from "next/navigation";
import { holds, type Membership } from "@/lib/auth/access";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { text } from "@/lib/services/common";
import { UUID_RE } from "@/lib/services/shapes";
import { artifactPort, type ArtifactPort, type FileEntry } from "./api";
import { ID, parseManifest, problem, type Problem } from "./wizard";

export type FormState = (Problem & { values: Record<string, string> }) | null;
export type Answer<T> = ({ ok: true } & T) | ({ ok: false } & Problem);

const SHA = /^sha256:[0-9a-f]{64}$/;
const refuse = (message: string): Problem => ({ message, fields: [] });

/** Why nothing may be sent, or the port. The role first: a viewer learns nothing about configuration. */
function gate(w: Membership, key: string): Problem | ArtifactPort {
  if (!holds(w.role, "manage_dev_deployment")) return refuse("Your role in this workspace does not allow that.");
  if (!UUID_RE.test(key)) return refuse("This form has expired. Reload the page to start again.");
  return artifactPort() ?? refuse("The Lab's model import service isn't available in this deployment yet.");
}
const isPort = (g: Problem | ArtifactPort): g is ArtifactPort => "call" in g;
const read = (data: FormData, names: string[]) => Object.fromEntries(names.map((n) => [n, text(data, n) ?? ""]));
const step = (q: Record<string, string>) => `/models/new?${new URLSearchParams(q)}`;

export async function createProject(_previous: FormState, data: FormData): Promise<FormState> {
  const w = await requireProviderWorkspace();
  const values = read(data, ["name", "slug", "description"]);
  const api = gate(w, text(data, "idempotencyKey") ?? "");
  if (!isPort(api)) return { ...api, values };
  const r = await api.call(w, "post", "/lab/v1/control/model-projects", { body: { name: values.name, slug: values.slug, description: values.description }, idempotencyKey: text(data, "idempotencyKey")! });
  if (!r.ok) return { ...problem(r.error, true), values };
  redirect(step({ project: r.data.project_id }));
}

export async function startImport(_previous: FormState, data: FormData): Promise<FormState> {
  const w = await requireProviderWorkspace();
  const values = read(data, ["host", "repo", "commit", "manifest"]); // the secret reference is never echoed
  const project = text(data, "projectId") ?? "";
  const secretRef = text(data, "secretRef")?.trim() || null;
  const api = gate(w, text(data, "idempotencyKey") ?? "");
  if (!isPort(api)) return { ...api, values };
  if (!ID.test(project)) return { ...refuse("This form has expired. Reload the page to start again."), values };
  const manifest = parseManifest(values.manifest);
  if ("error" in manifest) return { ...refuse(manifest.error), values };
  const r = await api.call(w, "post", "/lab/v1/artifacts/imports", {
    body: { project_id: project, source: { host: values.host, repo: values.repo, commit: values.commit }, secret_ref: secretRef, files: manifest.files },
    idempotencyKey: text(data, "idempotencyKey")!,
  });
  if (!r.ok) return { ...problem(r.error, true), values };
  redirect(step({ project, operation: r.data.operation_id }));
}

/** The browser upload, step 1: the upload session for the files the browser hashed. */
export async function createUpload(projectId: string, files: FileEntry[], key: string): Promise<Answer<{ uploadId: string; manifestSha: string }>> {
  const w = await requireProviderWorkspace();
  const api = gate(w, typeof key === "string" ? key : "");
  if (!isPort(api)) return { ok: false, ...api };
  if (typeof projectId !== "string" || !ID.test(projectId) || !Array.isArray(files) || files.length === 0) return { ok: false, ...refuse("Choose the model's files and try again.") };
  const r = await api.call(w, "post", "/lab/v1/artifacts/uploads", { body: { project_id: projectId, files }, idempotencyKey: key });
  return r.ok ? { ok: true, uploadId: r.data.upload_id, manifestSha: r.data.manifest_sha256 } : { ok: false, ...problem(r.error, true) };
}

/** Step 2, per file: the scoped PUT the API grants (the browser holds no storage credential). */
export async function grantPart(uploadId: string, relativePath: string): Promise<Answer<{ url: string }>> {
  const w = await requireProviderWorkspace();
  if (!holds(w.role, "manage_dev_deployment")) return { ok: false, ...refuse("Your role in this workspace does not allow that.") };
  const api = artifactPort();
  if (api === null) return { ok: false, ...refuse("The Lab's model import service isn't available in this deployment yet.") };
  if (typeof uploadId !== "string" || !ID.test(uploadId) || typeof relativePath !== "string") return { ok: false, ...refuse("That upload is not in this workspace.") };
  const r = await api.call(w, "post", "/lab/v1/artifacts/uploads/{upload_id}/parts", { params: { upload_id: uploadId }, body: { relative_path: relativePath } });
  return r.ok ? { ok: true, url: r.data.url } : { ok: false, ...problem(r.error, true) };
}

/** Step 3: ask the server to re-hash what it received; the answer is the verification operation. */
export async function completeUpload(uploadId: string, manifestSha: string, key: string): Promise<Answer<{ operationId: string }>> {
  const w = await requireProviderWorkspace();
  const api = gate(w, typeof key === "string" ? key : "");
  if (!isPort(api)) return { ok: false, ...api };
  if (typeof uploadId !== "string" || !ID.test(uploadId) || typeof manifestSha !== "string" || !SHA.test(manifestSha)) return { ok: false, ...refuse("That upload is not in this workspace.") };
  const r = await api.call(w, "post", "/lab/v1/artifacts/uploads/{upload_id}/complete", { params: { upload_id: uploadId }, body: { manifest_sha256: manifestSha }, idempotencyKey: key });
  return r.ok ? { ok: true, operationId: r.data.operation_id } : { ok: false, ...problem(r.error, true) };
}

export async function createRevision(_previous: FormState, data: FormData): Promise<FormState> {
  const w = await requireProviderWorkspace();
  const project = text(data, "projectId") ?? "";
  const artifact = text(data, "artifactId") ?? "";
  const api = gate(w, text(data, "idempotencyKey") ?? "");
  if (!isPort(api)) return { ...api, values: {} };
  if (!ID.test(project) || !ID.test(artifact)) return { ...refuse("This form has expired. Reload the page to start again."), values: {} };
  const r = await api.call(w, "post", "/lab/v1/control/model-projects/{project_id}/revisions", {
    params: { project_id: project }, body: { artifact_id: artifact }, idempotencyKey: text(data, "idempotencyKey")!,
  });
  if (!r.ok) return { ...problem(r.error, true), values: {} };
  redirect(step({ project, artifact, revision: r.data.serving_version_id }));
}
