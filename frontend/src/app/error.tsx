"use client";

import { useEffect } from "react";

import { RefreshButton } from "@/components/RefreshButton";
import { AlertTriangleIcon } from "@/components/icons";

/**
 * Route-level error boundary. Server data fetching failures normally render an
 * inline `ConnectionError`; this catches render and client exceptions.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surfaced in the browser console; the backend logs the request that failed.
    console.error("Agent Control Room render error", error);
  }, [error]);

  return (
    <div className="card mx-auto max-w-2xl p-6">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-rose-400/10 text-rose-300">
          <AlertTriangleIcon className="h-5 w-5" />
        </span>
        <div className="space-y-2">
          <h1 className="text-base font-semibold text-slate-100">Something went wrong</h1>
          <p className="text-sm text-slate-400">
            {error.message || "An unexpected error occurred while rendering this page."}
          </p>
          {error.digest ? (
            <p className="text-[11px] text-slate-600">
              Digest <code className="font-mono text-slate-500">{error.digest}</code>
            </p>
          ) : null}
          <div className="flex gap-2 pt-1">
            <button type="button" className="btn-primary" onClick={reset}>
              Try again
            </button>
            <RefreshButton label="Reload data" />
          </div>
        </div>
      </div>
    </div>
  );
}
