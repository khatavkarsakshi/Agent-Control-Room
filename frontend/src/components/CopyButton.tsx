"use client";

import { useState } from "react";

import { CheckIcon, CopyIcon } from "@/components/icons";

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => void copy()}>
      {copied ? <CheckIcon className="h-3.5 w-3.5 text-emerald-300" /> : <CopyIcon className="h-3.5 w-3.5" />}
      {copied ? "Copied" : label}
    </button>
  );
}
