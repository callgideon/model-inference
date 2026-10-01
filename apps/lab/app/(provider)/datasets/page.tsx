// N4: the provider's dataset versions and the import wizard. Server records only: a failed read is
// shown as that failure, never as an empty list.
import Link from "next/link";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { datasetsPort } from "@/lib/services/datasets/server";
import { FAILURE_COPY } from "@/lib/services/datasets/views";
import { ImportWizard } from "./forms";

export const metadata = { title: "Datasets · infrx Lab" };

export default async function Datasets() {
  const workspace = await requireProviderWorkspace();
  const versions = await (await datasetsPort()).versions(workspace.providerId);
  return (
    <>
      <h1>Datasets</h1>
      {!versions.ok ? (
        <p role="alert">{FAILURE_COPY[versions.error]}</p>
      ) : versions.value.length === 0 ? (
        <p>No dataset versions yet. Import a benchmark or an annotation export below.</p>
      ) : (
        <table>
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
      )}
      {workspace.role === "viewer" ? (
        <p>{FAILURE_COPY.denied}</p>
      ) : (
        <section aria-label="Import">
          <h2>Import</h2>
          <ImportWizard />
        </section>
      )}
    </>
  );
}
