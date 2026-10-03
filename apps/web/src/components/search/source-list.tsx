"use client";

import React, { useMemo, useState } from "react";
import { BookOpen, ChevronDown, ChevronUp, FileText } from "lucide-react";
import { AnswerSource } from "@/lib/api/search";

interface SourceListProps {
  sources: AnswerSource[];
  documentMap?: Record<string, string>;
  defaultExpanded?: boolean;
}

export function SourceList({
  sources,
  documentMap = {},
  defaultExpanded = false,
}: SourceListProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [openSourceIds, setOpenSourceIds] = useState<Record<number, boolean>>({});

  // Sort sources so highest relevance (rerank_score) comes first
  const sortedSources = useMemo(() => {
    if (!sources) return [];
    return [...sources].sort((a, b) => {
      const scoreA = typeof a.rerank_score === "number" ? a.rerank_score : 0;
      const scoreB = typeof b.rerank_score === "number" ? b.rerank_score : 0;
      return scoreB - scoreA;
    });
  }, [sources]);

  if (!sortedSources || sortedSources.length === 0) {
    return null;
  }

  const toggleSource = (sourceId: number) => {
    setOpenSourceIds((prev) => ({
      ...prev,
      [sourceId]: !prev[sourceId],
    }));
  };

  const toggleAllExcerpts = () => {
    const allOpen = sortedSources.every((s) => openSourceIds[s.source_id]);
    const nextState: Record<number, boolean> = {};
    for (const s of sortedSources) {
      nextState[s.source_id] = !allOpen;
    }
    setOpenSourceIds(nextState);
  };

  const areAllExcerptsOpen = sortedSources.every((s) => openSourceIds[s.source_id]);

  return (
    <div className="mt-4 pt-4 border-t border-border/80">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
          <BookOpen className="w-3.5 h-3.5 text-primary" />
          Sources ({sortedSources.length})
        </h3>
        <div className="flex items-center gap-3">
          {expanded && sortedSources.length > 1 && (
            <button
              type="button"
              onClick={toggleAllExcerpts}
              className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
            >
              {areAllExcerptsOpen ? "Collapse all excerpts" : "Expand all excerpts"}
            </button>
          )}
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors"
            aria-expanded={expanded}
          >
            <span>{expanded ? "Hide sources" : "Show sources"}</span>
            {expanded ? (
              <ChevronUp className="w-3.5 h-3.5" />
            ) : (
              <ChevronDown className="w-3.5 h-3.5" />
            )}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="grid grid-cols-1 gap-2">
          {sortedSources.map((source) => {
            const documentName =
              documentMap[source.document_id] || source.document_id;
            const pageText =
              source.start_page === source.end_page
                ? `Page ${source.start_page}`
                : `Pages ${source.start_page}–${source.end_page}`;
            const isSnippetOpen = openSourceIds[source.source_id] ?? false;

            return (
              <div
                key={source.source_id}
                className="rounded-lg border border-border/70 bg-card/40 hover:bg-card/70 transition-colors text-xs overflow-hidden flex flex-col"
              >
                <button
                  type="button"
                  onClick={() => toggleSource(source.source_id)}
                  className="p-2.5 flex items-center justify-between gap-2 text-left hover:bg-muted/40 transition-colors w-full focus:outline-none"
                  aria-expanded={isSnippetOpen}
                >
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
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-[11px] text-muted-foreground bg-muted/50 px-2 py-0.5 rounded">
                      {pageText}
                    </span>
                    {isSnippetOpen ? (
                      <ChevronUp className="w-3.5 h-3.5 text-muted-foreground" />
                    ) : (
                      <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
                    )}
                  </div>
                </button>

                {isSnippetOpen && (
                  <div className="px-2.5 pb-2.5 pt-0">
                    <div className="text-muted-foreground bg-background/50 p-2.5 rounded border border-border/40 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words">
                      &ldquo;{source.content}&rdquo;
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default SourceList;
