import { describe, expect, it } from "vitest";

import { buildAuditQuery, EVENT_LABELS, eventDetail } from "./audit";

describe("buildAuditQuery", () => {
  it("includes only the filters that are set", () => {
    expect(buildAuditQuery({ eventType: "", userId: "", from: "", to: "" })).toBe("limit=50");
  });

  it("turns dates into a whole-day range in UTC", () => {
    const query = new URLSearchParams(
      buildAuditQuery({ eventType: "rag.query", userId: "u1", from: "2026-10-01", to: "2026-10-02" }, "cur"),
    );
    expect(query.get("event_type")).toBe("rag.query");
    expect(query.get("user_id")).toBe("u1");
    expect(query.get("from")).toBe("2026-10-01T00:00:00Z");
    expect(query.get("to")).toBe("2026-10-02T23:59:59Z");
    expect(query.get("cursor")).toBe("cur");
  });

  it("names every event type in plain words", () => {
    expect(EVENT_LABELS["rag.query"]).toBe("Question asked");
    expect(Object.keys(EVENT_LABELS)).toContain("audit.viewed");
  });
});

describe("eventDetail", () => {
  const base = {
    id: "e1",
    occurred_at: "2026-10-17T12:00:00Z",
    actor_user_id: "u1",
    decision: "allow" as const,
    query_ref: null,
    query_text: null,
    retrieved_chunk_ids: [],
    trace_id: null,
    model: null,
    details: {},
  };

  it("counts passages and documents with correct plurals", () => {
    expect(
      eventDetail({ ...base, event_type: "rag.query", result_count: 1, retrieved_document_ids: ["d1"] }),
    ).toBe("1 passage from 1 document");
    expect(
      eventDetail({ ...base, event_type: "rag.query", result_count: 3, retrieved_document_ids: ["d1", "d2"] }),
    ).toBe("3 passages from 2 documents");
  });

  it("explains other events by their reason", () => {
    expect(
      eventDetail({
        ...base,
        event_type: "rag.denied",
        result_count: null,
        retrieved_document_ids: [],
        details: { reason: "conversation_not_found" },
      }),
    ).toBe("conversation not found");
  });
});
