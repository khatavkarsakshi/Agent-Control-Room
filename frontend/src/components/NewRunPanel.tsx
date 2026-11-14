"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { ChevronDownIcon, PlayIcon } from "@/components/icons";
import { createRun } from "@/lib/client";
import { toErrorMessage } from "@/lib/errors";
import type { Agent, ExecutionOptions } from "@/types";

interface NewRunPanelProps {
  agents: Agent[];
  defaultAgentId?: string;
  open?: boolean;
}

function optionalNumber(data: FormData, key: string): number | undefined {
  const raw = data.get(key);
  if (typeof raw !== "string" || raw.trim() === "") {
    return undefined;
  }
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function NewRunPanel({ agents, defaultAgentId, open = false }: NewRunPanelProps) {
  const router = useRouter();
  const [isOpen, setIsOpen] = useState(open);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeAgents = agents.filter((agent) => agent.is_active);
  const initialAgent =
    defaultAgentId && activeAgents.some((agent) => agent.id === defaultAgentId)
      ? defaultAgentId
      : (activeAgents[0]?.id ?? "");

  // The panel can be opened from a link (`?new=1` or the `#new-run` anchor)
  // without remounting the component.
  useEffect(() => {
    if (open) {
      setIsOpen(true);
    }
  }, [open]);

  useEffect(() => {
    const syncFromHash = (): void => {
      if (window.location.hash === "#new-run") {
        setIsOpen(true);
      }
    };
    syncFromHash();
    window.addEventListener("hashchange", syncFromHash);
    return () => window.removeEventListener("hashchange", syncFromHash);
  }, []);

  const onSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setError(null);
    const form = event.currentTarget;
    const data = new FormData(form);
    const agentId = String(data.get("agent_id") ?? "");
    const prompt = String(data.get("input_prompt") ?? "").trim();

    if (!agentId) {
      setError("Select an active agent.");
      return;
    }
    if (!prompt) {
      setError("Enter a prompt for the run.");
      return;
    }

    const execution: ExecutionOptions = {};
    const model = String(data.get("model_name") ?? "").trim();
    if (model) {
      execution.model_name = model;
    }
    const temperature = optionalNumber(data, "temperature");
    if (temperature !== undefined) {
      execution.temperature = temperature;
    }
    const maxIterations = optionalNumber(data, "max_iterations");
    if (maxIterations !== undefined) {
      execution.max_iterations = maxIterations;
    }
    const maxTokens = optionalNumber(data, "max_tokens");
    if (maxTokens !== undefined) {
      execution.max_tokens = maxTokens;
    }
    const timeout = optionalNumber(data, "timeout_seconds");
    if (timeout !== undefined) {
      execution.timeout_seconds = timeout;
    }

    setIsSubmitting(true);
    try {
      const run = await createRun({
        agent_id: agentId,
        input_prompt: prompt,
        ...(Object.keys(execution).length > 0 ? { execution_options: execution } : {}),
      });
      form.reset();
      router.push(`/runs/${run.id}`);
    } catch (cause) {
      setError(toErrorMessage(cause));
      setIsSubmitting(false);
    }
  };

  return (
    <section id="new-run" className="card overflow-hidden">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left"
        onClick={() => setIsOpen((value) => !value)}
        aria-expanded={isOpen}
      >
        <span className="flex items-center gap-3">
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-accent/10 text-accent">
            <PlayIcon className="h-4 w-4" />
          </span>
          <span>
            <span className="block text-sm font-semibold text-slate-100">Start a run</span>
            <span className="block text-xs text-slate-500">
              Enqueue an agent with the current configuration.
            </span>
          </span>
        </span>
        <ChevronDownIcon
          className={`h-4 w-4 shrink-0 text-slate-500 transition-transform ${isOpen ? "rotate-180" : ""}`}
        />
      </button>

      {isOpen ? (
        <form onSubmit={(event) => void onSubmit(event)} className="border-t border-white/5 px-5 py-4">
          {activeAgents.length === 0 ? (
            <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-sm text-amber-200">
              No active agents yet.{" "}
              <Link href="/agents" className="link">
                Create or activate an agent
              </Link>{" "}
              before starting a run.
            </div>
          ) : null}

          <div className="grid gap-3 lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
            <div className="space-y-3">
              <div>
                <label htmlFor="run-agent" className="label">
                  Agent
                </label>
                <select
                  id="run-agent"
                  name="agent_id"
                  className="input"
                  defaultValue={initialAgent}
                  disabled={activeAgents.length === 0}
                  required
                >
                  {activeAgents.length === 0 ? <option value="">No active agents</option> : null}
                  {activeAgents.map((agent) => (
                    <option key={agent.id} value={agent.id}>
                      {agent.name} · {agent.model_name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="run-model" className="label">
                  Model override (optional)
                </label>
                <input
                  id="run-model"
                  name="model_name"
                  className="input"
                  placeholder="Use the agent default"
                  maxLength={120}
                />
              </div>
            </div>

            <div>
              <label htmlFor="run-prompt" className="label">
                Prompt
              </label>
              <textarea
                id="run-prompt"
                name="input_prompt"
                className="input min-h-[132px] resize-y"
                placeholder="What should the agent do?"
                maxLength={20_000}
                required
              />
            </div>
          </div>

          <details className="mt-3 rounded-lg border border-white/5 bg-ink-950/40">
            <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium text-slate-400 hover:text-slate-200">
              Advanced execution options
            </summary>
            <div className="grid gap-3 border-t border-white/5 px-3 py-3 sm:grid-cols-2 xl:grid-cols-4">
              <div>
                <label htmlFor="run-temperature" className="label">
                  Temperature
                </label>
                <input
                  id="run-temperature"
                  name="temperature"
                  type="number"
                  min="0"
                  max="2"
                  step="0.1"
                  className="input"
                  placeholder="Default"
                />
              </div>
              <div>
                <label htmlFor="run-iterations" className="label">
                  Max iterations
                </label>
                <input
                  id="run-iterations"
                  name="max_iterations"
                  type="number"
                  min="1"
                  max="50"
                  step="1"
                  className="input"
                  placeholder="Default"
                />
              </div>
              <div>
                <label htmlFor="run-tokens" className="label">
                  Max output tokens
                </label>
                <input
                  id="run-tokens"
                  name="max_tokens"
                  type="number"
                  min="1"
                  max="32000"
                  step="1"
                  className="input"
                  placeholder="Default"
                />
              </div>
              <div>
                <label htmlFor="run-timeout" className="label">
                  Timeout (seconds)
                </label>
                <input
                  id="run-timeout"
                  name="timeout_seconds"
                  type="number"
                  min="1"
                  max="3600"
                  step="1"
                  className="input"
                  placeholder="Default"
                />
              </div>
            </div>
          </details>

          {error ? (
            <p className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-sm text-rose-200">
              {error}
            </p>
          ) : null}

          <div className="mt-4 flex items-center justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => setIsOpen(false)}>
              Close
            </button>
            <button type="submit" className="btn-primary" disabled={isSubmitting || activeAgents.length === 0}>
              <PlayIcon className="h-4 w-4" />
              {isSubmitting ? "Enqueuing…" : "Start run"}
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
