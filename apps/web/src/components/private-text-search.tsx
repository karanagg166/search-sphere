"use client";

import { useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useAuth } from "@/context/auth-context";
import { fetchDocuments } from "@/lib/api";
import { searchPrivateText } from "@/lib/api/private-search";
import { AuthModal } from "@/components/auth-modal";
import { Button } from "@/components/ui/button";

const searchErrors: Record<string, string> = {
  specific_search_terms_required: "Enter specific words, a name or an identifier from the PDF.",
  invalid_pdf: "This file could not be read as a PDF.",
  encrypted_pdf: "This PDF needs a password. Upload an unlocked copy to search its text.",
  pdf_page_limit: "Private text search supports PDFs with up to 100 pages.",
  pdf_text_limit: "This PDF exceeds the private text-search limit. Try a smaller file.",
  pdf_size_limit: "Private text search supports PDFs up to 20 MB.",
  private_search_busy: "Private text search is busy. Try again shortly.",
  private_search_timeout: "Text extraction timed out. Try a smaller PDF.",
  private_storage_unavailable: "The private original could not be loaded. Try again shortly.",
};

export function PrivateTextSearch() {
  const { user, isAuthenticated } = useAuth();
  const [authOpen, setAuthOpen] = useState(false);
  const searchParams = useSearchParams();
  const [selectedDocumentId, setDocumentId] = useState<string | undefined>();
  const documentId = selectedDocumentId ?? searchParams.get("document") ?? "";
  const [query, setQuery] = useState("");
  const ownerId = user?.id;
  const documents = useQuery({
    queryKey: ["documents", ownerId], queryFn: fetchDocuments, enabled: !!ownerId,
    refetchOnWindowFocus: false,
  });
  const search = useMutation({
    mutationFn: async (input: { documentId: string; query: string; ownerId: string }) => ({
      ...await searchPrivateText(input.documentId, input.query), ownerId: input.ownerId,
      documentId: input.documentId, query: input.query,
    }),
  });
  const result = search.data && search.data.ownerId === ownerId && search.data.documentId === documentId ? search.data : undefined;
  const selected = documents.data?.documents.find((document) => document.id === documentId);
  const error = search.error as { response?: { status?: number; data?: { detail?: { error_code?: string } } } } | null;
  const errorCode = error?.response?.data?.detail?.error_code;
  const errorText = error?.response?.status === 404 ? "This document is no longer available to your account."
    : error?.response?.status === 403 ? "Your account no longer has access."
    : error?.response?.status === 409 ? "The document changed. Refresh and search again."
    : errorCode && searchErrors[errorCode] ? searchErrors[errorCode] : "Private text search is unavailable. Please try again.";

  return (
    <main className="max-w-3xl mx-auto px-5 py-10 flex flex-col gap-6">
      <Link href="/search" className="text-sm text-primary underline">Back to AI chat</Link>
      <div>
        <h1 className="text-2xl font-semibold">Private text search</h1>
        <p className="text-sm text-muted-foreground mt-2">
          Find exact passages in your PDF with page citations. Processing stays on Search Sphere and uses no AI provider.
        </p>
        <p className="text-xs text-muted-foreground mt-2">
          Works with selectable PDF text. Scans require OCR and cannot be read in this mode. AI-generated answers remain subject to processing approval.
        </p>
      </div>
      {!isAuthenticated || !ownerId ? (
        <div>
          <p className="mb-3">Sign in to search your own private documents.</p>
          <Button onClick={() => setAuthOpen(true)}>Sign in</Button>
          <AuthModal isOpen={authOpen} onClose={() => setAuthOpen(false)} initialMode="signin" />
        </div>
      ) : (
        <>
          <form onSubmit={(event) => {
            event.preventDefault();
            if (selected && query.trim()) search.mutate({ documentId, query: query.trim(), ownerId });
          }} className="flex flex-col gap-4">
            <label className="text-sm flex flex-col gap-2">
              Your PDF
              <select aria-label="Your PDF" value={documentId} onChange={(event) => { setDocumentId(event.target.value); search.reset(); }} className="border border-border rounded-md bg-background p-2">
                <option value="">Select a document</option>
                {documents.data?.documents.map((document) => <option key={document.id} value={document.id}>{document.filename}</option>)}
              </select>
            </label>
            {documents.isPending && <p role="status">Loading your documents…</p>}
            {documents.isError && <p role="alert">Your documents could not be loaded. <button type="button" onClick={() => documents.refetch()} className="underline">Retry</button></p>}
            {documents.data?.total === 0 && <Link href="/" className="underline text-primary text-sm">Upload a PDF first</Link>}
            <label className="text-sm flex flex-col gap-2">
              Words or identifier to find
              <input aria-label="Words or identifier to find" value={query} maxLength={1000} onChange={(event) => setQuery(event.target.value)} className="border border-border rounded-md bg-background p-2" />
            </label>
            <Button type="submit" disabled={!selected || !query.trim() || search.isPending}>{search.isPending ? "Searching PDF…" : "Find passages"}</Button>
          </form>
          {search.isError && <p role="alert" className="text-destructive">{errorText}</p>}
          {result && (
            <section aria-label="Matching passages" className="flex flex-col gap-4">
              <h2 className="font-semibold">Matching passages</h2>
              <p className="text-xs text-muted-foreground">Exact source excerpts for “{result.query}” · {result.pages_checked} page(s) checked · No AI generation</p>
              {!result.native_text_present ? <p>This PDF has no readable native text. Scanned pages need approved OCR processing.</p>
                : result.results.length === 0 ? <p>No matching text found. Try exact words from the document.</p>
                  : result.results.map((source) => (
                    <article key={source.citation_number} className="p-4 rounded-lg border border-border">
                      <h3 className="text-sm font-medium">[{source.citation_number}] {source.filename} · Page {source.page_number}</h3>
                      <p className="mt-2 whitespace-pre-wrap text-sm">{source.text}</p>
                    </article>
                  ))}
            </section>
          )}
        </>
      )}
    </main>
  );
}
