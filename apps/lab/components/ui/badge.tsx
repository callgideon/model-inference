import type { ReactNode } from "react";
import { CircleAlert, CircleCheck, CircleX, Info } from "lucide-react";

export type Tone = "neutral" | "success" | "warning" | "danger" | "info";

const ICONS = { success: CircleCheck, warning: CircleAlert, danger: CircleX, info: Info, neutral: null };

/**
 * A status label: tone, icon and words, never colour alone. The mapping from a domain state to a
 * tone belongs to that domain's view model ("active" is not globally healthy); `success` only for a
 * proven outcome.
 */
export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  const Icon = ICONS[tone];
  return (
    <span className={`lab-badge lab-badge--${tone}`}>
      {Icon ? <Icon aria-hidden /> : null}
      {children}
    </span>
  );
}
