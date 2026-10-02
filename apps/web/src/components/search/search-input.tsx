"use client";

import React, { useState } from "react";
import { Search, Send, Filter, X } from "lucide-react";
import { Button } from "@/components/ui/button";

export interface DocumentOption {
  id: string;
  filename: string;
}

interface SearchInputProps {
  onSearch: (query: string, documentId?: string) => void;
  isLoading: boolean;
  placeholder?: string;
  documents?: DocumentOption[];
  selectedDocumentId?: string;
  onSelectDocumentId?: (id: string | undefined) => void;
}

export function SearchInput({
  onSearch,
  isLoading,
  placeholder = "Ask a question about your uploaded documents...",
  documents = [],
  selectedDocumentId,
  onSelectDocumentId,
}: SearchInputProps) {
  const [query, setQuery] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed || isLoading) return;
    onSearch(trimmed, selectedDocumentId);
    setQuery("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="p-4 rounded-xl border border-border bg-card shadow-sm flex flex-col gap-3"
    >
      <div className="flex items-center justify-between gap-2">
        <label
          htmlFor="search-query-input"
          className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5"
        >
          <Search className="w-3.5 h-3.5 text-primary" />
          Ask a Question
        </label>

        {documents.length > 0 && onSelectDocumentId && (
          <div className="flex items-center gap-1.5 text-xs">
            <Filter className="w-3 h-3 text-muted-foreground" />
            <select
              aria-label="Filter by document"
              value={selectedDocumentId || ""}
              onChange={(e) =>
                onSelectDocumentId(e.target.value ? e.target.value : undefined)
              }
              className="bg-background border border-border text-foreground text-xs rounded-md px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary truncate max-w-[200px]"
            >
              <option value="">All Documents ({documents.length})</option>
              {documents.map((doc) => (
                <option key={doc.id} value={doc.id}>
                  {doc.filename}
                </option>
              ))}
            </select>
            {selectedDocumentId && (
              <button
                type="button"
                onClick={() => onSelectDocumentId(undefined)}
                title="Clear document filter"
                className="text-muted-foreground hover:text-foreground p-0.5"
              >
                <X className="w-3 h-3" />
              </button>
            )}
          </div>
        )}
      </div>

      <div className="relative flex items-center gap-2">
        <input
          id="search-query-input"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={isLoading}
          autoComplete="off"
          className="flex-1 bg-background border border-input rounded-lg px-3.5 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-50 transition"
        />

        <Button
          type="submit"
          disabled={isLoading || !query.trim()}
          className="flex items-center gap-2 shrink-0 px-4 h-10 font-medium"
        >
          <Send className="w-4 h-4" />
          <span>Ask</span>
        </Button>
      </div>

      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>Grounded retrieval with Cohere RAG</span>
        <span>Press Enter to send</span>
      </div>
    </form>
  );
}

export default SearchInput;
