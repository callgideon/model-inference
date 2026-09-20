"use client";

import { useRouter, useSearchParams } from "next/navigation";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RANGES, type RangeKey } from "./ranges";

export function UsageControls({ keys }: { keys: { id: string; name: string }[] }) {
  const router = useRouter();
  const params = useSearchParams();
  const range = (params.get("range") ?? "24h") as RangeKey;
  const key = params.get("key") ?? "all";

  function set(name: string, value: string) {
    const next = new URLSearchParams(params.toString());
    if (value === "all" && name === "key") next.delete(name);
    else next.set(name, value);
    router.push(`/usage?${next.toString()}`);
  }

  return (
    <div className="flex items-center gap-2">
      <Select value={key} onValueChange={(v) => set("key", v as string)}>
        <SelectTrigger size="sm" className="max-w-44">
          <SelectValue placeholder="All keys" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All keys</SelectItem>
          {keys.map((k) => (
            <SelectItem key={k.id} value={k.id}>
              {k.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={range} onValueChange={(v) => set("range", v as string)}>
        <SelectTrigger size="sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.keys(RANGES).map((r) => (
            <SelectItem key={r} value={r}>
              Last {r}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
