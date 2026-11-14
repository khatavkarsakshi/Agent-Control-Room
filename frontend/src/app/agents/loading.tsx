import { CardsSkeleton, HeaderSkeleton } from "@/components/Skeleton";

export default function AgentsLoading() {
  return (
    <div className="space-y-6">
      <HeaderSkeleton />
      <div className="card h-20 animate-pulse" />
      <CardsSkeleton />
    </div>
  );
}
