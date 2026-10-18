"use client";

import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import type { RefObject } from "react";

gsap.registerPlugin(useGSAP);

const MOTION_WELCOME = "(prefers-reduced-motion: no-preference)";

/** Brings `selector` (inside `scope`) in once, one after another, from `from` plus
 * transparent. Short, and nothing at all when the reader prefers reduced motion: the
 * content is simply there. */
export function useReveal(
  scope: RefObject<HTMLElement | null>,
  selector: string,
  from: gsap.TweenVars,
  {
    stagger = 0.06,
    dependencies = [],
    enabled = true,
  }: { stagger?: number; dependencies?: unknown[]; enabled?: boolean } = {},
) {
  useGSAP(
    () => {
      // Without matchMedia the reader's motion preference is unknown: do not animate.
      if (!enabled || typeof window.matchMedia !== "function") return;
      const media = gsap.matchMedia();
      media.add(MOTION_WELCOME, () => {
        gsap.from(selector, {
          opacity: 0,
          ...from,
          duration: 0.35,
          ease: "power2.out",
          stagger,
          clearProps: "opacity,transform",
        });
      });
      return () => media.revert();
    },
    { scope, dependencies },
  );
}
