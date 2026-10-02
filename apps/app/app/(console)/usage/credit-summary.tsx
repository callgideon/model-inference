import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import type { CreditCardModel } from "../billing/credit-view-model";
import type { ViewState } from "./view-model";

/**
 * Usage's compact credit line (UX-07, C-04): the wallet's exact figures and its warning, with the full
 * accounting one link away on Credits. A failed wallet read is "Credits unavailable" with its retry,
 * never a zero.
 */
export function CreditSummary({ card, href }: { card: ViewState<CreditCardModel>; href: string }) {
  const credits = (
    <Link className="underline underline-offset-4" href="/billing">
      View credits
    </Link>
  );
  if (card.kind === "error") {
    return (
      <p role="status" className="mt-6 rounded-md border p-3 text-sm">
        Credits unavailable. {card.message}{" "}
        {card.recovery === "retry" ? (
          <a className="underline underline-offset-4" href={href}>
            Try again
          </a>
        ) : null}{" "}
        {credits}
      </p>
    );
  }
  if (card.kind !== "ready") return null;
  const { figures, state } = card.value;
  return (
    <section aria-label="Credits" className="mt-6 rounded-md border p-3 text-sm">
      <dl className="flex flex-wrap gap-x-6 gap-y-2">
        {figures.map((figure) => (
          <div key={figure.label}>
            <dt className="text-xs text-muted-foreground">{figure.label}</dt>
            <dd className={figure.emphasis ? "font-medium tabular-nums" : "tabular-nums text-muted-foreground"}>{figure.value}</dd>
          </div>
        ))}
      </dl>
      {state.kind === "funded" ? null : (
        <p role="status" className="mt-2">
          <span className="font-medium">{state.headline}.</span> {state.guidance}
        </p>
      )}
      <p className="mt-2 flex items-center gap-2">
        <Badge variant="secondary">CREDIT</Badge>
        {credits}
      </p>
    </section>
  );
}
