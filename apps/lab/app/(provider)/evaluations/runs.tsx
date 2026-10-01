import { cancelRun } from "@/lib/services/evaluation/actions";
import type { RunRow } from "@/lib/services/evaluation/view";

// B4.b: run progress from D7's records, every count out of all cases; cancel only where the row allows.
export function RunsTable({ rows }: { rows: RunRow[] }) {
  return (
    <table>
      <thead>
        <tr><th>Run</th><th>State</th><th>Done</th><th>Failed</th><th>Skipped</th><th>Not finished</th><th>Attempts</th><th>Cost</th><th /></tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td>{r.ref}</td><td>{r.state}</td><td>{r.done}</td><td>{r.failed}</td><td>{r.skipped}</td><td>{r.open}</td><td>{r.attempts}</td>
            <td>{r.costs.map((c) => <div key={c}>{c}</div>)}</td>
            <td>
              {r.cancel && (
                <form action={cancelRun}>
                  <input type="hidden" name="run_id" value={r.id} />
                  <button type="submit">Cancel run</button>
                </form>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
