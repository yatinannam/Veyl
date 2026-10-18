import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccessButton } from "./access-button";

const active = { id: "u2", email: "mia@acme.test", is_active: true };

afterEach(() => vi.unstubAllGlobals());

describe("AccessButton", () => {
  it("asks before deactivating someone", () => {
    const confirm = vi.fn().mockReturnValue(false);
    vi.stubGlobal("confirm", confirm);
    const onChange = vi.fn();
    render(<AccessButton user={active} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("mia@acme.test"));
    expect(onChange).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("reactivates without asking", () => {
    const confirm = vi.fn();
    vi.stubGlobal("confirm", confirm);
    const onChange = vi.fn();
    render(<AccessButton user={{ ...active, is_active: false }} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Reactivate" }));
    expect(confirm).not.toHaveBeenCalled();
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
