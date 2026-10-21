"use client";

import { useSyncExternalStore } from "react";

import { isStartingUp, onStartingUp } from "@/lib/api";

/** Shown while the API is slow to answer, which on a free host means it is waking from
 * sleep. The live region stays mounted so screen readers announce the change. */
export function StartingUpNotice() {
  const startingUp = useSyncExternalStore(onStartingUp, isStartingUp, () => false);
  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex justify-center px-4"
    >
      {startingUp ? (
        <p className="rounded-md border bg-background px-4 py-2 text-sm shadow-sm">
          Starting up, this can take a minute.
        </p>
      ) : null}
    </div>
  );
}
