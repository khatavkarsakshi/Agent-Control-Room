import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { CancelRunButton } from "@/components/CancelRunButton";
import { CopyButton } from "@/components/CopyButton";
import { EvaluationPanel } from "@/components/EvaluationPanel";
import { JsonBlock } from "@/components/JsonBlock";
import { RefreshButton } from "@/components/RefreshButton";
import { ReplayPanel } from "@/components/ReplayPanel";
import { ConnectionError } from "@/components/States";
import { RunStatusBadge } from "@/components/StatusBadge";
import { TraceWaterfall } from "@/components/TraceWaterfall";
import {
  ActivityIcon,
  ArrowLeftIcon,
  CheckCircleIcon,
  ClockIcon,
  CoinsIcon,
  DownloadIcon,
  ExternalLinkIcon,
  TokensIcon,
} from "@/components/icons";
import {
  getAgent,
  getEvaluationCatalog,
  getRun,
  getRunExportUrl,
  listReplaySessions,
} from "@/lib/api";
import { PUBLIC_API_BASE_URL, PUBLIC_JAEGER_URL } from "@/lib/env";
import { isNotFound, toErrorMessage } from "@/lib/errors";
import {
  formatCost,
  formatDateTime,
  formatDuration,
  formatNumber,
  percent,
  shortId,
} from "@/lib/format";
import type { Agent, EvaluationCatalog, ReplaySession, RunDetail } from "@/types";

export const dynamic = "force-dynamic";

interface RunPageProps {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: RunPageProps): Promise<Metadata> {
  const { id } = await params;
  return { title: `Run ${shortId(id, 10)}` };
}

