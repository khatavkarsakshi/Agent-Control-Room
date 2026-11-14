import Link from "next/link";

import { RunStatusBadge } from "@/components/StatusBadge";
import {
  formatCost,
  formatDateTime,
  formatDuration,
  formatNumber,
  shortId,
  truncate,
} from "@/lib/format";
import type { Run } from "@/types";

interface RunsTableProps {
  runs: Run[];
  agentNames: Record<string, string>;
  compact?: boolean;
}

export function RunsTable({ runs, agentNames, compact = false }: RunsTableProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-white/5">
            <th className="table-head px-5 py-2.5">Run</th>
            <th className="table-head px-3 py-2.5">Agent</th>
            <th className="table-head px-3 py-2.5">Status</th>
            <th className="table-head px-3 py-2.5 text-right">Tokens</th>
            <th className="table-head px-3 py-2.5 text-right">Cost</th>
            {compact ? null : <th className="table-head px-3 py-2.5 text-right">Latency</th>}
            <th className="table-head px-5 py-2.5 text-right">Created</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {runs.map((run) => {
            const agentName = agentNames[run.agent_id] ?? shortId(run.agent_id);
            return (
              <tr key={run.id} className="group transition-colors hover:bg-white/[0.025]">
                <td className="px-5 py-3">
                  <Link href={`/runs/${run.id}`} className="block max-w-[26rem]">
                    <span className="block truncate font-medium text-slate-200 group-hover:text-accent">
                      {truncate(run.input_prompt, 96)}
                    </span>
                    <span className="mt-0.5 flex items-center gap-2 font-mono text-[11px] text-slate-600">
                      {shortId(run.id, 10)}
                      {run.replay_of_run_id ? (
                        <span className="rounded bg-violet-400/10 px-1.5 py-0.5 text-[10px] text-violet-300">
                          replay
                        </span>
                      ) : null}
                    </span>
                  </Link>
                </td>
                <td className="px-3 py-3">
                  <Link
                    href={`/runs?agent_id=${run.agent_id}`}
                    className="text-slate-400 hover:text-accent"
                  >
                    {agentName}
                  </Link>
                </td>
                <td className="px-3 py-3">
                  <RunStatusBadge status={run.status} />
                </td>
                <td className="px-3 py-3 text-right font-mono text-xs text-slate-400">
                  {formatNumber(run.total_tokens_used)}
                </td>
                <td className="px-3 py-3 text-right font-mono text-xs text-slate-300">
                  {formatCost(run.total_cost_usd)}
                </td>
                {compact ? null : (
                  <td className="px-3 py-3 text-right font-mono text-xs text-slate-400">
                    {formatDuration(run.latency_ms)}
                  </td>
                )}
                <td className="whitespace-nowrap px-5 py-3 text-right text-xs text-slate-500">
                  {formatDateTime(run.created_at)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
