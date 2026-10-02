// N4 + UX-06 (L-07): one version - lineage, splits, samples, provenance and restrictions - with derive
// and export. A ref of another provider is a 404, the same as an unknown one.
import { randomUUID } from "node:crypto";
import Link from "next/link";
import { notFound } from "next/navigation";
import { buttonClass } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { PageHeader } from "@/components/ui/page-header";
import { ServiceState } from "@/components/ui/service-state";
import { holds } from "@/lib/auth/access";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { datasetsPort } from "@/lib/services/datasets/server";
import { FAILURE_COPY, restrictedCopy, splitSummary } from "@/lib/services/datasets/views";
import { deriveAction, exportAction } from "../actions";
import styles from "../datasets.module.css";
import { DeriveForm, ExportForm } from "../forms";

export const metadata = { title: "Dataset version · infrx Lab" };

const CRUMBS = [{ href: "/datasets", label: "Datasets" }];

export default async function Version({ params }: PageProps<"/datasets/[ref]">) {
  const workspace = await requireProviderWorkspace();
  const ref = decodeURIComponent((await params).ref);
  const status = await (await datasetsPort()).version(workspace.providerId, ref);
  if (!status.ok && status.error === "not_found") notFound();
  if (!status.ok) {
    return (
      <div className="lab-stack">
        <PageHeader breadcrumb={CRUMBS} title="Dataset version" />
        <ServiceState
          state={status.error === "denied" ? "denied" : "unavailable"}
          title="We couldn't load this version"
          explanation={FAILURE_COPY[status.error]}
          action={<Link className={buttonClass()} href={`/datasets/${encodeURIComponent(ref)}`}>Try again</Link>}
        />
      </div>
    );
  }
  const { samples, parentRefs } = status.value;
  const restricted = samples.filter((s) => s.restricted !== null).length;
  return (
    <div className="lab-stack">
      <PageHeader breadcrumb={CRUMBS} title="Dataset version" purpose="An immutable version: its lineage, splits and samples as the backend records them." />
      <p>
        <code className="lab-id">{ref}</code> <CopyButton value={ref} label="Copy dataset version ref" />
      </p>
      {parentRefs.length > 0 ? (
        <section aria-label="Lineage">
          <h2>Derived from</h2>
          <ul>
            {parentRefs.map((p) => (
              <li key={p}>
                <Link className="lab-id" href={`/datasets/${encodeURIComponent(p)}`}>{p}</Link>
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <p className={styles.note}>Imported directly: no parent version.</p>
      )}
      {restricted > 0 && (
        <p role="alert">
          {restricted} of {samples.length} samples are restricted now and excluded from reads, derivations and exports.
        </p>
      )}
      <div className={styles.scroll} role="region" aria-label="Splits" tabIndex={0}>
        <table className={styles.table}>
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
      </div>
      <div className={styles.scroll} role="region" aria-label="Samples and provenance" tabIndex={0}>
        <table className={styles.table}>
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
                <td className="lab-id">{s.sampleId}</td>
                <td>{s.split}</td>
                <td className="lab-id">{s.trace ? `trace ${s.trace.requestId} (content until ${s.trace.contentUntil})` : s.sourceRef}</td>
                <td>{s.restricted === null ? "readable" : restrictedCopy(s.restricted)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {holds(workspace.role, "run_evaluation") && (
        <>
          <section aria-labelledby="derive-title" className={`lab-stack ${styles.form}`}>
            <h2 id="derive-title">Freeze a new version</h2>
            <p className={styles.note}>Over this version as the base, its holdout stays frozen: new relatives of holdout samples are left out.</p>
            <DeriveForm base={ref} datasetId={randomUUID()} derive={deriveAction} />
          </section>
          <section aria-labelledby="export-title" className={`lab-stack ${styles.form}`}>
            <h2 id="export-title">Export</h2>
            <ExportForm datasetRef={ref} exportVersion={exportAction} />
          </section>
        </>
      )}
    </div>
  );
}
