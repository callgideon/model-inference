"use client";
// N4 client forms: the import wizard (preview, then import), derive and export. Each renders only what
// its server action returned; a pending action shows as pending and a failure is never shown as success.
import Link from "next/link";
import { useActionState } from "react";
import type { ActionState } from "@/lib/services/datasets/flows";
import type { Derived, ExportRecord, ImportJob, Preview } from "@/lib/services/datasets/port";
import { leakageWarnings } from "@/lib/services/datasets/views";
import { deriveAction, exportAction, previewImportAction, startImportAction } from "./actions";

const IDLE = { status: "idle" } as const;

function Problem({ state }: { state: ActionState<unknown> }) {
  if (state.status !== "error") return null;
  return (
    <div role="alert">
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

export function ImportWizard() {
  const [preview, previewAction, previewing] = useActionState<ActionState<Preview>, FormData>(previewImportAction, IDLE);
  const [started, startAction, starting] = useActionState<ActionState<ImportJob>, FormData>(startImportAction, IDLE);
  return (
    <form aria-label="Import a dataset">
      <label>
        Import spec (infrx.dataset_import.1, JSON)
        <textarea name="spec" required rows={10} />
      </label>
      <label>
        Rows (JSONL)
        <input type="file" name="file" accept=".jsonl,application/x-ndjson,application/json" required />
      </label>
      <label>
        <input type="checkbox" name="accept_rejects" /> Publish even if some rows are rejected (they are listed, never silently dropped)
      </label>
      <button formAction={previewAction} disabled={previewing}>
        {previewing ? "Checking…" : "Preview mapping"}
      </button>
      <button formAction={startAction} disabled={starting}>
        {starting ? "Starting…" : "Import"}
      </button>
      <Problem state={preview} />
      <Problem state={started} />
      {preview.status === "ok" && (
        <section aria-label="Preview">
          <h3>Fields</h3>
          <ul>
            {Object.entries(preview.value.fields).map(([name, types]) => (
              <li key={name}>
                {name}: {types.join(" | ")}
              </li>
            ))}
          </ul>
          <h3>First rows</h3>
          <ul>
            {preview.value.rows.map((row) => (
              <li key={row.line}>
                line {row.line}: {row.reason ? `refused (${row.reason})` : JSON.stringify(row.mapped)}
              </li>
            ))}
          </ul>
        </section>
      )}
    </form>
  );
}

export function DeriveForm({ base }: { base: string }) {
  const [state, action, pending] = useActionState<ActionState<Derived>, FormData>(deriveAction, IDLE);
  return (
    <form action={action} aria-label="Freeze a new version">
      <input type="hidden" name="base" value={base} />
      <label>
        New dataset id (UUID) <input name="dataset_id" required />
      </label>
      <label>
        Version <input name="version" inputMode="numeric" defaultValue="1" required />
      </label>
      <label>
        Split seed <input name="seed" inputMode="numeric" defaultValue="0" required />
      </label>
      <label>
        Train basis points <input name="train_bp" inputMode="numeric" defaultValue="8000" required />
      </label>
      <label>
        Validation basis points <input name="validation_bp" inputMode="numeric" defaultValue="1000" required />
      </label>
      <label>
        Versions to add (refs, one per line) <textarea name="add" rows={3} />
      </label>
      <button disabled={pending}>{pending ? "Deriving…" : "Derive version"}</button>
      <Problem state={state} />
      {state.status === "ok" && (
        <div role="status">
          <p>
            Published <Link href={`/datasets/${encodeURIComponent(state.value.datasetRef)}`}>{state.value.datasetRef}</Link> (split digest{" "}
            {state.value.splitDigest}).
          </p>
          {leakageWarnings({ derived: state.value }).map((w) => (
            <p key={w}>{w}</p>
          ))}
        </div>
      )}
    </form>
  );
}

export function ExportForm({ datasetRef }: { datasetRef: string }) {
  const [state, action, pending] = useActionState<ActionState<ExportRecord>, FormData>(exportAction, IDLE);
  return (
    <form action={action} aria-label="Export this version">
      <input type="hidden" name="dataset_ref" value={datasetRef} />
      <label>
        Link lifetime (seconds) <input name="ttl_s" inputMode="numeric" defaultValue="3600" required />
      </label>
      <button disabled={pending}>{pending ? "Exporting…" : "Export for training"}</button>
      <Problem state={state} />
      {state.status === "ok" && (
        <div role="status">
          <p>
            Export {state.value.exportId}, until {state.value.expiresAt}. The holdout and any sample whose grant no longer allows
            training are left out ({state.value.omitted.length} omitted).
          </p>
          <ul>
            {state.value.parts.map((part, n) => (
              <li key={part.sha256}>
                <a href={`/datasets/exports/${state.value.exportId}/${n}`} download>
                  Part {n}
                </a>{" "}
                ({part.items} items, sha256 {part.sha256})
              </li>
            ))}
          </ul>
        </div>
      )}
    </form>
  );
}
