import { ACCESS_COPY, type Membership } from "@/lib/auth/access";
import { selectWorkspace } from "@/lib/auth/actions";
import { providerAccessForRequest } from "@/lib/auth/guard";

// Every provider page reads the session: never prerender.
export const dynamic = "force-dynamic";

function Workspaces({ workspaces }: { workspaces: Membership[] }) {
  return (
    <ul>
      {workspaces.map((w) => (
        <li key={w.providerId}>
          <form action={selectWorkspace}>
            <input type="hidden" name="providerId" value={w.providerId} />
            <button type="submit">
              {w.providerName} ({w.role})
            </button>
          </form>
        </li>
      ))}
    </ul>
  );
}

export default async function ProviderLayout({ children }: { children: React.ReactNode }) {
  const access = await providerAccessForRequest();
  if (access.kind === "ready") {
    return (
      <>
        <header>
          <strong>infrx Lab</strong> · {access.workspace.providerName} ({access.workspace.role})
          {access.workspaces.length > 1 && <Workspaces workspaces={access.workspaces} />}
        </header>
        <main>{children}</main>
      </>
    );
  }
  if (access.kind === "select") {
    return (
      <main>
        <h1>Choose a provider workspace</h1>
        <Workspaces workspaces={access.workspaces} />
      </main>
    );
  }
  return (
    <main>
      <p>{ACCESS_COPY[access.kind]}</p>
    </main>
  );
}
