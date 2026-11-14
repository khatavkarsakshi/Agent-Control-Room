import type { ReplayStatus, RunStatus, SpanStatus, SpanType } from "@/types";

const DATE_TIME_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const DATE_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  year: "numeric",
  month: "short",
  day: "2-digit",
});

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return `${DATE_TIME_FORMATTER.format(date)} UTC`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return DATE_FORMATTER.format(date);
}

export function formatNumber(value: number, maximumFractionDigits = 0): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits }).format(value);
}

export function formatCost(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return "$0.00";
  }
  if (value < 0.01) {
    return `$${value.toFixed(6)}`;
  }
  if (value < 1) {
    return `$${value.toFixed(4)}`;
  }
  return `$${value.toFixed(2)}`;
}

export function formatDuration(milliseconds: number | null | undefined): string {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(milliseconds)) {
    return "—";
  }
  if (milliseconds < 1_000) {
    return `${Math.round(milliseconds)} ms`;
  }
  if (milliseconds < 60_000) {
    return `${(milliseconds / 1_000).toFixed(2)} s`;
  }
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = Math.round((milliseconds % 60_000) / 1_000);
  return `${minutes}m ${seconds}s`;
}

export function shortId(value: string | null | undefined, length = 8): string {
  if (!value) {
    return "—";
  }
  return value.length > length ? value.slice(0, length) : value;
}

export function truncate(value: string, length: number): string {
  if (value.length <= length) {
    return value;
  }
  return `${value.slice(0, length - 1)}…`;
}

export function percent(part: number, total: number): number {
  if (total <= 0) {
    return 0;
  }
  return Math.min(100, Math.max(0, (part / total) * 100));
}

export interface BadgeMeta {
  label: string;
  className: string;
  dotClassName: string;
}

export const RUN_STATUS_META: Record<RunStatus, BadgeMeta> = {
  pending: {
    label: "Pending",
    className: "border-slate-500/30 bg-slate-500/10 text-slate-300",
    dotClassName: "bg-slate-400",
  },
  running: {
    label: "Running",
    className: "border-sky-400/30 bg-sky-400/10 text-sky-300",
    dotClassName: "bg-sky-400 animate-pulse-soft",
  },
  completed: {
    label: "Completed",
    className: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
    dotClassName: "bg-emerald-400",
  },
  failed: {
    label: "Failed",
    className: "border-rose-400/30 bg-rose-400/10 text-rose-300",
    dotClassName: "bg-rose-400",
  },
  replayed: {
    label: "Replayed",
    className: "border-violet-400/30 bg-violet-400/10 text-violet-300",
    dotClassName: "bg-violet-400",
  },
};

export const SPAN_STATUS_META: Record<SpanStatus, BadgeMeta> = {
  ok: {
    label: "OK",
    className: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
    dotClassName: "bg-emerald-400",
  },
  error: {
    label: "Error",
    className: "border-rose-400/30 bg-rose-400/10 text-rose-300",
    dotClassName: "bg-rose-400",
  },
  timeout: {
    label: "Timeout",
    className: "border-amber-400/30 bg-amber-400/10 text-amber-300",
    dotClassName: "bg-amber-400",
  },
};

export interface SpanTypeMeta {
  label: string;
  barClassName: string;
  iconClassName: string;
}

export const SPAN_TYPE_META: Record<SpanType, SpanTypeMeta> = {
  llm_call: {
    label: "LLM",
    barClassName: "bg-emerald-400/80",
    iconClassName: "text-emerald-300",
  },
  tool_call: {
    label: "Tool",
    barClassName: "bg-sky-400/80",
    iconClassName: "text-sky-300",
  },
  data_access: {
    label: "Data",
    barClassName: "bg-amber-400/80",
    iconClassName: "text-amber-300",
  },
  decision: {
    label: "Decision",
    barClassName: "bg-violet-400/80",
    iconClassName: "text-violet-300",
  },
  error: {
    label: "Error",
    barClassName: "bg-rose-400/80",
    iconClassName: "text-rose-300",
  },
};

export const REPLAY_STATUS_META: Record<ReplayStatus, BadgeMeta> = {
  pending: {
    label: "Pending",
    className: "border-slate-500/30 bg-slate-500/10 text-slate-300",
    dotClassName: "bg-slate-400",
  },
  running: {
    label: "Running",
    className: "border-sky-400/30 bg-sky-400/10 text-sky-300",
    dotClassName: "bg-sky-400 animate-pulse-soft",
  },
  completed: {
    label: "Completed",
    className: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
    dotClassName: "bg-emerald-400",
  },
  failed: {
    label: "Failed",
    className: "border-rose-400/30 bg-rose-400/10 text-rose-300",
    dotClassName: "bg-rose-400",
  },
};

/** Render a 0..1 quality score as a percentage with one decimal place. */
export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return `${(value * 100).toFixed(1)}%`;
}

/** Format a difference with an explicit sign; deltas are replay minus original. */
export function formatSigned(value: number | null | undefined, fractionDigits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatNumber(value, fractionDigits)}`;
}

export function formatSignedCost(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${formatCost(Math.abs(value))}`;
}

export function formatSignedDuration(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${formatDuration(Math.abs(value))}`;
}
