"use client";

import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "@/lib/api";
import { useAuth } from "@/context/auth-context";
import { AuthModal } from "@/components/auth-modal";
import { Button } from "@/components/ui/button";
import {
  Activity,
  CheckCircle2,
  Search,
  XCircle,
  LogIn,
  UserPlus,
  LogOut,
} from "lucide-react";
import { DocumentUpload } from "@/components/documents/document-upload";
import { DocumentList } from "@/components/documents/document-list";

export default function Home() {
  const { user, isAuthenticated, logout } = useAuth();
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authModalMode, setAuthModalMode] = useState<"signin" | "signup">("signin");

  const { data: health, isLoading, isError } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 15000,
  });

  const openAuth = (mode: "signin" | "signup") => {
    setAuthModalMode(mode);
    setAuthModalOpen(true);
  };

  return (
    <main className="min-h-screen bg-background text-foreground p-6 md:p-12 max-w-5xl mx-auto flex flex-col gap-8">
      {/* Top Application Header */}
      <header className="border-b border-border pb-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl md:text-3xl font-bold tracking-tight flex items-center gap-3">
            <Search className="w-7 h-7 text-primary" />
            Search Sphere
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Document management and semantic search workspace.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* Backend Connection Indicator */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-border bg-card text-xs font-medium">
            <Activity className="w-3.5 h-3.5 text-primary" />
            <span>API:</span>
            {isLoading ? (
              <span className="text-muted-foreground">Connecting...</span>
            ) : isError ? (
              <span className="text-red-400 flex items-center gap-1">
                <XCircle className="w-3.5 h-3.5" /> Offline
              </span>
            ) : (
              <span className="text-emerald-400 flex items-center gap-1">
                <CheckCircle2 className="w-3.5 h-3.5" /> Connected
              </span>
            )}
          </div>

          {/* User Authentication Menu */}
          {isAuthenticated && user ? (
            <div className="flex items-center gap-3 bg-card border border-border px-3.5 py-1.5 rounded-full shadow-sm">
              <div className="w-7 h-7 rounded-full bg-primary/20 text-primary flex items-center justify-center font-semibold text-xs overflow-hidden border border-primary/30">
                {user.avatar_url ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img src={user.avatar_url} alt={user.name || "Avatar"} className="w-full h-full object-cover" />
                ) : (
                  (user.name || user.email).charAt(0).toUpperCase()
                )}
              </div>
              <div className="flex flex-col text-left">
                <span className="text-xs font-medium leading-none">{user.name || user.email.split("@")[0]}</span>
                <span className="text-[10px] text-muted-foreground leading-tight">{user.email}</span>
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

      {/* Document Upload Section */}
      <section>
        <DocumentUpload onRequireAuth={() => openAuth("signin")} />
      </section>

      {/* Stored Documents Section */}
      <section>
        <DocumentList />
      </section>

      {/* Auth Modal Dialog */}
      <AuthModal
        isOpen={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        initialMode={authModalMode}
      />
    </main>
  );
}
