// AP-09 09d: the Add model wizard's port onto AP-04 (/lab/v1/control/model-projects, /lab/v1/artifacts/*,
// /lab/v1/operations/{id}) over the generated clients' transport (@infrx/api-client/transport): the
// signed-in user's own session token, the actor's workspace as `provider_org_id`, no-store, an
// Idempotency-Key on every mutation, R270 errors typed. Server-only; the Lab holds no key.
//
// ponytail: `ArtifactPaths` restates the AP-04 operations of apps/infrx-api/openapi/consumer.json (the
// gateway composition) because the Lab client (@infrx/api-client/lab, generated from lab-control.json)
// does not carry them until AP-04 mounts on the Lab unit (WR-UX03-2); then this becomes
// `import type { paths } from "@infrx/api-client/lab"`. tests/ux/operate/api.test.ts pins it to the artifact.
import { createClient, type CallInit, type Result } from "@infrx/api-client/transport";
import type { Actor } from "@/lib/auth/access";
import { labConfig } from "@/lib/auth/config";
import { sessionToken } from "@/lib/auth/session";

type Json<T> = { content: { "application/json": T } };
type Query = { provider_org_id?: string; limit?: number; cursor?: string };
type Get<R> = { parameters: { query?: Query }; responses: { 200: Json<R> } };
type Post<B, R, S extends 200 | 201 | 202 = 201> = { parameters: { query?: Query }; requestBody: Json<B>; responses: { [K in S]: Json<R> } };

export type FileEntry = { relative_path: string; bytes: number; sha256: string; media_type: string };
export type FieldError = { field: string; code: string; message: string };
export type Project = { project_id: string; slug: string; name: string; description: string; public_model_id?: string | null; created_at: string };
export type OperationDoc = {
  operation_id: string; kind: string; state: "queued" | "running" | "succeeded" | "failed" | "cancel_requested" | "cancelled";
  phase?: string | null; resource_id?: string | null; created_at: string; updated_at: string; retry_after_s?: number | null;
  error?: { code: string; message: string; request_id: string; retryable: boolean; field_errors?: FieldError[] } | null;
};
export type Upload = { upload_id: string; project_id: string; manifest_sha256: string; state: string; expires_at: string };
export type PartGrant = { relative_path: string; method?: string; url: string; expires_s: number };
export type Artifact = {
  artifact_id: string; project_id: string; source: "upload" | "import" | "adopted"; source_repo?: string | null; source_commit?: string | null;
  files: FileEntry[]; manifest_sha256: string; verified_at: string; compatibility: { supported: boolean; profile?: string; reasons?: FieldError[] };
};
export type RevisionDoc = {
  serving_version_id: string; project_id: string; artifact_id: string; public_model_id: string; revision_label: string;
  model_revision: string; created_at: string;
};
type Page<T> = { data: T[]; next_cursor: string | null };
type Card = { summary?: string; license?: string; task?: string };

export type ArtifactPaths = {
  "/lab/v1/control/model-projects": { get: Get<Page<Project>>; post: Post<{ name: string; slug: string; description?: string }, Project> };
  "/lab/v1/control/model-projects/{project_id}/revisions": {
    get: Get<Page<RevisionDoc>>;
    post: Post<{ artifact_id: string; prompt_harness_ref?: string; preprocessor_profile_version?: string }, RevisionDoc>;
  };
  "/lab/v1/artifacts/uploads": { post: Post<{ project_id: string; files: FileEntry[]; card?: Card }, Upload> };
  "/lab/v1/artifacts/uploads/{upload_id}/parts": { post: Post<{ relative_path: string }, PartGrant, 200> };
  "/lab/v1/artifacts/uploads/{upload_id}/complete": { post: Post<{ manifest_sha256: string }, OperationDoc, 202> };
  "/lab/v1/artifacts/imports": {
    post: Post<{ project_id: string; source: { host: string; repo: string; commit: string }; secret_ref?: string | null; files: FileEntry[]; card?: Card }, OperationDoc, 202>;
  };
  "/lab/v1/artifacts/{artifact_id}": { get: Get<Artifact> };
  "/lab/v1/operations/{operation_id}": { get: Get<OperationDoc> };
};

type Paths = keyof ArtifactPaths;
type Method<P extends Paths> = keyof ArtifactPaths[P] & ("get" | "post");
type Answer<P extends Paths, M extends Method<P>> = ArtifactPaths[P][M] extends { responses: infer R } ? R[keyof R] extends Json<infer T> ? T : never : never;
type Init<P extends Paths, M extends Method<P>> = CallInit<ArtifactPaths[P][M]>;

export type ArtifactPort = {
  call<P extends Paths, M extends Method<P>>(actor: Pick<Actor, "providerId">, method: M, path: P, init?: Init<P, M>): Promise<Result<Answer<P, M>>>;
};

/** Every call as the actor's workspace (`provider_org_id`), whatever else the query holds. */
export function artifactClient(options: Parameters<typeof createClient>[0]): ArtifactPort {
  const client = createClient<ArtifactPaths>(options);
  return {
    call: (actor, method, path, init = {}) =>
      client.call(method as never, path, { ...init, query: { ...(init.query ?? {}), provider_org_id: actor.providerId } } as never) as never,
  };
}

/** The port when the Lab API is configured (LAB_API_URL and a Lab config), else null: the wizard says
 *  unavailable. Without a session token nothing is sent (the call is unavailable, as lib/services/http.ts). */
export function artifactPort(env: Record<string, string | undefined> = process.env): ArtifactPort | null {
  const config = labConfig(env);
  if (config === null) return null;
  return {
    call: async (actor, method, path, init) => {
      const bearer = await sessionToken().catch(() => null);
      if (!bearer) return { ok: false, requestId: "", error: { kind: "unavailable", status: null, reason: "network" } };
      return artifactClient({ baseUrl: config.apiUrl, session: () => ({ token: bearer }) }).call(actor, method, path, init);
    },
  };
}
