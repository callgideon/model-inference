"use client";

import { useState } from "react";
import Link from "next/link";
import { Snippet } from "@/components/snippet";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { PublishedModel } from "@/lib/contracts/v2/published-model";
import { buildExamples, VIDEO_URL_PLACEHOLDER, videoUrlInput } from "../docs/examples";

/**
 * UX-04 (C-02 "First video request"): the video example with the reader's own clip URL written into
 * it. The field is checked locally (`videoUrlInput`) and only fills in the code: nothing is fetched,
 * uploaded or stored, and until a valid URL is entered the code carries the visible placeholder.
 */
export function VideoExample({
  model,
  baseUrl,
  facts,
  disabled,
}: {
  model: PublishedModel;
  baseUrl: string;
  facts: string[];
  disabled: boolean;
}) {
  const [raw, setRaw] = useState("");
  const input = raw.trim() === "" ? null : videoUrlInput(raw);
  const url = input?.ok ? input.url : undefined;
  const example = buildExamples(model, { videoUrl: url }).find((e) => e.id === "video")!;
  return (
    <div className="space-y-3">
      <p className="text-muted-foreground">
        Send one finished clip: an https URL the API can fetch, or a local file through the{" "}
        <Link href="/docs#upload" className="underline underline-offset-4">
          upload steps
        </Link>
        . For many clips, use{" "}
        <Link href="/docs#async" className="underline underline-offset-4">
          async jobs
        </Link>{" "}
        with one Idempotency-Key per clip.
      </p>
      <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
        {facts.map((fact) => (
          <li key={fact}>{fact}</li>
        ))}
      </ul>
      <div className="space-y-1">
        <Label htmlFor="video-url">Your clip&apos;s https URL</Label>
        <Input
          id="video-url"
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="https://"
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
          aria-invalid={input !== null && !input.ok}
          aria-describedby="video-url-help"
        />
        <p id="video-url-help" role={input !== null && !input.ok ? "alert" : undefined} className="text-xs text-muted-foreground">
          {input !== null && !input.ok
            ? input.message
            : url === undefined
              ? `Until you enter one, the code says ${VIDEO_URL_PLACEHOLDER} and the API refuses it.`
              : "Written into the code below only. Nothing is fetched or uploaded from this page."}
        </p>
      </div>
      <Snippet snippets={example.snippets} baseUrl={baseUrl} disabled={disabled} />
    </div>
  );
}
