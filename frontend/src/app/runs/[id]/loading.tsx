import { HeaderSkeleton, StatsSkeleton, TableSkeleton } from "@/components/Skeleton";

export default function RunLoading() {
  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <div className="h-3 w-24 animate-pulse rounded bg-white/5" />
        <HeaderSkeleton />
      </div>
      <StatsSkeleton />
      <div className="card space-y-3 p-5">
        <div className="h-4 w-36 animate-pulse rounded bg-white/5" />
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className="flex items-center gap-4">
            <div className="h-4 flex-1 animate-pulse rounded bg-white/5" />
            <div className="h-2 w-1/2 animate-pulse rounded-full bg-white/5" />
            <div className="h-4 w-14 animate-pulse rounded bg-white/5" />
          </div>
        ))}
      </div>
      <TableSkeleton rows={2} />
    </div>
  );
}
