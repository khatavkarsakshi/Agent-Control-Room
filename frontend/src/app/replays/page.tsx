import type { Metadata } from "next";
import Link from "next/link";

import { InboxIcon, ReplayIcon } from "@/components/icons";
import { Pagination } from "@/components/Pagination";
import { ReplayStatusBadge } from "@/components/StatusBadge";
import { ConnectionError, EmptyState } from "@/components/States";
import { listReplaySessions } from "@/lib/api";
import { PUBLIC_API_BASE_URL } from "@/lib/env";
import { toErrorMessage } from "@/lib/errors";
import {
  formatDateTime,
  formatScore,
  formatSignedCost,
  formatSignedDuration,
  shortId,
} from "@/lib/format";
import type { ReplayComparison, ReplaySession, ReplaySessionList, ReplayStatus } from "@/types";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Replays" };

const STATUSES: ReplayStatus[] = ["pending", "running", "completed", "failed"];

type SearchParams = Record<string, string | string[] | undefined>;

interface ReplaysPageProps {
  searchParams: Promise<SearchParams>;
}

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function positiveInt(value: string | undefined, fallback: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return fallback;
  }
  return Math.min(parsed, max);
}

function comparisonOf(session: ReplaySession): ReplayComparison | null {
  const comparison = session.comparison_result;
  if (!comparison || !("output" in comparison)) {
    return null;
  }
  return comparison;
}

