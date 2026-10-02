"use client";

import React, { useState, useMemo } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Search,
  Sparkles,
  RotateCcw,
  FileText,
  ArrowLeft,
  CheckCircle2,
  XCircle,
  Activity,
  LogIn,
  UserPlus,
  LogOut,
  HelpCircle,
} from "lucide-react";
import { useAuth } from "@/context/auth-context";
import { fetchDocuments, fetchHealth } from "@/lib/api";
import {
  answerQuestion,
  ConversationMessage,
} from "@/lib/api/search";
import { AuthModal } from "@/components/auth-modal";
import { Button } from "@/components/ui/button";
import {
  SearchInput,
  AnswerCard,
  LoadingState,
  ChatTurn,
} from "@/components/search";

export default function SearchPage() {
  const { user, isAuthenticated, logout } = useAuth();
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authModalMode, setAuthModalMode] = useState<"signin" | "signup">("signin");

  // In-memory conversation thread state for follow-up questions
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [isAnswering, setIsAnswering] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState<string | undefined>(undefined);

  // Health status
  const { data: health, isLoading: healthLoading, isError: healthError } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 15000,
  });

  // Stored documents for filename resolution and scoping
  const { data: docData } = useQuery({
    queryKey: ["documents"],
    queryFn: fetchDocuments,
    enabled: isAuthenticated,
    refetchOnWindowFocus: false,
  });

  // Create document ID to filename mapping
  const documentMap = useMemo<Record<string, string>>(() => {
    const map: Record<string, string> = {};
    if (docData?.documents) {
      for (const doc of docData.documents) {
        map[doc.id] = doc.filename;
      }
    }
    return map;
  }, [docData]);

  const documentOptions = useMemo(() => {
    if (!docData?.documents) return [];
    return docData.documents.map((d) => ({
      id: d.id,
      filename: d.filename,
    }));
  }, [docData]);

  const openAuth = (mode: "signin" | "signup") => {
    setAuthModalMode(mode);
    setAuthModalOpen(true);
  };

  const handleClearChat = () => {
    setTurns([]);
  };

  const handleAskQuestion = async (queryText: string, docIdFilter?: string) => {
    if (!queryText.trim() || isAnswering) return;

    if (!isAuthenticated) {
      openAuth("signin");
      return;
    }

    const turnId = `turn-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
    const newTurn: ChatTurn = {
      id: turnId,
      query: queryText,
      status: "loading",
      timestamp: new Date(),
    };

    // Append new turn immediately
    setTurns((prev) => [...prev, newTurn]);
    setIsAnswering(true);

    // Prepare in-memory conversation context from previous successful turns
    const conversationContext: ConversationMessage[] = [];
    for (const turn of turns) {
      if (turn.status === "success" && turn.answer) {
        conversationContext.push({
          role: "user",
          content: turn.query,
        });
        conversationContext.push({
          role: "assistant",
          content: turn.answer,
        });
      }
    }

    try {
      const response = await answerQuestion({
        query: queryText,
        conversation_context: conversationContext,
        top_k: 5,
        candidate_k: 20,
        document_id: docIdFilter || undefined,
      });

      // Update turn with synthesized answer and source attributions
      setTurns((prev) =>
        prev.map((t) =>
          t.id === turnId
            ? {
                ...t,
                status: "success",
                answer: response.answer,
                sources: response.sources,
                retrievalQuery: response.retrieval_query,
                rewritten: response.rewritten,
              }
            : t
        )
      );
    } catch (err: any) {
      const backendMessage =
        err?.response?.data?.detail ||
        err?.message ||
        "Unable to generate answer. Please try again.";

      setTurns((prev) =>
        prev.map((t) =>
          t.id === turnId
            ? {
                ...t,
                status: "error",
                errorMessage: backendMessage,
              }
            : t
        )
      );
    } finally {
      setIsAnswering(false);
    }
  };

  return (
    <main className="min-h-screen bg-background text-foreground p-6 md:p-12 max-w-5xl mx-auto flex flex-col gap-6">
      {/* Top Application Header */}
      <header className="border-b border-border pb-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors px-2.5 py-1 rounded-md border border-border bg-card"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              <span>Documents</span>
            </Link>
            <h1 className="text-2xl md:text-3xl font-bold tracking-tight flex items-center gap-2.5">
              <Sparkles className="w-6 h-6 text-primary" />
              Search Sphere Q&amp;A
            </h1>
          </div>
          <p className="text-muted-foreground mt-1.5 text-xs md:text-sm">
            Ask questions about your uploaded documents with grounded Cohere RAG.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* Backend Connection Indicator */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-border bg-card text-xs font-medium">
            <Activity className="w-3.5 h-3.5 text-primary" />
            <span>API:</span>
            {healthLoading ? (
              <span className="text-muted-foreground">Connecting...</span>
            ) : healthError ? (
              <span className="text-red-400 flex items-center gap-1">
                <XCircle className="w-3.5 h-3.5" /> Offline
              </span>
            ) : (
              <span className="text-emerald-400 flex items-center gap-1">
                <CheckCircle2 className="w-3.5 h-3.5" /> Connected
              </span>
            )}
          </div>

          {/* User Auth Menu */}
          {isAuthenticated && user ? (
            <div className="flex items-center gap-3 bg-card border border-border px-3.5 py-1.5 rounded-full shadow-sm">
              <div className="w-7 h-7 rounded-full bg-primary/20 text-primary flex items-center justify-center font-semibold text-xs overflow-hidden border border-primary/30">
                {user.avatar_url ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img
                    src={user.avatar_url}
                    alt={user.name || "Avatar"}
                    className="w-full h-full object-cover"
                  />
                ) : (
                  (user.name || user.email).charAt(0).toUpperCase()
                )}
              </div>
              <div className="flex flex-col text-left">
                <span className="text-xs font-medium leading-none">
                  {user.name || user.email.split("@")[0]}
                </span>
                <span className="text-[10px] text-muted-foreground leading-tight">
                  {user.email}
                </span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={logout}
                className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive"
                title="Log Out"
              >
                <LogOut className="w-3.5 h-3.5" />
              </Button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => openAuth("signin")}
                className="flex items-center gap-1.5 text-xs font-medium"
              >
                <LogIn className="w-3.5 h-3.5" />
                Sign In
              </Button>
              <Button
                variant="default"
                size="sm"
                onClick={() => openAuth("signup")}
                className="flex items-center gap-1.5 text-xs font-medium"
              >
                <UserPlus className="w-3.5 h-3.5" />
                Sign Up
              </Button>
            </div>
          )}
        </div>
      </header>

      {/* Main Conversation Container */}
      <section className="flex flex-col gap-5 flex-1 min-h-[400px]">
        {/* Controls row: Clear thread & Conversation counter */}
        {turns.length > 0 && (
          <div className="flex items-center justify-between text-xs text-muted-foreground px-1">
            <span>
              Conversation turns: <span className="font-semibold text-foreground">{turns.length}</span>
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={handleClearChat}
              disabled={isAnswering}
              className="h-7 px-2.5 text-xs flex items-center gap-1.5"
            >
              <RotateCcw className="w-3 h-3" />
              New Conversation
            </Button>
          </div>
        )}

        {/* Empty State */}
        {turns.length === 0 && (
          <div className="py-14 px-6 text-center border border-dashed border-border rounded-xl bg-card/40 flex flex-col items-center justify-center gap-4 my-auto">
            <div className="w-14 h-14 rounded-2xl bg-primary/10 border border-primary/20 flex items-center justify-center text-primary">
              <Search className="w-7 h-7" />
            </div>

            <div className="max-w-md space-y-1.5">
              <h2 className="text-base font-semibold text-foreground">
                Ask questions about your uploaded documents.
              </h2>
              <p className="text-xs text-muted-foreground leading-relaxed">
                Search Sphere uses hybrid vector-lexical search and Cohere LLM to synthesize
                accurate, grounded answers with citations from your indexed PDFs.
              </p>
            </div>

            {/* Document readiness indicator */}
            {isAuthenticated && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground bg-muted/40 px-3.5 py-1.5 rounded-full border border-border/60">
                <FileText className="w-3.5 h-3.5 text-primary" />
                <span>
                  {docData?.documents?.length
                    ? `${docData.documents.length} document(s) available for search`
                    : "No documents uploaded yet. Upload a PDF from the Documents page first."}
                </span>
              </div>
            )}

            {/* Starter Suggestion Pills */}
            <div className="mt-2 flex flex-wrap justify-center gap-2 max-w-lg">
              {[
                "Summarize the main points of my documents",
                "What are the key technical concepts explained?",
                "What conclusions or next steps are mentioned?",
              ].map((suggestion, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => handleAskQuestion(suggestion, selectedDocId)}
                  className="text-[11px] px-3 py-1.5 rounded-full border border-border bg-card hover:bg-muted text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1.5 text-left"
                >
                  <HelpCircle className="w-3 h-3 text-primary/70" />
                  <span>&ldquo;{suggestion}&rdquo;</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Thread of Turns */}
        <div className="flex flex-col gap-4">
          {turns.map((turn) => (
            <AnswerCard
              key={turn.id}
              turn={turn}
              documentMap={documentMap}
              onRetry={(q) => handleAskQuestion(q, selectedDocId)}
            />
          ))}

          {/* Active Loading State */}
          {isAnswering && <LoadingState />}
        </div>
      </section>

      {/* Sticky Bottom Search Input */}
      <footer className="sticky bottom-6 pt-2">
        <SearchInput
          onSearch={handleAskQuestion}
          isLoading={isAnswering}
          documents={documentOptions}
          selectedDocumentId={selectedDocId}
          onSelectDocumentId={setSelectedDocId}
        />
      </footer>

      {/* Auth Modal */}
      <AuthModal
        isOpen={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        initialMode={authModalMode}
      />
    </main>
  );
}
