"use server";
// R4 proposals. Provider and role are the session's (requireProviderWorkspace), never the form's; a
// missing capability or malformed value is refused before the releases service is asked; its refusal
// comes back as its reason; success is a plain return to the page, which re-reads the records.
import { redirect } from "next/navigation";
import { requireProviderWorkspace } from "../../auth/guard.ts";
import { land } from "../common.ts";
import { releasesPort } from "./port.ts";

const POLICY_REF = /^lab:policy:[0-9a-f-]{36}:[0-9a-f-]{36}@sha256:[0-9a-f]{64}$/;
const FENCE = /^\d{1,15}$/;

export async function proposeRelease(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const [kind, policyRef, fence] = ["kind", "policyRef", "fence"].map((n) => data.get(n));
  const valid = (kind === "expand" || kind === "rollback") && typeof policyRef === "string" && POLICY_REF.test(policyRef)
    && typeof fence === "string" && FENCE.test(fence);
  redirect(await land("/releases", w, "propose_publication", valid, () => releasesPort().propose(w, kind as "expand" | "rollback", policyRef as string, Number(fence))));
}
