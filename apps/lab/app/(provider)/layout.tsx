import Link from "next/link";
import { ACCESS_COPY, type Membership } from "@/lib/auth/access";
import { selectWorkspace } from "@/lib/auth/actions";
import { providerAccessForRequest } from "@/lib/auth/guard";
import { signOut } from "@/lib/auth/sign-in";
import { SignInForm } from "@/lib/auth/sign-in-form";
import { isPreview } from "@/lib/services/control/port";

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

function SignOut() {
  return (
    <form action={signOut}>
      <button type="submit">Sign out</button>
    </form>
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
          <SignOut />
          <nav>
            <Link href="/overview">Overview</Link> · <Link href="/models">Models</Link> · <Link href="/deployments">Deployments</Link> · <Link href="/evaluations">Evaluations</Link> ·{" "}
            <Link href="/releases">Releases</Link> · <Link href="/optimizations">Optimizations</Link> ·{" "}
            <Link href="/annotations">Annotations</Link> · <Link href="/training">Training</Link> ·{" "}
            <Link href="/settings">Settings</Link>
          </nav>
          {isPreview() && <p role="note">Preview: control records come from an in-memory stand-in, not the control service.</p>}
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
        <SignOut />
      </main>
    );
  }
  if (access.kind === "signed-out") {
    return (
      <main>
        <h1>Sign in to the Lab</h1>
        <SignInForm />
      </main>
    );
  }
  return (
    <main>
      <p>{ACCESS_COPY[access.kind]}</p>
      {access.kind === "denied" && <SignOut />}
    </main>
  );
}
