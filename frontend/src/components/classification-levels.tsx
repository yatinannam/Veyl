import { ClassificationBand } from "@/components/classification";
import { CLASSIFICATION_LABEL, type Classification } from "@/lib/types";

const LEVELS: { level: Classification; readers: string }[] = [
  { level: "restricted", readers: "Admins only" },
  { level: "confidential", readers: "Managers and admins" },
  { level: "internal", readers: "Everyone in the workspace who is granted access" },
  { level: "public", readers: "Anyone granted access, with no sensitive content" },
];

/** The four levels on the sign-in panel, most sensitive first. Not animated: it arrives
 * in the server-rendered HTML, so a reveal could only hide what is already on screen. */
export function ClassificationLevels() {
  return (
    <ul aria-label="Classification levels" className="space-y-4">
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
