import { Fragment } from "react";
import { holds } from "@/lib/auth/access";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { CAPABILITY_COPY, ROLE_COPY } from "@/lib/services/control/view";
import { Badge } from "@/components/ui/badge";
import { CopyButton } from "@/components/ui/copy-button";
import { PageHeader } from "@/components/ui/page-header";
import s from "../operate.module.css";

export const metadata = { title: "Settings · infrx Lab" };

// The services a provider relies on. Their availability is the API's to state (CX-01, the workspace
// capabilities read); until the Lab reads it, each one says so rather than guessing from configuration.
const SERVICES = ["Control records", "Requests", "Model import", "Serving readiness"];

// UX-03 L-06: the workspace and what this membership's role allows, read-only. Membership changes are
// the provider administrator's and the operator's, outside this page; no control here pretends otherwise.
export default async function Settings() {
  const workspace = await requireProviderWorkspace();
  const role = ROLE_COPY[workspace.role];
  return (
    <>
      <PageHeader title="Settings" purpose="Your workspace, your access and the services it relies on." />
      <section className={s.section} aria-labelledby="workspace">
        <h2 id="workspace">Workspace</h2>
        <dl className={s.facts}>
          <dt>Name</dt><dd>{workspace.providerName}</dd>
          <dt>Workspace ID</dt>
          <dd><code className="lab-id">{workspace.providerId}</code> <CopyButton value={workspace.providerId} label="Copy workspace id" /></dd>
        </dl>
      </section>
      <section className={s.section} aria-labelledby="access">
        <h2 id="access">Your access</h2>
        <p><Badge>{role.label}</Badge> {role.description}</p>
        <dl className={s.facts}>
          {(Object.keys(CAPABILITY_COPY) as (keyof typeof CAPABILITY_COPY)[]).map((c) => (
            <Fragment key={c}>
              <dt>{CAPABILITY_COPY[c]}</dt><dd>{holds(workspace.role, c) ? "Yes" : "No"}</dd>
            </Fragment>
          ))}
        </dl>
        <p className={s.muted}>Public production changes are approved by an infrx operator, never by a provider role.</p>
      </section>
      <section className={s.section} aria-labelledby="data">
        <h2 id="data">Data permissions</h2>
        <dl className={s.facts}>
          <dt>Read customer content</dt><dd>Not part of any role</dd>
        </dl>
        <p className={s.muted}>Request content is readable only under a current grant from the customer for a stated purpose. A role never includes it.</p>
      </section>
      <section className={s.section} aria-labelledby="services">
        <h2 id="services">Service availability</h2>
        <dl className={s.facts}>
          {SERVICES.map((name) => (
            <Fragment key={name}>
              <dt>{name}</dt><dd>Not yet verified here</dd>
            </Fragment>
          ))}
        </dl>
        <p className={s.muted}>This page does not check services yet. Each page says when its own service could not be reached; this is not an uptime monitor.</p>
      </section>
    </>
  );
}
