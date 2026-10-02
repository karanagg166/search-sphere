import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ConversationSidebar } from "./conversation-sidebar";
import { ConversationSummary } from "@/lib/api/conversations";

describe("ConversationSidebar", () => {
  const sampleConversations: ConversationSummary[] = [
    {
      id: "conv-1",
      user_id: "user-1",
      title: "Project Architecture Discussion",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      message_count: 4,
    },
    {
      id: "conv-2",
      user_id: "user-1",
      title: "Redis Eviction Strategies",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      message_count: 2,
    },
  ];

  it("renders conversation list and handles selection", () => {
    const handleSelect = vi.fn();
    const handleNewChat = vi.fn();
    const handleDelete = vi.fn();

    render(
      <ConversationSidebar
        conversations={sampleConversations}
        activeId="conv-1"
        onSelect={handleSelect}
        onNewChat={handleNewChat}
        onDelete={handleDelete}
      />
    );

    expect(screen.getByText("Project Architecture Discussion")).toBeDefined();
    expect(screen.getByText("Redis Eviction Strategies")).toBeDefined();

    fireEvent.click(screen.getByText("Redis Eviction Strategies"));
    expect(handleSelect).toHaveBeenCalledWith("conv-2");
  });

  it("triggers onNewChat when New button is clicked", () => {
    const handleNewChat = vi.fn();
    render(
      <ConversationSidebar
        conversations={sampleConversations}
        activeId={null}
        onSelect={vi.fn()}
        onNewChat={handleNewChat}
        onDelete={vi.fn()}
      />
    );

    const newBtn = screen.getByRole("button", { name: /new/i });
    fireEvent.click(newBtn);
    expect(handleNewChat).toHaveBeenCalledTimes(1);
  });

  it("displays empty state when no conversations exist", () => {
    render(
      <ConversationSidebar
        conversations={[]}
        activeId={null}
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
        onDelete={vi.fn()}
      />
    );

    expect(screen.getByText(/no conversations yet/i)).toBeDefined();
  });
});
