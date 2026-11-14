import type { Metadata } from "next";
import Link from "next/link";

import { AutoRefresh } from "@/components/AutoRefresh";
import { InboxIcon } from "@/components/icons";
import { NewRunPanel } from "@/components/NewRunPanel";
import { Pagination } from "@/components/Pagination";
import { RunFilters, type RunFilterValues } from "@/components/RunFilters";
import { RunsTable } from "@/components/RunsTable";
import { ConnectionError, EmptyState } from "@/components/States";
import { listAgents, listRuns } from "@/lib/api";
import { PUBLIC_API_BASE_URL } from "@/lib/env";
import { toErrorMessage } from "@/lib/errors";
import type { Agent, Page, Run, RunStatus } from "@/types";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Runs" };

const STATUSES: RunStatus[] = ["pending", "running", "completed", "failed", "replayed"];

const SORTS = new Set([
  "created_at",
  "-created_at",
  "total_cost_usd",
  "-total_cost_usd",
  "latency_ms",
  "-latency_ms",
  "total_tokens_used",
  "-total_tokens_used",
]);

type SearchParams = Record<string, string | string[] | undefined>;

interface RunsPageProps {
  searchParams: Promise<SearchParams>;
}

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function numberParam(value: string | undefined): number | undefined {
  if (value === undefined || value.trim() === "") {
    return undefined;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
}

function positiveInt(value: string | undefined, fallback: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return fallback;
  }
  return Math.min(parsed, max);
}

export default async function RunsPage({ searchParams }: RunsPageProps) {
  const params = await searchParams;
  const statusParam = first(params.status);
  const status = statusParam && STATUSES.includes(statusParam as RunStatus) ? (statusParam as RunStatus) : undefined;
  const sortParam = first(params.sort);
  const sort = sortParam && SORTS.has(sortParam) ? sortParam : "-created_at";
  const limit = positiveInt(first(params.limit), 25, 200);
  const offset = Math.max(Number(first(params.offset) ?? 0) || 0, 0);
  const agentId = first(params.agent_id);
  const search = first(params.search);
  const dateFrom = first(params.date_from);
  const dateTo = first(params.date_to);
  const minCost = numberParam(first(params.min_cost));
  const maxCost = numberParam(first(params.max_cost));

  let agentsPage: Page<Agent>;
  let runsPage: Page<Run>;
  try {
    [agentsPage, runsPage] = await Promise.all([
      listAgents({ limit: 200 }),
      listRuns({
        agent_id: agentId,
        status,
        search,
        date_from: dateFrom,
        date_to: dateTo,
        min_cost: minCost,
        max_cost: maxCost,
        sort,
        limit,
        offset,
      }),
    ]);
  } catch (error) {
    return (
      <ConnectionError
        title="Could not load runs"
        message={toErrorMessage(error)}
        apiBaseUrl={PUBLIC_API_BASE_URL}
      />
    );
  }

  const agentNames = Object.fromEntries(agentsPage.items.map((agent) => [agent.id, agent.name]));
  const hasActiveRuns = runsPage.items.some(
    (run) => run.status === "pending" || run.status === "running",
  );
  const filterValues: RunFilterValues = {
    search,
    agentId,
    status,
    sort: first(params.sort),
    minCost: first(params.min_cost),
    maxCost: first(params.max_cost),
    dateFrom,
    dateTo,
  };
  const activeFilterCount = Object.values(filterValues).filter(Boolean).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-50">Runs</h1>
          <p className="mt-1 text-sm text-slate-500">
            {runsPage.total.toLocaleString()} run{runsPage.total === 1 ? "" : "s"} match the current
            filters{activeFilterCount > 0 ? ` · ${activeFilterCount} active` : ""}.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <AutoRefresh enabled={hasActiveRuns} />
          <Link href="#new-run" className="btn-primary">
            Start a run
          </Link>
        </div>
      </div>

      <NewRunPanel
        agents={agentsPage.items}
        defaultAgentId={agentId}
        open={first(params.new) === "1"}
      />

      <RunFilters agents={agentsPage.items} values={filterValues} />

      <section className="card overflow-hidden">
        {runsPage.items.length === 0 ? (
          <EmptyState
            icon={InboxIcon}
            title="No runs match these filters"
            description="Adjust the filters above, or start a new run with one of your agents."
            action={
              <Link href="/runs" className="btn-ghost">
                Clear filters
              </Link>
            }
          />
        ) : (
          <>
            <RunsTable runs={runsPage.items} agentNames={agentNames} />
            <Pagination
              basePath="/runs"
              total={runsPage.total}
              limit={runsPage.limit}
              offset={runsPage.offset}
              query={{
                ...params,
                status,
                sort,
                limit: String(limit),
              }}
            />
          </>
        )}
      </section>
    </div>
  );
}
