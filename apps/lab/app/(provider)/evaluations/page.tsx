import Link from "next/link";
import { requireProviderWorkspace } from "@/lib/auth/guard";
import { launchExperiment } from "@/lib/services/evaluation/actions";
import { evaluationPort, holds, isPreview } from "@/lib/services/evaluation/port";
import { refusalCopy, REFUSAL_COPY, runRow } from "@/lib/services/evaluation/view";
import { Badge } from "@/components/ui/badge";
import { ServiceState } from "@/components/ui/service-state";
import { RunsTable } from "./runs";
import { EvaluationsNav } from "./nav";
import { RefList } from "./refs";
import { catalogState, experimentRow, LAUNCH_COPY, LAUNCH_NEEDS, PROTOCOL_HELP } from "./view";
import { PreviewNote } from "@/components/preview-note";

export const metadata = { title: "Evaluations · infrx Lab" };

const help = (name: keyof typeof PROTOCOL_HELP) => <small id={`help-${name}`}>{PROTOCOL_HELP[name]}</small>;

// B4.a/b + UX-08 (L-08): experiments and runs from the evaluation records only; a launch queues backend
// runs and this page shows whatever the records say after the redirect. The catalog is its own section:
// when it cannot be read the launch is unavailable and the records still show.
export default async function Evaluations({ searchParams }: PageProps<"/evaluations">) {
  const workspace = await requireProviderWorkspace();
  const refused = refusalCopy((await searchParams).refused);
  const port = evaluationPort();
  const [runs, experiments, catalog] = await Promise.all([port.runs(workspace), port.experiments(workspace), port.catalog(workspace)]);
  if (!runs.ok || !experiments.ok) return <p role="alert">{REFUSAL_COPY.unavailable}</p>;
  const launch = catalogState(catalog, LAUNCH_NEEDS);
  return (
    <>
      <h1>Evaluations</h1>
      <EvaluationsNav current="experiments" />
      {isPreview() && <PreviewNote records="evaluation" service="evaluation" />}
      {refused && <p role="alert">{refused}</p>}
      <h2 id="experiments">Experiments</h2>
      {experiments.value.length === 0 ? (
        <ServiceState state="empty" title="No experiments yet." explanation="An experiment compares a baseline and a candidate serving revision on one frozen suite." />
      ) : (
        <table>
          <thead>
            <tr><th>Experiment</th><th>Created</th><th>Baseline</th><th>Candidate</th><th>Decision</th><th>Frozen dataset</th></tr>
          </thead>
          <tbody>
            {experiments.value.map((e) => experimentRow(workspace.role, e)).map((e) => (
              <tr key={e.id}>
                <td><Link href={`/experiments/${e.id}`}>{e.id}</Link></td><td>{e.created}</td>
                <td>{e.baseline}</td><td>{e.candidate}</td>
                <td><Badge tone={e.decision.tone}>{e.decision.text}</Badge> {e.decision.scope}</td>
                <td><code className="lab-id">{e.dataset}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2 id="runs">Runs</h2>
      {runs.value.length === 0 ? (
        <ServiceState state="empty" title="No runs yet." explanation="Each experiment queues two runs; their progress and cost appear here." />
      ) : <RunsTable rows={runs.value.map((r) => runRow(workspace.role, r))} />}
      {holds(workspace.role, "run_evaluation") && (
        launch.kind === "unavailable" ? (
          <ServiceState state="unavailable" title="Comparisons cannot be launched right now" explanation="The launch catalog (frozen datasets, harness revisions, evaluators and serving revisions) could not be read. The records above are current; nothing was launched." />
        ) : launch.kind === "empty" ? (
          <ServiceState state="empty" title="Nothing to compare yet" explanation={`This workspace's catalog offers no ${launch.missing.join(", no ")}.`} />
        ) : (
        <form action={launchExperiment}>
          <h2>Compare a baseline and a candidate</h2>
          <p>Both runs share the dataset, harness, evaluator, seed and case count; only the serving revision differs.</p>
          <input type="hidden" name="experiment_id" value={crypto.randomUUID()} />
          <fieldset>
            <legend>1. Frozen suite</legend>
            <label>Dataset <select name="dataset_ref" required>{launch.catalog.datasets.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}</select></label>
            <label>Harness <select name="harness_ref" required>{launch.catalog.harnesses.map((h) => <option key={h.ref} value={h.ref}>{`${h.harness_id} v${h.version} (${h.adapter})`}</option>)}</select></label>
            <label>Evaluator <select name="evaluator_ref" required>{launch.catalog.evaluators.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}</select></label>
            <label>Seed <input name="seed" inputMode="numeric" required aria-describedby="help-seed" /></label>{help("seed")}
            <label>Cases <input name="max_cases" inputMode="numeric" required aria-describedby="help-max_cases" /></label>{help("max_cases")}
            <RefList catalog={launch.catalog} />
          </fieldset>
          <fieldset>
            <legend>2. Serving revisions</legend>
            <p>{LAUNCH_COPY.serving}</p>
            <label>Baseline serving <select name="baseline_serving_ref" required>{launch.catalog.servings.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}</select></label>
            <label>Candidate serving <select name="candidate_serving_ref" required>{launch.catalog.servings.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}</select></label>
          </fieldset>
          <fieldset>
            <legend>3. Decision protocol (declared now, before any result)</legend>
            <label>Metric basis <select name="metric_source" required aria-describedby="help-metric_source">
              <option value="">Choose the metric basis</option>
              <option value="deterministic_metric">deterministic metric</option>
              <option value="teacher_judgment">teacher judgment</option>
            </select></label>{help("metric_source")}
            <label>Confidence <input name="confidence" inputMode="decimal" required aria-describedby="help-confidence" /></label>{help("confidence")}
            <label>Non-inferiority margin <input name="margin" inputMode="decimal" required aria-describedby="help-margin" /></label>{help("margin")}
            <label>Minimum paired cases <input name="min_cases" inputMode="numeric" required aria-describedby="help-min_cases" /></label>{help("min_cases")}
            <label>Required slices <textarea name="slices" aria-describedby="help-slices" /></label>{help("slices")}
          </fieldset>
          <fieldset>
            <legend>4. Limit</legend>
            <label>CREDIT limit per run <input name="run_limit" inputMode="decimal" required aria-describedby="help-run_limit" /></label>
            <small id="help-run_limit">{LAUNCH_COPY.limit}</small>
          </fieldset>
          <button type="submit">Queue baseline and candidate runs</button>
        </form>
        )
      )}
    </>
  );
}
