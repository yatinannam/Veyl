import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { UploadDialog } from "./upload-dialog";

describe("UploadDialog", () => {
  it("starts from an empty form after Cancel, including the file picker", () => {
    render(<UploadDialog role="admin" people={[]} onUploaded={vi.fn()} />);
    const picker = screen.getByLabelText("File");
    const file = new File(["# Handbook"], "handbook.md", { type: "text/markdown" });
    fireEvent.change(picker, { target: { files: [file] } });
    expect(screen.getByLabelText("Title")).toHaveValue("handbook");
    fireEvent.click(screen.getByRole("button", { name: "Cancel", hidden: true }));
    expect(screen.getByLabelText("Title")).toHaveValue("");
    // A fresh file input: choosing the same file again fires a change event.
    expect(screen.getByLabelText("File")).not.toBe(picker);
    expect((screen.getByLabelText("File") as HTMLInputElement).files?.length ?? 0).toBe(0);
  });
});
