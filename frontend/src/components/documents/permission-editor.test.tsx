import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PermissionEditor } from "./permission-editor";
import type { DocumentInfo } from "@/lib/types";

const doc: DocumentInfo = {
  id: "d1",
  title: "Roadmap",
  original_filename: "roadmap.md",
  mime_type: "text/markdown",
  size_bytes: 10,
  classification: "internal",
  status: "ready",
  error: null,
  uploaded_by: "u1",
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-01T10:00:00Z",
  can_manage: true,
  allowed_roles: ["member"],
  allowed_user_ids: ["u1"],
  acl_version: 3,
};

afterEach(() => vi.unstubAllGlobals());

describe("PermissionEditor", () => {
  it("detects a concurrent edit, shows the latest permissions, and saves against them", async () => {
    const latest = { ...doc, classification: "confidential" as const, allowed_roles: ["manager" as const], acl_version: 4 };
    const bodies: unknown[] = [];
    let puts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        if (init?.method === "PUT") {
          bodies.push(JSON.parse(String(init.body)));
          puts += 1;
          return Promise.resolve(
            puts === 1
              ? Response.json(
                  { error: { code: "conflict", message: "Someone else changed these permissions.", trace_id: "t" } },
                  { status: 409 },
                )
              : Response.json({ ...latest, acl_version: 5 }),
          );
        }
        expect(url).toBe("/api/v1/documents/d1");
        return Promise.resolve(Response.json(latest));
      }),
    );
    const onSaved = vi.fn();
    render(<PermissionEditor document={doc} role="admin" people={[]} onSaved={onSaved} onDeleted={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Save permissions" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Someone else changed");
    await waitFor(() => expect(screen.getByLabelText("Classification")).toHaveValue("confidential"));
    expect(bodies[0]).toMatchObject({ expected_acl_version: 3 });

    fireEvent.click(screen.getByRole("button", { name: "Save permissions" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(bodies[1]).toMatchObject({ classification: "confidential", expected_acl_version: 4 });
  });
});
