import Link from "next/link";
import { PageHeader } from "@/components/page-header";
import { Snippet, type SnippetKey } from "@/components/snippet";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { displayCredit } from "@/lib/contracts/v2/money-units";
import type { PublishedModel } from "@/lib/contracts/v2/published-model";
import { dateTime } from "@/lib/format";
import { getSession } from "@/lib/session";
import { createClient } from "@/lib/supabase/server";
import { CATALOG_EMPTY, CATALOG_UNAVAILABLE, PROVISIONAL_NOTE, requestFacts, videoFacts } from "../docs/content";
import { buildExamples } from "../docs/examples";
import { aliasResolutions, apiBaseUrl, loadCatalog, priceView } from "./catalog";

export const metadata = { title: "Models · infrx" };

export default async function ModelsPage() {
  const catalog = await loadCatalog(apiBaseUrl(process.env));
  if (catalog.status !== "ok") {
    console.warn(`models: published catalog unavailable (${catalog.reason})`);
    return (
      <>
        <PageHeader title="Models" />
        <p role="status" className="text-sm text-muted-foreground">
          {CATALOG_UNAVAILABLE}
        </p>
      </>
    );
  }

  const session = await getSession();
  const supabase = await createClient();
  const { data: keys } = await supabase
    .from("api_keys")
    .select("id, name, prefix")
    .eq("org_id", session.orgId)
    .is("revoked_at", null)
    .order("created_at", { ascending: false });

  return (
    <>
      <PageHeader
        title="Models"
        subtitle="What each published model accepts, what it costs and how to call it. Every figure here is what the API itself enforces."
      />
      <div className="space-y-4">
        {catalog.models.map((model) => (
          <ModelCard
            key={model.model_revision}
            model={model}
            published={catalog.models}
            baseUrl={catalog.baseUrl}
            keys={(keys ?? []) as SnippetKey[]}
          />
        ))}
        {catalog.models.length === 0 ? (
          <p role="status" className="text-sm text-muted-foreground">
            {CATALOG_EMPTY}
          </p>
        ) : null}
      </div>
    </>
  );
}

function ModelCard({
  model,
  published,
  baseUrl,
  keys,
}: {
  model: PublishedModel;
  published: PublishedModel[];
  baseUrl: string;
  keys: SnippetKey[];
}) {
  const price = priceView(model);
  const available = model.availability === "available";
  const quickstart = buildExamples(model).find((e) => e.id === "video")!;
  return (
    <Card>
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-heading text-base font-medium">{model.id}</h2>
          <Badge variant="outline">{model.owned_by}</Badge>
          <Badge variant={available ? "default" : "secondary"}>{available ? "Available" : "Unavailable"}</Badge>
          <span className="ml-auto text-xs text-muted-foreground">as of {dateTime(model.availability_as_of)}</span>
        </div>
        <p className="text-sm text-muted-foreground">
          Serving revision <code className="font-mono text-foreground">{model.model_revision}</code> (weights{" "}
          <code className="font-mono">{model.serving.model_repo}</code> at{" "}
          <code className="font-mono">{model.serving.model_commit.slice(0, 12)}</code>, {model.serving.precision}).
        </p>
      </CardHeader>

      <CardContent className="space-y-4 text-sm">
        <section aria-label="Price" className="rounded-lg border bg-muted/30 px-4 py-3">
          {price.unit === "CREDIT" ? (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-x-8 gap-y-2">
                <Figure label="Input, per 1M tokens" value={displayCredit(price.input)} />
                <Figure label="Output, per 1M tokens" value={displayCredit(price.output)} />
                <Figure label="Largest hold per request" value={displayCredit(price.maxHold)} />
                <Figure label="Rate card" value={price.version} mono />
              </div>
              {price.provisional ? (
                <p className="text-xs">
                  <Badge variant="secondary">Provisional</Badge> {PROVISIONAL_NOTE}
                </p>
              ) : null}
              <p className="text-xs text-muted-foreground">
                Charged in CREDIT from your one-time grant; CREDIT has no USD exchange rate.{" "}
                <Link href="/docs#pricing" className="underline underline-offset-4">
                  What is and is not charged
                </Link>
                .
              </p>
            </div>
          ) : (
            <div className="flex flex-wrap gap-x-8 gap-y-2">
              <Figure label="Input, per 1M tokens (USD, legacy pilot)" value={`USD ${price.input}`} />
              <Figure label="Output, per 1M tokens (USD, legacy pilot)" value={`USD ${price.output}`} />
              <Figure label="Price version" value={price.version} mono />
            </div>
          )}
        </section>

        <section aria-label="Names you can send">
          <h3 className="mb-1 text-xs text-muted-foreground">Names you can send as model</h3>
          <ul className="space-y-1">
            {aliasResolutions(model, published).map((r) => (
              <li key={r.requested_model}>
                <code className="font-mono">{r.requested_model}</code>
                <span className="text-muted-foreground"> resolves to </span>
                <code className="font-mono">{r.model_revision}</code>
              </li>
            ))}
          </ul>
        </section>

        <section aria-label="What it accepts">
          <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
            {[...videoFacts(model.capability), ...requestFacts(model.capability)].map((fact) => (
              <li key={fact}>{fact}</li>
            ))}
          </ul>
        </section>

        <Snippet snippets={quickstart.snippets} baseUrl={baseUrl} keys={keys} />
        <p className="text-xs text-muted-foreground">
          Uploads, streaming, async jobs and retries:{" "}
          <Link href="/docs" className="underline underline-offset-4">
            Docs
          </Link>
          .
        </p>
      </CardContent>
    </Card>
  );
}

function Figure({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={mono ? "font-mono text-xs" : "tabular-nums"}>{value}</div>
    </div>
  );
}
