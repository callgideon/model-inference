import { requireProviderWorkspace } from "@/lib/auth/guard";
import { subscribeCheckpoints } from "@/lib/services/evaluation/actions";
import { evaluationPort, holds, isPreview } from "@/lib/services/evaluation/port";
import { refusalCopy, REFUSAL_COPY, subscriptionRow } from "@/lib/services/evaluation/view";
import { PreviewNote } from "../runs";

export const metadata = { title: "Checkpoint subscriptions · infrx Lab" };

// B4.b: B3 subscriptions and one decision per checkpoint, as recorded. An evaluation is evidence
// only: nothing here publishes or promotes a checkpoint.
export default async function Checkpoints({ searchParams }: PageProps<"/evaluations/checkpoints">) {
  const workspace = await requireProviderWorkspace();
  const refused = refusalCopy((await searchParams).refused);
  const actor = { providerId: workspace.providerId, role: workspace.role };
  const port = evaluationPort();
  const [subscriptions, catalog] = await Promise.all([port.subscriptions(actor), port.catalog(actor)]);
  if (!subscriptions.ok || !catalog.ok) return <p role="alert">{REFUSAL_COPY.unavailable}</p>;
  const c = catalog.value;
  return (
    <>
      <h1>Checkpoint subscriptions</h1>
      {isPreview() && <PreviewNote />}
      {refused && <p role="alert">{refused}</p>}
      <p>Each checkpoint an external training run posts is evaluated once per subscription on a private dev deployment. It is never published or promoted.</p>
      {subscriptions.value.length === 0 && <p>No subscriptions yet.</p>}
      {subscriptions.value.map(subscriptionRow).map((s) => (
        <section key={s.id}>
          <h2>{s.externalRun}</h2>
          <dl>{s.suite.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
          <p>{s.budget}</p>
          <p>{s.policy}</p>
          {s.decisions.length === 0 ? (
            <p>No checkpoints received yet.</p>
          ) : (
            <table>
              <thead>
                <tr><th>Checkpoint</th><th>Step</th><th>Receipt</th><th>Evaluation</th></tr>
              </thead>
              <tbody>
                {s.decisions.map((d) => (
                  <tr key={d.checkpoint}><td>{d.checkpoint}</td><td>{d.step}</td><td>{d.receipt}</td><td>{d.outcome}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ))}
      {holds(workspace.role, "run_evaluation") && (
        <form action={subscribeCheckpoints}>
          <h2>Subscribe a pinned suite</h2>
          <input type="hidden" name="subscription_id" value={crypto.randomUUID()} />
          <label>External run <input name="external_run_ref" required /></label>
          <label>Dataset <select name="dataset_ref" required>{c.datasets.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}</select></label>
          <label>Harness <select name="harness_ref" required>{c.harnesses.map((h) => <option key={h.ref} value={h.ref}>{`${h.harness_id} v${h.version} (${h.adapter})`}</option>)}</select></label>
          <label>Evaluator <select name="evaluator_ref" required>{c.evaluators.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}</select></label>
          <label>Seed <input name="seed" inputMode="numeric" required /></label>
          <label>Cases <input name="max_cases" inputMode="numeric" required /></label>
          <label>CREDIT limit per run <input name="run_limit" inputMode="decimal" required /></label>
          <label>CREDIT limit in total <input name="limit" inputMode="decimal" required /></label>
          <label>Runs at once <input name="max_active" inputMode="numeric" required /></label>
          <label>Policy <select name="policy"><option value="latest_only">latest only</option><option value="every">every checkpoint</option></select></label>
          <button type="submit">Subscribe</button>
        </form>
      )}
    </>
  );
}
