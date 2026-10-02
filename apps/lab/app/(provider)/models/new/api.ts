// AP-09 09d: the Add model wizard's port onto AP-04 (/lab/v1/control/model-projects, /lab/v1/artifacts/*,
// /lab/v1/operations/{id}) over the generated clients' transport (@infrx/api-client/transport): the
// signed-in user's own session token, the actor's workspace as `provider_org_id`, no-store, an
// Idempotency-Key on every mutation, R270 errors typed. Server-only; the Lab holds no key.
// The operations are the generated Lab client's (apps/infrx-api/openapi/lab-control.json, WR-UX03-2);
// the document types below are the fields the wizard reads (tests/ux/operate/wizard.test.ts OP-W06).
import type { paths as ArtifactPaths } from "@infrx/api-client/lab";
import { createClient, type CallInit, type BodyOf, type Result, type Success } from "@infrx/api-client/transport";
import type { Actor } from "@/lib/auth/access";
import { labConfig } from "@/lib/auth/config";
import { sessionToken } from "@/lib/auth/session";

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

type Paths = keyof ArtifactPaths;
type Method<P extends Paths> = keyof ArtifactPaths[P] & ("get" | "post");
type Answer<P extends Paths, M extends Method<P>> = Success<ArtifactPaths[P][M]>;
// openapi-typescript 7 marks a request field the API defaults as required: these AP-04 request fields have
// server defaults in lab-control.json, so the wizard may leave them out.
type Defaulted = "card" | "prompt_harness_ref" | "preprocessor_profile_version" | "profile";
type Body<B> = Omit<B, Defaulted> & Partial<Pick<B, Extract<keyof B, Defaulted>>>;
type Init<P extends Paths, M extends Method<P>> = Omit<CallInit<ArtifactPaths[P][M]>, "body"> & { body?: Body<BodyOf<ArtifactPaths[P][M]>> };

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
