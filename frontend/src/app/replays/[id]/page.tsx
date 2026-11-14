import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { CancelReplayButton } from "@/components/CancelReplayButton";
import { JsonBlock } from "@/components/JsonBlock";
import { RefreshButton } from "@/components/RefreshButton";
import { ReplayStatusBadge, RunStatusBadge } from "@/components/StatusBadge";
import { ConnectionError } from "@/components/States";
import {
  AlertTriangleIcon,
  ArrowLeftIcon,
  CoinsIcon,
  GaugeIcon,
  GitBranchIcon,
  ReplayIcon,
  TokensIcon,
} from "@/components/icons";
import { getReplaySession } from "@/lib/api";
import { PUBLIC_API_BASE_URL } from "@/lib/env";
import { isNotFound, toErrorMessage } from "@/lib/errors";
import {
  formatCost,
  formatDateTime,
  formatDuration,
  formatScore,
  formatSigned,
  formatSignedCost,
  formatSignedDuration,
  shortId,
} from "@/lib/format";
import type {
  ComparisonDelta,
  ReplayComparison,
  ReplayEvaluationSnapshot,
  ReplaySession,
  RunStatus,
  SpanStats,
  ToolCallStats,
} from "@/types";

export const dynamic = "force-dynamic";

interface ReplayPageProps {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: ReplayPageProps): Promise<Metadata> {
  const { id } = await params;
  return { title: `Replay ${shortId(id, 10)}` };
}

function isComparison(value: ReplaySession["comparison_result"]): value is ReplayComparison {
  return value !== null && typeof value === "object" && "output" in value;
}

function isEvaluationSnapshot(
  value: ReplayComparison["evaluation"],
): value is ReplayEvaluationSnapshot {
  return value !== undefined && typeof value === "object" && "scores" in value;
}

