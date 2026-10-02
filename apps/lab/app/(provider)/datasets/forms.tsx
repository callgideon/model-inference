"use client";
// N4 + UX-06 (L-07) client forms: the guided import (Source → Mapping → Validate → Import), derive and
// export. Each renders only what its server action returned; a pending action shows as pending and a
// failure is never shown as success. The import's preview counts only for the exact file and mapping it
// checked (previewKey): an edit to either one sends the provider back to Validate and Import is off.
// The actions are props (the page passes its server actions) so the synthetic harness can drive the
// same component with fixtures (tests/ux/improve).
import Link from "next/link";
import { startTransition, useActionState, useState, type FormEvent } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import type { ActionState } from "@/lib/services/datasets/flows";
import type { Derived, ExportRecord, ImportJob, Preview } from "@/lib/services/datasets/port";
import { bpPercent, IMPORT_STEPS, importStep, leakageWarnings, previewKey, splitPlan, type SourceFile } from "@/lib/services/datasets/views";
import styles from "./datasets.module.css";

type Action<T> = (state: ActionState<T>, form: FormData) => Promise<ActionState<T>>;
const IDLE = { status: "idle" } as const;

/** A submit handler that dispatches `pick(submitter)` without React's automatic form reset, so a
 * previewed or refused form keeps the provider's file and entries (02-foundations: never reset valid
 * entries; a reset file input would also send the next step without its file). */
const keep = (pick: (submitter: HTMLElement | null) => (form: FormData) => void) => (event: FormEvent<HTMLFormElement>) => {
  event.preventDefault();
  const submitter = (event.nativeEvent as SubmitEvent).submitter;
  const form = new FormData(event.currentTarget, submitter);
  startTransition(() => pick(submitter)(form));
};

function Problem({ state }: { state: ActionState<unknown> }) {
  if (state.status !== "error") return null;
  return (
    <div role="alert" className={styles.problem}>
      <p>{state.message}</p>
      {state.report && (
        <ul>
          {state.report.rejected.map((r) => (
            <li key={r.line}>
              line {r.line}: {r.reason} {r.detail}
            </li>
          ))}
        </ul>
      )}
      {leakageWarnings({ leaks: state.leaks }).map((w) => (
        <p key={w}>{w}</p>
      ))}
    </div>
  );
}

/** "Import again" for a failed job (R252): a new job of the same upload; the page moves to it. */
export function ImportAgain({ importId, requeue }: { importId: string; requeue: Action<ImportJob> }) {
  const [state, action, pending] = useActionState<ActionState<ImportJob>, FormData>(requeue, IDLE);
  return (
    <form action={action} aria-label="Import again">
      <input type="hidden" name="import_id" value={importId} />
      <Button type="submit" pending={pending}>
        {pending ? "Starting…" : "Import again"}
      </Button>
      <Problem state={state} />
    </form>
  );
}

const meta = (file: FormDataEntryValue | File | null | undefined): SourceFile =>
  file instanceof File && file.size > 0 ? { name: file.name, size: file.size, lastModified: file.lastModified } : null;

const STEP_LABEL = { source: "Source", mapping: "Mapping", validate: "Validate", import: "Import" } as const;

type Checked = ActionState<Preview> & { key: string | null };

