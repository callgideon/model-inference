import Link from "next/link";
import { ConsolePreviewNotice } from "@/components/console-data-state";
import { PageHeader } from "@/components/page-header";
import { consumerCreditReads } from "../billing/credit-context";
import { creditCardState } from "../billing/credit-view-model";
import { CreditSummary } from "./credit-summary";
import { jobsPageModel, jobsPageRequest, parseJobFilters } from "./credit-view-model";
import { RequestsTable } from "./requests-table";
import { EmptyPanel, ErrorPanel, Pager } from "./states";
import { UsageControls } from "./usage-controls";

export const metadata = { title: "Usage · infrx" };

export default async function UsagePage({ searchParams }: PageProps<"/usage">) {
  const params = await searchParams;
  const { reads, preview, now } = await consumerCreditReads();
  const filters = parseJobFilters(params);

  const [wallet, jobs] = await Promise.all([reads.wallet(), reads.jobs(jobsPageRequest(filters, now))]);
  const found = wallet.ok ? wallet.value : null;
  const [creditsIn, keys] =
    found === null ? [null, null] : await Promise.all([reads.creditsIn(found.walletId), reads.keys(found.orgId)]);
  const card = creditCardState(wallet, creditsIn);
  const model = jobsPageModel({ filters, jobs });

  return (
    <>
      {preview ? <ConsolePreviewNotice /> : null}
      <PageHeader
        title="Usage"
        subtitle="Requests, results and credit charges."
        action={<UsageControls filters={model.filters} keys={keys?.ok ? keys.value : []} />}
      />

      <h2 className="mb-3 font-heading text-base font-medium">Requests</h2>

      {model.rows.kind === "error" ? (
        <ErrorPanel
          title="We couldn’t load usage"
          state={model.rows}
          href={model.here}
          firstPageHref={model.firstHref}
        />
      ) : null}

      {model.rows.kind === "empty" ? (
        <EmptyPanel>
          <p>{model.emptyText}</p>
          <Link className="mt-2 inline-block underline underline-offset-4" href={model.emptyAction.href}>
            {model.emptyAction.label}
          </Link>
        </EmptyPanel>
      ) : null}

      {model.rows.kind === "ready" ? (
        <>
          <RequestsTable rows={model.rows.value.rows} />
          <Pager
            label="Usage pages"
            page={model.rows.value.page}
            firstHref={model.rows.value.firstHref}
            previousHref={model.rows.value.previousHref}
            nextHref={model.rows.value.nextHref}
          />
          <p className="mt-2 text-xs text-muted-foreground">
            <strong>Charged</strong> is what settlement took from your balance, in the unit shown.{" "}
            <strong>Held</strong> is reserved while a request runs or awaits reconciliation — it is
            not a charge, and usage that was not reported is never estimated into one.
          </p>
        </>
      ) : null}

      <CreditSummary card={card} href={model.here} />
    </>
  );
}
