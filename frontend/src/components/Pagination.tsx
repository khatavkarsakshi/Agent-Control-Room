import Link from "next/link";

import { ArrowLeftIcon, ArrowRightIcon } from "@/components/icons";

type QueryValue = string | string[] | undefined;

interface PaginationProps {
  basePath: string;
  total: number;
  limit: number;
  offset: number;
  query: Record<string, QueryValue>;
}

function pageHref(
  basePath: string,
  query: Record<string, QueryValue>,
  limit: number,
  offset: number,
): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined) {
      continue;
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        params.append(key, item);
      }
    } else {
      params.set(key, value);
    }
  }
  params.set("limit", String(limit));
  if (offset > 0) {
    params.set("offset", String(offset));
  } else {
    params.delete("offset");
  }
  return `${basePath}?${params.toString()}`;
}

export function Pagination({ basePath, total, limit, offset, query }: PaginationProps) {
  if (total === 0) {
    return null;
  }

  const first = offset + 1;
  const last = Math.min(offset + limit, total);
  const previousOffset = Math.max(offset - limit, 0);
  const nextOffset = offset + limit;
  const hasPrevious = offset > 0;
  const hasNext = nextOffset < total;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/5 px-5 py-3">
      <p className="text-xs text-slate-500">
        Showing <span className="text-slate-300">{first.toLocaleString()}</span>–
        <span className="text-slate-300">{last.toLocaleString()}</span> of{" "}
        <span className="text-slate-300">{total.toLocaleString()}</span>
      </p>
      <div className="flex items-center gap-2">
        {hasPrevious ? (
          <Link href={pageHref(basePath, query, limit, previousOffset)} className="btn-ghost px-2.5 py-1.5 text-xs">
            <ArrowLeftIcon className="h-3.5 w-3.5" />
            Previous
          </Link>
        ) : (
          <span className="btn-ghost px-2.5 py-1.5 text-xs opacity-40" aria-disabled="true">
            <ArrowLeftIcon className="h-3.5 w-3.5" />
            Previous
          </span>
        )}
        {hasNext ? (
          <Link href={pageHref(basePath, query, limit, nextOffset)} className="btn-ghost px-2.5 py-1.5 text-xs">
            Next
            <ArrowRightIcon className="h-3.5 w-3.5" />
          </Link>
        ) : (
          <span className="btn-ghost px-2.5 py-1.5 text-xs opacity-40" aria-disabled="true">
            Next
            <ArrowRightIcon className="h-3.5 w-3.5" />
          </span>
        )}
      </div>
    </div>
  );
}
