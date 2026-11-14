import { HeaderSkeleton, TableSkeleton } from "@/components/Skeleton";

export default function RunsLoading() {
  return (
    <div className="space-y-6">
      <HeaderSkeleton />
      <div className="card space-y-3 p-4">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 8 }, (_, index) => (
            <div key={index} className="space-y-2">
              <div className="h-3 w-24 animate-pulse rounded bg-white/5" />
              <div className="h-9 animate-pulse rounded-lg bg-white/5" />
            </div>
          ))}
        </div>
      </div>
      <TableSkeleton rows={8} />
    </div>
  );
}
