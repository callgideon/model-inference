import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { KeyOption } from "../billing/credit-reads";
import { JOB_RANGES, MAX_MODEL_CHARS, type JobFilters, type JobRange } from "./credit-view-model";

const LABELS: Record<JobRange, string> = { all: "All time", "24h": "Last 24h", "7d": "Last 7d", "30d": "Last 30d" };
const ALL_KEYS = "all";

/**
 * The window, key and model filters, held in the URL: a plain GET form, so applying a filter
 * always starts the walk again (no cursor field - a cursor was a position in the old filters).
 * `parseJobFilters` drops anything it does not recognise, "all" keys included.
 */
export function UsageControls({ filters, keys }: { filters: JobFilters; keys: KeyOption[] }) {
  const ranges: JobRange[] = ["all", ...(Object.keys(JOB_RANGES) as JobRange[])];
  const keyItems: Record<string, string> = { [ALL_KEYS]: "All keys" };
  for (const key of keys) keyItems[key.id] = `${key.name} (${key.prefix}…)`;
  return (
    <form action="/usage" className="flex flex-wrap items-center gap-2">
      <Select name="range" defaultValue={filters.range} items={LABELS}>
        <SelectTrigger size="sm" aria-label="Time range">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {ranges.map((range) => (
            <SelectItem key={range} value={range}>
              {LABELS[range]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select name="key" defaultValue={filters.keyId ?? ALL_KEYS} items={keyItems}>
        <SelectTrigger size="sm" aria-label="API key">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.entries(keyItems).map(([id, label]) => (
            <SelectItem key={id} value={id}>
              {label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Input
        name="model"
        defaultValue={filters.model ?? ""}
        maxLength={MAX_MODEL_CHARS}
        placeholder="Model or revision"
        aria-label="Model"
        className="h-8 w-48"
      />
      <Button type="submit" size="sm" variant="outline">
        Apply
      </Button>
    </form>
  );
}
