// N4: one version - its splits, samples, provenance and restrictions - with derive and export. A ref of
// another provider is a 404, the same as an unknown one.
import { notFound } from "next/navigation";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { datasetsPort } from "@/lib/services/datasets/server";
import { FAILURE_COPY, restrictedCopy, splitSummary } from "@/lib/services/datasets/views";
import { DeriveForm, ExportForm } from "../forms";

export default async function Version({ params }: { params: Promise<{ ref: string }> }) {
  const workspace = await requireProviderWorkspace();
  const ref = decodeURIComponent((await params).ref);
  const status = await (await datasetsPort()).version(workspace.providerId, ref);
  if (!status.ok && status.error === "not_found") notFound();
  if (!status.ok) return <p role="alert">{FAILURE_COPY[status.error]}</p>;
  const { samples, parentRefs } = status.value;
  const restricted = samples.filter((s) => s.restricted !== null).length;
  return (
    <>
      <h1>Version</h1>
      <p>
        <code>{ref}</code>
      </p>
      {parentRefs.length > 0 && <p>Derived from {parentRefs.join(", ")}.</p>}
      {restricted > 0 && (
        <p role="alert">
          {restricted} of {samples.length} samples are restricted now and excluded from reads, derivations and exports.
        </p>
      )}
      <table>
        <caption>Splits</caption>
        <thead>
          <tr>
            <th scope="col">Split</th>
            <th scope="col">Samples</th>
            <th scope="col">Restricted</th>
          </tr>
        </thead>
        <tbody>
          {splitSummary(status.value).map((row) => (
            <tr key={row.split}>
              <td>{row.split}</td>
              <td>{row.samples}</td>
              <td>{row.restricted}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table>
        <caption>Samples and provenance</caption>
        <thead>
          <tr>
            <th scope="col">Sample</th>
            <th scope="col">Split</th>
            <th scope="col">Source</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {samples.map((s) => (
            <tr key={s.sampleId}>
              <td>{s.sampleId}</td>
              <td>{s.split}</td>
              <td>{s.trace ? `trace ${s.trace.requestId} (content until ${s.trace.contentUntil})` : s.sourceRef}</td>
              <td>{s.restricted === null ? "readable" : restrictedCopy(s.restricted)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {workspace.role !== "viewer" && (
        <>
          <h2>Freeze a new version</h2>
          <p>Over this version as the base, its holdout stays frozen: new relatives of holdout samples are left out.</p>
          <DeriveForm base={ref} />
          <h2>Export</h2>
          <ExportForm datasetRef={ref} />
        </>
      )}
    </>
  );
}
