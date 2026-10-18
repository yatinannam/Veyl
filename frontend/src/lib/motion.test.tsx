import { render } from "@testing-library/react";
import gsap from "gsap";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useReveal } from "./motion";

function stubMotionPreference(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("no-preference") ? !reduce : reduce,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      onchange: null,
      dispatchEvent: vi.fn(),
    })),
  );
}

function Revealed() {
  const scope = useRef<HTMLUListElement>(null);
  useReveal(scope, "li", { y: 6 });
  return (
    <ul ref={scope}>
      <li>One</li>
      <li>Two</li>
    </ul>
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useReveal", () => {
  it("animates the items in when motion is welcome", () => {
    stubMotionPreference(false);
    const from = vi.spyOn(gsap, "from");
    render(<Revealed />);
    expect(from).toHaveBeenCalledTimes(1);
    const [targets, vars] = from.mock.calls[0];
    expect(targets).toBe("li");
    expect(vars).toMatchObject({ opacity: 0, y: 6 });
    expect((vars as { duration: number }).duration).toBeLessThanOrEqual(0.4);
  });

  it("does nothing at all when the reader prefers reduced motion", () => {
    stubMotionPreference(true);
    const from = vi.spyOn(gsap, "from");
    const { getByText } = render(<Revealed />);
    expect(from).not.toHaveBeenCalled();
    expect(getByText("One").style.opacity).toBe("");
  });
});
