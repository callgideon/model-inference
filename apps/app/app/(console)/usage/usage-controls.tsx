"use client";

import { useRouter } from "next/navigation";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ALL, RANGES, usageHref, withFilter, type UsageFilters } from "./view-model";

/**
 * The three filters, held in the URL. Every transition goes through `withFilter`, which discards
 * the pagination cursor — a cursor is bound to the filters it was minted for, so keeping it would
 * hand the service a cursor from another scope (`invalid_cursor`). The logic lives in
 * `view-model.ts` and is tested there; this file is the control surface.
 */
export function UsageControls({
  filters,
  keys,
  models,
}: {
  filters: UsageFilters;
  keys: { id: string; name: string }[];
  models: string[];
}) {
  const router = useRouter();
  const go = (patch: Partial<Pick<UsageFilters, "range" | "keyId" | "model">>) =>
    router.push(usageHref(withFilter(filters, patch)));

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        value={filters.keyId ?? ALL}
        onValueChange={(value) => go({ keyId: value === ALL ? null : String(value) })}
      >
        <SelectTrigger size="sm" className="max-w-44" aria-label="Filter by API key">
          <SelectValue placeholder="All keys" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All keys</SelectItem>
          {keys.map((key) => (
            <SelectItem key={key.id} value={key.id}>
              {key.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={filters.model ?? ALL}
        onValueChange={(value) => go({ model: value === ALL ? null : String(value) })}
      >
        <SelectTrigger size="sm" className="max-w-56" aria-label="Filter by model">
          <SelectValue placeholder="All models" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All models</SelectItem>
          {models.map((model) => (
            <SelectItem key={model} value={model}>
              {model}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={filters.range}
        onValueChange={(value) => go({ range: value as UsageFilters["range"] })}
      >
        <SelectTrigger size="sm" aria-label="Time range">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.keys(RANGES).map((range) => (
            <SelectItem key={range} value={range}>
              Last {range}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
