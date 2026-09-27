import { requireProviderWorkspace } from "@/lib/auth/guard";
import { proposeChange, smokeDeployment } from "@/lib/services/control/actions";
import { controlPort } from "@/lib/services/control/port";
import { deploymentRows, refusalCopy, REFUSAL_COPY, type Action } from "@/lib/services/control/view";

export const metadata = { title: "Deployments · infrx Lab" };

const LABEL: Record<Action, string> = { smoke: "Run dev smoke", publish: "Propose publication", rollback: "Propose rollback" };

// L4: pinned identities, dev/prod visibility and proposals, from the records only. A button's
// outcome is whatever the records say after the redirect back here.
export default async function Deployments({ searchParams }: PageProps<"/deployments">) {
  const workspace = await requireProviderWorkspace();
  const refused = refusalCopy((await searchParams).refused);
  const actor = { providerId: workspace.providerId, role: workspace.role };
  const port = controlPort();
  const [deployments, proposals] = await Promise.all([port.deployments(actor), port.proposals(actor)]);
  if (!deployments.ok || !proposals.ok) return <p role="alert">{REFUSAL_COPY.unavailable}</p>;
  const rows = deploymentRows(workspace.role, deployments.value, proposals.value);
  return (
    <>
      <h1>Deployments</h1>
      {refused && <p role="alert">{refused}</p>}
      {rows.length === 0 ? (
        <p>No deployments yet. Register a model to create a private dev deployment.</p>
      ) : (
        <table>
          <thead>
            <tr><th>Model</th><th>Serving</th><th>Runtime</th><th>Schema</th><th>Rate card</th><th>Visibility</th><th>State</th><th>Smoke</th><th>Request</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.model}</td><td>{r.serving}</td><td>{r.runtime}</td><td>{r.schema}</td><td>{r.rate}</td><td>{r.where}</td><td>{r.state}</td><td>{r.smoke}</td>
                <td>{r.pending ?? "—"}</td>
                <td>
                  {r.actions.map((a) => (
                    <form key={a} action={a === "smoke" ? smokeDeployment : proposeChange}>
                      <input type="hidden" name="deploymentRevisionId" value={r.id} />
                      {a !== "smoke" && <input type="hidden" name="kind" value={a} />}
                      <button type="submit">{LABEL[a]}</button>
                    </form>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2>Publication and rollback requests</h2>
      {proposals.value.length === 0 ? (
        <p>No requests yet. An operator approves or rejects each one.</p>
      ) : (
        <table>
          <thead>
            <tr><th>Kind</th><th>Deployment</th><th>State</th><th>Proposed</th><th>Decided</th></tr>
          </thead>
          <tbody>
            {proposals.value.map((p) => (
              <tr key={p.proposalId}>
                <td>{p.kind}</td><td>{p.deploymentRevisionId}</td><td>{p.state}</td><td>{p.proposedAt}</td><td>{p.decidedAt ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
