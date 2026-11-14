"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { XCircleIcon } from "@/components/icons";
import { cancelReplay } from "@/lib/client";
import { toErrorMessage } from "@/lib/errors";

export function CancelReplayButton({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cancel = async (): Promise<void> => {
    setIsPending(true);
    setError(null);
    try {
      await cancelReplay(sessionId);
      router.refresh();
    } catch (cause) {
      setError(toErrorMessage(cause));
    } finally {
      setIsPending(false);
      setConfirming(false);
    }
  };

  if (error) {
    return <span className="text-xs text-rose-300">{error}</span>;
  }

  if (!confirming) {
    return (
      <button type="button" className="btn-danger" onClick={() => setConfirming(true)}>
        <XCircleIcon className="h-4 w-4" />
        Cancel replay
      </button>
    );
  }

  return (
    <span className="inline-flex items-center gap-2">
      <button type="button" className="btn-danger" onClick={() => void cancel()} disabled={isPending}>
        <XCircleIcon className="h-4 w-4" />
        {isPending ? "Cancelling…" : "Confirm cancel"}
      </button>
      <button
        type="button"
        className="btn-ghost"
        onClick={() => setConfirming(false)}
        disabled={isPending}
      >
        Keep running
      </button>
    </span>
  );
}
