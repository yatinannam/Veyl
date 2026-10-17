import type { DocumentInfo } from "@/lib/types";

const LABEL = { pending: "Queued", processing: "Processing", ready: "Ready", failed: "Failed" } as const;

export function StatusText({ document }: { document: DocumentInfo }) {
  if (document.status === "failed") {
    return (
      <span className="text-sm text-destructive">
        Failed{document.error ? `: ${document.error}` : ""}
      </span>
    );
  }
  return (
    <span className={document.status === "ready" ? "text-sm" : "text-sm text-muted-foreground"}>
      {LABEL[document.status]}
    </span>
  );
}
