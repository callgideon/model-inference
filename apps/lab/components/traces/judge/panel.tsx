// V3 panel: judge runs and calibration for one request, read only. It has no submit control: a paid
// judge run is requested from judge settings (C3L), never by default from here.
import Link from "next/link";
import type { Result } from "../detail/port.ts";
import type { JudgeRefusal, JudgeRun } from "./port.ts";
import { JUDGE_COPY, judgeRows } from "./view.ts";

export function JudgePanel({ result }: { result: Result<JudgeRun[], JudgeRefusal> }) {
  const rows = result.ok ? judgeRows(result.value) : [];
  return (
    <section aria-labelledby="request-judge">
      <h2 id="request-judge">Judge</h2>
      {!result.ok ? (
        <p role="status">{JUDGE_COPY[result.reason]}</p>
      ) : rows.length === 0 ? (
        <p role="status">{JUDGE_COPY.empty}</p>
      ) : (
        <ul>
          {rows.map((r) => (
            <li key={r.id} style={{ overflowWrap: "anywhere" }}>
              <p><strong>{r.label}</strong></p>
              <p>{r.status}</p>
              {r.scores.length > 0 && (
                <dl>
                  {r.scores.map(([criterion, value]) => (
                    <div key={criterion}>
                      <dt>{criterion}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
              )}
              <p>Verdict: {r.verdict}</p>
              <p>{r.calibration}</p>
            </li>
          ))}
        </ul>
      )}
      <p>
        Judge consent and budget are managed in <Link href="/settings">Settings</Link>.
      </p>
    </section>
  );
}
