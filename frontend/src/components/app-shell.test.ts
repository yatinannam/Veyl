import { describe, expect, it } from "vitest";

import { navItems } from "./app-shell";

describe("navItems", () => {
  it("shows members only what they can use", () => {
    expect(navItems(["rag.query", "tenant.read"]).map((i) => i.label)).toEqual(["Ask", "Documents"]);
  });

  it("adds people and the audit log for admins", () => {
    expect(
      navItems(["rag.query", "tenant.read", "user.manage", "audit.read"]).map((i) => i.label),
    ).toEqual(["Ask", "Documents", "People", "Audit log"]);
  });
});
