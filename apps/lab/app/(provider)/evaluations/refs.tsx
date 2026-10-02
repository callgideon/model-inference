import type { Catalog } from "@/lib/services/evaluation/port";

/** UX-08: each catalog choice's readable label beside its immutable ref (the selects show labels). */
export function RefList({ catalog }: { catalog: Catalog }) {
  const groups: [string, { ref: string; label: string }[]][] = [
    ["Frozen datasets", catalog.datasets],
    ["Harness revisions", catalog.harnesses.map((h) => ({ ref: h.ref, label: `${h.harness_id} v${h.version} (${h.adapter})` }))],
    ["Evaluators", catalog.evaluators],
    ["Serving revisions", catalog.servings],
  ];
  return (
    <details>
      <summary>Immutable references</summary>
      {groups.map(([title, options]) => (
        <dl key={title} aria-label={title}>
          {options.map((o) => <div key={o.ref}><dt>{o.label}</dt><dd><code className="lab-id">{o.ref}</code></dd></div>)}
        </dl>
      ))}
    </details>
  );
}
