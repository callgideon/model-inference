import { PageHeader } from "@/components/page-header";
import { Snippet, type SnippetKey } from "@/components/snippet";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { money, num } from "@/lib/format";
import { getSession } from "@/lib/session";
import { createClient } from "@/lib/supabase/server";
import type { Model } from "@/lib/types";

export const metadata = { title: "Models · infrx" };

export default async function ModelsPage() {
  const session = await getSession();
  const supabase = await createClient();

  const [{ data: models }, { data: keys }] = await Promise.all([
    supabase.from("models").select("*").neq("status", "retired").order("sort"),
    supabase
      .from("api_keys")
      .select("id, name, prefix")
      .eq("org_id", session.orgId)
      .is("revoked_at", null)
      .order("created_at", { ascending: false }),
  ]);

  return (
    <>
      <PageHeader
        title="Models"
        subtitle="Specialist models behind one OpenAI-compatible API. Copy a request and run it."
      />
      <div className="space-y-4">
        {(models ?? []).map((model) => (
          <ModelCard key={model.id} model={model as Model} keys={(keys ?? []) as SnippetKey[]} />
        ))}
        {(models ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            The catalog is empty. Run the seed migration.
          </p>
        ) : null}
      </div>
    </>
  );
}

function ModelCard({ model, keys }: { model: Model; keys: SnippetKey[] }) {
  const comingSoon = model.status === "coming_soon";
  return (
    <Card>
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-heading text-base font-medium">{model.name}</h2>
          <Badge variant="outline">{model.provider}</Badge>
          <Badge variant={comingSoon ? "secondary" : "default"}>
            {comingSoon ? "Coming soon" : "Live"}
          </Badge>
          <code className="ml-auto font-mono text-xs text-muted-foreground">{model.id}</code>
        </div>
        <p className="text-sm text-muted-foreground">{model.description}</p>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-x-8 gap-y-2 rounded-lg border bg-muted/30 px-4 py-3 text-sm">
          <Price label="Input" value={model.input_usd_per_m} />
          <Price label="Output" value={model.output_usd_per_m} />
          <Price label="Cache" value={model.cache_usd_per_m} />
          <div>
            <div className="text-xs text-muted-foreground">Context</div>
            <div className="tabular-nums">{num(model.context_tokens)} tokens</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">Modalities</div>
            <div>
              {model.input_modalities.join(", ")} → {model.output_modalities.join(", ")}
            </div>
          </div>
          {comingSoon ? (
            <div className="flex items-end text-xs text-muted-foreground">
              Prices are placeholders until the model is serving.
            </div>
          ) : null}
        </div>

        <Snippet
          snippets={model.snippets}
          baseUrl={model.base_url}
          keys={keys}
          disabled={comingSoon}
        />
      </CardContent>
    </Card>
  );
}

function Price({ label, value }: { label: string; value: number | null }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label} / 1M</div>
      <div className="tabular-nums">{value === null ? "—" : money(Number(value), 2)}</div>
    </div>
  );
}
