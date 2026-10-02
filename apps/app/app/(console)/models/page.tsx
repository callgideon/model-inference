import Link from "next/link";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { displayCredit } from "@/lib/contracts/v2/money-units";
import type { PublishedModel } from "@/lib/contracts/v2/published-model";
import { dateTime } from "@/lib/format";
import { consumerSession } from "@/lib/services/server";
import { CreateKeyDialog } from "../api-keys/create-key-dialog";
import { keysPageModel } from "../api-keys/view-model";
import { CATALOG_EMPTY, CATALOG_UNAVAILABLE, PROVISIONAL_NOTE, requestFacts, videoFacts } from "../docs/content";
import { aliasResolutions, apiBaseUrl, loadCatalog, priceView } from "./catalog";
import { firstCallModel } from "./first-call";
import { FirstCallPanel } from "./first-call-panel";

export const metadata = { title: "Models · infrx" };

export default async function ModelsPage() {
  const catalog = await loadCatalog(apiBaseUrl(process.env));
  if (catalog.status !== "ok") {
    console.warn(`models: published catalog unavailable (${catalog.reason})`);
    return (
      <>
        <PageHeader title="Models" />
        <p role="status" className="text-sm text-muted-foreground">
          {CATALOG_UNAVAILABLE}{" "}
          <a href="/models" className="underline underline-offset-4">
            Try again
          </a>
        </p>
      </>
    );
  }

  // UX-04: the first-call guide reads only C0's scoped port (the account's own keys and newest requests).
  const { context, reads } = await consumerSession();
  const keys = keysPageModel(context, reads === null ? null : await reads.keys());
  const guide = firstCallModel(keys, reads === null ? null : await reads.requests({}));
  const callable = catalog.models.find((m) => m.availability === "available") ?? catalog.models[0];

  return (
    <>
      <PageHeader title="Models" subtitle="Set up a call, then see what each published model accepts and costs." />
      {callable ? (
        <FirstCallPanel
          guide={guide}
          model={callable}
          baseUrl={catalog.baseUrl}
          createKey={guide.canCreate ? <CreateKeyDialog /> : null}
        />
      ) : null}
      <div className="space-y-4">
        {catalog.models.map((model) => (
          <ModelCard key={model.model_revision} model={model} published={catalog.models} />
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

/** "video + text -> text", from the record's modalities; a video input is always a finished clip. */
function capabilitySummary(model: PublishedModel): string {
  const finished = model.capability.video && !model.capability.video.live_stream;
  const inputs = model.capability.input_modalities.map((m) => (m === "video" && finished ? "finished video" : m));
  const modes = model.capability.execution_modes;
  const extras = [modes.includes("stream") ? "streamed output" : null, modes.includes("async") ? "async jobs" : null].filter(Boolean);
  return `${inputs.join(" + ")} → ${model.capability.output_modalities.join(" + ")}${extras.length ? `; ${extras.join(", ")}` : ""}`;
}

function ModelCard({ model, published }: { model: PublishedModel; published: PublishedModel[] }) {
  const price = priceView(model);
  const available = model.availability === "available";
  return (
    <Card>
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-heading text-base font-medium">
            <code className="font-mono">{model.id}</code>
          </h2>
          <Badge variant={available ? "default" : "secondary"}>{available ? "Accepting requests" : "Not accepting requests"}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">{capabilitySummary(model)}</p>
        <p className="text-xs text-muted-foreground">
          Catalog listing as of {dateTime(model.availability_as_of)}; a listing is not a live health check.
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
            </div>
          )}
        </section>

        <div className="flex flex-wrap gap-4">
          <a href="#quickstart" className="font-medium underline underline-offset-4">
            Set up a call
          </a>
          <Link href="/docs" className="underline underline-offset-4">
            API reference
          </Link>
        </div>

        <details>
          <summary className="cursor-pointer text-muted-foreground">Limits and serving details</summary>
          <div className="space-y-4 pt-3">
            <section aria-label="What it accepts">
              <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                {[...videoFacts(model.capability), ...requestFacts(model.capability)].map((fact) => (
                  <li key={fact}>{fact}</li>
                ))}
              </ul>
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
            <p className="text-xs text-muted-foreground">
              Serving revision <code className="font-mono text-foreground">{model.model_revision}</code> (weights{" "}
              <code className="font-mono">{model.serving.model_repo}</code> at{" "}
              <code className="font-mono">{model.serving.model_commit.slice(0, 12)}</code>, {model.serving.precision}),
              published by {model.owned_by}; {price.unit === "CREDIT" ? "rate card" : "price version"}{" "}
              <code className="font-mono">{price.version}</code>.
            </p>
          </div>
        </details>
      </CardContent>
    </Card>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="tabular-nums">{value}</div>
    </div>
  );
}
