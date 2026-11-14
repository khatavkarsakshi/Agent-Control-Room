import type { ComponentType, ReactNode } from "react";

export type StatTone = "default" | "accent" | "sky" | "amber" | "rose" | "violet";

const TONE_CLASSES: Record<StatTone, string> = {
  default: "bg-white/5 text-slate-300",
  accent: "bg-accent/10 text-accent",
  sky: "bg-sky-400/10 text-sky-300",
  amber: "bg-amber-400/10 text-amber-300",
  rose: "bg-rose-400/10 text-rose-300",
  violet: "bg-violet-400/10 text-violet-300",
};

interface StatCardProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon: ComponentType<{ className?: string }>;
  tone?: StatTone;
}

export function StatCard({ label, value, hint, icon: Icon, tone = "default" }: StatCardProps) {
  return (
    <div className="card p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="stat-label">{label}</p>
        <span className={`grid h-8 w-8 place-items-center rounded-lg ${TONE_CLASSES[tone]}`}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <p className="mt-3 text-2xl font-semibold tracking-tight text-slate-50">{value}</p>
      {hint ? <p className="mt-1.5 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}
