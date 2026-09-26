"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { revokeConsumerKey } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { REVOKE_LOST, revokeConfirmText, settle } from "./view-model";

export function RevokeButton({ id, name }: { id: string; name: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function revoke() {
    if (pending || !confirm(revokeConfirmText(name))) return;
    setPending(true);
    const result = await settle(() => revokeConsumerKey(id), REVOKE_LOST);
    setPending(false);
    if (!result.ok) toast.error(result.error.message);
    else toast.success(`Revoked ${name}`);
    // Either way the list re-reads committed state: a failed revoke may still have landed.
    router.refresh();
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
