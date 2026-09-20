export function money(n: number | null | undefined, digits = 2): string {
  const v = Number(n ?? 0);
  // Sub-cent amounts are normal here (a request costs ~$0.0001), so widen rather than round to $0.00.
  const d = v !== 0 && Math.abs(v) < 0.01 ? 4 : digits;
  return v.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}

export function num(n: number | null | undefined, digits = 0): string {
  return Number(n ?? 0).toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function compact(n: number | null | undefined): string {
  return Number(n ?? 0).toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });
}

export function pct(ratio: number | null | undefined, digits = 1): string {
  return `${(Number(ratio ?? 0) * 100).toFixed(digits)}%`;
}

export function ms(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return Number(n) >= 1000 ? `${(Number(n) / 1000).toFixed(2)} s` : `${Math.round(Number(n))} ms`;
}

export function date(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
