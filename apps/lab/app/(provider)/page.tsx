import { redirect } from "next/navigation";
import { requireProviderWorkspace } from "@/lib/auth/guard";

// L4: the Lab home is the overview.
export default async function Home() {
  await requireProviderWorkspace();
  redirect("/overview");
}