export default async function RunDetailPage({ params }: RunPageProps) {
  const { id } = await params;

  let run: RunDetail;
  try {
    run = await getRun(id);
  } catch (error) {
    if (isNotFound(error)) {
      notFound();
    }
    return (
      <ConnectionError
        title="Could not load this run"
        message={toErrorMessage(error)}
        apiBaseUrl={PUBLIC_API_BASE_URL}
      />
    );
  }

  let agent: Agent | null = null;
  try {
    agent = await getAgent(run.agent_id);
  } catch {
    // The agent may have been hard-deleted after the run; the run is still valid.
    agent = null;
  }

  // Phase 4 panels are supplementary: a catalog/replay outage must not hide the
  // run itself, so both fetches degrade to empty state.
  let catalog: EvaluationCatalog | null = null;
  try {
    catalog = await getEvaluationCatalog();
  } catch {
    catalog = null;
  }

  let replaySessions: ReplaySession[] = [];
  try {
    replaySessions = (
      await listReplaySessions({ original_run_id: run.id, limit: 10 })
    ).items;
  } catch {
    replaySessions = [];
  }
  const customMetrics =
    catalog?.evaluators.find((entry) => entry.evaluator_type === "custom")?.metrics ?? [];

  const metadataTraceId =
    typeof run.metadata.trace_id === "string" ? run.metadata.trace_id : null;
  const traceId = metadataTraceId ?? run.traces[0]?.trace_id ?? null;
  const totalTokens = run.traces.reduce((sum, trace) => sum + trace.tokens_used, 0);
  const spanCost = run.traces.reduce((sum, trace) => sum + trace.cost_usd, 0);
  const isActive = run.status === "pending" || run.status === "running";
  const errorSpans = run.traces.filter((trace) => trace.status !== "ok").length;
  const successRate = run.traces.length > 0 ? percent(run.traces.length - errorSpans, run.traces.length) : null;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/runs" className="inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300">
          <ArrowLeftIcon className="h-3.5 w-3.5" />
          Back to runs
        </Link>

        <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl font-semibold tracking-tight text-slate-50">
                Run <span className="font-mono text-lg text-slate-300">{shortId(run.id, 10)}</span>
              </h1>
              <RunStatusBadge status={run.status} />
              {run.replay_of_run_id ? (
                <span className="badge border-violet-400/30 bg-violet-400/10 text-violet-300">
                  replay of {shortId(run.replay_of_run_id)}
                </span>
              ) : null}
            </div>
            <p className="mt-1 text-sm text-slate-500">
              {agent ? (
                <>
                  <Link href={`/agents#${agent.id}`} className="text-slate-300 hover:text-accent">
                    {agent.name}
                  </Link>{" "}
                  ·{" "}
                </>
              ) : (
                <>Agent {shortId(run.agent_id)} · </>
              )}
              {run.traces[0]?.model_name ?? agent?.model_name ?? "model unknown"} · created{" "}
              {formatDateTime(run.created_at)}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <RefreshButton />
            {isActive ? <CancelRunButton runId={run.id} /> : null}
            {traceId ? (
              <a
                href={`${PUBLIC_JAEGER_URL}/trace/${traceId}`}
                target="_blank"
                rel="noreferrer"
                className="btn-ghost"
              >
                <ExternalLinkIcon className="h-4 w-4" />
                Jaeger
              </a>
            ) : null}
            <a href={getRunExportUrl(run.id)} className="btn-ghost">
              <DownloadIcon className="h-4 w-4" />
              Export JSON
            </a>
          </div>
        </div>
      </div>

      {run.error_message ? (
        <div className="rounded-xl border border-rose-400/20 bg-rose-400/5 px-4 py-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-rose-300">Run failed</p>
          <p className="mt-1 whitespace-pre-wrap text-sm text-rose-100">{run.error_message}</p>
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          label="Total cost"
          value={formatCost(run.total_cost_usd || spanCost)}
          hint={`${formatCost(spanCost)} attributed to spans`}
          icon={<CoinsIcon className="h-4 w-4" />}
          tone="accent"
        />
        <SummaryCard
          label="Tokens"
          value={formatNumber(run.total_tokens_used || totalTokens)}
          hint={`${formatNumber(run.traces.reduce((sum, trace) => sum + trace.tokens_input, 0))} in / ${formatNumber(
            run.traces.reduce((sum, trace) => sum + trace.tokens_output, 0),
          )} out`}
          icon={<TokensIcon className="h-4 w-4" />}
          tone="sky"
        />
        <SummaryCard
          label="Latency"
          value={formatDuration(run.latency_ms)}
          hint={
            run.started_at
              ? `Started ${formatDateTime(run.started_at)}`
              : "Not started yet"
          }
          icon={<ClockIcon className="h-4 w-4" />}
          tone="amber"
        />
        <SummaryCard
          label="Spans"
          value={formatNumber(run.traces.length)}
          hint={
            successRate === null
              ? "No spans recorded"
              : `${formatNumber(errorSpans)} with errors · ${successRate.toFixed(0)}% healthy`
          }
          icon={<ActivityIcon className="h-4 w-4" />}
          tone={errorSpans > 0 ? "rose" : "violet"}
        />
      </div>

      <TraceWaterfall initialRun={run} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-4">
          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Prompt</h2>
              <CopyButton value={run.input_prompt} />
            </div>
            <div className="card-body">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
                {run.input_prompt}
              </p>
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Output</h2>
              {run.output_response ? <CopyButton value={run.output_response} /> : null}
            </div>
            <div className="card-body">
              {run.output_response ? (
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
                  {run.output_response}
                </p>
              ) : (
                <p className="text-sm text-slate-500">
                  {isActive
                    ? "The agent is still working. The final answer appears here when the run completes."
                    : "No output was recorded for this run."}
                </p>
              )}
            </div>
          </section>

          <EvaluationPanel
            runId={run.id}
            initialEvaluations={run.evaluations}
            catalog={catalog}
            canEvaluate={Boolean(run.output_response)}
          />

          <ReplayPanel run={run} sessions={replaySessions} metricOptions={customMetrics} />

          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Run metadata</h2>
              <span className="text-[11px] text-slate-600">
                {Object.keys(run.metadata).length} keys
              </span>
            </div>
            <div className="card-body">
              <JsonBlock value={run.metadata} maxHeightClassName="max-h-64" />
            </div>
          </section>
        </div>

        <aside className="space-y-4">
          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Agent</h2>
              {agent ? (
                <Link href={`/runs?agent_id=${agent.id}`} className="text-xs text-accent hover:underline">
                  View runs
                </Link>
              ) : null}
            </div>
            <div className="card-body space-y-3">
              {agent ? (
                <>
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="text-sm font-medium text-slate-200">{agent.name}</p>
                      <p className="font-mono text-[11px] text-slate-500">{agent.model_name}</p>
                    </div>
                    <span
                      className={`badge ${
                        agent.is_active
                          ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                          : "border-slate-500/30 bg-slate-500/10 text-slate-400"
                      }`}
                    >
                      {agent.is_active ? "active" : "inactive"}
                    </span>
                  </div>
                  {agent.description ? (
                    <p className="text-xs leading-relaxed text-slate-500">{agent.description}</p>
                  ) : null}
                  <div>
                    <p className="stat-label mb-1.5">Tools</p>
                    {agent.tools_config.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {agent.tools_config.map((tool) => (
                          <span
                            key={tool.name}
                            className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
                          >
                            {tool.name}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-slate-600">No tools configured</p>
                    )}
                  </div>
                  {agent.system_prompt ? (
                    <details className="rounded-lg border border-white/5 bg-ink-950/40 px-3 py-2">
                      <summary className="cursor-pointer select-none text-xs text-slate-400 hover:text-slate-200">
                        System prompt
                      </summary>
                      <p className="scrollbar-thin mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed text-slate-500">
                        {agent.system_prompt}
                      </p>
                    </details>
                  ) : null}
                </>
              ) : (
                <p className="text-xs text-slate-500">
                  The agent for this run is no longer available (deleted). The run record is
                  preserved.
                </p>
              )}
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Timeline</h2>
            </div>
            <div className="card-body space-y-3">
              <TimelineRow label="Created" value={formatDateTime(run.created_at)} />
              <TimelineRow label="Started" value={formatDateTime(run.started_at)} />
              <TimelineRow label="Completed" value={formatDateTime(run.completed_at)} />
              <TimelineRow
                label="Total latency"
                value={formatDuration(run.latency_ms)}
                icon={<CheckCircleIcon className="h-3.5 w-3.5 text-emerald-300" />}
              />
              {traceId ? (
                <div className="border-t border-white/5 pt-3">
                  <p className="stat-label mb-1">OpenTelemetry trace</p>
                  <div className="flex items-center gap-1.5">
                    <code className="truncate font-mono text-[11px] text-slate-400">{traceId}</code>
                    <CopyButton value={traceId} label="" />
                  </div>
                </div>
              ) : null}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

function SummaryCard({
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

function TimelineRow({ label, value, icon }: { label: string; value: string; icon?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="flex items-center gap-1.5 text-right font-mono text-[11px] text-slate-300">
        {icon}
        {value}
      </span>
    </div>
  );
}
