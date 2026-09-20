export type Model = {
  id: string;
  name: string;
  provider: string;
  description: string;
  status: "live" | "coming_soon" | "retired";
  base_url: string;
  served_model: string;
  input_usd_per_m: number;
  output_usd_per_m: number;
  cache_usd_per_m: number | null;
  context_tokens: number;
  max_output_tokens: number | null;
  input_modalities: string[];
  output_modalities: string[];
  limits: Record<string, number>;
  snippets: Partial<Record<Language, string>>;
  sort: number;
};

export type Language = "curl" | "python" | "javascript";

export type ApiKey = {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

export type LedgerRow = {
  id: string;
  delta_usd: number;
  kind: "grant" | "purchase" | "usage" | "adjustment";
  reason: string | null;
  ref: string | null;
  created_at: string;
};

/** Return shape of the org_usage_summary(p_org, p_from, p_to, p_key) SQL function. */
export type UsageSummary = {
  requests: number;
  error_requests: number;
  avg_rps: number;
  ttft_p50_ms: number | null;
  tok_s_p50: number | null;
  latency_p50_ms: number | null;
  cache_hit_ratio: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
};

/** Return shape of org_usage_daily(...). */
export type UsageDay = {
  day: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
};
