"use client";

import { useRef, useState, type FormEvent } from "react";

import { AclFields, type Acl } from "@/components/documents/acl-fields";
import { ErrorText, Field, Input } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { describeError } from "@/lib/messages";
import type { DirectoryEntry, DocumentInfo, Role } from "@/lib/types";

const INITIAL_ACL: Acl = { classification: "internal", roles: ["member"], userIds: [] };

export function UploadDialog({
  role,
  people,
  onUploaded,
}: {
  role: Role;
  people: DirectoryEntry[];
  onUploaded: (document: DocumentInfo) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [acl, setAcl] = useState<Acl>(INITIAL_ACL);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  function close() {
    dialog.current?.close();
    setFile(null);
    setTitle("");
    setAcl(INITIAL_ACL);
    setError(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    form.append("title", title.trim() || file.name);
    form.append("classification", acl.classification);
    acl.roles.forEach((r) => form.append("allowed_roles", r));
    acl.userIds.forEach((id) => form.append("allowed_user_ids", id));
    setPending(true);
    setError(null);
    try {
      const document = await api.post<DocumentInfo>("/documents", form);
      onUploaded(document);
      close();
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <Button onClick={() => dialog.current?.showModal()}>Upload document</Button>
      <dialog
        ref={dialog}
        aria-labelledby="upload-title"
        className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-lg border border-border bg-card p-0 text-foreground backdrop:bg-foreground/40"
        onClose={close}
      >
        <form onSubmit={submit} className="grid gap-5 p-6">
          <div className="space-y-1">
            <h2 id="upload-title" className="text-lg font-semibold tracking-tight">
              Upload a document
            </h2>
            <p className="text-sm text-muted-foreground">PDF, TXT, or Markdown. It becomes searchable once processing finishes.</p>
          </div>
          <Field label="File" htmlFor="file">
            <Input
              id="file"
              type="file"
              accept=".pdf,.txt,.md"
              required
              className="py-1.5"
              onChange={(event) => {
                const chosen = event.target.files?.[0] ?? null;
                setFile(chosen);
                if (chosen && !title) setTitle(chosen.name.replace(/\.[^.]+$/, ""));
              }}
            />
          </Field>
          <Field label="Title" htmlFor="title">
            <Input id="title" value={title} maxLength={300} onChange={(event) => setTitle(event.target.value)} />
          </Field>
          <AclFields value={acl} onChange={setAcl} role={role} people={people} />
          <ErrorText>{error}</ErrorText>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" disabled={!file || pending}>
              {pending ? "Uploading" : "Upload"}
            </Button>
          </div>
        </form>
      </dialog>
    </>
  );
}
