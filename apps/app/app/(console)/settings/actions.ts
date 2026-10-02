"use server";

/**
 * C-07: the data-use server actions. Every rule (Origin check, input allowlist, one API call as the
 * signed-in session with the form's Idempotency-Key, refresh only after an acknowledged change) is
 * `dataUseActions`' (`lib/services/data-use`, tested there); this file only supplies Next's request
 * APIs and the request's client.
 */
import { revalidatePath } from "next/cache";
import { headers } from "next/headers";
import type { Result } from "@/lib/contracts/types";
import { apiSource } from "@/lib/request-api";
import { dataUseActions, type CaptureInput, type DataGrant, type DataUse, type WithdrawInput } from "@/lib/services/data-use";

const actions = dataUseActions({
  headers,
  api: async () => (await apiSource()).api,
  revalidate: (path) => revalidatePath(path),
});

/** Set one key's capture mode (C-07). */
export async function setKeyCapture(input: CaptureInput): Promise<Result<DataUse>> {
  return actions.setCapture(input);
}

/** Withdraw one of the account's grants to a provider (allowed while suspended). */
export async function withdrawDataGrant(input: WithdrawInput): Promise<Result<DataGrant>> {
  return actions.withdrawGrant(input);
}
