/**
 * Container liveness probe. Deliberately does not touch the backend API: it
 * only proves that the Next.js server is accepting requests, so a backend
 * outage does not restart the dashboard container.
 */
export function GET(): Response {
  return Response.json({ status: "ok", service: "agent-control-room-frontend" });
}
