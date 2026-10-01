import { requireProviderWorkspace } from "@/lib/auth/guard";
import { registerModel } from "@/lib/services/control/actions";
import { controlPort, holds } from "@/lib/services/control/port";
import { refusalCopy, REFUSAL_COPY } from "@/lib/services/control/view";

export const metadata = { title: "Models · infrx Lab" };

// L4: registered model revisions and assisted registration (the control service validates it).
export default async function Models({ searchParams }: PageProps<"/models">) {
  const workspace = await requireProviderWorkspace();
  const refused = refusalCopy((await searchParams).refused);
  const models = await controlPort().models(workspace);
  return (
    <>
      <h1>Models</h1>
      {refused && <p role="alert">{refused}</p>}
      {!models.ok ? (
        <p role="alert">{REFUSAL_COPY[models.reason]}</p>
      ) : models.value.length === 0 ? (
        <p>No models are registered in this workspace yet.</p>
      ) : (
        <table>
          <thead>
            <tr><th>Model</th><th>Revision</th><th>Artifact</th><th>Schema</th><th>Runtime</th><th>Registered</th></tr>
          </thead>
          <tbody>
            {models.value.map((m) => (
              <tr key={`${m.modelId}@${m.revisionLabel}`}>
                <td>{m.modelId}</td><td>{m.revisionLabel}</td><td>{m.artifactDigest}</td><td>{m.schemaVersion}</td><td>{m.runtime}</td><td>{m.registeredAt}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {holds(workspace.role, "manage_dev_deployment") && (
        <form action={registerModel}>
          <h2>Register a model revision</h2>
          <p>Registration creates a private dev deployment; nothing is public until an operator approves a publication.</p>
          <label>Name <input name="name" required pattern="[a-z0-9][a-z0-9\-]{0,62}" /></label>
          <label>Artifact digest <input name="artifactDigest" required placeholder="sha256:…" /></label>
          <label>Schema version <input name="schemaVersion" required /></label>
          <label>Runtime <input name="runtime" required /></label>
          <button type="submit">Register</button>
        </form>
      )}
    </>
  );
}
