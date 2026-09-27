import { requireProviderWorkspace } from "@/lib/auth/guard";

// L1 wiring: the Lab home. It calls the guard itself (a layout does not stop a page rendering).
export default async function Home() {
  const workspace = await requireProviderWorkspace();
  return <p>{workspace.providerName}: no Lab features are available yet.</p>;
}
