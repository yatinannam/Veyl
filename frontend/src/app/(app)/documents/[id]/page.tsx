"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { ClassificationBadge, ClassificationBand } from "@/components/classification";
import { PermissionEditor } from "@/components/documents/permission-editor";
import { StatusText } from "@/components/documents/status";
import { ErrorText } from "@/components/form";
import { useSession } from "@/components/session";
import { formatBytes, formatDateTime } from "@/lib/format";
import { describeError } from "@/lib/messages";
import type { DirectoryEntry, DocumentInfo } from "@/lib/types";
import { useApi } from "@/lib/use-api";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { me, can } = useSession();
  const document = useApi<DocumentInfo>(`/documents/${id}`);
  const manage = document.data?.can_manage === true;
  const people = useApi<DirectoryEntry[]>(manage && can("user.directory") ? "/users/directory" : null);

  if (document.error) {
    return (
      <div className="space-y-3">
        <ErrorText>{describeError(document.error)}</ErrorText>
        <Link href="/documents" className="text-sm text-primary hover:underline">
          Back to documents
        </Link>
      </div>
    );
  }
  if (!document.data) return <p className="text-sm text-muted-foreground">Loading document</p>;
  const doc = document.data;

  return (
    <div className="grid max-w-3xl gap-6">
      <Link href="/documents" className="text-sm text-primary hover:underline">
        Back to documents
      </Link>
      <div className="flex gap-4">
        <ClassificationBand level={doc.classification} className="w-1.5" />
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">{doc.title}</h1>
          <ClassificationBadge level={doc.classification} />
        </div>
      </div>
      <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        <dt className="text-muted-foreground">File</dt>
        <dd className="truncate">{doc.original_filename}</dd>
        <dt className="text-muted-foreground">Size</dt>
        <dd>{formatBytes(doc.size_bytes)}</dd>
        <dt className="text-muted-foreground">Status</dt>
        <dd>
          <StatusText document={doc} />
        </dd>
        <dt className="text-muted-foreground">Added</dt>
        <dd>{formatDateTime(doc.created_at)}</dd>
        <dt className="text-muted-foreground">Last changed</dt>
        <dd>{formatDateTime(doc.updated_at)}</dd>
      </dl>
      {manage ? (
        <PermissionEditor
          key={doc.id}
          document={doc}
          role={me.user.role}
          people={people.data ?? []}
          onSaved={() => document.reload()}
          onDeleted={() => router.push("/documents")}
        />
      ) : (
        <p className="text-sm text-muted-foreground">
          Only the person who uploaded this document and admins can change who can read it.
        </p>
      )}
    </div>
  );
}
