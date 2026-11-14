/**
 * Types mirroring the FastAPI response models in `backend/app/schemas`.
 * Keeping them in one place makes the API boundary explicit: a backend schema
 * change is a compile error in every component that consumes the field.
 */

export type RunStatus = "pending" | "running" | "completed" | "failed" | "replayed";

export type SpanType = "llm_call" | "tool_call" | "data_access" | "decision" | "error";

export type SpanStatus = "ok" | "error" | "timeout";

export interface ToolConfig {
  name: string;
  config: Record<string, unknown>;
}

export interface Agent {
  id: string;
  name: string;
  description: string | null;
  model_name: string;
  system_prompt: string | null;
  tools_config: ToolConfig[];
  created_at: string;
  updated_at: string;
  is_active: boolean;
}

export interface ToolCall {
  id: string;
  trace_id: string;
  tool_name: string;
  tool_input: Record<string, unknown> | null;
  tool_output: Record<string, unknown> | null;
  duration_ms: number;
  success: boolean;
  error_message: string | null;
}

export interface Trace {
  id: string;
  run_id: string;
  parent_trace_id: string | null;
  span_name: string;
  span_type: SpanType;
  input_data: Record<string, unknown> | null;
  output_data: Record<string, unknown> | null;
  status: SpanStatus;
  duration_ms: number;
  tokens_used: number;
  tokens_input: number;
  tokens_output: number;
  cost_usd: number;
  error_details: Record<string, unknown> | null;
  timestamp: string;
  trace_id: string;
  span_id: string;
  model_name: string | null;
  tool_calls: ToolCall[];
}

export interface TraceNode extends Trace {
  children: TraceNode[];
}

export interface TraceTree {
  run_id: string;
  trace_id: string | null;
  roots: TraceNode[];
  span_count: number;
  total_duration_ms: number;
}

export interface Run {
  id: string;
  agent_id: string;
  input_prompt: string;
  status: RunStatus;
  output_response: string | null;
  total_tokens_used: number;
  total_cost_usd: number;
  latency_ms: number | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  replay_of_run_id: string | null;
}

