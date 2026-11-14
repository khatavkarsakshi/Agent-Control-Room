import type { Metadata } from "next";
import Link from "next/link";

import { SearchIcon } from "@/components/icons";

export const metadata: Metadata = { title: "Not found" };

export default function NotFound() {
  return (
    <div className="card mx-auto max-w-2xl p-8 text-center">
      <span className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-white/5 text-slate-400">
        <SearchIcon className="h-6 w-6" />
      </span>
      <h1 className="mt-4 text-lg font-semibold text-slate-100">Page not found</h1>
      <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">
        The run, agent or page you requested does not exist. It may have been deleted, or the link
        may be out of date.
      </p>
      <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
        <Link href="/" className="btn-primary">
          Back to overview
        </Link>
        <Link href="/runs" className="btn-ghost">
          Browse runs
        </Link>
        <Link href="/agents" className="btn-ghost">
          Browse agents
        </Link>
      </div>
    </div>
  );
}
