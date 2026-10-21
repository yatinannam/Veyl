import type { AuditEvent } from "./types";

export const EVENT_LABELS: Record<string, string> = {
  "rag.query": "Question asked",
  "rag.denied": "Question refused",
  "authz.mismatch": "Authorization check failed",
  "auth.login_succeeded": "Signed in",
  "auth.login_failed": "Sign-in failed",
  "document.uploaded": "Document uploaded",
  "document.acl_changed": "Permissions changed",
  "document.deleted": "Document deleted",
  "document.ingested": "Document processed",
  "document.ingest_failed": "Processing failed",
  "user.created": "Person added",
  "user.role_changed": "Role changed",
  "user.activated": "Person reactivated",
  "user.deactivated": "Person deactivated",
  "tenant.updated": "Workspace settings changed",
  "audit.viewed": "Audit log viewed",
  "demo.reset": "Demo reset",
};

export interface AuditFilters {
  eventType: string;
  userId: string;
  from: string;
  to: string;
}

export const NO_FILTERS: AuditFilters = { eventType: "", userId: "", from: "", to: "" };

function localDay(day: string, h: number, m: number, s: number, ms: number): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year, month - 1, date, h, m, s, ms).toISOString();
}

export function buildAuditQuery(filters: AuditFilters, cursor?: string | null): string {
  const params = new URLSearchParams();
  if (filters.eventType) params.set("event_type", filters.eventType);
  if (filters.userId) params.set("user_id", filters.userId);
  // Date inputs are local calendar days; send the matching instants in UTC.
  if (filters.from) params.set("from", localDay(filters.from, 0, 0, 0, 0));
  if (filters.to) params.set("to", localDay(filters.to, 23, 59, 59, 999));
  if (cursor) params.set("cursor", cursor);
  params.set("limit", "50");
  return params.toString();
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/** A short, plain description of what an event involved. */
export function eventDetail(event: AuditEvent): string {
  if (event.event_type === "rag.query") {
    return `${plural(event.result_count ?? 0, "passage")} from ${plural(event.retrieved_document_ids.length, "document")}`;
  }
  const reason = event.details["reason"] ?? event.details["error"];
  return typeof reason === "string" ? reason.replaceAll("_", " ") : "";
}