export interface RunDetail extends Run {
  traces: Trace[];
  evaluations: Evaluation[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ToolDescription {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface Health {
  status: string;
  version: string;
  environment: string;
  uptime_seconds: number;
}

export interface Readiness {
  status: string;
  components: Record<string, string>;
}

/** Payload broadcast on the run event channel. */
export interface RunEventEnvelope<T = unknown> {
  type: string;
  run_id: string;
  timestamp: string;
  data: T;
}

export interface RunSnapshot {
  run: Run;
  traces: Trace[];
}

/** Trace row carried by a `trace.completed` event (input/output omitted). */
export interface TraceEvent {
  id: string;
  span_name: string;
  span_type: SpanType;
  status: SpanStatus;
  duration_ms: number;
  tokens_used: number;
  cost_usd: number;
  parent_trace_id: string | null;
  trace_id: string;
  span_id: string;
  model_name: string | null;
  tool_call_count: number;
  timestamp: string;
}

export interface ExecutionOptions {
  model_name?: string | null;
  temperature?: number;
  max_iterations?: number;
  max_tokens?: number;
  timeout_seconds?: number;
}

export interface CreateRunRequest {
  agent_id: string;
  input_prompt: string;
  execution_options?: ExecutionOptions;
  metadata?: Record<string, unknown>;
}

export interface CreateAgentRequest {
  name: string;
  description?: string | null;
  model_name: string;
  system_prompt?: string | null;
  tools_config?: Array<string | ToolConfig>;
  is_active?: boolean;
}

/** PATCH body for `/api/v1/agents/{id}`; omitting a field leaves it unchanged. */
export interface UpdateAgentRequest {
  name?: string;
  description?: string | null;
  model_name?: string;
  system_prompt?: string | null;
  tools_config?: Array<string | ToolConfig>;
  is_active?: boolean;
}

export interface CloneAgentRequest {
  name?: string | null;
}

// --- Evaluations (Phase 4) --------------------------------------------------
export type EvaluatorType = "ragas" | "deepeval" | "custom";

export interface Evaluation {
  id: string;
  run_id: string;
  evaluator_type: EvaluatorType;
  metric_name: string;
  score: number;
  reason: string | null;
  created_at: string;
}

export interface SkippedMetric {
  metric_name: string;
  reason: string;
}

export interface EvaluationRequest {
  evaluator_type: EvaluatorType;
  metrics?: string[];
  reference?: string | null;
  contexts?: string[];
  replace?: boolean;
}

export interface EvaluationReport {
  run_id: string;
  evaluator_type: EvaluatorType;
  evaluations: Evaluation[];
  skipped: SkippedMetric[];
  average_score: number | null;
  replaced: number;
}

export interface MetricStat {
  count: number;
  average: number | null;
  minimum: number | null;
  maximum: number | null;
}

export interface EvaluationSummary {
  run_id: string;
  evaluations: Evaluation[];
  average_score: number | null;
  by_metric: Record<string, MetricStat>;
  by_evaluator: Record<string, MetricStat>;
}

export interface MetricDefinition {
  name: string;
  description: string;
  requires_contexts: boolean;
  requires_reference: boolean;
}

export interface EvaluatorCatalogEntry {
  evaluator_type: EvaluatorType;
  display_name: string;
  available: boolean;
  reason: string | null;
  default_metrics: string[];
  metrics: MetricDefinition[];
}

export interface EvaluationCatalog {
  evaluators: EvaluatorCatalogEntry[];
}

export interface MetricAggregate {
  evaluator_type: EvaluatorType;
  metric_name: string;
  run_count: number;
  evaluation_count: number;
  average: number;
  minimum: number;
  maximum: number;
  latest_at: string;
}

export interface EvaluationAggregate {
  items: MetricAggregate[];
  total_evaluations: number;
}

export interface BatchEvaluationRequest {
  run_ids?: string[];
  agent_id?: string;
  status?: RunStatus;
  limit?: number;
  evaluator_type?: EvaluatorType;
  metrics?: string[];
  reference?: string | null;
  replace?: boolean;
}

export interface BatchEvaluationResponse {
  task_id: string | null;
  requested: number;
  run_ids: string[];
}

// --- Replay sessions (Phase 4) ----------------------------------------------
export type ReplayStatus = "pending" | "running" | "completed" | "failed";
export type ReplayMode = "live" | "offline";

export interface ReplayOverrides {
  input_prompt?: string | null;
  model_name?: string | null;
  system_prompt?: string | null;
  tools?: string[] | null;
  temperature?: number | null;
  max_iterations?: number | null;
  max_tokens?: number | null;
  timeout_seconds?: number | null;
}

export interface ReplayCreateRequest {
  mode: ReplayMode;
  overrides?: ReplayOverrides;
  evaluate?: boolean;
  metrics?: string[];
}

export interface ReplayRunSummary {
  id: string;
  agent_id: string;
  status: RunStatus;
  input_prompt: string;
  output_response: string | null;
  total_tokens_used: number;
  total_cost_usd: number;
  latency_ms: number | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  replay_of_run_id: string | null;
}

export interface ComparisonDelta {
  original: number | null;
  replay: number | null;
  delta: number | null;
  ratio: number | null;
}

export interface SpanStats {
  total: number;
  errors: number;
  by_type: Record<string, number>;
}

export interface ToolCallStats {
  calls: number;
  succeeded: number;
  failed: number;
  duration_ms: number;
}

export interface ReplayEvaluationSnapshot {
  evaluator_type: EvaluatorType;
  average_score: number | null;
  scores: Record<string, number>;
  skipped: SkippedMetric[];
}

export interface ReplayComparison {
  mode: ReplayMode;
  billed: boolean;
  status: { original: RunStatus; replay: RunStatus };
  prompt_changed: boolean;
  output: {
    original_chars: number;
    replay_chars: number;
    identical: boolean;
    similarity: number;
  };
  latency_ms: ComparisonDelta;
  tokens: ComparisonDelta;
  cost_usd: ComparisonDelta;
  spans: { original: SpanStats; replay: SpanStats };
  tool_calls: { original: ToolCallStats; replay: ToolCallStats };
  evaluations: {
    original: Record<string, number>;
    replay: Record<string, number>;
    delta: Record<string, number>;
  };
  evaluation?: ReplayEvaluationSnapshot | { error: string };
}

export interface ReplaySession {
  id: string;
  original_run_id: string;
  replay_run_id: string | null;
  status: ReplayStatus;
  overrides: Record<string, unknown>;
  comparison_result: ReplayComparison | { error: string; mode?: ReplayMode } | null;
  created_at: string;
  completed_at: string | null;
  original_run: ReplayRunSummary | null;
  replay_run: ReplayRunSummary | null;
}

export interface ReplaySessionList {
  items: ReplaySession[];
  total: number;
  limit: number;
  offset: number;
}

export interface ReplayCreateResponse {
  session: ReplaySession;
  task_id: string | null;
}

export interface DeleteEvaluationsResponse {
  run_id: string;
  deleted: number;
}
