"use client";

import { PUBLIC_API_BASE_URL } from "@/lib/env";
import { ApiError, type ApiErrorEnvelope } from "@/lib/errors";
import type {
  Agent,
  CloneAgentRequest,
  CreateAgentRequest,
  CreateRunRequest,
  DeleteEvaluationsResponse,
  EvaluationReport,
  EvaluationRequest,
  ReplayCreateRequest,
  ReplayCreateResponse,
  ReplaySession,
  Run,
  RunDetail,
  RunEventEnvelope,
  UpdateAgentRequest,
} from "@/types";

/**
 * Browser-side client. Server components fetch through `lib/api.ts`; mutations
 * and live queries happen in the browser, where `NEXT_PUBLIC_API_BASE_URL`
 * points at the published backend port.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${PUBLIC_API_BASE_URL}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch (error) {
    throw new ApiError(
      `Could not reach the API at ${PUBLIC_API_BASE_URL}. Is the backend running?`,
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

export function createRun(payload: CreateRunRequest): Promise<Run> {
  return request<Run>("/api/v1/runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getRun(runId: string): Promise<RunDetail> {
  return request<RunDetail>(`/api/v1/runs/${runId}`);
}

export function cancelRun(runId: string): Promise<Run> {
  return request<Run>(`/api/v1/runs/${runId}/cancel`, { method: "POST" });
}

export function createAgent(payload: CreateAgentRequest): Promise<Agent> {
  return request<Agent>("/api/v1/agents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateAgent(agentId: string, payload: UpdateAgentRequest): Promise<Agent> {
  return request<Agent>(`/api/v1/agents/${agentId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function cloneAgent(agentId: string, payload: CloneAgentRequest = {}): Promise<Agent> {
  return request<Agent>(`/api/v1/agents/${agentId}/clone`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/**
 * Soft-deletes (deactivates) an agent by default. A hard delete is only allowed
 * by the backend when the agent has no runs; otherwise it answers 409.
 */
export function deleteAgent(agentId: string, hard = false): Promise<void> {
  return request<void>(`/api/v1/agents/${agentId}?hard=${hard ? "true" : "false"}`, {
    method: "DELETE",
  });
}

export function runStreamUrl(runId: string): string {
  return `${PUBLIC_API_BASE_URL}/api/v1/runs/${runId}/stream`;
}

// --- Evaluations ------------------------------------------------------------
export function evaluateRun(
  runId: string,
  payload: EvaluationRequest,
): Promise<EvaluationReport> {
  return request<EvaluationReport>(`/api/v1/runs/${runId}/evaluations`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteRunEvaluations(runId: string): Promise<DeleteEvaluationsResponse> {
  return request<DeleteEvaluationsResponse>(`/api/v1/runs/${runId}/evaluations`, {
    method: "DELETE",
  });
}

// --- Replay sessions --------------------------------------------------------
export function startReplay(
  runId: string,
  payload: ReplayCreateRequest,
): Promise<ReplayCreateResponse> {
  return request<ReplayCreateResponse>(`/api/v1/runs/${runId}/replay`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function cancelReplay(sessionId: string): Promise<ReplaySession> {
  return request<ReplaySession>(`/api/v1/replay-sessions/${sessionId}/cancel`, {
    method: "POST",
  });
}

export type { RunEventEnvelope };
