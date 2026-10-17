"use client";

import { useState, type FormEvent } from "react";

import { ErrorText, Field, Input, Select } from "@/components/form";
import { useSession } from "@/components/session";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { describeError } from "@/lib/messages";
import { ROLE_LABEL, ROLES, type Role, type UserInfo } from "@/lib/types";
import { useApi } from "@/lib/use-api";

export default function PeoplePage() {
  const { me } = useSession();
  const users = useApi<UserInfo[]>("/users");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setPending(true);
    setError(null);
    try {
      await api.post("/users", {
        email: form.get("email"),
        password: form.get("password"),
        role: form.get("role"),
      });
      formElement.reset();
      users.reload();
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setPending(false);
    }
  }

  async function update(user: UserInfo, change: { role?: Role; is_active?: boolean }) {
    setError(null);
    try {
      await api.patch(`/users/${user.id}`, change);
      users.reload();
    } catch (failure) {
      setError(describeError(failure));
    }
  }

  return (
    <div className="grid max-w-4xl gap-8">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">People</h1>
        <p className="text-sm text-muted-foreground">
          Roles set how far each person&apos;s clearance reaches. Changes apply to their next request.
        </p>
      </div>
      <form onSubmit={create} className="grid gap-4 rounded-lg border border-border bg-card p-5 sm:grid-cols-[1fr_1fr_10rem_auto] sm:items-end">
        <Field label="Email" htmlFor="new-email">
          <Input id="new-email" name="email" type="email" required />
        </Field>
        <Field label="Temporary password" htmlFor="new-password" hint="At least 12 characters.">
          <Input id="new-password" name="password" type="password" minLength={12} required autoComplete="new-password" />
        </Field>
        <Field label="Role" htmlFor="new-role">
          <Select id="new-role" name="role" defaultValue="member">
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {ROLE_LABEL[role]}
              </option>
            ))}
          </Select>
        </Field>
        <Button type="submit" disabled={pending}>
          Add person
        </Button>
      </form>
      <ErrorText>{error ?? (users.error ? describeError(users.error) : null)}</ErrorText>
      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th scope="col" className="px-4 py-2.5 font-medium">Person</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Role</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Access</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Added</th>
            </tr>
          </thead>
          <tbody>
            {(users.data ?? []).map((user) => {
              const self = user.id === me.user.id;
              return (
                <tr key={user.id} className="border-b border-border last:border-b-0">
                  <td className="px-4 py-3">
                    {user.email}
                    {self ? <span className="text-muted-foreground"> (you)</span> : null}
                  </td>
                  <td className="px-4 py-3">
                    <label htmlFor={`role-${user.id}`} className="sr-only">
                      Role for {user.email}
                    </label>
                    <Select
                      id={`role-${user.id}`}
                      value={user.role}
                      disabled={self}
                      className="w-36"
                      onChange={(event) => void update(user, { role: event.target.value as Role })}
                    >
                      {ROLES.map((role) => (
                        <option key={role} value={role}>
                          {ROLE_LABEL[role]}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td className="px-4 py-3">
                    {self ? (
                      <span className="text-muted-foreground">Active</span>
                    ) : (
                      <Button
                        variant={user.is_active ? "outline" : "secondary"}
                        size="sm"
                        onClick={() => void update(user, { is_active: !user.is_active })}
                      >
                        {user.is_active ? "Deactivate" : "Reactivate"}
                      </Button>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-muted-foreground">{formatDate(user.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
