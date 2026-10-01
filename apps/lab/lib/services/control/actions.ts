"use server";
// L4 control actions. The provider and role are the session's (requireProviderWorkspace), never the
// form's; a missing capability or malformed value is refused before L3 is asked; L3's own refusal
// comes back as its reason; success is a plain return to the page, which re-reads the records.
import { redirect } from "next/navigation";
import { requireProviderWorkspace } from "../../auth/guard.ts";
import { field, land } from "../common.ts";
import { DIGEST_RE as DIGEST } from "../shapes.ts";
import { controlPort } from "./port.ts";

const NAME = /^[a-z0-9][a-z0-9-]{0,62}$/;
const IDENT = /^[A-Za-z0-9._:@/+-]{1,200}$/;

export async function registerModel(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const name = field(data, "name", NAME);
  const artifactDigest = field(data, "artifactDigest", DIGEST);
  const schemaVersion = field(data, "schemaVersion", IDENT);
  const runtime = field(data, "runtime", IDENT);
  redirect(await land("/models", w, "manage_dev_deployment", ![name, artifactDigest, schemaVersion, runtime].includes(null), () =>
    controlPort().register(w, { name: name!, artifactDigest: artifactDigest!, schemaVersion: schemaVersion!, runtime: runtime! }),
  ));
}

export async function smokeDeployment(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const id = field(data, "deploymentRevisionId", IDENT);
  redirect(await land("/deployments", w, "manage_dev_deployment", id !== null, () => controlPort().smoke(w, id!)));
}

export async function proposeChange(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const kind = data.get("kind");
  const id = field(data, "deploymentRevisionId", IDENT);
  const valid = (kind === "publish" || kind === "rollback") && id !== null;
  redirect(await land("/deployments", w, "propose_publication", valid, () => controlPort().propose(w, kind as "publish" | "rollback", id!)));
}