export default async function ReplaysPage({ searchParams }: ReplaysPageProps) {
  const params = await searchParams;
  const statusParam = first(params.status);
  const status =
    statusParam && STATUSES.includes(statusParam as ReplayStatus)
      ? (statusParam as ReplayStatus)
      : undefined;
  const originalRunId = first(params.original_run_id);
  const limit = positiveInt(first(params.limit), 25, 200);
  const offset = Math.max(Number(first(params.offset) ?? 0) || 0, 0);

  let sessions: ReplaySessionList;
  try {
    sessions = await listReplaySessions({
      original_run_id: originalRunId,
      status,
      limit,
      offset,
    });
  } catch (error) {
    return (
      <ConnectionError
        title="Could not load replay sessions"
        message={toErrorMessage(error)}
        apiBaseUrl={PUBLIC_API_BASE_URL}
      />
    );
  }

  const hasActive = sessions.items.some(
    (session) => session.status === "pending" || session.status === "running",
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-50">Replays</h1>
          <p className="mt-1 text-sm text-slate-500">
            {sessions.total.toLocaleString()} replay session{sessions.total === 1 ? "" : "s"}
            {originalRunId ? ` for run ${shortId(originalRunId)}` : ""}. Offline replays reuse
            recorded responses; live replays call the provider again.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {status ? (
            <Link href="/replays" className="btn-ghost px-2.5 py-1.5 text-xs">
              Clear filters
            </Link>
          ) : null}
          <Link href="/runs" className="btn-ghost">
            <ReplayIcon className="h-4 w-4" />
            Start from a run
          </Link>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        <FilterChip label="All" href={replaysHref(originalRunId)} active={!status} />
        {STATUSES.map((value) => (
          <FilterChip
            key={value}
            label={value}
            href={replaysHref(originalRunId, value)}
            active={status === value}
          />
        ))}
        {hasActive ? (
          <span className="badge ml-auto border-sky-400/30 bg-sky-400/10 text-sky-300">
            in flight
          </span>
        ) : null}
      </div>

      <section className="card overflow-hidden">
        {sessions.items.length === 0 ? (
          <EmptyState
            icon={InboxIcon}
            title="No replay sessions yet"
            description="Open a finished run, choose Replay this run, and compare a what-if execution against the original."
            action={
              <Link href="/runs" className="btn-primary">
                Browse runs
              </Link>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[920px] text-left text-sm">
              <thead>
                <tr className="border-b border-white/5 text-[11px] uppercase tracking-wider text-slate-500">
                  <th className="px-5 py-3 font-medium">Session</th>
                  <th className="px-5 py-3 font-medium">Mode</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Runs</th>
                  <th className="px-5 py-3 font-medium">Output similarity</th>
                  <th className="px-5 py-3 font-medium">Latency Δ</th>
                  <th className="px-5 py-3 font-medium">Cost Δ</th>
                  <th className="px-5 py-3 font-medium">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {sessions.items.map((session) => {
                  const comparison = comparisonOf(session);
                  const mode = comparison?.mode ?? String(session.overrides.mode ?? "live");
                  return (
                    <tr key={session.id} className="hover:bg-white/[0.02]">
                      <td className="px-5 py-3">
                        <Link
                          href={`/replays/${session.id}`}
                          className="font-mono text-xs text-accent hover:underline"
                        >
                          {shortId(session.id, 10)}
                        </Link>
                        {comparison?.billed === false ? (
                          <span className="ml-2 rounded border border-violet-400/30 bg-violet-400/10 px-1.5 py-0.5 text-[10px] text-violet-300">
                            no spend
                          </span>
                        ) : null}
                      </td>
                      <td className="px-5 py-3">
                        <span
                          className={`badge ${
                            mode === "offline"
                              ? "border-violet-400/30 bg-violet-400/10 text-violet-300"
                              : "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                          }`}
                        >
                          {mode}
                        </span>
                      </td>
                      <td className="px-5 py-3">
                        <ReplayStatusBadge status={session.status} />
                      </td>
                      <td className="px-5 py-3 text-xs">
                        <Link
                          href={`/runs/${session.original_run_id}`}
                          className="font-mono text-slate-400 hover:text-accent"
                        >
                          {shortId(session.original_run_id)}
                        </Link>
                        <span className="mx-1.5 text-slate-600">→</span>
                        {session.replay_run_id ? (
                          <Link
                            href={`/runs/${session.replay_run_id}`}
                            className="font-mono text-slate-400 hover:text-accent"
                          >
                            {shortId(session.replay_run_id)}
                          </Link>
                        ) : (
                          <span className="text-slate-600">—</span>
                        )}
                      </td>
                      <td className="px-5 py-3 font-mono text-xs text-slate-300">
                        {comparison ? formatScore(comparison.output.similarity) : "—"}
                        {comparison?.output.identical ? (
                          <span className="ml-1.5 text-[10px] text-emerald-300">identical</span>
                        ) : null}
                      </td>
                      <td className="px-5 py-3 font-mono text-xs text-slate-300">
                        {comparison
                          ? formatSignedDuration(comparison.latency_ms.delta)
                          : "—"}
                      </td>
                      <td className="px-5 py-3 font-mono text-xs text-slate-300">
                        {comparison ? formatSignedCost(comparison.cost_usd.delta) : "—"}
                      </td>
                      <td className="px-5 py-3 text-xs text-slate-500">
                        {formatDateTime(session.created_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <Pagination
          basePath="/replays"
          total={sessions.total}
          limit={sessions.limit}
          offset={sessions.offset}
          query={{ status, original_run_id: originalRunId }}
        />
      </section>

      <p className="text-xs text-slate-600">
        Similarity is a whitespace-normalised character ratio, not a semantic judgement.
      </p>
    </div>
  );
}

function replaysHref(originalRunId: string | undefined, status?: ReplayStatus): string {
  const params = new URLSearchParams();
  if (status) {
    params.set("status", status);
  }
  if (originalRunId) {
    params.set("original_run_id", originalRunId);
  }
  const query = params.toString();
  return query ? `/replays?${query}` : "/replays";
}

function FilterChip({
  label,
  href,
  active,
}: {
  label: string;
  href: string;
  active: boolean;
}) {
  return (
    <Link
      href={href}
      className={`rounded-full border px-3 py-1 text-xs capitalize ${
        active
          ? "border-accent/40 bg-accent/10 text-accent"
          : "border-white/10 bg-white/5 text-slate-400 hover:text-slate-200"
      }`}
    >
      {label}
    </Link>
  );
}
