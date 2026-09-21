"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RANGES } from "../usage/ranges.ts";
import {
  CONTENT_FILTERS,
  FEEDBACK_FILTERS,
  MODE_FILTERS,
  traceHref,
  withPatch,
  type FilterState,
} from "./query.ts";
import { JOB_STATES } from "../../../lib/contracts/types.ts";
import { displayed, issue, settleArrival, startSequence } from "./sequence.ts";

const ALL = "__all__";

/**
 * The filter bar. Every control is a link in disguise: the URL is the state, so a filtered list is
 * shareable and the page stays a server component.
 *
 * The controls show what the reader last picked until that navigation settles (`sequence.ts`), so
 * two quick changes cannot end with an older response putting a filter back.
 */
export function TraceFilters({
  filters,
  keys,
  models,
}: {
  filters: FilterState;
  keys: { id: string; name: string }[];
  models: string[];
}) {
  const router = useRouter();
  const [sequence, setSequence] = useState(() => startSequence(filters));
  // `isPending` stays true until the newest navigation of this transition has settled, so until
  // then the controls keep drawing what the reader picked rather than an arriving older page.
  const [isPending, startTransition] = useTransition();
  const shown = displayed(sequence, filters, isPending);

  function change(patch: Partial<FilterState>) {
    // Catch up with the URL before issuing: the page now rendered is the response to whichever
    // request minted its href, and a response older than the one already applied moves nothing.
    const caught = settleArrival(sequence, filters, traceHref(filters)).sequence;
    const next = withPatch(shown, patch);
    const href = traceHref(shown, patch);
    // The href is the identity the response will arrive under — a navigation carries no token.
    setSequence(issue(caught, next, href).sequence);
    startTransition(() => router.push(href));
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select value={shown.key ?? ALL} onValueChange={(v) => change({ key: v === ALL ? null : (v as string) })}>
        <SelectTrigger size="sm" className="max-w-44" aria-label="Filter by API key">
          <SelectValue placeholder="All keys" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All keys</SelectItem>
          {keys.map((key) => (
            <SelectItem key={key.id} value={key.id}>
              {key.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={shown.model ?? ALL}
        onValueChange={(v) => change({ model: v === ALL ? null : (v as string) })}
      >
        <SelectTrigger size="sm" className="max-w-52" aria-label="Filter by model">
          <SelectValue placeholder="All models" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All models</SelectItem>
          {models.map((model) => (
            <SelectItem key={model} value={model}>
              {model}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={shown.state ?? ALL}
        onValueChange={(v) => change({ state: (v === ALL ? null : v) as FilterState["state"] })}
      >
        <SelectTrigger size="sm" aria-label="Filter by outcome">
          <SelectValue placeholder="Any outcome" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>Any outcome</SelectItem>
          {JOB_STATES.map((state) => (
            <SelectItem key={state} value={state}>
              {state}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={shown.content ?? ALL}
        onValueChange={(v) => change({ content: (v === ALL ? null : v) as FilterState["content"] })}
      >
        <SelectTrigger size="sm" aria-label="Filter by captured content">
          <SelectValue placeholder="Any content" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>Any content</SelectItem>
          {CONTENT_FILTERS.map((state) => (
            <SelectItem key={state} value={state}>
              {state.replace("_", " ")}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={shown.mode ?? ALL}
        onValueChange={(v) => change({ mode: (v === ALL ? null : v) as FilterState["mode"] })}
      >
        <SelectTrigger size="sm" aria-label="Filter by capture mode">
          <SelectValue placeholder="Any mode" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>Any mode</SelectItem>
          {MODE_FILTERS.map((mode) => (
            <SelectItem key={mode} value={mode}>
              {mode}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={shown.feedback}
        onValueChange={(v) => change({ feedback: v as FilterState["feedback"] })}
      >
        <SelectTrigger size="sm" aria-label="Filter by feedback">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {FEEDBACK_FILTERS.map((value) => (
            <SelectItem key={value} value={value}>
              {value === "any" ? "Any feedback" : value === "yes" ? "With feedback" : "No feedback"}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={shown.range} onValueChange={(v) => change({ range: v as FilterState["range"] })}>
        <SelectTrigger size="sm" aria-label="Time range">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.keys(RANGES).map((range) => (
            <SelectItem key={range} value={range}>
              Last {range}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
