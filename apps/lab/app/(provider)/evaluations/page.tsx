import Link from "next/link";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { launchExperiment } from "@/lib/services/evaluation/actions";
import { evaluationPort, holds, isPreview } from "@/lib/services/evaluation/port";
import { comparison, refusalCopy, REFUSAL_COPY, runRow } from "@/lib/services/evaluation/view";
import { PreviewNote, RunsTable } from "./runs";

export const metadata = { title: "Evaluations · infrx Lab" };

// B4.a/b: experiments and runs from the evaluation records only; a launch queues backend runs and this
// page shows whatever the records say after the redirect.
export default async function Evaluations({ searchParams }: PageProps<"/evaluations">) {
  const workspace = await requireProviderWorkspace();
  const refused = refusalCopy((await searchParams).refused);
  const actor = { providerId: workspace.providerId, role: workspace.role };
  const port = evaluationPort();
  const [runs, experiments, catalog] = await Promise.all([port.runs(actor), port.experiments(actor), port.catalog(actor)]);
  if (!runs.ok || !experiments.ok || !catalog.ok) return <p role="alert">{REFUSAL_COPY.unavailable}</p>;
  const c = catalog.value;
  return (
    <>
      <h1>Evaluations</h1>
      {isPreview() && <PreviewNote />}
      {refused && <p role="alert">{refused}</p>}
      <p><Link href="/evaluations/checkpoints">Checkpoint subscriptions</Link></p>
      <h2>Experiments</h2>
      {experiments.value.length === 0 ? (
        <p>No experiments yet.</p>
      ) : (
        <table>
          <thead>
            <tr><th>Experiment</th><th>Created</th><th>Baseline</th><th>Candidate</th><th>Decision</th></tr>
          </thead>
          <tbody>
            {experiments.value.map((e) => (
              <tr key={e.experiment_id}>
                <td><Link href={`/experiments/${e.experiment_id}`}>{e.experiment_id}</Link></td><td>{e.created_at}</td>
                <td>{runRow(workspace.role, e.baseline).state}</td><td>{runRow(workspace.role, e.candidate).state}</td>
                <td>{e.report === null ? "pending: compared once both runs end" : comparison(e.report).outcome}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2>Runs</h2>
      {runs.value.length === 0 ? <p>No runs yet.</p> : <RunsTable rows={runs.value.map((r) => runRow(workspace.role, r))} />}
      {holds(workspace.role, "run_evaluation") && (
        <form action={launchExperiment}>
          <h2>Launch a baseline and a candidate</h2>
          <p>Both runs share the dataset, harness, evaluator, seed and case count; only the serving version differs. Each run spends provider_dev CREDIT up to its limit.</p>
          <input type="hidden" name="experiment_id" value={crypto.randomUUID()} />
          <label>Dataset <select name="dataset_ref" required>{c.datasets.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}</select></label>
          <label>Harness <select name="harness_ref" required>{c.harnesses.map((h) => <option key={h.ref} value={h.ref}>{`${h.harness_id} v${h.version} (${h.adapter})`}</option>)}</select></label>
          <label>Evaluator <select name="evaluator_ref" required>{c.evaluators.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}</select></label>
          <label>Baseline serving <select name="baseline_serving_ref" required>{c.servings.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}</select></label>
          <label>Candidate serving <select name="candidate_serving_ref" required>{c.servings.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}</select></label>
          <label>Seed <input name="seed" inputMode="numeric" required /></label>
          <label>Cases <input name="max_cases" inputMode="numeric" required /></label>
          <label>CREDIT limit per run <input name="run_limit" inputMode="decimal" required /></label>
          <fieldset>
            <legend>Decision protocol (declared now, before any result)</legend>
            <label>Confidence <input name="confidence" inputMode="decimal" required /></label>
            <label>Non-inferiority margin <input name="margin" inputMode="decimal" required /></label>
            <label>Minimum paired cases <input name="min_cases" inputMode="numeric" required /></label>
            <label>Metric basis <select name="metric_source"><option value="deterministic_metric">deterministic metric</option><option value="teacher_judgment">teacher judgment</option></select></label>
            <label>Required slices, one per line: name margin minimum-cases <textarea name="slices" /></label>
          </fieldset>
          <button type="submit">Queue baseline and candidate runs</button>
        </form>
      )}
    </>
  );
}