export function ImportWizard({ preview, start, template }: { preview: Action<Preview>; start: Action<ImportJob>; template: string }) {
  const [spec, setSpec] = useState(template);
  const [file, setFile] = useState<SourceFile>(null);
  // The key is taken from the very form that was previewed, so an edit made while it ran never counts.
  const [checked, previewAction, previewing] = useActionState<Checked, FormData>(
    async (_, form) => ({ ...(await preview(IDLE, form)), key: previewKey(String(form.get("spec") ?? ""), meta(form.get("file"))) }),
    { status: "idle", key: null },
  );
  const [started, startAction, starting] = useActionState<ActionState<ImportJob>, FormData>(start, IDLE);
  const step = importStep({ file, spec, previewed: checked.status === "ok" ? checked.key : null });
  const stale = checked.status === "ok" && step !== "import";
  return (
    <form aria-label="Import a dataset" className="lab-stack" onSubmit={keep((by) => (by?.dataset.step === "import" ? startAction : previewAction))}>
      <ol className={styles.steps} aria-label="Import steps">
        {IMPORT_STEPS.map((s, i) => (
          <li key={s} aria-current={s === step ? "step" : undefined}>
            {i + 1}. {STEP_LABEL[s]}
          </li>
        ))}
      </ol>
      <fieldset className={styles.step}>
        <legend>1. Source</legend>
        <Field
          label="Rows (JSONL)"
          description="One JSON object per line. Video references, labels and provenance are fields of each row; this is not a media uploader."
        >
          <Input type="file" name="file" accept=".jsonl,application/x-ndjson,application/json" required onChange={(e) => setFile(meta(e.currentTarget.files?.[0]))} />
        </Field>
        {file && (
          <p className={styles.note}>
            Selected <span className="lab-id">{file.name}</span> ({file.size} bytes)
          </p>
        )}
      </fieldset>
      <fieldset className={styles.step}>
        <legend>2. Mapping</legend>
        <Field
          label="Import spec (advanced mapping, infrx.dataset_import.1 JSON)"
          description="Pre-filled with this workspace and a new import and dataset id. Fill the licence, the grant the source registers under, the annotation method version and the dotted field paths (content; optionally group and split; finite video also media, start, end and clock_unit). Nothing is authorised for you."
        >
          <Input render={<textarea rows={14} />} name="spec" required value={spec} onValueChange={setSpec} spellCheck={false} />
        </Field>
      </fieldset>
      <fieldset className={styles.step}>
        <legend>3. Validate</legend>
        <p className={styles.note}>The preview maps the first 64 KiB of the file. It is not a full-file pass, and it is not proof of consent or leakage safety.</p>
        <Button type="submit" data-step="validate" pending={previewing}>
          {previewing ? "Checking…" : "Preview mapping"}
        </Button>
        <Problem state={checked} />
        {stale && (
          <p role="status" className={styles.note}>
            The file or the mapping changed after the preview. Preview again before importing.
          </p>
        )}
        {checked.status === "ok" && !stale && (
          <section aria-label="Preview" className="lab-stack">
            <h3>Fields</h3>
            <ul>
              {Object.entries(checked.value.fields).map(([name, types]) => (
                <li key={name}>
                  {name}: {types.join(" | ")}
                </li>
              ))}
            </ul>
            <h3>First rows</h3>
            <ul>
              {checked.value.rows.map((row) => (
                <li key={row.line}>
                  line {row.line}:{" "}
                  {row.reason ? <Badge tone="danger">Rejected ({row.reason})</Badge> : <span className="lab-id">{JSON.stringify(row.mapped)}</span>}
                </li>
              ))}
            </ul>
          </section>
        )}
      </fieldset>
      <fieldset className={styles.step}>
        <legend>4. Import</legend>
        <Field label="If the import finds invalid rows" description="Rejected rows are always listed with their line and reason, never silently dropped.">
          <Input render={<select />} name="accept_rejects" defaultValue="off">
            <option value="off">Refuse the import</option>
            <option value="on">Import the valid rows and list the rejected ones</option>
          </Input>
        </Field>
        {step !== "import" && <p className={styles.note}>Import is available once a preview of the current file and mapping has run.</p>}
        <Button type="submit" variant="primary" data-step="import" pending={starting} disabled={step !== "import"}>
          {starting ? "Starting…" : "Import"}
        </Button>
        <Problem state={started} />
      </fieldset>
    </form>
  );
}

