import { requireProviderWorkspace } from "@/lib/auth/guard";
import { evaluationPort, isPreview } from "@/lib/services/evaluation/port";
import { comparison, REFUSAL_COPY, runRow } from "@/lib/services/evaluation/view";
import { RunsTable } from "../../evaluations/runs";
import { ComparisonReport } from "../../evaluations/report";
import { PreviewNote } from "@/components/preview-note";

export const metadata = { title: "Experiment · infrx Lab" };

// B4.c: one experiment from the records - the protocol declared at launch, both runs' progress and,
// once B2 has compared them, its report as it is. Another provider's id is simply not here.
export default async function ExperimentPage({ params }: PageProps<"/experiments/[id]">) {
  const workspace = await requireProviderWorkspace();
  const { id } = await params;
  const list = await evaluationPort().experiments(workspace);
  if (!list.ok) return <p role="alert">{REFUSAL_COPY.unavailable}</p>;
  const e = list.value.find((x) => x.experiment_id === id);
  if (e === undefined) return <p role="alert">{REFUSAL_COPY.not_found}</p>;
  const p = e.protocol;
  const c = e.report === null ? null : comparison(e.report);
  return (
    <>
      <h1>Experiment {e.experiment_id}</h1>
      {isPreview() && <PreviewNote records="evaluation" service="evaluation" />}
      <h2>Protocol, declared at launch</h2>
      <p>{`${p.confidence} confidence · margin ${p.margin} · at least ${p.min_cases} paired cases · ${p.metric_source.replace("_", " ")}`}</p>
      <ul>{Object.entries(p.required_slices).map(([s, r]) => <li key={s}>{`slice ${s}: margin ${r.margin}, at least ${r.min_cases} cases`}</li>)}</ul>
      <h2>Runs</h2>
      <RunsTable rows={[runRow(workspace.role, e.baseline), runRow(workspace.role, e.candidate)]} />
      <h2>Comparison</h2>
      {c === null ? (
        <p>Pending: B2 compares the runs once both have ended.</p>
      ) : (
        <>
          <ComparisonReport report={e.report!} />
          <p><a href={`/experiments/${e.experiment_id}/report`} download>Export the report (JSON)</a></p>
        </>
      )}
    </>
  );
}
