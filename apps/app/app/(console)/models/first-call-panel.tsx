import Link from "next/link";
import { Check } from "lucide-react";
import { Snippet } from "@/components/snippet";
import type { PublishedModel } from "@/lib/contracts/v2/published-model";
import { videoFacts } from "../docs/content";
import { buildExamples } from "../docs/examples";
import type { FirstCall } from "./first-call";
import { VideoExample } from "./video-example";

/**
 * UX-04 (C-02): the three-step first-call guide at the top of /models. Compact guidance, not an
 * overlay: step one is the existing secure key dialog (`createKey`, passed in by the page only when
 * the account may create a key), the test call is the text connectivity check, and the request is
 * found in Usage. Everything it says comes from `firstCallModel`.
 */
export function FirstCallPanel({
  guide,
  model,
  baseUrl,
  createKey,
}: {
  guide: FirstCall;
  model: PublishedModel;
  baseUrl: string;
  createKey: React.ReactNode;
}) {
  const accepting = model.availability === "available";
  const text = buildExamples(model).find((e) => e.id === "text")!;
  const link = "underline underline-offset-4";
  return (
    <section id="quickstart" aria-labelledby="first-call-heading" className="mb-6 rounded-xl border bg-card p-4 text-sm">
      <details open={guide.open} className="space-y-4">
        <summary className="cursor-pointer">
          <h2 id="first-call-heading" className="inline font-heading text-base font-medium">
            {guide.heading}
          </h2>
        </summary>
        <ol className="grid gap-4 md:grid-cols-3">
          <Step n={1} title="Create a key" done={guide.key.kind === "existing"}>
            {guide.key.kind === "create" ? (
              <>
                <p className="text-muted-foreground">A key lets your code call the API. It is shown once.</p>
                {createKey}
              </>
            ) : guide.key.kind === "existing" ? (
              <>
                <p className="text-muted-foreground">Use an existing key or create a new one.</p>
                <ul className="space-y-0.5 text-xs text-muted-foreground">
                  {guide.key.keys.map((k) => (
                    <li key={k.prefix + k.name}>
                      {k.name} <code className="font-mono">{k.prefix}</code>
                    </li>
                  ))}
                </ul>
                <Link href="/api-keys" className={link}>
                  Manage keys
                </Link>
              </>
            ) : guide.key.kind === "refused" ? (
              <p role="status">{guide.key.reason}</p>
            ) : (
              <p role="status">
                Your keys could not be loaded right now.{" "}
                <Link href="/api-keys" className={link}>
                  Open API keys
                </Link>
              </p>
            )}
          </Step>
          <Step n={2} title="Make a test call" done={guide.done !== null}>
            <p className="text-muted-foreground">
              {accepting
                ? "Run the connectivity check below with INFRX_API_KEY set. It checks your key and the endpoint, not video understanding."
                : "This model is not accepting requests right now. The code below is a reference only."}
            </p>
          </Step>
          <Step n={3} title="View your request" done={guide.done !== null}>
            {guide.done ? (
              <Link href={`/usage/${guide.done.requestId}`} className={link}>
                Your latest successful request
              </Link>
            ) : (
              <p className="text-muted-foreground">
                Find it in{" "}
                <Link href="/usage" className={link}>
                  Usage
                </Link>{" "}
                with its result and charge.
              </p>
            )}
          </Step>
        </ol>
        <Snippet snippets={text.snippets} baseUrl={baseUrl} disabled={!accepting} />
        <details className="rounded-lg border p-3">
          <summary className="cursor-pointer font-medium">Use a video</summary>
          <div className="pt-3">
            <VideoExample model={model} baseUrl={baseUrl} facts={videoFacts(model.capability)} disabled={!accepting} />
          </div>
        </details>
      </details>
    </section>
  );
}

function Step({ n, title, done, children }: { n: number; title: string; done: boolean; children: React.ReactNode }) {
  return (
    <li className="space-y-2">
      <h3 className="flex items-center gap-2 font-medium">
        <span aria-hidden className="flex size-5 items-center justify-center rounded-full border text-xs">
          {done ? <Check className="size-3" /> : n}
        </span>
        {title}
        {done ? <span className="sr-only"> (done)</span> : null}
      </h3>
      {children}
    </li>
  );
}
