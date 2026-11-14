"use client";

import { useId, useState } from "react";

import { WrenchIcon } from "@/components/icons";

export interface ToolOption {
  name: string;
  description: string;
}

interface ToolPickerProps {
  tools: ToolOption[];
  initialSelected?: string[];
  disabled?: boolean;
  legend?: string;
  hint?: string;
}

/**
 * Checkbox grid for the tools an agent may call. Checkboxes keep the native
 * `name="tools"` value so the surrounding `<form>` can read them with FormData.
 */
export function ToolPicker({
  tools,
  initialSelected = [],
  disabled = false,
  legend = "Tools",
  hint = "Tools the agent can call while executing a run.",
}: ToolPickerProps) {
  const groupId = useId();
  const [selected, setSelected] = useState<Set<string>>(() => new Set(initialSelected));

  const toggle = (name: string, checked: boolean): void => {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(name);
      } else {
        next.delete(name);
      }
      return next;
    });
  };

  return (
    <fieldset disabled={disabled}>
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <legend className="label mb-0">{legend}</legend>
        <span className="text-[11px] text-slate-600">
          {selected.size} of {tools.length} selected
        </span>
      </div>
      <p className="mb-2 text-[11px] text-slate-600">{hint}</p>

      {tools.length === 0 ? (
        <p className="rounded-lg border border-white/5 bg-ink-950/60 px-3 py-2 text-xs text-slate-500">
          No tools are registered on the backend.
        </p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          {tools.map((tool) => {
            const id = `${groupId}-${tool.name}`;
            return (
              <label
                key={tool.name}
                htmlFor={id}
                className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-white/5 bg-ink-950/40 px-3 py-2.5 transition-colors hover:border-white/10 hover:bg-white/[0.03]"
              >
                <input
                  id={id}
                  type="checkbox"
                  name="tools"
                  value={tool.name}
                  checked={selected.has(tool.name)}
                  onChange={(event) => toggle(tool.name, event.target.checked)}
                  className="mt-0.5 h-4 w-4 shrink-0 rounded border-white/20 bg-ink-950 text-accent focus:ring-accent/40"
                />
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5 font-mono text-xs text-slate-200">
                    <WrenchIcon className="h-3 w-3 text-sky-300" />
                    {tool.name}
                  </span>
                  <span className="mt-0.5 block text-[11px] leading-snug text-slate-500">
                    {tool.description}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      )}
    </fieldset>
  );
}
