"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { revokeApiKey } from "./actions";

export function RevokeButton({ id, name }: { id: string; name: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function revoke() {
    if (!confirm(`Revoke "${name}"? Calls using it start failing within a minute.`)) return;
    setPending(true);
    const { error } = await revokeApiKey(id);
    setPending(false);
    if (error) toast.error(error);
    else {
      toast.success(`Revoked ${name}`);
      router.refresh();
    }
  }

  return (
    <Button
      size="icon-sm"
      variant="ghost"
      onClick={revoke}
      disabled={pending}
      aria-label={`Revoke ${name}`}
    >
      {pending ? <Loader2 className="animate-spin" /> : <Trash2 className="text-destructive" />}
    </Button>
  );
}
