import { requireProviderWorkspace } from "@/lib/auth/guard";
import { isPreview, releasesPort } from "@/lib/services/rollouts/port";
import { REFUSAL_COPY, variantRows } from "@/lib/services/rollouts/view";
import { PreviewNote } from "@/components/preview-note";

export const metadata = { title: "Optimizations · infrx Lab" };

// R4: R3's registered variants and their comparisons, each scoped to the hardware and runtime it ran on.
// A performance figure is a measurement only when it comes from an experiment's results at a commit.
export default async function Optimizations() {
  const workspace = await requireProviderWorkspace();
  const variants = await releasesPort().variants(workspace);
  if (!variants.ok) return <p role="alert">{REFUSAL_COPY.unavailable}</p>;
  const rows = variantRows(variants.value);
  return (
    <>
      <h1>Optimizations</h1>
      {isPreview() && <PreviewNote records="variant" service="rollout" />}
      {rows.length === 0 ? (
        <p>No optimized variants registered yet.</p>
      ) : (
        <table>
          <thead>
            <tr><th>Variant</th><th>Base scope</th><th>Variant scope</th><th>Changes</th><th>Comparison</th><th>Performance</th><th>Claim</th><th>Release</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td><td>{r.base}</td><td>{r.variant}</td><td>{r.changes}</td><td>{r.outcome}</td><td>{r.performance}</td><td>{r.claim}</td><td>{r.eligible}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
