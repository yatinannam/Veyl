import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AclFields, type Acl } from "./acl-fields";

const people = [
  { id: "u1", email: "alice@acme.test", role: "admin" as const },
  { id: "u2", email: "mia@acme.test", role: "member" as const },
];
const value: Acl = { classification: "internal", roles: ["member"], userIds: [] };

describe("AclFields", () => {
  it("offers managers only the levels they are cleared for", () => {
    render(<AclFields value={value} onChange={vi.fn()} role="manager" people={people} />);
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Public", "Internal", "Confidential"]);
  });

  it("offers admins every level", () => {
    render(<AclFields value={value} onChange={vi.fn()} role="admin" people={people} />);
    expect(screen.getAllByRole("option")).toHaveLength(4);
  });

  it("toggles roles and people", () => {
    const onChange = vi.fn();
    render(<AclFields value={value} onChange={onChange} role="admin" people={people} />);
    fireEvent.click(screen.getByLabelText("Managers"));
    expect(onChange).toHaveBeenLastCalledWith({ ...value, roles: ["member", "manager"] });
    fireEvent.click(screen.getByLabelText("mia@acme.test"));
    expect(onChange).toHaveBeenLastCalledWith({ ...value, userIds: ["u2"] });
  });
});
