// UX-00 primitive examples with synthetic records only: every ServiceState, a field error, long IDs,
// the dialog, the drawer and the copy button. Served only by tests/ux/browser.ts.
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/ui/copy-button";
import { Dialog, Drawer } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/field";
import { PageHeader } from "@/components/ui/page-header";
import { SERVICE_STATES, ServiceState } from "@/components/ui/service-state";

const LONG_ID = "sv-00000000-synthetic-0000-0000-000000000000-revision-with-a-very-long-identifier-that-must-wrap";

export default function Primitives() {
  return (
    <div className="lab-page">
      <PageHeader
        breadcrumb={[{ href: "/", label: "Harness" }]}
        title="Primitives"
        purpose="Synthetic examples of the UX-00 components."
        actions={<Button variant="primary">Primary action</Button>}
      />
      <section aria-label="States" className="lab-stack">
        {SERVICE_STATES.map((state) => (
          <ServiceState
            key={state}
            state={state}
            title={`Synthetic ${state} state`}
            explanation="A synthetic explanation."
            action={state === "unavailable" ? <Button>Try again</Button> : undefined}
            diagnosticId={state === "unavailable" ? LONG_ID : undefined}
          />
        ))}
      </section>
      <section aria-label="Badges" className="lab-stack">
        <p>
          <Badge tone="success">Passed</Badge> <Badge tone="warning">Awaiting approval</Badge> <Badge tone="danger">Failed</Badge>{" "}
          <Badge tone="info">Registered</Badge> <Badge>Retired</Badge>
        </p>
      </section>
      <form aria-label="Field" className="lab-stack">
        <Field label="Model reference" description="The immutable revision id." error="Enter a revision id.">
          <Input name="reference" defaultValue="" />
        </Field>
        <Button type="submit" pending>
          Saving
        </Button>
      </form>
      <section aria-label="Identifier" className="lab-stack">
        <code className="lab-id">{LONG_ID}</code>
        <CopyButton value={LONG_ID} label="Copy serving version id" />
      </section>
      <section aria-label="Overlays" className="lab-stack">
        <Dialog trigger={<Button>Open dialog</Button>} title="Synthetic dialog" description="A modal with two controls.">
          <Field label="Reason">
            <Input name="reason" />
          </Field>
          <Button variant="primary">Confirm</Button>
        </Dialog>
        <Drawer trigger={<Button>Open drawer</Button>} title="Synthetic drawer">
          <a href="#first">First link</a>
          <a href="#second">Second link</a>
        </Drawer>
        <Button id="after-overlays">Page control</Button>
      </section>
    </div>
  );
}
