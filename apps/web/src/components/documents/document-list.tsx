"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { FileText, ExternalLink, Trash2, Loader2, Database, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/context/auth-context";
import { fetchDocuments, deleteDocument, DocumentItem } from "@/lib/api";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export function DocumentList() {
  const { isAuthenticated } = useAuth();
  const queryClient = useQueryClient();
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["documents"],
    queryFn: fetchDocuments,
    enabled: isAuthenticated,
    refetchOnWindowFocus: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (docId: string) => deleteDocument(docId),
    onSuccess: () => {
      setDeleteError(null);
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onError: (err: any) => {
      setDeleteError(err?.response?.data?.detail || "Failed to delete document.");
    },
  });

  if (!isAuthenticated) {
    return null;
  }

  return (
    <section className="p-6 rounded-xl border border-border bg-card flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <Database className="w-5 h-5 text-primary" />
          Stored Documents ({data?.total ?? 0})
        </h2>
        {isLoading && <Loader2 className="w-4 h-4 text-muted-foreground animate-spin" />}
      </div>

      {deleteError && (
        <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/30 text-destructive flex items-center gap-2 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{deleteError}</span>
        </div>
      )}

      {isLoading ? (
        <div className="py-8 flex flex-col items-center justify-center text-muted-foreground gap-2">
          <Loader2 className="w-6 h-6 animate-spin text-primary" />
          <p className="text-xs">Loading stored documents...</p>
        </div>
      ) : isError ? (
        <div className="py-6 text-center text-xs text-destructive">
          Failed to load documents: {(error as Error)?.message}
        </div>
      ) : !data || data.documents.length === 0 ? (
        <div className="py-8 text-center text-muted-foreground border border-dashed border-border rounded-lg flex flex-col items-center justify-center gap-2">
          <FileText className="w-8 h-8 opacity-40" />
          <p className="text-xs">No documents uploaded yet.</p>
          <p className="text-[11px] text-muted-foreground">Upload a PDF above to persist it into object storage.</p>
        </div>
      ) : (
        <div className="flex flex-col divide-y divide-border border border-border rounded-lg overflow-hidden">
          {data.documents.map((doc: DocumentItem) => (
            <div
              key={doc.id}
              className="p-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3 hover:bg-muted/30 transition-colors"
            >
              <div className="flex items-center gap-3 min-w-0">
                <div className="p-2 rounded-lg bg-primary/10 text-primary shrink-0">
                  <FileText className="w-5 h-5" />
                </div>
                <div className="min-w-0">
                  <p className="text-xs font-semibold truncate text-foreground" title={doc.filename}>
                    {doc.filename}
                  </p>
                  <div className="flex items-center gap-2 mt-0.5 text-[11px] text-muted-foreground">
                    <span>{formatBytes(doc.file_size)}</span>
                    <span>•</span>
                    <span className="capitalize">{doc.status}</span>
                    <span>•</span>
                    <span>{new Date(doc.created_at).toLocaleDateString()}</span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2 self-end sm:self-center shrink-0">
                <a
                  href={doc.file_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="h-8 px-2.5 rounded-md border border-border bg-background hover:bg-muted text-xs font-medium flex items-center gap-1.5 transition-colors"
                  title="View PDF"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  <span>View</span>
                </a>

                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    if (confirm(`Are you sure you want to delete "${doc.filename}"?`)) {
                      deleteMutation.mutate(doc.id);
                    }
                  }}
                  disabled={deleteMutation.isPending}
                  className="h-8 px-2 text-xs text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                  title="Delete Document"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
