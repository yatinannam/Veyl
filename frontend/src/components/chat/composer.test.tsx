import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "./composer";

function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
}

describe("Composer", () => {
  it("keeps the question when asking fails", async () => {
    const onAsk = vi.fn().mockResolvedValue(false);
    render(<Composer onAsk={onAsk} disabled={false} />);
    ask("What flights may I book?");
    await waitFor(() => expect(onAsk).toHaveBeenCalledWith("What flights may I book?"));
    expect(screen.getByLabelText("Your question")).toHaveValue("What flights may I book?");
  });

  it("clears the question once it has been answered", async () => {
    render(<Composer onAsk={vi.fn().mockResolvedValue(true)} disabled={false} />);
    ask("What flights may I book?");
    await waitFor(() => expect(screen.getByLabelText("Your question")).toHaveValue(""));
  });

  it("does not send while an input method is composing text", () => {
    const onAsk = vi.fn().mockResolvedValue(true);
    render(<Composer onAsk={onAsk} disabled={false} />);
    const box = screen.getByLabelText("Your question");
    fireEvent.change(box, { target: { value: "nihongo" } });
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    expect(onAsk).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onAsk).toHaveBeenCalledWith("nihongo");
  });
});
