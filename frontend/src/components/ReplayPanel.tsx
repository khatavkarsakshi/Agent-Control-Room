"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { PlayIcon, ReplayIcon } from "@/components/icons";
import { ReplayStatusBadge } from "@/components/StatusBadge";
import { startReplay } from "@/lib/client";
import { toErrorMessage } from "@/lib/errors";
import { formatScore, formatDateTime, shortId } from "@/lib/format";
import type {
  MetricDefinition,
  ReplayCreateRequest,
  ReplayMode,
  ReplayOverrides,
  ReplaySession,
  RunDetail,
} from "@/types";

interface ReplayPanelProps {
  run: RunDetail;
  sessions: ReplaySession[];
  metricOptions: MetricDefinition[];
}

function optionalNumber(data: FormData, key: string): number | undefined {
  const raw = data.get(key);
  if (typeof raw !== "string" || raw.trim() === "") {
    return undefined;
  }
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function similarityOf(session: ReplaySession): number | null {
  const comparison = session.comparison_result;
  if (!comparison || !("output" in comparison)) {
    return null;
  }
  return comparison.output.similarity;
}

export function ReplayPanel({ run, sessions, metricOptions }: ReplayPanelProps) {
  const router = useRouter();
  const terminal = run.status === "completed" || run.status === "failed" || run.status === "replayed";
  const hasRecordedCalls = run.traces.some((trace) => trace.span_type === "llm_call");
  const [mode, setMode] = useState<ReplayMode>(hasRecordedCalls ? "offline" : "live");
  const [prompt, setPrompt] = useState(run.input_prompt);
  const [evaluate, setEvaluate] = useState(false);
  const [selectedMetrics, setSelectedMetrics] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleMetric = (name: string): void => {
    setSelectedMetrics((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name],
    );
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setError(null);
    const form = event.currentTarget;
    const data = new FormData(form);

    const overrides: ReplayOverrides = {};
    if (prompt.trim() && prompt.trim() !== run.input_prompt) {
      overrides.input_prompt = prompt.trim();
    }
    const modelName = String(data.get("model_name") ?? "").trim();
    if (modelName) {
      overrides.model_name = modelName;
    }
    const systemPrompt = String(data.get("system_prompt") ?? "").trim();
    if (systemPrompt) {
      overrides.system_prompt = systemPrompt;
    }
    const tools = String(data.get("tools") ?? "")
      .split(",")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
    if (tools.length > 0) {
      overrides.tools = tools;
    }
    const temperature = optionalNumber(data, "temperature");
    if (temperature !== undefined) {
      overrides.temperature = temperature;
    }
    const maxIterations = optionalNumber(data, "max_iterations");
    if (maxIterations !== undefined) {
      overrides.max_iterations = maxIterations;
    }

    const payload: ReplayCreateRequest = {
      mode,
      ...(Object.keys(overrides).length > 0 ? { overrides } : {}),
      ...(evaluate ? { evaluate: true } : {}),
      ...(evaluate && selectedMetrics.length > 0 ? { metrics: selectedMetrics } : {}),
    };

    setIsSubmitting(true);
    try {
      const created = await startReplay(run.id, payload);
      router.push(`/replays/${created.session.id}`);
    } catch (cause) {
      setError(toErrorMessage(cause));
      setIsSubmitting(false);
    }
  };

  return (
    <section id="replay" className="card">
      <div className="card-header">
        <div className="flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-violet-400/10 text-violet-300">
            <ReplayIcon className="h-4 w-4" />
          </span>
          <div>
            <h2 className="text-sm font-semibold text-slate-100">Replay this run</h2>
            <p className="text-xs text-slate-500">
              Re-execute with what-if overrides and compare the two outcomes.
            </p>
          </div>
        </div>
        <Link href={`/replays?original_run_id=${run.id}`} className="text-xs text-accent hover:underline">
          All sessions
        </Link>
      </div>

      {sessions.length > 0 ? (
        <div className="divide-y divide-white/5 border-b border-white/5">
          {sessions.map((session) => (
            <Link
              key={session.id}
              href={`/replays/${session.id}`}
              className="flex items-center justify-between gap-3 px-5 py-2.5 text-xs hover:bg-white/[0.02]"
            >
              <span className="flex min-w-0 items-center gap-2">
                <ReplayStatusBadge status={session.status} />
                <span className="font-mono text-slate-400">{shortId(session.id)}</span>
                <span className="text-slate-600">
                  {String(session.overrides.mode ?? "live")} · {formatDateTime(session.created_at)}
                </span>
              </span>
              <span className="shrink-0 font-mono text-slate-300">
                {similarityOf(session) === null ? "—" : formatScore(similarityOf(session))}
              </span>
            </Link>
          ))}
        </div>
      ) : null}

      <form onSubmit={(event) => void onSubmit(event)} className="card-body space-y-4">
        {!terminal ? (
          <p className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs text-amber-200">
            Replays become available once this run reaches a terminal state.
          </p>
        ) : null}

        <div className="grid gap-2 sm:grid-cols-2">
          <label
            className={`cursor-pointer rounded-lg border px-3 py-2 text-xs ${
              mode === "offline"
                ? "border-violet-400/40 bg-violet-400/10 text-violet-200"
                : "border-white/10 bg-white/5 text-slate-400"
            }`}
          >
            <input
              type="radio"
              name="replay_mode"
              className="sr-only"
              checked={mode === "offline"}
              onChange={() => setMode("offline")}
              disabled={!terminal || !hasRecordedCalls}
            />
            <span className="block font-semibold">Offline</span>
            <span className="block text-[11px] opacity-80">
              Reuse recorded LLM responses. No provider call, no spend; cost is hypothetical.
            </span>
            {!hasRecordedCalls ? (
              <span className="mt-1 block text-[11px] text-amber-300">
                No recorded LLM calls in this trace.
              </span>
            ) : null}
          </label>
          <label
            className={`cursor-pointer rounded-lg border px-3 py-2 text-xs ${
              mode === "live"
                ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-200"
                : "border-white/10 bg-white/5 text-slate-400"
            }`}
          >
            <input
              type="radio"
              name="replay_mode"
              className="sr-only"
              checked={mode === "live"}
              onChange={() => setMode("live")}
              disabled={!terminal}
            />
            <span className="block font-semibold">Live</span>
            <span className="block text-[11px] opacity-80">
              Call the provider again with overrides. Tokens are billed.
            </span>
          </label>
        </div>

        <div>
          <label htmlFor="replay-prompt" className="label">
            Prompt override
          </label>
          <textarea
            id="replay-prompt"
            className="input min-h-[88px] resize-y"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            maxLength={20_000}
            disabled={!terminal || isSubmitting}
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="replay-model" className="label">
              Model override
            </label>
            <input
              id="replay-model"
              name="model_name"
              className="input"
              placeholder="Use the original model"
              maxLength={120}
              disabled={!terminal || isSubmitting}
            />
          </div>
          <div>
            <label htmlFor="replay-tools" className="label">
              Tools override (comma separated)
            </label>
            <input
              id="replay-tools"
              name="tools"
              className="input"
              placeholder="calculator, current_time"
              disabled={!terminal || isSubmitting}
            />
          </div>
        </div>

        <div>
          <label htmlFor="replay-system" className="label">
            System prompt override
          </label>
          <textarea
            id="replay-system"
            name="system_prompt"
            className="input min-h-[64px] resize-y"
            placeholder="Leave empty to reuse the agent's system prompt"
            maxLength={20_000}
            disabled={!terminal || isSubmitting}
          />
        </div>

        <details className="rounded-lg border border-white/5 bg-ink-950/40">
          <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium text-slate-400 hover:text-slate-200">
            Execution overrides
          </summary>
          <div className="grid gap-3 border-t border-white/5 px-3 py-3 sm:grid-cols-2">
            <div>
              <label htmlFor="replay-temperature" className="label">
                Temperature
              </label>
              <input
                id="replay-temperature"
                name="temperature"
                type="number"
                min="0"
                max="2"
                step="0.1"
                className="input"
                placeholder="Original"
                disabled={!terminal || isSubmitting}
              />
            </div>
            <div>
              <label htmlFor="replay-iterations" className="label">
                Max iterations
              </label>
              <input
                id="replay-iterations"
                name="max_iterations"
                type="number"
                min="1"
                max="50"
                step="1"
                className="input"
                placeholder="Original"
                disabled={!terminal || isSubmitting}
              />
            </div>
          </div>
        </details>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-slate-400">
            <input
              type="checkbox"
              checked={evaluate}
              onChange={(event) => setEvaluate(event.target.checked)}
              disabled={!terminal || isSubmitting}
              className="h-4 w-4 rounded border-white/10 bg-ink-950 text-accent"
            />
            Evaluate the replay with the custom metrics
          </label>
          {evaluate ? (
            <div className="flex flex-wrap gap-1.5">
              {metricOptions.map((metric) => (
                <label
                  key={metric.name}
                  title={metric.description}
                  className={`cursor-pointer rounded border px-2 py-1 font-mono text-[10px] ${
                    selectedMetrics.includes(metric.name)
                      ? "border-accent/40 bg-accent/10 text-accent"
                      : "border-white/10 bg-white/5 text-slate-400"
                  }`}
                >
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={selectedMetrics.includes(metric.name)}
                    onChange={() => toggleMetric(metric.name)}
                  />
                  {metric.name}
                </label>
              ))}
            </div>
          ) : null}
        </div>

        {error ? (
          <p className="rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-sm text-rose-200">
            {error}
          </p>
        ) : null}

        <div className="flex items-center justify-end">
          <button type="submit" className="btn-primary" disabled={!terminal || isSubmitting}>
            <PlayIcon className="h-4 w-4" />
            {isSubmitting ? "Starting…" : `Start ${mode} replay`}
          </button>
        </div>
      </form>
    </section>
  );
}
