"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { CopyButton } from "@/components/CopyButton";
import { JsonBlock } from "@/components/JsonBlock";
import { SpanStatusBadge } from "@/components/StatusBadge";
import {
  AlertTriangleIcon,
  BrainIcon,
  ChevronRightIcon,
  DatabaseIcon,
  ExternalLinkIcon,
  GitBranchIcon,
  RefreshIcon,
  WrenchIcon,
} from "@/components/icons";
import { getRun, runStreamUrl } from "@/lib/client";
import { PUBLIC_JAEGER_URL } from "@/lib/env";
import { toErrorMessage } from "@/lib/errors";
import {
  SPAN_TYPE_META,
  formatCost,
  formatDateTime,
  formatDuration,
  formatNumber,
  percent,
  shortId,
} from "@/lib/format";
import {
  buildTraceTree,
  flattenTraceTree,
  mergeTraceEvent,
  sumTokens,
  traceWindow,
} from "@/lib/traces";
import type {
  RunDetail,
  RunEventEnvelope,
  RunSnapshot,
  RunStatus,
  SpanType,
  Trace,
  TraceEvent,
} from "@/types";

type Connection = "connecting" | "live" | "reconnecting" | "complete" | "unavailable" | "error";

const CONNECTION_META: Record<Connection, { label: string; className: string; dot: string }> = {
  connecting: { label: "Connecting", className: "text-slate-400", dot: "bg-slate-400 animate-pulse-soft" },
  live: { label: "Live", className: "text-emerald-300", dot: "bg-emerald-400 animate-pulse-soft" },
  reconnecting: { label: "Reconnecting", className: "text-amber-300", dot: "bg-amber-400 animate-pulse-soft" },
  complete: { label: "Run finished", className: "text-slate-400", dot: "bg-slate-500" },
  unavailable: { label: "Live updates off", className: "text-amber-300", dot: "bg-amber-400" },
  error: { label: "Stream error", className: "text-rose-300", dot: "bg-rose-400" },
};

const TERMINAL_STATUSES: RunStatus[] = ["completed", "failed", "replayed"];

const SPAN_ICONS: Record<SpanType, typeof BrainIcon> = {
  llm_call: BrainIcon,
  tool_call: WrenchIcon,
  data_access: DatabaseIcon,
  decision: GitBranchIcon,
  error: AlertTriangleIcon,
};

