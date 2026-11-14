"use client";

import { useMemo, useState, type FormEvent } from "react";

import { GaugeIcon, TrashIcon } from "@/components/icons";
import { deleteRunEvaluations, evaluateRun } from "@/lib/client";
import { toErrorMessage } from "@/lib/errors";
import { formatDateTime, formatScore } from "@/lib/format";
import type {
  Evaluation,
  EvaluationCatalog,
  EvaluatorCatalogEntry,
  EvaluatorType,
  SkippedMetric,
} from "@/types";

interface EvaluationPanelProps {
  runId: string;
  initialEvaluations: Evaluation[];
  catalog: EvaluationCatalog | null;
  /** False while a run is pending/running or has no output to score. */
  canEvaluate: boolean;
}

const EVALUATOR_LABEL: Record<EvaluatorType, string> = {
  custom: "Custom",
  ragas: "Ragas",
  deepeval: "DeepEval",
};

function average(evaluations: Evaluation[]): number | null {
  if (evaluations.length === 0) {
    return null;
  }
  return evaluations.reduce((sum, item) => sum + item.score, 0) / evaluations.length;
}

function scoreTone(score: number): string {
  if (score >= 0.75) {
    return "bg-emerald-400";
  }
  if (score >= 0.5) {
    return "bg-amber-400";
  }
  return "bg-rose-400";
}

