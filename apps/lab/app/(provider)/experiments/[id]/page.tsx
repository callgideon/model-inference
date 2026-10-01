import { requireProviderWorkspace } from "@/lib/auth/guard";
import { evaluationPort, isPreview } from "@/lib/services/evaluation/port";
import { comparison, REFUSAL_COPY, runRow } from "@/lib/services/evaluation/view";
import { RunsTable } from "../../evaluations/runs";
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
          <p role="status">{c.outcome}</p>
          <ul>{c.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          <p>{`${c.pairing} · ${c.basis} · ${c.confidence} · ${c.paired}`}</p>
          <table>
            <thead>
              <tr><th>Estimate</th><th>Cases</th><th>Candidate − baseline</th><th>Interval</th><th>Margin</th><th>Verdict</th><th /></tr>
            </thead>
            <tbody>
              {c.estimates.map((r) => (
                <tr key={r.name}><td>{r.name}</td><td>{r.paired}</td><td>{r.diff}</td><td>{r.interval}</td><td>{r.margin}</td><td>{r.verdict}</td><td>{r.improved}</td></tr>
              ))}
            </tbody>
          </table>
          <table>
            <thead>
              <tr><th>Run</th><th>Mean over all cases</th><th>Missing</th><th>Errored</th><th>Not comparable</th><th>Cost</th><th>Latency</th></tr>
            </thead>
            <tbody>
              {c.runs.map((r) => (
                <tr key={r.name}>
                  <td>{r.name}</td><td>{r.mean}</td><td>{r.missing}</td><td>{r.errors}</td><td>{r.notComparable}</td>
                  <td>{r.costs.map((x) => <div key={x}>{x}</div>)}</td><td>{r.latency}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>Cost difference, per unit: {c.costDelta.join(" · ")}</p>
          <dl>{c.identity.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
          <p><a href={`/experiments/${e.experiment_id}/report`} download>Export the report (JSON)</a></p>
        </>
      )}
    </>
  );
}
