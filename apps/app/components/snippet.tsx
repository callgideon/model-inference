"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Language } from "@/lib/types";

/** UX-04 (audit UX-A06): the code never holds a key in any form; it reads INFRX_API_KEY. */
export const KEY_NOTE =
  "Set INFRX_API_KEY in your shell to the full key you saved when you created it. This code never contains your key.";
export const REFERENCE_NOTE = "This model is not accepting requests: this code is a reference, not a call you can make now.";

const LABELS: Record<Language, string> = {
  curl: "cURL",
  python: "Python",
  javascript: "JavaScript",
};
const ORDER: Language[] = ["curl", "python", "javascript"];

export function Snippet({
  snippets,
  baseUrl,
  disabled = false,
}: {
  snippets: Partial<Record<Language, string>>;
  baseUrl: string;
  disabled?: boolean;
}) {
  const langs = ORDER.filter((l) => snippets[l]);
  const [lang, setLang] = useState<Language>(langs[0] ?? "curl");
  const [copied, setCopied] = useState(false);

  function render(l: Language) {
    return (snippets[l] ?? "").replaceAll("{{BASE_URL}}", baseUrl);
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(render(lang));
    } catch {
      toast.error("Copying was blocked; select the code and copy it by hand.");
      return;
    }
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

          <Button size="sm" variant="outline" onClick={copy}>
            {copied ? <Check /> : <Copy />}
            Copy
          </Button>
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
        {disabled ? REFERENCE_NOTE : KEY_NOTE}
      </p>
    </div>
  );
}
