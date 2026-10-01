import { randomUUID } from "node:crypto";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { adjudicateSample, assignReviewer, exportLabels, importLabels, reviewLabel } from "@/lib/services/pipelines/actions";
import { ADAPTERS, holds, isPreview, pipelinesPort } from "@/lib/services/pipelines/port";
import { exportRows, importRows, labelRows, refusalCopy, REFUSAL_COPY } from "@/lib/services/pipelines/view";

export const metadata = { title: "Annotations · infrx Lab" };

// P4: label import, review, adjudication and train-only export over P1's records. Every outcome shown
// is whatever the records say after the redirect back here.
export default async function Annotations({ searchParams }: PageProps<"/annotations">) {
  const workspace = await requireProviderWorkspace();
  const query = await searchParams;
  const refused = refusalCopy(query.refused);
  const shape = new RegExp(`^lab:dataset:${workspace.providerId}:[0-9a-f-]{36}@sha256:[0-9a-f]{64}$`);
  const dataset = typeof query.dataset === "string" && shape.test(query.dataset) ? query.dataset : null;
  const port = pipelinesPort();
  const [imports, exports, labels, disputes] = await Promise.all([
    port.imports(workspace), port.exports(workspace),
    dataset === null ? null : port.labels(workspace, dataset), dataset === null ? null : port.disagreements(workspace, dataset),
  ]);
  const failed = [imports, exports, labels, disputes].find((r) => r !== null && !r.ok);
  return (
    <>
      <h1>Annotations</h1>
      {isPreview() && <p role="note">Preview: pipeline records come from an in-memory stand-in, not the pipeline service.</p>}
      {refused && <p role="alert">{refused}</p>}
      <form method="get">
        <label>Dataset version <input name="dataset" required defaultValue={dataset ?? ""} placeholder="lab:dataset:…@sha256:…" /></label>
        <button type="submit">Open</button>
      </form>
      {failed && !failed.ok ? (
        <p role="alert">{REFUSAL_COPY[failed.reason]}</p>
      ) : (
        <>
          {dataset !== null && labels?.ok && disputes?.ok && (
            <>
              <h2>Labels</h2>
              <p>Imported and synthetic labels are never ground truth; only a human review in this Lab is.</p>
              {labels.value.length === 0 ? (
                <p>No labels for this dataset version yet.</p>
              ) : (
                <table>
                  <thead>
                    <tr><th>Sample</th><th>Kind</th><th>Ground truth</th><th>State</th><th>Value</th><th>Reviewer</th><th>Review</th></tr>
                  </thead>
                  <tbody>
                    {labelRows(workspace.role, labels.value).map((l) => (
                      <tr key={l.id}>
                        <td>{l.sample}</td><td>{l.kind}</td><td>{l.truth}</td><td>{l.state}</td><td><code>{l.value}</code></td><td>{l.reviewer}</td>
                        <td>
                          {l.reviewable && (
                            <form action={reviewLabel}>
                              <input type="hidden" name="datasetRef" value={dataset} />
                              <input type="hidden" name="annotationRef" value={l.id} />
                              <select name="decision" defaultValue="accepted"><option value="accepted">Accept</option><option value="rejected">Reject</option></select>
                              <input name="correction" placeholder="Correction (JSON; rejects this label)" />
                              <input name="rubricRef" required placeholder="lab:rubric:…" />
                              <button type="submit">Review</button>
                            </form>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <h2>Disagreements</h2>
              {disputes.value.length === 0 ? (
                <p>No sample has live labels that disagree.</p>
              ) : (
                <ul>
                  {disputes.value.map((d) => (
                    <li key={d.sampleId}>
                      {d.sampleId}: {d.annotationRefs.join(", ")}
                      {holds(workspace.role, "run_evaluation") && (
                        <form action={adjudicateSample}>
                          <input type="hidden" name="datasetRef" value={dataset} />
                          <input type="hidden" name="sampleId" value={d.sampleId} />
                          <input name="value" required placeholder="Adjudicated value (JSON)" />
                          <input name="rubricRef" required placeholder="lab:rubric:…" />
                          <button type="submit">Adjudicate</button>
                        </form>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {holds(workspace.role, "manage_members") && (
                <form action={assignReviewer}>
                  <h2>Assign a reviewer</h2>
                  <input type="hidden" name="datasetRef" value={dataset} />
                  <label>Sample <input name="sampleId" required /></label>
                  <label>Reviewer (member id) <input name="reviewerId" required /></label>
                  <label>Rubric <input name="rubricRef" required placeholder="lab:rubric:…" /></label>
                  <button type="submit">Assign</button>
                </form>
              )}
              {holds(workspace.role, "run_evaluation") && (
                <>
                  <form action={importLabels}>
                    <h2>Import labels</h2>
                    <p>One JSON object per line. A row that claims ground truth, names another dataset&apos;s sample or a sample without a current grant is rejected and listed.</p>
                    <input type="hidden" name="importId" value={randomUUID()} />
                    <input type="hidden" name="datasetRef" value={dataset} />
                    <label>Rubric <input name="rubricRef" required placeholder="lab:rubric:…" /></label>
                    <label>Rows (JSONL) <textarea name="rows" required rows={6} /></label>
                    <button type="submit">Import</button>
                  </form>
                  <form action={exportLabels}>
                    <h2>Export training labels</h2>
                    <p>Train split only: validation, holdout, disputed and ungranted samples are left out and listed with their reason.</p>
                    <input type="hidden" name="exportId" value={randomUUID()} />
                    <input type="hidden" name="datasetRef" value={dataset} />
                    <label>Adapter <select name="adapter">{ADAPTERS.map((a) => <option key={a} value={a}>{a}</option>)}</select></label>
                    <label>Expires after (seconds) <input name="ttlS" required type="number" min={1} max={604800} defaultValue={86400} /></label>
                    <button type="submit">Export</button>
                  </form>
                </>
              )}
            </>
          )}
          <h2>Imports</h2>
          {imports?.ok && imports.value.length === 0 ? (
            <p>No label imports yet.</p>
          ) : (
            <ul>
              {imports?.ok && importRows(imports.value).map((r) => (
                <li key={r.id}>
                  {r.dataset}: {r.summary}
                  {r.rejected.length > 0 && <ul>{r.rejected.map((x) => <li key={x}>{x}</li>)}</ul>}
                </li>
              ))}
            </ul>
          )}
          <h2>Exports</h2>
          {exports?.ok && exports.value.length === 0 ? (
            <p>No label exports yet.</p>
          ) : (
            <ul>
              {exports?.ok && exportRows(exports.value).map((e) => (
                <li key={e.id}>
                  <p>{e.id} · {e.adapter} · {e.items} examples · expires {e.expires}</p>
                  <details>
                    <summary>Lineage and omissions</summary>
                    <ul>{e.lineage.map((x) => <li key={x}>{x}</li>)}</ul>
                    <ul>{e.omitted.map((x) => <li key={x}>Omitted {x}</li>)}</ul>
                  </details>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </>
  );
}
