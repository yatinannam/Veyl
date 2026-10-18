"use client";

import { useRef } from "react";

import { ClassificationBand } from "@/components/classification";
import { useReveal } from "@/lib/motion";
import { CLASSIFICATION_LABEL, type Classification } from "@/lib/types";

const LEVELS: { level: Classification; readers: string }[] = [
  { level: "restricted", readers: "Admins only" },
  { level: "confidential", readers: "Managers and admins" },
  { level: "internal", readers: "Everyone in the workspace who is granted access" },
  { level: "public", readers: "Anyone granted access, with no sensitive content" },
];

/** The four levels on the sign-in panel, most sensitive first. */
export function ClassificationLevels() {
  const list = useRef<HTMLUListElement>(null);
  useReveal(list, "li", { x: -8 }, { stagger: 0.08 });
  return (
    <ul ref={list} aria-label="Classification levels" className="space-y-4">
      {LEVELS.map(({ level, readers }) => (
        <li key={level} className="flex gap-3">
          <ClassificationBand level={level} className="w-1.5" />
          <div>
            <p className="font-medium">{CLASSIFICATION_LABEL[level]}</p>
            <p className="text-sm opacity-75">{readers}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}
