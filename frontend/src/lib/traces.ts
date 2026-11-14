import type { Trace, TraceEvent, TraceNode } from "@/types";

/**
 * Assemble the flat trace list into the same parent/child tree the backend
 * returns from `/traces/tree`. Rows whose parent is missing are promoted to
 * roots so a partially loaded live view never loses spans.
 */
export function buildTraceTree(traces: Trace[]): TraceNode[] {
  const nodes = new Map<string, TraceNode>();
  for (const trace of traces) {
    nodes.set(trace.id, { ...trace, children: [] });
  }

  const roots: TraceNode[] = [];
  for (const trace of traces) {
    const node = nodes.get(trace.id);
    if (!node) {
      continue;
    }
    const parent = trace.parent_trace_id ? nodes.get(trace.parent_trace_id) : undefined;
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  const sort = (list: TraceNode[]): void => {
    list.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
    for (const node of list) {
      sort(node.children);
    }
  };
  sort(roots);
  return roots;
}

export interface FlatSpan {
  trace: TraceNode;
  depth: number;
}

export function flattenTraceTree(roots: TraceNode[]): FlatSpan[] {
  const flat: FlatSpan[] = [];
  const walk = (nodes: TraceNode[], depth: number): void => {
    for (const node of nodes) {
      flat.push({ trace: node, depth });
      walk(node.children, depth + 1);
    }
  };
  walk(roots, 0);
  return flat;
}

export interface TraceWindow {
  start: number;
  end: number;
  duration: number;
}

/** Time bounds used by the waterfall; handles zero-width spans. */
export function traceWindow(traces: Trace[]): TraceWindow {
  if (traces.length === 0) {
    return { start: 0, end: 0, duration: 1 };
  }
  let start = Number.POSITIVE_INFINITY;
  let end = Number.NEGATIVE_INFINITY;
  for (const trace of traces) {
    const spanStart = Date.parse(trace.timestamp);
    if (Number.isNaN(spanStart)) {
      continue;
    }
    const spanEnd = spanStart + Math.max(trace.duration_ms, 0);
    start = Math.min(start, spanStart);
    end = Math.max(end, spanEnd);
  }
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return { start: 0, end: 0, duration: 1 };
  }
  return { start, end, duration: Math.max(end - start, 1) };
}

/**
 * Merge a live `trace.completed` event into the local trace list.
 *
 * A span row is written when it opens, so the usual case is an update. If the
 * row is not present yet (event raced the snapshot) a partial row is created.
 */
export function mergeTraceEvent(traces: Trace[], event: TraceEvent): Trace[] {
  const index = traces.findIndex((trace) => trace.id === event.id);
  if (index === -1) {
    return [
      ...traces,
      {
        id: event.id,
        run_id: "",
        parent_trace_id: event.parent_trace_id,
        span_name: event.span_name,
        span_type: event.span_type,
        input_data: null,
        output_data: null,
        status: event.status,
        duration_ms: event.duration_ms,
        tokens_used: event.tokens_used,
        tokens_input: 0,
        tokens_output: 0,
        cost_usd: event.cost_usd,
        error_details: null,
        timestamp: event.timestamp,
        trace_id: event.trace_id,
        span_id: event.span_id,
        model_name: event.model_name,
        tool_calls: [],
      },
    ];
  }

  const next = [...traces];
  const current = next[index];
  if (!current) {
    return traces;
  }
  next[index] = {
    ...current,
    parent_trace_id: event.parent_trace_id,
    span_name: event.span_name,
    span_type: event.span_type,
    status: event.status,
    duration_ms: event.duration_ms,
    tokens_used: event.tokens_used,
    cost_usd: event.cost_usd,
    trace_id: event.trace_id,
    span_id: event.span_id,
    model_name: event.model_name,
    timestamp: event.timestamp,
  };
  return next;
}

export function sumTokens(traces: Trace[]): { input: number; output: number; total: number } {
  return traces.reduce(
    (acc, trace) => ({
      input: acc.input + trace.tokens_input,
      output: acc.output + trace.tokens_output,
      total: acc.total + trace.tokens_used,
    }),
    { input: 0, output: 0, total: 0 },
  );
}
