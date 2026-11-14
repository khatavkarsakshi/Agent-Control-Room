import type { ComponentType, ReactNode } from "react";

import { RefreshButton } from "@/components/RefreshButton";

interface EmptyStateProps {
  icon: ComponentType<{ className?: string }>;
  title: string;
  description: string;
  action?: ReactNode;
}

export function EmptyState({ icon: Icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-2xl bg-white/5 text-slate-400">
        <Icon className="h-6 w-6" />
      </span>
      <div className="space-y-1">
        <p className="text-sm font-semibold text-slate-200">{title}</p>
        <p className="mx-auto max-w-md text-sm text-slate-500">{description}</p>
      </div>
      {action}
    </div>
  );
}

export function ConnectionError({
  title = "The API is unreachable",
  message,
  apiBaseUrl,
}: {
  title?: string;
  message: string;
  apiBaseUrl: string;
}) {
  return (
    <div className="card mx-auto max-w-2xl p-6">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-rose-400/10 text-rose-300">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" className="h-5 w-5" aria-hidden="true">
            <path d="M10.3 3.9 2.6 17.2A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.8L13.7 3.9a2 2 0 0 0-3.4 0Z" />
            <path d="M12 9v4M12 16.5h.01" />
          </svg>
        </span>
        <div className="space-y-2">
          <h2 className="text-base font-semibold text-slate-100">{title}</h2>
          <p className="text-sm text-slate-400">{message}</p>
          <ul className="list-inside list-disc space-y-1 text-xs text-slate-500">
            <li>
              Expected API base URL: <code className="font-mono text-slate-400">{apiBaseUrl}</code>
            </li>
            <li>Start it with <code className="font-mono text-slate-400">docker compose up</code> or <code className="font-mono text-slate-400">uvicorn app.main:app</code> in <code className="font-mono text-slate-400">backend/</code>.</li>
            <li>Set <code className="font-mono text-slate-400">API_BASE_URL</code> for server rendering and <code className="font-mono text-slate-400">NEXT_PUBLIC_API_BASE_URL</code> for the browser.</li>
          </ul>
          <div className="pt-1">
            <RefreshButton label="Retry" />
          </div>
        </div>
      </div>
    </div>
  );
}
