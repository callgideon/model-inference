import { createClient } from "@supabase/supabase-js";

/** Service-role client: bypasses RLS. Server only — never import from a client component. */
export function createAdminClient() {
  if (typeof window !== "undefined") {
    throw new Error("createAdminClient() is server-only");
  }
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!key) throw new Error("SUPABASE_SERVICE_ROLE_KEY is not set");
  return createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}
