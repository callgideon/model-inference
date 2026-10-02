import Link from "next/link";
import { ACCESS_COPY, type Membership, type Role } from "@/lib/auth/access";
import { selectWorkspace } from "@/lib/auth/actions";
import { providerAccessForRequest } from "@/lib/auth/guard";
import { signOut } from "@/lib/auth/sign-in";
import { SignInForm } from "@/lib/auth/sign-in-form";
import { isPreview } from "@/lib/services/control/port";
import { Badge } from "@/components/ui/badge";
import { Button, buttonClass } from "@/components/ui/button";
import { ServiceState } from "@/components/ui/service-state";
import { PreviewNote } from "@/components/preview-note";
import { MobileNav, NavLink } from "./nav";
import s from "./operate.module.css";

// Every provider page reads the session: never prerender.
export const dynamic = "force-dynamic";

// UX-03 L-01: the role names a person reads (the capability table stays lib/auth/access.ts's).
const ROLE_LABEL: Record<Role, string> = { viewer: "Viewer", developer: "Developer", administrator: "Administrator" };
const APP_HINT = "Looking for an inference endpoint? Use the infrx App with your consumer account.";

// ponytail: a plain list; add search when a member routinely holds more than ~10 workspaces (L-01).
function Workspaces({ workspaces }: { workspaces: Membership[] }) {
  return (
    <ul className={s.rows}>
      {workspaces.map((w) => (
        <li key={w.providerId} className={s.row}>
          <span>
            <strong>{w.providerName}</strong> <Badge>{ROLE_LABEL[w.role]}</Badge>
          </span>
          <form action={selectWorkspace}>
            <input type="hidden" name="providerId" value={w.providerId} />
            <Button type="submit" size="sm" aria-label={`Open workspace ${w.providerName}`}>
              Open workspace
            </Button>
          </form>
        </li>
      ))}
    </ul>
  );
}

function SignOut() {
  return (
    <form action={signOut}>
      <Button type="submit" variant="ghost">
        Sign out
      </Button>
    </form>
  );
}

export default async function ProviderLayout({ children }: { children: React.ReactNode }) {
  const access = await providerAccessForRequest();
  if (access.kind === "ready") {
    // 02-foundations: Operate first; Improve collapsed (native details) until a later lane gates it.
    const nav = (
      <nav aria-label="Lab" className={s.nav}>
        <p className={s.group}>Operate</p>
        <ul>
          <li><NavLink href="/overview">Overview</NavLink></li>
          <li><NavLink href="/models">Models</NavLink></li>
          <li><NavLink href="/deployments">Deployments</NavLink></li>
          <li><NavLink href="/requests">Requests</NavLink></li>
        </ul>
        <details>
          <summary className={s.group}>Improve</summary>
          <ul>
            <li><NavLink href="/datasets">Datasets</NavLink></li>
            <li><NavLink href="/evaluations">Evaluations</NavLink></li>
            <li className={s.nested}><NavLink href="/judge">Judge setup</NavLink></li>
            <li><NavLink href="/annotations">Review</NavLink></li>
            <li><NavLink href="/training">Training</NavLink></li>
            <li><NavLink href="/releases">Releases</NavLink></li>
            <li className={s.nested}><NavLink href="/optimizations">Optimizations</NavLink></li>
          </ul>
        </details>
        <ul className={s.navFoot}>
          {access.workspaces.length > 1 && (
            <li>
              <details>
                <summary className={s.link}>Switch workspace</summary>
                <Workspaces workspaces={access.workspaces} />
              </details>
            </li>
          )}
          <li><NavLink href="/settings">Settings</NavLink></li>
          <li><SignOut /></li>
        </ul>
      </nav>
    );
    return (
      <div className={s.shell}>
        <a className={s.skip} href="#lab-main">
          Skip to content
        </a>
        <header className={s.top}>
          <MobileNav>{nav}</MobileNav>
          <span className={s.product}>infrx Lab</span>
          <span className={s.workspace}>{access.workspace.providerName}</span>
          <Badge>{ROLE_LABEL[access.workspace.role]}</Badge>
        </header>
        <aside className={s.sidebar}>{nav}</aside>
        <main id="lab-main" tabIndex={-1} className={`lab-page ${s.main}`}>
          {isPreview() && <PreviewNote records="control" service="control" />}
          {children}
        </main>
      </div>
    );
  }
  if (access.kind === "select") {
    return (
      <main className={s.card}>
        <h1>Choose a workspace</h1>
        <p>Your membership is checked again on every request.</p>
        <Workspaces workspaces={access.workspaces} />
        <SignOut />
      </main>
    );
  }
  if (access.kind === "signed-out") {
    return (
      <main className={s.card}>
        <h1>infrx Lab</h1>
        <p>Manage and improve your models. Access requires a provider workspace membership.</p>
        <SignInForm />
        <p>{APP_HINT}</p>
      </main>
    );
  }
  return (
    <main className={s.card}>
      {access.kind === "denied" ? (
        <>
          <h1>This account has no Lab workspace</h1>
          <p>{ACCESS_COPY.denied}</p>
          <p>Ask your provider administrator for access. {APP_HINT}</p>
        </>
      ) : (
        <ServiceState
          state="unavailable"
          title="We couldn't check your provider access"
          explanation={ACCESS_COPY[access.kind]}
          action={<Link href="/" className={buttonClass()}>Try again</Link>}
        />
      )}
      {access.kind === "denied" && <SignOut />}
    </main>
  );
}
