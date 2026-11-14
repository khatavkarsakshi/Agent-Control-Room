import type { Metadata } from "next";
import Link from "next/link";

import { AgentActions } from "@/components/AgentActions";
import { AgentCreateForm } from "@/components/AgentCreateForm";
import { Pagination } from "@/components/Pagination";
import { ConnectionError, EmptyState } from "@/components/States";
import {
  AgentsIcon,
  ArrowRightIcon,
  CoinsIcon,
  PlayIcon,
  SearchIcon,
  TokensIcon,
} from "@/components/icons";
import { listAgents, listRuns, listTools } from "@/lib/api";
import { PUBLIC_API_BASE_URL } from "@/lib/env";
import { toErrorMessage } from "@/lib/errors";
import { formatCost, formatDateTime, formatNumber } from "@/lib/format";
import type { Agent, Page, Run, ToolDescription } from "@/types";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Agents" };

const RUN_SAMPLE_LIMIT = 200;

type SearchParams = Record<string, string | string[] | undefined>;

interface AgentsPageProps {
  searchParams: Promise<SearchParams>;
}

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function activeParam(value: string | undefined): boolean | undefined {
  if (value === "true") {
    return true;
  }
  if (value === "false") {
    return false;
  }
  return undefined;
}

function positiveInt(value: string | undefined, fallback: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return fallback;
  }
  return Math.min(parsed, max);
}

interface AgentStats {
  runs: number;
  activeRuns: number;
  tokens: number;
  cost: number;
  lastRun: Run | null;
}

function collectStats(runs: Run[]): Map<string, AgentStats> {
  const stats = new Map<string, AgentStats>();
  for (const run of runs) {
    const entry = stats.get(run.agent_id) ?? {
      runs: 0,
      activeRuns: 0,
      tokens: 0,
      cost: 0,
      lastRun: null,
    };
    entry.runs += 1;
    entry.tokens += run.total_tokens_used;
    entry.cost += run.total_cost_usd;
    if (run.status === "pending" || run.status === "running") {
      entry.activeRuns += 1;
    }
    // Runs arrive newest first, so the first hit per agent is the latest run.
    if (!entry.lastRun) {
      entry.lastRun = run;
    }
    stats.set(run.agent_id, entry);
  }
  return stats;
}

