import type { Report } from "@/lib/services/evaluation/port";
import { Badge } from "@/components/ui/badge";
import { reportView } from "./view";

/** UX-08 (L-08): B2's stored comparison, decision first, then reasons, counts, intervals, cost by unit,
 * latency (labelled), and the frozen digests in Details. Nothing here re-derives a verdict. */
export function ComparisonReport({ report }: { report: Report }) {
  const v = reportView(report);
  return (
    <section aria-label="Comparison">
      <p role="status"><Badge tone={v.decision.tone}>{v.decision.text}</Badge> {v.decision.scope}</p>
      <ul>{v.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
      <p>{`${v.pairing} · ${v.basis} · ${v.confidence} · ${v.paired}`}</p>
      {v.basisNote && <p>{v.basisNote}</p>}
      <table>
        <thead>
          <tr><th>Estimate</th><th>Cases</th><th>Candidate − baseline</th><th>Interval</th><th>Margin</th><th>Verdict</th><th /></tr>
        </thead>
        <tbody>
          {v.estimates.map((r) => (
            <tr key={r.name}><td>{r.name}</td><td>{r.paired}</td><td>{r.diff}</td><td>{r.interval}</td><td>{r.margin}</td><td>{r.verdict}</td><td>{r.improved}</td></tr>
          ))}
        </tbody>
      </table>
      <table>
        <thead>
          <tr><th>Run</th><th>Mean over all cases</th><th>Missing</th><th>Errored</th><th>Not comparable</th><th>Cost</th><th>Latency</th></tr>
        </thead>
        <tbody>
          {v.runs.map((r) => (
            <tr key={r.name}>
              <td>{r.name}</td><td>{r.mean}</td><td>{r.missing}</td><td>{r.errors}</td><td>{r.notComparable}</td>
              <td>{r.costs.map((x) => <div key={x}>{x}</div>)}</td><td>{r.latency}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>{v.latencyNote}</p>
      <p>Cost difference, per unit: {v.costDelta.join(" · ")}</p>
      <details>
        <summary>Frozen identity</summary>
        <dl>{v.identity.map(([k, id]) => <div key={k}><dt>{k}</dt><dd><code className="lab-id">{id}</code></dd></div>)}</dl>
      </details>
    </section>
  );
}
