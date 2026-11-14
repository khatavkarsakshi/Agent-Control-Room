import type { ReplayStatus, RunStatus, SpanStatus } from "@/types";
import { REPLAY_STATUS_META, RUN_STATUS_META, SPAN_STATUS_META } from "@/lib/format";

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const meta = RUN_STATUS_META[status];
  return (
    <span className={`badge ${meta.className}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dotClassName}`} />
      {meta.label}
    </span>
  );
}

export function SpanStatusBadge({ status }: { status: SpanStatus }) {
  const meta = SPAN_STATUS_META[status];
  return (
    <span className={`badge ${meta.className}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dotClassName}`} />
      {meta.label}
    </span>
  );
}

export function ReplayStatusBadge({ status }: { status: ReplayStatus }) {
  const meta = REPLAY_STATUS_META[status];
  return (
    <span className={`badge ${meta.className}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dotClassName}`} />
      {meta.label}
    </span>
  );
}
