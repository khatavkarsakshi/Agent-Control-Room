"use client";

import { useRouter } from "next/navigation";
import { useTransition, type FormEvent } from "react";

import { SearchIcon } from "@/components/icons";
import type { Agent, RunStatus } from "@/types";

const STATUS_OPTIONS: Array<{ value: RunStatus | ""; label: string }> = [
  { value: "", label: "All statuses" },
  { value: "pending", label: "Pending" },
  { value: "running", label: "Running" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
  { value: "replayed", label: "Replayed" },
];

const SORT_OPTIONS = [
  { value: "-created_at", label: "Newest first" },
  { value: "created_at", label: "Oldest first" },
  { value: "-total_cost_usd", label: "Highest cost" },
  { value: "total_cost_usd", label: "Lowest cost" },
  { value: "-latency_ms", label: "Slowest first" },
  { value: "latency_ms", label: "Fastest first" },
  { value: "-total_tokens_used", label: "Most tokens" },
  { value: "total_tokens_used", label: "Fewest tokens" },
];

export interface RunFilterValues {
  search?: string;
  agentId?: string;
  status?: string;
  sort?: string;
  minCost?: string;
  maxCost?: string;
  dateFrom?: string;
  dateTo?: string;
}

function toDateTimeLocal(value: string | undefined): string {
  if (!value) {
    return "";
  }
  return value.slice(0, 16);
}

export function RunFilters({ agents, values }: { agents: Agent[]; values: RunFilterValues }) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const params = new URLSearchParams();
    const keys = [
      "search",
      "agent_id",
      "status",
      "sort",
      "min_cost",
      "max_cost",
      "date_from",
      "date_to",
    ] as const;

    for (const key of keys) {
      const raw = data.get(key);
      if (typeof raw !== "string") {
        continue;
      }
      const value = raw.trim();
      if (!value) {
        continue;
      }
      params.set(key, value);
    }

    startTransition(() => {
      const query = params.toString();
      router.push(query ? `/runs?${query}` : "/runs");
    });
  };

  const activeCount = Object.values(values).filter((value) => Boolean(value)).length;

  return (
    <form onSubmit={onSubmit} className="card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-200">Filters</h2>
        <div className="flex items-center gap-3">
          {activeCount > 0 ? (
            <button
              type="button"
              className="text-xs text-slate-400 hover:text-slate-200"
              onClick={() => startTransition(() => router.push("/runs"))}
            >
              Clear ({activeCount})
            </button>
          ) : null}
          <span className="text-[11px] text-slate-600">
            {isPending ? "Applying…" : "Press enter to apply"}
          </span>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div>
          <label htmlFor="filter-search" className="label">
            Search prompt / output
          </label>
          <div className="relative">
            <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              id="filter-search"
              name="search"
              defaultValue={values.search ?? ""}
              className="input pl-9"
              placeholder="Error text, prompt…"
              maxLength={200}
            />
          </div>
        </div>

        <div>
          <label htmlFor="filter-agent" className="label">
            Agent
          </label>
          <select id="filter-agent" name="agent_id" defaultValue={values.agentId ?? ""} className="input">
            <option value="">All agents</option>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="filter-status" className="label">
            Status
          </label>
          <select id="filter-status" name="status" defaultValue={values.status ?? ""} className="input">
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="filter-sort" className="label">
            Sort
          </label>
          <select id="filter-sort" name="sort" defaultValue={values.sort ?? "-created_at"} className="input">
            {SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="filter-from" className="label">
            Created after (UTC)
          </label>
          <input
            id="filter-from"
            name="date_from"
            type="datetime-local"
            defaultValue={toDateTimeLocal(values.dateFrom)}
            className="input"
          />
        </div>

        <div>
          <label htmlFor="filter-to" className="label">
            Created before (UTC)
          </label>
          <input
            id="filter-to"
            name="date_to"
            type="datetime-local"
            defaultValue={toDateTimeLocal(values.dateTo)}
            className="input"
          />
        </div>

        <div>
          <label htmlFor="filter-min-cost" className="label">
            Min cost (USD)
          </label>
          <input
            id="filter-min-cost"
            name="min_cost"
            type="number"
            min="0"
            step="0.000001"
            defaultValue={values.minCost ?? ""}
            className="input"
            placeholder="0.00"
          />
        </div>

        <div>
          <label htmlFor="filter-max-cost" className="label">
            Max cost (USD)
          </label>
          <input
            id="filter-max-cost"
            name="max_cost"
            type="number"
            min="0"
            step="0.000001"
            defaultValue={values.maxCost ?? ""}
            className="input"
            placeholder="1.00"
          />
        </div>
      </div>

      <div className="mt-3 flex justify-end">
        <button type="submit" className="btn-primary" disabled={isPending}>
          {isPending ? "Applying…" : "Apply filters"}
        </button>
      </div>
    </form>
  );
}
