"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

interface AutoRefreshProps {
  enabled: boolean;
  intervalMs?: number;
}

/**
 * Refresh server data while any run in view is still pending or running.
 * The run detail page uses SSE instead; list pages use this lightweight poll.
 */
export function AutoRefresh({ enabled, intervalMs = 5_000 }: AutoRefreshProps) {
  const router = useRouter();

  useEffect(() => {
    if (!enabled) {
      return;
    }
    const timer = setInterval(() => router.refresh(), intervalMs);
    return () => clearInterval(timer);
  }, [enabled, intervalMs, router]);

  if (!enabled) {
    return null;
  }

  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-sky-300" title="Active runs refresh automatically">
      <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-sky-400" />
      Auto-refreshing
    </span>
  );
}
