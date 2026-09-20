import { Mail } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata = { title: "Dedicated · infrx" };

const MAILTO =
  "mailto:sofia@callsofia.co?subject=" +
  encodeURIComponent("Dedicated capacity") +
  "&body=" +
  encodeURIComponent(
    "Model:\nExpected requests per second:\nRegion:\nLatency target:\nAnything else:\n",
  );

export default function DedicatedPage() {
  return (
    <>
      <PageHeader
        title="Dedicated"
        subtitle="Reserved GPUs for steady traffic, custom models or private networking."
      />
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Request dedicated capacity</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm text-muted-foreground">
          <p>
            Shared endpoints are rate limited and priced per token. A dedicated deployment gives you
            a fixed pool of GPUs: predictable latency under load, no per-token ceiling, your own
            model weights if you have them, and an hourly price instead of a per-token one.
          </p>
          <p>
            Tell us the model, the traffic shape and the latency you need and we will size it and
            quote it. Deployments run on 8×B300 nodes in us-east-1 today.
          </p>
          <Button render={<a href={MAILTO} />}>
            <Mail />
            Email us
          </Button>
        </CardContent>
      </Card>
    </>
  );
}
