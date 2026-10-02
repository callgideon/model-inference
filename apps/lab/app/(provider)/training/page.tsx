import { randomUUID } from "node:crypto";
import Link from "next/link";
import type { Membership } from "@/lib/auth/access";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { firstFailure } from "@/lib/services/common";
import { datasetsPort } from "@/lib/services/datasets/server";
import { approveCheckpoint, approveTeachers, importCheckpoint, planTeachers, prepareTraining, runAction } from "@/lib/services/pipelines/actions";
import { EXPORT_FORMATS, holds, isPreview, pipelinesPort, type Checkpoint, type Result, type TrainingRun } from "@/lib/services/pipelines/port";
import { checkpointRows, refusalCopy, REFUSAL_COPY, runRows, teacherRows, type RunAction } from "@/lib/services/pipelines/view";
import { PreviewNote } from "@/components/preview-note";
import { DatasetOptions } from "../datasets/options";
import { canLookUp, GATED_COPY, LOOKUP_LABEL } from "./view";

export const metadata = { title: "Training · infrx Lab" };

const LABEL: Record<RunAction, string> = { submit: "Submit bundle", finish: "Mark training finished", cancel: "Cancel run" };
const TEACHER_UNAVAILABLE = "Teacher batches are not available here: live teacher labelling is not enabled for this workspace.";

// P4 + UX-09 (L-10): the run and checkpoint records behind their own gate, so a listing that cannot be
// read (SR-AP10-2) leaves the heading and the separately read teacher section in place and offers no
// action on what it cannot see. An unknown submission is offered a lookup, never a resubmit.
async function Records({ workspace, runs, checkpoints }: { workspace: Membership; runs: Result<TrainingRun[]>; checkpoints: Result<Checkpoint[]> }) {
  if (!runs.ok || !checkpoints.ok) return <p role="alert">{REFUSAL_COPY[firstFailure(runs, checkpoints)!]}</p>;
  const port = pipelinesPort();
  const bundles = await Promise.all(runs.value.map((r) => port.bundle(workspace, r.externalRunId)));
  const rows = runRows(workspace.role, runs.value);
  const writer = holds(workspace.role, "run_evaluation");
  return (
    <>
      <h2 id="runs">Runs</h2>
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
                  {canLookUp(runs.value[i].state, writer) && (
                    <form action={runAction}>
                      <input type="hidden" name="externalRunId" value={r.id} />
                      <input type="hidden" name="op" value="submit" />
                      <button type="submit">{LOOKUP_LABEL}</button>
                    </form>
                  )}
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
      <h2 id="checkpoints">Checkpoints</h2>
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
    </>
  );
}

