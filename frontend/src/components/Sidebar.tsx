"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  AgentsIcon,
  DashboardIcon,
  ExternalLinkIcon,
  LogoIcon,
  ReplayIcon,
  RunsIcon,
} from "@/components/icons";
import { PUBLIC_API_DOCS_URL, PUBLIC_JAEGER_URL } from "@/lib/env";

const NAV_ITEMS = [
  { href: "/", label: "Overview", icon: DashboardIcon, exact: true },
  { href: "/runs", label: "Runs", icon: RunsIcon, exact: false },
  { href: "/replays", label: "Replays", icon: ReplayIcon, exact: false },
  { href: "/agents", label: "Agents", icon: AgentsIcon, exact: false },
] as const;

export function Sidebar() {
  const pathname = usePathname() ?? "/";

  return (
    <aside className="flex flex-col border-b border-white/5 bg-ink-900/60 lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
      <div className="flex h-16 items-center gap-3 border-b border-white/5 px-5">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent">
          <LogoIcon className="h-5 w-5" />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-100">Agent Control Room</p>
          <p className="truncate text-[11px] uppercase tracking-[0.16em] text-slate-500">
            Observability plane
          </p>
        </div>
      </div>

      <nav className="flex gap-1 overflow-x-auto px-3 py-3 lg:flex-col lg:overflow-visible">
        {NAV_ITEMS.map((item) => {
          const active = item.exact ? pathname === item.href : pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`nav-link shrink-0 ${active ? "nav-link-active" : ""}`}
              aria-current={active ? "page" : undefined}
            >
              <Icon className="h-4 w-4" />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto hidden space-y-4 px-5 pb-6 lg:block">
        <div className="h-px bg-white/5" />
        <p className="text-[11px] uppercase tracking-[0.16em] text-slate-600">External tools</p>
        <div className="space-y-1">
          <a
            href={PUBLIC_JAEGER_URL}
            target="_blank"
            rel="noreferrer"
            className="nav-link px-0 py-1 text-xs"
          >
            <ExternalLinkIcon className="h-3.5 w-3.5" />
            Jaeger traces
          </a>
          <a
            href={PUBLIC_API_DOCS_URL}
            target="_blank"
            rel="noreferrer"
            className="nav-link px-0 py-1 text-xs"
          >
            <ExternalLinkIcon className="h-3.5 w-3.5" />
            API documentation
          </a>
        </div>
        <p className="text-[11px] leading-relaxed text-slate-600">
          v0.4.0
          <br />
          Traces, evaluations and replay diffs.
        </p>
      </div>
    </aside>
  );
}
