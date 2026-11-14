"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";

import { RefreshIcon } from "@/components/icons";

export function RefreshButton({ label = "Refresh" }: { label?: string }) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  return (
    <button
      type="button"
      className="btn-ghost"
      onClick={() => startTransition(() => router.refresh())}
      disabled={isPending}
    >
      <RefreshIcon className={`h-4 w-4 ${isPending ? "animate-spin" : ""}`} />
      {isPending ? "Refreshing" : label}
    </button>
  );
}
