import Link from "next/link";

const TABS = [
  { id: "experiments", href: "/evaluations#experiments", label: "Experiments" },
  { id: "runs", href: "/evaluations#runs", label: "Runs" },
  { id: "checkpoints", href: "/evaluations/checkpoints", label: "Checkpoint subscriptions" },
  { id: "judge", href: "/judge", label: "Judge setup" },
];

/** UX-08: the Evaluations sections; runs and subscriptions stay apart, judge setup is found here. */
export function EvaluationsNav({ current }: { current: string }) {
  return (
    <nav aria-label="Evaluations">
      <ul>
        {TABS.map((t) => (
          <li key={t.id}><Link href={t.href} aria-current={current === t.id ? "page" : undefined}>{t.label}</Link></li>
        ))}
      </ul>
    </nav>
  );
}
