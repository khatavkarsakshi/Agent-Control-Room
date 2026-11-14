import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AgentActions } from "@/components/AgentActions";
import { CopyButton } from "@/components/CopyButton";
import { JsonBlock } from "@/components/JsonBlock";
import { NewRunPanel } from "@/components/NewRunPanel";
import { RunsTable } from "@/components/RunsTable";
import { ConnectionError } from "@/components/States";
import { StatCard } from "@/components/StatCard";
import {
  ActivityIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  ClockIcon,
  CoinsIcon,
  PlayIcon,
  TokensIcon,
} from "@/components/icons";
import { getAgent, listRuns, listTools } from "@/lib/api";
import { PUBLIC_API_BASE_URL } from "@/lib/env";
import { isNotFound, toErrorMessage } from "@/lib/errors";
import { formatCost, formatDateTime, formatDuration, formatNumber, shortId } from "@/lib/format";
import type { Agent, Page, Run } from "@/types";

export const dynamic = "force-dynamic";

const RUN_SAMPLE_LIMIT = 200;

interface AgentPageProps {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: AgentPageProps): Promise<Metadata> {
  const { id } = await params;
  try {
    const agent = await getAgent(id);
    return { title: agent.name };
  } catch {
    return { title: `Agent ${shortId(id, 10)}` };
  }
}

