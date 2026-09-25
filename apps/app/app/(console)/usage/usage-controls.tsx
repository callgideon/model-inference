"use client";

import { useRouter } from "next/navigation";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { JOB_RANGES, jobsHref, withJobRange, type JobFilters, type JobRange } from "./credit-view-model";

const LABELS: Record<JobRange, string> = { all: "All time", "24h": "Last 24h", "7d": "Last 7d", "30d": "Last 30d" };

/**
 * The time window, held in the URL. `withJobRange` discards the cursor: it was a position in the
 * old window. There is no key or model filter because the consumer read does not take one yet.
 */
export function UsageControls({ filters }: { filters: JobFilters }) {
  const router = useRouter();
  const ranges: JobRange[] = ["all", ...(Object.keys(JOB_RANGES) as JobRange[])];
  return (
    <Select
      value={filters.range}
      onValueChange={(value) => router.push(jobsHref(withJobRange(filters, value as JobRange)))}
    >
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
  );
}