export default async function ReplayDetailPage({ params }: ReplayPageProps) {
  const { id } = await params;

  let session: ReplaySession;
  try {
    session = await getReplaySession(id);
  } catch (error) {
    if (isNotFound(error)) {
      notFound();
    }
    return (
      <ConnectionError
        title="Could not load this replay session"
        message={toErrorMessage(error)}
        apiBaseUrl={PUBLIC_API_BASE_URL}
      />
    );
  }

  const detailed = session.comparison_result;
  const comparison = isComparison(detailed) ? detailed : null;
  const failure =
    detailed && !isComparison(detailed) && typeof detailed === "object" && "error" in detailed
      ? detailed.error
      : null;
  const mode = comparison?.mode ?? String(session.overrides.mode ?? "live");
  const active = session.status === "pending" || session.status === "running";

  return (
    <div className="space-y-6">
      <div>
        <Link href="/replays" className="inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300">
          <ArrowLeftIcon className="h-3.5 w-3.5" />
          Back to replays
        </Link>

        <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl font-semibold tracking-tight text-slate-50">
                Replay <span className="font-mono text-lg text-slate-300">{shortId(session.id, 10)}</span>
              </h1>
              <ReplayStatusBadge status={session.status} />
              <span
                className={`badge ${
                  mode === "offline"
                    ? "border-violet-400/30 bg-violet-400/10 text-violet-300"
                    : "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                }`}
              >
                {mode}
              </span>
              <span
                className={`badge ${
                  comparison?.billed
                    ? "border-amber-400/30 bg-amber-400/10 text-amber-300"
                    : "border-slate-500/30 bg-slate-500/10 text-slate-400"
                }`}
              >
                {comparison ? (comparison.billed ? "provider billed" : "no provider call") : "billing unknown"}
              </span>
            </div>
            <p className="mt-1 text-sm text-slate-500">
              Created {formatDateTime(session.created_at)}
              {session.completed_at ? ` · finished ${formatDateTime(session.completed_at)}` : ""}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <RefreshButton />
            {active ? <CancelReplayButton sessionId={session.id} /> : null}
            <Link href={`/runs/${session.original_run_id}`} className="btn-ghost">
              <GitBranchIcon className="h-4 w-4" />
              Original run
            </Link>
            {session.replay_run_id ? (
              <Link href={`/runs/${session.replay_run_id}`} className="btn-ghost">
                <ReplayIcon className="h-4 w-4" />
                Replay run
              </Link>
            ) : null}
          </div>
        </div>
      </div>

      {failure ? (
        <div className="rounded-xl border border-rose-400/20 bg-rose-400/5 px-4 py-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-rose-300">Replay failed</p>
          <p className="mt-1 whitespace-pre-wrap text-sm text-rose-100">{failure}</p>
        </div>
      ) : null}

      {active ? (
        <div className="rounded-xl border border-sky-400/20 bg-sky-400/5 px-4 py-3 text-sm text-sky-200">
          The replay is queued or running. This page refreshes on demand; the comparison appears when
          the worker finishes.
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Output similarity"
          value={comparison ? formatScore(comparison.output.similarity) : "—"}
          hint={
            comparison
              ? comparison.output.identical
                ? "Byte-identical output"
                : `${comparison.output.original_chars.toLocaleString()} → ${comparison.output.replay_chars.toLocaleString()} chars`
              : "Waiting for the comparison"
          }
          icon={<GaugeIcon className="h-4 w-4" />}
          tone="violet"
        />
        <MetricCard
          label="Latency Δ"
          value={comparison ? formatSignedDuration(comparison.latency_ms.delta) : "—"}
          hint={
            comparison && comparison.latency_ms.original !== null
              ? `${formatDuration(comparison.latency_ms.original)} → ${formatDuration(comparison.latency_ms.replay)}`
              : "No latency recorded"
          }
          icon={<ReplayIcon className="h-4 w-4" />}
          tone="sky"
        />
        <MetricCard
          label="Tokens Δ"
          value={comparison ? formatSigned(comparison.tokens.delta) : "—"}
          hint={
            comparison
              ? `${comparison.tokens.original ?? "—"} → ${comparison.tokens.replay ?? "—"}`
              : "Waiting for the comparison"
          }
          icon={<TokensIcon className="h-4 w-4" />}
          tone="amber"
        />
        <MetricCard
          label="Cost Δ"
          value={comparison ? formatSignedCost(comparison.cost_usd.delta) : "—"}
          hint={
            comparison
              ? `${formatCost(comparison.cost_usd.original ?? 0)} → ${formatCost(comparison.cost_usd.replay ?? 0)}${
                  comparison.billed ? "" : " (hypothetical)"
                }`
              : "Waiting for the comparison"
          }
          icon={<CoinsIcon className="h-4 w-4" />}
          tone="accent"
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-4">
          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Prompts</h2>
              {comparison ? (
                <span
                  className={`badge ${
                    comparison.prompt_changed
                      ? "border-amber-400/30 bg-amber-400/10 text-amber-300"
                      : "border-slate-500/30 bg-slate-500/10 text-slate-400"
                  }`}
                >
                  {comparison.prompt_changed ? "prompt changed" : "same prompt"}
                </span>
              ) : null}
            </div>
            <div className="card-body grid gap-4 lg:grid-cols-2">
              <div>
                <p className="stat-label mb-1.5">Original</p>
                <p className="scrollbar-thin max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-white/5 bg-ink-950/40 p-3 text-xs leading-relaxed text-slate-300">
                  {session.original_run?.input_prompt ?? "Original run unavailable"}
                </p>
              </div>
              <div>
                <p className="stat-label mb-1.5">Replay</p>
                <p className="scrollbar-thin max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-white/5 bg-ink-950/40 p-3 text-xs leading-relaxed text-slate-300">
                  {session.replay_run?.input_prompt ?? "Replay run unavailable"}
                </p>
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Outputs</h2>
            </div>
            <div className="card-body grid gap-4 lg:grid-cols-2">
              <OutputColumn
                label="Original"
                status={session.original_run?.status ?? null}
                output={session.original_run?.output_response ?? null}
                error={session.original_run?.error_message ?? null}
              />
              <OutputColumn
                label="Replay"
                status={session.replay_run?.status ?? null}
                output={session.replay_run?.output_response ?? null}
                error={session.replay_run?.error_message ?? null}
              />
            </div>
          </section>

          {comparison ? (
            <section className="card">
              <div className="card-header">
                <h2 className="text-sm font-semibold text-slate-100">Evaluation deltas</h2>
                <span className="text-[11px] text-slate-600">
                  {Object.keys(comparison.evaluations.delta).length} comparable metric(s)
                </span>
              </div>
              {Object.keys(comparison.evaluations.delta).length === 0 ? (
                <div className="card-body text-xs text-slate-500">
                  Evaluate both runs with the same metric to see a delta here.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[480px] text-left text-xs">
                    <thead>
                      <tr className="border-b border-white/5 text-[10px] uppercase tracking-wider text-slate-500">
                        <th className="px-5 py-2.5 font-medium">Metric</th>
                        <th className="px-5 py-2.5 font-medium">Original</th>
                        <th className="px-5 py-2.5 font-medium">Replay</th>
                        <th className="px-5 py-2.5 font-medium">Δ</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                      {Object.entries(comparison.evaluations.delta).map(([metric, delta]) => (
                        <tr key={metric}>
                          <td className="px-5 py-2.5 font-mono text-slate-300">{metric}</td>
                          <td className="px-5 py-2.5 font-mono text-slate-400">
                            {formatScore(comparison.evaluations.original[metric])}
                          </td>
                          <td className="px-5 py-2.5 font-mono text-slate-400">
                            {formatScore(comparison.evaluations.replay[metric])}
                          </td>
                          <td
                            className={`px-5 py-2.5 font-mono ${
                              delta > 0
                                ? "text-emerald-300"
                                : delta < 0
                                  ? "text-rose-300"
                                  : "text-slate-400"
                            }`}
                          >
                            {delta > 0 ? "+" : ""}
                            {formatScore(delta)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {comparison.evaluation ? (
                <div className="border-t border-white/5 px-5 py-3">
                  <p className="stat-label mb-2">Post-replay evaluation</p>
                  {isEvaluationSnapshot(comparison.evaluation) ? (
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(comparison.evaluation.scores).map(([metric, score]) => (
                        <span
                          key={metric}
                          className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 font-mono text-[10px] text-slate-300"
                        >
                          {metric} · {formatScore(score)}
                        </span>
                      ))}
                      {comparison.evaluation.skipped.map((item) => (
                        <span
                          key={item.metric_name}
                          className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-2 py-1 font-mono text-[10px] text-amber-200"
                          title={item.reason}
                        >
                          {item.metric_name} · skipped
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-amber-300">{comparison.evaluation.error}</p>
                  )}
                </div>
              ) : null}
            </section>
          ) : null}
        </div>

        <aside className="space-y-4">
          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Runs</h2>
            </div>
            <div className="card-body space-y-4">
              <RunSummaryRow label="Original" run={session.original_run} />
              <RunSummaryRow label="Replay" run={session.replay_run} />
            </div>
          </section>

          {comparison ? (
            <>
              <section className="card">
                <div className="card-header">
                  <h2 className="text-sm font-semibold text-slate-100">Execution shape</h2>
                </div>
                <div className="card-body space-y-4">
                  <SpanStatsRow label="Spans" stats={comparison.spans.original} other={comparison.spans.replay} />
                  <ToolStatsRow stats={comparison.tool_calls.original} other={comparison.tool_calls.replay} />
                  <DeltaRow label="Latency" delta={comparison.latency_ms} kind="duration" />
                  <DeltaRow label="Tokens" delta={comparison.tokens} kind="number" />
                  <DeltaRow label="Cost" delta={comparison.cost_usd} kind="cost" />
                </div>
              </section>
            </>
          ) : null}

          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Overrides</h2>
            </div>
            <div className="card-body">
              <JsonBlock value={session.overrides} maxHeightClassName="max-h-64" />
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Timeline</h2>
            </div>
            <div className="card-body space-y-2.5">
              <TimelineRow label="Created" value={formatDateTime(session.created_at)} />
              <TimelineRow label="Completed" value={formatDateTime(session.completed_at)} />
              <TimelineRow
                label="Mode"
                value={mode === "offline" ? "Offline (recorded)" : "Live (provider call)"}
              />
            </div>
          </section>
        </aside>
      </div>

      {comparison ? (
        <section className="card">
          <div className="card-header">
            <h2 className="text-sm font-semibold text-slate-100">Raw comparison</h2>
            <span className="text-[11px] text-slate-600">stored on the replay session</span>
          </div>
          <div className="card-body">
            <JsonBlock value={comparison} maxHeightClassName="max-h-96" />
          </div>
        </section>
      ) : null}
    </div>
  );
}

function MetricCard({
  label,
  value,
  hint,
  icon,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  icon: ReactNode;
  tone: "accent" | "sky" | "amber" | "rose" | "violet";
}) {
  const toneClass = {
    accent: "bg-accent/10 text-accent",
    sky: "bg-sky-400/10 text-sky-300",
    amber: "bg-amber-400/10 text-amber-300",
    rose: "bg-rose-400/10 text-rose-300",
    violet: "bg-violet-400/10 text-violet-300",
  }[tone];

  return (
    <div className="card p-5">
      <div className="flex items-start justify-between gap-2">
        <p className="stat-label">{label}</p>
        <span className={`grid h-8 w-8 place-items-center rounded-lg ${toneClass}`}>{icon}</span>
      </div>
      <p className="mt-3 text-2xl font-semibold tracking-tight text-slate-50">{value}</p>
      <p className="mt-1.5 truncate text-xs text-slate-500" title={hint}>
        {hint}
      </p>
    </div>
  );
}

function OutputColumn({
  label,
  status,
  output,
  error,
}: {
  label: string;
  status: RunStatus | null;
  output: string | null;
  error: string | null;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <p className="stat-label">{label}</p>
        {status ? <RunStatusBadge status={status} /> : null}
      </div>
      {error ? (
        <div className="rounded-lg border border-rose-400/20 bg-rose-400/5 p-3 text-xs text-rose-200">
          <AlertTriangleIcon className="mb-1 h-3.5 w-3.5" />
          {error}
        </div>
      ) : output ? (
        <p className="scrollbar-thin max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-white/5 bg-ink-950/40 p-3 text-xs leading-relaxed text-slate-300">
          {output}
        </p>
      ) : (
        <p className="rounded-lg border border-white/5 bg-ink-950/40 p-3 text-xs text-slate-600">
          No output recorded.
        </p>
      )}
    </div>
  );
}

function RunSummaryRow({
  label,
  run,
}: {
  label: string;
  run: ReplaySession["original_run"];
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <p className="stat-label">{label}</p>
        {run ? <RunStatusBadge status={run.status} /> : null}
      </div>
      {run ? (
        <div className="space-y-1 text-xs text-slate-400">
          <Link href={`/runs/${run.id}`} className="font-mono text-slate-300 hover:text-accent">
            {shortId(run.id, 10)}
          </Link>
          <p>
            {run.total_tokens_used.toLocaleString()} tokens · {formatCost(run.total_cost_usd)} ·{" "}
            {formatDuration(run.latency_ms)}
          </p>
          <p className="text-slate-600">{formatDateTime(run.completed_at ?? run.created_at)}</p>
        </div>
      ) : (
        <p className="text-xs text-slate-600">Run unavailable</p>
      )}
    </div>
  );
}

function SpanStatsRow({
  label,
  stats,
  other,
}: {
  label: string;
  stats: SpanStats;
  other: SpanStats;
}) {
  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="font-mono text-slate-300">
        {stats.total}
        {stats.errors > 0 ? <span className="text-rose-300"> ({stats.errors} err)</span> : null}
        <span className="mx-1 text-slate-600">→</span>
        {other.total}
        {other.errors > 0 ? <span className="text-rose-300"> ({other.errors} err)</span> : null}
      </span>
    </div>
  );
}

function ToolStatsRow({ stats, other }: { stats: ToolCallStats; other: ToolCallStats }) {
  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="text-slate-500">Tool calls</span>
      <span className="font-mono text-slate-300">
        {stats.calls}
        {stats.failed > 0 ? <span className="text-rose-300"> ({stats.failed} failed)</span> : null}
        <span className="mx-1 text-slate-600">→</span>
        {other.calls}
        {other.failed > 0 ? <span className="text-rose-300"> ({other.failed} failed)</span> : null}
      </span>
    </div>
  );
}

function DeltaRow({
  label,
  delta,
  kind,
}: {
  label: string;
  delta: ComparisonDelta;
  kind: "number" | "duration" | "cost";
}) {
  const percentage =
    delta.ratio === null ? null : `${((delta.ratio - 1) * 100).toFixed(0)}%`;
  const formatted =
    kind === "cost"
      ? formatSignedCost(delta.delta)
      : kind === "duration"
        ? formatSignedDuration(delta.delta)
        : formatSigned(delta.delta);
  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="font-mono text-slate-300">
        {formatted}
        {percentage ? <span className="ml-1.5 text-[10px] text-slate-600">{percentage}</span> : null}
      </span>
    </div>
  );
}

function TimelineRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="text-right font-mono text-[11px] text-slate-300">{value}</span>
    </div>
  );
}
