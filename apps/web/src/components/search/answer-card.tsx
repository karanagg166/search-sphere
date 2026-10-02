"use client";

import React from "react";
import {
  MessageSquare,
  Bot,
  AlertCircle,
  Clock,
  RotateCcw,
  Sparkles,
  ThumbsUp,
  ThumbsDown,
} from "lucide-react";
import { AnswerSource } from "@/lib/api/search";
import { SourceList } from "./source-list";

export interface ChatTurn {
  id: string;
  query: string;
  retrievalQuery?: string;
  rewritten?: boolean;
  answer?: string;
  sources?: AnswerSource[];
  status: "loading" | "success" | "error";
  errorMessage?: string;
  timestamp: Date;
}

interface AnswerCardProps {
  turn: ChatTurn;
  documentMap?: Record<string, string>;
  onRetry?: (query: string) => void;
  onFeedback?: (turnId: string, rating: 1 | -1) => void;
}

/**
 * Safely parses and renders LLM answer text into React DOM elements
 * without dangerouslySetInnerHTML. Safely highlights [n] citations,
 * formats code blocks, bullet/numbered lists, and paragraphs.
 */
function SafeFormattedAnswer({ text }: { text: string }) {
  if (!text) return null;

  // Split by code blocks: ```code```
  const parts = text.split(/(```[\s\S]*?```)/g);

  return (
    <div className="space-y-3 text-sm leading-relaxed text-foreground/90">
      {parts.map((part, index) => {
        if (part.startsWith("```") && part.endsWith("```")) {
          const lines = part.slice(3, -3).replace(/^\w+\n/, "");
          return (
            <pre
              key={index}
              className="p-3.5 my-2.5 rounded-lg bg-background/80 border border-border font-mono text-xs overflow-x-auto text-emerald-400"
            >
              <code>{lines}</code>
            </pre>
          );
        }

        // Split paragraphs by double line break
        const paragraphs = part.split(/\n\s*\n/);

        return (
          <React.Fragment key={index}>
            {paragraphs.map((para, pIdx) => {
              const trimmed = para.trim();
              if (!trimmed) return null;

              const lines = trimmed.split("\n");

              // Unordered list
              if (lines.every((l) => /^\s*[-*]\s+/.test(l))) {
                return (
                  <ul key={pIdx} className="list-disc pl-5 space-y-1 my-2">
                    {lines.map((l, lIdx) => (
                      <li key={lIdx}>
                        {renderInlineFormatted(l.replace(/^\s*[-*]\s+/, ""))}
                      </li>
                    ))}
                  </ul>
                );
              }

              // Numbered list
              if (lines.every((l) => /^\s*\d+\.\s+/.test(l))) {
                return (
                  <ol key={pIdx} className="list-decimal pl-5 space-y-1 my-2">
                    {lines.map((l, lIdx) => (
                      <li key={lIdx}>
                        {renderInlineFormatted(l.replace(/^\s*\d+\.\s+/, ""))}
                      </li>
                    ))}
                  </ol>
                );
              }

              // Normal paragraph with line-break preservation
              return (
                <p key={pIdx} className="leading-relaxed">
                  {lines.map((line, lIdx) => (
                    <React.Fragment key={lIdx}>
                      {renderInlineFormatted(line)}
                      {lIdx < lines.length - 1 && <br />}
                    </React.Fragment>
                  ))}
                </p>
              );
            })}
          </React.Fragment>
        );
      })}
    </div>
  );
}

/**
 * Highlights citation references [1], [2], etc., as styled badge elements
 * while keeping plain text safe from HTML injection.
 */
function renderInlineFormatted(str: string): React.ReactNode[] {
  const tokens = str.split(/(\[\d+\])/g);
  return tokens.map((tok, i) => {
    if (/^\[\d+\]$/.test(tok)) {
      return (
        <span
          key={i}
          className="inline-flex items-center justify-center px-1.5 py-0.2 mx-0.5 rounded text-[11px] font-semibold bg-primary/20 text-primary border border-primary/30 align-baseline cursor-default"
          title={`Attributed Source Citation ${tok}`}
        >
          {tok}
        </span>
      );
    }
    return tok;
  });
}

