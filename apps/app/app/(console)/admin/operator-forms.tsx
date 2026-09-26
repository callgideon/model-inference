"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition, type FormEvent } from "react";
import { operatorAction } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { OPERATOR_FORMS, formInput, nextKey, outcomeOf, type Outcome } from "./operator-form";

const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

const newKey = () => crypto.randomUUID();

/** One audited operator change. The key is held in state, never rendered, and rotates only after a commit. */
function OperatorForm({ form }: { form: (typeof OPERATOR_FORMS)[number] }) {
  const router = useRouter();
  const [key, setKey] = useState(newKey);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [pending, startTransition] = useTransition();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const target = event.currentTarget;
    const input = formInput(form.action, (name) => (data.has(name) ? String(data.get(name)) : null), key);
    startTransition(async () => {
      const result = await operatorAction(input);
      setOutcome(outcomeOf(result));
      setKey(nextKey(key, result, newKey));
      if (result.ok) {
        target.reset();
        router.refresh(); // re-read the committed state: balances, suspension, audit trail
      }
    });
  }

  const id = (name: string) => `${form.action}-${name}`;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{form.title}</CardTitle>
        <CardDescription>{form.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-3">
          {form.fields.map((field) => (
            <div key={field.name} className="space-y-1.5">
              <Label htmlFor={id(field.name)}>{field.label}</Label>
              {field.kind === "suspended" ? (
                <select id={id(field.name)} name={field.name} required defaultValue="true" className={SELECT_CLASS}>
                  <option value="true">Suspend</option>
                  <option value="false">Restore</option>
                </select>
              ) : (
                <Input
                  id={id(field.name)}
                  name={field.name}
                  required
                  maxLength={field.kind === "reason" ? 500 : field.kind === "id" ? 36 : 24}
                  inputMode={field.kind === "amount" ? "decimal" : undefined}
                  autoComplete="off"
                  spellCheck={false}
                  aria-describedby={field.hint ? id(`${field.name}-hint`) : undefined}
                  className={field.kind === "reason" ? "w-full" : "w-full font-mono"}
                />
              )}
              {field.hint ? (
                <p id={id(`${field.name}-hint`)} className="text-xs text-muted-foreground">
                  {field.hint}
                </p>
              ) : null}
            </div>
          ))}
          <Button type="submit" disabled={pending}>
            {pending ? "Applying…" : form.submit}
          </Button>
          <p role="status" aria-live="polite" className={outcome?.tone === "error" ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>
            {outcome?.text ?? ""}
          </p>
        </form>
      </CardContent>
    </Card>
  );
}

export function OperatorForms() {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      {OPERATOR_FORMS.map((form) => (
        <OperatorForm key={form.action} form={form} />
      ))}
    </div>
  );
}
