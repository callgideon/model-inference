import Link from "next/link";
import { PageHeader } from "@/components/page-header";
import { Snippet, type SnippetKey } from "@/components/snippet";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { displayCredit, type Credit } from "@/lib/contracts/v2/money-units";
import { INITIAL_SIGNUP_GRANT_CREDIT } from "@/lib/contracts/v2/types";
import { getSession } from "@/lib/session";
import { createClient } from "@/lib/supabase/server";
import { apiBaseUrl, holdCredit, loadCatalog, priceView } from "../models/catalog";
import {
  CATALOG_EMPTY,
  CATALOG_UNAVAILABLE,
  chargeDisclosure,
  duration,
  ERROR_ROWS,
  PROVISIONAL_NOTE,
  requestFacts,
  retentionFacts,
  REVOCATION_COPY,
  ROUTE_ROWS,
  SUPPORT_EMAIL,
  videoFacts,
} from "./content";
import { buildExamples, CAPTION_PROMPT, EXAMPLE_MAX_TOKENS } from "./examples";

export const metadata = { title: "Docs · infrx" };

// The grounding prompt Marlin was tuned on (provider document); the caption prompt is in examples.
const GROUNDING_PROMPT = `Identify the timestamps during which "<event>" takes place. Output the time range as "From <start> to <end>." (numbers in seconds).`;

