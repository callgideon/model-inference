"use server";
// B4 evaluation actions. The provider and role are the session's (requireProviderWorkspace), never the
// form's; a role without run_evaluation or a malformed value is refused before the backend is asked;
// its refusal comes back as its reason. Nothing runs here: a launch queues backend jobs and lands on
// the experiment's records, which say what actually happened.
import { redirect } from "next/navigation";
import type { Membership } from "../../auth/access.ts";
import { requireProviderWorkspace } from "../../auth/guard.ts";
import { UUID, UUID_RE as ID } from "../shapes.ts";
import { evaluationPort, holds, type Amount, type Launch, type Protocol, type Refusal, type Result, type SubscriptionRequest } from "./port.ts";

/** F3: an immutable `lab:<kind>:<provider>:<object>@sha256:<hex>` ref of the named kind. */
const ref = (kind: string) => new RegExp(`^lab:${kind}:${UUID}:${UUID}@sha256:[0-9a-f]{64}$`);
const CREDIT = /^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$/;
const SLICE = /^[A-Za-z0-9_.:-]{1,64}$/;

const text = (data: FormData, name: string) => {
  const v = data.get(name);
  return typeof v === "string" ? v : "";
};
const field = (data: FormData, name: string, shape: RegExp) => (shape.test(text(data, name)) ? text(data, name) : null);
const int = (data: FormData, name: string, min: number) => {
  const v = text(data, name);
  return /^[0-9]{1,9}$/.test(v) && Number(v) >= min ? Number(v) : null;
};
const num = (v: string) => (/^[0-9]+(\.[0-9]+)?$/.test(v) ? Number(v) : null);
/** A CREDIT amount as F3 carries it: exactly 8 decimals. */
const credit = (data: FormData, name: string): Amount | null => {
  const [whole, frac = ""] = (field(data, name, CREDIT) ?? "").split(".");
  return whole === "" ? null : { unit: "CREDIT", value: `${whole}.${frac.padEnd(8, "0")}` };
};

/** B2's predeclared thresholds (Protocol's own bounds); slices are `name margin min_cases` lines. */
function protocol(data: FormData): Protocol | null {
  const confidence = num(text(data, "confidence"));
  const margin = num(text(data, "margin"));
  const min_cases = int(data, "min_cases", 2);
  const metric_source = text(data, "metric_source");
  const required_slices: Protocol["required_slices"] = {};
  for (const line of text(data, "slices").split("\n").filter((l) => l.trim() !== "")) {
    const [name, m, min, ...rest] = line.trim().split(/\s+/);
    const rule = { margin: num(m ?? ""), min_cases: /^[0-9]{1,9}$/.test(min ?? "") ? Number(min) : 0 };
    if (rest.length > 0 || !SLICE.test(name) || name in required_slices || rule.margin === null || rule.min_cases < 2) return null;
    required_slices[name] = { margin: rule.margin, min_cases: rule.min_cases };
  }
  if (confidence === null || !(confidence > 0.5 && confidence < 1) || margin === null || min_cases === null) return null;
  if (metric_source !== "deterministic_metric" && metric_source !== "teacher_judgment") return null;
  return { confidence, margin, min_cases, metric_source, required_slices };
}

function parseLaunch(data: FormData): Launch | null {
  const l = {
    experiment_id: field(data, "experiment_id", ID), dataset_ref: field(data, "dataset_ref", ref("dataset")),
    harness_ref: field(data, "harness_ref", ref("harness")), evaluator_ref: field(data, "evaluator_ref", ref("evaluator")),
    baseline_serving_ref: field(data, "baseline_serving_ref", ref("serving")), candidate_serving_ref: field(data, "candidate_serving_ref", ref("serving")),
    seed: int(data, "seed", 0), max_cases: int(data, "max_cases", 1), run_limit: credit(data, "run_limit"), protocol: protocol(data),
  };
  return Object.values(l).includes(null) ? null : (l as Launch);
}

function parseSubscription(data: FormData): SubscriptionRequest | null {
  const policy = text(data, "policy");
  const s = {
    subscription_id: field(data, "subscription_id", ID), external_run_ref: field(data, "external_run_ref", ref("external_run")),
    dataset_ref: field(data, "dataset_ref", ref("dataset")), harness_ref: field(data, "harness_ref", ref("harness")),
    evaluator_ref: field(data, "evaluator_ref", ref("evaluator")), seed: int(data, "seed", 0), max_cases: int(data, "max_cases", 1),
    run_limit: credit(data, "run_limit"), limit: credit(data, "limit"), max_active: int(data, "max_active", 1),
    policy: policy === "latest_only" || policy === "every" ? policy : null,
  };
  return Object.values(s).includes(null) ? null : (s as SubscriptionRequest);
}


/** Refused here (role, then shape) or the backend's answer; success lands where `to` says. */
async function land<T>(page: string, w: Membership, valid: boolean, call: () => Promise<Result<T>>, to: (value: T) => string = () => page): Promise<never> {
  const refused: Refusal | null = !holds(w.role, "run_evaluation") ? "denied" : !valid ? "invalid" : null;
  const result = refused === null ? await call() : { ok: false as const, reason: refused };
  redirect(result.ok ? to(result.value) : `${page}?refused=${result.reason}`);
}

export async function launchExperiment(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const launch = parseLaunch(data);
  await land("/evaluations", w, launch !== null, () => evaluationPort().launch(w, launch!), (e) => `/experiments/${e.experiment_id}`);
}

export async function cancelRun(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const id = field(data, "run_id", ID);
  await land("/evaluations", w, id !== null, () => evaluationPort().cancel(w, id!));
}

export async function subscribeCheckpoints(data: FormData): Promise<void> {
  const w = await requireProviderWorkspace();
  const request = parseSubscription(data);
  await land("/evaluations/checkpoints", w, request !== null, () => evaluationPort().subscribe(w, request!));
}
