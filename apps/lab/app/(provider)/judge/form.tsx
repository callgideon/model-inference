"use client";
// WR-C3L-2: one C3L action as a form. Hidden values (the run id) come from the server render.
import { useActionState } from "react";
import { outcomeText } from "@/lib/services/judge/copy";
import type { Outcome } from "@/lib/services/judge/core";

type Field = { name: string; label: string; optional?: boolean };

export function JudgeForm({ action, title, fields, hidden = {} }: {
  action: (data: FormData) => Promise<Outcome>;
  title: string;
  fields: Field[];
  hidden?: Record<string, string>;
}) {
  const [state, submit, pending] = useActionState((_: Outcome | null, data: FormData) => action(data), null);
  return (
    <form action={submit}>
      <h2>{title}</h2>
      {fields.map((f) => (
        <label key={f.name}>
          {f.label} <input name={f.name} required={!f.optional} />
        </label>
      ))}
      {Object.entries(hidden).map(([name, value]) => (
        <input type="hidden" key={name} name={name} value={value} />
      ))}
      <button type="submit" disabled={pending}>
        {title}
      </button>
      {state && <p role="status">{outcomeText(state)}</p>}
    </form>
  );
}
