import { ServiceState } from "@/components/ui/service-state";
import { budgetRow, configRow, runRow, type JudgeRecords as Records } from "./view";

/** UX-08: the workspace's judge configurations, runs and budgets as AP-08 returns them; `null` = could not be read. */
export function JudgeRecords({ records }: { records: Records | null }) {
  if (records === null)
    return <ServiceState state="unavailable" title="Judge records cannot be read here yet" explanation="Configurations, runs and budgets are not listed until the Lab reads them from the judge API. The forms below still post; their answers show beside each form." />;
  return (
    <section aria-label="Judge records">
      <h2>Configurations</h2>
      {records.configs.length === 0 ? <p>No configurations yet.</p> : (
        <table>
          <thead><tr><th>Configuration</th><th>Model</th><th>Judge</th><th>Rubric</th><th>Sample</th><th>Calibration</th></tr></thead>
          <tbody>{records.configs.map(configRow).map((c) => (
            <tr key={c.id}><td><code className="lab-id">{c.id}</code></td><td>{c.model}</td><td>{c.judge}</td><td>{c.rubric}</td><td>{c.sample}</td><td>{c.calibration}</td></tr>
          ))}</tbody>
        </table>
      )}
      <h2>Runs</h2>
      {records.runs.length === 0 ? <p>No judge runs yet.</p> : (
        <table>
          <thead><tr><th>Run</th><th>Configuration</th><th>State</th><th>Samples</th><th>Cost</th><th>Payer</th><th>Requested</th></tr></thead>
          <tbody>{records.runs.map(runRow).map((r) => (
            <tr key={r.id}><td><code className="lab-id">{r.id}</code></td><td>{r.config}</td><td>{r.state}</td><td>{r.counts}</td><td>{r.money.map((m) => <div key={m}>{m}</div>)}</td><td>{r.payer}</td><td>{r.requested}</td></tr>
          ))}</tbody>
        </table>
      )}
      <h2>Budgets</h2>
      {records.budgets.length === 0 ? <p>No budgets yet.</p> : (
        <table>
          <thead><tr><th>Payer</th><th>Limit</th><th>Reserved</th><th>Settled</th></tr></thead>
          <tbody>{records.budgets.map(budgetRow).map((b) => (
            <tr key={b.payer}><td>{b.payer}</td><td>{b.limit}</td><td>{b.reserved}</td><td>{b.settled}</td></tr>
          ))}</tbody>
        </table>
      )}
    </section>
  );
}
