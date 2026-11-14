"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import {
  DuplicateIcon,
  PencilIcon,
  PowerIcon,
  TrashIcon,
} from "@/components/icons";
import { ToolPicker, type ToolOption } from "@/components/ToolPicker";
import { cloneAgent, deleteAgent, updateAgent } from "@/lib/client";
import { toErrorMessage } from "@/lib/errors";
import { MODEL_EXAMPLES, MODEL_FIELD_HINT } from "@/lib/models";
import type { Agent } from "@/types";

type Mode = "idle" | "edit" | "delete";

interface AgentActionsProps {
  agent: Agent;
  tools: ToolOption[];
  runCount: number;
}

/**
 * Client controls for a single agent card: edit the configuration, clone it,
 * toggle activation and (when it has no runs) delete it permanently.
 */
export function AgentActions({ agent, tools, runCount }: AgentActionsProps) {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("idle");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const run = async (label: string, action: () => Promise<string | void>): Promise<void> => {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      const message = await action();
      if (typeof message === "string") {
        setNotice(message);
      }
      router.refresh();
    } catch (cause) {
      setError(toErrorMessage(cause));
    } finally {
      setBusy(null);
    }
  };

  const onEdit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
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

    await run("Saving changes…", async () => {
      await updateAgent(agent.id, {
        name,
        model_name: modelName,
        description: description || null,
        system_prompt: systemPrompt || null,
        tools_config: selectedTools,
        is_active: data.get("is_active") === "on",
      });
      setMode("idle");
      return "Agent configuration saved.";
    });
  };

  const toggleActive = async (): Promise<void> => {
    const next = !agent.is_active;
    await run(next ? "Activating…" : "Deactivating…", async () => {
      await updateAgent(agent.id, { is_active: next });
      return next ? "Agent activated." : "Agent deactivated.";
    });
  };

  const onClone = async (): Promise<void> => {
    await run("Cloning…", async () => {
      const clone = await cloneAgent(agent.id);
      return `Cloned as “${clone.name}”.`;
    });
  };

  const onDeactivate = async (): Promise<void> => {
    await run("Deactivating…", async () => {
      await deleteAgent(agent.id, false);
      setMode("idle");
      return "Agent deactivated. Run history is preserved.";
    });
  };

  const onHardDelete = async (): Promise<void> => {
    await run("Deleting…", async () => {
      await deleteAgent(agent.id, true);
      setMode("idle");
      return "Agent permanently deleted.";
    });
  };

  return (
    <div className="mt-4 border-t border-white/5 pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-ghost px-2.5 py-1.5 text-xs"
          onClick={() => setMode((current) => (current === "edit" ? "idle" : "edit"))}
          aria-expanded={mode === "edit"}
          disabled={busy !== null}
        >
          <PencilIcon className="h-3.5 w-3.5" />
          Edit
        </button>
        <button
          type="button"
          className="btn-ghost px-2.5 py-1.5 text-xs"
          onClick={() => void onClone()}
          disabled={busy !== null}
        >
          <DuplicateIcon className="h-3.5 w-3.5" />
          Clone
        </button>
        <button
          type="button"
          className="btn-ghost px-2.5 py-1.5 text-xs"
          onClick={() => void toggleActive()}
          disabled={busy !== null}
        >
          <PowerIcon className="h-3.5 w-3.5" />
          {agent.is_active ? "Deactivate" : "Activate"}
        </button>
        <button
          type="button"
          className="btn-ghost px-2.5 py-1.5 text-xs text-rose-300 hover:border-rose-400/30 hover:bg-rose-400/10"
          onClick={() => setMode((current) => (current === "delete" ? "idle" : "delete"))}
          aria-expanded={mode === "delete"}
          disabled={busy !== null}
        >
          <TrashIcon className="h-3.5 w-3.5" />
          Delete
        </button>
        {busy ? <span className="text-[11px] text-slate-400">{busy}</span> : null}
      </div>

      {notice ? (
        <p className="mt-2 rounded-lg border border-emerald-400/20 bg-emerald-400/5 px-3 py-2 text-xs text-emerald-200">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p className="mt-2 rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-xs text-rose-200">
          {error}
        </p>
      ) : null}

      {mode === "edit" ? (
        <form onSubmit={(event) => void onEdit(event)} className="mt-3 space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={`edit-name-${agent.id}`} className="label">
                Name
              </label>
              <input
                id={`edit-name-${agent.id}`}
                name="name"
                className="input"
                defaultValue={agent.name}
                maxLength={120}
                pattern="[A-Za-z0-9][A-Za-z0-9 ._\-()/]*"
                required
              />
            </div>
            <div>
              <label htmlFor={`edit-model-${agent.id}`} className="label">
                Model
              </label>
              <input
                id={`edit-model-${agent.id}`}
                name="model_name"
                className="input font-mono"
                defaultValue={agent.model_name}
                maxLength={120}
                list={`edit-agent-models-${agent.id}`}
                required
              />
              <datalist id={`edit-agent-models-${agent.id}`}>
                {MODEL_EXAMPLES.map((model) => (
                  <option key={model} value={model} />
                ))}
              </datalist>
            </div>
          </div>

          <div>
            <label htmlFor={`edit-description-${agent.id}`} className="label">
              Description
            </label>
            <input
              id={`edit-description-${agent.id}`}
              name="description"
              className="input"
              defaultValue={agent.description ?? ""}
              maxLength={5_000}
            />
          </div>

          <div>
            <label htmlFor={`edit-system-prompt-${agent.id}`} className="label">
              System prompt
            </label>
            <textarea
              id={`edit-system-prompt-${agent.id}`}
              name="system_prompt"
              className="input min-h-[104px] resize-y font-mono text-xs"
              defaultValue={agent.system_prompt ?? ""}
              maxLength={100_000}
            />
            <p className="mt-1 text-[11px] leading-snug text-slate-600">{MODEL_FIELD_HINT}</p>
          </div>

          <ToolPicker
            tools={tools}
            initialSelected={agent.tools_config.map((tool) => tool.name)}
            disabled={busy !== null}
          />

          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              name="is_active"
              defaultChecked={agent.is_active}
              className="h-4 w-4 rounded border-white/20 bg-ink-950 text-accent focus:ring-accent/40"
            />
            Active
          </label>

          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              className="btn-ghost px-2.5 py-1.5 text-xs"
              onClick={() => setMode("idle")}
              disabled={busy !== null}
            >
              Cancel
            </button>
            <button type="submit" className="btn-primary px-2.5 py-1.5 text-xs" disabled={busy !== null}>
              Save changes
            </button>
          </div>
        </form>
      ) : null}

      {mode === "delete" ? (
        <div className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-3">
          {runCount > 0 ? (
            <>
              <p className="text-xs text-rose-100">
                This agent has at least {runCount.toLocaleString()} run
                {runCount === 1 ? "" : "s"} in the recent sample, so it cannot be deleted
                permanently. Deactivating keeps the run history intact and stops new runs.
              </p>
              <div className="mt-2 flex items-center gap-2">
                <button
                  type="button"
                  className="btn-danger px-2.5 py-1.5 text-xs"
                  onClick={() => void onDeactivate()}
                  disabled={busy !== null || !agent.is_active}
                >
                  Deactivate agent
                </button>
                <button
                  type="button"
                  className="btn-ghost px-2.5 py-1.5 text-xs"
                  onClick={() => setMode("idle")}
                  disabled={busy !== null}
                >
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="text-xs text-rose-100">
                Permanently delete this agent? It has no runs in the recent sample. If it has older
                runs, the backend will refuse the delete and you can deactivate it instead.
              </p>
              <div className="mt-2 flex items-center gap-2">
                <button
                  type="button"
                  className="btn-danger px-2.5 py-1.5 text-xs"
                  onClick={() => void onHardDelete()}
                  disabled={busy !== null}
                >
                  <TrashIcon className="h-3.5 w-3.5" />
                  Delete permanently
                </button>
                <button
                  type="button"
                  className="btn-ghost px-2.5 py-1.5 text-xs"
                  onClick={() => void onDeactivate()}
                  disabled={busy !== null || !agent.is_active}
                >
                  Deactivate instead
                </button>
                <button
                  type="button"
                  className="btn-ghost px-2.5 py-1.5 text-xs"
                  onClick={() => setMode("idle")}
                  disabled={busy !== null}
                >
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
