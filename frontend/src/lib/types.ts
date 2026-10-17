export type Role = "member" | "manager" | "admin";
export type Classification = "public" | "internal" | "confidential" | "restricted";
export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export const ROLES: Role[] = ["member", "manager", "admin"];
export const CLASSIFICATIONS: Classification[] = ["public", "internal", "confidential", "restricted"];

export const ROLE_LABEL: Record<Role, string> = {
  member: "Member",
  manager: "Manager",
  admin: "Admin",
};

export const CLASSIFICATION_LABEL: Record<Classification, string> = {
  public: "Public",
  internal: "Internal",
  confidential: "Confidential",
  restricted: "Restricted",
};

/** Highest classification each role may read. Mirrors the server; the server decides. */
export const CLEARANCE: Record<Role, Classification> = {
  member: "internal",
  manager: "confidential",
  admin: "restricted",
};

export function classificationRank(level: Classification): number {
  return CLASSIFICATIONS.indexOf(level);
}

export function withinClearance(role: Role, level: Classification): boolean {
  return classificationRank(level) <= classificationRank(CLEARANCE[role]);
}

export interface Me {
  user: { id: string; email: string; role: Role };
  tenant: { id: string; slug: string; name: string };
  permissions: string[];
}

export interface Citation {
  label: string;
  document_id: string;
  document_title: string;
  chunk_id: string;
  snippet: string;
  classification: Classification;
}

export interface QueryResponse {
  conversation_id: string;
  message_id: string;
  answer: string;
  citations: Citation[];
  trace_id: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  cited_document_ids: string[];
  citations: Citation[];
  trace_id: string;
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: StoredMessage[];
}

export interface DocumentInfo {
  id: string;
  title: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  classification: Classification;
  status: DocumentStatus;
  error: string | null;
  uploaded_by: string;
  created_at: string;
  updated_at: string;
  can_manage: boolean;
  allowed_roles: Role[] | null;
  allowed_user_ids: string[] | null;
}

export interface UserInfo {
  id: string;
  email: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface DirectoryEntry {
  id: string;
  email: string;
  role: Role;
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  actor_user_id: string | null;
  event_type: string;
  decision: "allow" | "deny" | "error";
  query_ref: string | null;
  query_text: string | null;
  retrieved_chunk_ids: string[];
  retrieved_document_ids: string[];
  result_count: number | null;
  trace_id: string | null;
  model: string | null;
  details: Record<string, unknown>;
}

export interface AuditPage {
  events: AuditEvent[];
  next_cursor: string | null;
}