// P4: external training over P3's records: the manual bundle, its run, returned checkpoints and their
// held-out evaluation. Every outcome shown is whatever the records say after the redirect back here.
export default async function Training({ searchParams }: PageProps<"/training">) {
  const workspace = await requireProviderWorkspace();
  const query = await searchParams;
  const refused = refusalCopy(query.refused);
  const port = pipelinesPort();
  const writer = holds(workspace.role, "run_evaluation");
  const [runs, checkpoints, batches, versions] = await Promise.all([
    port.runs(workspace), port.checkpoints(workspace), port.teacherBatches(workspace),
    writer ? (await datasetsPort()).versions(workspace.providerId) : null,
  ]);
  const gated = firstFailure(runs, checkpoints) !== null;
  return (
    <>
      <h1>Training</h1>
      <p>Teacher labelling, and training on your own compute from a versioned bundle, with returned checkpoints qualified on the run&apos;s pinned holdout.</p>
      <nav aria-label="Training sections">
        <a href="#runs">Runs</a> · <a href="#checkpoints">Checkpoints</a> · <a href="#teacher">Teacher labelling</a>
      </nav>
      {isPreview() && <PreviewNote records="pipeline" service="pipeline" />}
      {refused && <p role="alert">{refused}</p>}
      <p>Automatic training connectors are not offered: you download the bundle and train on your own compute, and it reserves nothing. Provider-reported training metrics never make a candidate eligible; only a succeeded evaluation on the run&apos;s pinned holdout does.</p>
      <Records workspace={workspace} runs={runs} checkpoints={checkpoints} />
      {gated && <p role="note">{GATED_COPY}</p>}
      <h2 id="teacher">Teacher labelling</h2>
      <p>A teacher model labels a dataset version&apos;s train and validation samples in chunks; the holdout is never sent. A batch is a dry run until an administrator approves it within its USD budget.</p>
      {!batches.ok ? <p role="note">{TEACHER_UNAVAILABLE}</p> : batches.value.length === 0 ? <p>No teacher batches yet.</p> : (
        <table>
          <thead>
            <tr><th>Batch</th><th>Teacher</th><th>Budget and ceiling</th><th>Plan</th><th>Chunks</th><th>Status</th><th /></tr>
          </thead>
          <tbody>
            {teacherRows(workspace.role, batches.value).map((b) => (
              <tr key={b.id}>
                <td>{b.id} · {b.dataset}</td><td>{b.teacher}</td><td>{b.budget} · {b.ceiling}</td><td>{b.plan}. {b.labels}</td>
                <td><ul>{b.chunks.map((c, i) => <li key={i}>{c}</li>)}</ul></td><td>{b.status}</td>
                <td>
                  {b.approvable && (
                    <form action={approveTeachers}>
                      <input type="hidden" name="batchId" value={b.id} />
                      <button type="submit">Approve the live batch within its budget</button>
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
          {batches.ok && (
            <form action={planTeachers}>
              <h2>Plan a teacher batch</h2>
              <input type="hidden" name="batchId" value={randomUUID()} />
              <label>Dataset version <input name="datasetRef" required list="dataset-versions" placeholder="lab:dataset:…@sha256:…" /></label>
              <label>Rubric <input name="rubricRef" required placeholder="lab:rubric:…" /></label>
              <label>Teacher model <input name="teacherModel" required /></label>
              <label>Prompt version <input name="promptVersion" required /></label>
              <label>Samples per chunk <input name="chunkSize" required type="number" min={1} max={200} defaultValue={50} /></label>
              <fieldset>
                <legend>Teacher budget (USD)</legend>
                <label>Payer <input name="payerRef" required placeholder="lab:payer:… (pays the teacher)" /></label>
                <label>Budget, USD <input name="budgetUsd" required placeholder="10.00000000" pattern="(0|[1-9][0-9]{0,11})\.[0-9]{8}" /></label>
              </fieldset>
              <button type="submit">Plan a dry run (nothing is sent)</button>
            </form>
          )}
          {!gated && (
            <form action={prepareTraining}>
              <h2>Prepare a training bundle</h2>
              <input type="hidden" name="externalRunId" value={randomUUID()} />
              <label>Dataset version <input name="datasetRef" required list="dataset-versions" placeholder="lab:dataset:…@sha256:…" /></label>
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
          )}
          {runs.ok && runs.value.length > 0 && (
            <form action={importCheckpoint}>
              <h2>Import a checkpoint</h2>
              <input type="hidden" name="checkpointId" value={randomUUID()} />
              <label>Run <select name="externalRunId">{runs.value.map((r) => <option key={r.externalRunId} value={r.externalRunId}>{r.externalRunId}</option>)}</select></label>
              <label>Artifact key <input name="artifactKey" required placeholder={`lab/${workspace.providerId}/training/<run>/…`} /></label>
              <label>Artifact digest <input name="artifactDigest" required placeholder="sha256:…" /></label>
              <button type="submit">Import checkpoint</button>
            </form>
          )}
          <DatasetOptions id="dataset-versions" versions={versions} />
        </>
      )}
    </>
  );
}
