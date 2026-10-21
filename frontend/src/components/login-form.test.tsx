import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginForm, safeNext } from "./login-form";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

afterEach(() => {
  vi.unstubAllGlobals();
  replace.mockReset();
});

function fill() {
  fireEvent.change(screen.getByLabelText("Workspace"), { target: { value: "acme" } });
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "mia@acme.test" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-horse" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("safeNext", () => {
  it.each([
    ["?next=/documents", "/documents"],
    ["?next=//evil.example", "/chat"],
    ["?next=/%5Cevil.example", "/chat"],
    ["?next=https://evil.example", "/chat"],
    ["?next=/%09/evil.example", "/chat"],
    ["?next=/%0A/evil.example", "/chat"],
    ["?next=/%0D/evil.example", "/chat"],
    ["?next=/%09%5Cevil.example", "/chat"],
    ["?next=/documents/abc?x=1#top", "/documents/abc?x=1#top"],
    ["", "/chat"],
  ])("%s -> %s", (search, expected) => {
    expect(safeNext(search)).toBe(expected);
  });
});

describe("LoginForm", () => {
  it("signs in and continues to the app", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json({}));
    vi.stubGlobal("fetch", fetchMock);
    render(<LoginForm />);
    fill();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat"));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      tenant_slug: "acme",
      email: "mia@acme.test",
      password: "correct-horse",
    });
  });

  it("explains a failed sign-in without saying which part was wrong", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          { error: { code: "invalid_credentials", message: "x", trace_id: "t" } },
          { status: 401 },
        ),
      ),
    );
    render(<LoginForm />);
    fill();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That workspace, email, and password don't match.",
    );
    expect(replace).not.toHaveBeenCalled();
  });

  it("offers no demo accounts unless the deployment publishes the password", () => {
    render(<LoginForm />);
    expect(screen.queryByRole("heading", { name: "Try the demo" })).not.toBeInTheDocument();
  });

  it("signs in as a demo account in one click", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json({}));
    vi.stubGlobal("fetch", fetchMock);
    render(<LoginForm demoPassword="demo-pass-123" />);
    expect(screen.getByText("demo-pass-123")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Sign in as Mia/ }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat"));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      tenant_slug: "acme",
      email: "mia@acme.test",
      password: "demo-pass-123",
    });
  });
});