export function EvaluationPanel({
  runId,
  initialEvaluations,
  catalog,
  canEvaluate,
}: EvaluationPanelProps) {
  const evaluators = useMemo(() => catalog?.evaluators ?? [], [catalog]);
  const firstAvailable =
    evaluators.find((entry) => entry.available)?.evaluator_type ??
    ("custom" as EvaluatorType);
  const [evaluatorType, setEvaluatorType] = useState<EvaluatorType>(firstAvailable);
  const [evaluations, setEvaluations] = useState<Evaluation[]>(initialEvaluations);
  const [selectedMetrics, setSelectedMetrics] = useState<string[]>([]);
  const [reference, setReference] = useState("");
  const [contexts, setContexts] = useState("");
  const [replace, setReplace] = useState(true);
  const [skipped, setSkipped] = useState<SkippedMetric[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const entry: EvaluatorCatalogEntry | undefined = useMemo(
    () => evaluators.find((item) => item.evaluator_type === evaluatorType),
    [evaluators, evaluatorType],
  );
  const metrics = entry?.metrics ?? [];
  // An empty selection means "let the evaluator pick the applicable defaults".
  const defaultMetrics = entry?.default_metrics ?? [];
  const activeMetrics = selectedMetrics.length > 0 ? selectedMetrics : defaultMetrics;
  const summary = average(evaluations);

  const toggleMetric = (name: string): void => {
    setSelectedMetrics((current) => {
      const base = current.length > 0 ? current : defaultMetrics;
      return base.includes(name)
        ? base.filter((item) => item !== name)
        : [...base, name];
    });
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (entry && !entry.available) {
      setError(entry.reason ?? "This evaluator is not available in this deployment.");
      return;
    }

    const contextList = contexts
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);

    setIsSubmitting(true);
    try {
      const report = await evaluateRun(runId, {
        evaluator_type: evaluatorType,
        ...(selectedMetrics.length > 0 ? { metrics: selectedMetrics } : {}),
        ...(reference.trim() ? { reference: reference.trim() } : {}),
        ...(contextList.length > 0 ? { contexts: contextList } : {}),
        replace,
      });
      setEvaluations((current) => [
        ...current.filter((item) => item.evaluator_type !== report.evaluator_type),
        ...report.evaluations,
      ]);
      setSkipped(report.skipped);
      setNotice(
        `${report.evaluations.length} score${report.evaluations.length === 1 ? "" : "s"} for this evaluator` +
          `${
            report.average_score === null ? "" : ` · average ${formatScore(report.average_score)}`
          }${report.replaced > 0 ? ` · updated ${report.replaced}` : ""}.`,
      );
    } catch (cause) {
      setError(toErrorMessage(cause));
      setSkipped([]);
    } finally {
      setIsSubmitting(false);
    }
  };

  const onDelete = async (): Promise<void> => {
    setError(null);
    setNotice(null);
    setIsSubmitting(true);
    try {
      const result = await deleteRunEvaluations(runId);
      setEvaluations([]);
      setSkipped([]);
      setNotice(`Deleted ${result.deleted} score${result.deleted === 1 ? "" : "s"}.`);
    } catch (cause) {
      setError(toErrorMessage(cause));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section className="card">
      <div className="card-header">
        <div className="flex items-center gap-2">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent/10 text-accent">
            <GaugeIcon className="h-4 w-4" />
          </span>
          <div>
            <h2 className="text-sm font-semibold text-slate-100">Quality evaluation</h2>
            <p className="text-xs text-slate-500">
              {evaluations.length > 0
                ? `${evaluations.length} metric${evaluations.length === 1 ? "" : "s"} scored · average ${formatScore(summary)}`
                : "No scores yet for this run."}
            </p>
          </div>
        </div>
        {evaluations.length > 0 ? (
          <button
            type="button"
            className="btn-ghost px-2.5 py-1.5 text-xs"
            onClick={() => void onDelete()}
            disabled={isSubmitting}
          >
            <TrashIcon className="h-3.5 w-3.5" />
            Clear scores
          </button>
        ) : null}
      </div>

      {evaluations.length > 0 ? (
        <div className="divide-y divide-white/5 border-b border-white/5">
          {evaluations.map((evaluation) => (
            <div key={evaluation.id} className="px-5 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="badge border-white/10 bg-white/5 text-slate-300">
                    {EVALUATOR_LABEL[evaluation.evaluator_type]}
                  </span>
                  <span className="font-mono text-xs text-slate-200">{evaluation.metric_name}</span>
                </div>
                <span className="text-sm font-semibold text-slate-100">
                  {formatScore(evaluation.score)}
                </span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/5">
                <div
                  className={`h-full rounded-full ${scoreTone(evaluation.score)}`}
                  style={{ width: `${Math.max(2, Math.round(evaluation.score * 100))}%` }}
                />
              </div>
              <p className="mt-1.5 text-[11px] text-slate-600">
                scored {formatDateTime(evaluation.created_at)}
              </p>
              {evaluation.reason ? (
                <details className="mt-1.5">
                  <summary className="cursor-pointer select-none text-[11px] text-slate-500 hover:text-slate-300">
                    Why this score
                  </summary>
                  <p className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed text-slate-500">
                    {evaluation.reason}
                  </p>
                </details>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      <form onSubmit={(event) => void onSubmit(event)} className="card-body space-y-4">
        {!canEvaluate ? (
          <p className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-xs text-amber-200">
            Evaluation becomes available once the run has finished with an output.
          </p>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="eval-evaluator" className="label">
              Evaluator
            </label>
            <select
              id="eval-evaluator"
              className="input"
              value={evaluatorType}
              onChange={(event) => {
                setEvaluatorType(event.target.value as EvaluatorType);
                setSelectedMetrics([]);
              }}
              disabled={!canEvaluate || isSubmitting}
            >
              {evaluators.length === 0 ? <option value="custom">Custom</option> : null}
              {evaluators.map((item) => (
                <option key={item.evaluator_type} value={item.evaluator_type}>
                  {item.display_name}
                  {item.available ? "" : " (unavailable)"}
                </option>
              ))}
            </select>
            {entry && !entry.available ? (
              <p className="mt-1 text-[11px] text-amber-300">{entry.reason}</p>
            ) : null}
          </div>

          <div>
            <span className="label">Metrics</span>
            <div className="flex flex-wrap gap-1.5">
              {metrics.length === 0 ? (
                <span className="text-xs text-slate-500">Evaluator defaults</span>
              ) : null}
              {metrics.map((metric) => {
                const checked = activeMetrics.includes(metric.name);
                const missingInput =
                  (metric.requires_contexts && contexts.trim().length === 0) ||
                  (metric.requires_reference && reference.trim().length === 0);
                return (
                  <label
                    key={metric.name}
                    title={metric.description}
                    className={`cursor-pointer rounded-lg border px-2 py-1 font-mono text-[10px] ${
                      checked
                        ? "border-accent/40 bg-accent/10 text-accent"
                        : "border-white/10 bg-white/5 text-slate-400 hover:text-slate-200"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={checked}
                      onChange={() => toggleMetric(metric.name)}
                      disabled={!canEvaluate || isSubmitting || !entry?.available}
                    />
                    {metric.name}
                    {missingInput ? <span className="ml-1 text-amber-300">*</span> : null}
                  </label>
                );
              })}
            </div>
            <p className="mt-1 text-[11px] text-slate-600">
              An empty selection uses the evaluator defaults.{" "}
              <span className="text-amber-300">*</span> needs a reference or contexts; if neither
              the request nor the trace supplies one, the metric is skipped.
            </p>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="eval-reference" className="label">
              Reference answer (optional)
            </label>
            <textarea
              id="eval-reference"
              className="input min-h-[80px] resize-y"
              value={reference}
              onChange={(event) => setReference(event.target.value)}
              placeholder="Ground truth for answer_correctness and context_recall."
              maxLength={20_000}
              disabled={!canEvaluate || isSubmitting}
            />
          </div>
          <div>
            <label htmlFor="eval-contexts" className="label">
              Retrieved contexts (optional, one per line)
            </label>
            <textarea
              id="eval-contexts"
              className="input min-h-[80px] resize-y"
              value={contexts}
              onChange={(event) => setContexts(event.target.value)}
              placeholder="Leave empty to extract contexts from data-access spans and tool outputs."
              disabled={!canEvaluate || isSubmitting}
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <label className="flex items-center gap-2 text-xs text-slate-400">
            <input
              type="checkbox"
              checked={replace}
              onChange={(event) => setReplace(event.target.checked)}
              disabled={!canEvaluate || isSubmitting}
              className="h-4 w-4 rounded border-white/10 bg-ink-950 text-accent"
            />
            Replace existing scores for the same metric
          </label>
          <button
            type="submit"
            className="btn-primary"
            disabled={!canEvaluate || isSubmitting || !entry?.available}
          >
            {isSubmitting ? "Scoring…" : "Run evaluation"}
          </button>
        </div>

        {skipped.length > 0 ? (
          <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-amber-300">
              Skipped
            </p>
            <ul className="mt-1 space-y-1 text-xs text-amber-100">
              {skipped.map((item) => (
                <li key={`${item.metric_name}-${item.reason}`}>
                  <span className="font-mono">{item.metric_name}</span>: {item.reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {notice ? <p className="text-xs text-emerald-300">{notice}</p> : null}
        {error ? (
          <p className="rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-sm text-rose-200">
            {error}
          </p>
        ) : null}

        <p className="text-[11px] text-slate-600">
          Scores are stored per run and metric (reruns update in place). Ragas and DeepEval need
          the optional evaluator packages and a judge model.
        </p>
      </form>
    </section>
  );
}
