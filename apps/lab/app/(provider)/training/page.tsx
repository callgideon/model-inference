import { randomUUID } from "node:crypto";
import Link from "next/link";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { approveCheckpoint, importCheckpoint, prepareTraining, runAction } from "@/lib/services/pipelines/actions";
import { EXPORT_FORMATS, holds, isPreview, pipelinesPort } from "@/lib/services/pipelines/port";
import { checkpointRows, refusalCopy, REFUSAL_COPY, runRows, type RunAction } from "@/lib/services/pipelines/view";

export const metadata = { title: "Training · infrx Lab" };

const LABEL: Record<RunAction, string> = { submit: "Submit bundle", finish: "Mark training finished", cancel: "Cancel run" };

// P4: external training over P3's records: the manual bundle, its run, returned checkpoints and their
// held-out evaluation. Every outcome shown is whatever the records say after the redirect back here.
export default async function Training({ searchParams }: PageProps<"/training">) {
  const workspace = await requireProviderWorkspace();
  const query = await searchParams;
  const refused = refusalCopy(query.refused);
  const actor = { providerId: workspace.providerId, role: workspace.role };
  const port = pipelinesPort();
  const [runs, checkpoints] = await Promise.all([port.runs(actor), port.checkpoints(actor)]);
  if (!runs.ok || !checkpoints.ok) return <p role="alert">{REFUSAL_COPY[!runs.ok ? runs.reason : checkpoints.ok ? "unavailable" : checkpoints.reason]}</p>;
  const bundles = await Promise.all(runs.value.map((r) => port.bundle(actor, r.externalRunId)));
  const rows = runRows(workspace.role, runs.value);
  const writer = holds(workspace.role, "run_evaluation");
  return (
    <>
      <h1>Training</h1>
      {isPreview() && <p role="note">Preview: pipeline records come from an in-memory stand-in, not the pipeline service.</p>}
      {refused && <p role="alert">{refused}</p>}
      <p>Automatic training connectors are not offered: you download the bundle and train on your own compute, and it reserves nothing. Provider-reported training metrics never make a candidate eligible; only a succeeded evaluation on the run&apos;s pinned holdout does.</p>
      <h2>Runs</h2>
      {rows.length === 0 ? (
        <p>No training runs yet.</p>
      ) : (
        <table>
          <thead>
            <tr><th>Run</th><th>Config</th><th>Data</th><th>Holdout</th><th>Budget and payer</th><th>Cost</th><th>State</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.id}>
                <td>{r.id} · {r.connector}</td><td>{r.config}</td><td>{r.data}</td><td>{r.holdout}</td>
                <td>{r.budget} · payer {r.payer}</td><td>{r.cost}</td><td>{r.state}</td>
                <td>
                  {r.actions.map((a) => (
                    <form key={a} action={runAction}>
                      <input type="hidden" name="externalRunId" value={r.id} />
                      <input type="hidden" name="op" value={a} />
                      <button type="submit">{LABEL[a]}</button>
                    </form>
                  ))}
                  {bundles[i].ok && (
                    <details>
                      <summary>Bundle</summary>
                      <pre>{bundles[i].value}</pre>
                    </details>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2>Checkpoints</h2>
      {checkpoints.value.length === 0 ? (
        <p>No checkpoints returned yet.</p>
      ) : (
        <table>
          <thead>
            <tr><th>Checkpoint</th><th>Lineage</th><th>Artifact</th><th>State</th><th /></tr>
          </thead>
          <tbody>
            {checkpointRows(workspace.role, checkpoints.value, runs.value).map((c) => (
              <tr key={c.id}>
                <td>{c.id}</td><td>{c.lineage}</td><td>{c.digest}</td><td>{c.state}</td>
                <td>
                  {c.comparison && <Link href={c.comparison}>Held-out comparison</Link>}
                  {c.approvable && (
                    <form action={approveCheckpoint}>
                      <input type="hidden" name="externalRunId" value={c.run} />
                      <input type="hidden" name="checkpointId" value={c.id} />
                      <button type="submit">Approve as a candidate (not public)</button>
                    </form>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {writer && (
        <>
          <form action={prepareTraining}>
            <h2>Prepare a training bundle</h2>
            <input type="hidden" name="externalRunId" value={randomUUID()} />
            <label>Dataset version <input name="datasetRef" required placeholder="lab:dataset:…@sha256:…" /></label>
            <label>Export format <select name="exportFormat">{EXPORT_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}</select></label>
            <label>Export id <input name="exportId" required /></label>
            <label>Objective <select name="objective"><option value="sft">SFT</option><option value="preference">Preference</option></select></label>
            <label>Adaptation <select name="adaptation"><option value="lora">LoRA</option><option value="full">Full</option></select></label>
            <label>Base model <input name="baseModel" required /></label>
            <fieldset>
              <legend>Budget (USD)</legend>
              <label>Payer <input name="payerRef" required placeholder="lab:payer:…" /></label>
              <label>Limit, USD <input name="limitUsd" required placeholder="25.00000000" pattern="(0|[1-9][0-9]{0,11})\.[0-9]{8}" /></label>
            </fieldset>
            <button type="submit">Prepare bundle</button>
          </form>
          {runs.value.length > 0 && (
            <form action={importCheckpoint}>
              <h2>Import a checkpoint</h2>
              <input type="hidden" name="checkpointId" value={randomUUID()} />
              <label>Run <select name="externalRunId">{runs.value.map((r) => <option key={r.externalRunId} value={r.externalRunId}>{r.externalRunId}</option>)}</select></label>
              <label>Artifact key <input name="artifactKey" required placeholder={`lab/${workspace.providerId}/training/<run>/…`} /></label>
              <label>Artifact digest <input name="artifactDigest" required placeholder="sha256:…" /></label>
              <button type="submit">Import checkpoint</button>
            </form>
          )}
        </>
      )}
    </>
  );
}