function isTerminal(status: RunStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

function parseJson<T>(event: MessageEvent<string>): T | null {
  try {
    return JSON.parse(event.data) as T;
  } catch {
    return null;
  }
}

interface TraceWaterfallProps {
  initialRun: RunDetail;
}

export function TraceWaterfall({ initialRun }: TraceWaterfallProps) {
  const [run, setRun] = useState<RunDetail>(initialRun);
  const [traces, setTraces] = useState<Trace[]>(initialRun.traces);
  const [connection, setConnection] = useState<Connection>(
    isTerminal(initialRun.status) ? "complete" : "connecting",
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [iteration, setIteration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const initialStatus = useRef(initialRun.status);

  const refresh = useCallback(async (): Promise<void> => {
    setIsRefreshing(true);
    try {
      const fresh = await getRun(run.id);
      setRun(fresh);
      setTraces(fresh.traces);
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    } finally {
      setIsRefreshing(false);
    }
  }, [run.id]);

  const scheduleRefresh = useCallback((): void => {
    if (refreshTimer.current) {
      clearTimeout(refreshTimer.current);
    }
    refreshTimer.current = setTimeout(() => {
      void refresh();
    }, 600);
  }, [refresh]);

  useEffect(() => {
    if (isTerminal(initialStatus.current)) {
      return;
    }

    const source = new EventSource(runStreamUrl(initialRun.id));

    const onSnapshot = (event: MessageEvent<string>): void => {
      const snapshot = parseJson<RunSnapshot>(event);
      if (!snapshot) {
        return;
      }
      setRun((current) => ({ ...current, ...snapshot.run }));
      setTraces(snapshot.traces);
      if (isTerminal(snapshot.run.status)) {
        setConnection("complete");
        source.close();
      } else {
        setConnection("live");
      }
    };

    const onStarted = (event: MessageEvent<string>): void => {
      const payload = parseJson<RunEventEnvelope<{ status: RunStatus }>>(event);
      if (!payload) {
        return;
      }
      setRun((current) => ({ ...current, status: payload.data.status }));
      setConnection("live");
    };

    const onTrace = (event: MessageEvent<string>): void => {
      const payload = parseJson<RunEventEnvelope<{ trace: TraceEvent }>>(event);
      if (!payload?.data?.trace) {
        return;
      }
      setTraces((current) => mergeTraceEvent(current, payload.data.trace));
      scheduleRefresh();
    };

    const onProgress = (event: MessageEvent<string>): void => {
      const payload = parseJson<RunEventEnvelope<{ iteration?: number }>>(event);
      if (typeof payload?.data?.iteration === "number") {
        setIteration(payload.data.iteration);
      }
      setConnection("live");
    };

    const onCompleted = (event: MessageEvent<string>): void => {
      const payload = parseJson<
        RunEventEnvelope<{
          status: RunStatus;
          total_tokens_used: number;
          total_cost_usd: number;
          latency_ms: number | null;
          output_preview?: string;
        }>
      >(event);
      if (payload) {
        setRun((current) => ({
          ...current,
          status: payload.data.status,
          total_tokens_used: payload.data.total_tokens_used,
          total_cost_usd: payload.data.total_cost_usd,
          latency_ms: payload.data.latency_ms,
        }));
      }
      setConnection("complete");
      source.close();
      scheduleRefresh();
    };

    const onFailed = (event: MessageEvent<string>): void => {
      const payload = parseJson<
        RunEventEnvelope<{ status: RunStatus; error_message?: string; latency_ms?: number | null }>
      >(event);
      if (payload) {
        setRun((current) => ({
          ...current,
          status: payload.data.status,
          error_message: payload.data.error_message ?? current.error_message,
          latency_ms: payload.data.latency_ms ?? current.latency_ms,
        }));
      }
      setConnection("complete");
      source.close();
      scheduleRefresh();
    };

    const onUnavailable = (): void => {
      setConnection("unavailable");
      source.close();
    };

    const onStreamError = (): void => {
      setConnection("error");
      source.close();
    };

    source.addEventListener("run.snapshot", onSnapshot as EventListener);
    source.addEventListener("run.started", onStarted as EventListener);
    source.addEventListener("run.progress", onProgress as EventListener);
    source.addEventListener("trace.completed", onTrace as EventListener);
    source.addEventListener("run.completed", onCompleted as EventListener);
    source.addEventListener("run.failed", onFailed as EventListener);
    source.addEventListener("stream.unavailable", onUnavailable);
    source.addEventListener("stream.error", onStreamError);
    source.onopen = () => setConnection("live");
    source.onerror = () => {
      if (source.readyState !== EventSource.CLOSED) {
        setConnection("reconnecting");
      }
    };

    return () => {
      source.close();
      if (refreshTimer.current) {
        clearTimeout(refreshTimer.current);
      }
    };
  }, [initialRun.id, scheduleRefresh]);

  const tree = useMemo(() => buildTraceTree(traces), [traces]);
  const flat = useMemo(() => flattenTraceTree(tree), [tree]);
  const window = useMemo(() => traceWindow(traces), [traces]);
  const tokens = useMemo(() => sumTokens(traces), [traces]);
  const cost = useMemo(() => traces.reduce((sum, trace) => sum + trace.cost_usd, 0), [traces]);
  const connectionMeta = CONNECTION_META[connection];

  return (
    <section className="card overflow-hidden">
      <div className="card-header">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">Trace waterfall</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            {flat.length} spans · {formatDuration(window.duration)} wall clock ·{" "}
            {formatNumber(tokens.total)} tokens · {formatCost(cost)}
            {iteration > 0 ? ` · iteration ${iteration}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className={`inline-flex items-center gap-1.5 text-[11px] ${connectionMeta.className}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${connectionMeta.dot}`} />
            {connectionMeta.label}
          </span>
          <button
            type="button"
            className="btn-ghost px-2.5 py-1.5 text-xs"
            onClick={() => void refresh()}
            disabled={isRefreshing}
          >
            <RefreshIcon className={`h-3.5 w-3.5 ${isRefreshing ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      {error ? (
        <p className="border-b border-rose-400/20 bg-rose-400/5 px-5 py-2 text-xs text-rose-200">
          {error}
        </p>
      ) : null}

      {flat.length === 0 ? (
        <div className="px-5 py-12 text-center text-sm text-slate-500">
          {run.status === "pending" || run.status === "running"
            ? "Waiting for the first span…"
            : "No spans were recorded for this run."}
        </div>
      ) : (
        <div>
          <div className="hidden grid-cols-[minmax(0,22rem)_minmax(0,1fr)] gap-4 border-b border-white/5 px-5 py-2 md:grid">
            <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-600">
              Span
            </span>
            <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-600">
              Timeline · {formatDuration(window.duration)}
            </span>
          </div>

          <div className="divide-y divide-white/5">
            {flat.map(({ trace, depth }) => {
              const typeMeta = SPAN_TYPE_META[trace.span_type];
              const Icon = SPAN_ICONS[trace.span_type];
              const start = Date.parse(trace.timestamp);
              const offset = Number.isNaN(start)
                ? 0
                : percent(start - window.start, window.duration);
              const width = Math.max(percent(trace.duration_ms, window.duration), 0.8);
              const isSelected = selectedId === trace.id;

              return (
                <div key={trace.id} className={isSelected ? "bg-white/[0.02]" : undefined}>
                  <button
                    type="button"
                    className="grid w-full grid-cols-1 items-center gap-1 px-5 py-2 text-left transition-colors hover:bg-white/[0.03] md:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] md:gap-4"
                    onClick={() => setSelectedId(isSelected ? null : trace.id)}
                    aria-expanded={isSelected}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <ChevronRightIcon
                        className={`h-3.5 w-3.5 shrink-0 text-slate-600 transition-transform ${isSelected ? "rotate-90" : ""}`}
                        style={{ marginLeft: depth * 14 }}
                      />
                      <Icon className={`h-3.5 w-3.5 shrink-0 ${typeMeta.iconClassName}`} />
                      <span className="truncate text-sm text-slate-300">{trace.span_name}</span>
                      {trace.tool_calls.length > 0 ? (
                        <span className="shrink-0 rounded bg-sky-400/10 px-1.5 py-0.5 text-[10px] text-sky-300">
                          {trace.tool_calls.length} tool
                        </span>
                      ) : null}
                    </span>

                    <span className="flex items-center gap-3 pl-8 md:pl-0">
                      <span className="relative hidden h-5 flex-1 rounded bg-white/[0.03] md:block">
                        <span
                          className={`absolute top-1/2 h-2 -translate-y-1/2 rounded-full ${typeMeta.barClassName}`}
                          style={{
                            left: `${Math.min(offset, 99.2)}%`,
                            width: `${width}%`,
                            minWidth: "3px",
                          }}
                          title={`${trace.span_name} · ${formatDuration(trace.duration_ms)}`}
                        />
                      </span>
                      <span className="w-16 shrink-0 text-right font-mono text-[11px] text-slate-500">
                        {formatDuration(trace.duration_ms)}
                      </span>
                      <SpanStatusBadge status={trace.status} />
                    </span>
                  </button>

                  {isSelected ? <SpanDetail trace={trace} /> : null}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}

function SpanDetail({ trace }: { trace: Trace }) {
  const typeMeta = SPAN_TYPE_META[trace.span_type];

  return (
    <div className="border-t border-white/5 bg-ink-950/50 px-5 py-4">
      <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <Detail label="Type" value={typeMeta.label} />
        <Detail label="Status" value={<SpanStatusBadge status={trace.status} />} />
        <Detail label="Duration" value={formatDuration(trace.duration_ms)} />
        <Detail
          label="Tokens"
          value={`${formatNumber(trace.tokens_input)} in / ${formatNumber(trace.tokens_output)} out`}
        />
        <Detail label="Cost" value={formatCost(trace.cost_usd)} />
        <Detail label="Model" value={trace.model_name ?? "—"} />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Input
          </p>
          <JsonBlock value={trace.input_data} maxHeightClassName="max-h-64" />
        </div>
        <div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Output
          </p>
          <JsonBlock value={trace.output_data} maxHeightClassName="max-h-64" />
        </div>
      </div>

      {trace.error_details ? (
        <div className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/5 p-3">
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-rose-300">
            Error details
          </p>
          <JsonBlock value={trace.error_details} maxHeightClassName="max-h-40" />
        </div>
      ) : null}

      {trace.tool_calls.length > 0 ? (
        <div className="mt-4 space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Tool calls
          </p>
          {trace.tool_calls.map((call) => (
            <div key={call.id} className="rounded-lg border border-white/5 bg-ink-900/60 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-sm text-slate-200">
                  <WrenchIcon className="h-3.5 w-3.5 text-sky-300" />
                  {call.tool_name}
                </span>
                <span className="flex items-center gap-3 text-[11px]">
                  <span className="font-mono text-slate-500">{formatDuration(call.duration_ms)}</span>
                  <span
                    className={`badge ${
                      call.success
                        ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                        : "border-rose-400/30 bg-rose-400/10 text-rose-300"
                    }`}
                  >
                    {call.success ? "success" : "failed"}
                  </span>
                </span>
              </div>
              <div className="mt-2 grid gap-2 lg:grid-cols-2">
                <JsonBlock value={call.tool_input} emptyLabel="No input" maxHeightClassName="max-h-40" />
                <JsonBlock value={call.tool_output} emptyLabel="No output" maxHeightClassName="max-h-40" />
              </div>
              {call.error_message ? (
                <p className="mt-2 text-xs text-rose-300">{call.error_message}</p>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-end gap-x-6 gap-y-2 border-t border-white/5 pt-3">
        <IdLine label="Trace ID" value={trace.trace_id} />
        <IdLine label="Span ID" value={trace.span_id} />
        <IdLine label="Row ID" value={trace.id} />
        <span className="text-[11px] text-slate-500">
          Started {formatDateTime(trace.timestamp)}
        </span>
        <Link
          href={`${PUBLIC_JAEGER_URL}/trace/${trace.trace_id}`}
          target="_blank"
          rel="noreferrer"
          className="ml-auto inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
        >
          Open in Jaeger
          <ExternalLinkIcon className="h-3 w-3" />
        </Link>
      </div>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <p className="stat-label">{label}</p>
      <p className="mt-1 truncate text-xs text-slate-300">{value}</p>
    </div>
  );
}

function IdLine({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wider text-slate-600">{label}</span>
      <code className="font-mono text-[11px] text-slate-400">{shortId(value, 12)}</code>
      <CopyButton value={value} label="" />
    </span>
  );
}
