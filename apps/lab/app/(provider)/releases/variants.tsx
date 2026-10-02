import type { Result, Variant } from "@/lib/services/rollouts/port";
import { REFUSAL_COPY } from "@/lib/services/rollouts/view";
import { failure, variantViews } from "@/lib/services/releases/view";
import { Badge } from "@/components/ui/badge";
import { ServiceState } from "@/components/ui/service-state";
import s from "../operate.module.css";

// UX-10 (L-11): R3's variants as the Releases page's subordinate comparison view (rendered by
// /optimizations, WR-UX10-1). Each is a separate serving version scoped to the identities it ran on;
// an identity not recorded or other hardware is not a comparison; a figure is a measurement only from
// an experiment's results at a commit (R4's rule).
export function Variants({ result }: { result: Result<Variant[]> }) {
  if (!result.ok) {
    return failure(result) === "denied"
      ? <ServiceState state="denied" title="Optimizations are not available to your role" explanation="Your role in this workspace does not allow reading these records." />
      : <ServiceState state="unavailable" title="We couldn't load optimizations" explanation={REFUSAL_COPY.unavailable} />;
  }
  if (result.value.length === 0) {
    return <ServiceState state="empty" title="No optimizations yet" explanation="No optimized variants registered yet." />;
  }
  return (
    <ul className={s.records} aria-label="Optimized variants">
      {variantViews(result.value).map((v) => (
        <li key={v.id} className={s.record}>
          <div className={s.recordHead}>
            <h3 className="lab-id">{v.changes || "No recorded change"}</h3>
            <Badge tone={v.comparable ? "warning" : "neutral"}>{v.comparable ?? v.outcome}</Badge>
          </div>
          <dl className={s.facts}>
            <dt>Variant</dt><dd><code className="lab-id">{v.id}</code></dd>
            <dt>Serving version</dt><dd className="lab-id">{v.serving}</dd>
            <dt>Base scope</dt><dd>{v.base}</dd>
            <dt>Variant scope</dt><dd>{v.variant}</dd>
            <dt>Comparison</dt><dd>{v.outcome}</dd>
            <dt>Report</dt><dd><code className="lab-id">{v.digest}</code></dd>
            <dt>Performance</dt><dd className="lab-id">{v.performance}</dd>
            <dt>Claim</dt><dd>{v.claim}</dd>
            <dt>Release</dt><dd>{v.eligible}</dd>
          </dl>
        </li>
      ))}
    </ul>
  );
}
