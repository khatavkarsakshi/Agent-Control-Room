import { HeaderSkeleton, StatsSkeleton, TableSkeleton } from "@/components/Skeleton";

export default function Loading() {
  return (
    <div className="space-y-6">
      <HeaderSkeleton />
      <StatsSkeleton />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <TableSkeleton rows={5} />
        <div className="card space-y-4 p-5">
          <div className="h-4 w-32 animate-pulse rounded-md bg-white/5" />
          <div className="h-2 w-full animate-pulse rounded-full bg-white/5" />
          <div className="h-2 w-4/5 animate-pulse rounded-full bg-white/5" />
          <div className="h-2 w-3/5 animate-pulse rounded-full bg-white/5" />
        </div>
      </div>
    </div>
  );
}
