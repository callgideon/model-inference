import { PageHeader } from "@/components/page-header";
import { createFakeConsoleServices } from "../../../lib/contracts/fake-services.ts";
import { currentAnchor, parseTraceParams } from "./query.ts";
import { TraceFilters } from "./trace-filters";
import { RejectedFilters, TraceTable } from "./trace-table";
import { buildTraceListView } from "./view-model.ts";

export const metadata = { title: "Traces · infrx" };

/**
 * V1 — the trace list.
 *
 * It reads through `ConsoleServices` and nothing else: no ClickHouse, no S3, no query parameter
 * reaching a service unchecked. The services here are the **contract fake** (03: UI tracks build
 * against fixtures while C implements the real ones), so this page is *implemented*, not
 * integrated. Integration request V1-IR2 replaces the two marked lines with C1's provider and the
 * trusted `getSession()` context; nothing else in this directory changes, because the fake and the
 * real services are the same interface.
 */
export default async function TracesPage({ searchParams }: PageProps<"/traces">) {
  const params = await searchParams;

  // --- V1-IR2: swap for C1's `createConsoleServices()` + `getSession()` ---------------------
  const services = createFakeConsoleServices();
  const session = services.sessions.owner;
  // -----------------------------------------------------------------------------------------

  const keysResult = await services.keys.list(session);
  // A failed key read is not "this organization has no keys": it costs the key names and the
  // "is anything captured?" fact, and the list says so rather than inventing either.
  const keys = keysResult.ok ? keysResult.value : [];
  const parsed = parseTraceParams(params, { now: currentAnchor(), keyIds: keys.map((key) => key.id) });
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
