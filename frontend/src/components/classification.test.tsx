import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ClassificationBadge, ClearanceMeter } from "./classification";
import { CLEARANCE, withinClearance } from "@/lib/types";

describe("classification", () => {
  it("labels the level in words, not colour alone", () => {
    render(<ClassificationBadge level="confidential" />);
    expect(screen.getByText("Confidential")).toBeInTheDocument();
  });

  it("describes the clearance meter for assistive technology", () => {
    render(<ClearanceMeter level={CLEARANCE.manager} />);
    expect(screen.getByRole("img", { name: "Clearance: up to Confidential" })).toBeInTheDocument();
  });

  it("matches the server's clearance rule", () => {
    expect(withinClearance("member", "internal")).toBe(true);
    expect(withinClearance("member", "confidential")).toBe(false);
    expect(withinClearance("manager", "restricted")).toBe(false);
    expect(withinClearance("admin", "restricted")).toBe(true);
  });
});
