// UX-06/UX-09 ReferenceField: this workspace's dataset versions as a datalist for a dataset ref field -
// the catalog where the datasets service answers, the typed ref (still validated by the action) where it
// does not. A refused or missing read renders nothing, never a made-up list.
import type { Result, VersionSummary } from "@/lib/services/datasets/port";

export function DatasetOptions({ id, versions }: { id: string; versions: Result<VersionSummary[]> | null }) {
  if (versions === null || !versions.ok) return null;
  return (
    <datalist id={id}>
      {versions.value.map((v) => (
        <option key={v.datasetRef} value={v.datasetRef} label={`${v.datasetId} v${v.version} · ${v.derivation} · ${v.samples} samples`} />
      ))}
    </datalist>
  );
}
