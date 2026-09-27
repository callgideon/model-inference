"use server";
// L4 control actions. The provider and role are the session's (requireProviderWorkspace), never the
// form's; a missing capability or malformed value is refused before L3 is asked; L3's own refusal
// comes back as its reason; success is a plain return to the page, which re-reads the records.
import { redirect } from "next/navigation";
import type { Membership } from "../../auth/access.ts";
import { requireProviderWorkspace } from "../../auth/guard.ts";
import { controlPort, holds, type Actor, type Capability, type Refusal, type Result } from "./port.ts";

const NAME = /^[a-z0-9][a-z0-9-]{0,62}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const IDENT = /^[A-Za-z0-9._:@/+-]{1,200}$/;
const field = (data: FormData, name: string, shape: RegExp) => {
  const v = data.get(name);
  return typeof v === "string" && shape.test(v) ? v : null;
};
const actor = (w: Membership): Actor => ({ providerId: w.providerId, role: w.role });

/** Refused here (role, then shape) or L3's answer; either way the page re-reads the records. */
async function land(page: string, w: Membership, capability: Capability, valid: boolean, call: () => Promise<Result<unknown>>): Promise<never> {
  const refused: Refusal | null = !holds(w.role, capability) ? "denied" : !valid ? "invalid" : null;
  const result = refused === null ? await call() : { ok: false as const, reason: refused };
  redirect(result.ok ? page : `${page}?refused=${result.reason}`);
}

export async function registerModel(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const name = field(data, "name", NAME);
  const artifactDigest = field(data, "artifactDigest", DIGEST);
  const schemaVersion = field(data, "schemaVersion", IDENT);
  const runtime = field(data, "runtime", IDENT);
  await land("/models", w, "manage_dev_deployment", ![name, artifactDigest, schemaVersion, runtime].includes(null), () =>
    controlPort().register(actor(w), { name: name!, artifactDigest: artifactDigest!, schemaVersion: schemaVersion!, runtime: runtime! }),
  );
}

export async function smokeDeployment(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const id = field(data, "deploymentRevisionId", IDENT);
  await land("/deployments", w, "manage_dev_deployment", id !== null, () => controlPort().smoke(actor(w), id!));
}

export async function proposeChange(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const kind = data.get("kind");
  const id = field(data, "deploymentRevisionId", IDENT);
  const valid = (kind === "publish" || kind === "rollback") && id !== null;
  await land("/deployments", w, "propose_publication", valid, () => controlPort().propose(actor(w), kind as "publish" | "rollback", id!));
}
