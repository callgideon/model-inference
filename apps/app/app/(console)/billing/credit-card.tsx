import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { CreditCardModel } from "./credit-view-model";

/** Markup only: every figure and sentence comes from `creditCardState()` (tests under `tests/u/`). */
export function CreditBalanceCard({ model }: { model: CreditCardModel }) {
  return (
    <Card>
      <CardHeader className="grid-cols-[1fr_auto] items-center">
        <CardTitle>Credits</CardTitle>
        <Badge variant="secondary">CREDIT</Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        {model.figures.length > 0 ? (
          <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {model.figures.map((figure) => (
              <div key={figure.label}>
                <dt className="text-xs text-muted-foreground">{figure.label}</dt>
                <dd
                  className={
                    figure.emphasis
                      ? "font-heading text-lg font-semibold tabular-nums"
                      : "mt-0.5 tabular-nums text-muted-foreground"
                  }
                >
                  {figure.value}
                </dd>
                <dd className="mt-0.5 text-xs text-muted-foreground">{figure.hint}</dd>
              </div>
            ))}
          </dl>
        ) : null}

        {model.reconciles ? null : (
          <p role="status" className="text-sm text-destructive">
            These figures do not reconcile — available should equal balance minus reserved. Treat
            them as provisional and tell the infrx team.
          </p>
        )}

        {model.state.kind === "funded" ? null : (
          <div role="status" className="rounded-md border border-dashed p-3">
            <p className="font-medium">{model.state.headline}</p>
            <p className="mt-1 text-sm text-muted-foreground">{model.state.guidance}</p>
          </div>
        )}

        {model.grant === "" ? null : <p className="text-sm">{model.grant}</p>}
        <p className="text-xs text-muted-foreground">{model.notice}</p>
      </CardContent>
    </Card>
  );
}