export default async function AgentsPage({ searchParams }: AgentsPageProps) {
  const params = await searchParams;
  const search = first(params.search);
  const isActive = activeParam(first(params.is_active));
  const limit = positiveInt(first(params.limit), 24, 200);
  const offset = Math.max(Number(first(params.offset) ?? 0) || 0, 0);

  let agentsPage: Page<Agent>;
  let runsPage: Page<Run>;
  let tools: ToolDescription[];
  try {
    [agentsPage, runsPage, tools] = await Promise.all([
      listAgents({ search, is_active: isActive, limit, offset }),
      listRuns({ limit: RUN_SAMPLE_LIMIT, sort: "-created_at" }),
      listTools(),
    ]);
  } catch (error) {
    return (
      <ConnectionError
        title="Could not load agents"
        message={toErrorMessage(error)}
        apiBaseUrl={PUBLIC_API_BASE_URL}
      />
    );
  }

  const agents = agentsPage.items;
  const stats = collectStats(runsPage.items);
  const activeCount = agents.filter((agent) => agent.is_active).length;
  const toolOptions = tools.map((tool) => ({ name: tool.name, description: tool.description }));
  const hasFilters = Boolean(search) || isActive !== undefined;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-50">Agents</h1>
          <p className="mt-1 text-sm text-slate-500">
            {formatNumber(agentsPage.total)} registered · {formatNumber(activeCount)} active on this
            page · {formatNumber(tools.length)} tools available.
          </p>
        </div>
        <Link href="#new-agent" className="btn-primary">
          Register an agent
        </Link>
      </div>

      <AgentCreateForm tools={toolOptions} defaultOpen={agentsPage.total === 0} />

      <form action="/agents" method="get" className="card p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[16rem] flex-1">
            <label htmlFor="agent-filter-search" className="label">
              Search name or description
            </label>
            <div className="relative">
              <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
              <input
                id="agent-filter-search"
                name="search"
                defaultValue={search ?? ""}
                className="input pl-9"
                placeholder="Research, support, extractor…"
                maxLength={120}
              />
            </div>
          </div>
          <div className="w-44">
            <label htmlFor="agent-filter-active" className="label">
              Activation
            </label>
            <select
              id="agent-filter-active"
              name="is_active"
              defaultValue={isActive === undefined ? "" : String(isActive)}
              className="input"
            >
              <option value="">All agents</option>
              <option value="true">Active only</option>
              <option value="false">Inactive only</option>
            </select>
          </div>
          <button type="submit" className="btn-primary">
            Apply
          </button>
          {hasFilters ? (
            <Link href="/agents" className="btn-ghost">
              Clear
            </Link>
          ) : null}
        </div>
      </form>

      {agents.length === 0 ? (
        <section className="card">
          <EmptyState
            icon={AgentsIcon}
            title={hasFilters ? "No agents match these filters" : "No agents registered yet"}
            description={
              hasFilters
                ? "Clear the filters, or register a new agent with the form above."
                : "Register an agent with a model, system prompt and the tools it may call. Runs are queued against active agents."
            }
            action={
              hasFilters ? (
                <Link href="/agents" className="btn-ghost">
                  Clear filters
                </Link>
              ) : (
                <Link href="#new-agent" className="btn-primary">
                  Register your first agent
                </Link>
              )
            }
          />
        </section>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {agents.map((agent) => {
            const agentStats = stats.get(agent.id);
            return (
              <section key={agent.id} id={agent.id} className="card scroll-mt-24 p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate text-base font-semibold text-slate-100">{agent.name}</h2>
                    <p className="mt-0.5 truncate font-mono text-[11px] text-slate-500">
                      {agent.model_name}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1.5">
                    <span
                      className={`badge ${
                        agent.is_active
                          ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                          : "border-slate-500/30 bg-slate-500/10 text-slate-400"
                      }`}
                    >
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${
                          agent.is_active ? "bg-emerald-400" : "bg-slate-500"
                        }`}
                      />
                      {agent.is_active ? "active" : "inactive"}
                    </span>
                    {agentStats && agentStats.activeRuns > 0 ? (
                      <span className="inline-flex items-center gap-1 text-[11px] text-sky-300">
                        <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-sky-400" />
                        {agentStats.activeRuns} running
                      </span>
                    ) : null}
                  </div>
                </div>

                {agent.description ? (
                  <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-slate-400">
                    {agent.description}
                  </p>
                ) : (
                  <p className="mt-2 text-sm text-slate-600">No description.</p>
                )}

                <dl className="mt-4 grid grid-cols-3 gap-2 rounded-lg border border-white/5 bg-ink-950/40 px-3 py-2.5">
                  <div>
                    <dt className="stat-label">Runs</dt>
                    <dd className="mt-0.5 font-mono text-sm text-slate-200">
                      {formatNumber(agentStats?.runs ?? 0)}
                    </dd>
                  </div>
                  <div>
                    <dt className="stat-label">Tokens</dt>
                    <dd className="mt-0.5 flex items-center gap-1 font-mono text-sm text-slate-200">
                      <TokensIcon className="h-3 w-3 text-slate-500" />
                      {formatNumber(agentStats?.tokens ?? 0)}
                    </dd>
                  </div>
                  <div>
                    <dt className="stat-label">Cost</dt>
                    <dd className="mt-0.5 flex items-center gap-1 font-mono text-sm text-slate-200">
                      <CoinsIcon className="h-3 w-3 text-slate-500" />
                      {formatCost(agentStats?.cost ?? 0)}
                    </dd>
                  </div>
                </dl>

                <p className="mt-2 text-[11px] text-slate-600">
                  {agentStats?.lastRun
                    ? `Last run ${formatDateTime(agentStats.lastRun.created_at)}`
                    : "No runs in the recent sample."}
                </p>

                <div className="mt-3">
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
                    <p className="text-xs text-slate-600">
                      No tools — the agent answers from the model alone.
                    </p>
                  )}
                </div>

                {agent.system_prompt ? (
                  <details className="mt-3 rounded-lg border border-white/5 bg-ink-950/40 px-3 py-2">
                    <summary className="cursor-pointer select-none text-xs text-slate-400 hover:text-slate-200">
                      System prompt
                    </summary>
                    <p className="scrollbar-thin mt-2 max-h-44 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-slate-500">
                      {agent.system_prompt}
                    </p>
                  </details>
                ) : null}

                <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-600">
                  <span>Created {formatDateTime(agent.created_at)}</span>
                  <span>Updated {formatDateTime(agent.updated_at)}</span>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Link
                    href={`/agents/${agent.id}`}
                    className="btn-ghost px-2.5 py-1.5 text-xs"
                  >
                    Details
                    <ArrowRightIcon className="h-3.5 w-3.5" />
                  </Link>
                  <Link
                    href={`/runs?agent_id=${agent.id}`}
                    className="btn-ghost px-2.5 py-1.5 text-xs"
                  >
                    Runs
                  </Link>
                  {agent.is_active ? (
                    <Link
                      href={`/runs?agent_id=${agent.id}&new=1`}
                      className="btn-ghost px-2.5 py-1.5 text-xs"
                    >
                      <PlayIcon className="h-3.5 w-3.5" />
                      Start run
                    </Link>
                  ) : null}
                </div>

                <AgentActions agent={agent} tools={toolOptions} runCount={agentStats?.runs ?? 0} />
              </section>
            );
          })}
        </div>
      )}

      {agents.length > 0 ? (
        <section className="card overflow-hidden">
          <Pagination
            basePath="/agents"
            total={agentsPage.total}
            limit={agentsPage.limit}
            offset={agentsPage.offset}
            query={{ ...params, search, is_active: isActive === undefined ? undefined : String(isActive) }}
          />
        </section>
      ) : null}
    </div>
  );
}
