import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { answerInConversation } from "@/lib/api/conversations";
import SearchPage from "./page";

vi.mock("@/context/auth-context", () => ({
  useAuth: () => ({ user: { name: "Synthetic", email: "synthetic@example.invalid" }, token: "synthetic-token", isAuthenticated: true, logout: vi.fn() }),
}));
vi.mock("@/context/backend-availability-context", () => ({
  useBackendAvailability: () => ({ isReady: true, isWaking: false, isOffline: false, status: "ready" }),
}));
vi.mock("@/components/backend-status-indicator", () => ({ BackendStatusIndicator: () => null }));
vi.mock("@/lib/api", () => ({ fetchDocuments: vi.fn(async () => ({ documents: [], total: 0 })) }));
vi.mock("@/lib/api/conversations", () => ({
  listConversations: vi.fn(async () => []),
  getConversation: vi.fn(),
  createConversation: vi.fn(async () => ({ id: "synthetic-conversation" })),
  deleteConversation: vi.fn(),
  answerInConversation: vi.fn(async () => ({ answer: "Compatible JSON answer", sources: [] })),
}));

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function ask() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><SearchPage /></QueryClientProvider>);
  await act(async () => {
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Explain the synthetic leave policy" } });
    fireEvent.submit(screen.getByRole("textbox").closest("form")!);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("Conversation provider failures", () => {
  it.each(["http", "sse"])("explains blocked processing and makes no second generation request after a %s error", async (kind) => {
    const payload = { error_code: "provider_not_approved", detail: "Model inference is unavailable for this request." };
    const fetch = vi.fn(async () => kind === "http"
      ? new Response(JSON.stringify(payload), { status: 503 })
      : new Response('event: error\ndata: {"code":"provider_not_approved","error":"Model inference is unavailable for this request."}\n\n'));
    vi.stubGlobal("fetch", fetch);
    await ask();
    expect(await screen.findByText(/administrator must approve these documents for external AI processing/)).toBeDefined();
    expect(screen.getByText("AI processing approval required.")).toBeDefined();
    expect(screen.queryByRole("button", { name: /retry question/i })).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(answerInConversation).not.toHaveBeenCalled();
  });

  it("does not replay a generation when the connection closes before completion", async () => {
    const fetch = vi.fn().mockRejectedValue(new Error("Network connection closed abruptly."));
    vi.stubGlobal("fetch", fetch);
    await ask();
    expect(await screen.findByText("Network connection closed abruptly.")).toBeDefined();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(answerInConversation).not.toHaveBeenCalled();
  });

  it("preserves approval-required behavior when the compatible JSON route rejects processing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Unsupported route", { status: 404 })));
    vi.mocked(answerInConversation).mockRejectedValueOnce({
      response: { status: 503, data: { error_code: "provider_not_approved" } },
    });
    await ask();
    expect(await screen.findByText("AI processing approval required.")).toBeDefined();
    expect(screen.queryByRole("button", { name: /retry question/i })).toBeNull();
    expect(answerInConversation).toHaveBeenCalledTimes(1);
  });

  it.each([404, 405])("retains the JSON compatibility route for an unsupported stream endpoint (HTTP %i)", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Unsupported route", { status })));
    await ask();
    await waitFor(() => expect(answerInConversation).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Compatible JSON answer")).toBeDefined();
  });
});
