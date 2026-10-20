"use client";

import { useState } from "react";

import { AclFields, type Acl } from "@/components/documents/acl-fields";
import { ErrorText } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { describeError } from "@/lib/messages";
import type { DirectoryEntry, DocumentInfo, Role } from "@/lib/types";

function aclOf(document: DocumentInfo): Acl {
  return {
    classification: document.classification,
    roles: document.allowed_roles ?? [],
    userIds: (document.allowed_user_ids ?? []).filter((id) => id !== document.uploaded_by),
  };
}

export function PermissionEditor({
  document,
  role,
  people,
  onSaved,
  onDeleted,
}: {
  document: DocumentInfo;
  role: Role;
  people: DirectoryEntry[];
  onSaved: (document: DocumentInfo) => void;
  onDeleted: () => void;
}) {
  const [acl, setAcl] = useState<Acl>(() => aclOf(document));
  // The version these permissions were loaded at; the server refuses a save against an
  // older one, so two editors cannot silently overwrite each other.
  const [version, setVersion] = useState(document.acl_version);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function save() {
    setPending(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.put<DocumentInfo>(`/documents/${document.id}/permissions`, {
        classification: acl.classification,
        allowed_roles: acl.roles,
        allowed_user_ids: acl.userIds,
        expected_acl_version: version,
      });
      setVersion(updated.acl_version);
      onSaved(updated);
      setNotice("Permissions saved. They apply to the next question anyone asks.");
    } catch (failure) {
      setError(describeError(failure));
      if (failure instanceof ApiError && failure.code === "conflict") {
        // Show what the other person saved; the next save is checked against it.
        const latest = await api.get<DocumentInfo>(`/documents/${document.id}`);
        setAcl(aclOf(latest));
        setVersion(latest.acl_version);
        onSaved(latest);
      }
    } finally {
      setPending(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Delete "${document.title}"? People will no longer get answers from it.`)) return;
    try {
      await api.delete(`/documents/${document.id}`);
      onDeleted();
    } catch (failure) {
      setError(describeError(failure));
    }
  }

  return (
    <section aria-labelledby="permissions-title" className="space-y-5 rounded-lg border border-border bg-card p-5">
      <h2 id="permissions-title" className="text-base font-semibold">
        Who can read this
      </h2>
      <AclFields value={acl} onChange={setAcl} role={role} people={people} />
      <ErrorText>{error}</ErrorText>
      {notice ? (
        <p role="status" className="text-sm text-muted-foreground">
          {notice}
        </p>
      ) : null}
      <div className="flex flex-wrap justify-between gap-2">
        <Button variant="destructive" onClick={() => void remove()}>
          Delete document
        </Button>
        <Button onClick={() => void save()} disabled={pending}>
          {pending ? "Saving" : "Save permissions"}
        </Button>
      </div>
    </section>
  );
}
