/**
 * Environment values shared by server and client code.
 *
 * `NEXT_PUBLIC_*` variables are inlined at build time, so they must be passed as
 * Docker build args (see `frontend/Dockerfile`). The server-only API base URL is
 * read at runtime and may point at a private container hostname.
 */

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

export const PUBLIC_API_BASE_URL = trimTrailingSlash(
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
);

export const PUBLIC_JAEGER_URL = trimTrailingSlash(
  process.env.NEXT_PUBLIC_JAEGER_URL ?? "http://localhost:16686",
);

export const PUBLIC_API_DOCS_URL = `${PUBLIC_API_BASE_URL}/docs`;