/** A new version from this one (and any additions): exact percentages, holdout the remainder. */
export function DeriveForm({ base, datasetId, derive }: { base: string; datasetId: string; derive: Action<Derived> }) {
  const [state, action, pending] = useActionState<ActionState<Derived>, FormData>(derive, IDLE);
  const [train, setTrain] = useState("80");
  const [validation, setValidation] = useState("10");
  const [touched, setTouched] = useState(false);
  const plan = splitPlan(train, validation);
  const error = touched && !plan.ok ? plan.message : null;
  return (
    <form onSubmit={keep(() => action)} aria-label="Freeze a new version" className="lab-stack">
      <input type="hidden" name="base" value={base} />
      <Field label="Dataset id" description="Generated for this new dataset. To add a version to an existing dataset, enter its id and the next version yourself; nothing guesses it.">
        <Input name="dataset_id" required defaultValue={datasetId} spellCheck={false} />
      </Field>
      <Field label="Version">
        <Input name="version" inputMode="numeric" defaultValue="1" required />
      </Field>
      <Field label="Split seed" description="The same seed over the same samples gives the same split.">
        <Input name="seed" inputMode="numeric" defaultValue="0" required />
      </Field>
      <Field label="Train (%)" error={error}>
        <Input inputMode="decimal" value={train} onValueChange={(v) => (setTrain(v), setTouched(true))} />
      </Field>
      <Field label="Validation (%)" error={error}>
        <Input inputMode="decimal" value={validation} onValueChange={(v) => (setValidation(v), setTouched(true))} />
      </Field>
      <input type="hidden" name="train_bp" value={plan.ok ? plan.trainBp : ""} />
      <input type="hidden" name="validation_bp" value={plan.ok ? plan.validationBp : ""} />
      <p role="status" className={styles.note}>
        {plan.ok
          ? `Train ${bpPercent(plan.trainBp)} · Validation ${bpPercent(plan.validationBp)} · Holdout ${bpPercent(plan.holdoutBp)} (the remainder)`
          : "Enter both shares to see the split."}
      </p>
      <Field label="Versions to add (refs, one per line)">
        <Input render={<textarea rows={3} />} name="add" spellCheck={false} />
      </Field>
      <Button type="submit" pending={pending} disabled={!plan.ok}>
        {pending ? "Deriving…" : "Derive version"}
      </Button>
      <Problem state={state} />
      {state.status === "ok" && (
        <div role="status">
          <p>
            Published <Link href={`/datasets/${encodeURIComponent(state.value.datasetRef)}`}>{state.value.datasetRef}</Link> (split digest{" "}
            <span className="lab-id">{state.value.splitDigest}</span>).
          </p>
          {leakageWarnings({ derived: state.value }).map((w) => (
            <p key={w}>{w}</p>
          ))}
        </div>
      )}
    </form>
  );
}

const LIFETIMES = [
  [3600, "1 hour"],
  [86_400, "1 day"],
  [604_800, "7 days"],
] as const;

export function ExportForm({ datasetRef, exportVersion }: { datasetRef: string; exportVersion: Action<ExportRecord> }) {
  const [state, action, pending] = useActionState<ActionState<ExportRecord>, FormData>(exportVersion, IDLE);
  return (
    <form onSubmit={keep(() => action)} aria-label="Export this version" className="lab-stack">
      <input type="hidden" name="dataset_ref" value={datasetRef} />
      <p className={styles.note}>
        Exports this immutable version for training: the holdout and every sample whose grant no longer allows training are left out and counted.
      </p>
      <Field label="Download links expire after">
        <Input render={<select />} name="ttl_s" defaultValue="3600">
          {LIFETIMES.map(([s, label]) => (
            <option key={s} value={s}>
              {label}
            </option>
          ))}
        </Input>
      </Field>
      <Button type="submit" pending={pending}>
        {pending ? "Exporting…" : "Export for training"}
      </Button>
      <Problem state={state} />
      {state.status === "ok" && (
        <div role="status">
          <p>
            Export <span className="lab-id">{state.value.exportId}</span> of <span className="lab-id">{state.value.datasetRef}</span>, until{" "}
            {state.value.expiresAt}. The holdout and any sample whose grant no longer allows training are left out ({state.value.omitted.length} omitted).
          </p>
          {state.value.omitted.length > 0 && (
            <ul>
              {state.value.omitted.map((o) => (
                <li key={o.sampleId}>
                  <span className="lab-id">{o.sampleId}</span>: {o.reason}
                </li>
              ))}
            </ul>
          )}
          <ul>
            {state.value.parts.map((part, n) => (
              <li key={part.sha256}>
                <a href={`/datasets/exports/${state.value.exportId}/${n}`} download>
                  Part {n}
                </a>{" "}
                ({part.items} items, sha256 <span className="lab-id">{part.sha256}</span>)
              </li>
            ))}
          </ul>
        </div>
      )}
    </form>
  );
}
