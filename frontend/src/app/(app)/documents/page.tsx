"use client";

import Link from "next/link";
import { useEffect } from "react";

import { ClassificationBadge, ClassificationBand } from "@/components/classification";
import { StatusText } from "@/components/documents/status";
import { UploadDialog } from "@/components/documents/upload-dialog";
import { ErrorText } from "@/components/form";
import { useSession } from "@/components/session";
import { formatDate } from "@/lib/format";
import { describeError } from "@/lib/messages";
import type { DirectoryEntry, DocumentInfo } from "@/lib/types";
import { useApi } from "@/lib/use-api";

export default function DocumentsPage() {
  const { me, can } = useSession();
  const documents = useApi<DocumentInfo[]>("/documents");
  const people = useApi<DirectoryEntry[]>(can("user.directory") ? "/users/directory" : null);
  const reload = documents.reload;
  const busy = (documents.data ?? []).some((d) => d.status === "pending" || d.status === "processing");

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(reload, 3000);
    return () => clearInterval(timer);
  }, [busy, reload]);

  const rows = documents.data ?? [];
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">Documents</h1>
          <p className="text-sm text-muted-foreground">Documents you can read in {me.tenant.name}.</p>
        </div>
        {can("document.upload") ? (
          <UploadDialog role={me.user.role} people={people.data ?? []} onUploaded={() => reload()} />
        ) : null}
      </div>
      <ErrorText>{documents.error ? describeError(documents.error) : null}</ErrorText>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {documents.loading
            ? "Loading documents"
            : can("document.upload")
              ? "Nothing here yet. Upload a document to make it searchable."
              : "No documents have been shared with you yet."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-muted-foreground">
              <tr>
                <th scope="col" className="px-4 py-2.5 font-medium">Document</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Classification</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Status</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Added</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((document) => (
                <tr key={document.id} className="border-b border-border last:border-b-0">
                  <td className="px-4 py-3">
                    <div className="flex gap-3">
                      <ClassificationBand level={document.classification} />
                      <div className="min-w-0">
                        <Link href={`/documents/${document.id}`} className="font-medium hover:underline">
                          {document.title}
                        </Link>
                        <p className="truncate text-xs text-muted-foreground">{document.original_filename}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <ClassificationBadge level={document.classification} />
                  </td>
                  <td className="px-4 py-3">
                    <StatusText document={document} />
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-muted-foreground">
                    {formatDate(document.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
