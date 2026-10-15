import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Home from "./page";

describe("Home", () => {
  it("shows the product name and what it does", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { level: 1, name: "Veyl" })).toBeInTheDocument();
    expect(screen.getByText(/permission-aware/i)).toBeInTheDocument();
  });
});
