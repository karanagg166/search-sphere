"use client";

import React, { useEffect, useState } from "react";
import { Loader2, Sparkles, FileSearch } from "lucide-react";

interface LoadingStateProps {
  stage?: "searching" | "generating";
}

export function LoadingState({ stage }: LoadingStateProps) {
  const [currentStage, setCurrentStage] = useState<"searching" | "generating">(
    stage || "searching"
  );

  useEffect(() => {
    if (stage) {
      setCurrentStage(stage);
      return;
    }
    // Simulate progression from searching to generating if stage is not explicitly managed
    const timer = setTimeout(() => {
      setCurrentStage("generating");
    }, 1800);
    return () => clearTimeout(timer);
  }, [stage]);

  return (
    <div
      role="status"
      aria-live="polite"
      className="p-6 rounded-xl border border-border bg-card/60 backdrop-blur-sm flex flex-col items-center justify-center gap-4 text-center my-4 animate-in fade-in duration-300"
    >
      <div className="relative flex items-center justify-center">
        <div className="w-12 h-12 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center text-primary animate-pulse">
          {currentStage === "searching" ? (
            <FileSearch className="w-6 h-6 animate-bounce" />
          ) : (
            <Sparkles className="w-6 h-6 animate-spin text-primary" />
          )}
        </div>
        <Loader2 className="w-14 h-14 text-primary/30 animate-spin absolute -inset-1" />
      </div>

      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground tracking-tight">
          {currentStage === "searching"
            ? "Searching documents..."
            : "Generating answer..."}
        </p>
        <p className="text-xs text-muted-foreground">
          {currentStage === "searching"
            ? "Performing hybrid retrieval and reranking relevant document chunks..."
            : "Synthesizing grounded response with verified citations..."}
        </p>
      </div>

      <div className="w-48 h-1.5 bg-muted rounded-full overflow-hidden mt-1">
        <div
          className={`h-full bg-primary rounded-full transition-all duration-700 ease-out ${
            currentStage === "searching" ? "w-1/2" : "w-5/6"
          }`}
        />
      </div>
    </div>
  );
}

export default LoadingState;
