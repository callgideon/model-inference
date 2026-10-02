// UX-06 synthetic examples: the guided import, derive and export forms over fixture actions. Served only
// by tests/ux/improve/browser.ts; every id and record is synthetic.
import { DeriveForm, ExportForm, ImportWizard } from "@/app/(provider)/datasets/forms";
import { importTemplate } from "@/lib/services/datasets/views";
import { derive, exportVersion, preview, start } from "./actions";

const ZERO = "00000000-0000-4000-8000-000000000000";
const REF = `lab:dataset:${ZERO}:${ZERO}@sha256:${"0".repeat(64)}`;

export default function Improve() {
  return (
    <div className="lab-page lab-stack">
      <h1>Improve harness</h1>
      <section aria-label="Import">
        <ImportWizard preview={preview} start={start} template={importTemplate({ providerId: ZERO, importId: ZERO, datasetId: ZERO, createdAt: "2026-10-02T00:00:00.000Z" })} />
      </section>
      <section aria-label="Derive">
        <DeriveForm base={REF} datasetId={ZERO} derive={derive} />
      </section>
      <section aria-label="Export">
        <ExportForm datasetRef={REF} exportVersion={exportVersion} />
      </section>
    </div>
  );
}
