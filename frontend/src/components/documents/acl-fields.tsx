import { Checkbox, Field, Select } from "@/components/form";
import {
  CLASSIFICATION_LABEL,
  CLASSIFICATIONS,
  ROLES,
  withinClearance,
  type Classification,
  type DirectoryEntry,
  type Role,
} from "@/lib/types";

export interface Acl {
  classification: Classification;
  roles: Role[];
  userIds: string[];
}

const ROLE_PLURAL: Record<Role, string> = { member: "Members", manager: "Managers", admin: "Admins" };

function toggle<T>(items: T[], item: T): T[] {
  return items.includes(item) ? items.filter((i) => i !== item) : [...items, item];
}

/** Who may read a document. Levels above the editor's clearance are not offered; the
 * server rejects them regardless. */
export function AclFields({
  value,
  onChange,
  role,
  people,
}: {
  value: Acl;
  onChange: (next: Acl) => void;
  role: Role;
  people: DirectoryEntry[];
}) {
  const levels = CLASSIFICATIONS.filter((level) => withinClearance(role, level));
  return (
    <div className="grid gap-5">
      <Field
        label="Classification"
        htmlFor="classification"
        hint="Readers also need clearance for this level: members up to internal, managers up to confidential."
      >
        <Select
          id="classification"
          value={value.classification}
          onChange={(event) =>
            onChange({ ...value, classification: event.target.value as Classification })
          }
        >
          {levels.map((level) => (
            <option key={level} value={level}>
              {CLASSIFICATION_LABEL[level]}
            </option>
          ))}
        </Select>
      </Field>
      <fieldset className="grid gap-2">
        <legend className="mb-1 text-sm font-medium">Roles that can read it</legend>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {ROLES.map((r) => (
            <label key={r} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={value.roles.includes(r)}
                onChange={() => onChange({ ...value, roles: toggle(value.roles, r) })}
              />
              {ROLE_PLURAL[r]}
            </label>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          Admins can always read documents in this workspace, and the uploader keeps access.
        </p>
      </fieldset>
      {people.length > 0 ? (
        <fieldset className="grid gap-2">
          <legend className="mb-1 text-sm font-medium">Also share with</legend>
          <div className="max-h-40 space-y-1.5 overflow-y-auto rounded-md border border-border p-2.5">
            {people.map((person) => (
              <label key={person.id} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={value.userIds.includes(person.id)}
                  onChange={() => onChange({ ...value, userIds: toggle(value.userIds, person.id) })}
                />
                {person.email}
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}
    </div>
  );
}
