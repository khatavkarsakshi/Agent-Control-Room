interface JsonBlockProps {
  value: unknown;
  emptyLabel?: string;
  maxHeightClassName?: string;
}

export function JsonBlock({
  value,
  emptyLabel = "No payload captured",
  maxHeightClassName = "max-h-80",
}: JsonBlockProps) {
  if (value === null || value === undefined) {
    return <p className="rounded-lg border border-white/5 bg-ink-950/60 px-3 py-2 text-xs text-slate-500">{emptyLabel}</p>;
  }

  return (
    <pre
      className={`scrollbar-thin overflow-auto rounded-lg border border-white/5 bg-ink-950/80 p-3 font-mono text-[11px] leading-relaxed text-slate-300 ${maxHeightClassName}`}
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
