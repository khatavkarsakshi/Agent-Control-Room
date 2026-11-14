import type { Metadata } from "next";
import Link from "next/link";

import { NewRunPanel } from "@/components/NewRunPanel";
import { RunStatusBadge } from "@/components/StatusBadge";
import { RunsTable } from "@/components/RunsTable";
import { ConnectionError, EmptyState } from "@/components/States";
import { StatCard } from "@/components/StatCard";
import {
  ActivityIcon,
  AgentsIcon,
  ArrowRightIcon,
  CheckCircleIcon,
  CoinsIcon,
  InboxIcon,
  PlayIcon,
  RunsIcon,
  TokensIcon,
} from "@/components/icons";
import { listAgents, listRuns } from "@/lib/api";
import { PUBLIC_API_BASE_URL } from "@/lib/env";
import { toErrorMessage } from "@/lib/errors";
import {
  formatCost,
  formatDuration,
  formatNumber,
  percent,
} from "@/lib/format";
import type { Agent, Page, Run, RunStatus } from "@/types";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Overview" };

const SAMPLE_LIMIT = 200;

export default async function DashboardPage() {
  let agentsPage: Page<Agent>;
  let runsPage: Page<Run>;
  try {
    [agentsPage, runsPage] = await Promise.all([
      listAgents({ limit: 200 }),
      listRuns({ limit: SAMPLE_LIMIT, sort: "-created_at" }),
    ]);
  } catch (error) {
    return (
      <ConnectionError
        title="Could not load the control room"
        message={toErrorMessage(error)}
        apiBaseUrl={PUBLIC_API_BASE_URL}
      />
    );
  }

  const agents = agentsPage.items;
  const runs = runsPage.items;
  const agentNames = Object.fromEntries(agents.map((agent) => [agent.id, agent.name]));
  const agentModels = Object.fromEntries(agents.map((agent) => [agent.id, agent.model_name]));

  const activeRuns = runs.filter((run) => run.status === "pending" || run.status === "running");
  const completedRuns = runs.filter((run) => run.status === "completed");
  const failedRuns = runs.filter((run) => run.status === "failed");
  const terminalRuns = runs.filter(
    (run) => run.status === "completed" || run.status === "failed" || run.status === "replayed",
  );
  const successRate = terminalRuns.length > 0 ? percent(completedRuns.length, terminalRuns.length) : 0;
  const totalCost = runs.reduce((sum, run) => sum + run.total_cost_usd, 0);
  const totalTokens = runs.reduce((sum, run) => sum + run.total_tokens_used, 0);
  const latencies = completedRuns
    .map((run) => run.latency_ms)
    .filter((value): value is number => value !== null);
  const averageLatency =
    latencies.length > 0 ? latencies.reduce((sum, value) => sum + value, 0) / latencies.length : null;

  const byModel = new Map<string, { runs: number; tokens: number; cost: number }>();
  for (const run of runs) {
    const model = agentModels[run.agent_id] ?? "unknown";
    const entry = byModel.get(model) ?? { runs: 0, tokens: 0, cost: 0 };
    entry.runs += 1;
    entry.tokens += run.total_tokens_used;
    entry.cost += run.total_cost_usd;
    byModel.set(model, entry);
  }
  const modelRows = [...byModel.entries()].sort((a, b) => b[1].cost - a[1].cost);
  const highestModelCost = modelRows[0]?.[1].cost ?? 0;

  const statuses: RunStatus[] = ["pending", "running", "completed", "failed", "replayed"];
  const statusCounts = statuses
    .map((status) => ({ status, count: runs.filter((run) => run.status === status).length }))
    .filter((entry) => entry.count > 0);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-accent">
            Control room
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-50">
            Agent observability at a glance
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Live status, token spend and trace health across the latest {SAMPLE_LIMIT} runs. Open a
            run to inspect every step in the waterfall.
          </p>
        </div>
        <Link href="/runs#new-run" className="btn-primary">
          <PlayIcon className="h-4 w-4" />
          Start a run
        </Link>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Active runs"
          value={formatNumber(activeRuns.length)}
          hint={`${formatNumber(runsPage.total)} total runs · ${formatNumber(agents.filter((agent) => agent.is_active).length)} active agents`}
          icon={ActivityIcon}
          tone={activeRuns.length > 0 ? "sky" : "default"}
        />
        <StatCard
          label="Success rate"
          value={`${successRate.toFixed(0)}%`}
          hint={`${formatNumber(completedRuns.length)} completed · ${formatNumber(failedRuns.length)} failed`}
          icon={CheckCircleIcon}
          tone={successRate >= 90 ? "accent" : successRate >= 70 ? "amber" : "rose"}
        />
        <StatCard
          label="Spend (sample)"
          value={formatCost(totalCost)}
          hint={`${formatNumber(totalTokens)} tokens across ${formatNumber(runs.length)} runs`}
          icon={CoinsIcon}
          tone="accent"
        />
        <StatCard
          label="Avg. latency"
          value={averageLatency === null ? "—" : formatDuration(averageLatency)}
          hint={`${formatNumber(agents.length)} registered agents`}
          icon={RunsIcon}
          tone="violet"
        />
      </div>

      <NewRunPanel agents={agents} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <section className="card overflow-hidden">
          <div className="card-header">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">Recent runs</h2>
              <p className="mt-0.5 text-xs text-slate-500">
                Latest {Math.min(runs.length, 8)} of {formatNumber(runsPage.total)}
              </p>
            </div>
            <Link href="/runs" className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
              All runs
              <ArrowRightIcon className="h-3.5 w-3.5" />
            </Link>
          </div>
          {runs.length === 0 ? (
            <EmptyState
              icon={InboxIcon}
              title="No runs yet"
              description="Start a run above, or seed demo data with `python -m scripts.seed_data`."
              action={
                <Link href="/agents" className="btn-ghost">
                  Configure an agent
                </Link>
              }
            />
          ) : (
            <RunsTable runs={runs.slice(0, 8)} agentNames={agentNames} compact />
          )}
        </section>

        <div className="space-y-6">
          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Cost by model</h2>
              <span className="text-[11px] text-slate-600">sample</span>
            </div>
            <div className="card-body space-y-4">
              {modelRows.length === 0 ? (
                <p className="text-sm text-slate-500">No spend recorded yet.</p>
              ) : (
                modelRows.slice(0, 6).map(([model, entry]) => (
                  <div key={model}>
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate font-mono text-xs text-slate-300">{model}</span>
                      <span className="shrink-0 font-mono text-xs text-slate-300">
                        {formatCost(entry.cost)}
                      </span>
                    </div>
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/5">
                      <div
                        className="h-full rounded-full bg-accent/70"
                        style={{ width: `${percent(entry.cost, highestModelCost)}%` }}
                      />
                    </div>
                    <p className="mt-1 text-[11px] text-slate-600">
                      {formatNumber(entry.runs)} runs · {formatNumber(entry.tokens)} tokens
                    </p>
                  </div>
                ))
              )}
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Status mix</h2>
            </div>
            <div className="card-body space-y-2.5">
              {statusCounts.length === 0 ? (
                <p className="text-sm text-slate-500">No runs yet.</p>
              ) : (
                statusCounts.map(({ status, count }) => {
                  return (
                    <div key={status} className="flex items-center justify-between gap-2">
                      <RunStatusBadge status={status} />
                      <span className="text-xs text-slate-400">
                        {formatNumber(count)}{" "}
                        <span className="text-slate-600">({percent(count, runs.length).toFixed(0)}%)</span>
                      </span>
                    </div>
                  );
                })
              )}
            </div>
          </section>
        </div>
      </div>

      <section className="card overflow-hidden">
        <div className="card-header">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">Agent fleet</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              {formatNumber(agents.length)} registered · {formatNumber(agents.filter((agent) => agent.is_active).length)} active
            </p>
          </div>
          <Link href="/agents" className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
            Manage agents
            <ArrowRightIcon className="h-3.5 w-3.5" />
          </Link>
        </div>

        {agents.length === 0 ? (
          <EmptyState
            icon={AgentsIcon}
            title="No agents registered"
            description="Create an agent with a model, system prompt and tools to start executing runs."
            action={
              <Link href="/agents#new-agent" className="btn-primary">
                Create an agent
              </Link>
            }
          />
        ) : (
          <div className="grid gap-px bg-white/5 sm:grid-cols-2 xl:grid-cols-3">
            {agents.slice(0, 6).map((agent) => {
              const agentRuns = runs.filter((run) => run.agent_id === agent.id);
              const agentCost = agentRuns.reduce((sum, run) => sum + run.total_cost_usd, 0);
              return (
                <div key={agent.id} className="bg-ink-900/90 p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <Link
                        href={`/agents#${agent.id}`}
                        className="block truncate text-sm font-medium text-slate-200 hover:text-accent"
                      >
                        {agent.name}
                      </Link>
                      <p className="truncate font-mono text-[11px] text-slate-500">{agent.model_name}</p>
                    </div>
                    <span
                      className={`badge shrink-0 ${
                        agent.is_active
                          ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                          : "border-slate-500/30 bg-slate-500/10 text-slate-400"
                      }`}
                    >
                      {agent.is_active ? "active" : "inactive"}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
                    <span>{formatNumber(agentRuns.length)} runs</span>
                    <span className="inline-flex items-center gap-1">
                      <TokensIcon className="h-3 w-3" />
                      {formatNumber(agentRuns.reduce((sum, run) => sum + run.total_tokens_used, 0))}
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <CoinsIcon className="h-3 w-3" />
                      {formatCost(agentCost)}
                    </span>
                  </div>
                  <div className="mt-3 flex items-center justify-between">
                    <div className="flex flex-wrap gap-1">
                      {agent.tools_config.slice(0, 3).map((tool) => (
                        <span
                          key={tool.name}
                          className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-slate-500"
                        >
                          {tool.name}
                        </span>
                      ))}
                      {agent.tools_config.length > 3 ? (
                        <span className="text-[10px] text-slate-600">
                          +{agent.tools_config.length - 3}
                        </span>
                      ) : null}
                    </div>
                    <Link
                      href={`/runs?agent_id=${agent.id}`}
                      className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
                    >
                      Runs
                      <ArrowRightIcon className="h-3 w-3" />
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
