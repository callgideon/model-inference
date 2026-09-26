import { notFound } from "next/navigation";
import { ConsoleDataUnavailable, ConsolePreviewNotice } from "@/components/console-data-state";
import { PageHeader } from "@/components/page-header";
import { providerRoute } from "@/lib/services/console";
import { getSession } from "@/lib/session";
import { consoleContext } from "../usage/fake-console-context";
import { parseTraceParams } from "./query.ts";
import { TraceFilters } from "./trace-filters";
import { RejectedFilters, TraceTable } from "./trace-table";
import { buildTraceListView } from "./view-model.ts";

export const metadata = { title: "Traces · infrx" };

/** V1 preview retained for migration to apps/lab after L1/L2. No provider content is served here. */
export default async function TracesPage({ searchParams }: PageProps<"/traces">) {
  providerRoute(await getSession(), notFound);
  const params = await searchParams;

  const context = consoleContext();
  if (context === null) return <ConsoleDataUnavailable title="Traces" />;
  const { services, session, now } = context;

  const keysResult = await services.keys.list(session);
  // A failed key read is not "this organization has no keys": it costs the key names and the
  // "is anything captured?" fact, and the list says so rather than inventing either.
  const keys = keysResult.ok ? keysResult.value : [];
  const parsed = parseTraceParams(params, { now: now.getTime(), keyIds: keys.map((key) => key.id) });
  const result = await services.traces(session, parsed.query);
  const view = buildTraceListView(result, {
    filters: parsed.filters,
    keys,
    narrowed: parsed.narrowed,
    keysUnavailable: !keysResult.ok,
  });

  // The list row carries an opaque model id and nothing enumerates the catalogue, so the filter
  // offers the models this window actually contains (plus whatever is already selected).
  const models = [
    ...new Set([
      ...(view.kind === "rows" ? view.rows.map((row) => row.model) : []),
      ...(parsed.filters.model === null ? [] : [parsed.filters.model]),
    ]),
  ].sort();

  return (
    <>
      <ConsolePreviewNotice />
      <PageHeader
        title="Traces"
        subtitle={`Per-request traces for keys with tracing on. Last ${parsed.filters.range}.`}
        action={
          <TraceFilters
            filters={parsed.filters}
            keys={keys.map((key) => ({ id: key.id, name: key.name }))}
            models={models}
          />
        }
      />
      <RejectedFilters rejected={parsed.rejected} ignored={parsed.ignored} />
      <TraceTable view={view} />
    </>
  );
}