export default async function DocsPage() {
  const catalog = await loadCatalog(apiBaseUrl(process.env));
  if (catalog.status !== "ok") {
    console.warn(`docs: published catalog unavailable (${catalog.reason})`);
    return (
      <>
        <PageHeader title="Docs" />
        <p role="status" className="text-sm text-muted-foreground">
          {CATALOG_UNAVAILABLE}
        </p>
      </>
    );
  }
  const model = catalog.models[0];
  if (!model) {
    return (
      <>
        <PageHeader title="Docs" />
        <p role="status" className="text-sm text-muted-foreground">
          {CATALOG_EMPTY}
        </p>
      </>
    );
  }

  const session = await getSession();
  const supabase = await createClient();
  const { data: keyRows } = await supabase
    .from("api_keys")
    .select("id, name, prefix")
    .eq("org_id", session.orgId)
    .is("revoked_at", null)
    .order("created_at", { ascending: false });
  const keys = (keyRows ?? []) as SnippetKey[];

  const { baseUrl } = catalog;
  const price = priceView(model);
  const retention = model.retention;
  const code = (text: string) => <code className="font-mono text-foreground">{text}</code>;

  return (
    <>
      <PageHeader
        title="Docs"
        subtitle={`How to call ${model.id} with your API key: every example below is run against the API's contract in CI.`}
      />

      <div className="space-y-4">
        <Section title="Quickstart">
          <p>
            Base URL: {code(baseUrl)}. Send your key as {code("Authorization: Bearer <key>")} on every call
            except {code("GET /v1/models")}. Create a key on the{" "}
            <Link href="/api-keys" className="underline underline-offset-4">
              API Keys
            </Link>{" "}
            page; its secret is shown once, and we keep only a hash of it.
          </p>
          <p>
            The chat route follows the OpenAI Chat Completions shape for the fields listed under Limits, so an
            OpenAI client pointed at {code(`${baseUrl}/v1`)} works for plain and streamed chat. Other OpenAI
            features are not served and are refused, not ignored.
          </p>
        </Section>

        {buildExamples(model).map((example) => (
          <Section key={example.id} title={example.title} id={example.id}>
            <p>{example.blurb}</p>
            <div className="not-prose pt-1">
              <Snippet snippets={example.snippets} baseUrl={baseUrl} keys={keys} />
            </div>
          </Section>
        ))}

        <Section title="Limits" id="limits">
          <ul className="list-disc space-y-1 pl-5">
            {[...videoFacts(model.capability), ...requestFacts(model.capability)].map((fact) => (
              <li key={fact}>{fact}</li>
            ))}
          </ul>
        </Section>

        <Section title="Streaming" id="streaming">
          <p>
            With {code('"stream": true')} the answer arrives as server-sent events: chat chunks in the OpenAI
            shape, a final chunk carrying {code("usage")}, then {code("data: [DONE]")}. Frames named{" "}
            {code("event: infrx.progress")} report the job&apos;s progress and carry no text; skip them if you only
            want the answer. Each frame has an {code("id:")}; if the connection drops, the output can be replayed
            from {code("GET /v1/jobs/{handle}/events")} with {code("Last-Event-ID")} for{" "}
            {duration(retention.stream_journal_ttl_s)}.
          </p>
          <p>
            Only the output streams. The input is always a finished video file; live video input is not
            supported.
          </p>
        </Section>

        <Section title="Async jobs, retries and request IDs" id="retries">
          <p>
            {code("POST /v1/jobs")} (or {code("Prefer: respond-async")} on the chat route) answers 202 with a{" "}
            {code("job_handle")}, a {code("Location")} and a {code("Retry-After")} poll hint once the job is durably
            accepted; the job runs whether or not you stay connected.
          </p>
          <p>
            Send an {code("Idempotency-Key")} of your own (one stable key per item) with every request you might
            retry. The same key with the same body and mode answers the original job ({code("idempotency_replayed")}{" "}
            and the {code("Idempotency-Replayed")} header say so) and is never charged twice; the same key with a
            different body or mode is {code("409 idempotency_conflict")}. A key keeps answering for{" "}
            {duration(retention.idempotency_ttl_s)} after its job finishes.
          </p>
          <p>
            A {code("429")} or {code("503")} carries {code("Retry-After")} in seconds: wait that long, then retry
            with the same key. Every response carries an {code("Inference-Id")} header; quote it when you contact
            support.
          </p>
        </Section>

        <Section title="Pricing and credits" id="pricing">
          {price.unit === "CREDIT" ? (
            <CreditPricing
              disclosure={chargeDisclosure(price)}
              provisional={price.provisional}
              version={price.version}
              maxHold={price.maxHold}
              exampleHold={holdCredit(model, EXAMPLE_MAX_TOKENS)}
            />
          ) : (
            <p>
              This deployment is metered under legacy USD pilot accounting (price version {code(price.version)}):
              USD {price.input} per million input tokens and USD {price.output} per million output tokens. USD is
              never converted to or from CREDIT.
            </p>
          )}
        </Section>

        <Section title="What we store" id="retention">
          <ul className="list-disc space-y-1 pl-5">
            {retentionFacts(retention).map((fact) => (
              <li key={fact}>{fact}</li>
            ))}
          </ul>
        </Section>

        <Section title="Keys and revocation" id="keys">
          <p>{REVOCATION_COPY}</p>
        </Section>

        <Section title="Prompts" id="prompts">
          <p>Marlin was tuned on these two prompts; other phrasings work, these give the best output.</p>
          <div className="not-prose space-y-3 pt-2">
            <Prompt label="Dense captioning with timestamps" text={CAPTION_PROMPT} />
            <Prompt label="Temporal grounding (replace <event>)" text={GROUNDING_PROMPT} />
          </div>
        </Section>

        <Card>
          <CardHeader>
            <CardTitle>Routes</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-20">Method</TableHead>
                  <TableHead>Path</TableHead>
                  <TableHead>What</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {ROUTE_ROWS.map(([method, path, what]) => (
                  <TableRow key={method + path}>
                    <TableCell className="font-mono">{method}</TableCell>
                    <TableCell className="font-mono text-xs">{path}</TableCell>
                    <TableCell className="whitespace-normal text-muted-foreground">{what}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Error codes</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16">Status</TableHead>
                  <TableHead>Code</TableHead>
                  <TableHead>What to do</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {ERROR_ROWS.map(([errorCode, status, fix]) => (
                  <TableRow key={errorCode}>
                    <TableCell className="font-mono">{status}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{errorCode}</TableCell>
                    <TableCell className="whitespace-normal text-muted-foreground">{fix}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="border-t p-4 text-xs text-muted-foreground">
              Errors use the OpenAI shape{" "}
              <code className="font-mono">{`{"error": {"message", "type", "code", "param"}}`}</code>. In a stream
              that already started, a failure arrives as a final error event ({code("stream_interrupted")} or{" "}
              {code("status_unknown")}).
            </p>
          </CardContent>
        </Card>

        <Section title="Support" id="support">
          <p>
            Email{" "}
            <a href={`mailto:${SUPPORT_EMAIL}`} className="underline underline-offset-4">
              {SUPPORT_EMAIL}
            </a>{" "}
            with the {code("Inference-Id")} of the request. Never send your API key.
          </p>
        </Section>
      </div>
    </>
  );
}

function CreditPricing({
  disclosure,
  provisional,
  version,
  maxHold,
  exampleHold,
}: {
  disclosure: ReturnType<typeof chargeDisclosure>;
  provisional: boolean;
  version: string;
  maxHold: Credit;
  exampleHold: Credit;
}) {
  return (
    <>
      {provisional ? <p className="font-medium text-foreground">{PROVISIONAL_NOTE}</p> : null}
      {disclosure.intro.map((paragraph) => (
        <p key={paragraph}>{paragraph}</p>
      ))}
      <p>The following are never charged:</p>
      <ul className="list-disc space-y-1 pl-5">
        {disclosure.neverCharged.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p>{disclosure.outro}</p>
      <p>
        Rate card <code className="font-mono text-foreground">{version}</code>. The largest hold one request can take
        is {displayCredit(maxHold)}; with {`"max_tokens": ${EXAMPLE_MAX_TOKENS}`} as in the examples it is{" "}
        {displayCredit(exampleHold)}.
      </p>
      <p>
        A verified individual account receives {displayCredit(INITIAL_SIGNUP_GRANT_CREDIT)} once. It is not refilled
        and does not expire. When your available balance cannot cover a request&apos;s hold, the request is refused
        with <code className="font-mono text-foreground">402 insufficient_credit</code> and nothing is charged; see{" "}
        <Link href="/billing" className="underline underline-offset-4">
          Credits
        </Link>{" "}
        for your balance.
      </p>
    </>
  );
}

function Section({ title, id, children }: { title: string; id?: string; children: React.ReactNode }) {
  return (
    <Card id={id}>
      <CardHeader>
        <CardTitle>
          <h2>{title}</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm text-muted-foreground">{children}</CardContent>
    </Card>
  );
}

function Prompt({ label, text }: { label: string; text: string }) {
  return (
    <div className="rounded-lg border bg-muted/30">
      <div className="border-b px-3 py-1.5 text-xs text-muted-foreground">{label}</div>
      <pre className="overflow-x-auto p-3 font-mono text-xs whitespace-pre-wrap text-foreground/90">{text}</pre>
    </div>
  );
}
