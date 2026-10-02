"use client";
// WR-C3L-2: one C3L action as a form. Hidden values (the run id, the first key) come from the server
// render. Register row 98: a `keyed` hidden field is the form's Idempotency-Key, replaced after any
// definite answer and kept after an unknown one, so a retry replays the write (UX08-J06).
import { useActionState, useState } from "react";
import { outcomeText } from "@/lib/services/judge/copy";
import type { Outcome } from "@/lib/services/judge/core";
import { retryKeepsKey } from "./view";

type Field = { name: string; label: string; optional?: boolean };

export function JudgeForm({ action, title, fields, hidden = {}, keyed }: {
  action: (data: FormData) => Promise<Outcome>;
  title: string;
  fields: Field[];
  hidden?: Record<string, string>;
  keyed?: string;
}) {
  const [values, setValues] = useState(hidden);
  const [state, submit, pending] = useActionState(async (_: Outcome | null, data: FormData) => {
    const outcome = await action(data);
    if (keyed && !retryKeepsKey(outcome)) setValues((v) => ({ ...v, [keyed]: crypto.randomUUID() }));
    return outcome;
  }, null);
  return (
    <form action={submit}>
      <h2>{title}</h2>
      {fields.map((f) => (
        <label key={f.name}>
          {f.label} <input name={f.name} required={!f.optional} />
        </label>
      ))}
      {Object.entries(values).map(([name, value]) => (
        <input type="hidden" key={name} name={name} value={value} />
      ))}
      <button type="submit" disabled={pending}>
        {title}
      </button>
      {state && <p role="status">{outcomeText(state)}</p>}
    </form>
  );
}
