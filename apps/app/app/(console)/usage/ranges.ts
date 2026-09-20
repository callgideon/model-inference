const MIN = 60_000;

/** Time-range picker values (spec F5), in milliseconds. */
export const RANGES = {
  "5m": 5 * MIN,
  "15m": 15 * MIN,
  "30m": 30 * MIN,
  "1h": 60 * MIN,
  "6h": 6 * 60 * MIN,
  "24h": 24 * 60 * MIN,
  "7d": 7 * 24 * 60 * MIN,
  "30d": 30 * 24 * 60 * MIN,
} as const;

export type RangeKey = keyof typeof RANGES;

export function resolveRange(value: string | undefined): { key: RangeKey; from: Date; to: Date } {
  const key = (value && value in RANGES ? value : "24h") as RangeKey;
  const to = new Date();
  return { key, from: new Date(to.getTime() - RANGES[key]), to };
}
