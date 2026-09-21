import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { BalanceCardModel } from "./view-model";

/**
 * Markup only — every number, label and sentence comes from `balanceCardModel()` in
 * `./view-model.ts`, which is what the tests under `tests/u/` exercise. There is deliberately no
 * control here that takes money.
 */
export function PromotionalBalanceCard({ model }: { model: BalanceCardModel }) {
  return (
    <Card>
      <CardHeader className="grid-cols-[1fr_auto] items-center">
        <CardTitle>Balance</CardTitle>
        <Badge variant="secondary">Promotional credit</Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="grid gap-4 sm:grid-cols-3">
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

        {model.reconciles ? null : (
          <p role="status" className="text-sm text-destructive">
            These three figures do not reconcile — available should equal promotional credit minus
            reserved. Treat them as provisional and tell us.
          </p>
        )}

        {model.state.kind === "funded" ? null : (
          <div className="rounded-md border border-dashed p-3">
            <p className="font-medium">{model.state.headline}</p>
            <p className="mt-1 text-sm text-muted-foreground">{model.state.guidance}</p>
          </div>
        )}

        <p className="text-xs text-muted-foreground">{model.notice}</p>
      </CardContent>
    </Card>
  );
}
