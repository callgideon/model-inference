import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { money } from "@/lib/format";
import type { Credits } from "@/lib/credits";

export function CreditsCard({ credits }: { credits: Credits }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Credits</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="grid grid-cols-3 gap-4 text-sm">
          <Figure label="Available" value={money(credits.available)} emphasis />
          <Figure label="Loaded" value={money(credits.loaded)} />
          <Figure label="Spent" value={money(credits.spent)} />
        </dl>
        <Button disabled title="Coming soon — payments are not wired up yet" className="w-full">
          Add credits
        </Button>
      </CardContent>
    </Card>
  );
}

function Figure({ label, value, emphasis }: { label: string; value: string; emphasis?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        className={
          emphasis
            ? "font-heading text-lg font-semibold tabular-nums"
            : "mt-0.5 tabular-nums text-muted-foreground"
        }
      >
        {value}
      </dd>
    </div>
  );
}
