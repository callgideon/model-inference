// N4 + UX-06 (L-07): the provider's dataset library and the guided import. Server records only: a failed
// read is that failure (never an empty list), and the page keeps its heading in every state.
import { randomUUID } from "node:crypto";
import Link from "next/link";
import { buttonClass } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { ServiceState } from "@/components/ui/service-state";
import { holds } from "@/lib/auth/access";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { MAX_UPLOAD_BYTES } from "@/lib/services/datasets/flows";
import { datasetsPort } from "@/lib/services/datasets/server";
import { FAILURE_COPY, importTemplate } from "@/lib/services/datasets/views";
import { previewImportAction, startImportAction } from "./actions";
import styles from "./datasets.module.css";
import { ImportWizard } from "./forms";

export const metadata = { title: "Datasets · infrx Lab" };

export default async function Datasets() {
  const workspace = await requireProviderWorkspace();
  const versions = await (await datasetsPort()).versions(workspace.providerId);
  const writer = holds(workspace.role, "run_evaluation"); // the flows' own rule (datasets feed evaluations and training)
  return (
    <div className="lab-stack">
      <PageHeader
        title="Datasets"
        purpose="Immutable dataset versions: import JSONL, inspect lineage and splits, derive new versions and export permitted samples."
        actions={writer ? <a className={buttonClass("primary")} href="#import">Import dataset</a> : undefined}
      />
      {!versions.ok ? (
        <ServiceState
          state={versions.error === "denied" ? "denied" : "unavailable"}
          title="We couldn't load dataset versions"
          explanation={FAILURE_COPY[versions.error]}
          action={<Link className={buttonClass()} href="/datasets">Try again</Link>}
        />
      ) : versions.value.length === 0 ? (
        <ServiceState state="empty" title="No dataset versions yet" explanation="Import a benchmark or an annotation export. A version cannot be edited once published; changes make a new version." />
      ) : (
        <div className={styles.scroll} role="region" aria-label="Dataset versions" tabIndex={0}>
          <table className={styles.table}>
            <caption>Dataset versions</caption>
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">Made by</th>
                <th scope="col">Samples</th>
              </tr>
            </thead>
            <tbody>
              {versions.value.map((v) => (
                <tr key={v.datasetRef}>
                  <td>
                    <Link href={`/datasets/${encodeURIComponent(v.datasetRef)}`}>
                      {v.datasetId} v{v.version}
                    </Link>
                  </td>
                  <td>{v.derivation}</td>
                  <td>{v.samples}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {writer ? (
        <section id="import" aria-labelledby="import-title" className={`lab-stack ${styles.form}`}>
          <h2 id="import-title">Import a dataset</h2>
          <p className={styles.note}>
            JSONL up to {MAX_UPLOAD_BYTES.toLocaleString("en-US")} bytes here. A published import is a new private dataset version in this workspace, not a public release.
          </p>
          <ImportWizard
            preview={previewImportAction}
            start={startImportAction}
            template={importTemplate({ providerId: workspace.providerId, importId: randomUUID(), datasetId: randomUUID(), createdAt: new Date().toISOString() })}
          />
        </section>
      ) : (
        <p className={styles.note}>Your role can read dataset versions. Importing, deriving and exporting need a developer or administrator role.</p>
      )}
    </div>
  );
}
