"use client";

import { useEffect, useState } from "react";

import { PUBLIC_API_BASE_URL } from "@/lib/env";
import type { Readiness } from "@/types";

type ApiState = "checking" | "online" | "degraded" | "offline";

const STATE_META: Record<ApiState, { label: string; dot: string; text: string }> = {
  checking: { label: "Checking API", dot: "bg-slate-500", text: "text-slate-500" },
  online: { label: "API online", dot: "bg-emerald-400", text: "text-emerald-300" },
  degraded: { label: "API degraded", dot: "bg-amber-400", text: "text-amber-300" },
  offline: { label: "API offline", dot: "bg-rose-400", text: "text-rose-300" },
};

export function ApiStatus() {
  const [state, setState] = useState<ApiState>("checking");
  const [detail, setDetail] = useState("");

  useEffect(() => {
    let disposed = false;

    const check = async (): Promise<void> => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5_000);
      try {
        const response = await fetch(`${PUBLIC_API_BASE_URL}/health/ready`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const body = (await response.json().catch(() => null)) as Readiness | null;
        if (disposed) {
          return;
        }
        if (response.ok && body?.status === "ok") {
          setState("online");
          setDetail("Database and Redis are reachable");
        } else {
          setState("degraded");
          const components = body?.components ?? {};
          setDetail(
            Object.entries(components)
              .map(([name, status]) => `${name}: ${status}`)
              .join(", ") || `Readiness returned ${response.status}`,
          );
        }
      } catch {
        if (!disposed) {
          setState("offline");
          setDetail(`No response from ${PUBLIC_API_BASE_URL}`);
        }
      } finally {
        clearTimeout(timeout);
      }
    };

    void check();
    const timer = setInterval(() => void check(), 30_000);

    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, []);

  const meta = STATE_META[state];

  return (
    <div
      className="flex items-center gap-2 rounded-full border border-white/5 bg-white/[0.02] px-3 py-1.5"
      title={detail || "Checking API readiness"}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${meta.dot} ${
          state === "checking" ? "animate-pulse-soft" : ""
        }`}
      />
      <span className={`text-[11px] font-medium ${meta.text}`}>{meta.label}</span>
    </div>
  );
}
