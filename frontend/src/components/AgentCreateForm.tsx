"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ChevronDownIcon, PlusIcon } from "@/components/icons";
import { ToolPicker, type ToolOption } from "@/components/ToolPicker";
import { createAgent } from "@/lib/client";
import { toErrorMessage } from "@/lib/errors";
import { MODEL_EXAMPLES, MODEL_FIELD_HINT } from "@/lib/models";
import type { Agent } from "@/types";

interface AgentCreateFormProps {
  tools: ToolOption[];
  defaultOpen?: boolean;
}

export function AgentCreateForm({ tools, defaultOpen = false }: AgentCreateFormProps) {
  const router = useRouter();
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formKey, setFormKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Agent | null>(null);

  const onSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setError(null);
    const form = event.currentTarget;
    const data = new FormData(form);

    const name = String(data.get("name") ?? "").trim();
    const modelName = String(data.get("model_name") ?? "").trim();
    if (!name || !modelName) {
      setError("Name and model are required.");
      return;
    }

    const description = String(data.get("description") ?? "").trim();
    const systemPrompt = String(data.get("system_prompt") ?? "").trim();
    const selectedTools = data
      .getAll("tools")
      .filter((value): value is string => typeof value === "string");

    setIsSubmitting(true);
    try {
      const agent = await createAgent({
        name,
        model_name: modelName,
        description: description || null,
        system_prompt: systemPrompt || null,
        tools_config: selectedTools,
        is_active: data.get("is_active") === "on",
      });
      form.reset();
      // Remount the form so the controlled tool picker clears its selection.
      setFormKey((value) => value + 1);
      setCreated(agent);
      setIsOpen(false);
      router.refresh();
    } catch (cause) {
      setError(toErrorMessage(cause));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section id="new-agent" className="card overflow-hidden">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left"
        onClick={() => setIsOpen((value) => !value)}
        aria-expanded={isOpen}
      >
        <span className="flex items-center gap-3">
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-accent/10 text-accent">
            <PlusIcon className="h-4 w-4" />
          </span>
          <span>
            <span className="block text-sm font-semibold text-slate-100">Register an agent</span>
            <span className="block text-xs text-slate-500">
              A name, a model, an optional system prompt and the tools it may call.
            </span>
          </span>
        </span>
        <ChevronDownIcon
          className={`h-4 w-4 shrink-0 text-slate-500 transition-transform ${isOpen ? "rotate-180" : ""}`}
        />
      </button>

      {created && !isOpen ? (
        <div className="border-t border-white/5 px-5 py-3 text-sm text-emerald-200">
          Agent{" "}
          <a href={`#${created.id}`} className="link font-medium">
            {created.name}
          </a>{" "}
          was created.
        </div>
      ) : null}

      {isOpen ? (
        <form
          key={formKey}
          onSubmit={(event) => void onSubmit(event)}
          className="border-t border-white/5 px-5 py-4"
        >
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <label htmlFor="agent-name" className="label">
                Name
              </label>
              <input
                id="agent-name"
                name="name"
                className="input"
                placeholder="Research assistant"
                maxLength={120}
                pattern="[A-Za-z0-9][A-Za-z0-9 ._\-()/]*"
                title="Start with a letter or digit; letters, digits, spaces and . _ - ( ) / are allowed."
                required
              />
            </div>
            <div>
              <label htmlFor="agent-model" className="label">
                Model
              </label>
              <input
                id="agent-model"
                name="model_name"
                className="input font-mono"
                placeholder="openai/gpt-4.1-mini"
                maxLength={120}
                list="create-agent-models"
                required
              />
              <datalist id="create-agent-models">
                {MODEL_EXAMPLES.map((model) => (
                  <option key={model} value={model} />
                ))}
              </datalist>
              <p className="mt-1 text-[11px] leading-snug text-slate-600">{MODEL_FIELD_HINT}</p>
            </div>
          </div>

          <div className="mt-3">
            <label htmlFor="agent-description" className="label">
              Description (optional)
            </label>
            <input
              id="agent-description"
              name="description"
              className="input"
              placeholder="What this agent is for"
              maxLength={5_000}
            />
          </div>

          <div className="mt-3">
            <label htmlFor="agent-system-prompt" className="label">
              System prompt (optional)
            </label>
            <textarea
              id="agent-system-prompt"
              name="system_prompt"
              className="input min-h-[112px] resize-y font-mono text-xs"
              placeholder="You are a careful research assistant. Use tools when they help, and cite your sources."
              maxLength={100_000}
            />
          </div>

          <div className="mt-4">
            <ToolPicker tools={tools} disabled={isSubmitting} />
          </div>

          <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              name="is_active"
              defaultChecked
              className="h-4 w-4 rounded border-white/20 bg-ink-950 text-accent focus:ring-accent/40"
            />
            Active — allow runs to be started against this agent
          </label>

          {error ? (
            <p className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-sm text-rose-200">
              {error}
            </p>
          ) : null}

          <div className="mt-4 flex items-center justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => setIsOpen(false)}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={isSubmitting}>
              <PlusIcon className="h-4 w-4" />
              {isSubmitting ? "Creating…" : "Create agent"}
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}
