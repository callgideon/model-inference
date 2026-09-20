import { CreditsCard } from "@/components/credits-card";
import { PageHeader } from "@/components/page-header";
import { StatTile } from "@/components/stat-tile";
import { Card, CardContent } from "@/components/ui/card";
import { getCredits } from "@/lib/credits";
import { compact, money, ms, num, pct } from "@/lib/format";
import { getSession } from "@/lib/session";
import { createClient } from "@/lib/supabase/server";
import type { UsageDay, UsageSummary } from "@/lib/types";
import { resolveRange } from "./ranges";
import { UsageChart } from "./usage-chart";
import { UsageControls } from "./usage-controls";

export const metadata = { title: "Usage · infrx" };

const EMPTY: UsageSummary = {
  requests: 0,
  error_requests: 0,
  avg_rps: 0,
  ttft_p50_ms: null,
  tok_s_p50: null,
  latency_p50_ms: null,
  cache_hit_ratio: null,
  prompt_tokens: 0,
  completion_tokens: 0,
  cost_usd: 0,
};

export default async function UsagePage({ searchParams }: PageProps<"/usage">) {
  const params = await searchParams;
  const session = await getSession();
  const supabase = await createClient();

  const {
    key: rangeKey,
    from,
    to,
  } = resolveRange(typeof params.range === "string" ? params.range : undefined);
  const keyParam = typeof params.key === "string" && params.key !== "all" ? params.key : null;
  const rpcArgs = {
    p_org: session.orgId,
    p_from: from.toISOString(),
    p_to: to.toISOString(),
    p_key: keyParam,
  };

  // The summary function has no 4xx/5xx split, so ask usage_events for the two counts.
  const errorQuery = (lo: number, hi: number) => {
    let q = supabase
      .from("usage_events")
      .select("id", { count: "exact", head: true })
      .eq("org_id", session.orgId)
      .gte("created_at", from.toISOString())
      .lt("created_at", to.toISOString())
      .gte("status", lo)
      .lt("status", hi);
    if (keyParam) q = q.eq("api_key_id", keyParam);
    return q;
  };

  const [summaryRes, dailyRes, keysRes, credits, c4xx, c5xx] = await Promise.all([
    supabase.rpc("org_usage_summary", rpcArgs).maybeSingle(),
    supabase.rpc("org_usage_daily", rpcArgs),
    supabase
      .from("api_keys")
      .select("id, name")
      .eq("org_id", session.orgId)
      .order("created_at", { ascending: false }),
    getCredits(session.orgId),
    errorQuery(400, 500),
    errorQuery(500, 600),
  ]);

  const s = { ...EMPTY, ...((summaryRes.data ?? {}) as Partial<UsageSummary>) };
  const days = (dailyRes.data ?? []) as UsageDay[];
  const totalTokens = Number(s.prompt_tokens) + Number(s.completion_tokens);
  const errorRate = s.requests > 0 ? Number(s.error_requests) / Number(s.requests) : 0;

  return (
    <>
      <PageHeader
        title="Usage"
        subtitle={`Metadata only — no prompts or video are stored. Last ${rangeKey}.`}
        action={<UsageControls keys={keysRes.data ?? []} />}
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Requests" value={num(s.requests)} hint={`${money(s.cost_usd)} spent`} />
        <StatTile label="Requests / s" value={num(s.avg_rps, 2)} hint="average over the range" />
        <StatTile label="TTFT p50" value={ms(s.ttft_p50_ms)} />
        <StatTile
          label="Output tok/s p50"
          value={s.tok_s_p50 === null ? "—" : num(s.tok_s_p50, 1)}
        />
        <StatTile label="Latency p50" value={ms(s.latency_p50_ms)} />
        <StatTile
          label="Error rate"
          value={pct(errorRate)}
          hint={`${num(c4xx.count ?? 0)} 4xx · ${num(c5xx.count ?? 0)} 5xx`}
        />
        <StatTile
          label="Cache hit"
          value={s.cache_hit_ratio === null ? "—" : pct(s.cache_hit_ratio)}
        />
        <StatTile
          label="Total tokens"
          value={compact(totalTokens)}
          hint={`${num(totalTokens)} tokens`}
        />
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-[2fr_1fr]">
        <Card>
          <CardContent className="space-y-4">
            <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
              <Detail label="Input tokens" value={num(s.prompt_tokens)} />
              <Detail label="Output tokens" value={num(s.completion_tokens)} />
              <Detail label="Total" value={num(totalTokens)} />
              <Detail label="Cost" value={money(s.cost_usd)} />
            </dl>
            <UsageChart days={days} />
          </CardContent>
        </Card>
        <CreditsCard credits={credits} />
      </div>
    </>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}
