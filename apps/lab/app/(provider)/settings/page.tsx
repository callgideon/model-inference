import { requireProviderWorkspace } from "@/lib/auth/guard";
import { holds } from "@/lib/auth/access";

export const metadata = { title: "Settings · infrx Lab" };

// L4: the workspace and what this membership's role allows. Membership changes are the provider
// administrator's and the operator's, outside this page.
export default async function Settings() {
  const workspace = await requireProviderWorkspace();
  const may = (capability: Parameters<typeof holds>[1]) => (holds(workspace.role, capability) ? "yes" : "no");
  return (
    <>
      <h1>Settings</h1>
      <dl>
        <dt>Workspace</dt><dd>{workspace.providerName}</dd>
        <dt>Your role</dt><dd>{workspace.role}</dd>
        <dt>Read aggregate health</dt><dd>{may("read_aggregate_health")}</dd>
        <dt>Register models and run dev deployments</dt><dd>{may("manage_dev_deployment")}</dd>
        <dt>Propose publication and rollback</dt><dd>{may("propose_publication")}</dd>
      </dl>
      <p>Public production changes are approved by an infrx operator, never by a provider role.</p>
    </>
  );
}
