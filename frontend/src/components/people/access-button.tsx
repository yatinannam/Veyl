"use client";

import { Button } from "@/components/ui/button";

/** Deactivating signs a person out everywhere and blocks sign-in, so it asks first. */
export function AccessButton({
  user,
  onChange,
}: {
  user: { id: string; email: string; is_active: boolean };
  onChange: (isActive: boolean) => void;
}) {
  function toggle() {
    if (
      user.is_active &&
      !window.confirm(`Deactivate ${user.email}? They are signed out and can no longer sign in.`)
    ) {
      return;
    }
    onChange(!user.is_active);
  }

  return (
    <Button variant={user.is_active ? "outline" : "secondary"} size="sm" onClick={toggle}>
      {user.is_active ? "Deactivate" : "Reactivate"}
    </Button>
  );
}
