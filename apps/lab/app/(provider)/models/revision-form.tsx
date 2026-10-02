"use client";
// UX-03 L-03: the registration form for a new revision of an imported model. Two steps plus a
// read-only review; Back never submits; the server's answer keeps the person's values and names each
// bad field; a registered revision is shown as the record the service returned ("Registered", never
// live); an unanswered request says so and points at Models before any second try.
import { Fragment, useActionState, useRef, useState } from "react";
import Link from "next/link";
import { Button, buttonClass } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import type { RegistrationField, RegistrationValues } from "@/lib/services/control/view";
import { registerRevision } from "./actions";
import s from "../operate.module.css";

const LABEL: Record<RegistrationField, string> = { name: "Model", artifactDigest: "Artifact SHA-256", schemaVersion: "Schema version", runtime: "Runtime" };

/** The fields, filled with `values` and marked with `errors` (the server's, after a submit). */
export function RevisionFields({ names, values, errors, hidden = false }: {
  names: string[]; values: RegistrationValues; errors: Partial<Record<RegistrationField, string>>; hidden?: boolean;
}) {
  return (
    <div hidden={hidden} className="lab-stack">
      <fieldset>
        <legend>Identify</legend>
        <Field label={LABEL.name} description="A model already imported into this workspace. Its identifier does not change." error={errors.name}>
          <Input name="name" defaultValue={values.name} render={<select />}>
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </Input>
        </Field>
      </fieldset>
      <fieldset>
        <legend>Revision</legend>
        <Field label={LABEL.artifactDigest} description="The digest of the approved artifact this revision serves, as sha256:<64 hex>. It must be one of the model's imported artifacts." error={errors.artifactDigest}>
          <Input name="artifactDigest" defaultValue={values.artifactDigest} autoComplete="off" spellCheck={false} placeholder="sha256:…" />
        </Field>
        <Field label={LABEL.schemaVersion} description="Advanced identifier: the request/response schema this revision accepts." error={errors.schemaVersion}>
          <Input name="schemaVersion" defaultValue={values.schemaVersion} autoComplete="off" spellCheck={false} />
        </Field>
        <Field label={LABEL.runtime} description="Identifies a runtime record. It does not reserve hardware or choose an engine." error={errors.runtime}>
          <Input name="runtime" defaultValue={values.runtime} autoComplete="off" spellCheck={false} />
        </Field>
      </fieldset>
    </div>
  );
}

export function RevisionForm({ names }: { names: string[] }) {
  const [state, action, pending] = useActionState(registerRevision, null);
  const [draft, setDraft] = useState<RegistrationValues | null>(null);
  const form = useRef<HTMLFormElement>(null);
  const values = state?.values ?? { name: names[0] ?? "", artifactDigest: "", schemaVersion: "", runtime: "" };
  const outcome = state?.outcome ?? null;
  if (outcome?.kind === "registered") {
    const d = outcome.deployment;
    return (
      <div role="status" className="lab-stack">
        <p>
          Registered private revision <strong>{d.modelId}</strong> {d.revisionLabel}. Deployment record <code className="lab-id">{d.deploymentRevisionId}</code> is
          registered; serving readiness has not been verified.
        </p>
        <div className={s.actions}>
          <Link href="/deployments" className={buttonClass("primary")}>View deployments</Link>
          <a href="/models" className={buttonClass()}>Register another revision</a>
        </div>
      </div>
    );
  }
  const review = () => {
    const data = new FormData(form.current ?? undefined);
    setDraft({ name: String(data.get("name") ?? ""), artifactDigest: String(data.get("artifactDigest") ?? ""), schemaVersion: String(data.get("schemaVersion") ?? ""), runtime: String(data.get("runtime") ?? "") });
  };
  return (
    <form ref={form} className={s.form} action={(data) => { setDraft(null); action(data); }}>
      {outcome && (
        <p role="alert">
          {outcome.message} {outcome.kind === "uncertain" && <a href="/models">Check models</a>}
        </p>
      )}
      <RevisionFields names={names} values={values} errors={state?.errors ?? {}} hidden={draft !== null} />
      {draft === null ? (
        <div className={s.actions}>
          <Button variant="primary" onClick={review}>Review</Button>
        </div>
      ) : (
        <section aria-labelledby="review-heading" className="lab-stack">
          <h3 id="review-heading">Review</h3>
          <dl className={s.facts}>
            {(Object.keys(LABEL) as RegistrationField[]).map((f) => (
              <Fragment key={f}>
                <dt>{LABEL[f]}</dt><dd className="lab-id">{draft[f] || "—"}</dd>
              </Fragment>
            ))}
          </dl>
          <p className={s.muted}>This registers a private revision and its deployment record. It does not start an engine or publish anything.</p>
          <div className={s.actions}>
            <Button variant="ghost" onClick={() => setDraft(null)}>Back</Button>
            <Button type="submit" variant="primary" pending={pending}>Register private revision</Button>
          </div>
        </section>
      )}
    </form>
  );
}
