"use client";

import React, { useState } from "react";
import { BookOpen, ChevronDown, ChevronUp, FileText } from "lucide-react";
import { AnswerSource } from "@/lib/api/search";

interface SourceListProps {
  sources: AnswerSource[];
  documentMap?: Record<string, string>;
}

export function SourceList({ sources, documentMap = {} }: SourceListProps) {
  const [expanded, setExpanded] = useState(true);

  if (!sources || sources.length === 0) {
    return null;
  }

  return (
    <div className="mt-4 pt-4 border-t border-border/80">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
          <BookOpen className="w-3.5 h-3.5 text-primary" />
          Sources ({sources.length})
        </h3>
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors"
          aria-expanded={expanded}
        >
          <span>{expanded ? "Collapse" : "Show all"}</span>
          {expanded ? (
            <ChevronUp className="w-3.5 h-3.5" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5" />
          )}
        </button>
      </div>

      {expanded && (
        <div className="grid grid-cols-1 gap-2.5">
          {sources.map((source) => {
            const documentName =
              documentMap[source.document_id] || source.document_id;
            const pageText =
              source.start_page === source.end_page
                ? `Page ${source.start_page}`
                : `Pages ${source.start_page}–${source.end_page}`;

            return (
              <div
                key={source.source_id}
                className="p-3 rounded-lg border border-border/70 bg-card/40 hover:bg-card/70 transition-colors text-xs flex flex-col gap-1.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="px-1.5 py-0.5 rounded bg-primary/15 text-primary font-bold text-[11px] shrink-0">
                      [{source.source_id}]
                    </span>
                    <div className="flex items-center gap-1.5 min-w-0">
                      <FileText className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                      <span
                        className="font-medium text-foreground truncate"
                        title={documentName}
                      >
                        {documentName}
                      </span>
                    </div>
                  </div>
                  <span className="text-[11px] text-muted-foreground shrink-0 bg-muted/50 px-2 py-0.5 rounded">
                    {pageText}
                  </span>
                </div>

                <div className="mt-1 text-muted-foreground bg-background/50 p-2.5 rounded border border-border/40 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words">
                  &ldquo;{source.content}&rdquo;
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default SourceList;
