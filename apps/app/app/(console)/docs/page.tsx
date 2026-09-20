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
import { getSession } from "@/lib/session";
import { createClient } from "@/lib/supabase/server";
import type { Model } from "@/lib/types";

export const metadata = { title: "Docs · infrx" };

// The two prompts Marlin was trained on, verbatim from the provider document
// (apps/infrx-api/openrouter/provider-models.json).
const CAPTION_PROMPT = `Provide a spatial description of this clip followed by time-ranged events.
For each event, give the time range as <start - end> and a short description.`;

const GROUNDING_PROMPT = `Identify the timestamps during which "<event>" takes place. Output the time range as "From <start> to <end>." (numbers in seconds).`;

const ERRORS = [
  ["401", "invalid api key", "The key is unknown or revoked. Check the Authorization header."],
  ["400", "one video per request", "Send a single video part per request."],
  ["400", "video is 180s; max is 120s", "Trim the clip to 120 seconds or less."],
  ["400", "video larger than 64.0 MB", "Re-encode below 64 MB."],
  [
    "400",
    "video_url must be an http(s) URL or a data: URL",
    "Pass a URL we can fetch, or a base64 data URL.",
  ],
  [
    "400",
    "could not read video: …",
    "The file could not be decoded — check the container and codec.",
  ],
  ["429", "at capacity, retry", "Wait for the seconds in the Retry-After header, then retry."],
  ["502", "upstream error: …", "The model server failed. Retry; tell us if it persists."],
];

export default async function DocsPage() {
  const session = await getSession();
  const supabase = await createClient();

  const [{ data: model }, { data: keys }] = await Promise.all([
    supabase.from("models").select("*").eq("status", "live").order("sort").limit(1).maybeSingle(),
    supabase
      .from("api_keys")
      .select("id, name, prefix")
      .eq("org_id", session.orgId)
      .is("revoked_at", null)
      .order("created_at", { ascending: false }),
  ]);

  const m = model as Model | null;
  const baseUrl = m?.base_url ?? "https://marlin2b.callbill.ai/v1";
  const limits = m?.limits ?? {};

  return (
    <>
      <PageHeader
        title="Docs"
        subtitle="Everything you need to make the first call and read the answer."
      />

      <div className="space-y-4">
        <Section title="Quickstart">
          <p>
            The API is OpenAI-compatible: point any OpenAI client at{" "}
            <code className="font-mono text-foreground">{baseUrl}</code> and send your key as a
            bearer token. Create a key on the API Keys page, then run one of these.
          </p>
          {m ? (
            <div className="not-prose pt-2">
              <Snippet
                snippets={m.snippets}
                baseUrl={baseUrl}
                keys={(keys ?? []) as SnippetKey[]}
              />
            </div>
          ) : null}
        </Section>

        <Section title="Authentication">
          <p>
            Every request needs{" "}
            <code className="font-mono text-foreground">Authorization: Bearer sk-infrx-…</code>.
            Keys belong to your organization; we store only their SHA-256 hash, so the secret is
            shown exactly once when you create it. Revoking a key on the API Keys page stops it
            working within 60 seconds. A missing, unknown or revoked key returns{" "}
            <code className="font-mono text-foreground">401</code>.
          </p>
        </Section>

        <Section title="Prompts">
          <p>
            Marlin-2B is trained on two prompts. Other phrasings work, but these are what the model
            was tuned for — use them verbatim for the best output.
          </p>
          <div className="not-prose space-y-3 pt-2">
            <Prompt label="Dense captioning with timestamps" text={CAPTION_PROMPT} />
            <Prompt label="Temporal grounding (replace <event>)" text={GROUNDING_PROMPT} />
          </div>
          <p>
            The model emits a leading{" "}
            <code className="font-mono text-foreground">&lt;think&gt;</code> token; the gateway
            strips it from both streaming and non-streaming responses.
          </p>
        </Section>

        <Section title="Video limits">
          <ul className="list-disc space-y-1 pl-5">
            <li>One video per request.</li>
            <li>
              Up to {String(limits.max_video_seconds ?? 120)} seconds and{" "}
              {String(limits.max_video_mb ?? 64)} MB.
            </li>
            <li>mp4, webm or mov.</li>
            <li>
              Pass it as an http(s) URL we can fetch, or as a base64{" "}
              <code className="font-mono text-foreground">data:</code> URL.
            </li>
            <li>
              Frames are sampled at 2 fps, 240 frames maximum — the grid the model was trained on.
            </li>
          </ul>
        </Section>

        <Section title="Streaming">
          <p>
            Set <code className="font-mono text-foreground">&quot;stream&quot;: true</code> to get
            server-sent events. The gateway turns on{" "}
            <code className="font-mono text-foreground">stream_options.include_usage</code>, so the
            final chunk before <code className="font-mono text-foreground">data: [DONE]</code>{" "}
            carries the token counts you are billed for.
          </p>
        </Section>

        <Section title="Request ids">
          <p>
            Every response carries an{" "}
            <code className="font-mono text-foreground">Inference-Id</code> header — a UUID that is
            also the id of the row you see on the Usage page. Quote it when you report a problem. We
            never store prompts, videos or completions; usage rows are metadata only.
          </p>
        </Section>

        <Card>
          <CardHeader>
            <CardTitle>Error codes</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16">Status</TableHead>
                  <TableHead>Message</TableHead>
                  <TableHead>What to do</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {ERRORS.map(([status, message, fix]) => (
                  <TableRow key={status + message}>
                    <TableCell className="font-mono">{status}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {message}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{fix}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="border-t p-4 text-xs text-muted-foreground">
              A 429 always carries a <code className="font-mono">Retry-After</code> header in
              seconds. Errors use the OpenAI shape:{" "}
              <code className="font-mono">{`{"error": {"message": …, "type": …}}`}</code>.
            </p>
          </CardContent>
        </Card>
      </div>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm text-muted-foreground">{children}</CardContent>
    </Card>
  );
}

function Prompt({ label, text }: { label: string; text: string }) {
  return (
    <div className="rounded-lg border bg-muted/30">
      <div className="border-b px-3 py-1.5 text-xs text-muted-foreground">{label}</div>
      <pre className="overflow-x-auto p-3 font-mono text-xs whitespace-pre-wrap text-foreground/90">
        {text}
      </pre>
    </div>
  );
}
