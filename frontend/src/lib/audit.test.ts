import { describe, expect, it } from "vitest";

import { buildAuditQuery, EVENT_LABELS } from "./audit";

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
