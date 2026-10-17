"use client";

import { useState } from "react";

import { Select } from "@/components/form";
import { Button } from "@/components/ui/button";
import { ROLE_LABEL, ROLES, type Role } from "@/lib/types";

/** A role picker that changes nothing until saved: a role sets a person's clearance, and
 * arrow keys on a focused select fire change events in some browsers. */
export function RoleControl({
  user,
  disabled,
  onSave,
}: {
  user: { id: string; email: string; role: Role };
  disabled: boolean;
  onSave: (role: Role) => void;
}) {
  const [role, setRole] = useState<Role>(user.role);
  return (
    <div className="flex items-center gap-2">
      <label htmlFor={`role-${user.id}`} className="sr-only">
        Role for {user.email}
      </label>
      <Select
        id={`role-${user.id}`}
        value={role}
        disabled={disabled}
        className="w-36"
        onChange={(event) => setRole(event.target.value as Role)}
      >
        {ROLES.map((option) => (
          <option key={option} value={option}>
            {ROLE_LABEL[option]}
          </option>
        ))}
      </Select>
      {role !== user.role ? (
        <>
          <Button size="sm" onClick={() => onSave(role)}>
            Save role
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setRole(user.role)}>
            Cancel
          </Button>
        </>
      ) : null}
    </div>
  );
}
