import "server-only";

import { ApiError, type ApiErrorEnvelope } from "@/lib/errors";
import { PUBLIC_API_BASE_URL } from "@/lib/env";
import type {
  Agent,
  CreateAgentRequest,
  CreateRunRequest,
  EvaluationAggregate,
  EvaluationCatalog,
  EvaluationSummary,
  Health,
  Page,
  Readiness,
  ReplaySession,
  ReplaySessionList,
  ReplayStatus,
  Run,
  RunDetail,
  RunStatus,
  ToolDescription,
  Trace,
  TraceTree,
} from "@/types";

const SERVER_API_BASE_URL = (
  process.env.API_BASE_URL ?? PUBLIC_API_BASE_URL
).replace(/\/+$/, "");

const REQUEST_TIMEOUT_MS = 10_000;

export interface RunQuery {
  agent_id?: string;
  status?: RunStatus | RunStatus[];
  search?: string;
  date_from?: string;
  date_to?: string;
  min_cost?: number;
  max_cost?: number;
  sort?: string;
  limit?: number;
  offset?: number;
}

function createQuery(params: Record<string, string | number | string[] | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") {
      continue;
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        search.append(key, item);
      }
      continue;
    }
    search.set(key, String(value));
  }
  const serialized = search.toString();
  return serialized ? `?${serialized}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${SERVER_API_BASE_URL}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
      signal: init?.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    throw new ApiError(
      `Could not reach the API at ${SERVER_API_BASE_URL}. Is the backend running?`,
      { status: 503, code: "api_unreachable", details: error },
    );
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorEnvelope | null;
    throw new ApiError(body?.error?.message ?? `Request failed with status ${response.status}`, {
      status: response.status,
      code: body?.error?.code,
      details: body?.error?.details,
      requestId: body?.error?.request_id,
    });
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function runQueryString(query: RunQuery): string {
  return createQuery({
    agent_id: query.agent_id,
    status: query.status,
    search: query.search,
    date_from: query.date_from,
    date_to: query.date_to,
    min_cost: query.min_cost,
    max_cost: query.max_cost,
    sort: query.sort,
    limit: query.limit,
    offset: query.offset,
  });
}

export function listAgents(params: {
  search?: string;
  is_active?: boolean;
  limit?: number;
  offset?: number;
} = {}): Promise<Page<Agent>> {
  return request<Page<Agent>>(
    `/api/v1/agents${createQuery({
      search: params.search,
      is_active: params.is_active === undefined ? undefined : String(params.is_active),
      limit: params.limit ?? 200,
      offset: params.offset ?? 0,
    })}`,
  );
}

export function getAgent(agentId: string): Promise<Agent> {
  return request<Agent>(`/api/v1/agents/${agentId}`);
}

export function listRuns(query: RunQuery = {}): Promise<Page<Run>> {
  return request<Page<Run>>(`/api/v1/runs${runQueryString(query)}`);
}

export function getRun(runId: string): Promise<RunDetail> {
  return request<RunDetail>(`/api/v1/runs/${runId}`);
}

export function getTraceTree(runId: string): Promise<TraceTree> {
  return request<TraceTree>(`/api/v1/traces/tree${createQuery({ run_id: runId })}`);
}

export function listTraces(runId: string): Promise<Page<Trace>> {
  return request<Page<Trace>>(
    `/api/v1/traces${createQuery({ run_id: runId, limit: 200, order: "asc" })}`,
  );
}

export function listTools(): Promise<ToolDescription[]> {
  return request<ToolDescription[]>("/api/v1/tools");
}

export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}

export function getReadiness(): Promise<Readiness> {
  return request<Readiness>("/health/ready");
}

// --- Evaluations ------------------------------------------------------------
export function getEvaluationCatalog(): Promise<EvaluationCatalog> {
  return request<EvaluationCatalog>("/api/v1/evaluations/metrics");
}

export function getRunEvaluations(runId: string): Promise<EvaluationSummary> {
  return request<EvaluationSummary>(`/api/v1/runs/${runId}/evaluations`);
}

export function getEvaluationAggregate(params: {
  agent_id?: string;
  evaluator_type?: string;
  metric_name?: string;
  date_from?: string;
  date_to?: string;
} = {}): Promise<EvaluationAggregate> {
  return request<EvaluationAggregate>(
    `/api/v1/evaluations/summary${createQuery({
      agent_id: params.agent_id,
      evaluator_type: params.evaluator_type,
      metric_name: params.metric_name,
      date_from: params.date_from,
      date_to: params.date_to,
    })}`,
  );
}

// --- Replay sessions --------------------------------------------------------
export interface ReplaySessionQuery {
  original_run_id?: string;
  status?: ReplayStatus | ReplayStatus[];
  limit?: number;
  offset?: number;
}

export function listReplaySessions(
  query: ReplaySessionQuery = {},
): Promise<ReplaySessionList> {
  return request<ReplaySessionList>(
    `/api/v1/replay-sessions${createQuery({
      original_run_id: query.original_run_id,
      status: query.status,
      limit: query.limit,
      offset: query.offset,
    })}`,
  );
}

export function getReplaySession(sessionId: string): Promise<ReplaySession> {
  return request<ReplaySession>(`/api/v1/replay-sessions/${sessionId}`);
}

export function createRun(payload: CreateRunRequest): Promise<Run> {
  return request<Run>("/api/v1/runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function createAgent(payload: CreateAgentRequest): Promise<Agent> {
  return request<Agent>("/api/v1/agents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getRunExportUrl(runId: string): string {
  // Links are opened by the browser, so always use the public base URL.
  return `${PUBLIC_API_BASE_URL}/api/v1/runs/${runId}/export`;
}
