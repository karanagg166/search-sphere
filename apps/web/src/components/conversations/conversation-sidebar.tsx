"use client";

import React from "react";
import { MessageSquare, Plus, Trash2, Clock, Sparkles } from "lucide-react";
import { ConversationSummary } from "@/lib/api/conversations";
import { Button } from "@/components/ui/button";

interface ConversationSidebarProps {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDelete: (id: string, e: React.MouseEvent) => void;
  isLoading?: boolean;
}

export function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onNewChat,
  onDelete,
  isLoading,
}: ConversationSidebarProps) {
  return (
    <aside className="w-full md:w-64 flex flex-col gap-3 shrink-0 border border-border bg-card/60 backdrop-blur-sm rounded-xl p-3.5 shadow-sm h-fit max-h-[700px]">
      <div className="flex items-center justify-between pb-2 border-b border-border/60">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          <MessageSquare className="w-3.5 h-3.5 text-primary" />
          <span>Conversations</span>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={onNewChat}
          className="h-7 px-2.5 text-xs flex items-center gap-1 font-medium bg-background hover:bg-muted"
        >
          <Plus className="w-3.5 h-3.5 text-primary" />
          <span>New</span>
        </Button>
      </div>

      <div className="flex flex-col gap-1.5 overflow-y-auto pr-1">
        {isLoading && (
          <div className="text-xs text-muted-foreground py-4 text-center">
            Loading conversations...
          </div>
        )}

        {!isLoading && conversations.length === 0 && (
          <div className="py-6 px-3 text-center text-xs text-muted-foreground flex flex-col items-center gap-2">
            <Sparkles className="w-5 h-5 text-muted-foreground/40" />
            <p>No conversations yet. Start a new chat to begin.</p>
          </div>
        )}

        {conversations.map((conv) => {
          const isActive = conv.id === activeId;
          const formattedDate = conv.updated_at
            ? new Date(conv.updated_at).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
              })
            : "";
          const displayTitle = conv.title?.trim() || "Untitled Conversation";

          return (
            <div
              key={conv.id}
              onClick={() => onSelect(conv.id)}
              className={`group flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg text-xs cursor-pointer transition-colors border ${
                isActive
                  ? "bg-primary/10 border-primary/40 text-foreground font-medium"
                  : "border-transparent hover:bg-muted/80 text-muted-foreground hover:text-foreground"
              }`}
            >
              <div className="flex flex-col gap-0.5 truncate flex-1">
                <span className="truncate text-left">{displayTitle}</span>
                <div className="flex items-center gap-2 text-[10px] text-muted-foreground/80">
                  <span className="flex items-center gap-1">
                    <Clock className="w-2.5 h-2.5" />
                    {formattedDate}
                  </span>
                  <span>•</span>
                  <span>{conv.message_count} msgs</span>
                </div>
              </div>

              <button
                type="button"
                onClick={(e) => onDelete(conv.id, e)}
                className="opacity-0 group-hover:opacity-100 p-1 hover:text-destructive rounded transition-opacity"
                title="Delete conversation"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
