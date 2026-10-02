"use client";

/**
 * C-07: the two data-use controls. Each submission is one server action (`./actions.ts`, passed in by
 * the page) carrying the idempotency key this form holds: it is never rendered, it is reused when a
 * change was not acknowledged (a retry applies once) and replaced only after one was. A choice is a
 * select plus Save, never a toggle that looks saved before the API answered. The page re-reads the
 * record after an acknowledged change (the action revalidates /settings).
 */
import { useState, useTransition, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import type { Result } from "@/lib/contracts/types";

export type Mode = "off" | "minimal" | "full";
export type SaveAction = (input: {
  key_id: string;
  mode: Mode;
  consent_version: number;
  retention_days: number;
  idempotency_key: string;
}) => Promise<Result<unknown>>;
export type WithdrawAction = (input: { grant_id: string; idempotency_key: string }) => Promise<Result<unknown>>;

export const MODE_LABELS: Record<Mode, string> = { off: "Off", minimal: "Metadata only", full: "Full content" };
const SELECT_CLASS =
  "h-8 rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30";

const newKey = () => crypto.randomUUID();
type Outcome = { tone: "ok" | "error"; text: string };
const outcomeOf = (result: Result<unknown>, done: string): Outcome =>
  result.ok ? { tone: "ok", text: done } : { tone: "error", text: `Not changed: ${result.error.message}.` };

function Status({ outcome }: { outcome: Outcome | null }) {
  return (
    <p role="status" aria-live="polite" className={outcome?.tone === "error" ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>
      {outcome?.text}
    </p>
  );
}

/** One key's capture choice, saved under the consent version the page read. */
export function CaptureForm(props: {
  keyId: string;
  name: string;
  mode: Mode;
  effectiveMode: Mode;
  consentVersion: number;
  retentionDays: number;
  suspended: boolean;
  save: SaveAction;
}) {
  const [key, setKey] = useState(newKey);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [pending, startTransition] = useTransition();
  const id = `capture-${props.keyId}`;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const mode = String(new FormData(event.currentTarget).get("mode")) as Mode;
    startTransition(async () => {
      const result = await props.save({
        key_id: props.keyId,
        mode,
        consent_version: props.consentVersion,
        retention_days: props.retentionDays,
        idempotency_key: key,
      });
      setOutcome(outcomeOf(result, "Saved. It applies to requests sent from now on."));
      setKey(result.ok ? newKey() : key);
    });
  }

  return (
    <form onSubmit={submit} className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={id} className="min-w-0 flex-1 break-all font-medium">
          {props.name}
        </label>
        <select id={id} name="mode" defaultValue={props.mode} disabled={props.suspended} className={SELECT_CLASS}>
          {(Object.keys(MODE_LABELS) as Mode[]).map((mode) => (
            <option key={mode} value={mode}>
              {MODE_LABELS[mode]}
            </option>
          ))}
        </select>
        <Button type="submit" size="sm" disabled={props.suspended || pending}>
          {pending ? "Saving…" : "Save"}
        </Button>
      </div>
      {props.effectiveMode !== props.mode ? (
        <p className="text-xs text-muted-foreground">
          Recording now: {MODE_LABELS[props.effectiveMode]}. The account&apos;s consent for this choice is not in effect.
        </p>
      ) : null}
      <Status outcome={outcome} />
    </form>
  );
}

/** Withdraw one active grant. Allowed while the account is suspended. */
export function WithdrawForm({ grantId, withdraw }: { grantId: string; withdraw: WithdrawAction }) {
  const [key, setKey] = useState(newKey);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [pending, startTransition] = useTransition();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    startTransition(async () => {
      const result = await withdraw({ grant_id: grantId, idempotency_key: key });
      setOutcome(outcomeOf(result, "Withdrawn."));
      setKey(result.ok ? newKey() : key);
    });
  }

  return (
    <form onSubmit={submit} className="space-y-1">
      <Button type="submit" size="sm" variant="outline" disabled={pending}>
        {pending ? "Withdrawing…" : "Withdraw"}
      </Button>
      <Status outcome={outcome} />
    </form>
  );
}
