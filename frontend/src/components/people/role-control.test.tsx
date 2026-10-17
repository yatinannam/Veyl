import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RoleControl } from "./role-control";

const user = { id: "u2", email: "mia@acme.test", role: "member" as const };

describe("RoleControl", () => {
  it("does not change anyone's role until the change is saved", () => {
    const onSave = vi.fn();
    render(<RoleControl user={user} disabled={false} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Role for mia@acme.test"), { target: { value: "admin" } });
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save role" }));
    expect(onSave).toHaveBeenCalledWith("admin");
  });

  it("offers no save button while the role is unchanged", () => {
    render(<RoleControl user={user} disabled={false} onSave={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Save role" })).not.toBeInTheDocument();
  });
});
