"use client";

import { useCallback, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { recallKey } from "@/lib/keys";
import type { Language } from "@/lib/types";

export type SnippetKey = { id: string; name: string; prefix: string };

const LABELS: Record<Language, string> = {
  curl: "cURL",
  python: "Python",
  javascript: "JavaScript",
};
const ORDER: Language[] = ["curl", "python", "javascript"];

// sessionStorage never changes behind our back within a tab, so there is nothing to
// subscribe to; useSyncExternalStore is here for the server snapshot (null), which
// keeps the secret out of the HTML and out of hydration mismatches.
const noSubscription = () => () => {};

function useStoredKey(id: string): string | null {
  const read = useCallback(() => (id ? recallKey(id) : null), [id]);
  return useSyncExternalStore(noSubscription, read, () => null);
}

export function Snippet({
  snippets,
  baseUrl,
  keys,
  disabled = false,
}: {
  snippets: Partial<Record<Language, string>>;
  baseUrl: string;
  keys: SnippetKey[];
  disabled?: boolean;
}) {
  const langs = ORDER.filter((l) => snippets[l]);
  const [lang, setLang] = useState<Language>(langs[0] ?? "curl");
  const [keyId, setKeyId] = useState(keys[0]?.id ?? "");
  const [copied, setCopied] = useState(false);

  const selected = keys.find((k) => k.id === keyId);
  const stored = useStoredKey(keyId);
  // Keys are stored hashed, so the server can never fill one in. If this tab minted
  // the key it still has the secret; otherwise the snippet carries the prefix and the
  // reader pastes the rest.
  const secret = stored ?? (selected ? `${selected.prefix}…` : "YOUR_API_KEY");

  function render(l: Language) {
    return (snippets[l] ?? "").replaceAll("{{BASE_URL}}", baseUrl).replaceAll("{{KEY}}", secret);
  }

  async function copy() {
    await navigator.clipboard.writeText(render(lang));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
    toast.success(`${LABELS[lang]} snippet copied`);
  }

  if (langs.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-lg border bg-muted/30">
      <Tabs value={lang} onValueChange={(v) => setLang(v as Language)} className="gap-0">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b p-2">
          <TabsList>
            {langs.map((l) => (
              <TabsTrigger key={l} value={l}>
                {LABELS[l]}
              </TabsTrigger>
            ))}
          </TabsList>

          <div className="flex items-center gap-2">
            {keys.length > 0 ? (
              <Select
                value={keyId}
                onValueChange={(v) => setKeyId(v as string)}
                disabled={disabled}
              >
                <SelectTrigger size="sm" className="max-w-44">
                  <SelectValue placeholder="Select a key" />
                </SelectTrigger>
                <SelectContent>
                  {keys.map((k) => (
                    <SelectItem key={k.id} value={k.id}>
                      {k.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Link
                href="/api-keys"
                className="text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
              >
                Create a key
              </Link>
            )}
            <Button size="sm" variant="outline" onClick={copy} disabled={disabled}>
              {copied ? <Check /> : <Copy />}
              Copy
            </Button>
          </div>
        </div>

        {langs.map((l) => (
          <TabsContent key={l} value={l}>
            <pre className="max-h-96 overflow-auto p-4 font-mono text-xs leading-relaxed text-foreground/90">
              <code>{render(l)}</code>
            </pre>
          </TabsContent>
        ))}
      </Tabs>

      <p className="border-t px-4 py-2 text-xs text-muted-foreground">
        {disabled
          ? "This model is not serving yet — the snippet is a preview."
          : stored
            ? "Ready to run — full key available in this browser session. It is never sent back to us; close the tab and only the prefix remains."
            : selected
              ? "Replace the truncated key with the full secret you copied when you created it; we only store its hash."
              : "Create an API key to fill this in."}
      </p>
    </div>
  );
}
