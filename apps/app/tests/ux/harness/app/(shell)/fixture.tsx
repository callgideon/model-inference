"use client";

// Synthetic accounts: ?balance=unavailable (the wallet could not be read), ?operator=1, ?email=long.
import { useSearchParams } from "next/navigation";
import { Sidebar } from "@/components/sidebar";

const LONG_EMAIL = "a-very-long-synthetic-address-for-overflow-checks.with.many.parts@subdomain.example.test";

export function Fixture() {
  const q = useSearchParams();
  return (
    <Sidebar
      email={q.get("email") === "long" ? LONG_EMAIL : "fixture@example.test"}
      balance={q.get("balance") === "unavailable" ? null : "10,000"}
      isOperator={q.get("operator") === "1"}
    />
  );
}
