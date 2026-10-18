"use client";

import { cn } from "cn";
import { useRef } from "react";

import {
  CLASSIFICATION_LABEL,
  CLASSIFICATIONS,
  classificationRank,
  type Classification,
} from "@/lib/types";
import { useReveal } from "@/lib/motion";

const FILL: Record<Classification, string> = {
  public: "bg-class-public",
  internal: "bg-class-internal",
  confidential: "bg-class-confidential",
  restricted: "bg-class-restricted",
};

/** The vertical band beside a document or citation. Always paired with a text label. */
export function ClassificationBand({
  level,
  className,
}: {
  level: Classification;
  className?: string;
}) {
  return (
    <span aria-hidden className={cn("w-1 shrink-0 self-stretch rounded-full", FILL[level], className)} />
  );
}

export function ClassificationBadge({ level }: { level: Classification }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-foreground">
      <span aria-hidden className={cn("size-2 rounded-full", FILL[level])} />
      {CLASSIFICATION_LABEL[level]}
    </span>
  );
}

/** Four segments, filled up to the reader's clearance. */
export function ClearanceMeter({ level }: { level: Classification }) {
  const meter = useRef<HTMLSpanElement>(null);
  useReveal(meter, "[data-segment]", { scaleX: 0, transformOrigin: "left center" }, { stagger: 0.05 });
  return (
    <span
      ref={meter}
      role="img"
      aria-label={`Clearance: up to ${CLASSIFICATION_LABEL[level]}`}
      className="flex items-center gap-0.5"
    >
      {CLASSIFICATIONS.map((step) => (
        <span
          key={step}
          data-segment
          className={cn(
            "h-1.5 w-5 rounded-full",
            classificationRank(step) <= classificationRank(level) ? FILL[step] : "bg-border",
          )}
        />
      ))}
    </span>
  );
}
