import { requireProviderWorkspace } from "@/lib/auth/guard";
import { proposeRelease } from "@/lib/services/rollouts/actions";
import { isPreview, releasesPort } from "@/lib/services/rollouts/port";
import { refusalCopy, releaseRows, REFUSAL_COPY } from "@/lib/services/rollouts/view";

export const metadata = { title: "Releases · infrx Lab" };

const LABEL = { expand: "Propose expansion", rollback: "Propose rollback" } as const;

// R4: controlled releases from the D9/R1/R2 records only. A proposal's outcome is whatever the records
// say after the redirect back here; an operator launches releases (P-12) and decides every proposal.
export default async function Releases({ searchParams }: PageProps<"/releases">) {
  const workspace = await requireProviderWorkspace();
  const refused = refusalCopy((await searchParams).refused);
  const records = await releasesPort().releases(workspace);
  if (!records.ok) return <p role="alert">{REFUSAL_COPY.unavailable}</p>;
  const rows = releaseRows(workspace.role, records.value);
  return (
    <>
      <h1>Releases</h1>
      {isPreview() && <p role="note">Preview: release records come from an in-memory stand-in, not the rollout service.</p>}
      {refused && <p role="alert">{refused}</p>}
      <p>An operator launches each shadow or canary release from its frozen plan and decides every proposal made here.</p>
      {rows.length === 0 ? (
        <p>No releases yet.</p>
      ) : (
        rows.map((r) => (
          <section key={r.id} aria-label={r.id}>
            <h2>{r.setup}</h2>
            <dl>
              <dt>Policy</dt><dd>{r.id}</dd>
              <dt>Status</dt><dd>{r.status}</dd>
              {r.blocked && (<><dt>Promotion</dt><dd>{r.blocked}</dd></>)}
              <dt>Request</dt><dd>{r.pending ?? "—"}</dd>
              <dt>Baseline</dt><dd>{r.baseline}</dd>
              <dt>Candidates</dt><dd>{r.candidates.join("; ")}</dd>
              <dt>Frozen plan</dt><dd>{r.plan}</dd>
              <dt>Traffic</dt><dd>{r.traffic}</dd>
              <dt>Candidate errors</dt><dd>{r.errors}</dd>
              <dt>Candidate p99</dt><dd>{r.p99}</dd>
              <dt>Quality coverage</dt><dd>{r.quality}</dd>
              <dt>Spend</dt><dd>{r.spend}</dd>
              <dt>Assignments</dt><dd>{r.assignments.length ? r.assignments.join("; ") : "—"}</dd>
            </dl>
            <h3>Decisions</h3>
            {r.lineage.length === 0 ? <p>No decisions yet.</p> : <ol>{r.lineage.map((l) => <li key={l}>{l}</li>)}</ol>}
            {r.actions.map((a) => (
              <form key={a} action={proposeRelease}>
                <input type="hidden" name="policyRef" value={r.id} />
                <input type="hidden" name="fence" value={r.fence} />
                <input type="hidden" name="kind" value={a} />
                <button type="submit">{LABEL[a]}</button>
              </form>
            ))}
          </section>
        ))
      )}
    </>
  );
}