export default async function AgentDetailPage({ params }: AgentPageProps) {
  const { id } = await params;

  let agent: Agent;
  try {
    agent = await getAgent(id);
  } catch (error) {
    if (isNotFound(error)) {
      notFound();
    }
    return (
      <ConnectionError
        title="Could not load this agent"
        message={toErrorMessage(error)}
        apiBaseUrl={PUBLIC_API_BASE_URL}
      />
    );
  }

  let runsPage: Page<Run>;
  let tools: Awaited<ReturnType<typeof listTools>>;
  try {
    [runsPage, tools] = await Promise.all([
      listRuns({ agent_id: agent.id, limit: RUN_SAMPLE_LIMIT, sort: "-created_at" }),
      listTools(),
    ]);
  } catch (error) {
    return (
      <ConnectionError
        title="Could not load this agent's runs"
        message={toErrorMessage(error)}
        apiBaseUrl={PUBLIC_API_BASE_URL}
      />
    );
  }

  const runs = runsPage.items;
  const activeRuns = runs.filter((run) => run.status === "pending" || run.status === "running");
  const completedRuns = runs.filter((run) => run.status === "completed");
  const totalTokens = runs.reduce((sum, run) => sum + run.total_tokens_used, 0);
  const totalCost = runs.reduce((sum, run) => sum + run.total_cost_usd, 0);
  const latencies = completedRuns
    .map((run) => run.latency_ms)
    .filter((value): value is number => value !== null);
  const averageLatency =
    latencies.length > 0 ? latencies.reduce((sum, value) => sum + value, 0) / latencies.length : null;
  const toolOptions = tools.map((tool) => ({ name: tool.name, description: tool.description }));
  const knownTools = new Map(tools.map((tool) => [tool.name, tool]));

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/agents"
          className="inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300"
        >
          <ArrowLeftIcon className="h-3.5 w-3.5" />
          Back to agents
        </Link>

        <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="truncate text-xl font-semibold tracking-tight text-slate-50">
                {agent.name}
              </h1>
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
              {activeRuns.length > 0 ? (
                <span className="inline-flex items-center gap-1 text-[11px] text-sky-300">
                  <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-sky-400" />
                  {activeRuns.length} running
                </span>
              ) : null}
            </div>
            <p className="mt-1 text-sm text-slate-500">
              <span className="font-mono text-slate-400">{agent.model_name}</span> ·{" "}
              {agent.tools_config.length} tool{agent.tools_config.length === 1 ? "" : "s"} · created{" "}
              {formatDateTime(agent.created_at)}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/runs?agent_id=${agent.id}`} className="btn-ghost">
              All runs
              <ArrowRightIcon className="h-4 w-4" />
            </Link>
            <Link href="#new-run" className="btn-primary">
              <PlayIcon className="h-4 w-4" />
              Start a run
            </Link>
          </div>
        </div>
      </div>

      {agent.description ? (
        <p className="max-w-3xl text-sm leading-relaxed text-slate-400">{agent.description}</p>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Runs"
          value={formatNumber(runsPage.total)}
          hint={`${formatNumber(activeRuns.length)} active · latest ${formatNumber(runs.length)} sampled`}
          icon={ActivityIcon}
          tone={activeRuns.length > 0 ? "sky" : "default"}
        />
        <StatCard
          label="Tokens (sample)"
          value={formatNumber(totalTokens)}
          hint={`across ${formatNumber(runs.length)} recent runs`}
          icon={TokensIcon}
          tone="violet"
        />
        <StatCard
          label="Cost (sample)"
          value={formatCost(totalCost)}
          hint={
            runs.length > 0
              ? `${formatCost(totalCost / runs.length)} average per run`
              : "No spend recorded"
          }
          icon={CoinsIcon}
          tone="accent"
        />
        <StatCard
          label="Avg. latency"
          value={averageLatency === null ? "—" : formatDuration(averageLatency)}
          hint={`${formatNumber(completedRuns.length)} completed runs`}
          icon={ClockIcon}
          tone="amber"
        />
      </div>

      <NewRunPanel agents={[agent]} defaultAgentId={agent.id} open={agent.is_active} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <section className="card overflow-hidden">
          <div className="card-header">
            <div>
              <h2 className="text-sm font-semibold text-slate-100">Recent runs</h2>
              <p className="mt-0.5 text-xs text-slate-500">
                Latest {Math.min(runs.length, 10)} of {formatNumber(runsPage.total)}
              </p>
            </div>
            <Link
              href={`/runs?agent_id=${agent.id}`}
              className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
            >
              All runs
              <ArrowRightIcon className="h-3.5 w-3.5" />
            </Link>
          </div>
          {runs.length === 0 ? (
            <div className="px-5 py-10 text-center text-sm text-slate-500">
              No runs yet. Start one above to see traces, tokens and cost.
            </div>
          ) : (
            <RunsTable
              runs={runs.slice(0, 10)}
              agentNames={{ [agent.id]: agent.name }}
              compact
            />
          )}
        </section>

        <aside className="space-y-4">
          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Configuration</h2>
            </div>
            <div className="card-body space-y-3">
              <div>
                <p className="stat-label">Agent ID</p>
                <div className="mt-1 flex items-center gap-1.5">
                  <code className="truncate font-mono text-[11px] text-slate-400">{agent.id}</code>
                  <CopyButton value={agent.id} label="" />
                </div>
              </div>
              <div>
                <p className="stat-label">Model</p>
                <p className="mt-1 font-mono text-xs text-slate-300">{agent.model_name}</p>
              </div>
              <div>
                <p className="stat-label">Updated</p>
                <p className="mt-1 text-xs text-slate-300">{formatDateTime(agent.updated_at)}</p>
              </div>
              <div>
                <p className="stat-label mb-1.5">Tools</p>
                {agent.tools_config.length === 0 ? (
                  <p className="text-xs text-slate-600">No tools configured.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {agent.tools_config.map((tool) => (
                      <li
                        key={tool.name}
                        className="rounded-lg border border-white/5 bg-ink-950/40 px-2.5 py-2"
                      >
                        <p className="font-mono text-[11px] text-slate-300">{tool.name}</p>
                        <p className="mt-0.5 text-[11px] leading-snug text-slate-600">
                          {knownTools.get(tool.name)?.description ?? "Unknown tool"}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-header">
              <h2 className="text-sm font-semibold text-slate-100">Manage</h2>
            </div>
            <div className="px-5 pb-5">
              <AgentActions agent={agent} tools={toolOptions} runCount={runsPage.total} />
            </div>
          </section>
        </aside>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <section className="card">
          <div className="card-header">
            <h2 className="text-sm font-semibold text-slate-100">System prompt</h2>
            {agent.system_prompt ? <CopyButton value={agent.system_prompt} /> : null}
          </div>
          <div className="card-body">
            {agent.system_prompt ? (
              <p className="scrollbar-thin max-h-80 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-slate-300">
                {agent.system_prompt}
              </p>
            ) : (
              <p className="text-sm text-slate-500">No system prompt configured.</p>
            )}
          </div>
        </section>

        <section className="card">
          <div className="card-header">
            <h2 className="text-sm font-semibold text-slate-100">Tool configuration</h2>
            <span className="text-[11px] text-slate-600">
              {agent.tools_config.length} entr{agent.tools_config.length === 1 ? "y" : "ies"}
            </span>
          </div>
          <div className="card-body">
            <JsonBlock
              value={agent.tools_config.length > 0 ? agent.tools_config : null}
              emptyLabel="No tools configured"
              maxHeightClassName="max-h-80"
            />
          </div>
        </section>
      </div>
    </div>
  );
}