export function AnswerCard({ turn, documentMap = {}, onRetry, onFeedback }: AnswerCardProps) {
  const [userRating, setUserRating] = React.useState<number | null>(null);

  return (
    <div className="flex flex-col gap-4 p-5 rounded-xl border border-border bg-card shadow-sm transition">
      {/* User Query Turn */}
      <div className="flex items-start gap-3">
        <div className="w-7 h-7 rounded-full bg-secondary flex items-center justify-center text-muted-foreground shrink-0 mt-0.5">
          <MessageSquare className="w-3.5 h-3.5" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-foreground">You asked</span>
            <span className="text-[11px] text-muted-foreground flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {turn.timestamp.toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
          </div>
          <p className="text-sm font-medium text-foreground mt-1 break-words">
            {turn.query}
          </p>
        </div>
      </div>

      {/* Query Reformulation Indicator */}
      {turn.rewritten && turn.retrievalQuery && (
        <div className="ml-10 px-3 py-1.5 rounded-md bg-muted/40 border border-border/50 text-[11px] text-muted-foreground flex items-center gap-2">
          <Sparkles className="w-3 h-3 text-primary shrink-0" />
          <span className="truncate">
            Rewritten for search:{" "}
            <span className="text-foreground font-mono">
              &ldquo;{turn.retrievalQuery}&rdquo;
            </span>
          </span>
        </div>
      )}

      {/* Assistant Answer Turn */}
      <div className="flex items-start gap-3 border-t border-border/60 pt-4">
        <div className="w-7 h-7 rounded-full bg-primary/20 text-primary flex items-center justify-center shrink-0 mt-0.5 border border-primary/30">
          <Bot className="w-3.5 h-3.5" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2 mb-2">
            <span className="text-xs font-semibold text-primary flex items-center gap-1.5">
              Search Sphere Answer
            </span>
          </div>

          {turn.status === "error" ? (
            <div className="p-3.5 rounded-lg bg-destructive/10 border border-destructive/30 text-destructive text-xs flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span className="font-semibold">
                  Unable to generate answer. Please try again.
                </span>
              </div>
              {turn.errorMessage && (
                <p className="text-[11px] opacity-90 pl-6">{turn.errorMessage}</p>
              )}
              {onRetry && (
                <button
                  type="button"
                  onClick={() => onRetry(turn.query)}
                  className="self-start mt-1 pl-6 text-xs underline font-medium hover:opacity-80 flex items-center gap-1"
                >
                  <RotateCcw className="w-3 h-3" />
                  Retry Question
                </button>
              )}
            </div>
          ) : turn.answer ? (
            <>
              <SafeFormattedAnswer text={turn.answer} />
              {turn.sources && turn.sources.length > 0 && (
                <SourceList sources={turn.sources} documentMap={documentMap} />
              )}
              {turn.status !== "loading" && (
                <div className="flex items-center gap-2 mt-3 pt-2.5 border-t border-border/40 text-xs text-muted-foreground">
                  <span className="text-[11px]">Was this answer accurate?</span>
                  <button
                    type="button"
                    aria-label="Thumbs up"
                    onClick={() => {
                      setUserRating(1);
                      onFeedback?.(turn.id, 1);
                    }}
                    className={`p-1 rounded hover:bg-muted transition-colors ${userRating === 1 ? "text-emerald-500 font-semibold" : ""}`}
                    title="Accurate / helpful"
                  >
                    <ThumbsUp className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    aria-label="Thumbs down"
                    onClick={() => {
                      setUserRating(-1);
                      onFeedback?.(turn.id, -1);
                    }}
                    className={`p-1 rounded hover:bg-muted transition-colors ${userRating === -1 ? "text-rose-500 font-semibold" : ""}`}
                    title="Inaccurate / unhelpful"
                  >
                    <ThumbsDown className="w-3.5 h-3.5" />
                  </button>
                  {userRating !== null && (
                    <span className="text-[11px] text-muted-foreground/80 italic ml-1">
                      Feedback saved
                    </span>
                  )}
                </div>
              )}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default AnswerCard;
