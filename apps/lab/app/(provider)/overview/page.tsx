import { requireProviderWorkspace } from "@/lib/auth/guard";
import { controlPort } from "@/lib/services/control/port";
import { healthRows, REFUSAL_COPY } from "@/lib/services/control/view";

export const metadata = { title: "Overview · infrx Lab" };

// L4: counts and redacted aggregate health, straight from the control records.
export default async function Overview() {
  const workspace = await requireProviderWorkspace();
  const actor = { providerId: workspace.providerId, role: workspace.role };
  const port = controlPort();
  const [deployments, proposals, aggregates] = await Promise.all([port.deployments(actor), port.proposals(actor), port.aggregates(actor)]);
  if (!deployments.ok || !proposals.ok || !aggregates.ok) return <p role="alert">{REFUSAL_COPY.unavailable}</p>;
  const count = (env: string, visibility: string) =>
    deployments.value.filter((d) => d.state === "active" && d.environment === env && d.visibility === visibility).length;
  const health = healthRows(aggregates.value);
  return (
    <>
      <h1>Overview</h1>
      <ul>
        <li>Dev deployments (private): {count("dev", "private")}</li>
        <li>Production deployments (public): {count("prod", "public")}</li>
        <li>Requests awaiting operator approval: {proposals.value.filter((p) => p.state === "proposed").length}</li>
      </ul>
      <h2>Health (aggregates only)</h2>
      {health.length === 0 ? (
        <p>No traffic has been measured for your deployments yet.</p>
      ) : (
        <table>
          <thead>
            <tr><th>Deployment</th><th>Window</th><th>Requests</th><th>Error rate</th><th>p95</th></tr>
          </thead>
          <tbody>
            {health.map((h) => (
              <tr key={`${h.deployment} ${h.window}`}>
                <td>{h.deployment}</td><td>{h.window}</td><td>{h.requests}</td><td>{h.errorRate}</td><td>{h.p95}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
