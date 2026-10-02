"use client";

import React, { useState, useMemo, useEffect } from "react";
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
import {
  listConversations,
  getConversation,
  createConversation,
  deleteConversation,
  answerInConversation,
  ConversationSummary,
} from "@/lib/api/conversations";
import { streamAnswer } from "@/lib/api/streaming";
import { submitMessageFeedback, submitGenericFeedback } from "@/lib/api/feedback";
import { ConversationSidebar } from "@/components/conversations/conversation-sidebar";
import { AuthModal } from "@/components/auth-modal";
import { Button } from "@/components/ui/button";
import {
  SearchInput,
  AnswerCard,
  LoadingState,
  ChatTurn,
} from "@/components/search";

function generateTurnId(): string {
  return `turn-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
}

export default function SearchPage() {
  const { user, token, isAuthenticated, logout } = useAuth();
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authModalMode, setAuthModalMode] = useState<"signin" | "signup">("signin");

  // Persistent conversation state
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
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

  // List user conversations
  const {
    data: conversations = [],
    isLoading: convsLoading,
    refetch: refetchConversations,
  } = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
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

  const handleNewChat = () => {
    setActiveConversationId(null);
    setTurns([]);
  };

  const handleSelectConversation = async (convId: string) => {
    if (convId === activeConversationId || isAnswering) return;
    setActiveConversationId(convId);

    try {
      const detail = await getConversation(convId);
      const loadedTurns: ChatTurn[] = [];
      let pendingUserTurn: ChatTurn | null = null;

      for (const msg of detail.messages) {
        if (msg.role === "user") {
          if (pendingUserTurn) {
            loadedTurns.push(pendingUserTurn);
          }
          pendingUserTurn = {
            id: msg.id,
            query: msg.content,
            status: "success",
            timestamp: new Date(msg.created_at),
          };
        } else if (msg.role === "assistant" && pendingUserTurn) {
          pendingUserTurn.answer = msg.content;
          pendingUserTurn.retrievalQuery = msg.retrieval_query || undefined;
          pendingUserTurn.rewritten = msg.rewritten;
          pendingUserTurn.sources = msg.sources || [];
          loadedTurns.push(pendingUserTurn);
          pendingUserTurn = null;
        }
      }

      if (pendingUserTurn) {
        loadedTurns.push(pendingUserTurn);
      }

      setTurns(loadedTurns);
    } catch (err) {
      console.error("Failed to load conversation messages", err);
    }
  };

  const handleDeleteConversation = async (convId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteConversation(convId);
      if (activeConversationId === convId) {
        handleNewChat();
      }
      refetchConversations();
    } catch (err) {
      console.error("Failed to delete conversation", err);
    }
  };

  const handleAskQuestion = async (queryText: string, docIdFilter?: string) => {
    if (!queryText.trim() || isAnswering) return;

    if (!isAuthenticated) {
      openAuth("signin");
      return;
    }

    const turnId = generateTurnId();
    const newTurn: ChatTurn = {
      id: turnId,
      query: queryText,
      status: "loading",
      timestamp: new Date(),
    };

    // Append new turn immediately
    setTurns((prev) => [...prev, newTurn]);
    setIsAnswering(true);

    try {
      let targetConvId = activeConversationId;

      // If no active conversation, create one first
      if (!targetConvId) {
        const created = await createConversation();
        targetConvId = created.id;
        setActiveConversationId(created.id);
      }

      const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const streamUrl = `${apiUrl}/conversations/${targetConvId}/answer/stream`;
      let accumulatedText = "";

      await streamAnswer(
        streamUrl,
        {
          query: queryText,
          document_id: docIdFilter || undefined,
          top_k: 5,
          candidate_k: 20,
        },
        token,
        {
          onMetadata: (meta) => {
            setTurns((prev) =>
              prev.map((t) =>
                t.id === turnId
                  ? {
                      ...t,
                      retrievalQuery: meta.retrieval_query,
                      rewritten: meta.rewritten,
                    }
                  : t
              )
            );
          },
          onSources: (sources) => {
            setTurns((prev) =>
              prev.map((t) =>
                t.id === turnId
                  ? {
                      ...t,
                      sources,
                    }
                  : t
              )
            );
          },
          onToken: (chunk) => {
            accumulatedText += chunk;
            setTurns((prev) =>
              prev.map((t) =>
                t.id === turnId
                  ? {
                      ...t,
                      answer: accumulatedText,
                    }
                  : t
              )
            );
          },
          onDone: (donePayload) => {
            setTurns((prev) =>
              prev.map((t) =>
                t.id === turnId
                  ? {
                      ...t,
                      id: donePayload.message_id || t.id,
                      status: "success",
                      answer: donePayload.answer || accumulatedText,
                      sources: donePayload.sources || t.sources,
                    }
                  : t
              )
            );
            refetchConversations();
          },
          onError: (errMsg) => {
            setTurns((prev) =>
              prev.map((t) =>
                t.id === turnId
                  ? {
                      ...t,
                      status: "error",
                      errorMessage: errMsg,
                    }
                  : t
              )
            );
          },
        }
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

  const handleFeedback = async (messageId: string, rating: 1 | -1) => {
    try {
      if (activeConversationId) {
        await submitMessageFeedback(activeConversationId, messageId, { rating }, token || undefined);
      } else {
        await submitGenericFeedback({ rating, message_id: messageId }, token || undefined);
      }
    } catch (err) {
      console.error("Failed to submit answer feedback", err);
    }
  };

  return (
    <main className="min-h-screen bg-background text-foreground p-4 md:p-8 max-w-7xl mx-auto flex flex-col gap-6">
      {/* Top Application Header */}
      <header className="border-b border-border pb-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
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
            Ask questions about your uploaded documents with grounded Cohere RAG and persistent chat history.
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

      {/* Main Content Layout with Sidebar */}
      <div className="flex flex-col md:flex-row gap-6 items-start w-full">
        {/* Persistent Conversations Sidebar */}
        {isAuthenticated && (
          <ConversationSidebar
            conversations={conversations}
            activeId={activeConversationId}
            onSelect={handleSelectConversation}
            onNewChat={handleNewChat}
            onDelete={handleDeleteConversation}
            isLoading={convsLoading}
          />
        )}

        {/* Main Conversation Thread */}
        <section className="flex flex-col gap-5 flex-1 min-w-0 w-full min-h-[450px]">
          {/* Controls row: Clear thread & Conversation counter */}
          {turns.length > 0 && (
            <div className="flex items-center justify-between text-xs text-muted-foreground px-1">
              <span>
                Conversation turns: <span className="font-semibold text-foreground">{turns.length}</span>
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={handleNewChat}
                disabled={isAnswering}
                className="h-7 px-2.5 text-xs flex items-center gap-1.5"
              >
                <RotateCcw className="w-3 h-3" />
                New Chat
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
                onFeedback={handleFeedback}
              />
            ))}

            {/* Active Loading State */}
            {isAnswering && <LoadingState />}
          </div>

          {/* Bottom Search Input */}
          <div className="sticky bottom-6 pt-2">
            <SearchInput
              onSearch={handleAskQuestion}
              isLoading={isAnswering}
              documents={documentOptions}
              selectedDocumentId={selectedDocId}
              onSelectDocumentId={setSelectedDocId}
            />
          </div>
        </section>
      </div>

      {/* Auth Modal */}
      <AuthModal
        isOpen={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        initialMode={authModalMode}
      />
    </main>
  );
}
